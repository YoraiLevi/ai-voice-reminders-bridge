"""Act 5's auth surfaces: an Apple password logging into a self-hosted server.

The human stood the HTTPS handoff up first try, and then hit both remaining
authentication surfaces in one run.

**With `ICLOUD_*` exported in their shell, the Radicale transport authenticated to the
local server with their APPLE credentials.** `doctor` had warned that env wins over the
creds file - the reporting was right and the precedence was wrong. Third instance of
transport-blindness: the credentials STEP (batch 11), the transport PARAMETER (batch 13),
and now the credentials LOADER.

Then the 401 it produced printed *"Next: voice-bridge icloud-login"* to a user on a
CalDAV server. Their comment, verbatim: `# icloud-login???`
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from voice_bridge.config import load_config
from voice_bridge.errors import auth_advice


def _cfg(tmp_path, tmp_mailbox, transport: str = "radicale"):
    cfg_file = tmp_path / "voice-bridge.json"
    cfg_file.write_text(
        json.dumps(
            {
                "transport": transport,
                "mailbox_dir": str(tmp_mailbox),
                "state_dir": str(tmp_path / "state"),
            }
        ),
        encoding="utf-8",
    )
    return load_config(cfg_file)


def _write_creds(cfg, user: str, password: str) -> None:
    cfg.creds_env.parent.mkdir(parents=True, exist_ok=True)
    cfg.creds_env.write_text(
        f"ICLOUD_CALDAV_URL=http://127.0.0.1:5299\n"
        f"ICLOUD_APPLE_ID={user}\n"
        f"ICLOUD_APP_PASSWORD={password}\n",
        encoding="utf-8",
    )


# --------------------------------------------------------------------------- #
# 1 - the precedence that let an Apple password reach a Radicale server
# --------------------------------------------------------------------------- #


def test_apple_env_no_longer_overrides_a_radicale_creds_file(tmp_path, tmp_mailbox, monkeypatch):
    """The field defect exactly: the file says `vox`, the shell says the Apple ID, and
    the server was being handed the Apple ID."""
    from voice_bridge.caldav import _creds

    cfg = _cfg(tmp_path, tmp_mailbox)
    _write_creds(cfg, "vox", "server-password")
    monkeypatch.setenv("ICLOUD_APPLE_ID", "human@example.com")
    monkeypatch.setenv("ICLOUD_APP_PASSWORD", "apple-app-password")

    user, password, _url = _creds(cfg)

    assert user == "vox", "a file written FOR this transport outranks a foreign env name"
    assert password == "server-password"


def test_apple_env_is_still_used_when_there_is_no_creds_file(tmp_path, tmp_mailbox, monkeypatch):
    """Tier 3 survives deliberately: this adapter also serves iCloud over CalDAV, where
    those names are exactly right, and a machine with no creds file must keep working.
    What `ICLOUD_*` may no longer do is override the file."""
    from voice_bridge.caldav import _creds

    cfg = _cfg(tmp_path, tmp_mailbox)
    monkeypatch.setenv("ICLOUD_APPLE_ID", "human@example.com")
    monkeypatch.setenv("ICLOUD_APP_PASSWORD", "apple-app-password")

    user, password, _url = _creds(cfg)

    assert user == "human@example.com"
    assert password == "apple-app-password"


def test_radicale_named_env_wins_over_everything(tmp_path, tmp_mailbox, monkeypatch):
    """Tier 1. A variable named for the backend you are talking to is an unambiguous
    instruction, so it outranks even the file - which is what makes headless and CI runs
    possible on this transport without borrowing Apple's names."""
    from voice_bridge.caldav import _creds

    cfg = _cfg(tmp_path, tmp_mailbox)
    _write_creds(cfg, "vox", "server-password")
    monkeypatch.setenv("ICLOUD_APPLE_ID", "human@example.com")
    monkeypatch.setenv("RADICALE_USER", "ci-user")
    monkeypatch.setenv("RADICALE_PASSWORD", "ci-password")

    user, password, _url = _creds(cfg)

    assert (user, password) == ("ci-user", "ci-password")


def test_the_icloud_transport_reads_ONLY_its_file(tmp_path, tmp_mailbox, monkeypatch):
    """Written to pin "iCloud keeps env-over-file" - and it found the opposite.

    `ICloudTransport.connect` calls `read_kv(creds_env, ...)`: the file and nothing else.
    The iCloud path has NEVER consulted the environment. So the doctor row claiming env
    "WINS over" the file was a false statement on this transport all along, pointing the
    opposite way from the radicale one - two wrong claims out of one sentence written
    before either adapter's behaviour was checked against it.
    """
    from voice_bridge.util import read_kv

    cfg = _cfg(tmp_path, tmp_mailbox, transport="icloud")
    cfg.creds_env.parent.mkdir(parents=True, exist_ok=True)
    cfg.creds_env.write_text(
        "ICLOUD_APPLE_ID=file@example.com\nICLOUD_PASSWORD=file-password\n", encoding="utf-8"
    )
    monkeypatch.setenv("ICLOUD_APPLE_ID", "env@example.com")
    monkeypatch.setenv("ICLOUD_PASSWORD", "env-password")

    assert read_kv(cfg.creds_env, "ICLOUD_APPLE_ID") == "file@example.com"
    assert read_kv(cfg.creds_env, "ICLOUD_PASSWORD") == "file-password"


def test_doctor_tells_an_icloud_user_their_env_is_being_IGNORED(tmp_path, tmp_mailbox, monkeypatch):
    """The corrected half of the same row: they have set credentials that do nothing,
    which is worth a WARN - just not the WARN it used to print."""
    from voice_bridge import doctor

    cfg = _cfg(tmp_path, tmp_mailbox, transport="icloud")
    cfg.creds_env.parent.mkdir(parents=True, exist_ok=True)
    cfg.creds_env.write_text(
        "ICLOUD_APPLE_ID=file@example.com\nICLOUD_PASSWORD=file-password\n", encoding="utf-8"
    )
    monkeypatch.setenv("ICLOUD_PASSWORD", "env-password")

    _name, verdict, detail = doctor._creds_row(cfg)

    assert verdict == "WARN"
    assert "ignored" in detail
    assert "WIN over" not in detail


def test_doctor_stops_claiming_env_wins_where_it_no_longer_does(tmp_path, tmp_mailbox, monkeypatch):
    """The row that correctly reported the OLD behaviour would be a false claim under the
    new one. A stale warning is a false claim wearing a helpful face."""
    from voice_bridge import doctor

    cfg = _cfg(tmp_path, tmp_mailbox)
    _write_creds(cfg, "vox", "server-password")
    monkeypatch.setenv("ICLOUD_APPLE_ID", "human@example.com")

    name, verdict, detail = doctor._creds_row(cfg)

    assert (name, verdict) == ("creds file", "WARN")
    assert "no longer override" in detail
    assert "WIN over" not in detail


# --------------------------------------------------------------------------- #
# 2 - advice that names the right remedy for the transport in use
# --------------------------------------------------------------------------- #


def test_radicale_auth_advice_never_says_icloud_login(tmp_path, tmp_mailbox, monkeypatch):
    """`# icloud-login???` - the human's own comment, and the fourth instance of a message
    naming a remedy that is a claim about the cause."""
    monkeypatch.delenv("ICLOUD_APPLE_ID", raising=False)
    cfg = _cfg(tmp_path, tmp_mailbox)

    text = "\n".join(auth_advice(cfg.transport, cfg.creds_env))

    assert "icloud-login` is not the remedy" in text
    assert str(cfg.creds_env) in text
    assert "radicale-server init --force" in text


def test_the_advice_leads_with_the_cause_that_is_hardest_to_see(tmp_path, tmp_mailbox, monkeypatch):
    """Ordered by how silently each bites. An exported Apple variable produces a 401 while
    every file on disk looks correct, so it goes first - and the advice says whether it is
    actually set rather than listing it as a possibility."""
    cfg = _cfg(tmp_path, tmp_mailbox)
    monkeypatch.setenv("ICLOUD_APPLE_ID", "human@example.com")

    lines = auth_advice(cfg.transport, cfg.creds_env)
    first = next(line for line in lines if line.strip().startswith("1."))

    assert "ICLOUD_APPLE_ID" in first
    assert "set in your environment" in first


def test_the_advice_says_so_when_env_is_NOT_the_cause(tmp_path, tmp_mailbox, monkeypatch):
    """Ruling out a cause is information too, and it stops the reader hunting for a
    variable that is not there."""
    for k in ("ICLOUD_APPLE_ID", "ICLOUD_USERNAME", "ICLOUD_PASSWORD", "ICLOUD_APP_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
    cfg = _cfg(tmp_path, tmp_mailbox)

    lines = auth_advice(cfg.transport, cfg.creds_env)

    assert any("not the cause" in line for line in lines)


def test_icloud_advice_is_unchanged(tmp_path, tmp_mailbox):
    """On iCloud the remedy really IS `icloud-login`. The fix is transport-awareness, not
    the removal of correct advice."""
    cfg = _cfg(tmp_path, tmp_mailbox, transport="icloud")

    text = "\n".join(auth_advice(cfg.transport, cfg.creds_env))

    assert "voice-bridge icloud-login" in text
    assert "not the remedy" not in text


# --------------------------------------------------------------------------- #
# 3 - a permanent auth failure is "you must act", not success
# --------------------------------------------------------------------------- #


def test_setup_exits_2_on_a_permanent_auth_failure(tmp_path, tmp_mailbox, monkeypatch, capsys):
    """It printed "config written. Could not authenticate" and exited 0, so a scripted
    caller saw success for a run that authenticated to nothing. Exit 2 is the contract's
    "you must act"; only the transient branch may end quietly."""
    from voice_bridge import setup as setup_mod
    from voice_bridge.icloud import ICloudError

    cfg_file = tmp_path / "voice-bridge.json"
    cfg_file.write_text(
        json.dumps(
            {
                "transport": "radicale",
                "mailbox_dir": str(tmp_mailbox),
                "state_dir": str(tmp_path / "state"),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(setup_mod, "_is_tty", lambda: False)
    monkeypatch.setattr(
        setup_mod, "make_transport", lambda _cfg: (_ for _ in ()).throw(ICloudError("401 auth"))
    )
    # THE SERVER IS ASSUMED REACHABLE, EXPLICITLY.
    #
    # This test went red in CI and green on two developer machines, and the reason was
    # neither the code nor the credentials: `run_setup` checks `is_reachable` before it
    # reaches the auth path, and the human's live act-5 Radicale was listening on the
    # default port here. So the test passed BECAUSE somebody else's server was up - the
    # same interference that made an e2e case fail an hour earlier, arriving in the more
    # dangerous direction. A test about the AUTH handler must not depend on a listener.
    from voice_bridge import server as server_mod

    monkeypatch.setattr(server_mod, "is_reachable", lambda _url: True)

    code = setup_mod.run_setup(config_path=cfg_file)
    out = capsys.readouterr().out

    assert code == 2
    assert "icloud-login    then re-run" not in out, "and not the wrong remedy"
    assert "is not the remedy" in out


def test_a_transient_failure_still_ends_quietly(tmp_path, tmp_mailbox, monkeypatch, capsys):
    """The distinction the exit code is FOR: a blip is not something to act on, so it must
    not start returning 2 just because auth now does."""
    import requests

    from voice_bridge import setup as setup_mod

    cfg_file = tmp_path / "voice-bridge.json"
    cfg_file.write_text(
        json.dumps({"mailbox_dir": str(tmp_mailbox), "state_dir": str(tmp_path / "state")}),
        encoding="utf-8",
    )
    monkeypatch.setattr(setup_mod, "_is_tty", lambda: False)
    monkeypatch.setattr(
        setup_mod,
        "make_transport",
        lambda _cfg: (_ for _ in ()).throw(requests.Timeout("read timed out")),
    )

    code = setup_mod.run_setup(config_path=cfg_file)

    assert code == 0, "a transient failure is not 'you must act'"


@pytest.mark.parametrize("transport", ["icloud", "radicale"])
def test_no_advice_site_hardcodes_icloud_login_for_every_transport(
    transport, tmp_path, tmp_mailbox
):
    """The sweep, as ordered. Any surface that advises `icloud-login` must reach that
    string through `auth_advice`, so a new transport cannot inherit iCloud's remedy by
    default."""
    cfg = _cfg(tmp_path, tmp_mailbox, transport=transport)

    text = "\n".join(auth_advice(cfg.transport, cfg.creds_env))
    mentions_login = "voice-bridge icloud-login" in text

    assert mentions_login is (transport == "icloud")


def test_the_setup_handler_uses_the_shared_advice_not_its_own_string():
    """Enumerated rather than remembered: `setup.py` must not carry its own copy of the
    remedy, or the two wordings drift and only one of them learns about a new transport."""
    import ast

    from voice_bridge import setup as setup_mod

    source = Path(setup_mod.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    literals = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]
    offenders = [lit for lit in literals if "icloud-login" in lit and "then re-run" in lit]

    assert not offenders, f"setup.py still prints its own login advice: {offenders}"
    assert "auth_advice" in source


def test_an_unreachable_server_is_an_obstacle_not_a_success(
    tmp_path, tmp_mailbox, monkeypatch, capsys
):
    """Found by reproducing the CI failure rather than by reading the code.

    `setup` on radicale with the server down printed "the server is configured but not
    answering - start it, then re-run setup" and returned **0**. Two lines telling the
    user to go and do something, followed by an exit code that says nothing needs doing.

    Batch 15 fixed exactly this shape one branch away (a permanent auth failure exiting 0)
    and left the neighbour, which is the third time this arc that one ruling reached half
    its surface. The rule, now stated where both branches can see it:

        AN OBSTACLE IS 2. A DECLINE IS 0.

    A user whose server is down did not choose that. A user who skips a step did.
    """
    import socket

    from voice_bridge import server as server_mod, setup as setup_mod

    with socket.socket() as probe:  # a port nothing is on, chosen not assumed
        probe.bind(("127.0.0.1", 0))
        dead_port = probe.getsockname()[1]

    cfg_file = tmp_path / "voice-bridge.json"
    cfg_file.write_text(
        json.dumps(
            {
                "transport": "radicale",
                "mailbox_dir": str(tmp_mailbox),
                "state_dir": str(tmp_path / "state"),
                "radicale_port": dead_port,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(setup_mod, "_is_tty", lambda: False)
    assert not server_mod.is_reachable(server_mod.client_url(load_config(cfg_file)))

    code = setup_mod.run_setup(config_path=cfg_file)
    out = capsys.readouterr().out

    assert code == 2, "an unreachable server is something the user must act on"
    assert "not answering" in out
    assert "radicale-server start --background" in out


def test_a_declined_step_is_still_zero(tmp_path, tmp_mailbox, monkeypatch, capsys):
    """The other half of the rule, pinned so the fix above cannot generalise into it.

    Skipping the credentials step is a CHOICE. The run did what the user asked, it names
    the command to finish later, and turning that into exit 2 would tell a script that a
    deliberate decision was a problem.

    ⚑ THIS TEST USED THE WRONG STEP TO MAKE A RIGHT POINT, and in doing so PINNED A
    DEFECT IN PLACE for three batches. It stubbed the RADICALE credentials step, which
    asks nothing and therefore cannot be declined, and asserted the decline exit code.
    So a radicale install with no credentials at all reported success, and the test made
    that look deliberate. A test can be worse than absent: an absent one leaves a gap,
    and this one filled the gap with a wrong answer wearing a green tick.

    The decline case belongs on the ICLOUD step, which actually asks. Its sibling - the
    radicale step, which is always an obstacle - is asserted directly below.
    """
    from voice_bridge import onboard, setup as setup_mod

    cfg_file = tmp_path / "voice-bridge.json"
    cfg_file.write_text(
        json.dumps(
            {
                "transport": "icloud",
                "mailbox_dir": str(tmp_mailbox),
                "state_dir": str(tmp_path / "state"),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(setup_mod, "_is_tty", lambda: True)
    monkeypatch.setattr(setup_mod, "_ask", lambda *_a, **_k: "")
    monkeypatch.setattr(setup_mod, "_ask_line", lambda *_a, **_k: "")
    monkeypatch.setattr(onboard, "step_credentials", lambda *_a, **_k: onboard.DECLINED)

    code = setup_mod.run_setup(config_path=cfg_file)

    assert code == 0, "a step the user declined is not an obstacle"


def test_missing_radicale_credentials_are_an_obstacle_not_a_decline(
    tmp_path, tmp_mailbox, monkeypatch, capsys
):
    """The sibling, and the fourth surface of one ruling finally closing.

    Nothing is asked on this path: the credentials are CREATED by `radicale-server init`,
    and a user who does not have them was handed two commands to go and run. That is the
    exit-code contract's "2 = you must act" in as pure a form as it comes - and it was
    exiting 0, so a scripted install of a spoke that could authenticate to nothing
    reported success.
    """
    from voice_bridge import onboard, setup as setup_mod

    cfg_file = tmp_path / "voice-bridge.json"
    cfg_file.write_text(
        json.dumps(
            {
                "transport": "radicale",
                "mailbox_dir": str(tmp_mailbox),
                "state_dir": str(tmp_path / "state"),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(setup_mod, "_is_tty", lambda: True)
    monkeypatch.setattr(setup_mod, "_ask", lambda *_a, **_k: "")
    monkeypatch.setattr(setup_mod, "_ask_line", lambda *_a, **_k: "")
    monkeypatch.setattr(onboard, "step_radicale_credentials", lambda *_a, **_k: onboard.BLOCKED)

    assert setup_mod.run_setup(config_path=cfg_file) == 2


def test_a_failed_icloud_login_is_an_obstacle_too(tmp_path, tmp_mailbox, monkeypatch, capsys):
    """The same conflation from the other side. `step_credentials` returned False both
    when the user said no AND when the login FAILED - one answer for a choice and a
    breakage, so the caller could only pick one exit code for both."""
    from voice_bridge import onboard, setup as setup_mod

    cfg_file = tmp_path / "voice-bridge.json"
    cfg_file.write_text(
        json.dumps(
            {
                "transport": "icloud",
                "mailbox_dir": str(tmp_mailbox),
                "state_dir": str(tmp_path / "state"),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(setup_mod, "_is_tty", lambda: True)
    monkeypatch.setattr(setup_mod, "_ask", lambda *_a, **_k: "")
    monkeypatch.setattr(setup_mod, "_ask_line", lambda *_a, **_k: "")
    monkeypatch.setattr(onboard, "step_credentials", lambda *_a, **_k: onboard.BLOCKED)

    assert setup_mod.run_setup(config_path=cfg_file) == 2
