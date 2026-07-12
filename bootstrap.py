# /// script
# requires-python = ">=3.11"
# dependencies = ["caldav>=3.2,<4", "icalendar>=6,<8"]
# ///
"""bootstrap.py — one provisioner for a voice-bridge project, both transports.

Run this ONCE from a project's own folder (its cwd) to set that project up. It is
IDEMPOTENT — safe to re-run. The `--transport` flag is the only branch:

  --transport icloud   (default)
      Write `./.claude/voice-bridge.json` (creds_env -> ~/.auth/icloud.env) and
      print the ONE manual step: create the two Reminders lists on the phone.
      iCloud FORBIDS a CalDAV client from creating lists, so this side stops at
      "config written + here are the two list names to make by hand".

  --transport radicale
      Write `./.claude/voice-bridge.json` (creds_env -> ~/.auth/radicale.env), then
      CONNECT to Radicale and CREATE the two VTODO lists if missing. Radicale allows
      list creation, so this side provisions END-TO-END with no phone step — the two
      lists sync to the phone via the one shared Radicale CalDAV account.

This script LIVES IN the voice-bridge repo, so it knows the repo path from its own
`__file__` — no filesystem search. It records that path into the config as `vb_path`,
so nothing downstream ever has to search for the repo again (SETUP.md reads it).

Usage (from inside the project folder you want to control by voice):
    uv run /path/to/voice-bridge/bootstrap.py                      # iCloud (default)
    uv run /path/to/voice-bridge/bootstrap.py --transport radicale # Radicale, self-provisioning
    uv run /path/to/voice-bridge/bootstrap.py --config ./.claude/voice-bridge.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# The repo this script ships in — resolved from our own location, never searched.
REPO_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_DIR))

# config.py is a sibling; reuse its slug + defaults so bootstrap and the poller agree
# exactly on the project slug (the poller namespaces its seen-files by `_slug(name)`).
from config import DEFAULTS, DEFAULT_REL_PATH, ConfigError, _slug, load_config  # noqa: E402


def _title(slug: str) -> str:
    """`example-project` -> `Example Project` (Title-cased, space-joined)."""
    return " ".join(word.capitalize() for word in slug.split("-") if word) or "Voice Bridge"


def _repo_path_str() -> str:
    """Forward-slashed repo path — works verbatim in a `uv run <vb>/...` command on
    Windows and POSIX alike, and reads cleanly out of JSON."""
    return str(REPO_DIR).replace("\\", "/")


# Fields echoed back on both first-run and the idempotent re-run, in this order.
_KEY_FIELDS = ("name", "inbox_list", "output_list", "from_name", "mailbox_dir", "creds_env", "vb_path")


def _print_fields(data: dict) -> None:
    width = max(len(k) for k in _KEY_FIELDS)
    for k in _KEY_FIELDS:
        print(f"  {k.ljust(width)} : {data.get(k, '(unset)')}")


def build_config(slug: str, title: str, *, creds_env: str) -> dict:
    """The exact JSON object written for a brand-new project. Per-project fields are
    DISTINCT (derived from the slug); the `~/.auth` locations are SHARED so one account
    serves the project. `creds_env` is the only transport-dependent field:
    `~/.auth/icloud.env` (Apple ID) vs `~/.auth/radicale.env` (Radicale URL + user/pass).
    `vb_path` pins the repo so nothing re-searches."""
    return {
        "name": slug,
        "inbox_list": f"To {title}",       # phone -> PC  (you add requests here)
        "output_list": f"From {title}",    # PC -> phone  (replies surface here)
        "from_name": f"{slug}-phone",
        "mailbox_dir": f"~/.claude/message-protocol/{slug}",
        "to_manager": DEFAULTS.get("to_manager", "to-manager.md"),
        "to_phone": DEFAULTS.get("to_phone", "to-phone.md"),
        "ntfy_topic_file": DEFAULTS.get("ntfy_topic_file", "~/.auth/ntfy-topic.txt"),
        "creds_env": creds_env,
        "cookie_dir": DEFAULTS.get("cookie_dir", "~/.auth/pyicloud-cookies"),
        "poll_interval": DEFAULTS.get("poll_interval", 10),
        # Extra field: the resolved repo path. config.load_config ignores unknown keys
        # (it reads a fixed set), so this is safe to carry in the same file.
        "vb_path": _repo_path_str(),
    }


def _expand_config_arg(p: str) -> Path:
    return Path(os.path.expanduser(p)).expanduser()


def _parse_overrides(pairs: list[str] | None, *, allowed: set[str]) -> dict:
    """Turn repeated `--set KEY=VALUE` into a validated dict. Every KEY must be a known
    config field (so a typo fails loudly instead of writing a silently-ignored key);
    `poll_interval` is coerced to int so it lands as a JSON number, not a string."""
    out: dict = {}
    for p in pairs or []:
        if "=" not in p:
            raise ValueError(f"--set expects KEY=VALUE, got: {p!r}")
        key, _, value = p.partition("=")
        key = key.strip()
        if key not in allowed:
            raise ValueError(f"--set unknown key {key!r}; allowed: {', '.join(sorted(allowed))}")
        out[key] = int(value) if key in ("poll_interval", "ntfy_body_limit", "reply_summary_limit") else value
    return out


def _title_from_config(data: dict, cwd: Path) -> str:
    """Recover the Title from an existing config's inbox_list (`To <Title>`)."""
    return data.get("inbox_list", "To ").partition("To ")[2] or _title(_slug(cwd.name))


def _make_vtodo_list(principal, name: str) -> str:
    """Create a VTODO-capable calendar named `name`; return `exists` or `created`.
    Guarded by an existence check, so calling it repeatedly is safe (idempotent).

    A newly-created list is SEEDED with one placeholder VTODO — an empty CalDAV list
    does not sync to the iPhone Reminders app until it holds at least one item, so
    without this seed the owner would have to add a first reminder by hand before the
    new lists appear on the phone."""
    from _caldav import find_list  # lazy — only the radicale path needs CalDAV

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


def _provision_icloud(cfg_path: Path, data: dict, title: str, config_preexisted: bool) -> int:
    """iCloud: config-only. The poller CANNOT create Reminders lists, so the two lists
    are a manual phone step. Print the exact names and a machine-readable done line."""
    if config_preexisted:
        print(f"ALREADY CONFIGURED: {data.get('name', '(no name)')}")
        _print_fields(data)
        print(f"  config path already at: {cfg_path}")
        return 0

    print(f"CONFIGURED: {data.get('name')}")
    _print_fields(data)
    print(f"  wrote config: {cfg_path}")
    print()
    print("NEXT STEP (one manual action — the poller CANNOT create iCloud lists):")
    print("  On your phone's Reminders app, create TWO lists named EXACTLY:")
    print(f"      To {title}")
    print(f"      From {title}")
    print("  Once those two lists exist, this project is ready to launch.")
    print()
    print(f"SETUP_DONE lists_needed=To {title}|From {title}")
    return 0


def _provision_radicale(cfg_path: Path, data: dict, config_preexisted: bool) -> int:
    """Radicale: config + CREATE the two lists over CalDAV (idempotent). Refuses to run
    against iCloud, whose CalDAV forbids list creation."""
    from _caldav import CredsError, caldav_url, connect, load_creds

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
            "refusing to run --transport radicale against iCloud — self-provisioning\n"
            "creates lists on a self-hosted CalDAV server. Point creds_env at\n"
            "~/.auth/radicale.env (ICLOUD_CALDAV_URL = your Radicale URL). See\n"
            "radicale/OWNER-SETUP.md.",
            file=sys.stderr,
        )
        return 2

    print(f"provisioning on {url} as {creds.masked()}")
    try:
        principal = connect(creds)
    except CredsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    statuses = {}
    for name in (cfg.inbox_list, cfg.output_list):
        try:
            statuses[name] = _make_vtodo_list(principal, name)
        except Exception as exc:  # pragma: no cover - network
            print(f"  {name!r}: ERROR creating list: {exc}", file=sys.stderr)
            return 2
        print(f"  {name!r}: {statuses[name]}")

    all_existed = all(s == "exists" for s in statuses.values())
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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "--transport",
        choices=("icloud", "radicale"),
        default="icloud",
        help="icloud (default): config only, make lists by hand on the phone. "
        "radicale: config + auto-create the two lists on the server.",
    )
    ap.add_argument("--config", metavar="PATH", help="explicit voice-bridge.json (default: ./.claude/voice-bridge.json)")
    ap.add_argument(
        "--set",
        action="append",
        dest="overrides",
        metavar="KEY=VALUE",
        help="override any config field (repeatable), e.g. --set inbox_list='To Web' "
        "--set from_name=web-phone --set poll_interval=30. Defaults fill the rest. "
        "Known keys: " + ", ".join(sorted(DEFAULTS)) + ".",
    )
    args = ap.parse_args(argv)

    try:
        overrides = _parse_overrides(args.overrides, allowed=set(DEFAULTS))
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    cwd = Path.cwd()
    cfg_path = _expand_config_arg(args.config) if args.config else (cwd / DEFAULT_REL_PATH)

    # --- config: keep an existing one, else derive names from the folder and write it ---
    config_preexisted = cfg_path.exists()
    if config_preexisted:
        if overrides:
            print(
                f"note: --set ignored — {cfg_path} already exists; edit it directly to change fields.",
                file=sys.stderr,
            )
        try:
            data = json.loads(cfg_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            print(f"ERROR: {cfg_path} exists but is not valid JSON: {exc}", file=sys.stderr)
            return 2
        title = _title_from_config(data, cwd)
    else:
        # Default persona is "Vox" (the voice system's brand), not the folder name.
        # Override with --set name=... / --set inbox_list=... to use anything else.
        slug = "vox"
        title = "Vox"
        creds_env = "~/.auth/radicale.env" if args.transport == "radicale" else "~/.auth/icloud.env"
        data = build_config(slug, title, creds_env=creds_env)
        # Explicit --set overrides win over the folder-derived / transport defaults.
        data.update(overrides)
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        cfg_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        title = _title_from_config(data, cwd)  # re-derive in case inbox_list was overridden

    if args.transport == "radicale":
        return _provision_radicale(cfg_path, data, config_preexisted)
    return _provision_icloud(cfg_path, data, title, config_preexisted)


if __name__ == "__main__":
    sys.exit(main())
