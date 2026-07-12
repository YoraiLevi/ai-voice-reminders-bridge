"""The transport-agnostic orchestration loop — the shared heart both legacy bridges
duplicated. It talks ONLY to the Transport interface, never a concrete backend.

Two directions across the two buffers:
  poll_inbox  : phone's inbox_list  --(read)-->  cfg.peer_inbox   (to-<route_to>.md)
  drain_replies: cfg.our_inbox (to-<spoke>.md)  --(send)-->  phone's output_list  (+ ntfy)

Dedupe is by seen-files (idempotent across restarts). Clocks are injectable (`now=`)
so timestamps are deterministic under test.
"""

from __future__ import annotations

import time
from datetime import datetime

from . import ntfy
from .config import Config
from .mailbox import (
    append_line,
    clip,
    eject_line,
    first_url,
    format_mailbox_line,
    frontload_link,
    join_line,
    load_seen,
    mark_seen,
)
from .transport import Transport


def _hhmm(now: datetime | None) -> str:
    return f"{(now or datetime.now()):%H:%M}"


def poll_inbox(cfg: Config, t: Transport, *, now: datetime | None = None) -> int:
    """Read new items from the phone's inbox list, append each to the peer's inbox
    file, and mark it seen. Returns how many were newly bridged."""
    inbox = t.resolve_list(cfg.inbox_list, cfg.inbox_list_id)
    seen = load_seen(cfg.seen_file)
    n = 0
    for item in t.read_incomplete(inbox):
        if item.id in seen:
            continue
        text = f"{item.title} — {item.notes}" if item.notes else item.title
        append_line(cfg.peer_inbox, format_mailbox_line(text, from_name=cfg.from_name, now=now))
        mark_seen(cfg.seen_file, item.id)  # seen-file FIRST: a crash after this can't re-emit
        try:
            t.complete(inbox, item.id)  # then clear it off the phone's list (secondary guard)
        except Exception:
            pass
        n += 1
    return n


def send_reply(
    cfg: Config,
    t: Transport,
    text: str,
    *,
    notify: bool = True,
    needs_input: bool = True,
    now: datetime | None = None,
) -> str:
    """Frame one reply — stamp `[HH:MM][spoke]`, front-load any URL, clip the title —
    then write it to the phone's output list and (notify) fire a banner. Returns the
    created item id. The framing is shared; the write crosses the seam into `t.add_todo`."""
    url = first_url(text)
    body = frontload_link(text.strip(), url)
    stamped = f"[{_hhmm(now)}][{cfg.spoke_name}] {body}"
    flat = " ".join(stamped.split())
    summary = clip(flat, cfg.reply_summary_limit, ellipsis=False) or "(reply)"
    out = t.resolve_list(cfg.output_list, cfg.output_list_id)
    rid = t.add_todo(out, summary, notes=stamped, needs_input=needs_input)
    if notify:
        ntfy.push(cfg, stamped, click=url)
    return rid


def drain_replies(cfg: Config, t: Transport, *, now: datetime | None = None) -> int:
    """Send each not-yet-drained line of our inbox file to the phone. Dedupe key is the
    line's index+text, stable because the file is append-only. Returns how many sent."""
    path = cfg.our_inbox
    if not path.exists():
        return 0
    seen = load_seen(cfg.reply_seen_file)
    n = 0
    for i, raw in enumerate(path.read_text(encoding="utf-8").splitlines()):
        line = raw.rstrip()
        if not line:
            continue
        key = f"{i}:{line}"
        if key in seen:
            continue
        send_reply(cfg, t, line, now=now)
        mark_seen(cfg.reply_seen_file, key)
        n += 1
    return n


def announce_join(cfg: Config, *, now: datetime | None = None) -> None:
    """Write the spoke's join line into the peer's inbox (protocol liveness)."""
    append_line(cfg.peer_inbox, join_line(cfg.from_name, now=now))


def announce_eject(cfg: Config, *, now: datetime | None = None) -> None:
    """Write the eject line and delete our own inbox file — exactly the protocol's
    clean-exit convention (a missed cleanup is harmless under the keystone rule)."""
    append_line(cfg.peer_inbox, eject_line(cfg.from_name, now=now))
    if cfg.our_inbox.exists():
        cfg.our_inbox.unlink()


def run_once(cfg: Config, t: Transport, *, now: datetime | None = None) -> tuple[int, int]:
    """One poll + drain pass. Returns (polled, drained)."""
    return poll_inbox(cfg, t, now=now), drain_replies(cfg, t, now=now)


def dry_run(cfg: Config) -> int:
    """Print the resolved config and the exact mailbox line a dictation would become —
    no transport, no network."""
    for k, v in cfg.as_dict().items():
        print(f"  {k:<20} : {v}")
    sample = format_mailbox_line("example request", from_name=cfg.from_name)
    print(f"\n  would append to {cfg.peer_inbox}:\n    {sample}")
    return 0


def run(cfg: Config, t: Transport, *, once: bool = False, interval: int | None = None) -> int:
    """Connect, announce join, loop poll+drain each interval, eject on stop. `--once`
    does a single pass and returns 0 if anything new was bridged, else 1."""
    period = interval if interval is not None else cfg.poll_interval
    t.connect()
    announce_join(cfg)
    try:
        if once:
            polled, drained = run_once(cfg, t)
            return 0 if (polled or drained) else 1
        while True:
            run_once(cfg, t)
            time.sleep(period)
    finally:
        announce_eject(cfg)
    return 0
