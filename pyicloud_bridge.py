# /// script
# requires-python = ">=3.11"
# dependencies = ["pyicloud"]
# ///
"""voice-bridge, pyicloud edition: the owner's REAL iCloud Reminders <-> the
manager's file mailbox. Config-driven (see config.py) so ONE install serves MANY
projects — list names, mailbox, creds and topic all come from a per-project
`.claude/voice-bridge.json` instead of being hard-coded.

Parallel path to `reminder_bridge.py` (the CalDAV/Radicale alt transport). Where
that one talks CalDAV VTODOs, this one talks to the modern CloudKit Reminders
store that the iPhone + Claude app actually use, via Apple's private web API
(pyicloud). CalDAV cannot see that store; pyicloud can read AND write it. Same
CONTRACT, different transport — pick this when the reminders come from the
phone's own Reminders app / Claude iOS app.

Every ~interval seconds it:
  1. reads INCOMPLETE reminders from the inbox list (real CloudKit);
  2. dedupes by reminder .id against a per-project seen-file;
  3. for each NEW item: appends ONE line to the manager's mailbox
     `<mailbox_dir>/<to_manager>` in that file's grammar:
         - [HH:MM] (<from_name>) <title — desc>
     the manager Monitors that file, so a new line wakes it; then marks the
     source reminder COMPLETE (second idempotency guard, mirrors reminder-watch).

Reply path (manager -> phone): `send_reply(text)` / `--reply "text"` creates a
new reminder in the output list with priority 1 ("needs input"), which the owner
reads in the iPhone Reminders app. The warm loop also drains the to-phone file.

Exit-code contract:
  --once : exit 1 = nothing new,  exit 0 = new item(s) appended.
  loop / --reply / --selftest / --dry-run / --show-config : 0 ok, non-zero error.

Auth: a pyicloud session already TRUSTED + cached under the config's cookie_dir
(seeded once by pyicloud_login.py). No 2FA at runtime. Credentials are read from
the config's creds_env file (ICLOUD_APPLE_ID + ICLOUD_PASSWORD), NEVER printed.

Usage:
  uv run pyicloud_bridge.py                 # poll forever (config poll_interval)
  uv run pyicloud_bridge.py --once          # single poll, exit-code contract
  uv run pyicloud_bridge.py --interval 30   # custom cadence (seconds)
  uv run pyicloud_bridge.py --reply "text"  # write one output-list reminder
  uv run pyicloud_bridge.py --selftest      # live end-to-end proof, temp mailbox
  uv run pyicloud_bridge.py --dry-run       # NO network; show the exact line + config
  uv run pyicloud_bridge.py --show-config   # print the resolved config and exit
  uv run pyicloud_bridge.py --config path/to/voice-bridge.json  # explicit config
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from config import Config, ConfigError, load_config

# Native iOS Reminders alarm on every reply (option-B parity with the CalDAV path):
# a timed due_date this far in the FUTURE makes iOS Reminders fire a banner+sound.
# Small lead so it's still pending when the phone syncs the new reminder.
ALARM_LEAD_SECONDS = 60
_URL_RE = re.compile(r"https?://\S+")


def _first_url(text: str) -> str | None:
    m = _URL_RE.search(text)
    return m.group(0).rstrip(").,;]") if m else None


def _frontload_link(body: str, url: str | None) -> str:
    """Put the link at the very START of the reminder body (first tappable thing)."""
    if not url or body.lstrip().startswith(url):
        return body
    return f"{url}\n\n{body}"


class BridgeError(RuntimeError):
    """Auth/credential/list problem stated in a log-safe way."""


# --- credentials (never printed) -------------------------------------------

def _read_kv(path: Path, key: str) -> str | None:
    if not path.exists():
        return None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith(key + "="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def _masked(apple_id: str) -> str:
    local, _, domain = apple_id.partition("@")
    who = f"{local[:1]}***@{domain}" if domain else "***"
    return f"apple_id={who} (pyicloud cached session)"


# --- pure helper (no network) ----------------------------------------------

def format_mailbox_line(
    title: str | None, notes: str | None, *, from_name: str, now: datetime | None = None
) -> str:
    """Render one to-manager.md line from a reminder's title/desc.

    Grammar: `- [HH:MM] (<from_name>) <message>`, ONE physical line. Embedded
    newlines in the description are flattened so the line stays single.
    """
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

def connect(cfg: Config):
    """Return a primed pyicloud Reminders service.

    Uses the cached trusted session (no 2FA). Calls list(r.lists()) once, which
    is REQUIRED before list_reminders() or the service 400s. Raises BridgeError
    with a log-safe message on any auth/priming failure.
    """
    apple_id = _read_kv(cfg.creds_env, "ICLOUD_APPLE_ID")
    password = _read_kv(cfg.creds_env, "ICLOUD_PASSWORD")
    if not apple_id or not password:
        raise BridgeError(
            f"Missing ICLOUD_APPLE_ID / ICLOUD_PASSWORD in {cfg.creds_env}. "
            "The pyicloud edition needs the MAIN Apple ID password (not an "
            "app-specific one) plus a trusted session seeded by pyicloud_login.py."
        )
    try:
        from pyicloud import PyiCloudService
    except ImportError as exc:  # pragma: no cover
        raise BridgeError(f"pyicloud not installed: {exc}") from exc

    try:
        api = PyiCloudService(apple_id, password, cookie_directory=str(cfg.cookie_dir))
    except Exception as exc:
        raise BridgeError(f"pyicloud login failed: {type(exc).__name__}: {exc}") from exc

    if getattr(api, "requires_2fa", False):
        raise BridgeError(
            "pyicloud session needs 2FA — the cached trusted session under "
            f"{cfg.cookie_dir} has expired. Re-run pyicloud_login.py (owner supplies "
            "the 6-digit code) to re-trust, then retry."
        )
    r = api.reminders
    try:
        list(r.lists())  # REQUIRED: primes the service before list_reminders
    except Exception as exc:
        raise BridgeError(f"could not prime Reminders service: {type(exc).__name__}: {exc}") from exc
    return r


def _lists_by_title(r) -> dict:
    return {l.title: l for l in r.lists()}


def _pending_count(r, list_obj) -> int:
    """Number of INCOMPLETE reminders in a list. The live list the phone writes to has
    unread items; ghost/orphaned duplicates are empty — so this distinguishes them."""
    try:
        res = r.list_reminders(list_obj.id)
        items = res if isinstance(res, list) else list(getattr(res, "reminders", res) or [])

        def done(x):
            return bool(x.get("completed")) if isinstance(x, dict) else bool(getattr(x, "completed", False))

        return sum(1 for x in items if not done(x))
    except Exception:
        return 0


def _require_list(r, title: str, list_id: str = "", *, auto_heal: bool = False):
    """Return the list object.

    Resolution order:
    1. AUTO-HEAL (when auto_heal=True, used for the INBOX): if several lists share the
       title (ghosts left behind when the owner deletes+recreates a list on the phone,
       each getting a NEW CloudKit id), pick the one with UNREAD items — that's the live
       list the phone is actually writing to; ghosts are empty. This SELF-HEALS a
       recreated list without needing a manual re-pin.
    2. PINNED id (`list_id`, a 'List/UUID'): exact match, ignoring title — robust against
       same-titled ghosts. If the pinned id has vanished (list recreated), fall through.
    3. TITLE: first match (warn if duplicated and not auto-healing).
    """
    lists = list(r.lists())
    same = [lst for lst in lists if lst.title == title]

    if auto_heal and len(same) > 1:
        counts = [(lst, _pending_count(r, lst)) for lst in same]
        best, n = max(counts, key=lambda t: t[1])
        if n > 0:
            return best  # the list with unread items = the live one

    if list_id:
        for lst in lists:
            if lst.id == list_id:
                return lst
        # pinned id vanished (list recreated) -> fall through to title resolution

    if not same:
        raise BridgeError(
            f"{title!r} list is not visible via pyicloud. This pyicloud build "
            "exposes no create-list API (r has create() for reminders only), so "
            "the list must be created ONCE on the iPhone Reminders app (or Claude "
            "iOS). Create it, then retry."
        )
    if len(same) > 1 and not auto_heal:
        import sys as _sys
        print(
            f"WARNING: {len(same)} lists titled {title!r} (ghosts?); using the first. "
            "Pin its id via inbox_list_id/output_list_id, or rely on auto_heal.",
            file=_sys.stderr,
        )
    return same[0]


def _incomplete(r, list_obj) -> list:
    """The not-completed reminders in a list. list_reminders() already returns
    only incomplete items, but we filter defensively in case that changes."""
    data = dict(r.list_reminders(list_obj.id))
    return [rem for rem in data.get("reminders", []) if not rem.completed]


def _mark_complete(r, rem) -> None:
    """Mark a reminder completed. VERIFIED: setting .completed=True then
    r.update(rem) drops it off the incomplete list and r.get(id).completed==True."""
    rem.completed = True
    r.update(rem)


def _notify_push(cfg: Config, text: str, *, click: str | None = None) -> None:
    """Best-effort push to ntfy.sh so the phone gets a REAL, INSTANT banner.

    ntfy is Apple-independent, so it's the reliable/fast notification for the iCloud
    channel (send_reply also sets a timed due_date to try for a NATIVE Reminders
    alarm too, but whether the pyicloud API triggers one is unverified — ntfy is the
    guaranteed banner). Topic from ntfy_topic_file; no-op if absent; never raises.
    `click` (a URL) sets the ntfy Click header for one-tap link open.
    """
    try:
        topic_file = cfg.ntfy_topic_file
        if not topic_file.exists():
            return
        topic = topic_file.read_text(encoding="utf-8").strip()
        if not topic:
            return
        import urllib.request

        # iOS notification banners clip ~150 chars, and clip MID-WORD. Keep the body
        # short and complete (word-boundary), so nothing looks silently cut off; the
        # full reply text lives in the output-list reminder anyway.
        body = " ".join(text.split())
        if len(body) > 150:
            body = body[:149].rsplit(" ", 1)[0].rstrip() + "…"

        headers = {
            "Title": cfg.ntfy_title.replace("{name}", cfg.name),
            "Tags": cfg.ntfy_tags,
            "Priority": cfg.ntfy_priority,
        }
        if click:
            headers["Click"] = click
        req = urllib.request.Request(
            f"{cfg.ntfy_server}/{topic}",
            data=body.encode("utf-8"),
            headers=headers,
            method="POST",
        )
        urllib.request.urlopen(req, timeout=8)
    except Exception:
        pass  # notification is best-effort; never break the reply on it


def send_reply(cfg: Config, text: str, *, priority: int = 1, r=None, notify: bool = True) -> str:
    """Write one reply as a new reminder in the output list, and (notify=True) push
    a real banner to the phone via ntfy. priority=1 marks "needs input". Returns
    the created reminder id.
    """
    r = r or connect(cfg)
    out = _require_list(r, cfg.output_list, cfg.output_list_id)
    # Stamp every reply with local time. The bridge is ASYNC/turn-based, not live:
    # replies can reach the owner a turn or more later, so a timestamp lets both the
    # owner and the voice assistant detect stale/superseded messages.
    # Prefix every reply with time AND project name, so with several projects running
    # side by side it's immediately clear which one an update is from.
    stamp = f"[{datetime.now():%H:%M}][{cfg.name}] "
    url = _first_url(text)
    body = _frontload_link(text.strip(), url)
    stamped = stamp + body
    summary = stamped.replace("\r", " ").replace("\n", " ").strip()[:120] or "(reply)"
    # Native iOS Reminders alarm: a timed due_date ~1 min out fires a banner+sound on
    # the phone (option-B parity with the Radicale/CalDAV path). Use a NAIVE LOCAL
    # wall-clock time — pyicloud mangles a tz-aware datetime (a tz-aware local time
    # fired the alarm exactly one UTC-offset EARLY, e.g. 3h behind at UTC+3). Naive
    # local is sent as-is and iOS reads it as local wall-clock. (PITFALL: tz here.)
    due = datetime.now() + timedelta(seconds=ALARM_LEAD_SECONDS)
    created = r.create(out.id, summary, desc=stamped, priority=priority, due_date=due)
    if notify:
        _notify_push(cfg, summary, click=url)
    return created.id or "(unknown-id)"


def drain_replies(cfg: Config, *, r=None) -> int:
    """Send each NEW line in the to-phone file as an output-list reminder.

    Lets the manager reply with an instant local file-append (no cold start); the
    warm loop drains it here. Dedupe is by line index, so the file is append-only.
    """
    reply_file = cfg.to_phone
    seen_path = cfg.reply_seen_file
    if not reply_file.exists():
        return 0
    lines = reply_file.read_text(encoding="utf-8").splitlines()
    seen_path.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if seen_path.exists():
        done = {x.strip() for x in seen_path.read_text(encoding="utf-8").splitlines() if x.strip()}
    r = r or connect(cfg)
    sent = 0
    for i, line in enumerate(lines):
        text = line.strip()
        if not text or text.startswith("#") or str(i) in done:
            continue
        send_reply(cfg, text, r=r)
        with seen_path.open("a", encoding="utf-8") as f:
            f.write(f"{i}\n")
        done.add(str(i))
        sent += 1
    return sent


def poll_inbox(cfg: Config, *, r=None, seen_path: Path | None = None, mailbox: Path | None = None) -> int:
    """One poll cycle. Returns the count of NEW items bridged this cycle.

    Per new reminder, in order: record its id in the seen-file (dedupe survives
    any later failure), append one line to the mailbox, then mark it complete.
    The seen-file is the primary dedupe; completion is the second guard exactly
    as reminder-watch does it. seen_path/mailbox override the config (for tests).
    """
    seen_path = seen_path or cfg.seen_file
    mailbox = mailbox or cfg.to_manager
    r = r or connect(cfg)
    inbox = _require_list(r, cfg.inbox_list, cfg.inbox_list_id, auto_heal=True)

    seen = _load_seen(seen_path)
    new_count = 0
    for rem in _incomplete(r, inbox):
        rid = rem.id
        if not rid or rid in seen:
            continue
        # 1) record id first — a crash after this can't re-emit
        _append_seen(seen_path, rid)
        seen.add(rid)
        # 2) bridge into the mailbox
        line = format_mailbox_line(rem.title, rem.desc, from_name=cfg.from_name)
        _append_line(mailbox, line)
        print(f"  bridged -> {line}")
        # 3) mark complete (second idempotency guard, mirrors reminder-watch)
        try:
            _mark_complete(r, rem)
        except Exception as exc:
            print(f"  warn: could not mark {rid} complete: {exc}", file=sys.stderr)
        new_count += 1
    return new_count


# --- dry-run (no network, no real mailbox) ---------------------------------

def dry_run(cfg: Config) -> int:
    """Prove the module reads its config WITHOUT any network: print the resolved
    config, then feed a FAKE reminder through format -> append into a TEMP mailbox
    and show the exact line. Touches no real mailbox and no network."""
    import tempfile

    cfg.print_resolved()

    fake_title = "check the aorus fan curve"
    fake_desc = "it's been loud since the last BIOS update — look at PWM min"
    line = format_mailbox_line(fake_title, fake_desc, from_name=cfg.from_name)

    tmp = Path(tempfile.gettempdir()) / "voice-bridge-dryrun-to-manager.md"
    _append_line(tmp, line)

    print("\n== DRY RUN (no network, no real mailbox write) ==")
    print(f"  inbox list       : {cfg.inbox_list!r}")
    print(f"  output list      : {cfg.output_list!r}")
    print(f"  fake reminder    : {fake_title!r} / {fake_desc!r}")
    print("\n  EXACT line that WOULD be appended to the mailbox:")
    print(f"    {line}")
    print(f"\n  (written to temp file for inspection: {tmp})")
    print(f"  (real mailbox would be: {cfg.to_manager})")
    return 0


# --- selftest (live, but writes to a TEMP mailbox) -------------------------

def selftest(cfg: Config) -> int:
    """Live end-to-end proof against the real account, WITHOUT touching the real
    to-manager.md or the real seen-file:
      1. create a temp reminder in the inbox list;
      2. run one poll into a TEMP mailbox + TEMP seen-file;
      3. assert the mailbox line was written AND the source reminder is complete;
      4. clean up (delete the temp reminder).
    Exit 0 = all assertions passed; non-zero = a failure (details printed)."""
    import tempfile

    r = connect(cfg)
    inbox = _require_list(r, cfg.inbox_list, cfg.inbox_list_id, auto_heal=True)
    titles = set(_lists_by_title(r))
    print("== SELFTEST (live account, temp mailbox) ==")
    print(f"  lists visible: {sorted(titles)}")
    for name in (cfg.inbox_list, cfg.output_list):
        print(f"  contract list {name!r}: {'present' if name in titles else 'MISSING'}")

    tmpdir = Path(tempfile.mkdtemp(prefix="voice-bridge-selftest-"))
    tmp_mailbox = tmpdir / "to-manager.md"
    tmp_seen = tmpdir / "seen.txt"

    marker = f"SELFTEST {datetime.now():%Y%m%d-%H%M%S}"
    created = r.create(inbox.id, marker, desc="selftest body — delete me", priority=0)
    rid = created.id
    print(f"\n  created temp reminder: {rid}")
    print(f"    title={marker!r}")

    rc = 0
    try:
        n = poll_inbox(cfg, r=r, seen_path=tmp_seen, mailbox=tmp_mailbox)
        print(f"\n  poll_inbox bridged {n} new item(s)")

        written = tmp_mailbox.read_text(encoding="utf-8") if tmp_mailbox.exists() else ""
        line_ok = marker in written and f"({cfg.from_name})" in written
        print(f"  [assert] mailbox line written: {line_ok}")
        if written:
            for ln in written.splitlines():
                if marker in ln:
                    print(f"    -> {ln}")
        if not line_ok:
            print("  FAIL: expected mailbox line not found", file=sys.stderr)
            rc = 1

        fresh = r.get(rid)
        complete_ok = bool(getattr(fresh, "completed", False))
        print(f"  [assert] source reminder marked complete: {complete_ok}")
        if not complete_ok:
            print("  FAIL: source reminder still incomplete", file=sys.stderr)
            rc = 1

        seen_ok = rid in _load_seen(tmp_seen)
        print(f"  [assert] id recorded in seen-file: {seen_ok}")
        if not seen_ok:
            rc = 1
    finally:
        try:
            r.delete(r.get(rid))
            print(f"\n  cleanup: deleted temp reminder {rid}")
        except Exception as exc:
            print(f"  cleanup warn: could not delete {rid}: {exc}", file=sys.stderr)
        for p in (tmp_mailbox, tmp_seen):
            try:
                p.unlink(missing_ok=True)
            except Exception:
                pass
        try:
            tmpdir.rmdir()
        except Exception:
            pass

    print(f"\n  SELFTEST {'PASSED' if rc == 0 else 'FAILED'}")
    return rc


# --- CLI -------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--config", metavar="PATH", help="explicit voice-bridge.json (else $VOICE_BRIDGE_CONFIG or ./.claude/voice-bridge.json)")
    ap.add_argument("--show-config", action="store_true", help="print the resolved config and exit")
    ap.add_argument("--dry-run", action="store_true", help="no network; show resolved config + the exact mailbox line")
    ap.add_argument("--once", action="store_true", help="single poll; exit 1=nothing new, 0=new items")
    ap.add_argument("--interval", type=int, default=None, help="loop cadence in seconds (default: config poll_interval)")
    ap.add_argument("--reply", metavar="TEXT", help="create one output-list reminder and exit")
    ap.add_argument("--notify", metavar="TEXT", help="push ONE ntfy banner (topic from config's ntfy_topic_file) and exit")
    ap.add_argument("--click", metavar="URL", help="with --notify: URL the banner opens when tapped (ntfy Click header)")
    ap.add_argument("--selftest", action="store_true", help="live end-to-end proof into a temp mailbox")
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

    if args.selftest:
        try:
            return selftest(cfg)
        except BridgeError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2

    if args.reply is not None:
        try:
            rid = send_reply(cfg, args.reply)
        except BridgeError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(f"sent output-list reminder {rid}")
        return 0

    if args.notify is not None:
        if not cfg.ntfy_topic_file.exists():
            print(f"error: no ntfy topic at {cfg.ntfy_topic_file} (set ntfy_topic_file)", file=sys.stderr)
            return 2
        _notify_push(cfg, args.notify, click=args.click)
        print(f"pushed ntfy banner to topic in {cfg.ntfy_topic_file}")
        return 0

    if args.once:
        try:
            r = connect(cfg)
            n = poll_inbox(cfg, r=r)
        except BridgeError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        return 0 if n > 0 else 1

    # continuous loop
    interval = args.interval if args.interval is not None else cfg.poll_interval
    apple_id = _read_kv(cfg.creds_env, "ICLOUD_APPLE_ID") or ""
    print(f"[{cfg.name}] pyicloud voice-bridge polling every {interval}s ({_masked(apple_id)}); Ctrl-C to stop")
    while True:
        try:
            r = connect(cfg)
            n = poll_inbox(cfg, r=r)
            if n:
                print(f"  [{datetime.now():%H:%M:%S}] {n} new item(s) bridged")
            sent = drain_replies(cfg, r=r)
            if sent:
                print(f"  [{datetime.now():%H:%M:%S}] {sent} reply(ies) sent to {cfg.output_list}")
        except BridgeError as exc:
            print(f"  auth/list error (will retry): {exc}", file=sys.stderr)
        except Exception as exc:  # keep the loop alive across transient faults
            print(f"  poll error (will retry): {exc}", file=sys.stderr)
        try:
            time.sleep(interval)
        except KeyboardInterrupt:
            print("\nstopping.")
            return 0


if __name__ == "__main__":
    sys.exit(main())
