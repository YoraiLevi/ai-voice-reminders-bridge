"""FakeTransport obeys the Transport contract — and is ghost-list-safe by id."""

from __future__ import annotations

import pytest

from voice_bridge.transport import FakeTransport, Transport


def test_fake_is_a_transport(fake_transport):
    assert isinstance(fake_transport, Transport)


def test_connect_and_enumerate(fake_transport):
    fake_transport.connect()
    assert fake_transport.connected
    names = {r.name for r in fake_transport.list_todo_lists()}
    assert names == {"Vox-Message-Inbox", "Vox-Message-Outbox"}


def test_add_read_complete_cycle(fake_transport):
    inbox = fake_transport.resolve_list("Vox-Message-Inbox")
    iid = fake_transport.add_todo(inbox, "buy milk")
    assert [i.title for i in fake_transport.read_incomplete(inbox)] == ["buy milk"]
    fake_transport.complete(inbox, iid)
    assert fake_transport.read_incomplete(inbox) == []  # completed items hidden


def test_resolve_by_id_disambiguates_ghost_titles():
    t = FakeTransport()
    a = t.add_list("Vox-Message-Inbox", list_id="GUID-A")
    t.add_list("Vox-Message-Inbox", list_id="GUID-B")  # ghost: same title
    # by-id resolves the exact one; by-name would be ambiguous (returns the last stored)
    assert t.resolve_list("", list_id="GUID-A") == a


def test_resolve_missing_raises():
    t = FakeTransport()
    with pytest.raises(LookupError):
        t.resolve_list("nope")
    with pytest.raises(LookupError):
        t.resolve_list("", list_id="missing")


def test_read_completed_and_incomplete_split(fake_transport):
    lst = fake_transport.resolve_list("Vox-Message-Inbox")
    a = fake_transport.add_todo(lst, "todo A")
    fake_transport.add_todo(lst, "todo B")
    fake_transport.complete(lst, a)
    assert [i.title for i in fake_transport.read_incomplete(lst)] == ["todo B"]
    assert [i.title for i in fake_transport.read_completed(lst)] == ["todo A"]


def test_add_todo_carries_needs_input(fake_transport):
    lst = fake_transport.resolve_list("Vox-Message-Outbox")
    fake_transport.add_todo(lst, "urgent", needs_input=True)
    assert fake_transport.read_incomplete(lst)[0].needs_input is True


def test_create_list_is_idempotent(fake_transport):
    before = len(fake_transport.list_todo_lists())
    a = fake_transport.create_list("Vox-Message-Inbox")  # already exists
    assert a.name == "Vox-Message-Inbox"
    assert len(fake_transport.list_todo_lists()) == before  # no duplicate
    fake_transport.create_list("Brand New")
    assert len(fake_transport.list_todo_lists()) == before + 1
