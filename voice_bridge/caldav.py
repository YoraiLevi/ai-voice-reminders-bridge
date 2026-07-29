"""CalDAV transport (self-hosted Radicale, or iCloud CalDAV). Folds in the former
`_caldav.py` plumbing plus the CalDAV backend halves of `reminder_bridge.py` and
`bootstrap.py`, all behind the Transport interface.

iCloud CalDAV forbids list creation; a self-hosted Radicale server allows it - so
`create_list` works on Radicale and is guarded against an iCloud URL.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from .config import Config
from .progress import step
from .transport import Item, ListRef, NotSupportedError, RefCache, Transport
from .util import read_env

CALDAV_URL = "https://caldav.icloud.com/"
ALARM_LEAD_SECONDS = 60


class CredsError(RuntimeError):
    """Credentials are missing or malformed."""


def _creds(cfg: Config) -> tuple[str, str, str]:
    """(apple_id, app_password, caldav_url) from env then the creds file. On iCloud the
    password must be an APP-SPECIFIC password; on Radicale it is the Radicale user's."""
    env = read_env(cfg.creds_env)

    def pick(*keys: str) -> str:
        for k in keys:
            v = os.environ.get(k) or env.get(k)
            if v:
                return v.strip()
        return ""

    apple_id = pick("ICLOUD_APPLE_ID", "ICLOUD_USERNAME")
    app_password = pick("ICLOUD_APP_PASSWORD")
    url = pick("ICLOUD_CALDAV_URL") or CALDAV_URL
    missing = [
        n
        for n, v in (("ICLOUD_APPLE_ID", apple_id), ("ICLOUD_APP_PASSWORD", app_password))
        if not v
    ]
    if missing:
        raise CredsError(
            f"Missing credential(s): {', '.join(missing)}. Set them in the environment "
            f"or in {cfg.creds_env}. On iCloud the password must be an APP-SPECIFIC "
            "password; on Radicale it is the Radicale user's password."
        )
    return apple_id, app_password, url


def _display_name(cal: Any) -> str:
    try:
        name = cal.get_display_name()
    except Exception:
        name = None
    return str(name) if name else str(cal.url).rstrip("/").rsplit("/", 1)[-1]


def _todo_fields(todo: Any) -> tuple[str, str, str]:
    comp = todo.icalendar_component

    def g(k: str) -> str:
        v = comp.get(k)
        return str(v) if v is not None else ""

    return g("uid"), g("summary"), g("description")


def _alarmed_ics(summary: str, description: str, needs_input: bool) -> str:
    """A VTODO with a DUE time + absolute DISPLAY VALARM so iOS fires a native banner.
    Times are tz-aware UTC (avoids the naive-local-stored-as-UTC bug)."""
    from icalendar import Alarm, Calendar, Todo

    now = datetime.now(timezone.utc)
    due = now + timedelta(seconds=ALARM_LEAD_SECONDS)
    todo = Todo()
    todo.add("uid", str(uuid.uuid4()))
    todo.add("dtstamp", now)
    todo.add("summary", summary)
    todo.add("description", description)
    todo.add("due", due)
    todo.add("status", "NEEDS-ACTION")
    if needs_input:
        todo.add("priority", 1)
    alarm = Alarm()
    alarm.add("action", "DISPLAY")
    alarm.add("description", summary)
    alarm.add("trigger", due)
    todo.add_component(alarm)
    cal = Calendar()
    cal.add("prodid", "-//voice-bridge//reply-alarm//EN")
    cal.add("version", "2.0")
    cal.add_component(todo)
    return cal.to_ical().decode("utf-8")


class CalDAVTransport(Transport):
    def __init__(self, cfg: Config, *, native_alarm: bool = True) -> None:
        self.cfg = cfg
        self.native_alarm = native_alarm
        self._principal: Any = None
        self._cals: dict[str, Any] = {}  # ListRef.id (== str(cal.url)) -> cal object
        self._refs = RefCache()

    # --- session ------------------------------------------------------------
    def connect(self) -> None:
        if self._principal is not None:
            return
        from caldav import DAVClient
        from caldav.lib.error import AuthorizationError, DAVError

        apple_id, password, url = _creds(self.cfg)
        client = DAVClient(url=url, username=apple_id, password=password, timeout=30.0)  # type: ignore[operator]
        try:
            with step("connecting to your CalDAV server"):
                self._principal = client.principal()
        except AuthorizationError as exc:
            # Auth: no amount of retrying enters a correct password. Stays a
            # CredsError, which `is_transient` classifies as NOT transient, so the
            # run loop stops and says what to fix.
            raise CredsError("server rejected the credentials (401 or 403).") from exc
        except DAVError:
            # Everything else - the server is down, restarting, or unreachable -
            # is a different question with a different answer: retry. Collapsing
            # it into CredsError told the loop to stop with "fix your
            # credentials" while the credentials were perfectly good (FMA-10).
            # Re-raised as-is so `is_transient` can classify it by type/status.
            raise

    def _p(self) -> Any:
        if self._principal is None:
            self.connect()
        return self._principal

    def _cal(self, lst: ListRef) -> Any:
        cal = self._cals.get(lst.id)
        if cal is None:
            cal = self.resolve_list(lst.name, lst.id) and self._cals.get(lst.id)
        return self._cals[lst.id]

    # --- Transport interface ------------------------------------------------
    def list_todo_lists(self) -> list[ListRef]:
        """The full inventory, and a genuinely expensive one.

        `get_supported_components()` is a PROPFIND PER CALENDAR - N+1 over the
        account - and caldav 3.x exposes no batched form of it: `Principal
        .calendars()` returns objects whose properties are fetched lazily, one
        request each. So the cost is inherent to the library, and the answer is to
        CALL THIS LESS rather than to make it cheaper. It is now reached only where
        an inventory is genuinely wanted: a picker's menu, an explicit refresh, and
        recovery after a stale id.

        The per-calendar objects are memoised in `self._cals` for `_cal`, which was
        already right; the refs are memoised for `resolve_list`, which was not.
        """
        out: list[ListRef] = []
        self._refs.refresh()
        with step("reading your lists"):
            cals = list(self._p().calendars())
        for cal in cals:
            try:
                comps = cal.get_supported_components()
            except Exception:
                comps = []
            if comps and "VTODO" not in comps:
                continue
            ref = ListRef(name=_display_name(cal), id=str(cal.url))
            self._cals[ref.id] = cal
            out.append(ref)
        return self._refs.remember(out)

    def resolve_list(self, name: str, list_id: str = "") -> ListRef:
        # CACHED BY ID, like the iCloud adapter: `list_todo_lists` is a full
        # inventory walk (one PROPFIND per calendar, below), and the poller used to
        # pay for it once per cycle and once per reply to re-answer a question
        # whose answer does not change while the id is valid.
        if list_id and (hit := self._refs.get(list_id)) is not None:
            return hit

        lists = self.list_todo_lists()
        if list_id:
            for ref in lists:
                if ref.id == list_id:
                    return ref
            raise LookupError(f"no CalDAV list with id {list_id!r}")
        target = name.strip().lower()
        for ref in lists:
            if ref.name.strip().lower() == target:
                return ref
        raise LookupError(f"{name!r} list is not visible over CalDAV")

    def invalidate_lists(self) -> None:
        self._refs.refresh()

    def _iter_objects(self, cal: Any):
        """Per-item load that SKIPS un-loadable entries - a dangling Radicale index
        entry 404s on load, and a bulk load would fail the whole poll on it."""
        for todo in cal.objects(load_objects=False):
            try:
                comp = todo.icalendar_component
            except Exception:
                continue
            yield todo, comp

    def read_incomplete(self, lst: ListRef) -> list[Item]:
        out: list[Item] = []
        for todo, comp in self._iter_objects(self._cal(lst)):
            status = str(comp.get("status") or "").upper()
            if status == "COMPLETED" or comp.get("completed") is not None:
                continue
            uid, title, notes = _todo_fields(todo)
            out.append(Item(id=uid, title=title, notes=notes))
        return out

    def read_completed(self, lst: ListRef) -> list[Item]:
        out: list[Item] = []
        for todo, comp in self._iter_objects(self._cal(lst)):
            status = str(comp.get("status") or "").upper()
            if status == "COMPLETED" or comp.get("completed") is not None:
                uid, title, notes = _todo_fields(todo)
                out.append(Item(id=uid, title=title, notes=notes))
        return out

    def add_todo(
        self, lst: ListRef, summary: str, notes: str = "", *, needs_input: bool = False
    ) -> str:
        cal = self._cal(lst)
        if self.native_alarm:
            todo = cal.save_todo(ical=_alarmed_ics(summary, notes or summary, needs_input))
        else:
            todo = cal.save_todo(
                summary=summary, description=notes, priority=1 if needs_input else None
            )
        uid, _, _ = _todo_fields(todo)
        return uid or "(unknown-uid)"

    def complete(self, lst: ListRef, item_id: str) -> None:
        """Mark an item handled. RAISES on not-found (transport contract, FMA-2).

        Falling off the end silently told the caller "done" when nothing had been
        completed, so the reminder stayed on the phone and the user re-dictated it.
        """
        for todo, comp in self._iter_objects(self._cal(lst)):
            if str(comp.get("uid") or "") == item_id:
                comp["status"] = "COMPLETED"
                comp["percent-complete"] = 100
                if "completed" not in comp:
                    comp.add("completed", datetime.now(timezone.utc))
                todo.save()
                return
        raise LookupError(f"no item {item_id!r} in list {lst.name!r}")

    def create_list(self, name: str) -> ListRef:
        _, _, url = _creds(self.cfg)
        if "icloud.com" in url:
            raise NotSupportedError(
                "iCloud CalDAV forbids creating lists - create them on the iPhone, or "
                "point ICLOUD_CALDAV_URL at a self-hosted Radicale server."
            )
        try:
            return self.resolve_list(name)  # already exists → idempotent
        except LookupError:
            pass
        cal_id = name.lower().replace(" ", "-")
        self._p().make_calendar(
            name=name, cal_id=cal_id, supported_calendar_component_set=["VTODO"]
        )
        ref = self.resolve_list(name)
        try:  # seed a placeholder - an empty CalDAV list doesn't sync to the iPhone
            self._cals[ref.id].save_todo(
                summary=f"{name} is live - the voice bridge created this list. Safe to delete."
            )
        except Exception:
            pass
        return ref
