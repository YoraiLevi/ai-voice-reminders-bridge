"""The file-mailbox contract — line format, join/eject announcements, and dedupe.

Pure and I/O-light so it is trivially unit-testable. The clock is injectable (`now=`)
so timestamps are deterministic under test.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path


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
