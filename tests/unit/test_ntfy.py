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
    assert sent.status == "sent" and bool(sent) is True
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
    assert ntfy.push(sample_config, "hi").status == "no_topic"
    assert called is False


def test_push_swallows_errors(tmp_path, tmp_mailbox, monkeypatch):
    cfg = _cfg_with_topic(tmp_path, tmp_mailbox)

    def raiser(*a, **k):
        raise OSError("network down")

    monkeypatch.setattr(urllib.request, "urlopen", raiser)
    # must never raise — a failed banner cannot break the reply
    assert ntfy.push(cfg, "hi").status == "failed"


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


# --------------------------------------------------------------------------- #
# NTFY-1 — three states, because "no topic" is not "send failed"
# --------------------------------------------------------------------------- #


def test_push_returns_sent_on_success(tmp_path, tmp_mailbox, monkeypatch):
    cfg = _cfg_with_topic(tmp_path, tmp_mailbox)
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: None)
    result = ntfy.push(cfg, "hello")
    assert result.status == "sent"
    assert bool(result) is True


def test_push_distinguishes_no_topic_from_failure(tmp_path, tmp_mailbox, monkeypatch):
    """The whole point: these two were both `False` and reported identically.

    A network outage was announced to the user as "no ntfy topic", sending them
    to configure something that was already configured.
    """
    cfg_no_topic = _cfg_with_topic(tmp_path, tmp_mailbox)
    cfg_no_topic.ntfy_topic_file.unlink()
    missing = ntfy.push(cfg_no_topic, "hello")

    cfg_ok = _cfg_with_topic(tmp_path / "b", tmp_mailbox)

    def boom(*a, **k):
        raise OSError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    failed = ntfy.push(cfg_ok, "hello")

    assert missing.status == "no_topic"
    assert failed.status == "failed"
    assert missing.status != failed.status, "the two causes must be distinguishable"
    assert not missing and not failed


def test_failure_detail_names_the_cause(tmp_path, tmp_mailbox, monkeypatch):
    """Without the reason the user cannot tell DNS from a bad topic from a 500."""
    cfg = _cfg_with_topic(tmp_path, tmp_mailbox)
    monkeypatch.setattr(
        urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(OSError("dns dead"))
    )
    result = ntfy.push(cfg, "hello")
    assert "dns dead" in result.detail


def test_no_topic_detail_names_the_file(tmp_path, tmp_mailbox):
    cfg = _cfg_with_topic(tmp_path, tmp_mailbox)
    cfg.ntfy_topic_file.unlink()
    result = ntfy.push(cfg, "hello")
    assert str(cfg.ntfy_topic_file) in result.detail


def test_blank_topic_file_is_no_topic_not_failure(tmp_path, tmp_mailbox):
    cfg = _cfg_with_topic(tmp_path, tmp_mailbox, topic="   \n")
    assert ntfy.push(cfg, "hello").status == "no_topic"


def test_push_still_never_raises(tmp_path, tmp_mailbox, monkeypatch):
    """Best-effort is preserved: a banner failure must not break the reply."""
    cfg = _cfg_with_topic(tmp_path, tmp_mailbox)
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("anything at all")),
    )
    assert ntfy.push(cfg, "hello").status == "failed"


def test_truthiness_keeps_existing_callers_working(tmp_path, tmp_mailbox, monkeypatch):
    """`poller` and `deliver` treat the result as a bool; that must keep holding."""
    cfg = _cfg_with_topic(tmp_path, tmp_mailbox)
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: None)
    assert ntfy.push(cfg, "x")
    cfg.ntfy_topic_file.unlink()
    assert not ntfy.push(cfg, "x")


def test_notify_exit_codes_separate_the_two_causes(tmp_path, tmp_mailbox, monkeypatch, capsys):
    """0 sent · 2 no topic (you must act) · 1 failed (transient, retry)."""
    from voice_bridge.cli import main

    cfg = _cfg_with_topic(tmp_path, tmp_mailbox)
    cfg_arg = str(tmp_path / "voice-bridge.json")

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: None)
    assert main(["--config", cfg_arg, "notify", "hi"]) == 0

    monkeypatch.setattr(
        urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(OSError("offline"))
    )
    assert main(["--config", cfg_arg, "notify", "hi"]) == 1
    assert "offline" in capsys.readouterr().out.lower()

    cfg.ntfy_topic_file.unlink()
    assert main(["--config", cfg_arg, "notify", "hi"]) == 2
