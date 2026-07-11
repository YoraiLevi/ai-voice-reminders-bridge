# /// script
# requires-python = ">=3.11"
# dependencies = ["caldav>=3.2,<4", "icalendar>=6,<8"]
# ///
"""radicale_bootstrap.py — FULLY self-provisioning per-project setup (Radicale).

The plain `bootstrap.py` writes a config but stops there: it CANNOT create the two
Reminders lists, so iCloud projects still need a manual phone-side step. Radicale
removes that wall — a CalDAV client is allowed to CREATE collections — so this
script provisions a brand-new project END-TO-END with ZERO manual server steps:

  1. Derive a slug + Title from THIS folder's name (the cwd basename).
  2. Write `./.claude/voice-bridge.json` for the RADICALE transport (creds_env points
     at `~/.auth/radicale.env`, which already carries the Radicale URL + user/pass,
     so the poller talks to Radicale, not iCloud).
  3. CONNECT to Radicale over CalDAV and CREATE the two VTODO lists `To <Title>` /
     `From <Title>` if they are missing. This is the step iCloud could not do.
     Because the phone already has the ONE shared Radicale CalDAV account added, the
     new lists auto-sync to Reminders on the phone — no phone-side action at all.

IDEMPOTENT. Re-run and:
  * config already present AND both lists already on the server
        -> print `ALREADY PROVISIONED: <name>`, exit 0.
  * config present but a list missing (partial state) -> create the missing list(s),
    then report provisioned. Any subset can be re-created safely (make_calendar is
    guarded by an existence check).

It never prints the Radicale password (see `_caldav.Creds.masked()`), and it REFUSES
to run against iCloud (the whole point is a server that accepts list creation).

This script LIVES IN the voice-bridge repo, so it knows the repo path from its own
`__file__`; it records that as `vb_path` in the config so nothing downstream ever has
to search for the repo again.

Usage (from inside the project folder you want to control by voice):
    uv run /path/to/voice-bridge/radicale_bootstrap.py
    uv run /path/to/voice-bridge/radicale_bootstrap.py --config ./.claude/voice-bridge.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# The repo this script ships in — resolved from our own location, never searched.
REPO_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_DIR))

from _caldav import CredsError, caldav_url, connect, find_list, load_creds  # noqa: E402
from config import DEFAULTS, DEFAULT_REL_PATH, ConfigError, _slug, load_config  # noqa: E402


def _title(slug: str) -> str:
    """`zz-provision-test` -> `Zz Provision Test` (Title-cased, space-joined)."""
    return " ".join(word.capitalize() for word in slug.split("-") if word) or "Voice Bridge"


def _repo_path_str() -> str:
    """Forward-slashed repo path — works verbatim in a `uv run <vb>/...` command on
    Windows and POSIX alike, and reads cleanly out of JSON."""
    return str(REPO_DIR).replace("\\", "/")


# Fields to echo back on both first-run and the idempotent re-run, in this order.
_KEY_FIELDS = ("name", "inbox_list", "output_list", "from_name", "mailbox_dir", "creds_env", "vb_path")


def _print_fields(data: dict) -> None:
    width = max(len(k) for k in _KEY_FIELDS)
    for k in _KEY_FIELDS:
        print(f"  {k.ljust(width)} : {data.get(k, '(unset)')}")


def build_config(slug: str, title: str) -> dict:
    """The exact JSON object we write for a brand-new Radicale project. Per-project
    fields are DISTINCT (derived from the slug); the `~/.auth` locations are SHARED so
    ONE Radicale CalDAV account on the phone serves every project. `creds_env` points
    at `~/.auth/radicale.env` (URL + Radicale user/pass) so the poller talks to
    Radicale, not iCloud. `vb_path` pins the repo so nothing re-searches."""
    return {
        "name": slug,
        "inbox_list": f"To {title}",       # phone -> PC  (you add requests here)
        "output_list": f"From {title}",    # PC -> phone  (replies surface here)
        "from_name": f"{slug}-phone",
        "mailbox_dir": f"~/.claude/message-protocol/{slug}",
        "to_manager": DEFAULTS.get("to_manager", "to-manager.md"),
        "to_phone": DEFAULTS.get("to_phone", "to-phone.md"),
        "ntfy_topic_file": DEFAULTS.get("ntfy_topic_file", "~/.auth/ntfy-topic.txt"),
        # The Radicale env file carries ICLOUD_CALDAV_URL + the Radicale user/pass, so
        # the SAME bridge code points at Radicale instead of iCloud (one value swap).
        "creds_env": "~/.auth/radicale.env",
        "cookie_dir": DEFAULTS.get("cookie_dir", "~/.auth/pyicloud-cookies"),
        "poll_interval": DEFAULTS.get("poll_interval", 10),
        # Extra field: the resolved repo path. config.load_config ignores unknown keys
        # (it reads a fixed set), so this is safe to carry in the same file.
        "vb_path": _repo_path_str(),
    }


def _make_vtodo_list(principal, name: str) -> str:
    """Create a VTODO-capable calendar named `name`; return `exists` or `created`.
    Guarded by an existence check, so calling it repeatedly is safe (idempotent).

    A newly-created list is SEEDED with one placeholder VTODO — an empty CalDAV list
    does not sync to the iPhone Reminders app until it holds at least one item, so
    without this seed the owner would have to add a first reminder by hand before the
    new lists appear on the phone."""
    if find_list(principal, name) is not None:
        return "exists"
    cal_id = name.lower().replace(" ", "-")
    principal.make_calendar(name=name, cal_id=cal_id, supported_calendar_component_set=["VTODO"])
    try:
        cal = find_list(principal, name)
        if cal is not None:
            cal.save_todo(summary=f"{name} is live — the voice bridge created this list. Safe to delete this item.")
    except Exception:
        pass  # seeding is best-effort; the list itself was created successfully
    return "created"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", metavar="PATH", help="explicit voice-bridge.json (default: ./.claude/voice-bridge.json)")
    args = ap.parse_args(argv)

    cwd = Path.cwd()
    cfg_path = _expand_config_arg(args.config) if args.config else (cwd / DEFAULT_REL_PATH)

    # --- 1. config: write it if missing, else keep the existing one -------------
    config_preexisted = cfg_path.exists()
    if config_preexisted:
        try:
            data = json.loads(cfg_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            print(f"ERROR: {cfg_path} exists but is not valid JSON: {exc}", file=sys.stderr)
            return 2
        title = data.get("inbox_list", "To ").partition("To ")[2] or _title(_slug(cwd.name))
    else:
        slug = _slug(cwd.name)
        title = _title(slug)
        data = build_config(slug, title)
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        cfg_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    # --- 2. connect to Radicale (creds + URL come from the config's creds_env) --
    try:
        cfg = load_config(str(cfg_path))
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
            "refusing to run against iCloud — self-provisioning creates lists on a\n"
            "self-hosted CalDAV server. Point creds_env at ~/.auth/radicale.env (which\n"
            "sets ICLOUD_CALDAV_URL to your Radicale URL). See radicale/OWNER-SETUP.md.",
            file=sys.stderr,
        )
        return 2

    print(f"provisioning on {url} as {creds.masked()}")
    try:
        principal = connect(creds)
    except CredsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    # --- 3. create the two lists if missing (idempotent make_calendar) ----------
    statuses = {}
    for name in (cfg.inbox_list, cfg.output_list):
        try:
            statuses[name] = _make_vtodo_list(principal, name)
        except Exception as exc:  # pragma: no cover - network
            print(f"  {name!r}: ERROR creating list: {exc}", file=sys.stderr)
            return 2
        print(f"  {name!r}: {statuses[name]}")

    all_existed = all(s == "exists" for s in statuses.values())

    # --- 4. report -------------------------------------------------------------
    print()
    if config_preexisted and all_existed:
        print(f"ALREADY PROVISIONED: {cfg.name}")
    else:
        print(f"PROVISIONED: {cfg.name}")
    _print_fields(data)
    print(f"  config path      : {cfg_path}")
    print()
    print("Both lists exist on Radicale and (via the shared phone CalDAV account) will")
    print("sync to Reminders on the phone automatically — NO manual phone step.")
    print()
    print("START THE POLLER (leave it running for the session):")
    vb = data.get("vb_path", _repo_path_str())
    print(f"  uv run {vb}/reminder_bridge.py --config ./.claude/voice-bridge.json")
    return 0


def _expand_config_arg(p: str) -> Path:
    import os

    return Path(os.path.expanduser(p)).expanduser()


if __name__ == "__main__":
    sys.exit(main())
