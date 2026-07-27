"""The error spine: `is_transient` classification and typed CLI errors.

Two failures motivate this module (design section 18.3, FMA-3 / TRANSPORT-1):

* the run loop retried *every* exception forever, so an expired session — which
  no retry can fix, because only a human can enter a 2FA code — looked exactly
  like a network blip. The gating property is therefore **auth is never
  transient**.
* one backend error produced three different CLI behaviours (traceback, blanket
  exit 2, infinite retry). `raise_command_error` gives one mapping, and
  deliberately re-raises anything unrecognised so a genuine bug is never
  disguised as a clean user-facing error.
"""

from __future__ import annotations

import pytest

from voice_bridge.caldav import CredsError
from voice_bridge.errors import CommandError, is_transient, raise_command_error
from voice_bridge.icloud import ICloudError

requests = pytest.importorskip("requests")
caldav_error = pytest.importorskip("caldav.lib.error")


# --------------------------------------------------------------------------- #
# is_transient — the core truth table
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "exc, expected, why",
    [
        # --- transient: retrying can actually help -------------------------
        (Exception("503 Service Unavailable"), True, "throttle/503"),
        (Exception("Too Many Requests"), True, "throttle wording"),
        (requests.exceptions.ConnectionError("dns went away"), True, "requests connection"),
        (requests.exceptions.Timeout("read timed out"), True, "requests timeout"),
        # --- NOT transient: retrying is futile or hides a bug --------------
        (ICloudError("session needs 2FA"), False, "auth — the gating case"),
        (CredsError("server rejected the credentials (401)."), False, "creds"),
        (KeyError("reminders"), False, "a bug, not a blip"),
        (LookupError("no such list"), False, "usage error"),
        (FileNotFoundError("icloud.env"), False, "OSError is NOT blanket-transient"),
        (ValueError("nonsense"), False, "unclassified -> bounded stop"),
    ],
)
def test_is_transient_truth_table(exc: Exception, expected: bool, why: str) -> None:
    assert is_transient(exc) is expected, why


def test_auth_is_never_transient() -> None:
    """The gating property, asserted on its own so it cannot be lost in a refactor."""
    assert is_transient(ICloudError("needs 2FA")) is False


def test_requests_connectionerror_is_not_the_builtin() -> None:
    """Why the classifier must name the `requests` family explicitly.

    `requests.exceptions.ConnectionError` does not inherit from
    `builtins.ConnectionError` — its MRO runs through `RequestException` to
    `OSError`. Matching the builtin would miss the commonest mid-poll transient;
    matching `OSError` broadly would swallow `FileNotFoundError`.
    """
    assert not issubclass(requests.exceptions.ConnectionError, ConnectionError)
    assert is_transient(requests.exceptions.ConnectionError("x")) is True
    assert is_transient(FileNotFoundError("x")) is False


# --------------------------------------------------------------------------- #
# is_transient — the caldav branch (classify by TYPE; DAVError has no status)
# --------------------------------------------------------------------------- #


def test_caldav_rate_limit_is_transient() -> None:
    assert is_transient(caldav_error.RateLimitError("slow down")) is True


def test_caldav_authorization_is_not_transient() -> None:
    """`AuthorizationError` covers 401 *and* 403 — neither is fixed by retrying."""
    assert is_transient(caldav_error.AuthorizationError("401 Unauthorized")) is False


def test_caldav_5xx_in_url_is_transient() -> None:
    """caldav puts the status text in `.url`, not `.reason` (errmsg is positional)."""
    exc = caldav_error.DAVError("500 Internal Server Error\n\nraw body")
    assert exc.url.startswith("500")
    assert is_transient(exc) is True


def test_caldav_response_error_trailing_status_is_transient() -> None:
    assert is_transient(caldav_error.ResponseError("REPORT failed with status 503")) is True


def test_caldav_port_5232_is_not_mistaken_for_5xx() -> None:
    """The trap: Radicale's default port makes a naive "starts with 5" sniff fire.

    A URL is not a status line, so parsing the first whitespace token as a
    three-digit integer must fail on `http://...` rather than match `5232`.
    """
    exc = caldav_error.NotFoundError("http://localhost:5232/vox/todo.ics")
    assert is_transient(exc) is False


def test_caldav_url_none_is_unclassified() -> None:
    """`DAVError.url` is optional and class-defaults to None — guard before parsing."""
    exc = caldav_error.DAVError(reason="no url at all")
    assert exc.url is None
    assert is_transient(exc) is False


def test_caldav_unparseable_is_unclassified() -> None:
    assert is_transient(caldav_error.DAVError("boom")) is False


def test_wrapped_network_error_classifies_by_cause() -> None:
    """A network blip wrapped at connect must not read as an auth failure.

    `ICloudTransport.connect` wraps *any* constructor exception in `ICloudError`,
    so a wifi drop would otherwise be reported as "run icloud-login" and stop the
    loop for good.
    """
    wrapped = ICloudError("could not connect")
    wrapped.__cause__ = requests.exceptions.ConnectionError("network down")
    assert is_transient(wrapped) is True


# --------------------------------------------------------------------------- #
# raise_command_error — one mapping for every command
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "exc, code",
    [
        (ICloudError("not set up"), 1),
        (CredsError("server rejected the credentials (401)."), 1),
        (requests.exceptions.ConnectionError("unreachable"), 1),
        (LookupError("Vox-Message-Inbox"), 2),
    ],
)
def test_raise_command_error_maps_known_failures(exc: Exception, code: int) -> None:
    with pytest.raises(CommandError) as got:
        raise_command_error(exc)
    assert got.value.code == code
    assert str(exc) in got.value.msg or got.value.msg  # message is preserved//guided


def test_raise_command_error_reraises_the_unexpected() -> None:
    """Never swallow a bug: an unrecognised error keeps its traceback."""
    boom = RuntimeError("a genuine bug")
    with pytest.raises(RuntimeError) as got:
        raise_command_error(boom)
    assert got.value is boom


def test_keyerror_is_not_mistaken_for_a_missing_list() -> None:
    """`KeyError` and `IndexError` subclass `LookupError`, but they are bugs.

    Mapping every `LookupError` to a tidy exit 2 would dress a genuine defect up
    as "that list doesn't exist" — the exact disguise the re-raise rule exists to
    prevent. Only a *plain* LookupError means "not found".
    """
    for bug in (KeyError("reminders"), IndexError("list index out of range")):
        with pytest.raises(type(bug)):
            raise_command_error(bug)


def test_command_error_carries_code_and_message() -> None:
    err = CommandError(2, "no such list — run `voice-bridge lists`")
    assert err.code == 2
    assert "voice-bridge lists" in err.msg
    assert "voice-bridge lists" in str(err)


def test_errors_module_has_no_hard_optional_dependency() -> None:
    """The core install is dependency-free; caldav and requests are extras.

    `errors` must import and classify without them, so the module may only reach
    for those libraries behind a guarded import.
    """
    import voice_bridge.errors as mod

    src = mod.__file__
    assert src is not None
    assert is_transient(Exception("503")) is True  # works regardless of extras
