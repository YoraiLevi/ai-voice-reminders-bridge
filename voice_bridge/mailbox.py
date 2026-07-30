"""The file-mailbox contract - line format, join/eject announcements, and dedupe.

Pure and I/O-light so it is trivially unit-testable. The clock is injectable (`now=`)
so timestamps are deterministic under test.
"""

from __future__ import annotations

import hashlib as _hashlib

import os
import re
from datetime import datetime
from pathlib import Path

from .util import atomic_write

_URL_RE = re.compile(r"https?://\S+")


def _hhmm(now: datetime | None) -> str:
    return f"{(now or datetime.now()):%H:%M}"


def format_mailbox_line(text: str, *, from_name: str, now: datetime | None = None) -> str:
    """Render one mailbox line: `- [HH:MM] (<from_name>) <text>`, exactly one physical
    line (embedded newlines are flattened to spaces)."""
    flat = " ".join(str(text).split())
    return f"- [{_hhmm(now)}] ({from_name}) {flat}"


def join_line(from_name: str, *, now: datetime | None = None) -> str:
    """The spoke's join announcement (protocol liveness convention)."""
    return f"- [{_hhmm(now)}] ({from_name}) joined - async voice spoke"


def eject_line(from_name: str, *, now: datetime | None = None) -> str:
    """The spoke's clean-exit announcement."""
    return f"- [{_hhmm(now)}] ({from_name}) stopping"


def append_line(path: Path, line: str, *, fsync: bool = False) -> None:
    """Append one line to an append-only mailbox file, creating parents as needed.

    With `fsync=True` the bytes are pushed to the physical device before returning.
    That matters wherever a LOCAL write is about to be followed by a REMOTE one:
    the remote side is durable the instant the server accepts it, while the local
    append lives in the page cache until the kernel writes it back - up to tens of
    seconds later. Power loss inside that window loses the dictation while the
    phone shows it handled, and nothing reports the loss (FMA-16).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    created = not path.exists()
    # newline="": write LF on every platform. The mailbox is a shared, append-only
    # protocol file, so its bytes must not depend on which OS appended the line -
    # text mode turns "\n" into "\r\n" on Windows, which silently gave the file a
    # different on-disk format from the one every reader's arithmetic assumed.
    with path.open("a", encoding="utf-8", newline="") as fh:
        fh.write(line.rstrip("\n") + "\n")
        if fsync:
            fh.flush()
            os.fsync(fh.fileno())
    if fsync and created:
        # A brand-new file needs its DIRECTORY entry flushed too, or the file
        # itself can be missing after a power loss even though its bytes landed.
        _fsync_dir(path.parent)


def _fsync_dir(directory: Path) -> None:
    """Flush a directory entry. Not supported on every platform; best effort."""
    try:
        fd = os.open(str(directory), os.O_RDONLY)
    except OSError:  # pragma: no cover - Windows has no directory fd
        return
    try:
        os.fsync(fd)
    except OSError:  # pragma: no cover - filesystem without dirent fsync
        pass
    finally:
        os.close(fd)


def load_seen(path: Path) -> set[str]:
    """Read the dedupe seen-file into a set (missing file -> empty set)."""
    if not path.exists():
        return set()
    return {ln.strip() for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()}


def mark_seen(path: Path, key: str) -> None:
    """Record one processed key so it is never processed again (idempotency)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(key.strip() + "\n")


def clip(text: str, limit: int, *, ellipsis: bool = True) -> str:
    """Truncate to `limit` chars; `limit <= 0` means NO clipping. With `ellipsis`,
    clip at a word boundary and append `...` (used for the ntfy banner body)."""
    if limit <= 0 or len(text) <= limit:
        return text
    if not ellipsis:
        return text[:limit]
    return text[: limit - 1].rsplit(" ", 1)[0].rstrip() + "..."


def file_signature(data: bytes, upto: int) -> str:
    """A cheap identity for the region a cursor claims to have already consumed.

    `upto` is the offset itself, so the signature covers exactly the bytes we say we
    have read - which makes it stable under APPENDS (the only way this file
    legitimately changes) and different the moment those bytes are not the same bytes.

    The first attempt hashed a fixed 512-byte head, and the suite caught it in one
    run: for a file smaller than 512 bytes the "head" IS the whole file, so every
    append changed the signature, every cursor reset to 0, and every reply would have
    been re-sent forever. A duplicate storm in place of a dropped line is not a fix.

    THE WHOLE consumed prefix is hashed, with no cap - which is what FMA-14's register
    entry specified before any of this was built ("full prefix, no cap"). A cap would
    leave a peer rewriting bytes past the cap undetectable, and it buys nothing: the
    caller has already read the entire file into memory to find new lines, so hashing
    a prefix of it is free next to the read that produced it.

    An offset alone is meaningless without knowing WHICH file it counted. Our own
    clean exit deletes `our_inbox` (the protocol's convention) while the cursor into
    it survives in the state dir, so a peer that replies while the bridge is down gets
    a fresh file read from a stale offset.

    Observed, not theorised: a 92-byte cursor against a new 108-byte file delivered
    `[vox] ger) LINE-THREE` - two replies silently lost and the third mangled from
    mid-word. A length check cannot catch it, because the new file was LONGER than the
    stale offset; only identity can.
    """
    return _hashlib.sha256(data[:upto]).hexdigest()[:16]


def load_cursor(cursor_file: Path) -> int:
    """The byte offset drained so far (0 if none).

    Reads the FIRST line, so a cursor written by an older version - a bare integer -
    still loads. Its signature is simply absent, which the readers treat as "cannot
    verify" rather than as a mismatch: an upgrade must not re-send every reply.
    """
    if not cursor_file.exists():
        return 0
    try:
        return int(cursor_file.read_text(encoding="utf-8").splitlines()[0].strip())
    except (ValueError, IndexError):
        return 0


def load_cursor_signature(cursor_file: Path) -> str:
    """The signature of the file this cursor was measured against, or "" if unknown."""
    if not cursor_file.exists():
        return ""
    lines = cursor_file.read_text(encoding="utf-8").splitlines()
    return lines[1].strip() if len(lines) > 1 else ""


def save_cursor(cursor_file: Path, offset: int, *, path: Path | None = None) -> None:
    """Persist the drain offset. Atomic: a torn cursor re-sends or strands replies.

    Pass `path` - the file the offset counts - and its signature is stored beside the
    number, so the next read can tell whether it is still the same file.
    """
    sig = ""
    if path is not None and offset > 0:
        try:
            sig = file_signature(path.read_bytes(), offset)
        except OSError:  # pragma: no cover - unreadable mailbox
            sig = ""
    atomic_write(cursor_file, "\n".join([str(offset), sig]) if sig else str(offset))


def resolved_cursor(cursor_file: Path, data: bytes) -> int:
    """Where to start reading `data`, given what the cursor claims.

    Resets to 0 when the file cannot be the one the offset was measured against -
    shorter than the offset, or a different identity. One place decides this, because
    both readers below need the same answer and two copies would eventually differ.
    """
    cur = load_cursor(cursor_file)
    if cur > len(data):  # file was truncated / rewritten
        return 0
    stored = load_cursor_signature(cursor_file)
    if stored and stored != file_signature(data, cur):
        return 0  # a DIFFERENT file - the offset counts bytes that no longer exist
    return cur


def read_new_entries(path: Path, cursor_file: Path) -> tuple[list[tuple[str, int]], int]:
    """Complete new lines, each paired with the byte offset that CONSUMES it.

    Callers used to derive that offset themselves with `len(line + "\\n")`, which
    silently assumes a one-byte terminator. On Windows the mailbox is written
    through text mode, so the terminator is two bytes, and the cursor fell one
    byte behind per line. After three lines in one batch it landed *inside the
    text* of the last one, and the tail was re-sent next cycle as its own reply.

    Measuring the real bytes removes the assumption entirely: it is correct for
    LF, CRLF, and a file containing both - which an append-only file written by
    different tools genuinely can.
    """
    if not path.exists():
        return [], 0
    data = path.read_bytes()
    cur = resolved_cursor(cursor_file, data)
    chunk = data[cur:]
    last_nl = chunk.rfind(b"\n")
    if last_nl == -1:  # no complete line yet
        return [], cur

    entries: list[tuple[str, int]] = []
    pos = cur
    for raw in chunk[: last_nl + 1].split(b"\n")[:-1]:
        pos += len(raw) + 1  # the exact bytes this line occupies, terminator included
        entries.append((raw.decode("utf-8", "replace").rstrip("\r"), pos))
    return entries, cur + last_nl + 1


def read_new_lines(path: Path, cursor_file: Path) -> tuple[list[str], int]:
    """COMPLETE lines appended since the saved byte-cursor, plus the new cursor (offset of
    the last newline). Robust where a line-index seen-set is not: a truncated/rewritten
    file (cursor > size) resets to 0; a half-written final line (no trailing newline) is
    held until it completes. Bounded state - one integer, no growing set."""
    if not path.exists():
        return [], 0
    data = path.read_bytes()
    cur = resolved_cursor(cursor_file, data)
    chunk = data[cur:]
    last_nl = chunk.rfind(b"\n")
    if last_nl == -1:  # no complete line yet
        return [], cur
    complete = chunk[: last_nl + 1].decode("utf-8", "replace")
    return complete.splitlines(), cur + last_nl + 1


def compact_seen(seen_file: Path, live_ids: set[str]) -> None:
    """Rewrite the seen-file keeping only ids still present (drop ids for items long gone),
    bounding growth to the list size. Safe: an id for a still-incomplete item is in
    `live_ids` (it's re-read every cycle), so it is never dropped."""
    if not seen_file.exists():
        return
    keep = load_seen(seen_file) & set(live_ids)
    # Atomic: a torn seen-file loses dedupe state and re-delivers dictations.
    atomic_write(seen_file, ("\n".join(sorted(keep)) + "\n") if keep else "")


#: `- [HH:MM] (who) body` - the mailbox's own framing, with the time wildcarded.
_MAILBOX_LINE_RE = re.compile(r"^-\s*\[\d{2}:\d{2}\]\s*\((?P<who>.*?)\)\s*(?P<body>.*)$")


def strip_mailbox_prefix(line: str) -> str:
    """The message inside a mailbox line, without the line's own framing.

    A mailbox line already carries a timestamp and a speaker. Forwarding it to the
    phone verbatim and stamping it again produced two clocks and two names in one
    notification - on the smallest screen the system has (UX-4). The framing is
    for the mailbox; the phone wants the message.

    Only a LEADING mailbox stamp is removed, so ordinary text containing brackets
    survives untouched.
    """
    match = _MAILBOX_LINE_RE.match(line.strip())
    return match.group("body").strip() if match else line


def strip_astral(text: str) -> str:
    """Drop characters outside the Basic Multilingual Plane (emoji, mostly).

    Used for reminder TITLES only. The full text still reaches the user in the
    notes and the banner, so this loses decoration rather than content - which is
    the trade the alternative does not offer, since a title the phone refuses to
    render loses the message entirely (UX-3).
    """
    cleaned = "".join(ch for ch in text if ord(ch) < 0x10000)
    return " ".join(cleaned.split())


def first_url(text: str) -> str | None:
    """The first http(s) URL in `text`, or None. Used to surface a tappable link."""
    m = _URL_RE.search(text or "")
    return m.group(0) if m else None


def frontload_link(text: str, url: str | None) -> str:
    """Move `url` to the front so a phone banner/reminder shows the tappable link
    first. No url, or a text already starting with it, is returned unchanged."""
    if not url or text.startswith(url):
        return text
    return f"{url} - {text}"
