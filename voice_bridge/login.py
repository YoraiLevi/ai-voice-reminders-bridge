"""`icloud-login` - capture credentials securely, then establish a trusted session.

This used to read an Apple ID and password from a file the user had to write by
hand, and its only real job was passing a 2FA code along. Four things were wrong
with that, and they are fixed together because they are one flow:

* **It never persisted the trust token.** `validate_2fa_code` alone does not make
  a session durable - `trust_session()` does. Without it the session lapses far
  sooner than expected, and nobody connects the surprise re-prompt weeks later
  back to the login that "worked" (LOGIN-4). Success is now claimed only after
  `is_trusted_session` has actually been observed.
* **It could not capture credentials at all**, so the documented setup path was
  "hand-write this file" (LOGIN-0).
* **Failures were tracebacks**, not exit codes a script could act on (LOGIN-1).
* **Secrets were read lossily**, so a password containing quotes or spaces did
  not survive the round trip (LOGIN-2).

There is deliberately **no `--password` flag**: anything on argv is visible in the
process list to every other user on the machine. Passwords arrive through
`getpass` (no echo) or on stdin.

Testability shape: `_make_service` is the single injection point for building the
service, the interactive helpers are module-level so they can be replaced, and
`decide_intent` is pure - so the state machine can be exercised without a
terminal, which is why its branches are covered at all.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, TextIO

from .config import Config
from .prompting import ask, ask_secret
from .util import read_secret, write_env

_QUOTES = ("'", '"')


def resolve_code(
    *, code: str | None, code_file: str | None, code_stdin: bool, stdin: TextIO | None = None
) -> str:
    """Pick the 2FA code from (in order) --code, --code-file, stdin, or a prompt. Pure
    apart from the optional prompt, so the flag/file/stdin selection is unit-testable."""
    if code:
        return code.strip()
    if code_file:
        return Path(code_file).read_text(encoding="utf-8").strip()
    if code_stdin:
        return (stdin or sys.stdin).readline().strip()
    return ask("Enter the 6-digit 2FA code: ").strip()


# --------------------------------------------------------------------------- #
# seams - replaced wholesale in tests, so nothing here needs pyicloud or a TTY
# --------------------------------------------------------------------------- #


def _make_service(apple_id: str, password: str, cookie_dir: Path) -> Any:
    """Build the iCloud service. The ONE place the real library is constructed."""
    from pyicloud import PyiCloudService

    cookie_dir.mkdir(parents=True, exist_ok=True)
    return PyiCloudService(apple_id, password, cookie_directory=str(cookie_dir))


def _is_tty() -> bool:
    return sys.stdin.isatty()


def _prompt_apple_id() -> str:
    return ask("Apple ID (email): ").strip()


def _read_password(*, from_stdin: bool = False) -> str:
    """Never from argv. Piped input is taken verbatim apart from its line ending."""
    if from_stdin:
        return sys.stdin.readline().rstrip("\n").rstrip("\r")
    return ask_secret("Apple ID password (hidden): ")


def _confirm_use_existing() -> bool:
    return (ask("use existing credentials? [Y/n] ").strip().lower() or "y").startswith("y")


# --------------------------------------------------------------------------- #
# the decision, as a pure function
# --------------------------------------------------------------------------- #


def decide_intent(*, has_creds: bool, is_tty: bool, new: bool, enter_2fa: bool) -> str:
    """What this invocation should do, given only facts - no I/O, no prompting.

    Returns one of `capture` (ask for and store credentials), `reauth` (keep the
    credentials, force a fresh 2FA), `confirm` (ask whether to reuse),
    `use_existing`, or `no_creds_non_tty` (nothing stored and no way to ask).

    Purity is the point: the previous version's decisions were tangled with
    `input()` calls, so the branches could only be exercised by driving a real
    terminal - which meant they never were.
    """
    if new:
        return "capture"
    if enter_2fa:
        return "reauth" if has_creds else "capture"  # fall back rather than fail
    if not has_creds:
        return "capture" if is_tty else "no_creds_non_tty"
    return "confirm" if is_tty else "use_existing"


def _looks_quote_wrapped(value: str) -> bool:
    """True when a stored value starts and ends with the same quote character.

    Values are read verbatim - quotes are content, not syntax - so a hand-written
    `ICLOUD_PASSWORD="secret"` really does contain them. Stripping silently would
    reintroduce exactly the lossy transformation this module exists to remove, so
    instead we say what we see and let the user decide.
    """
    return len(value) >= 2 and value[0] == value[-1] and value[0] in _QUOTES


def _persist(cfg: Config, apple_id: str, password: str) -> None:
    """Store credentials only once they are known to work (see LOGIN-1)."""
    write_env(cfg.creds_env, {"ICLOUD_APPLE_ID": apple_id, "ICLOUD_PASSWORD": password})
    if sys.platform == "win32":
        print(f"note: {cfg.creds_env} holds your Apple ID password in plain text.")


def _clear_session(cfg: Config) -> None:
    """Drop cached cookies so the next construction really does re-authenticate."""
    if not cfg.cookie_dir.exists():
        return
    for child in cfg.cookie_dir.iterdir():
        if child.is_file():
            child.unlink()


def icloud_login(  # noqa: C901 - a flat state machine; clearer read end to end
    cfg: Config,
    *,
    code: str | None = None,
    code_file: str | None = None,
    code_stdin: bool = False,
    apple_id: str | None = None,
    password_stdin: bool = False,
    new: bool = False,
    enter_2fa: bool = False,
) -> int:
    """Establish a trusted iCloud session. Exit 0 success · 1 rejected · 2 you must act."""
    stored_id = read_secret(cfg.creds_env, "ICLOUD_APPLE_ID")
    stored_pw = read_secret(cfg.creds_env, "ICLOUD_PASSWORD")
    has_creds = bool(stored_id and stored_pw)

    intent = decide_intent(has_creds=has_creds, is_tty=_is_tty(), new=new, enter_2fa=enter_2fa)

    no_terminal_guidance = (
        f"error: no credentials in {cfg.creds_env}, and no terminal to ask.\n"
        "       pass --apple-id EMAIL with --password-stdin, or run --new on a terminal."
    )

    if intent == "no_creds_non_tty":
        print(no_terminal_guidance)
        return 2

    if intent == "confirm" and not _confirm_use_existing():
        intent = "capture"

    if intent == "capture":
        try:
            use_id = apple_id or _prompt_apple_id()
            use_pw = _read_password(from_stdin=password_stdin)
        except EOFError:
            # We asked, and there was nothing to read. `isatty()` is not always
            # honest - under some Windows shells it reports a terminal even when
            # stdin is redirected - so reaching EOF here means the same thing the
            # non-TTY branch means, and deserves the same actionable message
            # rather than a bare "cancelled".
            print(no_terminal_guidance)
            return 2
        # Ctrl-C is NOT caught here. `ask`/`ask_secret` turn it into `Cancelled`,
        # which the CLI's one handler reports - so this path does not grow a second
        # wording for the same keystroke. Nothing has been written at this point,
        # so unwinding loses nothing.
        persist_after = True
    else:
        use_id, use_pw = str(stored_id), str(stored_pw)
        persist_after = False
        if _looks_quote_wrapped(use_pw):
            print(
                "note: the stored password starts and ends with a quote character. Values are "
                "read literally, so those quotes are part of the password - remove them if the "
                "login below is rejected."
            )

    if intent == "reauth" or new:
        _clear_session(cfg)

    # --- authenticate -------------------------------------------------------
    try:
        api = _make_service(use_id, use_pw, cfg.cookie_dir)
    except ImportError:
        print("error: pyicloud not installed - install the 'icloud' extra.")
        return 2
    except OSError as exc:
        print(f"error: could not reach iCloud: {exc}")
        return 1
    except Exception as exc:
        # A wrong password lands here. Nothing has been written, which is the
        # point: rejected credentials must not be persisted for the next run to
        # trip over.
        print(f"error: iCloud login failed: {exc}")
        return 1

    # These two flags are NOT independent, which is what broke the first live run.
    # In pyicloud, `requires_2sa` is `hsaVersion >= 1` and `requires_2fa` is
    # `hsaVersion == 2`, so 2SA is a strict SUPERSET: a modern two-factor account
    # reports BOTH as true. Checking 2SA first therefore rejected every real 2FA
    # account with "not supported" before the 2FA branch could run - while Apple
    # was already showing the code on the user's phone. Only an account that wants
    # 2SA and NOT 2FA is the legacy case we cannot handle (LIVE-1).
    needs_2fa = bool(getattr(api, "requires_2fa", False))
    if getattr(api, "requires_2sa", False) and not needs_2fa:
        print(
            "error: this account uses legacy two-STEP verification (2SA), which is "
            "not supported - upgrade it to two-FACTOR (2FA) in your Apple ID settings."
        )
        return 2

    if persist_after:
        _persist(cfg, use_id, use_pw)

    # --- establish a durable session ---------------------------------------
    if not needs_2fa:
        print("session already trusted.")
        return 0

    if not (code or code_file or code_stdin) and not _is_tty():
        print(
            "error: a 2FA code is required and there is no terminal to ask - "
            "pass --code, --code-file, or --code-stdin."
        )
        return 2

    try:
        value = resolve_code(code=code, code_file=code_file, code_stdin=code_stdin)
    except OSError as exc:
        print(f"error: could not read the 2FA code: {exc}")
        return 2
    if not value:
        print("error: no 2FA code provided")
        return 2

    if not api.validate_2fa_code(value):
        print("error: 2FA code rejected")
        return 1

    # LOGIN-4: validating the code is not the same as persisting the trust. Ask
    # for the token, then VERIFY it stuck before telling the user they are done.
    trust = getattr(api, "trust_session", None)
    if callable(trust):
        trust()
    if not getattr(api, "is_trusted_session", False):
        print("error: the 2FA code was accepted but the session was not trusted - re-run.")
        return 1

    # Deliberately no lifetime claim: Apple documents no duration, and the old
    # message stated one ("~60 days") as though it were fact.
    print("trusted; session cached. Re-run this if you are asked for a code again.")
    return 0
