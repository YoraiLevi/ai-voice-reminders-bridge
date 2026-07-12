"""Contract: the poll/drain/reply cycle + join/eject, against FakeTransport. Zero network."""

from __future__ import annotations

from voice_bridge import poller


def _inbox(t):
    return t.resolve_list("Vox-Message-Inbox")


def test_poll_inbox_bridges_new_and_dedupes(sample_config, fake_transport, fixed_clock):
    cfg = sample_config
    fake_transport.add_todo(_inbox(fake_transport), "buy milk", "2%")
    n = poller.poll_inbox(cfg, fake_transport, now=fixed_clock)
    assert n == 1
    text = cfg.peer_inbox.read_text(encoding="utf-8")
    assert text.strip() == "- [08:48] (vox) buy milk — 2%"
    # second poll: already seen → nothing new (idempotency)
    assert poller.poll_inbox(cfg, fake_transport, now=fixed_clock) == 0


def test_poll_inbox_dedupes_across_restart(sample_config, fake_transport, fixed_clock):
    cfg = sample_config
    fake_transport.add_todo(_inbox(fake_transport), "task")
    poller.poll_inbox(cfg, fake_transport, now=fixed_clock)
    # a "restart" reloads seen from disk — still deduped
    assert poller.poll_inbox(cfg, fake_transport, now=fixed_clock) == 0
    assert cfg.peer_inbox.read_text(encoding="utf-8").count("task") == 1


def test_send_reply_frames_and_writes(sample_config, fake_transport, fixed_clock):
    cfg = sample_config
    rid = poller.send_reply(
        cfg, fake_transport, "done, see https://x/y", notify=False, now=fixed_clock
    )
    out = fake_transport.resolve_list("Vox-Message-Outbox")
    items = fake_transport.read_incomplete(out)
    assert len(items) == 1 and items[0].id == rid
    # stamped [HH:MM][spoke], url front-loaded into the body
    assert items[0].notes == "[08:48][vox] https://x/y — done, see https://x/y"


def test_drain_replies_sends_and_dedupes(sample_config, fake_transport, fixed_clock):
    cfg = sample_config
    cfg.our_inbox.parent.mkdir(parents=True, exist_ok=True)
    cfg.our_inbox.write_text("first reply\nsecond reply\n", encoding="utf-8")
    n = poller.drain_replies(cfg, fake_transport, now=fixed_clock)
    assert n == 2
    out = fake_transport.resolve_list("Vox-Message-Outbox")
    assert len(fake_transport.read_incomplete(out)) == 2
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
    cfg.our_inbox.write_text("pending\n", encoding="utf-8")
    poller.announce_join(cfg, now=fixed_clock)
    assert "(vox) joined — async voice spoke" in cfg.peer_inbox.read_text(encoding="utf-8")
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
