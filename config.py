"""Per-project configuration for voice-bridge.

Today the bridge modules used to hard-code the two Reminders list names, the
mailbox directory, the ntfy topic file, and the credential/cookie locations.
That pinned one install to one project. This module lifts all of that into a
small JSON config so ONE install can serve MANY projects — each project just
drops a `.claude/voice-bridge.json` (or points `VOICE_BRIDGE_CONFIG` at one).

Resolution order (first that exists wins):
  1. an explicit path passed to `load_config(path=...)` / `--config PATH`
  2. $VOICE_BRIDGE_CONFIG
  3. ./.claude/voice-bridge.json   (relative to the current working directory)
  4. no file  -> pure built-in defaults (a minimal, working config)

Every field has a sensible default, so a config may set only what it overrides
(even `{}` is valid). `~` is expanded in every path field. Secrets NEVER live in
the config or the repo — only the *locations* of the credential/cookie/topic
files under `~/.auth` do; the secrets themselves stay in those files.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# --- defaults --------------------------------------------------------------

DEFAULTS: dict[str, Any] = {
    "name": "vox",
    # The two Reminders lists that are the contract with the phone.
    "inbox_list": "To Vox",     # phone -> PC   (requests you add on the phone)
    "output_list": "From Vox",  # PC -> phone   (replies surfaced back)
    # Optional: pin a list by its exact CloudKit record id (e.g. "List/UUID"). When set,
    # the iCloud bridge resolves the list by id and IGNORES title — the robust fix for
    # orphaned/ghost Reminders lists that share a title (they linger server-side, hidden
    # from the phone UI, but the raw API still returns them). Empty = resolve by title.
    "inbox_list_id": "",
    "output_list_id": "",
    # The tag written into each mailbox line: "- [HH:MM] (<from_name>) <msg>".
    "from_name": "owner-phone",
    # The manager's file mailbox + the two channel files inside it.
    "mailbox_dir": "~/.claude/message-protocol",
    "to_manager": "to-manager.md",  # bridge appends inbound reminders here
    "to_phone": "to-phone.md",      # manager appends replies here; loop drains them
    # Secrets/session live under ~/.auth (NEVER in the repo).
    "ntfy_topic_file": "~/.auth/ntfy-topic.txt",
    # ntfy server that banners are POSTed to (override for a self-hosted ntfy).
    "ntfy_server": "https://ntfy.sh",
    "creds_env": "~/.auth/icloud.env",
    "cookie_dir": "~/.auth/pyicloud-cookies",
    # Poll cadence in seconds.
    "poll_interval": 10,
}

ENV_VAR = "VOICE_BRIDGE_CONFIG"
DEFAULT_REL_PATH = Path(".claude") / "voice-bridge.json"


class ConfigError(RuntimeError):
    """The config file exists but could not be parsed / is invalid."""


def _expand(p: str) -> Path:
    return Path(os.path.expanduser(str(p))).expanduser()


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(name).lower()).strip("-")
    return s or "voice-bridge"


@dataclass(frozen=True)
class Config:
    """Fully-resolved settings. Path fields are absolute, `~`-expanded Paths."""

    name: str
    inbox_list: str
    output_list: str
    inbox_list_id: str
    output_list_id: str
    from_name: str
    mailbox_dir: Path
    to_manager: Path
    to_phone: Path
    seen_file: Path
    reply_seen_file: Path
    ntfy_topic_file: Path
    ntfy_server: str
    creds_env: Path
    cookie_dir: Path
    poll_interval: int
    source: str = "defaults"  # where this config was loaded from (for logs)

    def as_display(self) -> dict[str, str]:
        """A flat, printable view of the resolved settings (for --show-config)."""
        return {
            "source": self.source,
            "name": self.name,
            "inbox_list": self.inbox_list,
            "output_list": self.output_list,
            "inbox_list_id": self.inbox_list_id,
            "output_list_id": self.output_list_id,
            "from_name": self.from_name,
            "mailbox_dir": str(self.mailbox_dir),
            "to_manager": str(self.to_manager),
            "to_phone": str(self.to_phone),
            "seen_file": str(self.seen_file),
            "reply_seen_file": str(self.reply_seen_file),
            "ntfy_topic_file": str(self.ntfy_topic_file),
            "ntfy_server": self.ntfy_server,
            "creds_env": str(self.creds_env),
            "cookie_dir": str(self.cookie_dir),
            "poll_interval": str(self.poll_interval),
        }

    def print_resolved(self) -> None:
        d = self.as_display()
        width = max(len(k) for k in d)
        print("== resolved voice-bridge config ==")
        for k, v in d.items():
            print(f"  {k.ljust(width)} : {v}")


def _find_config_path(explicit: str | Path | None) -> Path | None:
    """Locate the config file per the resolution order. Returns None if none found
    (callers then fall back to pure defaults)."""
    if explicit:
        return _expand(str(explicit))
    env = os.environ.get(ENV_VAR)
    if env:
        return _expand(env)
    local = Path.cwd() / DEFAULT_REL_PATH
    if local.exists():
        return local
    return None


def load_config(path: str | Path | None = None) -> Config:
    """Build a resolved Config. Missing file -> defaults; present-but-broken -> raise."""
    cfg_path = _find_config_path(path)

    data: dict[str, Any] = {}
    source = "defaults (no config file found)"
    if cfg_path is not None:
        if not cfg_path.exists():
            raise ConfigError(f"config file not found: {cfg_path}")
        try:
            data = json.loads(cfg_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ConfigError(f"invalid JSON in {cfg_path}: {exc}") from exc
        if not isinstance(data, dict):
            raise ConfigError(f"config root must be a JSON object: {cfg_path}")
        source = str(cfg_path)

    merged = {**DEFAULTS, **data}

    mailbox_dir = _expand(merged["mailbox_dir"])
    slug = _slug(merged["name"])
    state_dir = mailbox_dir / "state"

    return Config(
        name=str(merged["name"]),
        inbox_list=str(merged["inbox_list"]),
        output_list=str(merged["output_list"]),
        inbox_list_id=str(merged.get("inbox_list_id", "")),
        output_list_id=str(merged.get("output_list_id", "")),
        from_name=str(merged["from_name"]),
        mailbox_dir=mailbox_dir,
        to_manager=mailbox_dir / merged["to_manager"],
        to_phone=mailbox_dir / merged["to_phone"],
        # Seen-files are namespaced by the project slug so several projects can
        # share one mailbox_dir without their dedupe state colliding.
        seen_file=state_dir / f"{slug}-seen.txt",
        reply_seen_file=state_dir / f"{slug}-reply-seen.txt",
        ntfy_topic_file=_expand(merged["ntfy_topic_file"]),
        ntfy_server=str(merged["ntfy_server"]).rstrip("/"),
        creds_env=_expand(merged["creds_env"]),
        cookie_dir=_expand(merged["cookie_dir"]),
        poll_interval=int(merged["poll_interval"]),
        source=source,
    )


if __name__ == "__main__":
    # `python config.py [PATH]` prints the resolved settings — the quickest way to
    # see exactly what a given config resolves to.
    import sys

    arg = sys.argv[1] if len(sys.argv) > 1 else None
    load_config(arg).print_resolved()
