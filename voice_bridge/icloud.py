"""iCloud transport over the pyicloud private CloudKit Reminders API. Folds in the
backend half of `pyicloud_bridge.py`. Cannot create lists (pyicloud exposes no
create-list API) - `create_list` raises NotSupportedError.

Not integration-tested (2FA + a private API can't run in CI). The Transport ABC +
FakeTransport give the *path* behavioural coverage; this module has a thin unit test
that it maps the pyicloud shape correctly.
"""

from __future__ import annotations

import time
from typing import Any, Callable

from . import _icloud_crdt
from .config import Config
from .progress import step
from .transport import Item, ListRef, NotSupportedError, RefCache, Transport
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


def _tighter(given: Any, budget: float) -> Any:
    """The smaller of a caller's timeout and ours, preserving the caller's shape.

    `requests` accepts either a number or a `(connect, read)` pair, and silently
    doing the wrong thing with the pair is how a "fix" becomes a new bug - so the
    pair stays a pair, clamped element-wise.
    """
    if isinstance(given, (int, float)):
        return min(float(given), budget)
    if isinstance(given, tuple):
        return tuple(min(float(v), budget) if isinstance(v, (int, float)) else v for v in given)
    return budget  # an unrecognised shape: ours is the one we can reason about


def _apply_timeout(api: Any, seconds: float) -> None:
    """Give every iCloud HTTP call a client-side deadline.

    INVESTIGATED after "Request failed to iCloud" took a very long time to appear
    during interactive setup. It was not our backoff: `_retrying` fires only on a
    throttle and spends at most ~7s. The real answer is that **there was no
    client-side timeout at all** - pyicloud builds a plain session, `requests`
    defaults to waiting indefinitely, and a connection that hangs rather than
    refuses is bounded only by the OS TCP stack. So the user waited on a socket,
    not on us, and no amount of reading our code would have shown a number.

    `requests` has no session-level timeout setting, so the budget is injected
    around `session.request`.

    IT CLAMPS RATHER THAN YIELDS, and the change is deliberate. The first version
    used `setdefault`, so an explicit per-call timeout won - which sounds correct
    and had the exact wrong effect: pyicloud's cloudkit client passes `timeout=60`
    on the reminders calls, so our 30s budget applied to everything EXCEPT the
    calls that actually hang. A user set 30, waited 60, and the setting they could
    see was the one thing not in force.

    The argument for yielding is real: a library may pass a longer timeout because
    that call genuinely needs it, and clamping could cut off a legitimate slow
    operation. We take the other side for two reasons. A library's default is a
    guess about its median caller, not a considered opinion about THIS user's
    patience; and the cost of clamping too tightly is now cheap - the interactive
    paths retry in place and keep what was already decided - while the cost of
    yielding is a person watching a frozen terminal with no feedback, which is the
    complaint that produced this. Anyone who needs longer raises `icloud_timeout`,
    which is the point of it being settable.

    Non-fatal by design: a pyicloud that does not expose a session should not stop
    the bridge from working, it should only mean we cannot bound the wait.
    """
    session: Any = getattr(api, "session", None)
    original = getattr(session, "request", None)
    if session is None or original is None or seconds <= 0:  # pragma: no cover - defensive
        return

    def _with_timeout(*args: Any, **kwargs: Any) -> Any:
        given = kwargs.get("timeout")
        kwargs["timeout"] = seconds if given is None else _tighter(given, seconds)
        return original(*args, **kwargs)

    session.request = _with_timeout


def _item(rem: Any) -> Item:
    """One pyicloud `Reminder` -> our `Item`.

    The model is TYPED (`id`, `title`, `desc`, `completed`), so this reads
    attributes directly. The previous adapter went through dict-or-attr helpers,
    which quietly tolerated any shape at all - including shapes the library never
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
        self.r: Any = None  # the reminders service
        #: pyicloud requires ONE `lists()` call before `list_reminders()`, or the
        #: service 400s. Tracked rather than done eagerly, so the first real
        #: inventory read satisfies it and nobody pays for a separate one.
        self._primed = False
        self._refs = RefCache()

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

        # Correct the CRDT length computation before any write happens. Without
        # this, a reminder whose text contains an emoji is stored with declared
        # lengths in codepoints where Apple counts UTF-16 units, and the phone
        # renders it BLANK - while every read-back through the API looks perfect
        # (LIVE-5). See _icloud_crdt for why the API cannot detect it.
        _icloud_crdt.install()
        try:
            with step("connecting to iCloud"):
                api = PyiCloudService(apple_id, password, cookie_directory=str(self.cfg.cookie_dir))
        except Exception as exc:
            raise ICloudError(f"pyicloud login failed: {type(exc).__name__}: {exc}") from exc
        if getattr(api, "requires_2fa", False):
            raise ICloudError(
                f"session needs 2FA - the cached session under {self.cfg.cookie_dir} "
                "expired. Re-run `voice-bridge icloud-login`."
            )
        _apply_timeout(api, self.cfg.icloud_timeout)
        # NO PRIMING FETCH HERE, deliberately. pyicloud requires one `lists()` call
        # before `list_reminders()` or the service 400s - but making it a separate
        # step meant `lists` paid for the inventory TWICE, once to prime and once to
        # display. On a slow day that is two ~7s waits back to back, which the new
        # progress lines made impossible to miss. Priming is now a SIDE EFFECT of
        # the first real inventory read (`_inventory`), so nobody pays for it twice.
        self.r = api.reminders

    def _svc(self) -> Any:
        if self.r is None:
            self.connect()
        return self.r

    def _inventory(self) -> list[ListRef]:
        """THE one place that downloads the list of lists. Also does the priming.

        Every inventory read goes through here, which is what makes the priming
        requirement free: the first read satisfies it, and there is no second call
        to pay for.
        """
        # `RemindersList.id` is the required identifier the create/query helpers
        # take; `guid` is optional metadata and is NOT what they want.
        svc = self._svc()
        refs = [ListRef(name=lst.title, id=str(lst.id)) for lst in svc.lists()]
        self._primed = True
        self._refs.refresh()
        return self._refs.remember(refs)

    def _ensure_primed(self) -> None:
        """Satisfy pyicloud's required first `lists()` call, once, lazily.

        Item operations call this because they cannot run before priming. If an
        inventory has already been read - which is the usual case, since something
        had to resolve the list first - this costs nothing.
        """
        if not self._primed:
            self._inventory()

    def list_todo_lists(self) -> list[ListRef]:
        """The full inventory - a real download - and the cache is rebuilt from it.

        Callers that only need ONE list by id should use `resolve_list`, which
        answers from the cache. This is for the three places that genuinely want an
        inventory: a picker's menu, an explicit refresh, and stale-id recovery.

        Always fetches: `r) refresh` exists because the user just made a list, and a
        freshness window that answered it from a cache would break the one case the
        key is for.
        """
        with step("reading your lists"):
            return self._inventory()

    def resolve_list(self, name: str, list_id: str = "") -> ListRef:
        # CACHED BY ID. A selected id either still names a list - same answer every
        # time - or it is gone, which surfaces when an operation on it fails. It
        # does not need re-discovering every poll cycle to find that out.
        if list_id and (hit := self._refs.get(list_id)) is not None:
            return hit

        with step("reading your lists"):
            refs = self._inventory()
        if list_id:
            for ref in refs:
                if ref.id == list_id:
                    return ref
            # An id that matches nothing is a STALE SELECTION, and it must not fall
            # through to a name. Falling through is how a probe "succeeded": with a
            # role unselected, the fabricated default name matched a real leftover
            # list on the account, so a message went somewhere plausible, nobody was
            # reading it, and the command printed ok. The other two adapters already
            # raised here; this one - the live one - did not.
            raise LookupError(
                f"no list with id {list_id!r} (it was selected once and is gone now) - "
                f"run `voice-bridge lists --select` to choose again"
            )
        if not name:
            raise LookupError("no list id selected - run `voice-bridge lists --select`")
        same = [ref for ref in refs if ref.name == name]
        if not same:
            raise LookupError(
                f"{name!r} list is not visible via pyicloud (create it on the iPhone)"
            )
        return same[0]

    def invalidate_lists(self) -> None:
        self._refs.refresh()

    def _query(self, list_id: str, *, include_completed: bool) -> list[Any]:
        self._ensure_primed()
        with step("reading that list's items"):
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
        self._ensure_primed()
        with step(f"sending {summary[:40]!r} to your phone"):
            created = _retrying(
                lambda: self._svc().create(
                    list_id=lst.id,
                    title=summary,
                    desc=notes,
                    priority=1 if needs_input else 0,
                )
            )

        # LIVE-4: roughly one create in two came back titled "New Reminder" -
        # Apple's default for a reminder with no title - while the notes held the
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

        Both used to be silent - an inner catch swallowed backend errors and a
        missing id fell off the end of a loop - so the caller could not tell
        "cleared off the phone" from "quietly didn't", the reminder stayed
        visible, the user re-dictated it, and the agent got it twice (FMA-2).
        """
        self._ensure_primed()
        try:
            with step("marking that message done"):
                rem = _retrying(lambda: self._svc().get(item_id))
        except Exception as exc:
            raise LookupError(f"no item {item_id!r} in list {lst.name!r}: {exc}") from exc
        if rem is None:
            raise LookupError(f"no item {item_id!r} in list {lst.name!r}")
        rem.completed = True
        _retrying(lambda: self._svc().update(rem))

    def create_list(self, name: str) -> ListRef:
        raise NotSupportedError(
            "iCloud (pyicloud) exposes no create-list API - create the two lists once "
            "on the iPhone Reminders app, then retry."
        )
