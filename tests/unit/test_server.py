"""server: init writes config + bcrypt user + client creds; url + down-status. No network."""

from __future__ import annotations

import json

from voice_bridge import server
from voice_bridge.config import load_config


def _cfg(tmp_path, tmp_mailbox, port=5299):
    p = tmp_path / "voice-bridge.json"
    p.write_text(
        json.dumps(
            {
                "transport": "radicale",
                "mailbox_dir": str(tmp_mailbox),
                "state_dir": str(tmp_path / "state"),
                "radicale_port": port,
            }
        ),
        encoding="utf-8",
    )
    return load_config(p)


def test_init_writes_config_user_and_creds(tmp_path, tmp_mailbox):
    cfg = _cfg(tmp_path, tmp_mailbox, port=5299)
    server.init(cfg, user="tester", password="secret")
    p = server.paths(cfg)
    assert "hosts = 0.0.0.0:5299" in p.config.read_text(encoding="utf-8")
    assert p.users.read_text(encoding="utf-8").startswith("tester:$2b$")  # bcrypt, no plaintext
    creds = cfg.creds_env.read_text(encoding="utf-8")
    assert "ICLOUD_CALDAV_URL=http://127.0.0.1:5299" in creds
    assert "ICLOUD_APPLE_ID=tester" in creds
    assert "ICLOUD_APP_PASSWORD=secret" in creds


def test_client_url_from_port(tmp_path, tmp_mailbox):
    assert server.client_url(_cfg(tmp_path, tmp_mailbox, port=5300)) == "http://127.0.0.1:5300"


def test_status_when_down(tmp_path, tmp_mailbox):
    s = server.status(_cfg(tmp_path, tmp_mailbox, port=5399))
    assert s["reachable"] is False and s["pid"] is None and s["initialised"] is False


def test_stop_when_not_running(tmp_path, tmp_mailbox, capsys):
    assert server.stop(_cfg(tmp_path, tmp_mailbox, port=5399)) == 0
    assert "not running" in capsys.readouterr().out
