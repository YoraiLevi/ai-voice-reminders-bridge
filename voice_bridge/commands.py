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
from typing import Callable

from .config import Config
from .errors import CommandError, raise_command_error
from .factory import make_transport
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


__all__ = ["CommandError", "connected_transport", "list_command", "peek_command"]
