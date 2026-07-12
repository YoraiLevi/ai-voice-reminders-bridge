"""Integration: CalDAVTransport against a real embedded Radicale server."""

from __future__ import annotations

import pytest

from voice_bridge.caldav import CalDAVTransport, CredsError
from voice_bridge.transport import NotSupportedError


def test_create_add_read_complete_roundtrip(radicale_config):
    t = CalDAVTransport(radicale_config, native_alarm=False)
    t.connect()

    ref = t.create_list("Vox-Message-Inbox")
    assert ref.name == "Vox-Message-Inbox"
    # idempotent re-create → same list, no duplicate
    n_before = len(t.list_todo_lists())
    ref2 = t.create_list("Vox-Message-Inbox")
    assert ref2.id == ref.id
    assert len(t.list_todo_lists()) == n_before

    uid = t.add_todo(ref, "buy milk", "2%")
    assert "buy milk" in {i.title for i in t.read_incomplete(ref)}

    t.complete(ref, uid)
    assert "buy milk" not in {i.title for i in t.read_incomplete(ref)}
    assert "buy milk" in {i.title for i in t.read_completed(ref)}


def test_resolve_missing_list_raises(radicale_config):
    t = CalDAVTransport(radicale_config)
    t.connect()
    with pytest.raises(LookupError):
        t.resolve_list("Does Not Exist")


def test_bad_creds_raise(radicale_config):
    radicale_config.creds_env.write_text("ICLOUD_APPLE_ID=test\n", encoding="utf-8")  # no password
    t = CalDAVTransport(radicale_config)
    with pytest.raises(CredsError):
        t.connect()


def test_create_list_refuses_icloud(radicale_config):
    radicale_config.creds_env.write_text(
        "ICLOUD_APPLE_ID=x\nICLOUD_APP_PASSWORD=y\nICLOUD_CALDAV_URL=https://caldav.icloud.com/\n",
        encoding="utf-8",
    )
    t = CalDAVTransport(radicale_config)
    with pytest.raises(NotSupportedError):
        t.create_list("Anything")
