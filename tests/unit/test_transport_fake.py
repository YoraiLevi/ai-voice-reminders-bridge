"""FakeTransport obeys the Transport contract — and is ghost-list-safe by id."""

from __future__ import annotations

import pytest

from voice_bridge.transport import FakeTransport, ListRef, Transport


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
