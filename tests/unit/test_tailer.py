"""tailer: last-N reads and rotation-safe following.

Both behaviours were bugs in the CLI handler (TAIL-1/TAIL-2): `tail` dumped a
file's entire history, and `--follow` kept a byte offset that it never reset, so
after the file was truncated or rotated it sat past the end and silently showed
nothing for ever — the worst kind of failure for a monitoring command, because
silence is indistinguishable from "nothing is happening".

Extracting them as pure functions is what makes them testable at all: the old
logic lived inside a follow loop and could only be exercised with threads and
sleeps.
"""

from __future__ import annotations

from voice_bridge import tailer


def _write(path, lines):
    path.write_text("".join(f"{ln}\n" for ln in lines), encoding="utf-8")


# --------------------------------------------------------------------------- #
# read_tail — TAIL-1
# --------------------------------------------------------------------------- #


def test_read_tail_returns_only_the_last_n(tmp_path):
    f = tmp_path / "to-manager.md"
    _write(f, [f"line {i}" for i in range(100)])
    got = tailer.read_tail(f, 10)
    assert got == [f"line {i}" for i in range(90, 100)]


def test_read_tail_defaults_to_twenty(tmp_path):
    f = tmp_path / "to-manager.md"
    _write(f, [f"line {i}" for i in range(100)])
    assert len(tailer.read_tail(f)) == 20


def test_read_tail_shorter_file_returns_everything(tmp_path):
    f = tmp_path / "to-manager.md"
    _write(f, ["only", "three", "lines"])
    assert tailer.read_tail(f, 20) == ["only", "three", "lines"]


def test_read_tail_missing_or_empty_is_empty(tmp_path):
    assert tailer.read_tail(tmp_path / "absent.md") == []
    empty = tmp_path / "empty.md"
    empty.write_text("", encoding="utf-8")
    assert tailer.read_tail(empty) == []


def test_read_tail_zero_returns_nothing(tmp_path):
    f = tmp_path / "f.md"
    _write(f, ["a", "b"])
    assert tailer.read_tail(f, 0) == []


# --------------------------------------------------------------------------- #
# follow_step — TAIL-2
# --------------------------------------------------------------------------- #


def test_follow_step_from_zero_reads_everything(tmp_path):
    f = tmp_path / "f.md"
    _write(f, ["a", "b"])
    lines, offset = tailer.follow_step(f, 0)
    assert lines == ["a", "b"]
    assert offset == f.stat().st_size


def test_follow_step_returns_only_what_is_new(tmp_path):
    f = tmp_path / "f.md"
    _write(f, ["a"])
    _, offset = tailer.follow_step(f, 0)
    with f.open("a", encoding="utf-8") as fh:
        fh.write("b\n")
    lines, offset2 = tailer.follow_step(f, offset)
    assert lines == ["b"]
    assert offset2 > offset


def test_follow_step_resets_when_the_file_shrinks(tmp_path):
    """TAIL-2: after truncation the offset points past the end.

    Without a reset the reader returns nothing for ever and the user sees a
    perfectly healthy-looking, permanently silent `--follow`.
    """
    f = tmp_path / "f.md"
    _write(f, [f"old {i}" for i in range(50)])
    _, offset = tailer.follow_step(f, 0)

    _write(f, ["fresh start"])  # rotated/truncated: now much smaller
    lines, new_offset = tailer.follow_step(f, offset)

    assert lines == ["fresh start"], "must re-read from the beginning, not go silent"
    assert new_offset == f.stat().st_size


def test_follow_step_holds_an_incomplete_trailing_line(tmp_path):
    """A half-written line is a half-written message; show it once it is whole."""
    f = tmp_path / "f.md"
    f.write_text("complete\npartial-no-newline", encoding="utf-8")
    lines, offset = tailer.follow_step(f, 0)
    assert lines == ["complete"]

    with f.open("a", encoding="utf-8") as fh:
        fh.write("-now-finished\n")
    lines2, _ = tailer.follow_step(f, offset)
    assert lines2 == ["partial-no-newline-now-finished"]


def test_follow_step_missing_file_is_quiet_and_resettable(tmp_path):
    """The spoke deletes its own inbox on eject; following must survive that."""
    f = tmp_path / "gone.md"
    lines, offset = tailer.follow_step(f, 999)
    assert lines == []
    assert offset == 0

    _write(f, ["recreated"])
    lines2, _ = tailer.follow_step(f, offset)
    assert lines2 == ["recreated"]


def test_follow_step_no_change_returns_nothing(tmp_path):
    f = tmp_path / "f.md"
    _write(f, ["a"])
    _, offset = tailer.follow_step(f, 0)
    assert tailer.follow_step(f, offset) == ([], offset)
