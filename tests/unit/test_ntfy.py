"""ntfy.push: correct request shape, and best-effort no-op / never-raise behavior."""

from __future__ import annotations

import json
import urllib.request

from voice_bridge import ntfy
from voice_bridge.config import load_config


def _cfg_with_topic(tmp_path, tmp_mailbox, topic="my-topic"):
    state = tmp_path / "state"
    state.mkdir(parents=True, exist_ok=True)
    (state / "ntfy-topic.txt").write_text(topic, encoding="utf-8")
    cfg_file = tmp_path / "voice-bridge.json"
    cfg_file.write_text(
        json.dumps({"mailbox_dir": str(tmp_mailbox), "state_dir": str(state)}), encoding="utf-8"
    )
    return load_config(cfg_file)


def test_push_builds_request(tmp_path, tmp_mailbox, monkeypatch):
    cfg = _cfg_with_topic(tmp_path, tmp_mailbox)
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["headers"] = {k.lower(): v for k, v in req.header_items()}
        captured["body"] = req.data.decode("utf-8")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    sent = ntfy.push(cfg, "build done", click="https://x/y")
    assert sent is True
    assert captured["url"] == "https://ntfy.sh/my-topic"
    assert captured["headers"]["title"] == "Vox"  # ntfy_title default, {name} substituted
    assert captured["headers"]["tags"] == "robot"
    assert captured["headers"]["priority"] == "high"
    assert captured["headers"]["click"] == "https://x/y"
    assert captured["body"] == "build done"


def test_push_no_topic_is_noop(sample_config, monkeypatch):
    # sample_config's state_dir has no ntfy-topic.txt
    called = False

    def boom(*a, **k):
        nonlocal called
        called = True

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    assert ntfy.push(sample_config, "hi") is False
    assert called is False


def test_push_swallows_errors(tmp_path, tmp_mailbox, monkeypatch):
    cfg = _cfg_with_topic(tmp_path, tmp_mailbox)

    def raiser(*a, **k):
        raise OSError("network down")

    monkeypatch.setattr(urllib.request, "urlopen", raiser)
    # must never raise — a failed banner cannot break the reply
    assert ntfy.push(cfg, "hi") is False


def test_push_clips_body(tmp_path, tmp_mailbox, monkeypatch):
    cfg = load_config(
        _write_cfg(tmp_path, tmp_mailbox, {"ntfy_body_limit": 20}),
    )
    (cfg.ntfy_topic_file.parent).mkdir(parents=True, exist_ok=True)
    cfg.ntfy_topic_file.write_text("t", encoding="utf-8")
    seen = {}
    monkeypatch.setattr(
        urllib.request, "urlopen", lambda req, timeout=None: seen.update(b=req.data.decode())
    )
    ntfy.push(cfg, "word " * 20)
    assert len(seen["b"]) <= 20


def _write_cfg(tmp_path, tmp_mailbox, extra):
    p = tmp_path / "voice-bridge.json"
    p.write_text(
        json.dumps(
            {"mailbox_dir": str(tmp_mailbox), "state_dir": str(tmp_path / "state"), **extra}
        ),
        encoding="utf-8",
    )
    return p
