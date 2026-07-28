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
from .errors import CommandError, is_transient
from .factory import make_transport
from .mailbox import append_line, format_mailbox_line
from .prompting import Cancelled
from .prompting import ask as _ask_line
from .selection import (
    confirm_selection,
    missing_roles,
    missing_roles_message,
    pick,
    resolve_selection,
)
from .transport import ListRef, NotSupportedError, Transport

#: Fields worth asking about on a guided run - the ones people actually change.
#: Everything else has a sensible default and can be set later with `config set`.
#: The list-NAME questions were removed here. They were asked before selection
#: existed, when a name was how a list got found; now the picker chooses by id and
#: writes the chosen list's REAL name back to the config. So the answers were
#: overwritten minutes later by the same run - the user typed something, watched it
#: be ignored, and had no way to know that was correct behaviour rather than a bug.
_PROMPTABLE = (
    ("transport", "transport (icloud/radicale)"),
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
            # Explicit acceptance, program-wide: an answer that vanishes into the
            # next prompt leaves the user unsure whether it registered at all.
            print(f"  ACCEPTED - {field} = {given}")
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
        #
        # The NAME to create comes from `SUGGESTED_NAMES`, not from the config's
        # cached display name - which is empty until something is selected, and is
        # a record of what you chose rather than an instruction about what to make.
        for field, name in (
            ("inbox_list_id", cfg.inbox_list or SUGGESTED_NAMES["inbox"]),
            ("output_list_id", cfg.output_list or SUGGESTED_NAMES["outbox"]),
        ):
            ref = t.create_list(name)
            if created is not None:
                created[field] = ref.id
        return [
            "Radicale: the two lists are created / verified.",
            "They sync to the phone via the shared CalDAV account - no phone step.",
        ]
    # iCloud has nothing to provision - it cannot create lists over the API - so
    # this call only proves the account is reachable. Auth and network failures
    # propagate as typed CommandErrors and the caller classifies them.
    #
    # It used to probe `resolve_list(name, id)` for both roles and then print either
    # "create TWO lists named EXACTLY <defaults>" or "iCloud: both lists visible
    # (<names>)". Both are pre-selection-era residue, and both became WRONG the day
    # selection moved to ids: the first tells a user with two perfectly good lists
    # to go make differently-named ones, and the second asserts BY NAME the exact
    # thing this feature exists to stop asserting by name. What really happens next
    # is the picker over the full inventory, with `_ICLOUD_GUIDE` above it when the
    # account has no lists yet. One place decides, not two.
    t.connect()
    return []


#: Names to CREATE or SUGGEST - never a claim about what you have.
#:
#: They were the config's defaults for the two display-name fields, which meant a
#: fresh install already "knew" two list names nobody had chosen. Kept here because
#: Radicale genuinely has to name the lists it creates, and the iCloud guidance has
#: to suggest something; separated from the config so a suggestion can never be
#: mistaken for a selection.
SUGGESTED_NAMES = {"inbox": "Vox-Message-Outbox", "outbox": "Vox-Message-Inbox"}

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

    # A TEST THAT CANNOT RUN MUST REFUSE, NOT PASS. With a role unselected this
    # resolved by NAME - against the fabricated default the config used to carry -
    # and on a real account that name matched a leftover list from an earlier
    # setup. So the probe was written, read back, and reported `[ok] machine round
    # trip`, while the user was told to look in a list the bridge would never poll.
    # Every layer was honest about what it did; none of them was asked whether the
    # thing it did meant anything.
    missing = missing_roles(cfg)
    if missing:
        raise CommandError(2, "\n       ".join(missing_roles_message(missing)))

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


def _report_transient(exc: Exception, cfg: Config) -> None:
    """Say what failed, in enough detail to act on, and log it.

    Asked for directly: *"log what happened? it took a really long time? fix?
    investigate?"*. Three separate complaints, and the middle one is the important
    one - a failure with no duration and no exception class is indistinguishable
    from the program hanging, so the user cannot tell whether to wait or to quit.

    The exception CLASS is named rather than only its message, because "Request
    failed" is what the library says for every network fault and it identifies
    nothing. Elapsed time is on the logger, not printed: it matters when you are
    diagnosing and is noise when you are not.
    """
    from . import log as log_mod

    log_mod.get().warning(
        "transient transport failure during setup: %s: %s (icloud_timeout=%ss)",
        type(exc).__name__,
        exc,
        cfg.icloud_timeout,
    )
    print(f"config written, credentials fine. The transport did not answer: {exc}")
    print(f"That is a temporary failure, not a setup problem ({type(exc).__name__}).")
    print(f"Calls give up after {cfg.icloud_timeout:g}s; -v logs the detail, --log-file keeps it.")


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

    # THE RETRY LOOP IS HERE, around the lists step alone.
    #
    # It used to be a recursive `run_setup(...)`, which the transcript showed for
    # what it was: answering "Try again now?" replayed the preamble, re-walked the
    # credentials step, and reopened the picker from scratch. The batch-2 promise
    # was an in-place retry OF THE FAILING OPERATION, and a re-entry from the top
    # is not that - it still costs the whole flow, just politely.
    #
    # Looping here keeps everything already settled: the config is written, the
    # credentials step is done, and any role already picked stays picked, because
    # `cfg` is re-read from the file each pass.
    while True:
        try:
            # Constructed INSIDE the try. An adapter's constructor can fail for the
            # same reasons its calls can - unreadable credentials, a missing
            # optional dependency - and a failure one line above the handler that
            # exists to classify it escapes as a traceback instead.
            t = make_transport(cfg)
            created: dict[str, str] = {}
            # Radicale CAN create the lists, but doing so silently would make the
            # two transports behave differently for no reason the user picked.
            # Asking unifies them: answer no and the flow is exactly iCloud's.
            if guided:
                print("")
                print("[2/5] Your lists")
                print("        next: notifications")
            auto = True
            if cfg.transport == "radicale" and _is_tty():
                auto = ask_radicale_creation(ask=_ask_line, show=print)
            for line in provision(cfg, t, created=created if auto else None, create=auto):
                print(line)
            # Choose the lists NOW. Nothing is inferred from a title: either we
            # just created it and know its id, or the user is asked.
            if settle_selection(cfg, t, config_path=path, created=created):
                cfg = load_config(path)
            break
        except NotSupportedError as exc:
            print(f"error: {exc}")
            return 2
        except Cancelled:
            # This block contains interactive prompts (list creation, selection),
            # so a Ctrl-C arrives as an exception like any other and the broad
            # handler below would dress it up as a transport failure - telling the
            # user their backend is unreachable when they simply stopped. Re-raise
            # to the one CLI handler.
            raise
        except Exception as exc:
            # THE ADVICE MUST MATCH THE DIAGNOSIS. These two failures were
            # collapsed into one message that always said "run icloud-login", and a
            # live run hit the wrong half: a transient iCloud hiccup AFTER a
            # successful login and a successful first pick was answered with
            # "Request failed / Next: voice-bridge icloud-login". The credentials
            # were fine. Re-logging in fixes nothing, and being sent to re-enter
            # working credentials teaches you to distrust the next instruction too.
            #
            # Third appearance of this class (after "create the lists by hand" for
            # a bad password, and provision's name-based guidance), so it is named
            # plainly: a message that names a REMEDY is a claim about the CAUSE.
            if is_transient(exc):
                _report_transient(exc, cfg)
                if _is_tty() and onboard._yes(_ask_line, "  Try again now?"):
                    cfg = load_config(path)  # keep every choice already made
                    continue
                print("Re-run when you are ready:  voice-bridge setup")
                return 0
            if type(exc).__name__ in {"ICloudError", "CredsError"}:
                print(f"config written. Could not authenticate: {exc}")
                print("Next:  voice-bridge icloud-login    then re-run:  voice-bridge setup")
                return 0
            raise

    if as_json:
        return report(cfg, t, as_json=True)

    # BOTH ROLES ARE A HARD REQUIREMENT from here on, ruled after a run that
    # skipped one and was told "You are set up. Start the bridge with: voice-bridge
    # run" - which `run` then correctly refused, one command later. Setup made a
    # claim the very next command disproved.
    #
    # Everything below needs a list that is actually being polled: the round trip
    # has nowhere to send, and the phone prompt would carry a name nobody chose.
    # `s)` stays offered in the picker, because someone may genuinely need to go
    # make a list on their phone - but skipping leaves the install INCOMPLETE, and
    # the closing lines say so instead of congratulating them.
    unsettled = missing_roles(cfg)
    if unsettled:
        print("")
        print("Setup stopped here - it cannot go further without both lists.")
        for line in missing_roles_message(unsettled):
            print(f"  {line}")
        print("  Then re-run:  voice-bridge setup")
        if guided:
            onboard.farewell(print, ready=False)
        return 2

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

    if do_verify:
        print("")
        print("[4/5] A test message")
        print("        next: your phone prompt")
        print("  already verified above.")
    else:
        onboard.step_test_message(
            cfg,
            ask=_ask_line,
            show=print,
            run_verify=lambda: verify(cfg, t),
            probe=_PROBE,
        )

    onboard.step_phone_prompt(cfg, ask=_ask_line, show=print)

    print("")
    # The default is stated in words as well as in [Y/n]. Starting the bridge is
    # the one answer here with a consequence you cannot see from the prompt - it
    # takes over the terminal - so what Enter does gets said out loud.
    if onboard._yes(_ask_line, "Start the bridge now? (Enter = yes, start it)"):
        print("  starting - press Ctrl-C to stop.")
        from .runner import run_command

        return run_command(config_path=str(path))
    onboard.farewell(print, ready=True)
    return 0


_ICLOUD_GUIDE = (
    "iCloud does not allow creating lists over the API, so make them on your phone:",
    "  Reminders -> new list, twice. Any names you like; you choose them here by id.",
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
        # internal=True: the cached display name is DERIVED state the program
        # owns, not a knob. A person setting it would change a caption and
        # nothing else, which is why it left the settable surface.
        set_value(
            config_path,
            "inbox_list" if res.role == "inbox" else "output_list",
            ref.name,
            internal=True,
        )
        confirm_selection(ref, role=res.role, show=show)
        written += 1

    unresolved = [r for r in plan.resolutions if r.status != "selected"]
    if unresolved and tty and cfg.transport == "icloud" and not created:
        for line in _ICLOUD_GUIDE:
            show(
                line.format(
                    inbox=SUGGESTED_NAMES["inbox"],
                    outbox=SUGGESTED_NAMES["outbox"],
                )
            )
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
