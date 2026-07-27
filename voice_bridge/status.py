"""A glance at the spoke's health — the quick counterpart to `doctor`'s full survey.
File/pidfile/reachability only; no transport auth (that's `doctor`'s job), so it's fast."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from .config import Config
from .mailbox import load_seen


def poller_pidfile(cfg: Config) -> Path:
    return cfg.state_dir / "poller.pid"


def _alive(pid: int) -> bool:
    """Is this pid running? Probing with signal 0 asks without delivering anything.

    A non-positive pid is rejected BEFORE the call, and not merely as tidiness:
    on Windows `signal.CTRL_C_EVENT` is 0 and pid 0 means "every process in this
    console group", so `os.kill(0, 0)` would deliver a real Ctrl-C to the whole
    group — the status command killing the poller it was asked to report on. An
    empty or truncated pidfile parses to 0, so that path is reachable (FMA-17).
    """
    if pid <= 0:
        return False
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
    out["server_reachable"] = (
        server_mod.is_reachable(server_mod.client_url(cfg)) if cfg.transport == "radicale" else None
    )
    return out
