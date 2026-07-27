"""e2e: drive cli.main(argv) and assert dispatch + exit codes. In-process (no
subprocess) for speed/reliability; the console script is smoke-tested separately."""

from __future__ import annotations

import json
import os

import pytest

from voice_bridge.cli import main


def _cfg(tmp_path, tmp_mailbox) -> str:
    p = tmp_path / "voice-bridge.json"
    p.write_text(
        json.dumps({"mailbox_dir": str(tmp_mailbox), "state_dir": str(tmp_path / "state")}),
        encoding="utf-8",
    )
    return str(p)


def test_no_command_returns_2(capsys):
    assert main([]) == 2


def test_unknown_subcommand_exits_2():
    with pytest.raises(SystemExit) as e:
        main(["bogus"])
    assert e.value.code == 2


def test_config_show(tmp_path, tmp_mailbox, capsys):
    assert main(["--config", _cfg(tmp_path, tmp_mailbox), "config", "show"]) == 0
    assert "spoke_name" in capsys.readouterr().out


def test_config_fields(capsys):
    assert main(["config", "fields"]) == 0
    assert "poll_interval" in capsys.readouterr().out


def test_config_set_and_get(tmp_path, tmp_mailbox, capsys):
    cfg = _cfg(tmp_path, tmp_mailbox)
    assert main(["--config", cfg, "config", "set", "poll_interval", "30"]) == 0
    assert main(["--config", cfg, "config", "get", "poll_interval"]) == 0
    assert capsys.readouterr().out.strip().endswith("30")


def test_config_set_unknown_key_returns_2(tmp_path, tmp_mailbox, capsys):
    assert main(["--config", _cfg(tmp_path, tmp_mailbox), "config", "set", "bogus", "1"]) == 2


def test_run_dry_run(tmp_path, tmp_mailbox, capsys):
    assert main(["--config", _cfg(tmp_path, tmp_mailbox), "run", "--dry-run"]) == 0
    assert "would append to" in capsys.readouterr().out


def test_vox_prompt_renders_list_names(tmp_path, tmp_mailbox, capsys):
    assert main(["--config", _cfg(tmp_path, tmp_mailbox), "vox-prompt"]) == 0
    out = capsys.readouterr().out
    assert "Vox-Message-Inbox" in out and "Vox-Message-Outbox" in out


def test_notify_no_topic_returns_2(tmp_path, tmp_mailbox, capsys):
    assert main(["--config", _cfg(tmp_path, tmp_mailbox), "notify", "hi"]) == 2


def test_radicale_server_url(tmp_path, tmp_mailbox, capsys):
    cfg = _cfg(tmp_path, tmp_mailbox)
    main(["--config", cfg, "config", "set", "radicale_port", "5299"])
    assert main(["--config", cfg, "radicale-server", "url"]) == 0
    assert "http://127.0.0.1:5299" in capsys.readouterr().out


def test_radicale_server_status_down(tmp_path, tmp_mailbox, capsys):
    # nothing running -> reachable False -> exit 1
    assert main(["--config", _cfg(tmp_path, tmp_mailbox), "radicale-server", "status"]) == 1
    assert "reachable" in capsys.readouterr().out


def test_status_and_json(tmp_path, tmp_mailbox, capsys):
    cfg = _cfg(tmp_path, tmp_mailbox)
    assert main(["--config", cfg, "status"]) == 0
    assert "transport" in capsys.readouterr().out
    assert main(["--json", "--config", cfg, "status"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["transport"] == "icloud" and data["poller_running"] is False


def test_tail_prints_existing(tmp_path, tmp_mailbox, capsys):
    cfg_file = _cfg(tmp_path, tmp_mailbox)
    from voice_bridge.config import load_config

    cfg = load_config(cfg_file)
    cfg.peer_inbox.parent.mkdir(parents=True, exist_ok=True)
    cfg.peer_inbox.write_text("- [08:48] (vox) hello\n", encoding="utf-8")
    assert main(["--config", cfg_file, "tail", "--box", "manager"]) == 0
    assert "[manager] - [08:48] (vox) hello" in capsys.readouterr().out


def test_send_pushes_to_outbox(tmp_path, tmp_mailbox, monkeypatch, capsys):
    import voice_bridge.commands as commands_mod
    from voice_bridge.transport import FakeTransport

    t = FakeTransport()
    t.add_list("Vox-Message-Inbox")
    t.add_list("Vox-Message-Outbox")
    # `send` now builds its transport through the shared connected_transport seam,
    # which is where the typed error mapping lives — so that is what to intercept.
    monkeypatch.setattr(commands_mod, "make_transport", lambda cfg: t)
    assert main(["--config", _cfg(tmp_path, tmp_mailbox), "send", "hi there", "--no-notify"]) == 0
    out = t.resolve_list("Vox-Message-Outbox")
    assert "hi there" in t.read_incomplete(out)[0].title


def test_vox_prompt_survives_a_non_utf8_console(tmp_path, tmp_mailbox):
    """The one test here that MUST be a subprocess.

    `vox-prompt | clip` is the documented way to get the prompt onto a phone, and
    the prompt contains `→`. On Windows a redirected stdout defaults to cp1252,
    which cannot encode that character, so the command died with a
    UnicodeEncodeError — reported as a generic `error:` because UnicodeEncodeError
    subclasses ValueError. In-process tests cannot catch this: pytest's captured
    stdout is already unicode-safe.

    Forcing the child to cp1252 reproduces the failure deterministically on every
    platform, so this stays honest on the Linux CI leg too.
    """
    import subprocess
    import sys

    env = {**os.environ, "PYTHONIOENCODING": "cp1252", "PYTHONUTF8": "0"}
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "from voice_bridge.cli import main; raise SystemExit(main(['--config', "
            f"{_cfg(tmp_path, tmp_mailbox)!r}, 'vox-prompt']))",
        ],
        capture_output=True,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    assert "→".encode() in proc.stdout, "non-ascii content must survive the pipe"
    assert b"<!--" not in proc.stdout
