# /// script
# requires-python = ">=3.11"
# dependencies = ["caldav>=3.2,<4", "icalendar>=6,<8"]
# ///
"""Create the inbox / output VTODO lists on the CalDAV server (config-driven).

Point this at the RUNNING Radicale (via the same creds/URL the bridge uses) to
create the two task lists from the Windows side. In normal operation the iPhone
creates them; creating them here lets the whole loop be proven with NO phone
involved, and is idempotent (skips any that already exist).

List NAMES come from the resolved voice-bridge config (inbox_list / output_list).
Credentials come from `_caldav.load_creds(cfg)`:
    ICLOUD_CALDAV_URL   e.g. http://127.0.0.1:5232/   (Radicale, not iCloud)
    ICLOUD_APPLE_ID     the Radicale username
    ICLOUD_APP_PASSWORD the Radicale password
from the environment or the config's creds_env file.

Usage (from radicale/, with the env set or creds_env present):
    uv run init_lists.py [--config PATH]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Import the bridge's shared plumbing from the parent voice-bridge/ directory.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _caldav import CredsError, caldav_url, connect, find_list, load_creds  # noqa: E402
from config import ConfigError, load_config  # noqa: E402


def _make_vtodo_list(principal, name: str) -> str:
    """Create a VTODO-capable calendar named `name`; return a status word."""
    if find_list(principal, name) is not None:
        return "exists"
    cal_id = name.lower().replace(" ", "-")
    principal.make_calendar(name=name, cal_id=cal_id, supported_calendar_component_set=["VTODO"])
    return "created"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", metavar="PATH", help="explicit voice-bridge.json")
    args = ap.parse_args(argv)

    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    try:
        creds = load_creds(cfg)
    except CredsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    url = caldav_url()
    if "icloud.com" in url:
        print(
            "refusing to run against iCloud — this script creates lists on a\n"
            "self-hosted CalDAV server. Set ICLOUD_CALDAV_URL to your Radicale URL.",
            file=sys.stderr,
        )
        return 2

    print(f"connecting to {url} as {creds.masked()}")
    principal = connect(creds)
    for name in (cfg.inbox_list, cfg.output_list):
        status = _make_vtodo_list(principal, name)
        print(f"  {name!r}: {status}")
    print("done. Run probe.py next to confirm GREEN.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
