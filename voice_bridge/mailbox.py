"""The file-mailbox contract — line format, join/eject announcements, and dedupe.

Pure and I/O-light so it is trivially unit-testable. The clock is injectable (`now=`)
so timestamps are deterministic under test.
"""

from __future__ import annotations

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
    return f"- [{_hhmm(now)}] ({from_name}) joined — async voice spoke"


def eject_line(from_name: str, *, now: datetime | None = None) -> str:
    """The spoke's clean-exit announcement."""
    return f"- [{_hhmm(now)}] ({from_name}) stopping"


def append_line(path: Path, line: str) -> None:
    """Append one line to an append-only mailbox file, creating parents as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line.rstrip("\n") + "\n")


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
    clip at a word boundary and append `…` (used for the ntfy banner body)."""
    if limit <= 0 or len(text) <= limit:
        return text
    if not ellipsis:
        return text[:limit]
    return text[: limit - 1].rsplit(" ", 1)[0].rstrip() + "…"


def load_cursor(cursor_file: Path) -> int:
    """The byte offset drained so far (0 if none)."""
    if not cursor_file.exists():
        return 0
    try:
        return int(cursor_file.read_text(encoding="utf-8").strip())
    except ValueError:
        return 0


def save_cursor(cursor_file: Path, offset: int) -> None:
    """Persist the drain offset. Atomic: a torn cursor re-sends or strands replies."""
    atomic_write(cursor_file, str(offset))


def read_new_lines(path: Path, cursor_file: Path) -> tuple[list[str], int]:
    """COMPLETE lines appended since the saved byte-cursor, plus the new cursor (offset of
    the last newline). Robust where a line-index seen-set is not: a truncated/rewritten
    file (cursor > size) resets to 0; a half-written final line (no trailing newline) is
    held until it completes. Bounded state — one integer, no growing set."""
    if not path.exists():
        return [], 0
    data = path.read_bytes()
    cur = load_cursor(cursor_file)
    if cur > len(data):  # file was truncated / rewritten
        cur = 0
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


def first_url(text: str) -> str | None:
    """The first http(s) URL in `text`, or None. Used to surface a tappable link."""
    m = _URL_RE.search(text or "")
    return m.group(0) if m else None


def frontload_link(text: str, url: str | None) -> str:
    """Move `url` to the front so a phone banner/reminder shows the tappable link
    first. No url, or a text already starting with it, is returned unchanged."""
    if not url or text.startswith(url):
        return text
    return f"{url} — {text}"
