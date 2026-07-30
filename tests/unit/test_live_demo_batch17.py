"""Batch 17: the CLEAN exit was the destroyer.

Observed live, and deterministic. Peer replies had been queued in the mailbox since
early afternoon. The human started the bridge at 18:58 and Ctrl-C'd within seconds -
an entirely ordinary thing to do to a foreground process. `announce_eject` deleted
`our_inbox` before the first cycle had drained it. The file was verified GONE, the
replies were never delivered, and nothing said a word.

This is FMA-14's sibling and it is worse in one specific way: FMA-14 needed the peer
to write while we were DOWN, whereas this needs only a person to be fast. Batch 13
fixed the convention's other half - a cursor left pointing into a deleted file - and
in doing so made the deletion itself the last unstated assumption: *everything in this
file has been sent*. A fast Ctrl-C falsifies it.

The invariant is now stated: **never delete content this run did not read.** The file
and its cursor survive together, and batch 13's signature machinery is what makes
trusting that cursor across the gap safe.

The alternative fix - one final drain before ejecting - is argued down in
`announce_eject`'s docstring, and the short version is that it puts a network call in
the shutdown path where a second Ctrl-C would raise out of the `finally` and skip the
eject line altogether. Trading a silent loss for a broken exit is not a trade.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from voice_bridge import poller
from voice_bridge.mailbox import save_cursor, undrained
from voice_bridge.transport import FakeTransport


@dataclass
class CtrlCTransport(FakeTransport):
    """Ctrl-C landing in `connect()` - i.e. before the first cycle can drain anything.

    That is the exact live shape: the interrupt arrived while the very first connect
    was still in flight, so `run_once` never ran and every queued reply was still
    behind the cursor when the `finally` fired.
    """

    def connect(self) -> None:
        raise KeyboardInterrupt


def _queue(cfg, *lines: str) -> None:
    cfg.our_inbox.parent.mkdir(parents=True, exist_ok=True)
    with cfg.our_inbox.open("a", encoding="utf-8", newline="") as fh:
        for line in lines:
            fh.write(line + "\n")


def _delivered(t: FakeTransport, cfg) -> list[str]:
    """The reply BODIES that reached the phone - notes only, deliberately.

    A single reply carries its text in both the title and the notes, so joining the two
    counts every delivery twice and a duplicate-detecting test reads permanently red.
    The notes are the one field that holds each body exactly once whether it arrived
    alone or inside a digest.
    """
    items = t.read_incomplete(t.resolve_list("", cfg.output_list_id))
    return [it.notes or it.title for it in items]


def test_a_fast_ctrl_c_no_longer_destroys_the_queued_replies(sample_config, capsys):
    """The live defect, end to end: queue, start, interrupt before a cycle, and the
    replies must still be on disk afterwards."""
    cfg = sample_config
    _queue(cfg, "- [14:30] (manager) the first answer", "- [14:31] (manager) the second")

    rc = poller.run(cfg, CtrlCTransport())

    assert rc == 0, "Ctrl-C is a deliberate stop, not a failure"
    assert cfg.our_inbox.exists(), "the clean exit must not delete an undrained inbox"
    body = cfg.our_inbox.read_text(encoding="utf-8")
    assert "the first answer" in body and "the second" in body
    assert "(vox) stopping" in cfg.peer_inbox.read_text(encoding="utf-8"), (
        "keeping the file must not cost the protocol its eject line"
    )
    assert "2 undelivered replies" in capsys.readouterr().out, (
        "a file left behind silently reads as a failed cleanup - the loss was silent once"
    )


def test_the_next_run_delivers_the_kept_replies_exactly_once(sample_config, fake_transport, capsys):
    """The other half of the same sentence. Surviving is only useful if the replies then
    arrive, and arrive ONCE - the recovery path the manager re-queued by hand."""
    cfg = sample_config
    _queue(cfg, "- [14:30] (manager) the first answer", "- [14:31] (manager) the second")

    poller.run(cfg, CtrlCTransport())
    assert _delivered(fake_transport, cfg) == [], "nothing was sent by the interrupted run"

    poller.run(cfg, fake_transport, once=True)

    sent = " ".join(_delivered(fake_transport, cfg))
    assert sent.count("the first answer") == 1
    assert sent.count("the second") == 1


def test_a_drained_inbox_is_still_deleted(sample_config, fake_transport):
    """The convention is intact where it was never wrong. This is the converse test,
    and it is the one that keeps the fix from quietly becoming "never clean up":
    a rule that fires on every exit teaches the reader nothing about which exits matter.
    """
    cfg = sample_config
    _queue(cfg, "- [14:30] (manager) already sent")
    poller.drain_replies(cfg, fake_transport)

    poller.announce_eject(cfg)

    assert not cfg.our_inbox.exists()
    assert not cfg.reply_cursor_file.exists(), "batch 13: no cursor into a deleted file"


def test_the_kept_file_keeps_its_cursor(sample_config, fake_transport):
    """The two must survive TOGETHER. Keeping the file while clearing the cursor - which
    is what batch 13's rule says in isolation - would re-send everything already
    delivered on the next start. The cursor is only invalid when the file is gone."""
    cfg = sample_config
    _queue(cfg, "- [14:30] (manager) already sent")
    poller.drain_replies(cfg, fake_transport)
    _queue(cfg, "- [14:32] (manager) not yet sent")

    poller.announce_eject(cfg)

    assert cfg.our_inbox.exists()
    assert cfg.reply_cursor_file.exists(), "the cursor is what stops a re-send"

    poller.drain_replies(cfg, fake_transport)
    sent = " ".join(_delivered(fake_transport, cfg))
    assert sent.count("already sent") == 1, "the delivered reply must not be sent twice"
    assert "not yet sent" in sent


def test_a_half_written_line_counts_as_undrained(sample_config):
    """BYTES decide, not lines. A trailing line without its newline is not a complete
    entry, so a line-counting rule would licence deleting it - and it is somebody's
    words either way."""
    cfg = sample_config
    cfg.our_inbox.parent.mkdir(parents=True, exist_ok=True)
    cfg.our_inbox.write_text("- [14:30] (manager) mid-writ", encoding="utf-8")

    poller.announce_eject(cfg)

    assert cfg.our_inbox.exists()


def test_an_untrustworthy_cursor_keeps_the_whole_file(sample_config, tmp_path):
    """A cursor whose signature does not match the file counts bytes that may never have
    existed. `resolved_cursor` already resets it to 0 for reading; the same answer has to
    govern deleting, or the two disagree about what was consumed at the one moment the
    disagreement is unrecoverable."""
    cfg = sample_config
    _queue(cfg, "- [14:30] (manager) one", "- [14:31] (manager) two")
    save_cursor(cfg.reply_cursor_file, cfg.our_inbox.stat().st_size, path=cfg.our_inbox)
    # The peer rewrites the consumed region - same length, different bytes, which is the
    # case a length check cannot see. write_BYTES, or text mode would change the length
    # too on Windows and the test would pass for the wrong reason.
    cfg.our_inbox.write_bytes(b"- [14:30] (manager) ONE\n- [14:31] (manager) two\n")

    assert undrained(cfg.our_inbox, cfg.reply_cursor_file)[1] == 2

    poller.announce_eject(cfg)
    assert cfg.our_inbox.exists()


@pytest.mark.parametrize(
    "content, expect_lines",
    [
        ("", 0),
        ("\n\n", 0),
        ("# a comment\n", 0),
        ("- [10:00] (m) one\n", 1),
    ],
)
def test_undrained_counts_only_what_would_be_delivered(tmp_path, content, expect_lines):
    """The count is what the user is told, so it has to be true. Blank lines and comments
    are bytes we keep but not replies we owe anyone - the message branches on that."""
    inbox = tmp_path / "to-vox.md"
    # write_BYTES: `write_text` goes through text mode, so on Windows every "\n" becomes
    # two bytes and this test's own arithmetic would be wrong about the file it wrote.
    # That is the same one-byte-terminator assumption `read_new_entries` exists to kill.
    inbox.write_bytes(content.encode("utf-8"))
    cursor = tmp_path / "reply.cursor"

    held, replies = undrained(inbox, cursor)

    assert replies == expect_lines
    assert held == len(content.encode("utf-8"))


def test_a_missing_inbox_is_not_undrained(sample_config):
    """The ordinary case on a fresh machine: no file, nothing held, and the eject must
    not invent work for itself."""
    assert undrained(sample_config.our_inbox, sample_config.reply_cursor_file) == (0, 0)
