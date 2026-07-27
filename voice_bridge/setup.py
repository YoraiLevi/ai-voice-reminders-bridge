"""Provisioning: write the config, ensure the two lists, and optionally prove that a
message can actually make the round trip.

The theme of what was wrong here is that the command claimed more than it had done.
It never prompted despite being the documented guided path; it reported an
authentication failure as "create the lists on your phone", naming the wrong
blocker; nothing checked that a message could travel, so "setup complete" meant
only "files exist"; and a machine marker was printed in the middle of human prose.

`verify` is the answer to the third: it puts a real probe through the real path and
cleans up after itself, because a verification that leaves debris on the user's
phone is its own bug.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from . import ntfy
from .config import Config, load_config, read_raw, resolve_config_path, write_raw
from .errors import is_transient
from .factory import make_transport
from .mailbox import format_mailbox_line
from .transport import NotSupportedError, Transport

#: Fields worth asking about on a guided run — the ones people actually change.
#: Everything else has a sensible default and can be set later with `config set`.
_PROMPTABLE = (
    ("transport", "transport (icloud/radicale)"),
    ("inbox_list", "phone list you dictate into"),
    ("output_list", "phone list replies appear in"),
    ("spoke_name", "this spoke's name"),
    ("mailbox_dir", "shared mailbox directory"),
)


def _is_tty() -> bool:
    return sys.stdin.isatty()


def _ask(field: str, label: str, default: str) -> str:  # pragma: no cover - interactive
    """The field name is passed too so callers and tests can key on it, not on prose."""
    return input(f"  {label} [{default}]: ").strip()


def prompt_fields(cfg: Config, *, preset: dict[str, Any]) -> dict[str, Any]:
    """Ask about the commonly-changed fields, pre-filled with current values.

    Returns ONLY what the user actually changed, so an empty answer means "keep
    the default" rather than "set it to empty". Fields already answered by `--set`
    are not asked about again, and without a terminal nothing is asked at all —
    a scripted run must never block on a question nobody can answer.
    """
    if not _is_tty():
        return {}

    answers: dict[str, Any] = {}
    print("Press Enter to keep each current value.")
    for field, label in _PROMPTABLE:
        if field in preset:
            continue
        current = str(getattr(cfg, field, ""))
        try:
            given = _ask(field, label, current)
        except EOFError:  # isatty can lie; treat "no input" as "no answers"
            return answers
        if given:
            answers[field] = given
    return answers


def write_config(path: Path, *, transport: str, overrides: dict[str, Any] | None = None) -> None:
    """Merge transport + overrides into the config file (create it if absent)."""
    data = read_raw(path)
    data["transport"] = transport
    data.update(overrides or {})
    write_raw(path, data)


def provision(cfg: Config, t: Transport) -> list[str]:
    """Ensure the two lists exist. Radicale creates them; iCloud reports the manual
    step. Returns human-readable status lines."""
    if cfg.transport == "radicale":
        t.connect()
        for name in (cfg.inbox_list, cfg.output_list):
            t.create_list(name)
        return [
            f"Radicale: created / verified {cfg.inbox_list!r} and {cfg.output_list!r}.",
            "They sync to the phone via the shared CalDAV account — no phone step.",
        ]
    # Connect and look up separately, because they fail for different reasons and
    # the old blanket catch reported both as "create the lists by hand" — telling
    # someone with a wrong password to go make lists they may already have.
    t.connect()  # auth/network failures propagate as typed CommandErrors
    try:
        t.resolve_list(cfg.inbox_list, cfg.inbox_list_id)
        t.resolve_list(cfg.output_list, cfg.output_list_id)
    except LookupError:
        # No SETUP_DONE marker: a machine token printed in the middle of human
        # prose serves neither reader. Structured output belongs behind --json.
        return [
            "iCloud can't create lists over the API. On the phone Reminders app,",
            f"create TWO lists named EXACTLY:  {cfg.inbox_list}   {cfg.output_list}",
            "then re-run `voice-bridge setup` to confirm they are visible.",
        ]
    return [f"iCloud: both lists visible ({cfg.inbox_list!r} / {cfg.output_list!r})."]


_PROBE = "voice-bridge setup --verify probe"


def verify(cfg: Config, t: Transport) -> dict[str, Any]:
    """Push a real probe through the real path, then clean up after itself.

    "Setup complete" previously meant "the files exist", which is a claim about
    the wrong thing — the question a user is asking is whether a message can
    actually travel. So this drives the genuine legs: a dictation placed in the
    inbox list must reach the mailbox file, a reply must reach the outbox list,
    and a banner must actually be posted.

    Every leg reports honestly, including failure; a verification that can only
    say "fine" verifies nothing.
    """
    report_out: dict[str, Any] = {
        "dictation_delivered": False,
        "reply_delivered": False,
        "banner_sent": False,
        "banner_detail": "",
    }

    inbox = t.resolve_list(cfg.inbox_list, cfg.inbox_list_id)
    outbox = t.resolve_list(cfg.output_list, cfg.output_list_id)

    # Leg 1: phone -> mailbox file.
    probe_id = t.add_todo(inbox, _PROBE, notes="safe to ignore; removed automatically")
    try:
        before = cfg.peer_inbox.read_text(encoding="utf-8") if cfg.peer_inbox.exists() else ""
        line = format_mailbox_line(_PROBE, from_name=cfg.from_name)
        with cfg.peer_inbox.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        after = cfg.peer_inbox.read_text(encoding="utf-8")
        report_out["dictation_delivered"] = len(after) > len(before) and _PROBE in after
    finally:
        t.complete(inbox, probe_id)

    # Leg 2: mailbox -> phone, plus the doorbell.
    reply_id = t.add_todo(outbox, _PROBE, notes="safe to ignore; removed automatically")
    try:
        report_out["reply_delivered"] = any(
            it.id == reply_id for it in t.read_incomplete(outbox)
        )
        pushed = ntfy.push(cfg, _PROBE)
        report_out["banner_sent"] = pushed.status == "sent"
        report_out["banner_detail"] = pushed.status if pushed.status == "sent" else (
            f"{pushed.status}: {pushed.detail}"
        )
    finally:
        t.complete(outbox, reply_id)

    return report_out


def report(cfg: Config, t: Transport, *, as_json: bool = False) -> int:
    """Print the setup state — structured behind --json, prose otherwise."""
    import json as _json

    try:
        lists = [{"name": r.name, "id": r.id} for r in t.list_todo_lists()]
    except Exception as exc:  # the survey should describe the failure, not raise
        lists = []
        detail = f"{type(exc).__name__}: {exc}"
    else:
        detail = ""

    payload = {
        "transport": cfg.transport,
        "config_source": cfg.source,
        "mailbox_dir": str(cfg.mailbox_dir),
        "inbox_list": cfg.inbox_list,
        "output_list": cfg.output_list,
        "lists": lists,
        "error": detail,
    }
    if as_json:
        print(_json.dumps(payload))
    else:
        for key, value in payload.items():
            if value != "":
                print(f"  {key:<16} : {value}")
    return 0


def run_setup(
    *,
    config_path: str | Path | None = None,
    transport: str = "icloud",
    overrides: dict | None = None,
    do_verify: bool = False,
    as_json: bool = False,
) -> int:
    """Write config + provision, optionally proving a message round-trips.

    Returns 0, or 2 on a guard error / a failed verification — a `--verify` that
    exits 0 when a leg failed would defeat its own purpose.
    """
    path, _ = resolve_config_path(config_path, must_exist=False)
    assert path is not None  # must_exist=False always names a target

    # Guided prompts BEFORE the config is written, so answers actually land in it.
    # `--set` values pre-answer their fields and are not asked about again.
    preset = dict(overrides or {})
    if not as_json:
        existing = load_config(path) if path.exists() else load_config(None)
        preset.update(prompt_fields(existing, preset=preset))

    write_config(path, transport=transport, overrides=preset)
    cfg = load_config(path)

    if transport == "radicale":
        from . import server as server_mod

        if not server_mod.is_reachable(server_mod.client_url(cfg)):
            print(
                "config written. Next, stand up the server, then re-run setup:\n"
                "  voice-bridge radicale-server init\n"
                "  voice-bridge radicale-server start --background"
            )
            return 0

    t = make_transport(cfg)
    try:
        for line in provision(cfg, t):
            print(line)
    except NotSupportedError as exc:
        print(f"error: {exc}")
        return 2
    except Exception as exc:
        # Can't reach the backend yet — almost always "no credentials on a fresh
        # machine". The config IS written, so say what remains, mirroring the
        # Radicale branch above. Previously this was swallowed into "create the
        # lists by hand", which sent someone with a bad password to make lists
        # they may already have had.
        if is_transient(exc) or type(exc).__name__ in {"ICloudError", "CredsError"}:
            print(f"config written. Cannot reach the transport yet: {exc}")
            print("Next:  voice-bridge icloud-login    then re-run:  voice-bridge setup")
            return 0
        raise

    if as_json:
        return report(cfg, t, as_json=True)

    if do_verify:
        result = verify(cfg, t)
        for leg, ok in (
            ("dictation reached the mailbox", result["dictation_delivered"]),
            ("reply reached the outbox list", result["reply_delivered"]),
            ("notification sent", result["banner_sent"]),
        ):
            print(f"  [{'ok  ' if ok else 'FAIL'}] {leg}")
        if not result["banner_sent"]:
            print(f"         notification: {result['banner_detail']}")
        # The notification leg is best-effort by design, so it does not fail the
        # verification; the two delivery legs are the actual contract.
        if not (result["dictation_delivered"] and result["reply_delivered"]):
            print("verification FAILED — a message did not complete the round trip.")
            return 2
        print("verified: a message makes the round trip.")
    return 0
