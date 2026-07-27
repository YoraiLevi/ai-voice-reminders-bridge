"""The `icloud-login` flow, and the pure pieces underneath it.

Nothing here touches pyicloud or the network. `login._make_service` is the single
injection point for building the service, and the interactive helpers are
module-level so a test can replace them — which is what makes a state machine
full of prompts and terminals testable at all.
"""

from __future__ import annotations

import io

import pytest

from voice_bridge import login as login_mod
from voice_bridge.login import resolve_code
from voice_bridge.util import read_secret, write_env

GOOD = "correct-horse"


def test_code_from_flag():
    assert resolve_code(code="123456", code_file=None, code_stdin=False) == "123456"


def test_code_from_file(tmp_path):
    f = tmp_path / "code.txt"
    f.write_text("654321\n", encoding="utf-8")
    assert resolve_code(code=None, code_file=str(f), code_stdin=False) == "654321"


def test_code_from_stdin():
    assert (
        resolve_code(code=None, code_file=None, code_stdin=True, stdin=io.StringIO("111222\n"))
        == "111222"
    )


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def _seed_creds(cfg, apple_id="me@icloud.com", password=GOOD):
    write_env(cfg.creds_env, {"ICLOUD_APPLE_ID": apple_id, "ICLOUD_PASSWORD": password})


def _no_prompts(monkeypatch, *, apple_id="me@icloud.com", password=GOOD, confirm=True):
    """Replace every interactive helper so a test never blocks on input."""
    monkeypatch.setattr(login_mod, "_prompt_apple_id", lambda: apple_id)
    monkeypatch.setattr(login_mod, "_read_password", lambda **kw: password)
    monkeypatch.setattr(login_mod, "_confirm_use_existing", lambda: confirm)
    monkeypatch.setattr(login_mod, "_is_tty", lambda: True)


class _Trusted:
    """Minimal already-trusted service, for the lossless-password cases."""

    def __init__(self, password):
        self.password = password
        self.requires_2fa = False
        self.requires_2sa = False
        self.is_trusted_session = True
        self.calls = ["construct"]


# --------------------------------------------------------------------------- #
# decide_intent — pure, so the whole state machine is testable without I/O
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "has_creds, is_tty, new, enter_2fa, expected",
    [
        (False, True, False, False, "capture"),            # fresh machine
        (True, True, False, False, "confirm"),             # ask before reusing
        (True, False, False, False, "use_existing"),       # non-TTY: no EOFError
        (False, False, False, False, "no_creds_non_tty"),  # nothing to do, say so
        (True, True, True, False, "capture"),              # --new overwrites
        (False, False, True, False, "capture"),            # --new works headless too
        (True, True, False, True, "reauth"),               # --enter-2fa keeps creds
        (False, True, False, True, "capture"),             # ...but falls back if none
    ],
)
def test_decide_intent_table(has_creds, is_tty, new, enter_2fa, expected):
    got = login_mod.decide_intent(
        has_creds=has_creds, is_tty=is_tty, new=new, enter_2fa=enter_2fa
    )
    assert got == expected


# --------------------------------------------------------------------------- #
# LOGIN-0 — intent branches
# --------------------------------------------------------------------------- #

def test_fresh_machine_captures_then_trusts(sample_config, fake_icloud, monkeypatch):
    _no_prompts(monkeypatch)
    assert login_mod.icloud_login(sample_config, code="123456") == 0
    assert read_secret(sample_config.creds_env, "ICLOUD_PASSWORD") == GOOD
    assert "trust_session" in fake_icloud()["service"].calls


def test_existing_creds_confirmed_and_already_trusted(sample_config, fake_icloud, monkeypatch,
                                                      capsys):
    fake_icloud(requires_2fa=False)
    _seed_creds(sample_config)
    _no_prompts(monkeypatch, confirm=True)
    assert login_mod.icloud_login(sample_config) == 0
    assert "already trusted" in capsys.readouterr().out.lower()


def test_declining_the_confirm_recaptures(sample_config, fake_icloud, monkeypatch):
    _seed_creds(sample_config, password="old-password")
    _no_prompts(monkeypatch, confirm=False, password=GOOD)
    assert login_mod.icloud_login(sample_config, code="123456") == 0
    assert read_secret(sample_config.creds_env, "ICLOUD_PASSWORD") == GOOD


def test_non_tty_uses_existing_without_prompting(sample_config, fake_icloud, monkeypatch):
    """A scheduled run must not die on EOFError trying to ask a question."""
    fake_icloud(requires_2fa=False)
    _seed_creds(sample_config)
    monkeypatch.setattr(login_mod, "_is_tty", lambda: False)

    def explode(*a, **k):
        raise AssertionError("must not prompt when there is no terminal")

    monkeypatch.setattr(login_mod, "_confirm_use_existing", explode)
    monkeypatch.setattr(login_mod, "_prompt_apple_id", explode)
    assert login_mod.icloud_login(sample_config) == 0


def test_non_tty_without_creds_exits_2_with_guidance(sample_config, fake_icloud, monkeypatch,
                                                     capsys):
    monkeypatch.setattr(login_mod, "_is_tty", lambda: False)
    assert login_mod.icloud_login(sample_config) == 2
    out = capsys.readouterr().out
    assert "--apple-id" in out or "--new" in out


def test_new_flag_overwrites_existing_credentials(sample_config, fake_icloud, monkeypatch):
    _seed_creds(sample_config, password="stale")
    _no_prompts(monkeypatch, password=GOOD)
    assert login_mod.icloud_login(sample_config, new=True, code="123456") == 0
    assert read_secret(sample_config.creds_env, "ICLOUD_PASSWORD") == GOOD


def test_enter_2fa_keeps_creds_and_forces_a_fresh_code(sample_config, fake_icloud, monkeypatch):
    _seed_creds(sample_config)
    _no_prompts(monkeypatch)
    assert login_mod.icloud_login(sample_config, enter_2fa=True, code="123456") == 0
    calls = fake_icloud()["service"].calls
    assert any(c.startswith("validate_2fa_code") for c in calls)
    assert read_secret(sample_config.creds_env, "ICLOUD_APPLE_ID") == "me@icloud.com"


def test_enter_2fa_without_creds_falls_back_to_capture(sample_config, fake_icloud, monkeypatch):
    _no_prompts(monkeypatch)
    assert login_mod.icloud_login(sample_config, enter_2fa=True, code="123456") == 0
    assert read_secret(sample_config.creds_env, "ICLOUD_PASSWORD") == GOOD


# --------------------------------------------------------------------------- #
# Security — the password must never reach argv
# --------------------------------------------------------------------------- #

def test_password_stdin_does_not_call_getpass(sample_config, fake_icloud, monkeypatch):
    monkeypatch.setattr(login_mod, "_prompt_apple_id", lambda: "me@icloud.com")
    monkeypatch.setattr(login_mod, "_is_tty", lambda: True)
    monkeypatch.setattr(login_mod, "_confirm_use_existing", lambda: False)

    def no_getpass(*a, **k):
        raise AssertionError("getpass must not be used when the password is piped")

    monkeypatch.setattr(login_mod.getpass, "getpass", no_getpass)
    monkeypatch.setattr(login_mod.sys, "stdin", io.StringIO(GOOD + "\n"))
    assert login_mod.icloud_login(sample_config, password_stdin=True, code="123456") == 0


def test_interactive_password_goes_through_getpass(sample_config, fake_icloud, monkeypatch):
    """No echo, and never argv — the reason there is no --password flag."""
    seen = {}

    def fake_getpass(prompt=""):
        seen["called"] = True
        return GOOD

    monkeypatch.setattr(login_mod, "_prompt_apple_id", lambda: "me@icloud.com")
    monkeypatch.setattr(login_mod, "_is_tty", lambda: True)
    monkeypatch.setattr(login_mod.getpass, "getpass", fake_getpass)
    assert login_mod.icloud_login(sample_config, code="123456") == 0
    assert seen.get("called") is True


# --------------------------------------------------------------------------- #
# LOGIN-1 — clean exits, never a traceback
# --------------------------------------------------------------------------- #

def test_wrong_password_exits_1_and_persists_nothing(sample_config, fake_icloud, monkeypatch,
                                                     capsys):
    _no_prompts(monkeypatch, password="wrong-password")
    assert login_mod.icloud_login(sample_config) == 1
    assert "error" in capsys.readouterr().out.lower()
    assert read_secret(sample_config.creds_env, "ICLOUD_PASSWORD") is None, (
        "a rejected password must not be written to disk"
    )


def test_missing_code_file_exits_2(sample_config, fake_icloud, monkeypatch, capsys):
    _no_prompts(monkeypatch)
    assert login_mod.icloud_login(sample_config, code_file="/nope/absent.txt") == 2
    assert "error" in capsys.readouterr().out.lower()


def test_code_needed_but_no_tty_exits_2(sample_config, fake_icloud, monkeypatch, capsys):
    _seed_creds(sample_config)
    monkeypatch.setattr(login_mod, "_is_tty", lambda: False)
    assert login_mod.icloud_login(sample_config) == 2
    assert "code" in capsys.readouterr().out.lower()


def test_network_failure_exits_1(sample_config, monkeypatch):
    _no_prompts(monkeypatch)

    def dead(*a, **k):
        raise OSError("network unreachable")

    monkeypatch.setattr(login_mod, "_make_service", dead)
    assert login_mod.icloud_login(sample_config) == 1


# --------------------------------------------------------------------------- #
# LOGIN-4 — the durability sentinel
# --------------------------------------------------------------------------- #

def test_success_calls_trust_session_and_verifies_it(sample_config, fake_icloud, monkeypatch):
    """Delete `trust_session()` from the implementation and this goes red.

    Without it the trust token is never persisted, so the session lapses and the
    user is asked to re-enter a code far sooner than expected — a slow failure
    nobody connects back to login.
    """
    _no_prompts(monkeypatch)
    assert login_mod.icloud_login(sample_config, code="123456") == 0
    svc = fake_icloud()["service"]
    assert "trust_session" in svc.calls
    assert svc.is_trusted_session is True


def test_trust_that_does_not_stick_exits_1(sample_config, fake_icloud, monkeypatch, capsys):
    """Claiming success without verifying is how the old message came to lie."""
    fake_icloud(trust_works=False)
    _no_prompts(monkeypatch)
    assert login_mod.icloud_login(sample_config, code="123456") == 1
    assert "trust" in capsys.readouterr().out.lower()


def test_success_message_makes_no_unverified_lifetime_claim(sample_config, fake_icloud,
                                                            monkeypatch, capsys):
    """The shipped message claimed "~60 days", a number Apple does not document."""
    _no_prompts(monkeypatch)
    login_mod.icloud_login(sample_config, code="123456")
    assert "60" not in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# LOGIN-3 — legacy two-step accounts
# --------------------------------------------------------------------------- #

def test_two_step_account_exits_2_saying_so(sample_config, fake_icloud, monkeypatch, capsys):
    fake_icloud(requires_2fa=False, requires_2sa=True)
    _no_prompts(monkeypatch)
    assert login_mod.icloud_login(sample_config) == 2
    assert "2sa" in capsys.readouterr().out.lower()


# --------------------------------------------------------------------------- #
# LOGIN-2 — the captured password survives verbatim (the A+ cutover)
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("pw", ["p@ss w0rd", 'has"quote', "has=equals", "unicode-ue-key", "tail "])
def test_capture_persists_the_exact_password(sample_config, monkeypatch, pw):
    monkeypatch.setattr(
        login_mod, "_make_service", lambda apple_id, password, cookie_dir: _Trusted(password)
    )
    _no_prompts(monkeypatch, password=pw)
    assert login_mod.icloud_login(sample_config) == 0
    assert read_secret(sample_config.creds_env, "ICLOUD_PASSWORD") == pw


def test_quote_wrapped_stored_password_warns(sample_config, monkeypatch, capsys):
    """A+ ruling: read it verbatim, but say so, so the confusion self-diagnoses.

    Values are literal, so a hand-written `PASSWORD="secret"` really does contain
    the quotes. Rather than silently stripping them — the transformation LOGIN-2
    exists to remove — say that they look wrapped and let the user decide.
    """
    wrapped = '"wrapped"'
    write_env(
        sample_config.creds_env,
        {"ICLOUD_APPLE_ID": "me@icloud.com", "ICLOUD_PASSWORD": wrapped},
    )
    monkeypatch.setattr(
        login_mod, "_make_service", lambda apple_id, password, cookie_dir: _Trusted(password)
    )
    monkeypatch.setattr(login_mod, "_is_tty", lambda: False)
    login_mod.icloud_login(sample_config)
    assert "quote" in capsys.readouterr().out.lower()


def test_eof_while_prompting_gives_the_same_guidance_as_no_tty(sample_config, monkeypatch,
                                                               capsys):
    """`isatty()` is not always honest, so the EOF path must be equally helpful.

    Under some Windows shells stdin reports a terminal even when redirected, so
    the flow reaches the prompt and immediately hits EOF. That is the same
    situation the non-TTY branch handles, and it earns the same actionable
    message rather than a bare "cancelled".
    """
    monkeypatch.setattr(login_mod, "_is_tty", lambda: True)

    def eof():
        raise EOFError

    monkeypatch.setattr(login_mod, "_prompt_apple_id", eof)
    assert login_mod.icloud_login(sample_config) == 2
    out = capsys.readouterr().out
    assert "--apple-id" in out and "--password-stdin" in out
