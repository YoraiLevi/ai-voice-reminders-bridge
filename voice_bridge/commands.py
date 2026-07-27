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

import json
import sys
from pathlib import Path
from typing import Callable

from .config import Config, set_value
from .errors import CommandError, raise_command_error
from .factory import make_transport
from .pins import pick, resolve_pins
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
    patching `commands.make_transport` — the obvious way to substitute a fake.
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
    When an id is pinned, that pin — not the name — decides which row is active,
    and a pin that matches nothing is called out rather than failing quietly at
    the next poll.
    """
    pins = {"inbox": cfg.inbox_list_id, "outbox": cfg.output_list_id}
    names = {"inbox": cfg.inbox_list, "outbox": cfg.output_list}

    rows = []
    for ref in t.list_todo_lists():
        role = ""
        active = False
        for box, pin in pins.items():
            if pin:
                if ref.id == pin:
                    role, active = box, True
            elif ref.name == names[box]:
                role, active = box, True
        rows.append({"name": ref.name, "id": ref.id, "role": role, "active": active})

    if as_json:
        print(json.dumps(rows))
    else:
        for row in rows:
            mark = f"  <- active {row['role']}" if row["active"] else ""
            print(f"  {row['name']}{mark}\n      id: {row['id']}")
        if not rows:
            print("  (no lists)")

    for box, pin in pins.items():
        if pin and not any(r["id"] == pin for r in rows):
            print(
                f"  warning: {box}_list_id is pinned to {pin!r}, which is not found — "
                f"run `voice-bridge lists` and re-pin, or clear it to match by name."
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
    tested the value for truthiness and 0 is falsy — so asking for nothing
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
        suffix = f" — {it.notes}" if it.notes else ""
        flag = "  [needs input]" if it.needs_input else ""
        print(f"  - {it.title}{suffix}{flag}\n      id: {it.id}")
    return 0


# --------------------------------------------------------------------------- #
# lists --pin  — the management door onto the one resolver
# --------------------------------------------------------------------------- #


def _tty() -> bool:
    return sys.stdin.isatty()


def pin_command(
    cfg: Config,
    t: Transport,
    *,
    config_path: Path,
    ask: Callable[[str], str] = input,
    show: Callable[[str], None] = print,
    is_tty: Callable[[], bool] | None = None,
) -> int:
    """Manage the list pins interactively — keep, change, or clear each one.

    This is *management*, not merely disambiguation: it runs even when a name is
    unambiguous, because "change" has to mean change. Pins are the only thing
    standing between a dictation and a same-named ghost, so they must be
    adjustable without hand-editing JSON or looking up a GUID.

    Every decision here comes from `resolve_pins` and is presented by the shared
    `pick` — the same engine and the same component `setup` uses. Two pickers
    would drift apart exactly the way two resolvers would.
    """
    if not (is_tty or _tty)():
        # Never prompt a pipe: it blocks for ever, or reads EOF and takes an
        # answer nobody gave. Say what happened and name the fix instead.
        show("error: `lists --pin` is interactive and stdin is not a terminal.")
        show("       Run it in a terminal, or set the id directly:")
        show("       voice-bridge config set inbox_list_id <id>   (see `voice-bridge lists`)")
        return 2

    plan = resolve_pins(cfg, t)
    changed = 0

    for res in plan.resolutions:
        label = "dictations arrive in" if res.role == "inbox" else "replies go out to"
        show("")
        show(f'{res.role}: "{res.name}" — {label} this list')

        if res.status == "missing":
            # Nothing to choose between. Creating a list is `setup`'s job, not a
            # picker's, so say so rather than offering an empty menu.
            show(f'  no list named "{res.name}" exists — run `voice-bridge setup` to create it.')
            continue

        if res.status == "dangling":
            show(f"  the current pin {res.current} no longer exists — pick a replacement.")

        choice = pick(res, ask=ask, show=show)

        if choice.action == "pin":
            set_value(config_path, res.field, choice.list_id)
            show(f"  pinned {choice.list_id}")
            changed += 1
        elif choice.action == "clear":
            set_value(config_path, res.field, "")
            show("  pin cleared — this list will be matched by name again.")
            changed += 1
        else:
            show("  unchanged.")

    if changed:
        show("")
        show("config updated. Re-run `voice-bridge vox-prompt | clip` — the prompt carries")
        show("these ids, so the phone must be given the new ones.")
    return 0


__all__ = [
    "CommandError",
    "connected_transport",
    "list_command",
    "peek_command",
    "pin_command",
]
