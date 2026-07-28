"""Mailbox contract: line grammar, join/eject, dedupe, and clip (incl. -1 = no clip)."""

from __future__ import annotations

from voice_bridge import mailbox


def test_line_grammar_and_flattening(fixed_clock):
    line = mailbox.format_mailbox_line("hello\nthere   world", from_name="vox", now=fixed_clock)
    assert line == "- [08:48] (vox) hello there world"  # one physical line


def test_join_and_eject_lines(fixed_clock):
    assert mailbox.join_line("vox", now=fixed_clock) == "- [08:48] (vox) joined - async voice spoke"
    assert mailbox.eject_line("vox", now=fixed_clock) == "- [08:48] (vox) stopping"


def test_append_and_load_roundtrip(tmp_path):
    f = tmp_path / "sub" / "to-manager.md"  # parent auto-created
    mailbox.append_line(f, "- [08:48] (vox) hi")
    assert f.read_text(encoding="utf-8") == "- [08:48] (vox) hi\n"


def test_seen_dedupe(tmp_path):
    seen = tmp_path / "seen.txt"
    assert mailbox.load_seen(seen) == set()
    mailbox.mark_seen(seen, "item-1")
    mailbox.mark_seen(seen, "item-2")
    assert mailbox.load_seen(seen) == {"item-1", "item-2"}


def test_clip_no_clip_when_limit_nonpositive():
    text = "x" * 300
    assert mailbox.clip(text, -1) == text
    assert mailbox.clip(text, 0) == text


def test_clip_word_boundary_with_ellipsis():
    text = "word " * 60  # 300 chars
    out = mailbox.clip(text, 150)
    assert len(out) <= 150
    assert out.endswith("...")


def test_clip_hard_cut_without_ellipsis():
    out = mailbox.clip("x" * 300, 120, ellipsis=False)
    assert out == "x" * 120


def test_first_url_found_and_none():
    assert mailbox.first_url("see https://ex.com/a for details") == "https://ex.com/a"
    assert mailbox.first_url("no link here") is None


def test_frontload_link_moves_url_to_front():
    assert (
        mailbox.frontload_link("done, see https://x/y", "https://x/y")
        == "https://x/y - done, see https://x/y"
    )
    assert mailbox.frontload_link("plain text", None) == "plain text"
    assert (
        mailbox.frontload_link("https://x/y already front", "https://x/y")
        == "https://x/y already front"
    )


def test_read_new_lines_cursor_advances(tmp_path):
    f, c = tmp_path / "to.md", tmp_path / "cur"
    f.write_text("a\nb\n", encoding="utf-8")
    lines, off = mailbox.read_new_lines(f, c)
    assert lines == ["a", "b"]
    mailbox.save_cursor(c, off)
    with f.open("a", encoding="utf-8") as fh:
        fh.write("c\n")
    lines2, _ = mailbox.read_new_lines(f, c)
    assert lines2 == ["c"]  # only the new line


def test_read_new_lines_holds_partial_last_line(tmp_path):
    f, c = tmp_path / "to.md", tmp_path / "cur"
    f.write_text("done\npartial", encoding="utf-8")  # no trailing newline
    lines, _ = mailbox.read_new_lines(f, c)
    assert lines == ["done"]  # the partial line is held until completed


def test_read_new_lines_resets_on_truncation(tmp_path):
    f, c = tmp_path / "to.md", tmp_path / "cur"
    f.write_text("x\ny\nz\n", encoding="utf-8")
    _, off = mailbox.read_new_lines(f, c)
    mailbox.save_cursor(c, off)
    f.write_text("new\n", encoding="utf-8")  # rewritten shorter than the cursor
    lines, _ = mailbox.read_new_lines(f, c)
    assert lines == ["new"]  # cursor reset, re-read from 0


def test_compact_seen_keeps_only_live(tmp_path):
    s = tmp_path / "seen"
    s.write_text("a\nb\nc\n", encoding="utf-8")
    mailbox.compact_seen(s, {"b", "c", "zzz"})
    assert mailbox.load_seen(s) == {"b", "c"}
