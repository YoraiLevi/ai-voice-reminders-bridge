"""Contract: the poll/drain/reply cycle + join/eject, against FakeTransport. Zero network."""

from __future__ import annotations

import pytest

from voice_bridge import poller
from voice_bridge.mailbox import save_cursor
from voice_bridge.setup import SUGGESTED_NAMES
from voice_bridge.transport import FakeTransport


class _Stop(BaseException):
    """Breaks the resilient loop in tests (BaseException → not caught by except Exception)."""


def _inbox(t):
    # The list the bridge READS. Named via the default rather than a literal, so
    # it follows the config instead of pinning one naming decision.
    return t.resolve_list("", SUGGESTED_INBOX_ID)


#: The ids the fake derives for the two lists `sample_config` selects.
SUGGESTED_INBOX_ID = "list-vox-message-outbox"
SUGGESTED_OUTBOX_ID = "list-vox-message-inbox"


def _seeded(cls=FakeTransport):
    t = cls()
    t.add_list(SUGGESTED_NAMES["inbox"])
    t.add_list(SUGGESTED_NAMES["outbox"])
    return t


def test_poll_inbox_bridges_new_and_dedupes(sample_config, fake_transport, fixed_clock):
    cfg = sample_config
    fake_transport.add_todo(_inbox(fake_transport), "buy milk", "2%")
    n = poller.poll_inbox(cfg, fake_transport, now=fixed_clock)
    assert n == 1
    text = cfg.peer_inbox.read_text(encoding="utf-8")
    assert text.strip() == "- [08:48] (vox) buy milk - 2%"
    # second poll: already seen → nothing new (idempotency)
    assert poller.poll_inbox(cfg, fake_transport, now=fixed_clock) == 0


def test_poll_inbox_dedupes_across_restart(sample_config, fake_transport, fixed_clock):
    cfg = sample_config
    fake_transport.add_todo(_inbox(fake_transport), "task")
    poller.poll_inbox(cfg, fake_transport, now=fixed_clock)
    # a "restart" reloads seen from disk - still deduped
    assert poller.poll_inbox(cfg, fake_transport, now=fixed_clock) == 0
    assert cfg.peer_inbox.read_text(encoding="utf-8").count("task") == 1


def test_send_reply_frames_and_writes(sample_config, fake_transport, fixed_clock):
    cfg = sample_config
    rid = poller.send_reply(
        cfg, fake_transport, "done, see https://x/y", notify=False, now=fixed_clock
    )
    out = fake_transport.resolve_list("", SUGGESTED_OUTBOX_ID)  # where replies go
    items = fake_transport.read_incomplete(out)
    assert len(items) == 1 and items[0].id == rid
    # stamped [HH:MM][spoke], url front-loaded into the body
    assert items[0].notes == "[08:48][vox] https://x/y - done, see https://x/y"


def test_drain_replies_sends_and_dedupes(sample_config, fake_transport, fixed_clock):
    cfg = sample_config
    cfg.our_inbox.parent.mkdir(parents=True, exist_ok=True)
    cfg.our_inbox.write_text("first reply\nsecond reply\n", encoding="utf-8")
    n = poller.drain_replies(cfg, fake_transport, now=fixed_clock)
    assert n == 2, "both replies were sent"
    out = fake_transport.resolve_list("", SUGGESTED_OUTBOX_ID)  # where replies go
    # Two pending in ONE cycle is a burst, so they arrive as one digest (UX-2).
    # The count returned is still 2 - it reports replies delivered, not reminders.
    assert len(fake_transport.read_incomplete(out)) == 1
    # re-drain the same file → nothing new
    assert poller.drain_replies(cfg, fake_transport, now=fixed_clock) == 0


def test_send_reply_notify_false_skips_banner(sample_config, fake_transport, monkeypatch):
    import voice_bridge.ntfy as ntfy

    called = False

    def spy(*a, **k):
        nonlocal called
        called = True
        return True

    monkeypatch.setattr(ntfy, "push", spy)
    poller.send_reply(sample_config, fake_transport, "hi", notify=False)
    assert called is False


def test_join_and_eject_lifecycle(sample_config, fake_transport, fixed_clock):
    cfg = sample_config
    cfg.our_inbox.parent.mkdir(parents=True, exist_ok=True)
    cfg.our_inbox.write_text("drained\n", encoding="utf-8")
    # STATE THE PREMISE. This test asserts the clean-exit DELETE, which since batch 17
    # only applies to a fully drained inbox - so the drain has to be part of the setup
    # rather than inherited from the old unconditional behaviour. The file used to say
    # "pending", which is precisely the state the delete now refuses.
    save_cursor(cfg.reply_cursor_file, cfg.our_inbox.stat().st_size, path=cfg.our_inbox)
    poller.announce_join(cfg, now=fixed_clock)
    assert "(vox) joined - async voice spoke" in cfg.peer_inbox.read_text(encoding="utf-8")
    poller.announce_eject(cfg, now=fixed_clock)
    assert "(vox) stopping" in cfg.peer_inbox.read_text(encoding="utf-8")
    assert not cfg.our_inbox.exists()  # protocol: delete own inbox on clean exit


def test_run_once_returns_counts(sample_config, fake_transport, fixed_clock):
    cfg = sample_config
    fake_transport.add_todo(_inbox(fake_transport), "hello")
    polled, drained = poller.run_once(cfg, fake_transport, now=fixed_clock)
    assert polled == 1 and drained == 0


def test_dry_run_touches_no_transport(sample_config, capsys):
    rc = poller.dry_run(sample_config)
    out = capsys.readouterr().out
    assert rc == 0
    assert "spoke_name" in out and "example request" in out


def test_run_once_error_returns_2_and_ejects(wired_config):
    class Boom(FakeTransport):
        def read_incomplete(self, lst):
            raise RuntimeError("boom")

    t = _seeded(Boom)
    assert poller.run(wired_config, t, once=True) == 2
    assert "(vox) stopping" in wired_config.peer_inbox.read_text(encoding="utf-8")  # eject ran


def test_run_loop_retries_then_recovers(wired_config, monkeypatch):
    calls = {"n": 0}

    class Flaky(FakeTransport):
        def read_incomplete(self, lst):
            calls["n"] += 1
            if calls["n"] <= 2:
                raise RuntimeError("net blip")
            return []

    def fake_sleep(_s):
        if calls["n"] >= 3:  # after recovery, the period-sleep → break out
            raise _Stop
        # else: a backoff sleep during the failures - just return

    monkeypatch.setattr(poller.time, "sleep", fake_sleep)
    with pytest.raises(_Stop):
        poller.run(wired_config, _seeded(Flaky), once=False, interval=1)
    assert calls["n"] >= 3  # retried past both failures and recovered
