"""Shared fixtures. The FakeTransport + a fixed clock are the highest-leverage ones:
they let the whole spoke be tested with no network and deterministic timestamps.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from voice_bridge.config import load_config
from voice_bridge.transport import FakeTransport


@pytest.fixture(autouse=True)
def _clean_icloud_env(monkeypatch):
    """Keep host ICLOUD_* env vars from leaking into file-based creds resolution."""
    for k in (
        "ICLOUD_APPLE_ID",
        "ICLOUD_USERNAME",
        "ICLOUD_APP_PASSWORD",
        "ICLOUD_PASSWORD",
        "ICLOUD_CALDAV_URL",
    ):
        monkeypatch.delenv(k, raising=False)


@pytest.fixture
def fixed_clock() -> datetime:
    """A frozen wall-clock for deterministic [HH:MM] stamps."""
    return datetime(2026, 7, 12, 8, 48, 0)


@pytest.fixture
def tmp_mailbox(tmp_path: Path) -> Path:
    """An empty mailbox dir (the shared bus)."""
    d = tmp_path / "agent-mail"
    d.mkdir()
    return d


@pytest.fixture
def sample_config(tmp_path: Path, tmp_mailbox: Path):
    """A resolved Config whose mailbox + state dirs live under tmp (no touching $HOME)."""
    cfg_file = tmp_path / "voice-bridge.json"
    cfg_file.write_text(
        json.dumps(
            {
                "mailbox_dir": str(tmp_mailbox),
                "state_dir": str(tmp_path / "state"),
            }
        ),
        encoding="utf-8",
    )
    return load_config(cfg_file)


@pytest.fixture
def fake_transport() -> FakeTransport:
    """In-memory backend pre-seeded with the two default lists."""
    t = FakeTransport()
    t.add_list("Vox-Message-Inbox")
    t.add_list("Vox-Message-Outbox")
    return t


@pytest.fixture
def radicale_server(tmp_path):
    """An embedded Radicale CalDAV server on a random localhost port — the real oracle
    for the CalDAV transport. htpasswd auth (test:test), tmp filesystem storage."""
    import socket
    import threading
    from wsgiref.simple_server import WSGIRequestHandler, make_server

    radicale = pytest.importorskip("radicale")
    from radicale import config as rconfig

    storage = tmp_path / "collections"
    storage.mkdir()
    users = tmp_path / "users"
    users.write_text("test:test\n", encoding="utf-8")
    cfg = rconfig.load(())
    cfg.update(
        {
            "auth": {
                "type": "htpasswd",
                "htpasswd_filename": str(users),
                "htpasswd_encryption": "plain",
            },
            "storage": {"filesystem_folder": str(storage)},
            "rights": {"type": "authenticated"},
        },
        "test",
        privileged=True,
    )
    app = radicale.Application(cfg)

    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()

    class _Quiet(WSGIRequestHandler):
        def log_message(self, *a):  # noqa: D401 - silence access log
            pass

    httpd = make_server("127.0.0.1", port, app, handler_class=_Quiet)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield {"url": f"http://127.0.0.1:{port}", "username": "test", "password": "test"}
    finally:
        httpd.shutdown()
        thread.join(timeout=5)


@pytest.fixture
def radicale_config(radicale_server, tmp_path, tmp_mailbox):
    """A resolved Config wired to the embedded Radicale server (transport=radicale)."""
    import json

    state = tmp_path / "state"
    state.mkdir(parents=True, exist_ok=True)
    creds = state / "radicale.env"
    creds.write_text(
        f"ICLOUD_APPLE_ID={radicale_server['username']}\n"
        f"ICLOUD_APP_PASSWORD={radicale_server['password']}\n"
        f"ICLOUD_CALDAV_URL={radicale_server['url']}\n",
        encoding="utf-8",
    )
    cfg_file = tmp_path / "voice-bridge.json"
    cfg_file.write_text(
        json.dumps(
            {
                "transport": "radicale",
                "mailbox_dir": str(tmp_mailbox),
                "state_dir": str(state),
                "creds_env": str(creds),
            }
        ),
        encoding="utf-8",
    )
    from voice_bridge.config import load_config

    return load_config(cfg_file)
