# /// script
# requires-python = ">=3.11"
# ///
"""bootstrap.py — deterministic, IDEMPOTENT per-project voice-bridge setup.

Run this ONCE from a project's own folder (its cwd) to create that project's
`./.claude/voice-bridge.json`. It is safe to re-run: a second run detects the
existing config and just prints it. Nothing here talks to the network, creates
Reminders lists, or touches your Apple credentials — it only writes ONE local
config file and tells you the one manual step left (make the two phone lists).

This script LIVES IN the voice-bridge repo, so it knows the repo path from its
own `__file__` — there is no filesystem search on this side. It records that
path into the config as `vb_path`, so nothing downstream ever has to search for
the repo again (SETUP.md reads `vb_path` straight out of the config).

Behavior:
  * If `./.claude/voice-bridge.json` already exists  -> print
    `ALREADY CONFIGURED: <name>` + the resolved key fields, exit 0.
  * Otherwise (first time) -> derive a slug + Title label from the folder name,
    write a config with DISTINCT per-project fields (name / inbox_list /
    output_list / from_name / mailbox_dir) and the SHARED `~/.auth` locations,
    print the NEXT STEP (create the two Reminders lists — the poller cannot make
    lists), and print a machine-readable final line:
        SETUP_DONE lists_needed=To <Title>|From <Title>

Usage:
    uv run /path/to/voice-bridge/bootstrap.py     # from inside the project folder
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# The repo this script ships in — resolved from our own location, never searched.
REPO_DIR = Path(__file__).resolve().parent

# Reuse config.py's slug + defaults so bootstrap and the poller agree exactly on
# the project slug (the poller namespaces its seen-files by `_slug(name)`; if the
# two disagreed, dedupe state would land under a different name than expected).
sys.path.insert(0, str(REPO_DIR))
try:
    from config import DEFAULTS, DEFAULT_REL_PATH, _slug  # type: ignore
except Exception:  # pragma: no cover - config.py is a sibling; this is a safety net
    import re

    DEFAULT_REL_PATH = Path(".claude") / "voice-bridge.json"
    DEFAULTS = {
        "to_manager": "to-manager.md",
        "to_phone": "to-phone.md",
        "ntfy_topic_file": "~/.auth/ntfy-topic.txt",
        "creds_env": "~/.auth/icloud.env",
        "cookie_dir": "~/.auth/pyicloud-cookies",
        "poll_interval": 10,
    }

    def _slug(name: str) -> str:
        s = re.sub(r"[^a-z0-9]+", "-", str(name).lower()).strip("-")
        return s or "voice-bridge"


def _title(slug: str) -> str:
    """`project-proposals` -> `Project Proposals` (Title-cased, space-joined)."""
    return " ".join(word.capitalize() for word in slug.split("-") if word) or "Voice Bridge"


def _repo_path_str() -> str:
    """Forward-slashed repo path — works verbatim in a `uv run <vb>/...` command on
    Windows and POSIX alike, and reads cleanly out of JSON."""
    return str(REPO_DIR).replace("\\", "/")


# Fields to echo back on both first-run and the idempotent re-run, in this order.
_KEY_FIELDS = ("name", "inbox_list", "output_list", "from_name", "mailbox_dir", "vb_path")


def _print_fields(data: dict) -> None:
    width = max(len(k) for k in _KEY_FIELDS)
    for k in _KEY_FIELDS:
        print(f"  {k.ljust(width)} : {data.get(k, '(unset)')}")


def build_config(slug: str, title: str) -> dict:
    """The exact JSON object we write for a brand-new project. Per-project fields
    are DISTINCT (derived from the slug); the `~/.auth` locations are SHARED so one
    Apple login serves every project. `vb_path` pins the repo so nothing re-searches."""
    return {
        "name": slug,
        "inbox_list": f"To {title}",       # phone -> PC  (you add requests here)
        "output_list": f"From {title}",    # PC -> phone  (replies surface here)
        "from_name": f"{slug}-phone",
        "mailbox_dir": f"~/.claude/message-protocol/{slug}",
        "to_manager": DEFAULTS.get("to_manager", "to-manager.md"),
        "to_phone": DEFAULTS.get("to_phone", "to-phone.md"),
        "ntfy_topic_file": DEFAULTS.get("ntfy_topic_file", "~/.auth/ntfy-topic.txt"),
        "creds_env": DEFAULTS.get("creds_env", "~/.auth/icloud.env"),
        "cookie_dir": DEFAULTS.get("cookie_dir", "~/.auth/pyicloud-cookies"),
        "poll_interval": DEFAULTS.get("poll_interval", 10),
        # Extra field: the resolved repo path. config.load_config ignores unknown
        # keys (it reads a fixed set), so this is safe to carry in the same file.
        "vb_path": _repo_path_str(),
    }


def main(argv: list[str] | None = None) -> int:
    cwd = Path.cwd()
    cfg_path = cwd / DEFAULT_REL_PATH

    # --- idempotent re-run: config already exists -> report and stop ----------
    if cfg_path.exists():
        try:
            data = json.loads(cfg_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            print(f"ERROR: {cfg_path} exists but is not valid JSON: {exc}", file=sys.stderr)
            return 2
        print(f"ALREADY CONFIGURED: {data.get('name', '(no name)')}")
        _print_fields(data)
        print(f"  config path already at: {cfg_path}")
        if not data.get("vb_path"):
            print(
                "  note: this config predates vb_path — SETUP.md will locate the repo once."
            )
        return 0

    # --- first-time setup: derive names from the folder, write the config -----
    slug = _slug(cwd.name)
    title = _title(slug)
    config = build_config(slug, title)

    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")

    print(f"CONFIGURED: {slug}")
    _print_fields(config)
    print(f"  wrote config: {cfg_path}")
    print()
    print("NEXT STEP (one manual action — the poller CANNOT create lists):")
    print(f"  On your phone's Reminders app, create TWO lists named EXACTLY:")
    print(f"      To {title}")
    print(f"      From {title}")
    print("  (If you use the CalDAV alt transport, create the equivalent two")
    print("   Radicale collections with those same two names instead.)")
    print("  Once those two lists exist, this project is ready to launch.")
    print()
    print(f"SETUP_DONE lists_needed=To {title}|From {title}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
