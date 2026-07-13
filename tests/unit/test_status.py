"""status.gather: file/pidfile/reachability glance (no transport auth)."""

from __future__ import annotations

from voice_bridge import status


def test_gather_down_system(sample_config):
    d = status.gather(sample_config)
    assert d["transport"] == "icloud"
    assert d["poller_running"] is False
    assert d["last_inbound"] is None and d["last_outbound"] is None
    assert d["server_reachable"] is None  # not radicale
    assert d["inbox_seen"] == 0


def test_gather_reports_last_inbound(sample_config):
    sample_config.peer_inbox.parent.mkdir(parents=True, exist_ok=True)
    sample_config.peer_inbox.write_text("- [08:48] (vox) hi\n", encoding="utf-8")
    assert status.gather(sample_config)["last_inbound"] is not None
