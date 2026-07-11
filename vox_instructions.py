# /// script
# requires-python = ">=3.11"
# dependencies = ["caldav>=3.2,<4", "icalendar>=6,<8"]
# ///
"""vox_instructions.py — the GLOBAL "Vox Instructions" list.

Vox (the phone-side voice assistant) reads ONE global "Vox Instructions" list at the
start of each chat to get its current operating rules + the project routing table. This
is deliberately GLOBAL, not per-project: Vox's core behaviour is identical for every
project; the only per-project thing is which To/From list pair a project uses — that's a
row in a single routing table, not a separate instruction set.

The list holds TWO canonical items, each OVERWRITTEN wholesale on update (so it's never a
half-edited pile), with a version timestamp in the title:
  - "Vox Instructions - GLOBAL RULES (updated <ts>)"  = Vox's operating prompt
    (sourced from docs/BRIDGE-INSTRUCTIONS.md section 1, so there's one source of truth).
  - "Vox Instructions - ROUTING TABLE (updated <ts>)" = one line per project:
        <project> = <To-list> / <From-list>

SELF-PROVISIONING: `radicale_bootstrap.py` calls `upsert_routing_row(...)` after creating
a new project's To/From lists, so onboarding a project auto-registers it into Vox's
routing with zero manual step. `ensure_list` creates the global list on first use, so even
the global part bootstraps itself.

The ONLY manual step (unavoidable — bootstrapping) is ONE line in Vox's phone-side prompt:
  "At the start of every new conversation, silently read the most recent items in the
   Reminders list named 'Vox Instructions' ..."

Usage:
  uv run vox_instructions.py --config ./.claude/voice-bridge.json --register
      -> ensure the global list + refresh GLOBAL RULES + add/update THIS project's routing row.
  uv run vox_instructions.py --config <cfg>            (no --register: refresh rules only)
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from datetime import datetime
from pathlib import Path

from caldav.lib import error as caldav_error

REPO_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_DIR))

from _caldav import connect, find_list, load_creds  # noqa: E402
from config import load_config  # noqa: E402

ROUTING_MAX_RETRIES = 8  # optimistic-CAS retries when a concurrent writer wins the race

LIST_NAME = "Vox Instructions"
RULES_PREFIX = "Vox Instructions - GLOBAL RULES"
ROUTING_PREFIX = "Vox Instructions - ROUTING TABLE"
ROUTING_HEADER = "PROJECT ROUTING TABLE  (say a project's name and Vox uses these two lists):"
# a routing row: "<name> = <To-list> / <From-list>" — the header has no ' = ' so it won't self-match.
_ROW_RE = re.compile(r"^\s*([^=/]+?)\s*=\s*(.+?)\s*/\s*(.+?)\s*$")


def _phone_prompt() -> str:
    """The single source of truth for Vox's rules = the phone-side prompt block in the doc."""
    doc = (REPO_DIR / "docs" / "BRIDGE-INSTRUCTIONS.md").read_text(encoding="utf-8")
    after = doc.split("## 1. PHONE-SIDE PROMPT", 1)[1]
    return after.split("```", 2)[1].strip()


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def ensure_list(principal):
    """Return the global list, creating it (VTODO-capable) if missing. Idempotent."""
    cal = find_list(principal, LIST_NAME)
    if cal is None:
        principal.make_calendar(
            name=LIST_NAME,
            cal_id=LIST_NAME.lower().replace(" ", "-"),
            supported_calendar_component_set=["VTODO"],
        )
        cal = find_list(principal, LIST_NAME)
    return cal


def _find_item(cal, prefix: str):
    for todo in cal.objects(load_objects=True):
        try:
            summ = str(todo.icalendar_component.get("summary") or "")
        except Exception:
            continue  # an item deleted mid-listing 404s on load — skip it
        if summ.startswith(prefix):
            return todo
    return None


def _replace_item(cal, prefix: str, summary: str, description: str) -> None:
    """Delete any existing item whose title starts with `prefix`, then create the new one
    (overwrite-wholesale, so the item is never a half-updated mess)."""
    old = _find_item(cal, prefix)
    if old is not None:
        try:
            old.delete()
        except Exception:
            pass
    cal.save_todo(summary=summary, description=description)


def set_global_rules(cal) -> str:
    summary = f"{RULES_PREFIX} (updated {_now()})"
    _replace_item(cal, RULES_PREFIX, summary, _phone_prompt())
    return summary


def _parse_routing(desc: str) -> dict:
    rows: dict[str, tuple[str, str]] = {}
    for line in (desc or "").splitlines():
        if line.strip().startswith("PROJECT ROUTING TABLE"):
            continue
        m = _ROW_RE.match(line)
        if m:
            rows[m.group(1).strip()] = (m.group(2).strip(), m.group(3).strip())
    return rows


def _routing_body(rows: dict) -> tuple[str, str]:
    summary = f"{ROUTING_PREFIX} (updated {_now()})"
    lines = [ROUTING_HEADER] + [f"{n} = {rows[n][0]} / {rows[n][1]}" for n in sorted(rows)]
    return summary, "\n".join(lines)


def _read_routing(cal) -> dict:
    item = _find_item(cal, ROUTING_PREFIX)
    if item is None:
        return {}
    return _parse_routing(str(item.icalendar_component.get("description") or ""))


def ensure_routing_item(cal):
    """Guarantee the single routing-table item exists, so concurrent registrations only ever
    UPDATE it (one stable resource) — that lets ETag If-Match CAS work and removes the
    create-race entirely."""
    item = _find_item(cal, ROUTING_PREFIX)
    if item is None:
        summary, body = _routing_body({})
        cal.save_todo(summary=summary, description=body)
        item = _find_item(cal, ROUTING_PREFIX)
    return item


def _jitter(name: str, attempt: int) -> float:
    # name-derived offset desyncs concurrent writers; grows with attempt count
    return (abs(hash(name)) % 13) * 0.02 + attempt * 0.08


def upsert_routing_row(principal, name: str, inbox: str, output: str) -> dict:
    """Add/update ONE project's routing row, CONCURRENCY-SAFE via ETag If-Match CAS.

    Load the single routing item (captures its ETag) -> merge my row into the CURRENT rows
    -> save() (sends `if-match: <etag>`). If another writer changed it since our load, the
    server returns 412 and caldav raises ETagMismatchError -> we re-load the now-updated
    content (which INCLUDES the other writer's row), merge our row again, and retry. So two
    projects onboarding at once BOTH end up registered — neither silently drops the other.

    Returns a confirmation dict: {name, verified, total_rows, attempts, rows} — so onboarding
    is NOT silent fire-and-forget; the caller can see (and log) that the row actually landed.
    """
    cal = ensure_list(principal)
    ensure_routing_item(cal)
    for attempt in range(ROUTING_MAX_RETRIES):
        item = _find_item(cal, ROUTING_PREFIX)
        if item is None:
            ensure_routing_item(cal)
            continue
        try:
            item.load()  # refresh content AND ETag together
        except Exception:
            time.sleep(_jitter(name, attempt))
            continue
        rows = _parse_routing(str(item.icalendar_component.get("description") or ""))
        if rows.get(name) == (inbox, output):
            return {"name": name, "verified": True, "total_rows": len(rows),
                    "attempts": attempt + 1, "rows": sorted(rows)}
        rows[name] = (inbox, output)
        summary, body = _routing_body(rows)
        comp = item.icalendar_component
        comp.pop("summary", None)
        comp.add("summary", summary)
        comp.pop("description", None)
        comp.add("description", body)
        try:
            item.save()  # If-Match CAS on the loaded ETag
        except caldav_error.ETagMismatchError:
            time.sleep(_jitter(name, attempt))  # a concurrent writer won; re-read + retry
            continue
        except Exception:
            time.sleep(_jitter(name, attempt))
            continue
        # save() succeeded under If-Match => our row is durably merged (any later writer
        # loads THIS version and keeps our row). Confirmed.
        return {"name": name, "verified": True, "total_rows": len(rows),
                "attempts": attempt + 1, "rows": sorted(rows)}
    return {"name": name, "verified": False, "total_rows": len(_read_routing(cal)),
            "attempts": ROUTING_MAX_RETRIES, "rows": sorted(_read_routing(cal))}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", required=True, help="a voice-bridge.json (for Radicale creds + this project)")
    ap.add_argument("--register", action="store_true", help="also add/update THIS project's routing row")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    principal = connect(load_creds(cfg))
    cal = ensure_list(principal)
    print("global rules :", set_global_rules(cal))
    if args.register:
        conf = upsert_routing_row(principal, cfg.name, cfg.inbox_list, cfg.output_list)
        ok = "OK" if conf["verified"] else "NOT VERIFIED"
        print(f"routing row  : {conf['name']} -> {ok} "
              f"({conf['total_rows']} rows total, {conf['attempts']} attempt(s)); rows={conf['rows']}")
    else:
        ensure_routing_item(cal)
        print("routing table: ensured")
    print(f"\n'{LIST_NAME}' is live on Radicale and will sync to the phone's Reminders.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
