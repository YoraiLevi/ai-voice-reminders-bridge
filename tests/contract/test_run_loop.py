"""The run loop's durability and stopping contracts (FMA-1/2/12/16, RUN-1/2/9).

These are the failures that cost a user a message or an evening, and every one of
them looked like success from inside the program:

* a mid-batch send failure re-sent every earlier reply, because the cursor
  advanced once per batch instead of once per line (FMA-1);
* a failed `complete()` was swallowed, so the reminder stayed visible on the
  phone, the user re-dictated it, and the agent received it twice (FMA-2);
* an expired session was retried for ever, because no retry can enter a 2FA code
  (RUN-1) — and a 503 that never cleared backed off silently for ever (FMA-12);
* power loss between the mailbox append and marking the item handled dropped the
  dictation entirely, while the phone showed it done (FMA-16).
"""

from __future__ import annotations

import os

import pytest

from voice_bridge import poller
from voice_bridge.mailbox import load_cursor
from voice_bridge.transport import FakeTransport
from voice_bridge.icloud import ICloudError


def _mailbox(cfg):
    cfg.mailbox_dir.mkdir(parents=True, exist_ok=True)
    cfg.peer_inbox.touch()
    cfg.our_inbox.touch()


def _replies(cfg, *lines):
    _mailbox(cfg)
    cfg.our_inbox.write_text("".join(f"{ln}\n" for ln in lines), encoding="utf-8")


# --------------------------------------------------------------------------- #
# FMA-1 — a failed send marks nothing delivered, and nothing is ever sent twice
#
# Originally phrased as "the cursor advances per line, not per batch", which was
# the fix for a loop of N separate sends. Since UX-2 a burst leaves as ONE send,
# so there is no partial state to protect; the property that survives — and the
# one that always mattered — is exactly-once delivery across a failure.
# --------------------------------------------------------------------------- #


def test_midbatch_failure_does_not_resend_earlier_replies(
    sample_config, fake_transport, monkeypatch
):
    """The failure that turns one hiccup into a wall of duplicates.

    Three replies, the send fails. The old code saved the cursor only after a loop
    of three separate sends, so the next cycle re-sent replies one and two — and
    every retry repeated them again.

    Since UX-2 a burst of 2+ leaves as ONE digest, which changes the shape of this
    guarantee without weakening it: there is no longer a *partial* send to protect
    against, because the three replies succeed or fail together. What must still
    hold — and is what this test now pins — is that a failed send marks NOTHING as
    delivered, and the recovery delivers each reply exactly once, never twice.
    """
    _replies(sample_config, "first", "second", "third")
    sent: list[list[str]] = []

    def flaky(cfg, t, texts, **kw):
        raise OSError("network died mid-batch")

    monkeypatch.setattr(poller, "send_digest", flaky)
    with pytest.raises(OSError):
        poller.drain_replies(sample_config, fake_transport)

    assert sent == [], "a failed send delivered nothing"
    assert load_cursor(sample_config.reply_cursor_file) == 0, "and so may mark nothing as consumed"

    # Recover: the send now succeeds. Each reply goes out exactly once.
    monkeypatch.setattr(poller, "send_digest", lambda cfg, t, texts, **kw: sent.append(texts))
    poller.drain_replies(sample_config, fake_transport)
    assert sent == [["first", "second", "third"]], "each reply exactly once"

    poller.drain_replies(sample_config, fake_transport)
    assert sent == [["first", "second", "third"]], "and never again on a later cycle"


def test_a_lone_reply_still_advances_its_own_cursor(sample_config, fake_transport, monkeypatch):
    """Below the digest threshold the original per-line path still runs, so the
    per-line cursor advance stays under test rather than becoming dead code."""
    _replies(sample_config, "only one")
    monkeypatch.setattr(poller, "send_reply", lambda cfg, t, text, **kw: None)

    assert poller.drain_replies(sample_config, fake_transport) == 1
    assert load_cursor(sample_config.reply_cursor_file) == (sample_config.our_inbox.stat().st_size)


def test_cursor_never_moves_backwards(sample_config, fake_transport):
    _replies(sample_config, "one", "two")
    poller.drain_replies(sample_config, fake_transport)
    first = load_cursor(sample_config.reply_cursor_file)
    poller.drain_replies(sample_config, fake_transport)
    assert load_cursor(sample_config.reply_cursor_file) >= first


# --------------------------------------------------------------------------- #
# FMA-2 — a failed complete() must not be swallowed
# --------------------------------------------------------------------------- #


def test_failed_complete_is_retried_not_swallowed(sample_config, fake_transport, monkeypatch):
    """Swallowing it left the reminder visible, so the user re-dictated it.

    The message had already reached the agent, so the "helpful" retry by the human
    produced a duplicate — the tool teaching the user to create the bug.
    """
    _mailbox(sample_config)
    inbox = fake_transport.resolve_list(sample_config.inbox_list, "")
    fake_transport.add_todo(inbox, "buy milk")

    def refuse(lst, item_id):
        raise OSError("backend refused the completion")

    monkeypatch.setattr(fake_transport, "complete", refuse)
    with pytest.raises(OSError):
        poller.poll_inbox(sample_config, fake_transport)

    # Delivered once, and still pending on the phone so the next cycle can retry.
    assert "buy milk" in sample_config.peer_inbox.read_text(encoding="utf-8")
    assert len(fake_transport.read_incomplete(inbox)) == 1


def test_completed_item_is_not_delivered_twice(sample_config, fake_transport):
    _mailbox(sample_config)
    inbox = fake_transport.resolve_list(sample_config.inbox_list, "")
    fake_transport.add_todo(inbox, "buy milk")

    assert poller.poll_inbox(sample_config, fake_transport) == 1
    assert poller.poll_inbox(sample_config, fake_transport) == 0
    assert sample_config.peer_inbox.read_text(encoding="utf-8").count("buy milk") == 1


# --------------------------------------------------------------------------- #
# FMA-16 — the append is flushed to disk before the item is marked handled
# --------------------------------------------------------------------------- #


def test_mailbox_append_is_fsynced_before_marking_seen(sample_config, fake_transport, monkeypatch):
    """Ruled fix: without this, power loss loses the dictation while the phone
    shows it done — the message is gone and nothing reports it.

    The remote completion is durable the instant the server accepts it; the local
    append is not durable until it is flushed. Ordering alone cannot close that,
    which is why the fsync is the fix rather than a reordering.
    """
    _mailbox(sample_config)
    inbox = fake_transport.resolve_list(sample_config.inbox_list, "")
    fake_transport.add_todo(inbox, "remember the milk")

    order: list[str] = []
    real_fsync = os.fsync
    monkeypatch.setattr(os, "fsync", lambda fd: order.append("fsync") or real_fsync(fd))
    real_mark = poller.mark_seen
    monkeypatch.setattr(
        poller, "mark_seen", lambda p, k: order.append("mark_seen") or real_mark(p, k)
    )

    poller.poll_inbox(sample_config, fake_transport)
    assert "fsync" in order, "the append must be flushed to disk"
    assert order.index("fsync") < order.index("mark_seen"), (
        "flushing after marking handled leaves the same window open"
    )


# --------------------------------------------------------------------------- #
# RUN-1/2 + FMA-12 — stop when retrying cannot help, and bound when it might
# --------------------------------------------------------------------------- #


class _Boom:
    """A transport whose connect() always raises the given error."""

    def __init__(self, exc):
        self.exc = exc
        self.attempts = 0
        self.invalidations = 0

    def connect(self):
        self.attempts += 1
        raise self.exc

    def invalidate_lists(self):
        # Part of the Transport contract since the call-count audit: the run loop
        # forgets remembered list ids before retrying, so a list deleted mid-run
        # resurfaces as a LookupError instead of being retried forever.
        self.invalidations += 1


def test_auth_failure_stops_immediately_with_guidance(wired_config, capsys):
    """No retry can type a 2FA code, so retrying is just a quieter way to fail."""
    _mailbox(wired_config)
    t = _Boom(ICloudError("session needs 2FA"))
    rc = poller.run(wired_config, t, interval=0)
    assert rc == 2
    assert t.attempts == 1, "an auth failure must not be retried at all"
    assert "icloud-login" in capsys.readouterr().out.lower()


def test_transient_failure_is_bounded_not_infinite(wired_config, capsys):
    """A 503 that never clears used to back off for ever — silent death dressed
    as patience. It now gives up and says why."""
    _mailbox(wired_config)
    t = _Boom(OSError("503 Service Unavailable"))
    rc = poller.run(wired_config, t, interval=0, max_attempts=3, backoff_base=0)
    assert rc == 2
    assert t.attempts == 3
    out = capsys.readouterr().out.lower()
    assert "giving up" in out or "gave up" in out


def test_unexpected_error_is_bounded_and_keeps_its_cause(wired_config, capsys):
    _mailbox(wired_config)
    t = _Boom(RuntimeError("a genuine bug"))
    rc = poller.run(wired_config, t, interval=0, max_attempts=2, backoff_base=0)
    assert rc == 2
    assert "a genuine bug" in capsys.readouterr().out


class _Stop(BaseException):
    """Breaks the loop without being caught as a failure (run catches Exception)."""


def test_transient_counter_resets_after_a_good_cycle(wired_config, fake_transport, monkeypatch):
    """Otherwise a long-lived poller accumulates unrelated blips until it quits.

    Two separate transient failures, each followed by a good cycle, with a budget
    of two. If the counter did not reset, the second failure would exhaust it and
    the loop would give up — on a system that is plainly working.
    """
    calls = {"n": 0}
    real_connect = fake_transport.connect

    def flaky():
        calls["n"] += 1
        if calls["n"] in (1, 3):
            raise OSError("503 Service Unavailable")
        if calls["n"] > 4:
            raise _Stop  # escape the loop; not an Exception, so not counted
        return real_connect()

    monkeypatch.setattr(fake_transport, "connect", flaky)
    with pytest.raises(_Stop):
        poller.run(wired_config, fake_transport, interval=0, max_attempts=2, backoff_base=0)
    assert calls["n"] > 4, "the loop must survive both blips rather than giving up"


# --------------------------------------------------------------------------- #
# RUN-9 — one poller per mailbox
# --------------------------------------------------------------------------- #


def test_second_poller_is_refused(sample_config, fake_transport, monkeypatch):
    """Two pollers on one account is how the throttle storms start."""
    _mailbox(sample_config)
    pidfile = sample_config.state_dir / "poller.pid"
    pidfile.parent.mkdir(parents=True, exist_ok=True)
    pidfile.write_text(str(os.getpid()), encoding="utf-8")  # ourselves: definitely alive

    rc = poller.run(sample_config, fake_transport, once=True, interval=0)
    assert rc == 2


def test_force_overrides_the_guard(wired_config, fake_transport):
    _mailbox(wired_config)
    pidfile = wired_config.state_dir / "poller.pid"
    pidfile.parent.mkdir(parents=True, exist_ok=True)
    pidfile.write_text(str(os.getpid()), encoding="utf-8")

    rc = poller.run(wired_config, fake_transport, once=True, interval=0, force=True)
    assert rc in (0, 1)


def test_stale_pidfile_does_not_block(wired_config, fake_transport):
    """A crashed poller leaves its pidfile behind; that must not lock the user out."""
    _mailbox(wired_config)
    pidfile = wired_config.state_dir / "poller.pid"
    pidfile.parent.mkdir(parents=True, exist_ok=True)
    pidfile.write_text("0", encoding="utf-8")  # never a live pid

    rc = poller.run(wired_config, fake_transport, once=True, interval=0)
    assert rc in (0, 1)


# --------------------------------------------------------------------------- #
# RUN-4 — a dry run writes nothing
# --------------------------------------------------------------------------- #


def test_dry_run_creates_no_config(tmp_path, tmp_mailbox, capsys):
    """Asking "what would this do?" used to answer by doing part of it.

    The dry run went through the setup flow first, so on a fresh machine it wrote
    a config file — the single thing a dry run promises not to do.
    """
    from voice_bridge.runner import run_command

    target = tmp_path / "absent" / "voice-bridge.json"
    rc = run_command(
        dry_run=True,
        config_path=str(target),
        overrides={"mailbox_dir": str(tmp_mailbox), "state_dir": str(tmp_path / "s")},
    )
    assert rc == 0
    assert not target.exists(), "a dry run must not create the config it was asked about"
    assert not (tmp_path / "s").exists(), "nor any state directory"
    assert "would append" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# LIVE-6 — Ctrl-C is how people stop a foreground daemon
# --------------------------------------------------------------------------- #


def test_ctrl_c_stops_cleanly_and_still_ejects(wired_config, fake_transport, monkeypatch, capsys):
    """Stopping the bridge is a normal action, not an error.

    `run` caught Exception, and KeyboardInterrupt is a BaseException, so Ctrl-C
    escaped as a raw traceback — while the flows corpus already promised a clean
    stop. The eject was never at risk (a `finally` runs during unwinding), but a
    traceback tells the user something broke when nothing did.
    """
    _mailbox(wired_config)

    def interrupt(_seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr(poller.time, "sleep", interrupt)

    rc = poller.run(wired_config, fake_transport, interval=1)

    assert rc == 0, "a deliberate stop is success, not failure"
    assert "stopped" in capsys.readouterr().out.lower()
    assert "stopping" in wired_config.peer_inbox.read_text(encoding="utf-8"), (
        "the peer must still see us leave"
    )
    assert not (wired_config.state_dir / "poller.pid").exists(), "pidfile must be cleaned up"


# --------------------------------------------------------------------------- #
# UX-4 — stamp once
# --------------------------------------------------------------------------- #


def test_a_drained_line_is_stamped_once_not_twice(sample_config, fake_transport):
    """The banner read "[23:05][vox] - [23:06] (manager) buy milk".

    A mailbox line already carries its own `- [HH:MM] (who)` stamp, and the drain
    passed the whole line to send_reply, which stamped it again. Two clocks and
    two speakers in one notification, on the small screen where brevity matters
    most.
    """
    from voice_bridge.mailbox import format_mailbox_line

    _replies(sample_config, format_mailbox_line("buy milk", from_name="manager"))
    poller.drain_replies(sample_config, fake_transport)

    outbox = fake_transport.resolve_list(sample_config.output_list, "")
    item = fake_transport.read_incomplete(outbox)[0]

    # The stamp is `[HH:MM][vox]`, so brackets are not the measure — TIMESTAMPS are.
    import re

    times = re.compile(r"\[\d{2}:\d{2}\]")
    assert len(times.findall(item.title)) == 1, f"one timestamp only, got {item.title!r}"
    assert "(manager)" not in item.title, "the mailbox framing is not for the phone"
    assert "buy milk" in item.title
    assert len(times.findall(item.notes)) == 1, f"notes single-stamped too, got {item.notes!r}"


def test_a_line_without_a_mailbox_stamp_is_untouched(sample_config, fake_transport):
    """Only a LEADING mailbox stamp is removed — arbitrary text keeps its brackets."""
    _replies(sample_config, "look at [this] bracketed thing")
    poller.drain_replies(sample_config, fake_transport)

    outbox = fake_transport.resolve_list(sample_config.output_list, "")
    item = fake_transport.read_incomplete(outbox)[0]
    assert "[this] bracketed thing" in item.title


# --------------------------------------------------------------------------- #
# The two edge cases: a list that is not selected, and one that stopped existing
# --------------------------------------------------------------------------- #


def test_run_refuses_to_start_with_nothing_selected(unselected_config, fake_transport, capsys):
    """No selection is a hard stop, not a fallback.

    An unselected role used to resolve by matching a title at runtime — the exact
    silent guess this feature removed. It now fails BEFORE the mailbox is claimed,
    because starting, announcing a join and then dying every cycle is worse than
    never starting: the peer sees a spoke that is present and silent.
    """
    rc = poller.run(unselected_config, fake_transport, once=True)
    out = capsys.readouterr().out

    assert rc == 2
    assert "no list is selected" in out
    assert "voice-bridge lists --select" in out, "it must name the way out"
    assert "voice-bridge setup" in out
    assert (
        not unselected_config.peer_inbox.exists()
        or "joined" not in unselected_config.peer_inbox.read_text(encoding="utf-8")
    ), "it must not announce a join it cannot honour"


def test_a_deleted_list_stops_immediately_instead_of_retrying(wired_config, capsys):
    """Deleted mid-run — retrying cannot bring it back.

    The generic give-up message blames connectivity, which would send the user to
    inspect a network that is working fine, looking for a list they deleted.
    """
    t = FakeTransport()  # the selected ids exist in the config but not here

    rc = poller.run(wired_config, t, once=False, max_attempts=5, backoff_base=0)
    out = capsys.readouterr().out

    assert rc == 2
    assert "a selected list is gone" in out
    assert "voice-bridge lists --select" in out
    assert "connectivity" not in out, "a deleted list is not a network problem"


def test_a_non_transient_failure_does_not_blame_connectivity(wired_config, capsys):
    """An unwritable list raises a permissions error, not a network one. The advice
    has to match the fault, or it sends the user to the wrong place."""

    class ReadOnly(FakeTransport):
        def add_todo(self, lst, summary, notes="", *, needs_input=False):
            raise PermissionError("403 Forbidden: list is read-only")

    t = ReadOnly()
    t.add_list("Vox-Message-Outbox", "list-vox-message-outbox")
    t.add_list("Vox-Message-Inbox", "list-vox-message-inbox")
    wired_config.our_inbox.parent.mkdir(parents=True, exist_ok=True)
    wired_config.our_inbox.write_text("- [10:00] (manager) hi\n", encoding="utf-8")

    rc = poller.run(wired_config, t, once=False, max_attempts=2, backoff_base=0)
    out = capsys.readouterr().out

    assert rc == 2
    assert "403 Forbidden" in out, "the real cause must survive to the surface"
    assert "check connectivity" not in out, "it must not send them to inspect the network"
    assert "not a connectivity problem" in out
