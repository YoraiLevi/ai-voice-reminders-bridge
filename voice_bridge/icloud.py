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


def _item(rem: Any) -> Item:
    """One pyicloud `Reminder` -> our `Item`.

    The model is TYPED (`id`, `title`, `desc`, `completed`), so this reads
    attributes directly. The previous adapter went through dict-or-attr helpers,
    which quietly tolerated any shape at all — including shapes the library never
    returns, which is how a call to a non-existent method survived to a live run.
    """
    return Item(
        id=str(rem.id),
        title=str(rem.title or ""),
        notes=str(getattr(rem, "desc", "") or ""),
    )


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
        # `RemindersList.id` is the required identifier the create/query helpers
        # take; `guid` is optional metadata and is NOT what they want.
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

    def _query(self, list_id: str, *, include_completed: bool) -> list[Any]:
        result = _retrying(
            lambda: self._svc().list_reminders(list_id, include_completed=include_completed)
        )
        return list(result.reminders)

    def read_incomplete(self, lst: ListRef) -> list[Item]:
        return [_item(rem) for rem in self._query(lst.id, include_completed=False)]

    def read_completed(self, lst: ListRef) -> list[Item]:
        # The query returns both when completed are included, so select here.
        return [
            _item(rem)
            for rem in self._query(lst.id, include_completed=True)
            if getattr(rem, "completed", False)
        ]

    def add_todo(
        self, lst: ListRef, summary: str, notes: str = "", *, needs_input: bool = False
    ) -> str:
        created = _retrying(
            lambda: self._svc().create(
                list_id=lst.id,
                title=summary,
                desc=notes,
                priority=1 if needs_input else 0,
            )
        )

        # LIVE-4: roughly one create in two came back titled "New Reminder" —
        # Apple's default for a reminder with no title — while the notes held the
        # full text. Identical text succeeded on the next attempt, so it is not
        # content-dependent: `create` writes a CRDT title document and then reads
        # the record back, and that read-back can land before the title has
        # propagated. We know what we asked for, so if what came back disagrees,
        # say it again. Idempotent, and self-healing whether the cause is a stale
        # read or a genuinely dropped field.
        if summary and str(getattr(created, "title", "") or "") != summary:
            created.title = summary
            _retrying(lambda: self._svc().update(created))

        return str(created.id)

    def complete(self, lst: ListRef, item_id: str) -> None:
        """Mark an item handled. RAISES on failure and on not-found.

        Both used to be silent — an inner catch swallowed backend errors and a
        missing id fell off the end of a loop — so the caller could not tell
        "cleared off the phone" from "quietly didn't", the reminder stayed
        visible, the user re-dictated it, and the agent got it twice (FMA-2).
        """
        try:
            rem = _retrying(lambda: self._svc().get(item_id))
        except Exception as exc:
            raise LookupError(f"no item {item_id!r} in list {lst.name!r}: {exc}") from exc
        if rem is None:
            raise LookupError(f"no item {item_id!r} in list {lst.name!r}")
        rem.completed = True
        _retrying(lambda: self._svc().update(rem))

    def create_list(self, name: str) -> ListRef:
        raise NotSupportedError(
            "iCloud (pyicloud) exposes no create-list API — create the two lists once "
            "on the iPhone Reminders app, then retry."
        )
