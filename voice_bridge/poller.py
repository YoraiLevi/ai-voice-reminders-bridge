"""The transport-agnostic orchestration loop - the shared heart both legacy bridges
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
from . import progress
from .config import Config
from .errors import _is_auth, is_transient
from .status import staleness_hint as _staleness_hint
from .mailbox import (
    append_line,
    clip,
    compact_seen,
    eject_line,
    first_url,
    format_mailbox_line,
    strip_astral,
    strip_mailbox_prefix,
    frontload_link,
    join_line,
    load_seen,
    mark_seen,
    read_new_entries,
    save_cursor,
    undrained,
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
            # Already delivered, yet still live on the backend - so a previous
            # completion failed. Retry it rather than leaving the reminder
            # visible: the user reads "still there" as "not processed", re-dictates,
            # and the agent receives the message twice (FMA-2). This is the
            # `seen ∩ live` retry set, and it costs one call per stuck item.
            t.complete(inbox, item.id)
            continue

        text = f"{item.title} - {item.notes}" if item.notes else item.title
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
    """Frame one reply - stamp `[HH:MM][spoke]`, front-load any URL, clip the title -
    then write it to the phone's output list and (notify) fire a banner. Returns the
    created item id. The framing is shared; the write crosses the seam into `t.add_todo`."""
    url = first_url(text)
    body = frontload_link(text.strip(), url)
    stamped = f"[{_hhmm(now)}][{cfg.spoke_name}] {body}"
    flat = " ".join(stamped.split())
    # The TITLE may be stripped of emoji; `stamped` - which carries the original
    # text - is what goes into the notes and the banner, so nothing is lost.
    titled = strip_astral(flat) if cfg.emoji_titles == "strip" else flat
    summary = clip(titled, cfg.reply_summary_limit, ellipsis=False) or "(reply)"
    out = t.resolve_list(cfg.output_list, cfg.output_list_id)
    rid = t.add_todo(out, summary, notes=stamped, needs_input=needs_input)
    if notify:
        ntfy.schedule(cfg, stamped, click=url)
    return rid


#: A "burst" is this many replies pending in ONE cycle. Below it, nothing changes:
#: a lone reply keeps its exact previous shape, because bundling a single message
#: only buys it a worse title.
DIGEST_MIN = 2


def send_digest(
    cfg: Config,
    t: Transport,
    texts: list[str],
    *,
    notify: bool = True,
    now: datetime | None = None,
) -> str:
    """Bundle a burst into ONE reminder and ONE banner.

    Three replies written in one cycle used to arrive as three reminders and three
    banners - three interruptions for one thought. The title is a count, so the
    content has to live in the notes: a digest that summarises the replies away has
    thrown the message out.

    Each line keeps its OWN `[HH:MM][spoke]` stamp. They are still separate
    messages that happen to be delivered together, and the phone-side rule that the
    newest supersedes older ones on a topic needs per-line times to work.
    """
    stamped = [f"[{_hhmm(now)}][{cfg.spoke_name}] {text.strip()}" for text in texts]
    body = "\n".join(stamped)
    title = f"{len(texts)} replies [{_hhmm(now)}]"
    out = t.resolve_list(cfg.output_list, cfg.output_list_id)
    rid = t.add_todo(out, title, notes=body, needs_input=True)
    if notify:
        # ONE banner for the whole digest, delayed once. The delay must not
        # multiply with the number of replies bundled into it.
        ntfy.schedule(cfg, body, click=first_url(body))
    return rid


def drain_replies(cfg: Config, t: Transport, *, now: datetime | None = None) -> int:
    """Send each not-yet-drained line of our inbox file to the phone, tracked by a
    byte-offset cursor (robust to truncation, bounded state). Returns how many sent.

    A burst of `DIGEST_MIN`+ lines is presented as one reminder (UX-2), but that is
    **presentation only**. The cursor still advances per line, and only after a
    successful send - the moment bundling becomes a durability batch, FMA-1 returns.
    """
    # Offsets come from the reader, which measures real bytes. Deriving them here
    # with len(line + "\n") assumed a ONE-byte terminator: on Windows the mailbox
    # is written through text mode, so the terminator is two, the cursor fell a
    # byte behind per line, and after three lines it landed inside the text of the
    # last one - re-sending its tail next cycle as its own reply.
    raw_entries, _ = read_new_entries(cfg.our_inbox, cfg.reply_cursor_file)

    # Blank lines and comments get an empty text: they are never sent, but they
    # MUST still advance the cursor or they are re-read every cycle for ever.
    entries: list[tuple[str, int]] = []
    for raw, off in raw_entries:
        line = raw.strip()
        # Drop the mailbox's own `- [HH:MM] (who)` framing before restamping, or
        # the phone shows two timestamps and two speakers (UX-4).
        text = strip_mailbox_prefix(line) if line and not line.startswith("#") else ""
        entries.append((text, off))

    sendable = [text for text, _ in entries if text]

    if len(sendable) >= DIGEST_MIN:
        # ONE send for the burst. If it raises, the cursor is untouched and the
        # whole burst is retried next cycle - replies that never arrived must never
        # be marked delivered. Only once it has succeeded do the offsets advance,
        # still one line at a time.
        send_digest(cfg, t, sendable, now=now)
        for _, off in entries:
            save_cursor(cfg.reply_cursor_file, off, path=cfg.our_inbox)
        return len(sendable)

    for text, off in entries:
        # Advance PER LINE, not once per batch. Saving only at the end meant a
        # failure on reply three re-sent replies one and two on the next cycle -
        # and again on every retry after that. Per-line, a crash costs at most one
        # duplicate: the line that was in flight (FMA-1).
        if text:
            send_reply(cfg, t, text, now=now)
        save_cursor(cfg.reply_cursor_file, off, path=cfg.our_inbox)
    return len(sendable)


def announce_join(cfg: Config, *, now: datetime | None = None) -> None:
    """Write the spoke's join line into the peer's inbox (protocol liveness)."""
    append_line(cfg.peer_inbox, join_line(cfg.from_name, now=now))


def announce_eject(cfg: Config, *, now: datetime | None = None, show=print) -> None:
    """Write the eject line and, IF NOTHING IS LEFT IN IT, delete our own inbox file.

    The protocol's clean-exit convention deletes `our_inbox`, and a missed cleanup is
    harmless under the keystone rule - which is exactly the asymmetry this fix rests
    on. Keeping the file costs the peer nothing; deleting it costs whatever was in it.

    THE UNCONDITIONAL DELETE WAS SILENT DATA LOSS ON THE ROUTINE PATH. Observed live:
    replies had been queued since early afternoon, the bridge was started at 18:58 and
    Ctrl-C'd within seconds, and the eject removed the file BEFORE the first cycle had
    drained it. The file was gone, the replies were never delivered, and nothing said
    so. Batch 13 fixed this convention's other half - a stale cursor into a deleted
    file - and this is its sibling: the deletion itself assumed a drain that had not
    happened.

    So the invariant is now stated rather than assumed: **never delete content this
    run did not read.** When anything is left, the file AND its cursor both stay -
    together, because the cursor is what stops the next run re-sending what this one
    already delivered, and batch 13's signature makes it safe to trust across the gap.

    Deliberately NOT a final drain before ejecting. That was the other candidate fix,
    and it is worse here: it puts a network call in the shutdown path, at the moment
    the user has said stop, where it can hang on a dead network, cannot succeed at all
    after an auth failure, and - if a second Ctrl-C lands inside it - raises out of
    this `finally` and skips the eject line entirely. It would trade a silent loss for
    a broken exit. Once the file survives, the delay it saves is one restart, and the
    next run delivers correctly.
    """
    append_line(cfg.peer_inbox, eject_line(cfg.from_name, now=now))

    held, replies = undrained(cfg.our_inbox, cfg.reply_cursor_file)
    if held:
        # SAY IT. A file quietly left behind reads as a failed cleanup, and the whole
        # reason this was worth a P1 is that the loss happened without a word.
        if replies:
            show(
                f"kept {replies} undelivered {'reply' if replies == 1 else 'replies'} "
                f"in {cfg.our_inbox} - the next run delivers them."
            )
        else:
            show(f"kept {cfg.our_inbox} - {held} bytes in it were never read.")
        return

    if cfg.our_inbox.exists():
        cfg.our_inbox.unlink()
    # A cursor into a file we just deleted counts bytes that no longer exist. Leaving
    # it behind is what turned this convention into silent data loss: the peer replies
    # while we are down, the file comes back shorter or differently, and the next run
    # reads from an offset measured against a file that is gone. Observed delivering
    # `[vox] ger) LINE-THREE` with two replies missing entirely.
    cfg.reply_cursor_file.unlink(missing_ok=True)


def run_once(cfg: Config, t: Transport, *, now: datetime | None = None) -> tuple[int, int]:
    """One poll + drain pass. Returns (polled, drained)."""
    return poll_inbox(cfg, t, now=now), drain_replies(cfg, t, now=now)


def dry_run(cfg: Config) -> int:
    """Print the resolved config and the exact mailbox line a dictation would become -
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


def preflight_lists(cfg: Config, *, show=print) -> int:
    """0 if both roles have a list selected; 2 with instructions otherwise.

    Deliberately config-only: it contacts no backend, so it costs nothing and
    cannot itself fail. "Nothing selected" is knowable without asking anyone, and
    it is checked BEFORE the mailbox is claimed - starting, announcing a join and
    then dying every cycle is worse than never starting, because the peer sees a
    spoke that is present and silent.

    This is where the last of the name-matching behaviour dies: an unselected role
    used to fall back to matching a title at runtime, which is exactly the silent
    guess the whole feature removed.

    A selection that points at a DELETED list is not detectable here - that needs
    the backend - so the run loop catches it as a LookupError and stops with the
    same advice.
    """
    missing = [
        (where, field)
        for where, field, value in (
            ("dictations", "inbox_list_id", cfg.inbox_list_id),
            ("replies", "output_list_id", cfg.output_list_id),
        )
        if not value
    ]
    if not missing:
        return 0

    for where, field in missing:
        show(f"error: no list is selected for {where} ({field} is empty).")
    show("")
    show("Nothing can be delivered until each role points at a real list. Fix it with:")
    show("  voice-bridge setup           (guided: create the lists, then choose them)")
    show("  voice-bridge lists --select  (choose from the lists you already have)")
    return 2


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

    * **auth** - no retry can enter a 2FA code, so retrying is only a quieter way
      to fail. Stop immediately and say what to run (RUN-1).
    * **transient** - back off, but BOUNDED. A 503 that never clears used to be
      retried for ever: silent death dressed up as patience (FMA-12).
    * **anything else** - probably a bug. A few attempts, then stop and surface it
      rather than looping on it for ever (RUN-2).

    `--once` does a single attempt - 0 if anything new, 1 if nothing, 2 on error.
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

    # Cheap, backend-free, and BEFORE the mailbox is claimed.
    if preflight_lists(cfg) != 0:
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
        # foreground daemon - deliberate, not a failure - and it is a
        # BaseException, so `except Exception` never saw it and it surfaced as a
        # traceback that said something broke when nothing had. The eject in
        # `finally` was never at risk; only the reporting was (LIVE-6).
        try:
            # PROGRESS OFF for the loop. Interactive commands announce every wait
            # because a person is watching one; the poller makes the same calls
            # every ten seconds for hours, and narrating them would bury the events
            # that matter under the ones that do not. The orientation block is this
            # path's feedback, and a failure still logs itself with its class.
            with progress.suspended():
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
                            print("The session needs attention - run `voice-bridge icloud-login`.")
                            return 2

                        if isinstance(exc, LookupError):
                            # A selected list stopped existing - deleted on the phone,
                            # most likely, mid-run. Retrying cannot bring it back, and
                            # the generic give-up message blames connectivity, sending
                            # the user to look at their network for a list they deleted.
                            print(f"error: a selected list is gone - {exc}")
                            print("       Nothing can be delivered until you choose another:")
                            print("         voice-bridge lists --select")
                            return 2

                        # FORGET THE REMEMBERED LISTS before retrying. Ids are cached
                        # so a healthy cycle costs no inventory download, but that means
                        # a list deleted mid-run no longer fails at `resolve_list` - it
                        # fails at the operation, wearing whatever the backend calls it,
                        # and could be retried forever against something that is never
                        # coming back. One invalidation here turns the next cycle's
                        # resolve back into an honest LookupError, which the branch
                        # above answers with "choose another list".
                        t.invalidate_lists()

                        attempts += 1
                        kind = "transient" if is_transient(exc) else "unexpected"
                        if attempts >= max_attempts or once:
                            if once:
                                log.error("run --once failed: %s", exc)
                                print(f"error: {exc}")
                                return 2
                            # Only a TRANSIENT failure earns the connectivity advice.
                            # Saying it after a permissions error or a bug sends the
                            # user to inspect a network that was never the problem.
                            hint = (
                                "       check connectivity, or whether a second poller is running."
                                if kind == "transient"
                                else "       this is not a connectivity problem - read the error above."
                            )
                            print(
                                f"error: giving up after {attempts} {kind} failures - {exc}\n{hint}"
                            )
                            return 2

                        log.warning("%s error: %s - retrying in %ss", kind, exc, backoff)
                        if backoff:
                            time.sleep(backoff)
                        backoff = min(max(backoff * 2, 1), max_backoff)
                        continue
                    time.sleep(period)
        except KeyboardInterrupt:
            print("stopped.")
            return 0
    finally:
        # Ring anything still waiting rather than waiting out its delay (which
        # would make Ctrl-C feel broken) or dropping it (which would lose a
        # banner the user was told to expect).
        ntfy.flush()
        announce_eject(cfg)
        pidfile.unlink(missing_ok=True)
