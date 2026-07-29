"""Backend-touching commands, as functions that take an already-connected transport.

**Standing principle: no backend calls inline in the CLI handler.** Logic that
lives in `cli.py` is neither error-disciplined nor testable, and that single root
produced four different responses to one backend failure: `lists` and `peek`
raised a traceback, `send` returned a blanket exit 2, and `setup.provision`
reported a genuine authentication failure as "create the lists by hand".

`connected_transport` gives all of them one mapping. The `*_command` functions
accept the transport as an argument (the same dependency-injection shape the
poller uses), so every branch can be driven by an in-memory fake.
"""

from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path
from typing import Callable

from .config import Config, set_value
from .errors import CommandError, raise_command_error
from .factory import make_transport
from .prompting import ask as _ask_line
from .selection import (
    ROLE_DIRECTION,
    ROLE_LABEL,
    ROLE_SELECTED_MARK,
    confirm_selection,
    pick,
    resolve_selection,
)
from .transport import Transport


def connected_transport(
    cfg: Config, *, make: Callable[[Config], Transport] | None = None
) -> Transport:
    """Build a transport and connect it, translating failures into exit codes.

    Exit 1 means "the backend is unavailable or not set up yet"; exit 2 means
    "you must act" (a list that does not exist). Anything unrecognised is
    re-raised with its traceback, because a tidy message for an unknown fault
    hides a bug.

    `make` defaults to the module-level factory but is resolved **at call time**,
    not captured as a default argument: a default binds the function object when
    this module is imported, which would make the seam impossible to intercept by
    patching `commands.make_transport` - the obvious way to substitute a fake.
    """
    t = (make or make_transport)(cfg)
    try:
        t.connect()
    except Exception as exc:
        raise_command_error(exc)  # raises CommandError, or re-raises exc
        raise  # pragma: no cover - unreachable; keeps the type checker honest
    return t


# --------------------------------------------------------------------------- #
# lists
# --------------------------------------------------------------------------- #


def list_command(cfg: Config, t: Transport, *, as_json: bool) -> int:
    """Show every list on the backend, and which ones this spoke actually uses.

    Deleting and recreating a list on the phone leaves same-titled orphans with
    different ids ("ghosts"), so matching by name silently picks one of them.
    When an id is selected, that selection - not the name - decides which row is
    active, and a selection matching nothing is called out rather than failing
    quietly at the next poll.
    """
    selected = {"inbox": cfg.inbox_list_id, "outbox": cfg.output_list_id}

    rows = []
    for ref in t.list_todo_lists():
        # ONLY an id marks a row. The name fallback that used to live here was
        # residue from before selection existed, and it did not merely guess - it
        # OVERWROTE a correct id match, because it ran second. A list explicitly
        # selected as the inbox was relabelled "outbox" purely because its title
        # happened to equal the other role's default name, which is precisely the
        # inversion the user saw on their own screen.
        role = next((box for box, sel in selected.items() if sel and ref.id == sel), "")
        rows.append({"name": ref.name, "id": ref.id, "role": role, "active": bool(role)})

    if as_json:
        # `role` stays "inbox"/"outbox" here: JSON is read by programs, and those
        # are the field names in the config. Only the PROSE moves to the user's seat.
        print(json.dumps(rows))
    else:
        for row in rows:
            mark = (
                f"   <- SELECTED: {ROLE_SELECTED_MARK[str(row['role'])]}" if row["active"] else ""
            )
            print(f"  {row['name']}{mark}\n      id: {row['id']}")
        if not rows:
            print("  (no lists)")

    for box, sel in selected.items():
        if sel and not any(r["id"] == sel for r in rows):
            print(
                f"  warning: {box}_list_id selects {sel!r}, which no longer exists - "
                f"run `voice-bridge lists --select` to choose again, or clear it to "
                f"match by name."
            )
    return 0


# --------------------------------------------------------------------------- #
# peek
# --------------------------------------------------------------------------- #


def peek_command(
    cfg: Config,
    t: Transport,
    *,
    box: str,
    completed: bool,
    limit: int | None,
    as_json: bool,
) -> int:
    """Show what is sitting in one of the two lists right now.

    `limit=0` means zero. It previously meant "no limit", because the old code
    tested the value for truthiness and 0 is falsy - so asking for nothing
    returned everything.
    """
    name = cfg.inbox_list if box == "inbox" else cfg.output_list
    list_id = cfg.inbox_list_id if box == "inbox" else cfg.output_list_id
    try:
        lst = t.resolve_list(name, list_id)
    except Exception as exc:
        raise_command_error(exc)
        raise  # pragma: no cover - unreachable

    items = t.read_completed(lst) if completed else t.read_incomplete(lst)
    if limit is not None:
        items = items[:limit]

    if as_json:
        print(
            json.dumps(
                [
                    {
                        "id": it.id,
                        "title": it.title,
                        "notes": it.notes,
                        "needs_input": it.needs_input,
                    }
                    for it in items
                ]
            )
        )
        return 0

    if not items:
        print("  (no items)")
        return 0
    for it in items:
        suffix = f" - {it.notes}" if it.notes else ""
        flag = "  [needs input]" if it.needs_input else ""
        print(f"  - {it.title}{suffix}{flag}\n      id: {it.id}")
    return 0


# --------------------------------------------------------------------------- #
# lists --select  - the management door onto the one resolver
# --------------------------------------------------------------------------- #


def _tty() -> bool:
    return sys.stdin.isatty()


_STATE_NOTE = {
    "stale": "the selected list no longer exists",
    "unselected": "nothing selected yet",
}


def _choose_role(plan: object, *, ask: Callable[[str], str], show: Callable[[str], None]):
    """Ask WHICH list to change, and return that resolution (or None to finish).

    This replaces a forced serial walk through every role. Being marched through
    lists you did not come to change is the kind of interaction people abandon
    half-way, and abandoning half-way through a *config editor* leaves exactly the
    ambiguity the feature exists to remove. Changing one thing and leaving is the
    common case, so it is the default path rather than an escape from a queue.
    """
    resolutions = plan.resolutions  # type: ignore[attr-defined]
    show("")
    show("Which list do you want to change?")
    show("Pick a role to change its list; each shows what it uses today.")
    for i, res in enumerate(resolutions, start=1):
        state = res.current if res.current else "nothing selected"
        note = _STATE_NOTE.get(res.status, "")
        suffix = f"   ({note})" if note else ""
        # The caption is `res.name`, which the resolver read from the ACCOUNT by
        # id - never the config's cached copy. That is the whole of the "the name
        # did not update" bug: the id changed and the caption did not.
        caption = f'  "{res.name}"' if res.name else ""
        show(f"  {i}) {ROLE_LABEL[res.role]}  ({ROLE_DIRECTION[res.role]}){caption}")
        show(f"       currently: {state}{suffix}")
    show("")
    show("  d) done - leave everything else unchanged")

    prompt = f"Choose 1-{len(resolutions)}, or d: "
    while True:
        try:
            answer = ask(prompt).strip().lower()
        except EOFError:
            return None
        if answer in ("d", "q", ""):  # `q` and Enter both read as "I am finished"
            return None
        if answer.isdigit() and 1 <= int(answer) <= len(resolutions):
            return resolutions[int(answer) - 1]
        show(f"  not one of the options - choose 1-{len(resolutions)}, or d.")


def _pick_with_retry(
    res,
    *,
    cfg: Config,
    t: Transport,
    ask: Callable[[str], str],
    show: Callable[[str], None],
    is_tty: bool,
):
    """Run the picker, surviving a stall on `r) refresh`. None = give up.

    The refresh is the only network call left inside the picker, and it is the one
    a user reaches for precisely when the account is being slow - they pressed `r`
    because the list had not appeared yet. A traceback there is the worst possible
    answer: it is the moment they were being patient.

    Retrying re-enters `pick`, which redraws from `res.candidates` - so nothing the
    user has done is lost, because at this point they have not decided anything
    yet. The decision itself is protected further down, by never going back to the
    network after it.
    """
    from .transient import offer_retry

    def _refresh() -> list:
        return t.list_todo_lists()

    while True:
        try:
            return pick(res, ask=ask, show=show, refresh=_refresh)
        except Exception as exc:
            if not offer_retry(exc, cfg, ask=ask, show=show, is_tty=lambda: is_tty):
                return None


def select_command(
    cfg: Config,
    t: Transport,
    *,
    config_path: Path,
    ask: Callable[[str], str] = _ask_line,
    show: Callable[[str], None] = print,
    is_tty: Callable[[], bool] | None = None,
) -> int:
    """Choose which list each role uses - keep, change, or clear a selection.

    Opens with the roles and their current state, so you change what you came for
    and leave. It is *management*, not merely disambiguation: it runs even when a
    name is unambiguous, because "change" has to mean change. A selection is the
    only thing standing between a dictation and a same-named ghost, so it must be
    adjustable without hand-editing JSON or looking up a GUID.

    Every decision here comes from `resolve_selection` and is presented by the shared
    `pick` - the same engine and the same component `setup` uses. Two pickers
    would drift apart exactly the way two resolvers would.
    """
    from .transient import offer_retry

    tty = (is_tty or _tty)()
    if not tty:
        # Never prompt a pipe: it blocks for ever, or reads EOF and takes an
        # answer nobody gave. Say what happened and name the fix instead.
        show("error: `lists --select` is interactive and stdin is not a terminal.")
        show("       Run it in a terminal, or set the id directly:")
        show("       voice-bridge config set inbox_list_id <id>   (see `voice-bridge lists`)")
        return 2

    # The opening read. This path had NO transient handling at all - `setup` grew
    # it in batch 2 and this command never did, so a slow account here produced a
    # raw traceback where `setup` would have said "temporary, try again?".
    current = cfg
    while True:
        try:
            refs = t.list_todo_lists()  # the menu; `r` inside the picker re-reads
            break
        except Exception as exc:
            if not offer_retry(exc, current, ask=ask, show=show, is_tty=lambda: tty):
                return 1
    plan = resolve_selection(current, t, refs=refs)
    changed = 0

    while True:
        res = _choose_role(plan, ask=ask, show=show)
        if res is None:  # "done", or the stream ended
            break

        if not res.candidates:
            # An empty account: there is nothing to choose between. Creating lists
            # is `setup`'s job, not a picker's, so say so rather than opening an
            # empty picker.
            show("  this account has no lists - run `voice-bridge setup` first.")
            continue

        if res.status == "stale":
            show(f"  the selected list no longer exists ({res.current}) - choose a replacement.")

        choice = _pick_with_retry(res, cfg=current, t=t, ask=ask, show=show, is_tty=tty)
        if choice is None:  # a blip the user chose not to wait out
            break
        if choice.action == "select":
            value = choice.list_id
            # NO THIRD FETCH. The picker hands back the row the user chose, from
            # the listing they chose it FROM - the role menu and the picker have
            # already read the account twice within seconds, and a third read here
            # bought nothing: it could only agree with what they saw, disagree with
            # it, or - as happened - time out after the decision and lose it.
            #
            # This is not the batch-3 rule inverted. That rule was about re-drawing
            # a SCREEN after changing the world. Resolving an answer against the
            # question it answered is the opposite case: "21" means row 21 of the
            # list in front of them, and any other listing is a different question.
            assert choice.ref is not None  # `select` always carries its row
            ref = choice.ref
            confirm_selection(ref, role=res.role, show=show)
            # The name travels with the id: the phone prompt shows both, and it
            # must show what the list is actually called rather than a default.
            # internal=True: the cached display name is DERIVED state the program
            # owns, not a knob. A person setting it would change a caption and
            # nothing else, which is why it left the settable surface.
            set_value(
                config_path,
                "inbox_list" if res.role == "inbox" else "output_list",
                ref.name,
                internal=True,
            )
        elif choice.action == "clear":
            value = ""
            show("  selection cleared - this list will be matched by name again.")
        else:
            show("  unchanged.")
            continue

        set_value(config_path, res.field, value)
        changed += 1
        # Re-classify against the listing already in hand, so the menu shows the
        # new state immediately. Going back through the one resolver - rather than
        # patching the row by hand - is what keeps this screen honest when the
        # change turns a stale selection into a settled one.
        # The field name is dynamic BY DESIGN: `Resolution.field` is where the
        # role->field mapping lives, and re-deriving it here is exactly the
        # duplication that mapping exists to prevent (UX-1 inverted it once).
        current = dataclasses.replace(current, **{res.field: value})  # type: ignore[arg-type]
        # Re-classify against WHAT THE PICKER SAW, not the opening snapshot and not
        # a fresh fetch. `r) refresh` exists so a list created seconds ago can be
        # chosen, and re-classifying that choice against the inventory from before
        # it existed declared it STALE - the menu announced "the selected list no
        # longer exists" about a list the user had just picked off a refreshed
        # screen. `choice.seen` is that refreshed screen, so the bug stays fixed
        # with no network call and no window in which one can fail.
        refs = choice.seen or refs
        plan = resolve_selection(current, t, refs=refs)

    if changed:
        show("")
        show("config updated. Re-run `voice-bridge vox-prompt` - the prompt carries these")
        show("ids, so the phone must be given the new ones.")
    return 0


__all__ = [
    "CommandError",
    "connected_transport",
    "list_command",
    "peek_command",
    "select_command",
]
