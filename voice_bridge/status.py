"""A glance at the spoke's health - the quick counterpart to `doctor`'s full survey.
File/pidfile/reachability only; no transport auth (that's `doctor`'s job), so it's fast."""

from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path

from .config import Config
from .mailbox import load_seen


def poller_pidfile(cfg: Config) -> Path:
    return cfg.state_dir / "poller.pid"


def _alive(pid: int) -> bool:
    """Is this pid running? Asks without delivering anything to it.

    On POSIX, signal 0 is the standard "does it exist?" probe and sends nothing.

    On Windows there is no such signal. `os.kill` maps signal 0 to
    `GenerateConsoleCtrlEvent(CTRL_C_EVENT, pid)` - an actual Ctrl-C delivered to
    a process GROUP, not a query. So the probe would interrupt whatever shares the
    console, which for a test run is the test runner itself. It is easy to believe
    this is safe, because under a terminal emulator with no real console attached
    the event silently fails and the call appears to "just return" - which is
    exactly what an earlier check of this concluded. On a real console it fires.

    Windows therefore uses `OpenProcess`, which only asks. A non-positive pid is
    still rejected up front: pid 0 means "every process in this console group",
    and an empty or truncated pidfile parses to 0 (FMA-17).
    """
    if pid <= 0:
        return False

    if os.name == "nt":
        import ctypes

        # PROCESS_QUERY_LIMITED_INFORMATION - the least authority that answers.
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # type: ignore[attr-defined]
        if not handle:
            return False
        try:
            still_running = 259  # STILL_ACTIVE
            code = ctypes.c_ulong()
            ok = ctypes.windll.kernel32.GetExitCodeProcess(  # type: ignore[attr-defined]
                handle, ctypes.byref(code)
            )
            return bool(ok) and code.value == still_running
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)  # type: ignore[attr-defined]

    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def poller_pid(cfg: Config) -> int | None:
    f = poller_pidfile(cfg)
    if not f.exists():
        return None
    try:
        pid = int(f.read_text(encoding="utf-8").strip())
    except ValueError:
        return None
    return pid if _alive(pid) else None


def _mtime(path: Path) -> str | None:
    if not path.exists():
        return None
    return datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds")


#: `- [HH:MM] (from_name) body` with the TIME wildcarded - an exact match against
#: an unknown timestamp is impossible. `from_name` is captured greedily up to the
#: last `") "` so a name containing a bracket cannot truncate the parse.
_LINE_RE = re.compile(r"^- \[\d{2}:\d{2}\] \((?P<who>.*)\) (?P<body>.*)$")

#: The suffixes our own announcements end with (see mailbox.join_line/eject_line).
_ANNOUNCEMENTS = ("joined - async voice spoke", "stopping")


def _last_complete_line(path: Path) -> str | None:
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines or not text.endswith("\n"):
        # Hold a half-written final line, as the drain reader does.
        lines = lines[:-1] if lines and not text.endswith("\n") else lines
    return lines[-1] if lines else None


def _is_our_announcement(line: str, spoke: str) -> bool:
    """True when the peer file's last line is a join/eject announcement of ours.

    The discriminator is FORMAT, not authorship: every line in that file is our
    write - dictations are forwarded under our own tag - so asking "is this ours?"
    would silence the hint permanently.
    """
    m = _LINE_RE.match(line)
    if not m or m.group("who") != spoke:
        return False
    return m.group("body").strip() in _ANNOUNCEMENTS


def staleness_hint(cfg: Config, *, stale_after: int = 3600, now: float | None = None) -> str | None:
    """Warn when a dictation has gone unanswered for too long, or None.

    This is the silent half-round-trip: the message is delivered correctly and
    nobody is reading it. voice-bridge does not own peer presence and cannot see
    whether an agent exists, so it never claims one is absent - it reports only
    the file facts it can observe, and says "is a peer joined?" rather than
    "no peer is joined" (FMA-9).
    """
    if stale_after < 0:  # explicitly disabled
        return None

    peer = cfg.peer_inbox
    if not peer.exists():
        return None

    peer_mtime = peer.stat().st_mtime
    # A missing inbox means we were never answered at all (it is deleted on a
    # clean eject), so epoch-0 is the honest reading rather than "no data".
    ours_mtime = cfg.our_inbox.stat().st_mtime if cfg.our_inbox.exists() else 0.0

    clock = now if now is not None else datetime.now().timestamp()
    elapsed = clock - peer_mtime
    if peer_mtime <= ours_mtime or elapsed <= stale_after:
        return None

    last = _last_complete_line(peer)
    if last is None or _is_our_announcement(last, cfg.from_name):
        return None  # announcing ourselves is not an unanswered question

    return (
        f"warning: {peer.name} updated {int(elapsed)}s ago with no newer reply "
        f"in {cfg.our_inbox.name} - is a peer joined?"
    )


def gather(cfg: Config) -> dict:
    from . import server as server_mod

    out: dict = {
        "transport": cfg.transport,
        "mailbox_dir": str(cfg.mailbox_dir),
        "poller_running": poller_pid(cfg) is not None,
        # Named for what they measure, not for a direction that inverted between
        # the two edges. `peer_inbox` is where OUR dictations are written, so its
        # mtime is the last dictation we delivered; `our_inbox` is where replies
        # arrive. The old `last_inbound`/`last_outbound` labels named the exact
        # opposite of their file, so anyone debugging "nothing is arriving" was
        # reading the wrong timestamp (STATUS-1).
        "last_dictation": _mtime(cfg.peer_inbox),
        "last_reply": _mtime(cfg.our_inbox),
        "inbox_seen": len(load_seen(cfg.seen_file)),
    }
    hint = staleness_hint(cfg, stale_after=getattr(cfg, "reply_stale_after", 3600))
    if hint:
        out["warning"] = hint
    out["server_reachable"] = (
        server_mod.is_reachable(server_mod.client_url(cfg)) if cfg.transport == "radicale" else None
    )
    return out
