"""Pure helpers for reading the tail of a mailbox file and following it.

Kept free of config and I/O policy so the `tail` command's behaviour can be
tested directly, without threads or sleeps - the old logic lived inside a follow
loop and was effectively untestable, which is why both of its bugs survived:

* it printed a file's entire history rather than a useful last-N (TAIL-1);
* `--follow` never reset its byte offset, so after a truncation or rotation the
  offset sat past the end of the file and the command went silent for ever
  (TAIL-2). For a monitoring command that is the worst failure available:
  silence looks exactly like "nothing has happened yet".
"""

from __future__ import annotations

from pathlib import Path

DEFAULT_TAIL_LINES = 20


def read_tail(path: Path, n: int = DEFAULT_TAIL_LINES) -> list[str]:
    """The last `n` complete lines of `path` (fewer if the file is shorter).

    A missing or empty file yields no lines rather than an error: the mailbox
    files legitimately come and go, and a viewer should not crash on an absence.
    """
    if n <= 0 or not path.exists():
        return []
    text = path.read_text(encoding="utf-8", errors="replace")
    if not text:
        return []
    return text.splitlines()[-n:]


def follow_step(path: Path, prev_offset: int) -> tuple[list[str], int]:
    """One poll of a followed file: `(new complete lines, offset to resume from)`.

    Rotation safety is the point. If the file is now *smaller* than the offset we
    were holding, it was truncated or rotated, so the offset is meaningless and we
    start again from the beginning - otherwise the reader would sit past the end
    and report nothing for ever.

    An incomplete trailing line (no newline yet) is held back and re-read once it
    is finished, matching the mailbox drain reader: a half-written line is a
    half-written message.
    """
    if not path.exists():
        return [], 0

    size = path.stat().st_size
    start = 0 if size < prev_offset else prev_offset

    with path.open("rb") as fh:
        fh.seek(start)
        chunk = fh.read()

    if not chunk:
        return [], start

    text = chunk.decode("utf-8", errors="replace")
    consumed = len(chunk)
    if not text.endswith("\n"):
        # Hold the partial last line: rewind to just after the final newline so it
        # is re-read whole next time. No newline at all -> consume nothing.
        cut = text.rfind("\n")
        if cut == -1:
            return [], start
        text = text[: cut + 1]
        consumed = len(text.encode("utf-8"))

    return text.splitlines(), start + consumed
