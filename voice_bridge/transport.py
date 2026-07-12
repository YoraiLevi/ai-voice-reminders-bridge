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
    The id is what makes list resolution ghost-safe (duplicate titles disambiguated)."""

    name: str
    id: str


@dataclass(frozen=True)
class Item:
    """One incomplete todo/reminder read from a list."""

    id: str
    title: str
    notes: str = ""


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
        """Incomplete items in the list (completed ones excluded)."""

    @abstractmethod
    def add_todo(self, lst: ListRef, summary: str, notes: str = "") -> str:
        """Create a todo in the list; return its id."""

    @abstractmethod
    def complete(self, lst: ListRef, item_id: str) -> None:
        """Mark an item complete so it is not read again."""


@dataclass
class FakeTransport(Transport):
    """In-memory backend for tests. Lists are held as a LIST (not name-keyed) so it can
    model ghosts — two lists sharing a title but with distinct ids, exactly what
    id-pinning exists to disambiguate. Items are id->[Item]; completed ids are tracked
    so read_incomplete hides them. Deterministic ids."""

    _lists: list[ListRef] = field(default_factory=list)
    _items: dict[str, list[Item]] = field(default_factory=dict)
    _completed: set[str] = field(default_factory=set)
    _seq: int = 0
    connected: bool = False

    def add_list(self, name: str, list_id: str | None = None) -> ListRef:
        ref = ListRef(name=name, id=list_id or f"list-{name.lower().replace(' ', '-')}")
        self._lists.append(ref)
        self._items.setdefault(ref.id, [])
        return ref

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

    def add_todo(self, lst: ListRef, summary: str, notes: str = "") -> str:
        item = Item(id=self._next_id(), title=summary, notes=notes)
        self._items.setdefault(lst.id, []).append(item)
        return item.id

    def complete(self, lst: ListRef, item_id: str) -> None:
        self._completed.add(item_id)
