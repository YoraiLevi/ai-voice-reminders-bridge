"""Error classification and typed CLI failures — the spine every command rides.

Two rules earn this module its place:

**Auth is never transient.** The run loop used to retry every exception forever,
so an expired session — which no retry can fix, since only a human can enter a
2FA code — was indistinguishable from a network blip. `is_transient` decides
retry-vs-stop, and answers False for anything auth-shaped.

**Never disguise a bug.** `raise_command_error` maps the failures we genuinely
understand onto clean exit codes, and *re-raises* everything else with its
traceback intact. A tidy error message for an unrecognised fault is worse than a
crash, because it looks like the tool working.

`caldav` and `requests` are optional extras, so both are imported defensively:
the core install has no third-party dependencies and must still classify.
"""

from __future__ import annotations

import re

try:  # optional extra — present with [icloud] or [caldav]
    from requests import exceptions as _requests_exc
except ImportError:  # pragma: no cover - exercised by the dependency-free install
    _requests_exc = None  # type: ignore[assignment]

try:  # optional extra — present with [caldav] or [server]
    from caldav.lib import error as _dav
except ImportError:  # pragma: no cover - exercised by the dependency-free install
    _dav = None  # type: ignore[assignment]


class CommandError(Exception):
    """A failure a command can report cleanly: an exit code plus what to do."""

    def __init__(self, code: int, msg: str) -> None:
        super().__init__(msg)
        self.code = code
        self.msg = msg


# --------------------------------------------------------------------------- #
# transient classification
# --------------------------------------------------------------------------- #

_THROTTLE_MARKERS = ("503", "throttle", "too many", "service unavailable")

#: A caldav ``errmsg`` renders as ``"STATUS reason\n\nraw"``, so a by-method
#: error's ``.url`` *starts* with the status code. A URL does not, which is what
#: keeps Radicale's default port 5232 from being read as a 5xx.
_STATUS_TOKEN = re.compile(r"^\s*(\d{3})\b")
_TRAILING_STATUS = re.compile(r"\b(5\d\d)\b")


def _is_throttle(exc: BaseException) -> bool:
    """Rate-limiting, by message. Backends signal it inconsistently."""
    text = str(exc).lower()
    return any(m in text for m in _THROTTLE_MARKERS)


def _is_requests_network(exc: BaseException) -> bool:
    """A connection/timeout error from `requests`.

    Named explicitly, because `requests.exceptions.ConnectionError` is *not*
    `builtins.ConnectionError` — its MRO runs `RequestException -> OSError`.
    Matching the builtin misses the commonest mid-poll transient; matching
    `OSError` broadly would swallow `FileNotFoundError` and friends.
    """
    if _requests_exc is None:
        return False
    return isinstance(exc, (_requests_exc.ConnectionError, _requests_exc.Timeout))


def _dav_status(exc: BaseException) -> int | None:
    """Best-effort HTTP status for a caldav error.

    `DAVError` carries no status attribute — only `.url` and a free-text
    `.reason` — and caldav passes `errmsg()` *positionally*, so the status text
    lands in `.url`. `.url` is optional and class-defaults to None, hence the
    guard before parsing.
    """
    url = getattr(exc, "url", None)
    if isinstance(url, str):
        if m := _STATUS_TOKEN.match(url):
            return int(m.group(1))
    if m := _TRAILING_STATUS.search(str(exc)):
        return int(m.group(1))
    return None


def _classify_dav(exc: BaseException) -> bool | None:
    """caldav errors, by TYPE, subclasses first. None = not a caldav error."""
    if _dav is None or not isinstance(exc, _dav.DAVError):
        return None
    if isinstance(exc, _dav.RateLimitError):
        return True
    if isinstance(exc, _dav.AuthorizationError):
        return False  # covers 401 AND 403 — credentials or permissions, not a blip
    status = _dav_status(exc)
    if status is not None and 500 <= status <= 599:
        return True
    return False  # unclassified -> bounded stop, not endless retry


def is_transient(exc: BaseException) -> bool:
    """True when retrying could plausibly succeed.

    False for auth, usage, and anything unrecognised: an unclassified error gets
    a bounded retry and then stops, rather than backing off forever.
    """
    if isinstance(exc, LookupError):  # includes KeyError/IndexError — all bugs or usage
        return False

    dav = _classify_dav(exc)
    if dav is not None:
        return dav

    if _is_requests_network(exc):
        return True

    # BEFORE the auth verdict: `ICloudTransport.connect` wraps *any* constructor
    # failure in `ICloudError`, so a wifi drop at connect arrives auth-shaped.
    # Judging it by its own type would stop the loop for good and tell the
    # operator to re-login over a blip. Judge it by what caused it.
    cause = exc.__cause__
    if cause is not None and cause is not exc:
        if _is_requests_network(cause) or _is_throttle(cause):
            return True

    # An auth failure that merely *mentions* a status must not read as a blip,
    # so the auth check comes before the throttle sniff.
    if _is_auth(exc):
        return False

    if _is_throttle(exc):
        return True

    return False


# --------------------------------------------------------------------------- #
# typed command failures
# --------------------------------------------------------------------------- #


def _is_auth(exc: BaseException) -> bool:
    """Credentials/session failures, matched by name to avoid importing transports.

    `errors` is a foundation module; importing `icloud`/`caldav` here would
    invert the dependency direction, so the two transport error types are
    recognised structurally instead.
    """
    return type(exc).__name__ in {"ICloudError", "CredsError"}


def raise_command_error(exc: BaseException) -> None:
    """Translate a backend failure into a `CommandError`, or re-raise it.

    Exit codes: **1** the transport is unavailable or not set up (try again or
    finish setup), **2** you must act (usage — a missing list, a bad key).
    Anything unrecognised is re-raised untouched.
    """
    if _is_auth(exc):
        raise CommandError(1, str(exc)) from exc

    if _is_requests_network(exc) or _is_throttle(exc):
        raise CommandError(1, f"transport unavailable: {exc}") from exc

    if _dav is not None and isinstance(exc, _dav.DAVError):
        if isinstance(exc, _dav.AuthorizationError):
            raise CommandError(1, f"credentials or permissions rejected: {exc}") from exc
        raise CommandError(1, f"transport unavailable: {exc}") from exc

    # Only a *plain* LookupError means "not found". KeyError and IndexError also
    # subclass it, and dressing those as a tidy exit 2 would hide a real defect.
    if isinstance(exc, LookupError) and not isinstance(exc, (KeyError, IndexError)):
        raise CommandError(2, str(exc)) from exc

    raise exc
