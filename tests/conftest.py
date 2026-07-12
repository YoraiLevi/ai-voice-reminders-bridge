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
