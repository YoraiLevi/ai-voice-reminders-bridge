"""Reply digests — bundle a burst, never a lone reply (UX-2, option C at N=2).

Three replies written in one cycle used to arrive as three reminders and three
banners, which is three interruptions for one thought. Bundling fixes the noise,
but it is **presentation on the way out and nothing more**: the moment bundling
becomes a durability batch, FMA-1 returns — the bug where a failure part-way
through re-sent everything that had already gone, on that cycle and every retry
after it.

So these tests hold two lines at once: the burst must arrive as one reminder and
one banner, AND the cursor must still advance per line.
"""

from __future__ import annotations

import dataclasses

from voice_bridge import poller
from voice_bridge.mailbox import load_cursor


def _cfg(sample_config, **kw):
    return dataclasses.replace(sample_config, **kw)


def _write(cfg, *lines: str) -> None:
    cfg.our_inbox.parent.mkdir(parents=True, exist_ok=True)
    with cfg.our_inbox.open("a", encoding="utf-8") as fh:
        for line in lines:
            fh.write(line + "\n")


def _outbox(cfg, t):
    return t.read_incomplete(t.resolve_list(cfg.output_list, cfg.output_list_id))


class _Banners:
    """Counts pushes, because 'one banner' is half the point of the feature.

    Banners are DELAYED now (notify_delay, default 3s) so the buzz lands after the
    reminder it announces. `.settled` flushes anything still waiting, so these
    tests exercise the real scheduling path rather than side-stepping it.
    """

    def __init__(self, monkeypatch):
        self.sent: list[str] = []
        monkeypatch.setattr(
            poller.ntfy, "push", lambda cfg, text, click=None: self.sent.append(text)
        )

    @property
    def settled(self) -> list[str]:
        poller.ntfy.flush()
        return self.sent


# --------------------------------------------------------------------------- #
# a lone reply is untouched
# --------------------------------------------------------------------------- #


def test_a_single_reply_is_not_digested(sample_config, fake_transport, monkeypatch):
    """N=2 means a lone reply keeps its exact previous shape — no regression in
    the common case, and no reminder titled '1 replies'."""
    banners = _Banners(monkeypatch)
    _write(sample_config, "- [10:00] (manager) the build is green")

    assert poller.drain_replies(sample_config, fake_transport) == 1

    items = _outbox(sample_config, fake_transport)
    assert len(items) == 1
    assert "the build is green" in items[0].title
    assert "replies" not in items[0].title
    assert len(banners.settled) == 1


# --------------------------------------------------------------------------- #
# a burst becomes one reminder and one banner
# --------------------------------------------------------------------------- #


def test_a_burst_becomes_one_reminder(sample_config, fake_transport, monkeypatch):
    banners = _Banners(monkeypatch)
    _write(
        sample_config,
        "- [10:00] (manager) first",
        "- [10:00] (manager) second",
        "- [10:00] (manager) third",
    )

    assert poller.drain_replies(sample_config, fake_transport) == 3, "it still reports 3 sent"

    items = _outbox(sample_config, fake_transport)
    assert len(items) == 1, "three replies, ONE reminder"
    assert len(banners.settled) == 1, "three replies, ONE banner"


def test_the_digest_title_counts_and_the_notes_carry_every_line(
    sample_config, fake_transport, monkeypatch
):
    """The title is a summary, so the content must live in the notes — a digest
    that summarises away the replies has thrown the message out."""
    _Banners(monkeypatch)
    _write(
        sample_config,
        "- [10:00] (manager) alpha",
        "- [10:00] (manager) beta",
    )
    poller.drain_replies(sample_config, fake_transport)

    item = _outbox(sample_config, fake_transport)[0]
    assert item.title.startswith("2 replies")
    for word in ("alpha", "beta"):
        assert word in item.notes


def test_every_digested_line_keeps_its_own_stamp(sample_config, fake_transport, monkeypatch):
    """Each line is still a separate message with its own time, and the mailbox's
    own `- [HH:MM] (who)` framing is still stripped (UX-4) — bundling must not
    reintroduce the doubled timestamp."""
    _Banners(monkeypatch)
    _write(
        sample_config,
        "- [09:00] (manager) alpha",
        "- [09:00] (manager) beta",
    )
    poller.drain_replies(sample_config, fake_transport)

    notes = _outbox(sample_config, fake_transport)[0].notes
    assert notes.count("[vox]") == 2, "each line carries the spoke stamp"
    assert "(manager)" not in notes, "the mailbox framing must still be stripped"


# --------------------------------------------------------------------------- #
# the durability property bundling must not break
# --------------------------------------------------------------------------- #


def test_the_cursor_still_advances_past_every_line(sample_config, fake_transport, monkeypatch):
    """The whole file must be consumed, so a second pass sends nothing again."""
    _Banners(monkeypatch)
    _write(
        sample_config,
        "- [10:00] (manager) first",
        "- [10:00] (manager) second",
    )
    poller.drain_replies(sample_config, fake_transport)

    assert load_cursor(sample_config.reply_cursor_file) == sample_config.our_inbox.stat().st_size
    assert poller.drain_replies(sample_config, fake_transport) == 0, "nothing re-sent"
    assert len(_outbox(sample_config, fake_transport)) == 1


def test_a_failed_digest_leaves_the_cursor_alone(sample_config, fake_transport, monkeypatch):
    """If the send raises, nothing was delivered — so nothing may be marked sent.

    Advancing first would silently drop the whole burst: the replies are gone from
    the queue and never reached the phone.
    """
    _Banners(monkeypatch)
    _write(
        sample_config,
        "- [10:00] (manager) first",
        "- [10:00] (manager) second",
    )

    def _boom(*_a, **_kw):
        raise RuntimeError("backend down")

    monkeypatch.setattr(fake_transport, "add_todo", _boom)

    try:
        poller.drain_replies(sample_config, fake_transport)
    except RuntimeError:
        pass

    assert load_cursor(sample_config.reply_cursor_file) == 0, (
        "nothing was delivered, so nothing is consumed"
    )


def test_comments_and_blank_lines_are_consumed_but_never_sent(
    sample_config, fake_transport, monkeypatch
):
    """They must still advance the cursor, or they are re-read every cycle for ever."""
    _Banners(monkeypatch)
    _write(
        sample_config,
        "# a comment",
        "",
        "- [10:00] (manager) only real line",
    )
    assert poller.drain_replies(sample_config, fake_transport) == 1

    item = _outbox(sample_config, fake_transport)[0]
    assert "only real line" in item.title
    assert "comment" not in item.title
    assert load_cursor(sample_config.reply_cursor_file) == sample_config.our_inbox.stat().st_size


def test_two_lines_split_across_cycles_are_not_digested(sample_config, fake_transport, monkeypatch):
    """A burst is 2+ pending in ONE cycle. Two replies a minute apart are two
    separate thoughts and must stay two separate reminders."""
    _Banners(monkeypatch)
    _write(sample_config, "- [10:00] (manager) first")
    poller.drain_replies(sample_config, fake_transport)
    _write(sample_config, "- [10:01] (manager) second")
    poller.drain_replies(sample_config, fake_transport)

    items = _outbox(sample_config, fake_transport)
    assert len(items) == 2
    assert all("replies" not in it.title for it in items)


# --------------------------------------------------------------------------- #
# the byte-accounting bug this feature's tests uncovered
# --------------------------------------------------------------------------- #


def test_crlf_line_endings_do_not_desync_the_cursor(sample_config, fake_transport, monkeypatch):
    """A mailbox written with CRLF must consume exactly, or replies fragment.

    `append_line` opened the file in text mode, so on Windows every terminator was
    two bytes while the drain assumed one. The cursor fell a byte behind per line;
    after three lines it pointed INSIDE the text of the last one, and the tail was
    re-sent next cycle as its own reply — a message the agent never wrote,
    delivered to the phone as if it had.

    Written here as raw bytes so the test means the same thing on every platform.
    """
    _Banners(monkeypatch)
    sample_config.our_inbox.parent.mkdir(parents=True, exist_ok=True)
    sample_config.our_inbox.write_bytes(
        b"- [10:00] (manager) alpha\r\n- [10:00] (manager) bravo\r\n- [10:00] (manager) charlie\r\n"
    )

    assert poller.drain_replies(sample_config, fake_transport) == 3
    assert load_cursor(sample_config.reply_cursor_file) == sample_config.our_inbox.stat().st_size

    # The second pass is the one that used to invent a reply out of a leftover tail.
    assert poller.drain_replies(sample_config, fake_transport) == 0
    assert len(_outbox(sample_config, fake_transport)) == 1, "one digest, and no phantom"


def test_a_mailbox_mixing_both_terminators_is_still_exact(
    sample_config, fake_transport, monkeypatch
):
    """An append-only file really can carry both, having been appended to by
    different tools on different platforms."""
    _Banners(monkeypatch)
    sample_config.our_inbox.parent.mkdir(parents=True, exist_ok=True)
    sample_config.our_inbox.write_bytes(b"- [10:00] (manager) alpha\r\n- [10:00] (manager) bravo\n")

    assert poller.drain_replies(sample_config, fake_transport) == 2
    assert load_cursor(sample_config.reply_cursor_file) == sample_config.our_inbox.stat().st_size
    assert poller.drain_replies(sample_config, fake_transport) == 0


def test_appended_lines_are_lf_on_every_platform(sample_config):
    """The mailbox is a shared protocol file; its bytes must not depend on which
    OS appended the line."""
    from voice_bridge.mailbox import append_line

    append_line(sample_config.our_inbox, "- [10:00] (vox) hello")
    assert b"\r\n" not in sample_config.our_inbox.read_bytes()


# --------------------------------------------------------------------------- #
# the banner is held back so it lands AFTER the reminder
# --------------------------------------------------------------------------- #


def test_the_banner_does_not_fire_during_the_poll_cycle(sample_config, fake_transport, monkeypatch):
    """Reported from live use: the buzz arrived a beat BEFORE the reminder.

    A push is one fast POST; the reminder has to sync to the phone. So the banner
    is held back - and the hold must not happen inside the cycle, which would
    stall polling for every message.
    """
    banners = _Banners(monkeypatch)
    started: list[float] = []

    class _FakeTimer:
        def __init__(self, delay, fn):
            started.append(delay)
            self.fn, self.daemon = fn, False

        def start(self):
            pass  # never fires on its own: the delay has not elapsed

        def cancel(self):
            pass

    monkeypatch.setattr(poller.ntfy, "_TIMER", _FakeTimer)
    _write(sample_config, "- [10:00] (manager) hello")
    poller.drain_replies(sample_config, fake_transport)

    assert started == [3.0], "it must schedule at the configured delay, not send"
    assert banners.sent == [], "nothing may be pushed inside the poll cycle"
    assert poller.ntfy.pending_count() == 1

    poller.ntfy.flush()
    # The time is vox's own stamp, applied when the reply goes out, so assert the
    # content rather than a clock reading this test does not control.
    assert len(banners.sent) == 1, "and it must still ring afterwards"
    assert banners.sent[0].endswith("[vox] hello")


def test_zero_delay_sends_immediately(sample_config, fake_transport, monkeypatch):
    """0 restores the old behaviour exactly, for anyone who preferred it."""
    banners = _Banners(monkeypatch)
    cfg = _cfg(sample_config, notify_delay=0)

    _write(cfg, "- [10:00] (manager) hello")
    poller.drain_replies(cfg, fake_transport)

    assert banners.sent, "with no delay the push happens inline"
    assert poller.ntfy.pending_count() == 0


def test_a_digest_delays_ONE_banner_not_one_per_reply(sample_config, fake_transport, monkeypatch):
    """The delay must not multiply with the number of replies bundled."""
    banners = _Banners(monkeypatch)
    _write(
        sample_config,
        "- [10:00] (manager) first",
        "- [10:00] (manager) second",
        "- [10:00] (manager) third",
    )
    poller.drain_replies(sample_config, fake_transport)

    assert poller.ntfy.pending_count() == 1, "three replies, ONE delayed banner"
    assert len(banners.settled) == 1


def test_a_delayed_banner_that_fails_is_reported(sample_config, monkeypatch, caplog):
    """The immediate path returns PushResult to a caller who reports it. A timer
    thread has no caller, so a failed delayed banner would otherwise be
    indistinguishable from one that rang."""
    from voice_bridge import ntfy

    fired: list = []

    class _Timer:
        def __init__(self, delay, fn):
            fired.append(fn)
            self.daemon = False

        def start(self):
            pass

        def cancel(self):
            pass

    monkeypatch.setattr(ntfy, "_TIMER", _Timer)
    monkeypatch.setattr(
        ntfy, "push", lambda cfg, text, click=None: ntfy.PushResult("failed", "503")
    )

    ntfy.schedule(sample_config, "hello")
    with caplog.at_level("WARNING"):
        fired[0]()

    assert any("delayed banner not sent" in r.message for r in caplog.records)
    assert any("503" in str(r.args) or "503" in r.getMessage() for r in caplog.records)


def test_stopping_the_bridge_rings_a_waiting_banner(sample_config, fake_transport, monkeypatch):
    """A banner still inside its delay when you press Ctrl-C must fire, not vanish.

    Waiting out the remaining delay would make stopping feel broken; dropping it
    would lose a message the user was told to expect. The run loop flushes.
    """
    banners = _Banners(monkeypatch)

    class _NeverFires:
        def __init__(self, delay, fn):
            self.fn, self.daemon = fn, False

        def start(self):
            pass

        def cancel(self):
            pass

    monkeypatch.setattr(poller.ntfy, "_TIMER", _NeverFires)
    _write(sample_config, "- [10:00] (manager) landed just before you stopped")
    poller.drain_replies(sample_config, fake_transport)
    assert banners.sent == [], "still waiting out its delay"

    poller.ntfy.flush()  # what the run loop's finally block calls
    assert len(banners.sent) == 1, "shutdown must ring it, not drop it"
    assert poller.ntfy.pending_count() == 0
