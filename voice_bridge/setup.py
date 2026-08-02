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
from .factory import TRANSPORTS, make_transport
from .mailbox import append_line, format_mailbox_line
from .prompting import Cancelled
from .prompting import ask as _ask_line
from .transient import guarded
from .selection import (
    ROLE_LABEL,
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
#:
#: Third element: the closed set of acceptable answers, or None for free text.
#: A prompt that PRINTS its choices and then accepts anything else is making a
#: false claim in the same breath - the human typed `rad` and was told
#: "ACCEPTED - transport = rad".
_PROMPTABLE = (
    ("transport", f"transport ({'/'.join(TRANSPORTS)})", TRANSPORTS),
    ("spoke_name", "this spoke's name", None),
    ("mailbox_dir", "shared mailbox directory", None),
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
    for field, label, choices in _PROMPTABLE:
        if field in preset:
            continue
        current = str(getattr(cfg, field, ""))
        while True:
            try:
                given = _ask(field, label, current)
            except EOFError:  # isatty can lie; treat "no input" as "no answers"
                return answers
            if not given or choices is None or given in choices:
                break
            # RE-ASK, exactly as `[Y/n]` does for junk. The alternative - accept and
            # let the failure surface three steps later - is what turned one typo
            # into a config naming a backend that does not exist.
            print(f"  not one of the choices - type {' or '.join(choices)}")
        if given:
            answers[field] = given
            # Explicit acceptance, program-wide: an answer that vanishes into the
            # next prompt leaves the user unsure whether it registered at all. It is
            # printed only AFTER validation - ACCEPTED on a value we are about to
            # reject is a lie in the one line the user is reading for reassurance.
            print(f"  ACCEPTED - {field} = {given}")
    return answers


#: The selection fields, and the display cache that belongs to each. Kept together
#: because they are one fact - "which list, and what it was called" - and a switch
#: that dropped the id while keeping the name would leave a config claiming a list
#: nobody selected, which is precisely the batch-4 defect.
_SELECTION_FIELDS = (("inbox_list_id", "inbox_list"), ("output_list_id", "output_list"))


def write_config(
    path: Path, *, transport: str, overrides: dict[str, Any] | None = None
) -> list[str]:
    """Merge transport + overrides into the config file (create it if absent).

    Returns lines to show the user - empty unless the transport CHANGED.

    **A transport switch leaves no hybrid behind.** The human switched icloud ->
    radicale mid-setup and the config on disk ended up naming radicale while still
    holding two CloudKit list ids: runbook section 0's dangling-pin scenario,
    performed by the product itself. An id is issued BY a backend and means nothing
    to any other, so carrying it across is not conservative, it is wrong.

    So the ids and their cached names are cleared in the SAME write that changes the
    transport. Not "write transport last" - that leaves the same window one step
    later, and the flow below needs a coherent config to run at all. One write, one
    consistent state, and the invariant is checkable: **a config never holds ids
    from a transport other than its own.**

    CLEARED, never re-resolved - the doctor dangling-pin rule. Re-resolving would
    hand back a different list under a switch verb. The batch-4 gate then refuses to
    finish setup until both roles are chosen again, so the user cannot walk away
    half-switched, and the returned lines say that is coming rather than letting it
    arrive as a surprise.
    """
    data = read_raw(path)
    previous = str(data.get("transport") or "")
    data["transport"] = transport
    data.update(overrides or {})
    now = str(data.get("transport") or "")

    notes: list[str] = []
    if previous and now and now != previous:
        dropped = [id_field for id_field, _ in _SELECTION_FIELDS if data.get(id_field)]
        notes.append(f"transport changed: {previous} -> {now}")
        if dropped:
            for id_field, name_field in _SELECTION_FIELDS:
                data[id_field] = ""
                data[name_field] = ""
            notes.append(
                f"  cleared the {len(dropped)} list selection(s) from {previous} - "
                f"a list id belongs to the backend that issued it, and means nothing to {now}."
            )
            notes.append("  You will choose both lists again on the new transport.")
    write_raw(path, data)
    return notes


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
    """Renderability of the probe's stored title, or (None, why) if we cannot say.

    Only meaningful where the backend stores a CRDT document, so a backend without
    one reports None rather than a false pass - "not applicable" and "fine" must
    not look the same.

    None now covers a second, larger case: the check RAN and could not reach a
    verdict, usually because Apple had not made the fresh record readable yet. That
    is neither a pass nor a failure, and the caller must not dress it as either.
    """
    from .titlelint import check_stored

    svc = getattr(t, "r", None)
    if svc is None:
        return None, "not applicable for this transport"
    verdict = check_stored(svc, item_id)
    if verdict.undetermined:
        return None, verdict.describe()
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


def report_verification(result: dict[str, Any], *, show: Callable[[str], None] = print) -> int:
    """Print the legs and decide the verdict. 0 verified, 2 failed.

    ONE implementation, because there are now two doors onto it - `verify` the verb
    and `setup --verify` - and the rule this codebase keeps re-learning is that a
    fact stated in two places will eventually disagree with itself. The specific
    disagreement to avoid here is not hypothetical: this block already printed
    `[FAIL]` and `verified:` in the same breath once.
    """
    for leg, ok in (
        ("dictation reached the mailbox", result["dictation_delivered"]),
        ("reply reached the outbox list", result["reply_delivered"]),
    ):
        show(f"  [{'ok  ' if ok else 'FAIL'}] {leg}")

    # THE BANNER IS BEST-EFFORT, SO IT DOES NOT WEAR A CONTRACT LEG'S DRESS.
    #
    # Batch 9 ruled this for the title leg and I applied it only there, leaving the
    # banner printing `[FAIL] notification sent` four lines above `verified:` - the
    # very shape the human called out. Walking the Radicale sim put it back on screen
    # and made the inconsistency my own.
    #
    # `PushResult` already had the three states this needs: not configured is not a
    # failure at all, and a genuine send failure is a warning, because a message that
    # arrived without its doorbell still arrived.
    if result["banner_sent"]:
        show("  [ok  ] notification sent")
    elif str(result["banner_detail"]).startswith("no_topic"):
        show("  [ -- ] notification not configured - no banners will be sent")
        show(f"         {result['banner_detail']}")
    else:
        show("  [warn] notification not sent - the message still arrived")
        show(f"         {result['banner_detail']}")

    # THREE STATES, AND THE MIDDLE ONE IS THE POINT.
    #
    # A live run printed "[FAIL] the phone will not render that title" and then,
    # four lines later, "verified: a message makes the round trip." Both came from
    # here. Whatever the truth was, that output cannot be right - and the batch-9
    # ruling applies: if a leg is best-effort its failure must not wear a contract
    # leg's dress, and if it is NOT best-effort then `verified` must not print over
    # it.
    #
    # So the leg is split by what we actually know. A CONFIRMED bad stored title is
    # a real user-visible failure (LIVE-5: the reminder arrives blank) and it blocks.
    # "Could not check" - a fresh record Apple has not served yet - is reported as
    # unknown and blocks nothing, because a diagnostic that cannot reach a verdict
    # has not found a defect.
    if result["title_renderable"] is False:
        show(f"  [FAIL] the phone will not render that title - {result['title_detail']}")
    elif result["title_renderable"]:
        show("  [ok  ] title is renderable on the phone")
    elif result["title_detail"]:
        show(f"  [ ?  ] title not confirmed - {result['title_detail']}")
        show("         the message itself arrived; this check is about the title only.")

    # The notification leg is best-effort by design, so it does not fail the
    # verification; the two delivery legs are the actual contract, and a title we
    # PROVED unrenderable joins them - it is a message the phone cannot show.
    if not (result["dictation_delivered"] and result["reply_delivered"]):
        show("verification FAILED - a message did not complete the round trip.")
        return 2
    if result["title_renderable"] is False:
        show("verification FAILED - the round trip works, but the title will not render.")
        return 2
    show("verified: a message makes the round trip.")
    return 0


def verify_command(cfg: Config, connect: Callable[[Config], Transport]) -> int:
    """The round-trip check, alone. No config written, no questions, no ceremony.

    Ordered after a phone dictation: *"What is the purpose of setup --verify? It
    seems unclear and confusing."* It was doing what it said - a setup, with a
    verification inside it - and the name promised the reverse. Someone asking
    "does my bridge still work?" answered five guided steps to reach one probe.

    So the check is its own verb. `setup` keeps its embedded test step, because
    proving the round trip is a legitimate part of installing; what it loses is the
    claim to be the only way to ask. This is deliberately NOT a `doctor` flag:
    doctor reports, and this WRITES - it creates a probe reminder in each list and
    completes it afterwards. A verb that mutates should not hide inside one that
    inspects.

    `connect` is a CALLABLE, not a transport, so nothing authenticates until the
    offline precondition passes. The first version took a connected transport and
    the CLI built it at the call site - so running `verify` with no lists selected
    logged into the account and THEN said it could not check anything. That is the
    batch-4 ordering rule, which `run` already obeys: a check that is cheap and
    local goes before any side effect, and reaching a network at all is one.
    """
    unsettled = missing_roles(cfg)
    if unsettled:
        for line in missing_roles_message(unsettled):
            print(line)
        return 2

    t = connect(cfg)
    result = guarded(lambda: verify(cfg, t), cfg, ask=_ask_line, is_tty=_is_tty)
    if result is None:  # a stall the user chose not to wait out
        print("verification not completed. Try again with:  voice-bridge verify")
        return 1
    return report_verification(result)


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
    transport: str | None = None,
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
    existing = load_config(path) if path.exists() else load_config(None)
    if not as_json:
        preset.update(prompt_fields(existing, preset=preset))

    # WHICH TRANSPORT, IN PRECEDENCE ORDER - and `None` means nobody said.
    #
    # `--transport` used to default to "icloud", so a flag nobody typed reached here
    # as an explicit answer and stamped iCloud over a radicale config: a working
    # install converted by a bare `voice-bridge setup`, and - once batch 11 correctly
    # read that as a transport switch - its two list pins cleared with it. Section 26
    # says a request is not a decision; a flag's DEFAULT is not even a request.
    #
    # So: what the person just answered, else what they passed explicitly, else what
    # the file already decided, else the first-run default. Only the last of those is
    # ours to choose, and only when nothing else exists.
    requested = str(preset.get("transport") or transport or existing.transport or TRANSPORTS[0])

    for line in write_config(path, transport=requested, overrides=preset):
        print(line)
    cfg = load_config(path)

    # FROM HERE ON, `cfg.transport` IS THE ONLY ANSWER TO "WHICH BACKEND".
    #
    # The parameter is a REQUEST (from `--transport`, defaulting to icloud); the file
    # is the DECISION, and the preamble above may have overridden the request. They
    # disagreed, and every step keyed off the parameter was wrong while every step
    # keyed off the config was right - which is the whole diagnosis of the transport
    # switch. The user answered "radicale", the file said radicale, `make_transport`
    # built the CalDAV adapter, and this function still walked them through an Apple
    # ID login and skipped the radicale server check, because two lines here were
    # reading a stale local variable. A fact stated in two places will eventually
    # disagree with itself; the fix is to stop stating it twice.
    #
    # `test_no_step_in_run_setup_branches_on_the_transport_PARAMETER` enumerates this
    # rather than trusting anyone to remember it.
    del transport

    if guided and cfg.transport == "icloud":
        # Offered INLINE rather than named in a footnote: knowing the command
        # exists is not the same as being walked to it, and the gap between those
        # is where a first run stalls.
        def _login() -> int:
            from .login import icloud_login

            return icloud_login(cfg)

        outcome = onboard.step_credentials(cfg, ask=_ask_line, show=print, login=_login)
        if outcome != onboard.OK:
            onboard.farewell(print, ready=False)
            # A DECLINE IS 0; AN OBSTACLE IS 2 - the same rule the unreachable-server
            # branch below states, finally reaching its last surface. Saying no to
            # "set them up now?" is a choice the run honoured; a login that FAILED is
            # not, and a script cannot tell them apart from one exit code.
            return 0 if outcome == onboard.DECLINED else 2
        cfg = onboard.reload_config(path)

    if cfg.transport == "radicale":
        from . import server as server_mod

        if guided and onboard.step_radicale_credentials(cfg, show=print) != onboard.OK:
            onboard.farewell(print, ready=False)
            # Always 2 here: this step asks nothing, so there is nothing to decline.
            # Exiting 0 meant a scripted install of a radicale spoke with NO CREDENTIALS
            # AT ALL reported success - found by a doc reviewer reading for a different
            # defect entirely.
            return 2

        if not server_mod.is_reachable(server_mod.client_url(cfg)):
            # NOT the same message as the credentials step above, because it is not
            # the same state. Reaching here means the account exists (that step
            # passed) and the process is simply not running - so `init`, which
            # refuses to overwrite credentials anyway, is the wrong instruction.
            # One remedy per diagnosis; a message that names a remedy is a claim
            # about the cause.
            print(
                f"config written. The server is configured but not answering at "
                f"{server_mod.client_url(cfg)}."
            )
            print("Start it, then re-run setup:")
            print("  voice-bridge radicale-server start --background")
            # 2, not 0. AN OBSTACLE IS 2; A DECLINE IS 0.
            #
            # Batch 15 fixed exit 0 on a permanent auth failure and left this neighbour
            # alone - the third time this arc that one ruling reached half its surface
            # (the title leg without the banner leg, F5 without the HTTPS case, auth
            # without reachability). A user whose server is down did not choose that, and
            # the two lines above tell them to go and do something, which is exactly what
            # the contract's 2 means. A user who SKIPS a step did choose it, so the
            # declined-step paths keep returning 0.
            return 2

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
                print("== YOUR LISTS ==")
                print("   pick the two lists that carry messages between phone and PC")
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
                # ONE implementation, shared with `lists --select`. That command
                # had no transient path at all until a crash mid-pick proved the
                # gap, and a second wording here would have been the next drift.
                from .transient import offer_retry

                if offer_retry(exc, cfg, ask=_ask_line, show=print, is_tty=_is_tty):
                    cfg = load_config(path)  # keep every choice already made
                    continue
                print("Re-run when you are ready:  voice-bridge setup")
                return 0
            if type(exc).__name__ in {"ICloudError", "CredsError"}:
                # TRANSPORT-AWARE, and exit 2 rather than 0.
                #
                # It printed "Next: voice-bridge icloud-login" to a user on a self-hosted
                # CalDAV server ("# icloud-login???" was their comment) and then exited 0 -
                # so a scripted caller saw success for a run that had authenticated to
                # nothing. A permanent auth failure is the exit-code contract's "2 = you
                # must act"; only the transient branch above may end quietly.
                from .errors import auth_advice

                print(f"config written. Could not authenticate: {exc}")
                for line in auth_advice(cfg.transport, cfg.creds_env):
                    print(line)
                print("Then re-run:  voice-bridge setup")
                return 2
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
        result = guarded(lambda: verify(cfg, t), cfg, ask=_ask_line, is_tty=_is_tty)
        if result is None:  # a stall the user chose not to wait out
            print("verification not completed. Try again with:  voice-bridge verify")
            return 1
        rc = report_verification(result)
        if rc != 0:
            return rc

    if not guided:
        return 0

    # Steps 3-5 run only on the guided path. `--verify` on its own stays exactly
    # what it was, so a scripted check does not suddenly start asking questions.
    onboard.step_notifications(cfg, config_path=path, ask=_ask_line, show=print)
    cfg = onboard.reload_config(path)

    if do_verify:
        print("")
        print("== A TEST MESSAGE ==")
        print("   send one message the whole way round and check it")
        print("  already verified above.")
    else:
        onboard.step_test_message(
            cfg,
            ask=_ask_line,
            show=print,
            run_verify=lambda: guarded(lambda: verify(cfg, t), cfg, ask=_ask_line, is_tty=_is_tty),
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

    # SAY WHAT IS ALREADY CHOSEN, like the steps either side of it. Steps 1 and 3
    # report "already configured: <path>" on a re-run; this one printed its header
    # and then nothing at all, so the only step whose state the user actually cares
    # about was the only silent one. The names come from the resolver, which reads
    # them from the account by id - so this doubles as a check that the selection
    # still points at something real.
    for res in plan.resolutions:
        if res.status == "selected":
            show(f"  already chosen - {ROLE_LABEL[res.role]}: {res.name}  [{res.current}]")

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
            # `res.candidates` is the inventory the plan was built from, one call
            # up. Re-fetching to find a list we created seconds ago in the same
            # function is the third-fetch shape again, and `resolve_list` answers
            # from the cache anyway.
            ref = next((r for r in res.candidates if r.id == created[res.field]), None)
            if ref is None:
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
            # The SAME ruling as `lists --select`: the picker hands back the row
            # the user chose, so there is nothing to look up. Fetching again here
            # would re-ask a question that was already answered on screen, and
            # would do it in the one window where a stall costs the answer.
            assert choice.ref is not None  # `select` always carries its row
            _record(res, choice.ref)
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
    # The old second line read "in whatever client you use (the same way the iCloud
    # path works)". The human's verdict, verbatim: "this doesn't make any sense" - and
    # they were right twice. "Whatever client you use" assumes they have one, and the
    # parenthesis explains this path by comparing it to a path they may never have
    # taken. An explanation that requires knowing the other branch is not an
    # explanation.
    show("This server can create the two lists for you now.")
    show("Or say no and make them yourself in any CalDAV app connected to it, then")
    show("come back and choose them here.")
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
