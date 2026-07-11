# /// script
# requires-python = ">=3.11"
# dependencies = ["caldav>=3.2,<4", "icalendar>=6,<8"]
# ///
"""GO / NO-GO probe for the CalDAV alt transport: is a CalDAV Reminders bridge
viable on THIS account/server? (Config-driven — list names + creds from config.)

iCloud only exposes reminder lists over CalDAV that were never "upgraded" to the
newer CloudKit store; on a modern account a freshly-made list may be invisible
even though the iPhone sees it perfectly. This probe settles that empirically.
Against a self-hosted Radicale server it should simply go GREEN.

It reports, in order:
  (a) can we AUTH to the CalDAV server?
  (b) do we see ANY VTODO-capable lists?
  (c) is the configured inbox list visible, and can we read what the phone put there?

Then one verdict:
  GREEN  -> the bridge is viable; build/run reminder_bridge.py.
  RED [AUTH]            -> credentials rejected.
  RED [NO-TODO-LISTS]   -> CalDAV sees no task lists at all.
  RED [CLOUDKIT-INVISIBLE] -> auth + other lists OK, but the inbox list is not
      visible over CalDAV (the CloudKit store-format wall) -> use the Radicale
      fallback (radicale/OWNER-SETUP.md).

Run:  uv run probe.py [--config PATH]     (READ-ONLY: never creates/completes/deletes)
"""

from __future__ import annotations

import argparse
import sys

from _caldav import CredsError, connect, find_list, load_creds, todo_fields, todo_lists
from config import ConfigError, load_config


def _line(sym: str, msg: str) -> None:
    print(f"  {sym} {msg}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", metavar="PATH", help="explicit voice-bridge.json")
    args = ap.parse_args(argv)

    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    inbox_name = cfg.inbox_list
    print("== CalDAV Reminders probe ==")
    print(f"  project: {cfg.name}  |  inbox list: {inbox_name!r}  |  output list: {cfg.output_list!r}")

    # --- credentials --------------------------------------------------------
    try:
        creds = load_creds(cfg)
    except CredsError as exc:
        _line("x", str(exc))
        print("\nVERDICT: RED [NO-CREDS] — supply credentials, then re-run.")
        return 2
    print(f"  using {creds.masked()}")

    # --- (a) auth -----------------------------------------------------------
    print("\n== (a) authenticate + discover shard ==")
    try:
        principal = connect(creds)
    except CredsError as exc:
        _line("x", str(exc))
        print("\nVERDICT: RED [AUTH] — the server rejected the credentials.")
        return 1
    _line("+", "authenticated; principal discovered")

    # --- (b) any VTODO-capable lists ---------------------------------------
    print("\n== (b) enumerate VTODO-capable lists ==")
    try:
        lists = todo_lists(principal)
    except Exception as exc:  # pragma: no cover - network
        _line("x", f"could not enumerate calendars: {exc}")
        print("\nVERDICT: RED [DISCOVERY] — calendars could not be listed.")
        return 1

    if not lists:
        _line("x", "no VTODO-capable lists are visible over CalDAV")
        print("\nVERDICT: RED [NO-TODO-LISTS] — this account exposes no task lists")
        print("  over CalDAV. -> Use the self-hosted Radicale fallback (radicale/OWNER-SETUP.md).")
        return 1
    _line("+", f"found {len(lists)} VTODO-capable list(s): " + ", ".join(n for n, _ in lists))

    # --- (c) the inbox list specifically ------------------------------------
    print(f"\n== (c) is {inbox_name!r} visible + readable ==")
    inbox = find_list(principal, inbox_name)
    if inbox is None:
        _line("x", f"{inbox_name!r} is NOT visible over CalDAV")
        print(f"\nVERDICT: RED [CLOUDKIT-INVISIBLE] — auth works and other lists are")
        print(f"  visible, but {inbox_name!r} is not. A list created on a modern iPhone is")
        print("  CloudKit-backed and invisible to CalDAV. -> Switch to the Radicale")
        print("     fallback (radicale/OWNER-SETUP.md).")
        return 1

    try:
        # iCloud 500s on the server-side "exclude completed" filter, so read all
        # (objects()) and drop completed client-side — same as reminder_bridge.
        items = [
            t
            for t in inbox.objects(load_objects=True)
            if str(t.icalendar_component.get("status") or "").upper() != "COMPLETED"
            and t.icalendar_component.get("completed") is None
        ]
    except Exception as exc:  # pragma: no cover - network
        _line("x", f"{inbox_name!r} is visible but could not be read: {exc}")
        print("\nVERDICT: RED [READ-FAIL] — list visible but its items did not read.")
        return 1

    _line("+", f"{inbox_name!r} is visible; {len(items)} incomplete item(s)")
    for t in items[:5]:
        f = todo_fields(t)
        title = f["title"] or "(no title)"
        notes = f" — {f['notes']}" if f["notes"] else ""
        _line(" ", f"read: {title}{notes}")

    print(f"\nVERDICT: GREEN — auth OK, VTODO lists visible, and {inbox_name!r} is")
    print("  readable over CalDAV. The bridge is viable; run reminder_bridge.py.")
    if not items:
        print("  (Note: inbox is currently empty. Add a test item on the iPhone and")
        print("   re-run to confirm a phone-written item is readable end-to-end.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
