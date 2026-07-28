"""The Transport abstraction — the ~5 backend ops that actually differ between iCloud
and Radicale — plus an in-memory FakeTransport for tests.

Everything else (mailbox contract, dedupe, ntfy, the poll/drain loop) is shared and
transport-agnostic; it talks only to this interface. This is what lets the whole spoke
be tested without a real backend, and gives the iCloud path behavioral coverage without
an Apple account.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ListRef:
    """A todo list as the backend sees it — name plus a stable id (GUID / record id).
    The id is what makes list resolution ghost-safe (duplicate titles disambiguated).

    The trailing fields are OPTIONAL display metadata, used to tell same-named lists
    apart in the picker. They are deliberately the things a user can SEE on their
    phone, so a row can be matched to the list they recognise — and they ride along
    in data the backend already returned, so showing them costs no extra call.

    There is no date field because pyicloud 2.6.5's RemindersList has none
    (`id, title, color, count, badge_emblem, sorting_style, is_group, reminder_ids,
    guid, record_change_tag`). A plausible "last modified" column would be a
    fabrication, which is precisely what pinning exists to eliminate.
    """

    name: str
    id: str
    count: int | None = None
    color: str = ""
    badge_emblem: str = ""
    is_group: bool = False


@dataclass(frozen=True)
class Item:
    """One todo/reminder read from a list."""

    id: str
    title: str
    notes: str = ""
    needs_input: bool = False


class Transport(ABC):
    """The backend contract. Implementations: ICloudTransport, CalDAVTransport, and the
    FakeTransport below. Only these ops touch the backend; nothing else does."""

    @abstractmethod
    def connect(self) -> None:
        """Authenticate / establish a session. Idempotent."""

    @abstractmethod
    def list_todo_lists(self) -> list[ListRef]:
        """Every VTODO-capable list (name + id) — powers `lists` and ghost detection."""

    @abstractmethod
    def resolve_list(self, name: str, list_id: str = "") -> ListRef:
        """Resolve a list by id when given (ghost-safe), else by exact name.
        Raises LookupError if not found."""

    @abstractmethod
    def read_incomplete(self, lst: ListRef) -> list[Item]:
        """Incomplete items in the list (completed ones excluded) — the poll hot path."""

    @abstractmethod
    def read_completed(self, lst: ListRef) -> list[Item]:
        """Completed items — observability only (`peek --completed`); the poller never
        calls this."""

    @abstractmethod
    def add_todo(
        self, lst: ListRef, summary: str, notes: str = "", *, needs_input: bool = False
    ) -> str:
        """Create a todo in the list; return its id. `needs_input` requests a native
        alarm (the decision differs per backend, so it crosses the seam here)."""

    @abstractmethod
    def complete(self, lst: ListRef, item_id: str) -> None:
        """Mark an item complete so it is not read again."""

    @abstractmethod
    def create_list(self, name: str) -> ListRef:
        """Create a VTODO-capable list. Backends that forbid it (iCloud) raise
        NotSupportedError."""


class NotSupportedError(RuntimeError):
    """A transport was asked to do something its backend forbids (e.g. iCloud list
    creation over CalDAV)."""


@dataclass
class FakeTransport(Transport):
    """In-memory backend for tests. Lists are held as a LIST (not name-keyed) so it can
    model ghosts — two lists sharing a title but with distinct ids, exactly what
    id-pinning exists to disambiguate. Items are id->[Item]; completed ids are tracked
    so read_incomplete hides them. Deterministic ids."""

    _lists: list[ListRef] = field(default_factory=list)
    #: Lists that exist but are not visible yet — the create-lag knob. iCloud sync
    #: is not instant, so a list the user just made on their phone is absent from
    #: the next read. `reveal()` is the test's stand-in for sync catching up, which
    #: is what makes the picker's refresh loop drivable without a real account.
    _unsynced: list[ListRef] = field(default_factory=list)
    _items: dict[str, list[Item]] = field(default_factory=dict)
    _completed: set[str] = field(default_factory=set)
    _seq: int = 0
    connected: bool = False

    def add_list(self, name: str, list_id: str | None = None, **meta: object) -> ListRef:
        ref = ListRef(
            name=name,
            id=list_id or f"list-{name.lower().replace(' ', '-')}",
            **meta,  # type: ignore[arg-type]
        )
        self._lists.append(ref)
        self._items.setdefault(ref.id, [])
        return ref

    def add_unsynced_list(self, name: str, list_id: str | None = None, **meta: object) -> ListRef:
        """A list that exists on the device but has not synced yet."""
        ref = ListRef(name=name, id=list_id or f"list-{name}", **meta)  # type: ignore[arg-type]
        self._unsynced.append(ref)
        return ref

    def reveal(self) -> None:
        """Sync catches up: everything created on-device is now visible."""
        self._lists.extend(self._unsynced)
        for ref in self._unsynced:
            self._items.setdefault(ref.id, [])
        self._unsynced.clear()

    def _next_id(self) -> str:
        self._seq += 1
        return f"item-{self._seq}"

    # --- Transport interface ------------------------------------------------
    def connect(self) -> None:
        self.connected = True

    def list_todo_lists(self) -> list[ListRef]:
        return list(self._lists)

    def resolve_list(self, name: str, list_id: str = "") -> ListRef:
        if list_id:
            for ref in self._lists:
                if ref.id == list_id:
                    return ref
            raise LookupError(f"no list with id {list_id!r}")
        matches = [ref for ref in self._lists if ref.name == name]
        if not matches:
            raise LookupError(f"no list named {name!r}")
        return matches[0]  # by-name returns the first; ghosts need id to disambiguate

    def read_incomplete(self, lst: ListRef) -> list[Item]:
        return [it for it in self._items.get(lst.id, []) if it.id not in self._completed]

    def read_completed(self, lst: ListRef) -> list[Item]:
        return [it for it in self._items.get(lst.id, []) if it.id in self._completed]

    def add_todo(
        self, lst: ListRef, summary: str, notes: str = "", *, needs_input: bool = False
    ) -> str:
        item = Item(id=self._next_id(), title=summary, notes=notes, needs_input=needs_input)
        self._items.setdefault(lst.id, []).append(item)
        return item.id

    def complete(self, lst: ListRef, item_id: str) -> None:
        """Honour the contract: not-found RAISES.

        This used to add ANY id to the completed set, including one that was never
        there — so the fake certified a poller that silently lost completions. A
        double must not be more forgiving than the thing it stands for.
        """
        if not any(it.id == item_id for it in self._items.get(lst.id, [])):
            raise LookupError(f"no item {item_id!r} in list {lst.name!r}")
        self._completed.add(item_id)

    def create_list(self, name: str) -> ListRef:
        existing = [r for r in self._lists if r.name == name]
        return existing[0] if existing else self.add_list(name)
