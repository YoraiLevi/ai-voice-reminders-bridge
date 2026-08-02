"""Configuration for the vox spoke - resolution, defaults, and derivation.

Three concerns live in one flat config, separated by *where things live* (see the
design spec, section 5):

  identity/routing  - who we are (`spoke_name`) and who we route to (`route_to`)
  phone bus         - the two Reminders list names (+ optional GUID pins)
  shared mailbox    - the protocol's dir (theirs) we read/write files in
  our private state - creds/session/topic, under an XDG state dir (ours)

Resolution order (first that exists wins):
  1. explicit path passed to load_config(path=...) / --config PATH
  2. $VOICE_BRIDGE_CONFIG
  3. ./.claude/voice-bridge.json   (relative to cwd)
  4. no file -> pure built-in defaults

Every field has a default, so `{}` is valid. `~` is expanded in path fields. Secrets
never live here - only the *locations* of secret files under `state_dir` do.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

from .util import atomic_write

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
    # NOTE the deliberate crossing, ruled from live use (UX-1): the list NAMES are
    # written from the PHONE USER's seat, while these FIELD names are written from
    # the bridge's. So the bridge's inbox - where it reads dictations from - is the
    # user's OUTBOX, because that is where they send from. An inbox belongs to
    # whoever reads it, and the person reading the phone is the user.
    # EMPTY until a list is actually chosen. These are a cache of the selected
    # list's real name, and a cache with a fabricated default is not a cache - it is
    # an invention that every surface then renders as fact. A fresh config claimed
    # two lists nobody had selected: the guided flow told the user to look in
    # "Vox-Message-Inbox" for a role they had skipped, and the phone prompt shipped
    # that name to the model as an instruction. Written only by the picker, and
    # refreshed by `doctor`, from the account.
    "inbox_list": "",  # user dictates here -> we read (phone -> us)
    "output_list": "",  # user reads here    <- we write (us -> phone)
    "inbox_list_id": "",
    "output_list_id": "",
    # shared mailbox (the protocol's, theirs)
    "mailbox_dir": "~/.agent-mail",
    # our private state (ours) - "" state_dir -> XDG default
    "state_dir": "",
    # transport
    "transport": "icloud",  # icloud | radicale
    # Client-side deadline for every iCloud HTTP call. There was none: `requests`
    # waits indefinitely by default, so a hung connection was bounded only by the
    # OS, which is why one interactive failure took minutes to report itself.
    # Tighter than a daemon would want on purpose - this path has a person in it.
    "icloud_timeout": 30.0,
    # self-hosted Radicale server (managed by `voice-bridge radicale-server`)
    # LOOPBACK by default, since batch 14. An iPhone refuses plain HTTP for CalDAV, so
    # the working posture is `tailscale serve` terminating TLS in front of this - and
    # then the LAN needs no access at all, which makes `0.0.0.0` the WORSE option rather
    # than the necessary one. Set it explicitly if you really are serving plain HTTP to
    # other machines.
    #
    # It also shrinks a near-miss: a `radicale-server init --force` run without
    # `--config` mid-triage wrote a fresh server config into whichever config resolved,
    # and with the old default that config bound every interface.
    "radicale_host": "127.0.0.1",  # bind address
    "radicale_port": 5232,  # int
    "radicale_user": "vox",  # the single CalDAV user
    # ntfy
    "ntfy_server": "https://ntfy.sh",
    # Seconds to hold a banner back so it lands AFTER the reminder it announces.
    # A push is one fast POST; the reminder has to sync to the phone, so the
    # buzz used to beat the thing it was announcing. 0 restores that. Sending
    # happens on a timer thread, so the poll cycle never waits.
    "notify_delay": 3.0,
    "ntfy_title": "Vox",
    "ntfy_tags": "robot",
    "ntfy_priority": "high",
    "ntfy_body_limit": -1,  # -1 = no clip
    # tuning
    # Emoji in reminder titles render correctly - confirmed on a device against
    # the CRDT length fix. "strip" remains available as an escape hatch for the
    # case that made it necessary: an unfixed pyicloud, whose encoder declares
    # codepoints where Apple counts UTF-16 units, so astral titles arrive blank.
    "emoji_titles": "allow",  # allow | strip
    "reply_summary_limit": -1,  # -1 = no clip
    "poll_interval": 10,
}

#: Stored, but NOT knobs. Read from old files, written by the program, and absent
#: from every surface that invites a user to change them.
#:
#: All three were settings that could not do what a setting appears to promise.
#:
#: * `from_name` and `spoke_name` printed identical values in `config show` and
#:   neither said what it drove, so they read as one knob shown twice. Setting
#:   `from_name` then changed nothing the user could see - the phone prompt renders
#:   `spoke_name` - and that is not a bug in the prompt, it is two names for one
#:   identity. Folded: `from_name` is always `spoke_name`.
#: * `inbox_list` / `output_list` stopped being identity when selection moved to
#:   ids. Editing a name now changes a caption and nothing else, while the id keeps
#:   pointing where it pointed - a setting whose visible effect is a label is a trap.
#:   They survive as a CACHED DISPLAY NAME, written by the picker and refreshed from
#:   the account whenever a transport is at hand.
#:
#: Kept in `DEFAULTS` on purpose: an existing config that names them must still
#: load, in silence. Deprecating a setting is not a reason to break a file.
_DERIVED_ONLY = {"from_name", "inbox_list", "output_list"}

#: Why each is no longer settable, and where the user should go instead. A refusal
#: that does not name the replacement verb is how the mis-advice class starts.
_DERIVED_GUIDANCE = {
    "from_name": "from_name is derived from spoke_name - set spoke_name instead",
    "inbox_list": "list names are read from your account - choose the list with `lists --select`",
    "output_list": "list names are read from your account - choose the list with `lists --select`",
}

#: The keys a PERSON may set. `_ALLOWED_KEYS` remains the storage vocabulary, so
#: the program can still write a cached name that the user may not type.
SETTABLE_KEYS = set(DEFAULTS) - _DERIVED_ONLY

_INT_FIELDS = {"ntfy_body_limit", "reply_summary_limit", "poll_interval", "radicale_port"}
#: Coerced (and therefore VALIDATED) at set time. Without this, `config set
#: notify_delay abc` would store a string that only fails at the next load, far
#: from the mistake.
_FLOAT_FIELDS = {"notify_delay", "icloud_timeout"}
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
    icloud_timeout: float
    ntfy_server: str
    notify_delay: float
    ntfy_title: str
    ntfy_tags: str
    ntfy_priority: str
    ntfy_body_limit: int
    emoji_titles: str
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
    reply_cursor_file: Path  # byte-offset cursor for draining our inbox
    source: str = "defaults"

    def as_dict(self) -> dict[str, str]:
        """Flat printable view of the FULL resolved state (`config show`/`get`).

        Derived from `dataclasses.fields` rather than hand-listed, so it cannot
        drift as fields are added: the hand-written version had fallen behind, and
        real fields such as `seen_file` were reported as "unknown field" (CFG-3).

        This is the full state; the *settable* subset is a different question and
        is answered by `field_help()`.
        """
        out = {"source": self.source}
        out.update({f.name: str(getattr(self, f.name)) for f in fields(self) if f.name != "source"})
        return out


def parse_overrides(pairs: list[str] | None, *, internal: bool = False) -> dict[str, Any]:
    """Turn repeated `--set KEY=VALUE` into a validated dict. Unknown key -> error;
    int fields coerced so they land as JSON numbers.

    `internal=True` is the PROGRAM writing derived state - the picker caching a
    chosen list's real name. It widens the vocabulary to `_ALLOWED_KEYS` and is
    never reachable from a command line, because the point of the deprecation is
    that a person is not offered a knob that cannot keep its promise.
    """
    vocabulary = _ALLOWED_KEYS if internal else SETTABLE_KEYS | set(_STATE_DERIVED) | {"creds_env"}
    out: dict[str, Any] = {}
    for p in pairs or []:
        if "=" not in p:
            raise ValueError(f"--set expects KEY=VALUE, got: {p!r}")
        key, _, value = p.partition("=")
        key = key.strip()
        if key not in vocabulary:
            # A deprecated key is NOT an unknown key. Saying "unknown setting" for
            # a name the user just read in their own config file, or in yesterday's
            # docs, sends them looking for a typo that is not there - so it is named
            # as deprecated and pointed at the verb that replaced it.
            if key in _DERIVED_ONLY:
                raise ValueError(_DERIVED_GUIDANCE[key])
            # Names the KEY and the allowed set, but not the flag: this same
            # parser backs `--set` and `config set`, and naming one sends half the
            # callers looking at the wrong thing.
            raise ValueError(f"unknown setting {key!r}; allowed: {', '.join(sorted(vocabulary))}")
        # A bare `float()` failure reads "could not convert string to float: 'abc'",
        # which names neither the setting nor what it wanted. The unknown-key message
        # right above sets the standard: say what was wrong AND what is acceptable.
        if key in _INT_FIELDS:
            try:
                out[key] = int(value)
            except ValueError:
                raise ValueError(f"{key} must be a whole number, got {value!r}") from None
        elif key in _FLOAT_FIELDS:
            try:
                out[key] = float(value)
            except ValueError:
                raise ValueError(f"{key} must be a number of seconds, got {value!r}") from None
        else:
            out[key] = value
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
    """Persist the config. Atomic: a crash mid-write must not corrupt it (FMA-11)."""
    atomic_write(path, json.dumps(data, indent=2) + "\n")


def set_value(path: Path, key: str, value: str, *, internal: bool = False) -> None:
    """Validate + coerce one KEY=VALUE (via parse_overrides) and persist it into the
    config file, preserving the rest. Unknown key raises ValueError.

    `internal=True` for derived state the program owns - see `parse_overrides`.
    """
    coerced = parse_overrides([f"{key}={value}"], internal=internal)
    data = read_raw(path)
    data.update(coerced)
    write_raw(path, data)


def field_help() -> list[tuple[str, str]]:
    """(field, default) pairs for `config --help` - every SETTABLE key.

    The derived-only fields are absent by construction rather than by a second
    hand-maintained list: a surface that has to remember to hide something will
    eventually forget, and the whole point of the deprecation is that nobody is
    invited to set them.
    """
    rows = [(k, repr(v)) for k, v in DEFAULTS.items() if k in SETTABLE_KEYS]
    rows.append(("creds_env", "{state_dir}/{transport}.env"))
    for k, v in _STATE_DERIVED.items():
        rows.append((k, "{state_dir}/" + v))
    return rows


def resolve_config_path(
    explicit: str | Path | None = None, *, must_exist: bool = True
) -> tuple[Path | None, str]:
    """Decide which config file to use, and report which provider decided.

    **One resolver for reads and writes.** They used to disagree: writes always
    targeted the project-local file while reads honoured `$VOICE_BRIDGE_CONFIG`
    first, so with the variable set a `config set` appeared to succeed and then
    silently did nothing (CFG-1).

    `must_exist=True` (reads) returns `(None, ...)` when no file is present -
    the caller falls back to defaults. `must_exist=False` (writes) still names
    the project-local path, because that is the file to *create*.

    The provider string exists so an error can say *why* this path was chosen;
    "config file not found" is unactionable without it (CFG-4).
    """
    if explicit:
        return _expand(explicit), "--config"
    env = os.environ.get(ENV_VAR)
    if env:
        return _expand(env), f"${ENV_VAR}"
    local = Path.cwd() / DEFAULT_REL_PATH
    if local.exists() or not must_exist:
        return local, f"./{DEFAULT_REL_PATH.as_posix()}"
    return None, "defaults"


def _find_config_path(explicit: str | Path | None) -> Path | None:
    """Backwards-compatible shim for callers that only want the path."""
    return resolve_config_path(explicit)[0]


def load_config(
    path: str | Path | None = None, *, overrides: dict[str, Any] | None = None
) -> Config:
    """Build a resolved Config. Missing file -> defaults; present-but-broken -> raise.
    `overrides` (e.g. from --set) win over the file and defaults."""
    cfg_path, provider = resolve_config_path(path)
    data: dict[str, Any] = {}
    source = "defaults (no config file found)"
    if cfg_path is not None:
        if not cfg_path.exists():
            # Name the provider: the same message is otherwise unactionable when
            # the path came from an environment variable the user forgot about.
            raise ConfigError(f"config file not found: {cfg_path} (from {provider})")
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
    # ALWAYS the spoke name, even when an old file names something else. One
    # identity, one place to change it: a stored `from_name` used to override this
    # silently, so the mailbox tag and the phone persona could disagree with no
    # surface saying which one you were looking at. Old files still load - the key
    # is simply no longer consulted.
    from_name = spoke
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
        icloud_timeout=float(merged["icloud_timeout"]),
        ntfy_server=str(merged["ntfy_server"]).rstrip("/"),
        notify_delay=float(merged["notify_delay"]),
        ntfy_title=str(merged["ntfy_title"]),
        ntfy_tags=str(merged["ntfy_tags"]),
        ntfy_priority=str(merged["ntfy_priority"]),
        ntfy_body_limit=int(merged["ntfy_body_limit"]),
        emoji_titles=str(merged["emoji_titles"]),
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
        reply_cursor_file=seen_dir / f"{slug}-reply.cursor",
        source=source,
    )
