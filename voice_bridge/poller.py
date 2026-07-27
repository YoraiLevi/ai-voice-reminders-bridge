"""The transport-agnostic orchestration loop — the shared heart both legacy bridges
duplicated. It talks ONLY to the Transport interface, never a concrete backend.

Two directions across the two buffers:
  poll_inbox  : phone's inbox_list  --(read)-->  cfg.peer_inbox   (to-<route_to>.md)
  drain_replies: cfg.our_inbox (to-<spoke>.md)  --(send)-->  phone's output_list  (+ ntfy)

Dedupe is by seen-files (idempotent across restarts). Clocks are injectable (`now=`)
so timestamps are deterministic under test.
"""

from __future__ import annotations

import os
import time
from datetime import datetime

from . import log as _log
from . import ntfy
from .config import Config
from .errors import _is_auth, is_transient
from .status import staleness_hint as _staleness_hint
from .mailbox import (
    load_cursor,
    append_line,
    clip,
    compact_seen,
    eject_line,
    first_url,
    format_mailbox_line,
    strip_astral,
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
            # Already delivered, yet still live on the backend — so a previous
            # completion failed. Retry it rather than leaving the reminder
            # visible: the user reads "still there" as "not processed", re-dictates,
            # and the agent receives the message twice (FMA-2). This is the
            # `seen ∩ live` retry set, and it costs one call per stuck item.
            t.complete(inbox, item.id)
            continue

        text = f"{item.title} — {item.notes}" if item.notes else item.title
        # fsync BEFORE marking handled: the completion below is durable remotely
        # the moment the server accepts it, while this append is not durable until
        # flushed. Ordering alone cannot close that window (FMA-16, ruled fix).
        append_line(
            cfg.peer_inbox,
            format_mailbox_line(text, from_name=cfg.from_name, now=now),
            fsync=True,
        )
        mark_seen(cfg.seen_file, item.id)  # seen-file FIRST: a crash after this can't re-emit
        # NOT swallowed. All three transports used to hide a failure here, so the
        # loop could not tell "cleared off the phone" from "silently didn't".
        t.complete(inbox, item.id)
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
    # The TITLE may be stripped of emoji; `stamped` — which carries the original
    # text — is what goes into the notes and the banner, so nothing is lost.
    titled = strip_astral(flat) if cfg.emoji_titles == "strip" else flat
    summary = clip(titled, cfg.reply_summary_limit, ellipsis=False) or "(reply)"
    out = t.resolve_list(cfg.output_list, cfg.output_list_id)
    rid = t.add_todo(out, summary, notes=stamped, needs_input=needs_input)
    if notify:
        ntfy.push(cfg, stamped, click=url)
    return rid


def drain_replies(cfg: Config, t: Transport, *, now: datetime | None = None) -> int:
    """Send each not-yet-drained line of our inbox file to the phone, tracked by a
    byte-offset cursor (robust to truncation, bounded state). Returns how many sent."""
    start = load_cursor(cfg.our_inbox and cfg.reply_cursor_file)
    lines, offset = read_new_lines(cfg.our_inbox, cfg.reply_cursor_file)
    if start > offset:  # the reader reset the cursor (file shrank); follow it
        start = 0

    n = 0
    consumed = start
    for raw in lines:
        # Advance PER LINE, not once per batch. Saving only at the end meant a
        # failure on reply three re-sent replies one and two on the next cycle —
        # and again on every retry after that. Per-line, a crash costs at most one
        # duplicate: the line that was in flight (FMA-1).
        consumed += len((raw + "\n").encode("utf-8"))
        line = raw.strip()
        if line and not line.startswith("#"):
            send_reply(cfg, t, line, now=now)
            n += 1
        save_cursor(cfg.reply_cursor_file, consumed)
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


def _another_poller_is_running(cfg: Config) -> int | None:
    """The pid of a LIVE poller holding this mailbox, or None.

    A stale pidfile from a crashed run must not lock the user out, so the pid is
    probed rather than trusted.
    """
    from .status import _alive  # local import: status is a consumer of config only

    pidfile = cfg.state_dir / "poller.pid"
    if not pidfile.exists():
        return None
    try:
        pid = int(pidfile.read_text(encoding="utf-8").strip())
    except ValueError:
        return None
    return pid if _alive(pid) else None


def run(
    cfg: Config,
    t: Transport,
    *,
    once: bool = False,
    interval: int | None = None,
    max_backoff: int = 300,
    max_attempts: int = 5,
    backoff_base: int = 1,
    force: bool = False,
    stale_after: int = 3600,
) -> int:
    """Announce join, loop poll+drain each interval, eject on stop.

    Failures are classified rather than uniformly retried, because they need
    opposite responses:

    * **auth** — no retry can enter a 2FA code, so retrying is only a quieter way
      to fail. Stop immediately and say what to run (RUN-1).
    * **transient** — back off, but BOUNDED. A 503 that never clears used to be
      retried for ever: silent death dressed up as patience (FMA-12).
    * **anything else** — probably a bug. A few attempts, then stop and surface it
      rather than looping on it for ever (RUN-2).

    `--once` does a single attempt — 0 if anything new, 1 if nothing, 2 on error.
    """
    log = _log.get()
    period = interval if interval is not None else cfg.poll_interval

    # RUN-9: two pollers on one account is how the throttle storms start, and the
    # second one is nearly always an accident.
    if not force and (other := _another_poller_is_running(cfg)) is not None:
        print(
            f"error: a poller is already running (pid {other}). "
            "Stop it first, or pass --force if you are sure."
        )
        return 2

    pidfile = cfg.state_dir / "poller.pid"
    pidfile.parent.mkdir(parents=True, exist_ok=True)
    pidfile.write_text(str(os.getpid()), encoding="utf-8")
    announce_join(cfg)

    backoff = max(backoff_base, 0)
    attempts = 0
    try:
        # One handler around the WHOLE loop: Ctrl-C can land in either sleep, in
        # connect, or mid-cycle, and a handler that covers only some of those is
        # the reason this escaped once already. Ctrl-C is how a person stops a
        # foreground daemon — deliberate, not a failure — and it is a
        # BaseException, so `except Exception` never saw it and it surfaced as a
        # traceback that said something broke when nothing had. The eject in
        # `finally` was never at risk; only the reporting was (LIVE-6).
        try:
            while True:
                try:
                    t.connect()
                    polled, drained = run_once(cfg, t)
                    # Report the one thing a working bridge cannot otherwise tell you:
                    # the messages are being delivered and nothing is reading them.
                    if (hint := _staleness_hint(cfg, stale_after=stale_after)) is not None:
                        log.warning("%s", hint)
                    attempts = 0  # a good cycle clears the budget, so unrelated
                    backoff = max(backoff_base, 0)  # blips never accumulate to a stop
                    if once:
                        return 0 if (polled or drained) else 1
                except Exception as exc:
                    if _is_auth(exc):
                        print(f"error: {exc}")
                        print("The session needs attention — run `voice-bridge icloud-login`.")
                        return 2

                    attempts += 1
                    kind = "transient" if is_transient(exc) else "unexpected"
                    if attempts >= max_attempts or once:
                        if once:
                            log.error("run --once failed: %s", exc)
                            print(f"error: {exc}")
                            return 2
                        print(
                            f"error: giving up after {attempts} {kind} failures — {exc}\n"
                            "       check connectivity, or whether a second poller is running."
                        )
                        return 2

                    log.warning("%s error: %s — retrying in %ss", kind, exc, backoff)
                    if backoff:
                        time.sleep(backoff)
                    backoff = min(max(backoff * 2, 1), max_backoff)
                    continue
                time.sleep(period)
        except KeyboardInterrupt:
            print("stopped.")
            return 0
    finally:
        announce_eject(cfg)
        pidfile.unlink(missing_ok=True)
