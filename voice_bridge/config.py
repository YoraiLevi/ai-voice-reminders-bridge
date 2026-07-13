"""Configuration for the vox spoke — resolution, defaults, and derivation.

Three concerns live in one flat config, separated by *where things live* (see the
design spec, section 5):

  identity/routing  — who we are (`spoke_name`) and who we route to (`route_to`)
  phone bus         — the two Reminders list names (+ optional GUID pins)
  shared mailbox    — the protocol's dir (theirs) we read/write files in
  our private state — creds/session/topic, under an XDG state dir (ours)

Resolution order (first that exists wins):
  1. explicit path passed to load_config(path=...) / --config PATH
  2. $VOICE_BRIDGE_CONFIG
  3. ./.claude/voice-bridge.json   (relative to cwd)
  4. no file -> pure built-in defaults

Every field has a default, so `{}` is valid. `~` is expanded in path fields. Secrets
never live here — only the *locations* of secret files under `state_dir` do.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ENV_VAR = "VOICE_BRIDGE_CONFIG"
DEFAULT_REL_PATH = Path(".claude") / "voice-bridge.json"

# Fields whose default is DERIVED from another resolved field (state_dir); a user may
# still override them explicitly. Kept out of DEFAULTS so derivation is unambiguous.
# creds_env is derived separately (its name depends on the transport: icloud.env /
# radicale.env), so only these two are fixed-name state files.
_STATE_DERIVED = {
    "cookie_dir": "pyicloud-cookies",
    "ntfy_topic_file": "ntfy-topic.txt",
}

# Every fixed-default field. `config --help` renders from this + _STATE_DERIVED.
DEFAULTS: dict[str, Any] = {
    # identity / routing
    "spoke_name": "vox",  # our inbox is  to-<spoke_name>.md
    "route_to": "manager",  # peer inbox is to-<route_to>.md
    "from_name": "",  # "" -> derived = spoke_name (the mailbox-line tag)
    # phone bus
    "inbox_list": "Vox-Message-Inbox",  # phone -> us (dictations)
    "output_list": "Vox-Message-Outbox",  # us -> phone (replies)
    "inbox_list_id": "",
    "output_list_id": "",
    # shared mailbox (the protocol's, theirs)
    "mailbox_dir": "~/.agent-mail",
    # our private state (ours) — "" state_dir -> XDG default
    "state_dir": "",
    # transport
    "transport": "icloud",  # icloud | radicale
    # self-hosted Radicale server (managed by `voice-bridge radicale-server`)
    "radicale_host": "0.0.0.0",  # bind address
    "radicale_port": 5232,  # int
    "radicale_user": "vox",  # the single CalDAV user
    # ntfy
    "ntfy_server": "https://ntfy.sh",
    "ntfy_title": "Vox",
    "ntfy_tags": "robot",
    "ntfy_priority": "high",
    "ntfy_body_limit": -1,  # -1 = no clip
    # tuning
    "reply_summary_limit": -1,  # -1 = no clip
    "poll_interval": 10,
}

_INT_FIELDS = {"ntfy_body_limit", "reply_summary_limit", "poll_interval", "radicale_port"}
_ALLOWED_KEYS = set(DEFAULTS) | set(_STATE_DERIVED) | {"creds_env"}


class ConfigError(RuntimeError):
    """The config file exists but could not be parsed / is invalid."""


def _expand(p: str | Path) -> Path:
    return Path(os.path.expanduser(str(p))).expanduser()


def _default_state_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or (Path.home() / ".local" / "state")
    return Path(base) / "vox-mailbox"


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(name).lower()).strip("-")
    return s or "vox"


@dataclass(frozen=True)
class Config:
    """Fully-resolved settings. Path fields are absolute, `~`-expanded Paths."""

    spoke_name: str
    route_to: str
    from_name: str
    inbox_list: str
    output_list: str
    inbox_list_id: str
    output_list_id: str
    mailbox_dir: Path
    state_dir: Path
    transport: str
    ntfy_server: str
    ntfy_title: str
    ntfy_tags: str
    ntfy_priority: str
    ntfy_body_limit: int
    reply_summary_limit: int
    poll_interval: int
    radicale_host: str
    radicale_port: int
    radicale_user: str
    creds_env: Path
    cookie_dir: Path
    ntfy_topic_file: Path
    # derived mailbox files
    our_inbox: Path  # to-<spoke_name>.md   (messages TO us)
    peer_inbox: Path  # to-<route_to>.md     (messages we SEND)
    seen_file: Path  # inbox dedupe by item id (private, under state_dir)
    reply_seen_file: Path  # legacy; drain now uses reply_cursor_file
    reply_cursor_file: Path  # byte-offset cursor for draining our inbox
    source: str = "defaults"

    def as_dict(self) -> dict[str, str]:
        """Flat printable view (for `config show` / `--show-config`)."""
        return {
            "source": self.source,
            "spoke_name": self.spoke_name,
            "route_to": self.route_to,
            "from_name": self.from_name,
            "inbox_list": self.inbox_list,
            "output_list": self.output_list,
            "inbox_list_id": self.inbox_list_id,
            "output_list_id": self.output_list_id,
            "mailbox_dir": str(self.mailbox_dir),
            "state_dir": str(self.state_dir),
            "transport": self.transport,
            "ntfy_server": self.ntfy_server,
            "ntfy_title": self.ntfy_title,
            "ntfy_tags": self.ntfy_tags,
            "ntfy_priority": self.ntfy_priority,
            "ntfy_body_limit": str(self.ntfy_body_limit),
            "reply_summary_limit": str(self.reply_summary_limit),
            "poll_interval": str(self.poll_interval),
            "radicale_host": self.radicale_host,
            "radicale_port": str(self.radicale_port),
            "radicale_user": self.radicale_user,
            "creds_env": str(self.creds_env),
            "cookie_dir": str(self.cookie_dir),
            "ntfy_topic_file": str(self.ntfy_topic_file),
            "our_inbox": str(self.our_inbox),
            "peer_inbox": str(self.peer_inbox),
        }


def parse_overrides(pairs: list[str] | None) -> dict[str, Any]:
    """Turn repeated `--set KEY=VALUE` into a validated dict. Unknown key -> error;
    int fields coerced so they land as JSON numbers."""
    out: dict[str, Any] = {}
    for p in pairs or []:
        if "=" not in p:
            raise ValueError(f"--set expects KEY=VALUE, got: {p!r}")
        key, _, value = p.partition("=")
        key = key.strip()
        if key not in _ALLOWED_KEYS:
            raise ValueError(
                f"--set unknown key {key!r}; allowed: {', '.join(sorted(_ALLOWED_KEYS))}"
            )
        out[key] = int(value) if key in _INT_FIELDS else value
    return out


def default_config_path() -> Path:
    """Where a project's config lives by default (cwd/.claude/voice-bridge.json)."""
    return Path.cwd() / DEFAULT_REL_PATH


def read_raw(path: Path) -> dict[str, Any]:
    """The config file's JSON as-written (not resolved). Missing file -> {}."""
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def write_raw(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def set_value(path: Path, key: str, value: str) -> None:
    """Validate + coerce one KEY=VALUE (via parse_overrides) and persist it into the
    config file, preserving the rest. Unknown key raises ValueError."""
    coerced = parse_overrides([f"{key}={value}"])
    data = read_raw(path)
    data.update(coerced)
    write_raw(path, data)


def field_help() -> list[tuple[str, str]]:
    """(field, default) pairs for `config --help` — every settable key."""
    rows = [(k, repr(v)) for k, v in DEFAULTS.items()]
    rows.append(("creds_env", "{state_dir}/{transport}.env"))
    for k, v in _STATE_DERIVED.items():
        rows.append((k, "{state_dir}/" + v))
    return rows


def _find_config_path(explicit: str | Path | None) -> Path | None:
    if explicit:
        return _expand(explicit)
    env = os.environ.get(ENV_VAR)
    if env:
        return _expand(env)
    local = Path.cwd() / DEFAULT_REL_PATH
    if local.exists():
        return local
    return None


def load_config(
    path: str | Path | None = None, *, overrides: dict[str, Any] | None = None
) -> Config:
    """Build a resolved Config. Missing file -> defaults; present-but-broken -> raise.
    `overrides` (e.g. from --set) win over the file and defaults."""
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

    merged = {**DEFAULTS, **data, **(overrides or {})}

    spoke = str(merged["spoke_name"])
    route = str(merged["route_to"])
    from_name = str(merged["from_name"]) or spoke
    mailbox_dir = _expand(merged["mailbox_dir"])
    state_dir = _expand(merged["state_dir"]) if merged["state_dir"] else _default_state_dir()

    def _state_path(key: str) -> Path:
        explicit = data.get(key) or (overrides or {}).get(key)
        if explicit:
            return _expand(explicit)
        return state_dir / _STATE_DERIVED[key]

    transport = str(merged["transport"])
    creds_explicit = data.get("creds_env") or (overrides or {}).get("creds_env")
    creds_env = _expand(creds_explicit) if creds_explicit else state_dir / f"{transport}.env"

    slug = _slug(spoke)
    seen_dir = state_dir / "seen"

    return Config(
        spoke_name=spoke,
        route_to=route,
        from_name=from_name,
        inbox_list=str(merged["inbox_list"]),
        output_list=str(merged["output_list"]),
        inbox_list_id=str(merged.get("inbox_list_id", "")),
        output_list_id=str(merged.get("output_list_id", "")),
        mailbox_dir=mailbox_dir,
        state_dir=state_dir,
        transport=transport,
        ntfy_server=str(merged["ntfy_server"]).rstrip("/"),
        ntfy_title=str(merged["ntfy_title"]),
        ntfy_tags=str(merged["ntfy_tags"]),
        ntfy_priority=str(merged["ntfy_priority"]),
        ntfy_body_limit=int(merged["ntfy_body_limit"]),
        reply_summary_limit=int(merged["reply_summary_limit"]),
        poll_interval=int(merged["poll_interval"]),
        radicale_host=str(merged["radicale_host"]),
        radicale_port=int(merged["radicale_port"]),
        radicale_user=str(merged["radicale_user"]),
        creds_env=creds_env,
        cookie_dir=_state_path("cookie_dir"),
        ntfy_topic_file=_state_path("ntfy_topic_file"),
        our_inbox=mailbox_dir / f"to-{spoke}.md",
        peer_inbox=mailbox_dir / f"to-{route}.md",
        seen_file=seen_dir / f"{slug}-inbox-seen.txt",
        reply_seen_file=seen_dir / f"{slug}-reply-seen.txt",
        reply_cursor_file=seen_dir / f"{slug}-reply.cursor",
        source=source,
    )
