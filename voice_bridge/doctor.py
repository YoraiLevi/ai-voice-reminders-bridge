"""`doctor` - survey every interface and report GREEN / WARN / RED, worst as the exit code.

A health check's entire product is *justified confidence*, so a false GREEN is
worse than no check at all: it converts "I don't know" into "I checked, it's
fine". The previous version could report GREEN for five untrue reasons -

* the config row was a literal GREEN, printed before anything was inspected;
* the credentials row asked only whether the file existed, so an empty or
  half-written one passed;
* the lists row matched by NAME, so a pinned id pointing at a deleted list passed
  while every actual poll would fail, because polling resolves by id;
* the topic row passed on an empty file, and an empty topic silently disables
  every notification;
* the mailbox row **created the directory it was checking**, then reported on the
  state it had just manufactured.

That last one is the sharpest: a survey that mutates cannot fail, and "your
mailbox path is wrong" is exactly what a misconfigured user needs to hear. The
survey is now read-only; `--fix` is the consent that makes repair legitimate.
"""

from __future__ import annotations

from pathlib import Path

from .config import Config, set_value
from .factory import make_transport
from .selection import resolve_selection
from .transport import ListRef, Transport
from .util import read_env

_RANK = {"GREEN": 0, "WARN": 1, "RED": 2}

#: What a usable credentials file must contain, per backend. "Exists" is not a
#: check - on the iCloud path the file may be written by hand, so it can be
#: partial, and a partial file fails later at a point far from the mistake.
_REQUIRED_KEYS = {
    "icloud": ("ICLOUD_APPLE_ID", "ICLOUD_PASSWORD"),
    "radicale": ("ICLOUD_CALDAV_URL", "ICLOUD_APPLE_ID", "ICLOUD_APP_PASSWORD"),
}

_QUOTES = ("'", '"')

Row = tuple[str, str, str]


#: Credential fields an environment variable can supply. Reported, never blocked:
#: env-over-file is a legitimate convention, and scripted runs depend on it.
_ENV_CRED_KEYS = (
    "ICLOUD_APPLE_ID",
    "ICLOUD_USERNAME",
    "ICLOUD_APP_PASSWORD",
    "ICLOUD_PASSWORD",
    "ICLOUD_CALDAV_URL",
)


def _env_overrides() -> list[str]:
    """Credential names set in the environment, which WIN over the creds file.

    Found in a QA rehearsal: `radicale-server init` writes credentials and prints
    where it put them, so the user reasonably believes those are in use - but an
    inherited `ICLOUD_APPLE_ID` silently authenticated as someone else, and the
    only symptom was a bare 401. The precedence is fine; the silence was not.
    """
    import os

    return [k for k in _ENV_CRED_KEYS if os.environ.get(k)]


def _creds_row(cfg: Config) -> Row:
    if not cfg.creds_env.exists():
        return ("creds file", "RED", f"create {cfg.creds_env}")

    values = read_env(cfg.creds_env)
    required = _REQUIRED_KEYS.get(cfg.transport, _REQUIRED_KEYS["icloud"])
    missing = [k for k in required if not values.get(k)]
    if missing:
        return ("creds file", "RED", f"{cfg.creds_env} is missing {', '.join(missing)}")

    # Same heuristic and wording the login flow uses, so the diagnosis exists in
    # both places a user looks. Values are read verbatim, so wrapping quotes
    # really do become part of the secret.
    wrapped = [
        k
        for k in required
        if len(values.get(k, "")) >= 2 and values[k][0] == values[k][-1] and values[k][0] in _QUOTES
    ]
    if overrides := _env_overrides():
        # THE WORDING TRACKS WHO ACTUALLY READS WHAT, and checking that turned up a
        # second defect while fixing the first.
        #
        # This row said "those WIN over <file>, so the file may not be what is actually
        # used" for every transport. On radicale that was true and is now fixed (the file
        # outranks a foreign env name - batch 15). On **iCloud it was never true**:
        # `ICloudTransport.connect` reads `read_kv(creds_env, ...)`, the file and nothing
        # else. A user with `ICLOUD_PASSWORD` exported was told their file might be
        # ignored when it was the only thing being read - the same false claim, pointing
        # the opposite way, and it had been there all along.
        #
        # Both cases still deserve a WARN, because in both the user has set credentials
        # that are not doing what they expect. What changes is what to tell them.
        if cfg.transport == "radicale":
            return (
                "creds file",
                "WARN",
                f"{', '.join(overrides)} set in the environment - these are APPLE "
                f"credentials and no longer override {cfg.creds_env} on this transport, "
                "but they would still be used if that file were missing. Unset them for "
                "a self-hosted server",
            )
        return (
            "creds file",
            "WARN",
            f"{', '.join(overrides)} set in the environment - this transport reads "
            f"{cfg.creds_env} and NOT the environment, so those values are being "
            "ignored. If you meant them to apply, put them in the file "
            "(`voice-bridge icloud-login` writes it)",
        )

    if wrapped:
        return (
            "creds file",
            "WARN",
            f"{', '.join(wrapped)} starts and ends with a quote character - values are read "
            "literally, so remove the quotes if authentication fails",
        )
    return ("creds file", "GREEN", "")


def _duplicates_holding_items(t: Transport, refs: list[ListRef]) -> int:
    count = 0
    for ref in refs:
        try:
            if t.read_incomplete(ref):
                count += 1
        except Exception:  # pragma: no cover - a backend that cannot read one list
            continue
    return count


def _list_rows(cfg: Config, t: Transport, *, fix: bool = False) -> tuple[Row, Row]:
    """(auth row, lists row). If auth fails the lists row is UNKNOWN, never GREEN.

    The verdicts come from `resolve_selection` - the same engine `setup` and
    `lists --select` use. This module used to decide for itself whether a pin was
    settled, which made three implementations of one question; they agreed only
    by luck, and the name comparison here was the one that missed case-twins.
    """
    try:
        t.connect()
        plan = resolve_selection(cfg, t)
    except Exception as exc:
        return (
            ("transport auth", "RED", f"{type(exc).__name__}: {exc}"),
            ("lists", "WARN", "not checked - authentication failed"),
        )

    problems: list[str] = []
    for res in plan.resolutions:
        if res.status == "stale":
            # The selection is what polling resolves by, so it is what must exist.
            config_file = Path(cfg.source) if cfg.source.endswith(".json") else None
            if fix and config_file is not None and config_file.exists():
                # CLEAR, never re-resolve. Re-resolving by name would hand back a
                # DIFFERENT list under a repair verb - a silent substitution
                # dressed as a fix. Clearing restores "we don't know yet", and the
                # next interactive run asks properly.
                set_value(config_file, res.field, "")
                problems.append(f"{res.field} pointed at {res.current!r} - cleared it (--fix)")
            elif fix:
                # `source` is a description, not always a path ("defaults (no config
                # file found)"). Say we could not write rather than inventing a file.
                problems.append(
                    f"{res.field} {res.current!r} matches no list, and there is no config "
                    f"file to clear it from (source: {cfg.source})"
                )
            else:
                problems.append(
                    f"the selected list no longer exists ({res.field}={res.current!r}) - "
                    f"run `voice-bridge lists --select`, or `doctor --fix` to clear it"
                )
        elif res.status == "unselected":
            # No id means nothing to poll. Name matching used to paper over this by
            # guessing, which is exactly what was removed: an unselected role is a
            # real, reportable state rather than something to resolve silently.
            problems.append(
                f"{res.field} is not set - choose a list via `voice-bridge lists --select`"
            )

    _refresh_cached_names(cfg, plan)

    if problems:
        return (("transport auth", "GREEN", ""), ("lists", "WARN", "; ".join(problems)))
    return (("transport auth", "GREEN", ""), ("lists", "GREEN", ""))


def _refresh_cached_names(cfg: Config, plan) -> None:
    """Write back a display name the account has since changed.

    The config's list name is a CACHE. The id is the identity, so a rename on the
    phone breaks nothing - but the phone prompt prints the name, and a prompt that
    names a list the user has renamed reads as a bug in the tool rather than a
    stale copy of a label.

    `doctor` is the natural place: it is the only read-only command that already
    holds a connected transport and the full inventory, so refreshing costs no
    extra call. Silent by design - correcting a cache to match the account is not
    a finding, and reporting it as one would add noise to the survey people run
    when something is actually wrong.
    """
    config_file = Path(cfg.source) if cfg.source.endswith(".json") else None
    if config_file is None or not config_file.exists():
        return
    for res in plan.resolutions:
        if res.status != "selected":
            continue
        cached = cfg.inbox_list if res.role == "inbox" else cfg.output_list
        if res.name and res.name != cached:
            field = "inbox_list" if res.role == "inbox" else "output_list"
            set_value(config_file, field, res.name, internal=True)


def _topic_row(cfg: Config) -> Row:
    if not cfg.ntfy_topic_file.exists():
        return ("ntfy topic", "WARN", f"write a topic to {cfg.ntfy_topic_file}")
    if not cfg.ntfy_topic_file.read_text(encoding="utf-8").strip():
        # An empty file looks configured and sends nothing at all.
        return ("ntfy topic", "WARN", f"{cfg.ntfy_topic_file} is empty - no banners will be sent")
    return ("ntfy topic", "GREEN", "")


def _mailbox_row(cfg: Config, *, fix: bool) -> Row:
    if fix:
        try:
            cfg.mailbox_dir.mkdir(parents=True, exist_ok=True)
            cfg.peer_inbox.touch(exist_ok=True)
            cfg.our_inbox.touch(exist_ok=True)
        except OSError as exc:
            return ("mailbox dir", "RED", f"cannot create {cfg.mailbox_dir}: {exc}")

    if not cfg.mailbox_dir.is_dir():
        return ("mailbox dir", "RED", f"{cfg.mailbox_dir} does not exist - re-run with --fix")
    missing = [p.name for p in (cfg.peer_inbox, cfg.our_inbox) if not p.exists()]
    if missing:
        return ("mailbox dir", "WARN", f"missing {', '.join(missing)} - re-run with --fix")
    return ("mailbox dir", "GREEN", "")


def run(cfg: Config, t: Transport | None = None, *, fix: bool = False) -> int:
    """Survey everything. Read-only unless `fix` is set. Returns the worst severity."""
    t = t or make_transport(cfg)

    auth_row, lists_row = _list_rows(cfg, t, fix=fix)
    rows: list[Row] = [
        ("config", "GREEN", f"source: {cfg.source}"),
        _creds_row(cfg),
        auth_row,
        lists_row,
        _topic_row(cfg),
        _mailbox_row(cfg, fix=fix),
    ]

    worst = 0
    for name, status, msg in rows:
        line = f"  [{status:<5}] {name}"
        if msg:
            line += f"  -> {msg}"
        print(line)
        worst = max(worst, _RANK[status])
    return worst
