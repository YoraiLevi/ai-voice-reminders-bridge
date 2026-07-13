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

from . import log as _log
from . import ntfy
from .config import Config
from .mailbox import (
    append_line,
    clip,
    compact_seen,
    eject_line,
    first_url,
    format_mailbox_line,
    frontload_link,
    join_line,
    load_seen,
    mark_seen,
    read_new_lines,
    save_cursor,
)
from .transport import Transport


def _hhmm(now: datetime | None) -> str:
    return f"{(now or datetime.now()):%H:%M}"


def poll_inbox(cfg: Config, t: Transport, *, now: datetime | None = None) -> int:
    """Read new items from the phone's inbox list, append each to the peer's inbox
    file, and mark it seen. Returns how many were newly bridged."""
    inbox = t.resolve_list(cfg.inbox_list, cfg.inbox_list_id)
    seen = load_seen(cfg.seen_file)
    items = t.read_incomplete(inbox)
    n = 0
    for item in items:
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
    # bound the seen-file: keep only ids still readable this cycle (a not-yet-completed
    # item is still in `items`, so it's never dropped).
    live = {i.id for i in items}
    if len(seen) > len(live) + 50:
        compact_seen(cfg.seen_file, live)
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
    """Send each not-yet-drained line of our inbox file to the phone, tracked by a
    byte-offset cursor (robust to truncation, bounded state). Returns how many sent."""
    lines, offset = read_new_lines(cfg.our_inbox, cfg.reply_cursor_file)
    n = 0
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        send_reply(cfg, t, line, now=now)
        n += 1
    save_cursor(cfg.reply_cursor_file, offset)  # advance past everything read (incl. skipped)
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


def run(
    cfg: Config,
    t: Transport,
    *,
    once: bool = False,
    interval: int | None = None,
    max_backoff: int = 300,
) -> int:
    """Announce join, loop poll+drain each interval, eject on stop. Resilient: a transport
    error is logged and retried with exponential backoff (never crashes the daemon).
    `--once` does a single attempt — 0 if anything new, 1 if nothing, 2 on error."""
    log = _log.get()
    period = interval if interval is not None else cfg.poll_interval
    announce_join(cfg)
    backoff = 1
    try:
        while True:
            try:
                t.connect()
                polled, drained = run_once(cfg, t)
                backoff = 1
                if once:
                    return 0 if (polled or drained) else 1
            except Exception as exc:  # transport/network hiccup
                if once:
                    log.error("run --once failed: %s", exc)
                    return 2
                log.warning("transport error: %s — retrying in %ss", exc, backoff)
                time.sleep(backoff)
                backoff = min(backoff * 2, max_backoff)
                continue
            time.sleep(period)
    finally:
        announce_eject(cfg)
