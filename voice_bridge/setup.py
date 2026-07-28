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
from typing import Any, Callable

from . import ntfy
from .config import Config, load_config, read_raw, resolve_config_path, set_value, write_raw
from .errors import is_transient
from .factory import make_transport
from .mailbox import append_line, format_mailbox_line
from .prompting import Cancelled
from .prompting import ask as _ask_line
from .selection import confirm_selection, pick, resolve_selection
from .transport import ListRef, NotSupportedError, Transport

#: Fields worth asking about on a guided run - the ones people actually change.
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
    return _ask_line(f"  {label} [{default}]: ").strip()


def prompt_fields(cfg: Config, *, preset: dict[str, Any]) -> dict[str, Any]:
    """Ask about the commonly-changed fields, pre-filled with current values.

    Returns ONLY what the user actually changed, so an empty answer means "keep
    the default" rather than "set it to empty". Fields already answered by `--set`
    are not asked about again, and without a terminal nothing is asked at all -
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


def provision(
    cfg: Config,
    t: Transport,
    *,
    created: dict[str, str] | None = None,
    create: bool = True,
) -> list[str]:
    """Ensure the two lists exist. Radicale creates them; iCloud reports the manual
    step. Returns human-readable status lines.

    Pass `created` to receive {config field: id} for lists this call created, so the
    caller can select them without inferring anything from their titles."""
    if cfg.transport == "radicale":
        t.connect()
        if not create:
            return [
                "Radicale: create the two lists yourself in your CalDAV client,",
                "then choose them here (press r to refresh until they appear).",
            ]
        # Keep the ids the backend returns: choosing by them is knowledge, where
        # matching their titles later would only be a guess.
        for field, name in (("inbox_list_id", cfg.inbox_list), ("output_list_id", cfg.output_list)):
            ref = t.create_list(name)
            if created is not None:
                created[field] = ref.id
        return [
            f"Radicale: created / verified {cfg.inbox_list!r} and {cfg.output_list!r}.",
            "They sync to the phone via the shared CalDAV account - no phone step.",
        ]
    # Connect and look up separately, because they fail for different reasons and
    # the old blanket catch reported both as "create the lists by hand" - telling
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


def _check_title(t: Transport, item_id: str) -> tuple[bool | None, str]:
    """Renderability of the probe's stored title, or (None, why) if unavailable.

    Only meaningful where the backend stores a CRDT document, so a backend without
    one reports None rather than a false pass - "not applicable" and "fine" must
    not look the same.
    """
    from .titlelint import check_stored

    svc = getattr(t, "r", None)
    if svc is None:
        return None, "not applicable for this transport"
    verdict = check_stored(svc, item_id)
    return verdict.ok, verdict.describe()


def verify(cfg: Config, t: Transport) -> dict[str, Any]:
    """Push a real probe through the real path, then clean up after itself.

    "Setup complete" previously meant "the files exist", which is a claim about
    the wrong thing - the question a user is asking is whether a message can
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
        "title_renderable": None,
        "title_detail": "",
    }

    inbox = t.resolve_list(cfg.inbox_list, cfg.inbox_list_id)
    outbox = t.resolve_list(cfg.output_list, cfg.output_list_id)

    # Leg 1: phone -> mailbox file.
    probe_id = t.add_todo(inbox, _PROBE, notes="safe to ignore; removed automatically")
    try:
        before = cfg.peer_inbox.read_text(encoding="utf-8") if cfg.peer_inbox.exists() else ""
        # Through `append_line`, not a raw open: it creates the parent directory,
        # and on a genuinely fresh machine - which is exactly when someone runs
        # setup --verify - the mailbox does not exist yet. A raw open crashed the
        # guided flow at its last step, after everything else had succeeded.
        append_line(cfg.peer_inbox, format_mailbox_line(_PROBE, from_name=cfg.from_name))
        after = cfg.peer_inbox.read_text(encoding="utf-8")
        report_out["dictation_delivered"] = len(after) > len(before) and _PROBE in after
    finally:
        t.complete(inbox, probe_id)

    # Leg 2: mailbox -> phone, plus the doorbell.
    reply_id = t.add_todo(outbox, _PROBE, notes="safe to ignore; removed automatically")
    try:
        report_out["reply_delivered"] = any(it.id == reply_id for it in t.read_incomplete(outbox))

        # Ask whether the phone could actually RENDER what we just wrote. Every
        # other check here reads the text back through the same API that wrote it,
        # which cannot see a malformed title document (LIVE-5) - this inspects the
        # stored structure instead, so the verdict needs no phone.
        report_out["title_renderable"], report_out["title_detail"] = _check_title(t, reply_id)
        pushed = ntfy.push(cfg, _PROBE)
        report_out["banner_sent"] = pushed.status == "sent"
        report_out["banner_detail"] = (
            pushed.status if pushed.status == "sent" else (f"{pushed.status}: {pushed.detail}")
        )
    finally:
        t.complete(outbox, reply_id)

    return report_out


def report(cfg: Config, t: Transport, *, as_json: bool = False) -> int:
    """Print the setup state - structured behind --json, prose otherwise."""
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

    Returns 0, or 2 on a guard error / a failed verification - a `--verify` that
    exits 0 when a leg failed would defeat its own purpose.
    """
    from . import onboard

    path, _ = resolve_config_path(config_path, must_exist=False)
    assert path is not None  # must_exist=False always names a target

    guided = not as_json and _is_tty()
    if guided:
        onboard.welcome(print)

    # Guided prompts BEFORE the config is written, so answers actually land in it.
    # `--set` values pre-answer their fields and are not asked about again.
    preset = dict(overrides or {})
    if not as_json:
        existing = load_config(path) if path.exists() else load_config(None)
        preset.update(prompt_fields(existing, preset=preset))

    write_config(path, transport=transport, overrides=preset)
    cfg = load_config(path)

    if guided and transport == "icloud":
        # Offered INLINE rather than named in a footnote: knowing the command
        # exists is not the same as being walked to it, and the gap between those
        # is where a first run stalls.
        def _login() -> int:
            from .login import icloud_login

            return icloud_login(cfg)

        if not onboard.step_credentials(cfg, ask=_ask_line, show=print, login=_login):
            onboard.farewell(print, ready=False)
            return 0
        cfg = onboard.reload_config(path)

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
        created: dict[str, str] = {}
        # Radicale CAN create the lists, but doing so silently would make the two
        # transports behave differently for no reason the user picked. Asking
        # unifies them: answer no and the flow is exactly iCloud's.
        if guided:
            print("")
            print("[2/5] Your lists")
            print("        next: notifications")
        auto = True
        if cfg.transport == "radicale" and _is_tty():
            auto = ask_radicale_creation(ask=_ask_line, show=print)
        for line in provision(cfg, t, created=created if auto else None, create=auto):
            print(line)
        # Choose the lists NOW. Nothing is inferred from a title: either we just
        # created it and know its id, or the user is asked.
        if settle_selection(cfg, t, config_path=path, created=created):
            cfg = load_config(path)
    except NotSupportedError as exc:
        print(f"error: {exc}")
        return 2
    except Cancelled:
        # This block contains interactive prompts (list creation, selection), so a
        # Ctrl-C arrives as an exception like any other and the broad handler below
        # would dress it up as a transport failure - telling the user their backend
        # is unreachable when they simply stopped. Re-raise to the one CLI handler.
        raise
    except Exception as exc:
        # Can't reach the backend yet - almost always "no credentials on a fresh
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
        if result["title_renderable"] is False:
            print(f"  [FAIL] the phone will not render that title - {result['title_detail']}")
        elif result["title_renderable"]:
            print("  [ok  ] title is renderable on the phone")
        # The notification leg is best-effort by design, so it does not fail the
        # verification; the two delivery legs are the actual contract.
        if not (result["dictation_delivered"] and result["reply_delivered"]):
            print("verification FAILED - a message did not complete the round trip.")
            return 2
        print("verified: a message makes the round trip.")

    if not guided:
        return 0

    # Steps 3-5 run only on the guided path. `--verify` on its own stays exactly
    # what it was, so a scripted check does not suddenly start asking questions.
    onboard.step_notifications(cfg, config_path=path, ask=_ask_line, show=print)
    cfg = onboard.reload_config(path)

    print("")
    print("[4/5] A test message")
    print("        next: your phone prompt")
    if not do_verify:
        if onboard._yes(_ask_line, "  Send a real message round-trip now?"):
            result = verify(cfg, t)
            ok = result["dictation_delivered"] and result["reply_delivered"]
            print(f"  [{'ok  ' if ok else 'FAIL'}] a message makes the round trip")
            if not ok:
                print("       something is not connected yet - `voice-bridge doctor` says what.")
        else:
            print("  skipped - check it later with:  voice-bridge setup --verify")
    else:
        print("  already verified above.")

    onboard.step_phone_prompt(cfg, ask=_ask_line, show=print)

    print("")
    if onboard._yes(_ask_line, "Start the bridge now?"):
        print("  starting - press Ctrl-C to stop.")
        from .runner import run_command

        return run_command(config_path=str(path))
    onboard.farewell(print, ready=True)
    return 0


_ICLOUD_GUIDE = (
    "iCloud does not allow creating lists over the API, so make them on your phone:",
    "  Reminders -> new list, twice. Any names you like - you will choose them here",
    "  by id, so the names are yours, not ours.",
    "  Suggested: {inbox} (you dictate into) and {outbox} (replies appear in).",
    "",
    "iCloud sync is not instant. When a list does not appear below yet, press r to",
    "refresh until it does.",
)


def settle_selection(
    cfg: Config,
    t: Transport,
    *,
    config_path: Path,
    created: dict[str, str] | None = None,
    ask: Callable[[str], str] = _ask_line,
    show: Callable[[str], None] = print,
    is_tty: Callable[[], bool] | None = None,
) -> int:
    """Choose a list for each role, and record BOTH its id and its real name.

    Nothing is ever inferred from a title. A list is used because someone chose it,
    which is why this asks rather than matching - the point of the whole feature is
    that setting up the lists correctly replaces relying on hardcoded names.

    The name is written alongside the id because the phone prompt shows both, and
    it must show what the list is *actually called*. A name in the config that came
    from a default rather than from the chosen list would be a caption that does
    not match its picture.

    `created` maps a config field to an id the transport just returned from
    `create_list`. Recording that is knowledge, not inference - we made the list a
    moment ago. Only Radicale can create lists.

    Without a terminal nothing is asked: a prompt written to a pipe blocks for
    ever, or reads EOF and takes an answer nobody gave. It warns and returns.
    """
    tty = (is_tty or _is_tty)()
    created = created or {}
    plan = resolve_selection(cfg, t)
    written = 0

    def _record(res, ref: ListRef) -> None:
        """Persist the choice: the id decides, the name is what the phone shows."""
        nonlocal written
        set_value(config_path, res.field, ref.id)
        set_value(config_path, "inbox_list" if res.role == "inbox" else "output_list", ref.name)
        confirm_selection(ref, role=res.role, show=show)
        written += 1

    unresolved = [r for r in plan.resolutions if r.status != "selected"]
    if unresolved and tty and cfg.transport == "icloud" and not created:
        for line in _ICLOUD_GUIDE:
            show(line.format(inbox=cfg.inbox_list, outbox=cfg.output_list))
        show("")

    for res in plan.resolutions:
        if res.status == "selected":
            continue

        if res.field in created:
            ref = next((r for r in t.list_todo_lists() if r.id == created[res.field]), None)
            if ref is not None:
                # Say why nothing was asked: we created this list moments ago and
                # hold the id it returned, so there is nothing to guess at.
                show("  just created this list, so it is chosen for you:")
                _record(res, ref)
                continue

        if not tty:
            what = (
                "the selected list no longer exists"
                if res.status == "stale"
                else "no list is selected"
            )
            show(f"warning: {what} for {res.field}.")
            show("         Nothing can be delivered for this role until you choose one:")
            show("           voice-bridge lists --select")
            continue

        if res.status == "stale":
            show(f"  the selected list no longer exists ({res.current}) - choose a replacement.")

        choice = pick(res, ask=ask, show=show, refresh=t.list_todo_lists)
        if choice.action == "select":
            ref = next(r for r in t.list_todo_lists() if r.id == choice.list_id)
            _record(res, ref)
        else:
            show(
                "  left unselected - nothing will be delivered for this role until you"
                " run `voice-bridge lists --select`."
            )

    return written


def ask_radicale_creation(*, ask: Callable[[str], str], show: Callable[[str], None]) -> bool:
    """Should setup create the Radicale lists, or will the user make them?

    Radicale *can* create lists, but doing it silently would make the two
    transports behave differently for no reason the user chose. Asking unifies
    them: answer "no" and the flow is exactly iCloud's - create the lists
    yourself, refresh, select.
    """
    show("Radicale can create the two lists for you, or you can make them yourself")
    show("in whatever client you use (the same way the iCloud path works).")
    while True:
        try:
            answer = ask("Create them for you? [Y/n]: ").strip().lower()
        except EOFError:
            return True  # the non-interactive default is the helpful one
        if answer in ("", "y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        show("  please answer y or n.")
