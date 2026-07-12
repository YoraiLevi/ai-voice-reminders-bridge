# /// script
# requires-python = ">=3.11"
# dependencies = ["pyicloud"]
# ///
"""vox_instructions.py — the GLOBAL "Vox Instructions" list (iCloud edition).

Vox (the phone-side voice assistant) reads ONE global "Vox Instructions" list at the
start of each chat to get its current operating rules + the project routing table. This
is deliberately GLOBAL, not per-project.

**Transport = iCloud (pyicloud).** The canonical Vox Instructions list now lives on iCloud
Reminders (the fast channel), so this script writes THERE — matching the source of truth.
(It used to write to Radicale/CalDAV; that was migrated away 2026-07-12.)

The list holds these canonical items, each OVERWRITTEN wholesale on update (never a
half-edited pile), with a version timestamp in the title:
  - "Vox Instructions - GLOBAL RULES (updated <ts>)"  = Vox's operating prompt
    (sourced from docs/BRIDGE-INSTRUCTIONS.md section 1 — one source of truth).
  - "Vox Instructions - ROUTING TABLE (updated <ts>)" = one line per project:
        <project> = <To-list> / <From-list>
  - "Vox Instructions - BOOTSTRAP LINE ..."           = the one manual line for Vox's prompt.
  - "Vox Instructions - LIVE STATE ..."               = condensed current-state mirror.

pyicloud CANNOT create a Reminders list, so the "Vox Instructions" list must already exist
on iCloud (create it once in the Reminders app). Among same-titled lists (ghosts from
delete+recreate), this picks the one with the MOST items = the canonical one.

Usage (config = the iCloud voice-bridge.json):
  uv run vox_instructions.py --config ./.claude/voice-bridge.json                 # refresh rules+bootstrap
  uv run vox_instructions.py --config <cfg> --register                            # + this project's routing row
  uv run vox_instructions.py --config <cfg> --live-state-file <path>              # refresh LIVE STATE item
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_DIR))

import pyicloud_bridge as pb  # noqa: E402  (reuse its cached-session connect())
from config import load_config  # noqa: E402

ROUTING_MAX_RETRIES = 8

VOX_LIST_NAME = "Vox Instructions"
RULES_PREFIX = "Vox Instructions - GLOBAL RULES"
ROUTING_PREFIX = "Vox Instructions - ROUTING TABLE"
BOOTSTRAP_PREFIX = "Vox Instructions - BOOTSTRAP LINE (paste into Vox's phone prompt)"
LIVE_STATE_PREFIX = "Vox Instructions - LIVE STATE (what's happening right now)"
BOOTSTRAP_TEXT = (
    "At the start of every new conversation, silently read the most recent items in the Reminders list "
    "named 'Vox Instructions' - it holds your current operating rules and the project routing table "
    "(which project maps to which To/From lists). Follow it as your up-to-date instructions; if it's "
    "unavailable, fall back to what you already have."
)
ROUTING_HEADER = "PROJECT ROUTING TABLE  (say a project's name and Vox uses these two lists):"
_ROW_RE = re.compile(r"^\s*([^=/]+?)\s*=\s*(.+?)\s*/\s*(.+?)\s*$")


# --- content (transport-independent) ---------------------------------------

def _phone_prompt() -> str:
    doc = (REPO_DIR / "docs" / "BRIDGE-INSTRUCTIONS.md").read_text(encoding="utf-8")
    after = doc.split("## 1. PHONE-SIDE PROMPT", 1)[1]
    return after.split("```", 2)[1].strip()


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


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


# --- iCloud transport (pyicloud) -------------------------------------------

def _title(x) -> str:
    return (x.get("title") if isinstance(x, dict) else getattr(x, "title", "")) or ""


def _desc(x) -> str:
    return (x.get("desc") if isinstance(x, dict) else getattr(x, "desc", None)) or \
           (x.get("description") if isinstance(x, dict) else getattr(x, "description", "")) or ""


def vox_list_id(r) -> str:
    """Resolve the iCloud Vox Instructions list id. Among same-titled lists (ghosts), pick the
    one with the MOST items — the canonical one holds the migrated content; ghosts are empty.
    pyicloud can't create lists, so it must already exist."""
    same = [l for l in r.lists() if l.title == VOX_LIST_NAME]
    if not same:
        raise SystemExit(
            f"'{VOX_LIST_NAME}' list not found on iCloud. Create it once in the Reminders app, then retry."
        )
    return max(same, key=lambda l: l.count).id


def _items(r, list_id: str) -> list:
    res = r.list_reminders(list_id)
    return res if isinstance(res, list) else list(getattr(res, "reminders", res) or [])


def _replace_item(r, list_id: str, prefix: str, summary: str, description: str) -> None:
    """Overwrite-wholesale: delete every item titled with `prefix`, then create the new one."""
    for it in _items(r, list_id):
        if _title(it).startswith(prefix):
            try:
                r.delete(it)
            except Exception:
                pass
    r.create(list_id, summary[:250], desc=description)


def set_global_rules(r, list_id: str) -> str:
    summary = f"{RULES_PREFIX} (updated {_now()})"
    _replace_item(r, list_id, RULES_PREFIX, summary, _phone_prompt())
    return summary


def set_bootstrap(r, list_id: str) -> str:
    summary = f"{BOOTSTRAP_PREFIX} (updated {_now()})"
    _replace_item(r, list_id, BOOTSTRAP_PREFIX, summary, BOOTSTRAP_TEXT)
    return summary


def set_live_state(r, list_id: str, text: str) -> str:
    summary = f"{LIVE_STATE_PREFIX} (updated {_now()})"
    _replace_item(r, list_id, LIVE_STATE_PREFIX, summary, text.strip() or "(no live state set)")
    return summary


def _read_routing(r, list_id: str) -> dict:
    for it in _items(r, list_id):
        if _title(it).startswith(ROUTING_PREFIX):
            return _parse_routing(_desc(it))
    return {}


def _jitter(name: str, attempt: int) -> float:
    return (abs(hash(name)) % 13) * 0.02 + attempt * 0.08


def upsert_routing_row(r, list_id: str, name: str, inbox: str, output: str) -> dict:
    """Add/update ONE project's routing row via read-modify-write + verify + retry.

    (iCloud/pyicloud has no ETag If-Match, so this isn't a hard atomic CAS like the old CalDAV
    path — but it re-reads and verifies after writing and retries on a lost race. Project
    onboarding is rare, so a last-writer race is unlikely and self-corrects on the next run.)
    Returns {name, verified, total_rows, attempts, rows}."""
    for attempt in range(ROUTING_MAX_RETRIES):
        rows = _read_routing(r, list_id)
        if rows.get(name) == (inbox, output):
            return {"name": name, "verified": True, "total_rows": len(rows),
                    "attempts": attempt + 1, "rows": sorted(rows)}
        rows[name] = (inbox, output)
        summary, body = _routing_body(rows)
        _replace_item(r, list_id, ROUTING_PREFIX, summary, body)
        after = _read_routing(r, list_id)
        if after.get(name) == (inbox, output):
            return {"name": name, "verified": True, "total_rows": len(after),
                    "attempts": attempt + 1, "rows": sorted(after)}
        time.sleep(_jitter(name, attempt))
    final = _read_routing(r, list_id)
    return {"name": name, "verified": False, "total_rows": len(final),
            "attempts": ROUTING_MAX_RETRIES, "rows": sorted(final)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", required=True, help="the iCloud voice-bridge.json (creds + this project)")
    ap.add_argument("--register", action="store_true", help="also add/update THIS project's routing row")
    ap.add_argument("--live-state-file", metavar="PATH", help="refresh the LIVE STATE item from this file")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    r = pb.connect(cfg)  # pyicloud cached trusted session (no 2FA)
    list_id = vox_list_id(r)

    if args.live_state_file:
        text = open(os.path.expanduser(args.live_state_file), encoding="utf-8").read()
        print("live state   :", set_live_state(r, list_id, text))
        return 0

    print("global rules :", set_global_rules(r, list_id))
    print("bootstrap    :", set_bootstrap(r, list_id))
    if args.register:
        conf = upsert_routing_row(r, list_id, cfg.name, cfg.inbox_list, cfg.output_list)
        ok = "OK" if conf["verified"] else "NOT VERIFIED"
        print(f"routing row  : {conf['name']} -> {ok} "
              f"({conf['total_rows']} rows total, {conf['attempts']} attempt(s)); rows={conf['rows']}")
    else:
        if not any(_title(it).startswith(ROUTING_PREFIX) for it in _items(r, list_id)):
            summary, body = _routing_body({})
            _replace_item(r, list_id, ROUTING_PREFIX, summary, body)
        print("routing table: ensured")
    print(f"\n'{VOX_LIST_NAME}' is live on iCloud and syncs to the phone's Reminders.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
