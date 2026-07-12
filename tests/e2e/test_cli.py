"""e2e: drive cli.main(argv) and assert dispatch + exit codes. In-process (no
subprocess) for speed/reliability; the console script is smoke-tested separately."""

from __future__ import annotations

import json

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
