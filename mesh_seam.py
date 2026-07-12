# /// script
# requires-python = ">=3.11"
# ///
"""mesh_seam.py — the PROPOSED boundary between this Reminders INTERFACE adapter
and a future, decoupled MESH.  (Issue #4; contract owned by issue #3.)

WHY THIS FILE EXISTS
--------------------
Today this repo *is* the mesh: the reliability core (dedup / mark-complete /
cursor / watchdog), multi-agent addressing (the Vox routing table), and the
Radicale substrate are all fused into the phone-facing bridge.  Issue #4 says
this repo should become **one interface implementation** — the iCloud Reminders
/ Vox adapter — that *plugs into* a separate mesh.

This module names the seam the adapter will call through.  It is the single
place a reviewer can look to see "where does the interface stop and the mesh
begin?"  It lets the split be discussed and reviewed **before** any code moves.

STATUS: PROVISIONAL — NOT WIRED, NOT IMPORTED BY ANYTHING.
--------------------------------------------------------
- Nothing in the running bridge imports this file, so adding it CANNOT affect the
  live pollers (`pyicloud_bridge.py`, `reminder_bridge.py`).  It is a design
  artifact, not a dependency.
- The method set below MIRRORS the *proposed* mesh API in issue #3 section 5
  (`join / send / poll / ack / directory`).  That contract is **still being
  decided in #3** (A1 shared-inbox vs A2 sender-partitioned; sign-from-v1 or
  defer; cron vs always-on checkers).  So these signatures are a discussion
  placeholder, deliberately un-final:
    * DO NOT import this into the bridges yet.
    * DO NOT treat these signatures as frozen — #3 may rename/reshape them.
    * The point is the *shape of the boundary*, not the exact types.

When #3 settles the contract, `mesh.py` (a real client) implements this Protocol
against Radicale, and the interface adapter is refactored to depend on THIS
abstraction instead of reaching into CalDAV/pyicloud reliability details itself.
See REFACTOR-PLAN.md for the phased migration and what waits on #3.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


# --- data shapes (PROVISIONAL — final form owned by #3 section 5's envelope) ---

@dataclass(frozen=True)
class MeshMessage:
    """One decoded, verified, not-yet-processed message handed to the adapter.

    PROVISIONAL. #3's envelope carries {v,id,from,to,ts,kind,reply_to,sig}; this
    is the minimal subset the *interface* needs to render a phone line + ack.
    """

    id: str          # stable idempotency key (ULID in #3) — dedupe + ack target
    sender: str      # agent_id of the sender ("manager", "w3", "human", ...)
    to: str          # agent_id of the intended recipient (this adapter's id)
    body: str        # human-facing text the interface renders to the phone
    kind: str = "msg"  # msg | ack | nack | hb


@dataclass(frozen=True)
class Receipt:
    """Result of retiring a message (the ACKSEQ outcome in #3 section 5)."""

    id: str
    acked: bool


@runtime_checkable
class Mesh(Protocol):
    """The boundary the Reminders/Vox interface adapter calls THROUGH.

    Everything on the mesh side of this Protocol (reliability core, addressing,
    Radicale provisioning, cross-agent isolation) is OUT OF SCOPE for this repo
    once the refactor lands — it belongs to the mesh (issue #3).  The interface
    adapter's only job is: translate phone <-> MeshMessage, and drive this loop.

    PROVISIONAL signatures — see the module docstring.  Do not wire until #3.
    """

    def join(self, agent_id: str) -> None:
        """Idempotently ensure this agent's mailbox + directory entry exist.

        The interface adapter joins as one agent (e.g. `human-gw` in #3) — the
        single component bridging the phone into the mesh.  Provisioning of the
        substrate (Radicale users/collections/rights) is the MESH's job, not the
        adapter's; `join` is the adapter's only touch-point to it.
        """
        ...

    def send(self, to: str, body: str, *, kind: str = "msg",
             reply_to: str | None = None) -> str:
        """Deliver `body` to agent `to`; return the message id.

        At-least-once with read-back (no bare-2xx 'sent').  The reliability
        guarantees live in the MESH implementation — the adapter just calls this
        when the phone produces a request ("To Claude" -> send(to=..., body=...)).
        """
        ...

    def poll(self) -> list[MeshMessage]:
        """Return verified, deduped, not-yet-processed messages for this agent.

        The cursor / dedup-ledger / per-item fault isolation that make this safe
        are the MESH's responsibility.  The adapter renders each result to the
        phone (ntfy + output list + native alarm) — that rendering stays HERE.
        """
        ...

    def ack(self, message: MeshMessage) -> Receipt:
        """Retire a message AFTER the interface has delivered it (process-then-ack).

        This is the fix for the live 'completed == processed' bug (#3 section 1):
        the source is retired only once the phone-facing delivery is done, never
        at poll-read.  Ordering/crash-safety (ACKSEQ) is the MESH's to guarantee.
        """
        ...

    def directory(self) -> dict[str, dict]:
        """The live roster: {agent_id: entry}.  Feeds the Vox routing table.

        Today the routing table is authored *into* the interface
        (`vox_instructions.py`).  Post-refactor it is a VIEW the adapter renders
        from the mesh directory — the directory itself is owned by the MESH.
        """
        ...


__all__ = ["Mesh", "MeshMessage", "Receipt"]


if __name__ == "__main__":  # pragma: no cover - this file is a design artifact
    print(__doc__)
