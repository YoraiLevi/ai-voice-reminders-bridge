"""Integration: voice-bridge launches its OWN Radicale server, and the CalDAV
transport talks to it. The whole loop is self-hosting."""

from __future__ import annotations

import json
import socket
import time

import pytest

from voice_bridge import server
from voice_bridge.caldav import CalDAVTransport
from voice_bridge.config import load_config


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _cfg(tmp_path, tmp_mailbox, port):
    p = tmp_path / "voice-bridge.json"
    p.write_text(
        json.dumps(
            {
                "transport": "radicale",
                "mailbox_dir": str(tmp_mailbox),
                "state_dir": str(tmp_path / "state"),
                "radicale_host": "127.0.0.1",
                "radicale_port": port,
            }
        ),
        encoding="utf-8",
    )
    return load_config(p)


def test_init_start_caldav_stop(tmp_path, tmp_mailbox):
    pytest.importorskip("radicale")
    cfg = _cfg(tmp_path, tmp_mailbox, _free_port())
    server.init(cfg, user="tester", password="pw")
    try:
        assert server.start(cfg, background=True) == 0
        assert server.is_reachable(server.client_url(cfg))

        t = CalDAVTransport(cfg, native_alarm=False)
        t.connect()
        ref = t.create_list("Vox-Message-Inbox")
        assert ref.name == "Vox-Message-Inbox"
        uid = t.add_todo(ref, "hi", "there")
        assert "hi" in {i.title for i in t.read_incomplete(ref)}
        t.complete(ref, uid)
        assert "hi" not in {i.title for i in t.read_incomplete(ref)}
    finally:
        server.stop(cfg)
    for _ in range(20):
        if not server.is_reachable(server.client_url(cfg)):
            break
        time.sleep(0.2)
    assert not server.is_reachable(server.client_url(cfg))
