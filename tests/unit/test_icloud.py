"""iCloud transport: it maps the pyicloud shape correctly (with a fake service, no
network). Behavioural coverage of the path comes via FakeTransport; this pins the
adapter's field mapping and the create-list refusal."""

from __future__ import annotations

import pytest

from voice_bridge.icloud import ICloudTransport
from voice_bridge.transport import NotSupportedError


class _FakeList:
    def __init__(self, title, id):
        self.title = title
        self.id = id


class _FakeReminders:
    def __init__(self):
        self._lists = [_FakeList("Vox-Message-Inbox", "L1"), _FakeList("Vox-Message-Outbox", "L2")]
        self._items = {
            "L1": [{"guid": "g1", "title": "buy milk", "desc": "2%", "completed": False}],
            "L2": [],
        }

    def lists(self):
        return self._lists

    def list_reminders(self, lid):
        return {"reminders": self._items.get(lid, [])}

    def post(self, title, description=None, collection=None):
        self._items.setdefault(collection, []).append(
            {"guid": "g2", "title": title, "desc": description, "completed": False}
        )
        return "g2"


def _t(sample_config):
    t = ICloudTransport(sample_config)
    t.r = _FakeReminders()  # inject a primed service, skip connect/pyicloud
    return t


def test_enumerate_and_resolve(sample_config):
    t = _t(sample_config)
    refs = t.list_todo_lists()
    assert {r.name for r in refs} == {"Vox-Message-Inbox", "Vox-Message-Outbox"}
    assert t.resolve_list("Vox-Message-Inbox").id == "L1"


def test_read_incomplete_maps_fields(sample_config):
    t = _t(sample_config)
    ref = t.resolve_list("Vox-Message-Inbox")
    items = t.read_incomplete(ref)
    assert items[0].id == "g1" and items[0].title == "buy milk" and items[0].notes == "2%"


def test_add_todo_posts(sample_config):
    t = _t(sample_config)
    ref = t.resolve_list("Vox-Message-Outbox")
    assert t.add_todo(ref, "reply", "body") == "g2"
    assert t.read_incomplete(ref)[0].title == "reply"


def test_resolve_missing_raises(sample_config):
    t = _t(sample_config)
    with pytest.raises(LookupError):
        t.resolve_list("Nope")


def test_create_list_unsupported(sample_config):
    with pytest.raises(NotSupportedError):
        ICloudTransport(sample_config).create_list("X")
