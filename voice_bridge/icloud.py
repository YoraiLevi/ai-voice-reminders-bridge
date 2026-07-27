"""iCloud transport over the pyicloud private CloudKit Reminders API. Folds in the
backend half of `pyicloud_bridge.py`. Cannot create lists (pyicloud exposes no
create-list API) — `create_list` raises NotSupportedError.

Not integration-tested (2FA + a private API can't run in CI). The Transport ABC +
FakeTransport give the *path* behavioural coverage; this module has a thin unit test
that it maps the pyicloud shape correctly.
"""

from __future__ import annotations

import time
from typing import Any, Callable

from .config import Config
from .transport import Item, ListRef, NotSupportedError, Transport
from .util import read_kv


class ICloudError(RuntimeError):
    """Auth / priming failure against the pyicloud Reminders service."""


def _is_throttle(exc: Exception) -> bool:
    """One definition, shared with the classifier. It lived in two places while
    `errors` was a foundation nothing consumed; the poller consumes it now."""
    from .errors import _is_throttle as _shared

    return _shared(exc)


def _retrying(fn: Callable[[], Any], *, tries: int = 4) -> Any:
    """Call fn, retrying with exponential backoff ONLY on a throttle/503 (the old
    multi-account 503 storm). Any other error is raised immediately; a persistent
    throttle is raised after `tries`."""
    delay = 1.0
    for i in range(tries):
        try:
            return fn()
        except Exception as exc:
            if i == tries - 1 or not _is_throttle(exc):
                raise
            time.sleep(delay)
            delay = min(delay * 2, 30)


def _completed(rem: Any) -> bool:
    if isinstance(rem, dict):
        return bool(rem.get("completed"))
    return bool(getattr(rem, "completed", False))


def _field(rem: Any, key: str) -> str:
    if isinstance(rem, dict):
        return str(rem.get(key) or "")
    return str(getattr(rem, key, "") or "")


class ICloudTransport(Transport):
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.r: Any = None  # the primed reminders service

    def connect(self) -> None:
        if self.r is not None:
            return
        apple_id = read_kv(self.cfg.creds_env, "ICLOUD_APPLE_ID")
        password = read_kv(self.cfg.creds_env, "ICLOUD_PASSWORD")
        if not apple_id or not password:
            raise ICloudError(
                f"Missing ICLOUD_APPLE_ID / ICLOUD_PASSWORD in {self.cfg.creds_env}. "
                "pyicloud needs the MAIN Apple ID password + a trusted session "
                "(seed it with `voice-bridge icloud-login`)."
            )
        try:
            from pyicloud import PyiCloudService
        except ImportError as exc:  # pragma: no cover
            raise ICloudError(f"pyicloud not installed: {exc}") from exc
        try:
            api = PyiCloudService(apple_id, password, cookie_directory=str(self.cfg.cookie_dir))
        except Exception as exc:
            raise ICloudError(f"pyicloud login failed: {type(exc).__name__}: {exc}") from exc
        if getattr(api, "requires_2fa", False):
            raise ICloudError(
                f"session needs 2FA — the cached session under {self.cfg.cookie_dir} "
                "expired. Re-run `voice-bridge icloud-login`."
            )
        r = api.reminders
        list(r.lists())  # REQUIRED priming before list_reminders() or the service 400s
        self.r = r

    def _svc(self) -> Any:
        if self.r is None:
            self.connect()
        return self.r

    def list_todo_lists(self) -> list[ListRef]:
        return [ListRef(name=lst.title, id=str(lst.id)) for lst in self._svc().lists()]

    def resolve_list(self, name: str, list_id: str = "") -> ListRef:
        lists = list(self._svc().lists())
        if list_id:
            for lst in lists:
                if str(lst.id) == list_id:
                    return ListRef(name=lst.title, id=str(lst.id))
        same = [lst for lst in lists if lst.title == name]
        if not same:
            raise LookupError(
                f"{name!r} list is not visible via pyicloud (create it on the iPhone)"
            )
        return ListRef(name=same[0].title, id=str(same[0].id))

    def _raw_list(self, list_id: str) -> Any:
        for lst in self._svc().lists():
            if str(lst.id) == list_id:
                return lst
        raise LookupError(f"list id {list_id!r} vanished")

    def _reminders(self, list_id: str) -> list[Any]:
        data = dict(_retrying(lambda: self._svc().list_reminders(list_id)))
        return list(data.get("reminders", []))

    def read_incomplete(self, lst: ListRef) -> list[Item]:
        return [
            Item(id=_field(rem, "guid"), title=_field(rem, "title"), notes=_field(rem, "desc"))
            for rem in self._reminders(lst.id)
            if not _completed(rem)
        ]

    def read_completed(self, lst: ListRef) -> list[Item]:
        return [
            Item(id=_field(rem, "guid"), title=_field(rem, "title"), notes=_field(rem, "desc"))
            for rem in self._reminders(lst.id)
            if _completed(rem)
        ]

    def add_todo(
        self, lst: ListRef, summary: str, notes: str = "", *, needs_input: bool = False
    ) -> str:
        guid = _retrying(lambda: self._svc().post(summary, description=notes, collection=lst.id))
        return str(guid or "(unknown-guid)")

    def complete(self, lst: ListRef, item_id: str) -> None:
        """Mark an item handled. RAISES on failure and on not-found.

        Both used to be silent — the inner catch swallowed backend errors, and a
        missing id simply fell off the end of the loop. The caller could not tell
        "cleared off the phone" from "quietly didn't", so the reminder stayed
        visible, the user re-dictated it, and the agent got it twice (FMA-2).
        """
        for rem in self._reminders(lst.id):
            if _field(rem, "guid") == item_id:
                rem.completed = True
                self._svc().update(rem)
                return
        raise LookupError(f"no item {item_id!r} in list {lst.name!r}")

    def create_list(self, name: str) -> ListRef:
        raise NotSupportedError(
            "iCloud (pyicloud) exposes no create-list API — create the two lists once "
            "on the iPhone Reminders app, then retry."
        )
