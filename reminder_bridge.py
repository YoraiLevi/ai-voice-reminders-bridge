# /// script
# requires-python = ">=3.11"
# dependencies = ["caldav>=3.2,<4", "icalendar>=6,<8"]
# ///
"""voice-bridge, CalDAV edition (ALT transport): CalDAV Reminders <-> the
manager's file mailbox. Config-driven (see config.py). The PRIMARY transport is
`pyicloud_bridge.py`; use this one with a self-hosted Radicale server (see
radicale/OWNER-SETUP.md) when the CloudKit private-API path is unavailable.

ASYNC channel: this is delayed, turn-based message-passing, NOT a live call. A
reply can reach the owner a full turn (or more) later, so every reply is stamped
with local `[HH:MM]` and the newest message on a topic supersedes older ones.

Every ~interval seconds it:
  1. reads incomplete VTODOs from the inbox list over CalDAV;
  2. dedupes by VTODO UID against a per-project seen-file;
  3. for each NEW item: marks it completed (double idempotency, like
     reminder-watch) AND appends ONE line to the manager's mailbox in that file's
     grammar:  - [HH:MM] (<from_name>) <title — notes>

Reply path (manager -> phone): `send_reply(text)` creates a new VTODO in the
output list with priority 1 ("needs input"). The loop also DRAINs a reply file
(the config's to_phone) — each new line becomes an Output VTODO.

Exit-code contract:
  --once : exit 1 = nothing new,  exit 0 = new item(s) appended.
  loop / --reply / --dry-run / --show-config : 0 on success, non-zero on error.

Never prints or logs the app-specific password (see _caldav.Creds.masked()).

Usage:
  uv run reminder_bridge.py                 # poll forever (config poll_interval)
  uv run reminder_bridge.py --once
  uv run reminder_bridge.py --interval 30
  uv run reminder_bridge.py --reply "text"  # one output-list VTODO
  uv run reminder_bridge.py --dry-run       # no network; show the exact line + config
  uv run reminder_bridge.py --config PATH
"""

from __future__ import annotations

import argparse
import sys
import time
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from icalendar import Alarm, Calendar, Todo

from _caldav import CredsError, connect, find_list, load_creds, todo_fields
from config import Config, ConfigError, load_config

# Every reply also fires a native iOS Reminders alarm (owner decision 2026-07-11 "option B":
# alarm on EVERY reply, not just urgent ones — he uses Reminders solely for this channel).
# The trigger is a small offset in the FUTURE so the alarm is still pending when the phone
# next syncs the CalDAV item (a due time already in the past may not fire). ntfy stays on too.
ALARM_LEAD_SECONDS = 60
_URL_RE = re.compile(r"https?://\S+")


def _incomplete_todos(cal):
    """Yield only the not-completed VTODOs from a list.

    iCloud returns 500 on caldav's server-side "exclude completed" filter, so read
    everything the iCloud-safe way (`objects()`) and drop completed client-side.

    Loads each object INDIVIDUALLY and SKIPS any that error on load. A Radicale
    collection can retain a DANGLING index entry pointing at a deleted resource;
    loading it 404s, and a bulk `load_objects=True` would then fail the WHOLE poll
    on that one bad item — silently blocking EVERY new message. Per-item load +
    skip keeps the poll alive despite an orphaned entry.
    """
    for todo in cal.objects(load_objects=False):
        try:
            comp = todo.icalendar_component  # lazy-loads here; 404s on a dangling entry
        except Exception as exc:
            print(f"  skip un-loadable item ({type(exc).__name__}): {getattr(todo, 'url', '?')}",
                  file=sys.stderr)
            continue
        status = str(comp.get("status") or "").upper()
        if status == "COMPLETED" or comp.get("completed") is not None:
            continue
        yield todo


def _mark_complete(todo) -> None:
    """Mark a VTODO completed by editing its component and saving (object-type
    agnostic, iCloud-safe — we set STATUS/COMPLETED/PERCENT ourselves and PUT)."""
    comp = todo.icalendar_component
    comp["status"] = "COMPLETED"
    comp["percent-complete"] = 100
    if "completed" not in comp:
        comp.add("completed", datetime.now(timezone.utc))
    todo.save()


# --- pure helpers (no network) ---------------------------------------------

def format_mailbox_line(
    title: str | None, notes: str | None, *, from_name: str, now: datetime | None = None
) -> str:
    """Render one to-manager.md line: `- [HH:MM] (<from_name>) <message>`, ONE line."""
    now = now or datetime.now()
    hhmm = now.strftime("%H:%M")
    title = (title or "(no title)").strip()
    body = (notes or "").replace("\r", " ").replace("\n", " ").strip()
    msg = f"{title} — {body}" if body else title
    return f"- [{hhmm}] ({from_name}) {msg}"


def _load_seen(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {ln.strip() for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()}


def _append_seen(path: Path, uid: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(uid + "\n")


def _append_line(path: Path, line: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


# --- network side ----------------------------------------------------------

def _principal(cfg: Config):
    return connect(load_creds(cfg))


def _notify_push(cfg: Config, text: str, *, click: str | None = None) -> None:
    """Best-effort ntfy push so the phone gets a banner on a reply — the CalDAV path
    has no push of its own. Topic from cfg.ntfy_topic_file; no-op if absent; never raises.
    Body kept short + word-boundary (iOS banners clip ~150 chars mid-word).
    `click` (a URL) sets the ntfy Click header so the banner opens the link in ONE tap
    (this is the ntfy-for-links path the owner keeps alongside native alarms)."""
    try:
        topic_file = cfg.ntfy_topic_file
        if not topic_file or not topic_file.exists():
            return
        topic = topic_file.read_text(encoding="utf-8").strip()
        if not topic:
            return
        import urllib.request

        body = " ".join(text.split())
        if len(body) > 150:
            body = body[:149].rsplit(" ", 1)[0].rstrip() + "…"
        headers = {"Title": f"Claude Code · {cfg.name}", "Tags": "robot", "Priority": "high"}
        if click:
            headers["Click"] = click
        req = urllib.request.Request(
            f"https://ntfy.sh/{topic}",
            data=body.encode("utf-8"),
            headers=headers,
            method="POST",
        )
        urllib.request.urlopen(req, timeout=8)
    except Exception:
        pass  # notification is best-effort; never break the reply on it


def _first_url(text: str) -> str | None:
    m = _URL_RE.search(text)
    return m.group(0).rstrip(").,;]") if m else None


def _frontload_link(body: str, url: str | None) -> str:
    """Put the link at the VERY START of the reminder body so it's the first tappable
    thing in the iOS Reminders notes (owner preference). No-op if no url / already first."""
    if not url or body.lstrip().startswith(url):
        return body
    return f"{url}\n\n{body}"


def _alarmed_todo_ics(summary: str, description: str, needs_input: bool) -> str:
    """Build a VTODO carrying a DUE time + an absolute DISPLAY VALARM, so iOS Reminders
    fires a NATIVE banner+sound. Times are timezone-aware UTC (avoids the naive-local
    stored-as-UTC bug); the trigger is ALARM_LEAD_SECONDS in the future."""
    now = datetime.now(timezone.utc)
    due = now + timedelta(seconds=ALARM_LEAD_SECONDS)
    todo = Todo()
    todo.add("uid", str(uuid.uuid4()))
    todo.add("dtstamp", now)
    todo.add("summary", summary)
    todo.add("description", description)
    todo.add("due", due)
    todo.add("status", "NEEDS-ACTION")
    if needs_input:
        todo.add("priority", 1)
    alarm = Alarm()
    alarm.add("action", "DISPLAY")
    alarm.add("description", summary)
    alarm.add("trigger", due)  # absolute trigger at the due time
    todo.add_component(alarm)
    cal = Calendar()
    cal.add("prodid", "-//voice-bridge//reply-alarm//EN")
    cal.add("version", "2.0")
    cal.add_component(todo)
    return cal.to_ical().decode("utf-8")


def send_reply(
    cfg: Config,
    text: str,
    *,
    needs_input: bool = True,
    notify: bool = True,
    native_alarm: bool = True,
    principal=None,
) -> str:
    """Write one reply as a new VTODO in the output list. priority=1 marks "needs
    input". Returns the created UID. Raises CredsError/RuntimeError on failure.

    native_alarm=True (the default, per the owner's "option B") attaches a DUE time +
    a native iOS Reminders VALARM so every reply also fires a banner+sound — a reliable
    backstop to ntfy. Any link in the text is moved to the very start of the reminder
    body (two-tap open) AND set as the ntfy Click header (one-tap open)."""
    principal = principal or _principal(cfg)
    out = find_list(principal, cfg.output_list)
    if out is None:
        raise RuntimeError(
            f"{cfg.output_list!r} list is not visible over CalDAV. Create it on the "
            "iPhone (or in Radicale) — see radicale/OWNER-SETUP.md."
        )
    # Stamp every reply with local time — the channel is ASYNC/turn-based, so a
    # timestamp lets the owner + voice assistant detect stale/superseded messages.
    # Prefix every reply with time AND project name so multiple projects are distinguishable.
    stamp = f"[{datetime.now():%H:%M}][{cfg.name}] "
    url = _first_url(text)
    body = _frontload_link(text.strip(), url)
    stamped = stamp + body
    summary = stamped.replace("\r", " ").replace("\n", " ").strip()[:120] or "(reply)"
    if native_alarm:
        todo = out.save_todo(ical=_alarmed_todo_ics(summary, stamped, needs_input))
    else:
        todo = out.save_todo(summary=summary, description=stamped, priority=1 if needs_input else None)
    if notify:
        _notify_push(cfg, summary, click=url)
    f = todo_fields(todo)
    return f["uid"] or "(unknown-uid)"


def poll_inbox(cfg: Config, *, principal=None, seen_path: Path | None = None, mailbox: Path | None = None) -> int:
    """One poll cycle. Returns the count of NEW items bridged this cycle.

    Per new VTODO: append its UID to the seen-file, mark it completed, and append
    one line to the mailbox. seen-file first, so a crash after marking-complete
    can't re-emit; mark-complete is the second idempotency guard."""
    seen_path = seen_path or cfg.seen_file
    mailbox = mailbox or cfg.to_manager
    principal = principal or _principal(cfg)
    inbox = find_list(principal, cfg.inbox_list)
    if inbox is None:
        raise RuntimeError(
            f"{cfg.inbox_list!r} list is not visible over CalDAV. Run probe.py — if it "
            "reports CLOUDKIT-INVISIBLE, switch to the Radicale fallback (radicale/OWNER-SETUP.md)."
        )

    seen = _load_seen(seen_path)
    new_count = 0
    for todo in _incomplete_todos(inbox):
        f = todo_fields(todo)
        uid = f["uid"]
        if not uid or uid in seen:
            continue
        _append_seen(seen_path, uid)
        seen.add(uid)
        try:
            _mark_complete(todo)
        except Exception as exc:
            print(f"  warn: could not mark {uid} complete: {exc}", file=sys.stderr)
        line = format_mailbox_line(f["title"], f["notes"], from_name=cfg.from_name)
        _append_line(mailbox, line)
        print(f"  bridged -> {line}")
        new_count += 1
    return new_count


def drain_replies(cfg: Config, *, principal=None) -> int:
    """Turn each NEW line in the to-phone file into an output-list VTODO. Dedupe by
    line-number so identical reply texts aren't collapsed. No-op if absent."""
    reply_file = cfg.to_phone
    seen_path = cfg.reply_seen_file
    if not reply_file.exists():
        return 0
    lines = reply_file.read_text(encoding="utf-8").splitlines()
    seen = _load_seen(seen_path)
    principal = principal or _principal(cfg)
    sent = 0
    for idx, raw in enumerate(lines):
        text = raw.strip()
        if not text or text.startswith("#"):
            continue
        marker = str(idx)
        if marker in seen:
            continue
        try:
            uid = send_reply(cfg, text, principal=principal)
        except Exception as exc:
            print(f"  warn: reply line {idx} not sent: {exc}", file=sys.stderr)
            continue
        _append_seen(seen_path, marker)
        seen.add(marker)
        print(f"  reply -> output VTODO {uid}: {text[:60]}")
        sent += 1
    return sent


# --- dry-run (no network, no real mailbox) ---------------------------------

def dry_run(cfg: Config) -> int:
    """Feed a FAKE VTODO through parse -> mailbox-append and show the EXACT line.
    Writes to a temp file, not the real mailbox, and touches no network."""
    import tempfile

    cfg.print_resolved()

    class _FakeComp(dict):
        pass

    class _FakeTodo:
        def __init__(self, uid, summary, description):
            self.icalendar_component = _FakeComp(uid=uid, summary=summary, description=description)

    fake = _FakeTodo(
        uid="FAKE-UID-1234@voice-bridge",
        summary="check the aorus fan curve",
        description="it's been loud since the last BIOS update — look at PWM min",
    )
    f = todo_fields(fake)
    line = format_mailbox_line(f["title"], f["notes"], from_name=cfg.from_name)

    tmp = Path(tempfile.gettempdir()) / "voice-bridge-dryrun-to-manager.md"
    _append_line(tmp, line)

    print("\n== DRY RUN (no network, no real mailbox write) ==")
    print(f"  inbox list       : {cfg.inbox_list!r}")
    print(f"  fake VTODO uid   : {f['uid']}")
    print("\n  EXACT line that WOULD be appended to the mailbox:")
    print(f"    {line}")
    print(f"\n  (written to temp file for inspection: {tmp})")
    print(f"  (real mailbox would be: {cfg.to_manager})")
    return 0


# --- CLI -------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", metavar="PATH", help="explicit voice-bridge.json")
    ap.add_argument("--show-config", action="store_true", help="print the resolved config and exit")
    ap.add_argument("--once", action="store_true", help="single poll; exit 1=nothing new, 0=new items")
    ap.add_argument("--interval", type=int, default=None, help="loop cadence in seconds (default: config poll_interval)")
    ap.add_argument("--reply", metavar="TEXT", help="send one output-list VTODO and exit")
    ap.add_argument("--no-replies", action="store_true", help="do NOT drain the reply file in the loop")
    ap.add_argument("--dry-run", action="store_true", help="no network; show resolved config + the exact mailbox line")
    args = ap.parse_args(argv)

    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    if args.show_config:
        cfg.print_resolved()
        return 0

    if args.dry_run:
        return dry_run(cfg)

    if args.reply is not None:
        try:
            uid = send_reply(cfg, args.reply)
        except (CredsError, RuntimeError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(f"sent output-list VTODO {uid}")
        return 0

    if args.once:
        try:
            principal = _principal(cfg)
            n = poll_inbox(cfg, principal=principal)
            if not args.no_replies:
                drain_replies(cfg, principal=principal)
        except (CredsError, RuntimeError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        return 0 if n > 0 else 1

    # continuous loop
    interval = args.interval if args.interval is not None else cfg.poll_interval
    try:
        creds = load_creds(cfg)
    except CredsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"[{cfg.name}] CalDAV voice-bridge polling every {interval}s ({creds.masked()}); Ctrl-C to stop")
    while True:
        try:
            principal = connect(creds)
            n = poll_inbox(cfg, principal=principal)
            if not args.no_replies:
                drain_replies(cfg, principal=principal)
            if n:
                print(f"  [{datetime.now():%H:%M:%S}] {n} new item(s) bridged")
        except CredsError as exc:
            print(f"  auth error (will retry): {exc}", file=sys.stderr)
        except Exception as exc:  # keep the loop alive across transient network faults
            print(f"  poll error (will retry): {exc}", file=sys.stderr)
        try:
            time.sleep(interval)
        except KeyboardInterrupt:
            print("\nstopping.")
            return 0


if __name__ == "__main__":
    sys.exit(main())
