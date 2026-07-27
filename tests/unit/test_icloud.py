"""iCloud transport: it maps the pyicloud shape correctly (with a fake service, no
network). Behavioural coverage of the path comes via FakeTransport; this pins the
adapter's field mapping and the create-list refusal."""

from __future__ import annotations

import pytest

from voice_bridge.icloud import ICloudTransport
from voice_bridge.transport import NotSupportedError


class _FakeList:
    """Mirrors `RemindersList`: `id` is the identifier, `guid` is optional metadata."""

    def __init__(self, title, id):
        self.title = title
        self.id = id
        self.guid = None


class _FakeReminder:
    """Mirrors `Reminder` — a TYPED model, not a dict."""

    def __init__(self, id, title, desc="", completed=False, list_id=""):
        self.id = id
        self.title = title
        self.desc = desc
        self.completed = completed
        self.list_id = list_id


class _FakeResult:
    """Mirrors `ListRemindersResult`, whose `.reminders` holds the models."""

    def __init__(self, reminders):
        self.reminders = reminders


class _FakeReminders:
    """Mirrors pyicloud 2.6.5's RemindersService — the METHOD NAMES it really has.

    The previous double exposed `post(title, description, collection)` and returned
    dicts. No such method exists on the real service, so every test passed against
    an adapter that could not work: the failure only appeared on a live account
    (LIVE-3). A double that invents its subject's API certifies code against a
    library that does not exist, so this one is kept deliberately close to the
    real signatures: create / get / update / list_reminders / lists.
    """

    def __init__(self):
        self._lists = [_FakeList("Vox-Message-Inbox", "L1"), _FakeList("Vox-Message-Outbox", "L2")]
        self._items = {
            "L1": [_FakeReminder("g1", "buy milk", "2%", list_id="L1")],
            "L2": [],
        }
        self._seq = 1

    def lists(self):
        return self._lists

    def list_reminders(self, list_id, include_completed=False, results_limit=200):
        items = self._items.get(list_id, [])
        if not include_completed:
            items = [r for r in items if not r.completed]
        return _FakeResult(items[:results_limit])

    def reminders(self, list_id=None):
        return self._items.get(list_id, [])

    def get(self, reminder_id):
        for items in self._items.values():
            for r in items:
                if r.id == reminder_id:
                    return r
        raise KeyError(reminder_id)

    def create(self, list_id, title, desc="", completed=False, priority=0, **kw):
        self._seq += 1
        rem = _FakeReminder(f"g{self._seq}", title, desc, completed, list_id)
        self._items.setdefault(list_id, []).append(rem)
        return rem

    def update(self, reminder):
        return None


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


def test_add_todo_creates(sample_config):
    t = _t(sample_config)
    ref = t.resolve_list("Vox-Message-Outbox")
    assert t.add_todo(ref, "reply", "body") == "g2"
    assert t.read_incomplete(ref)[0].title == "reply"


def test_complete_marks_and_updates(sample_config):
    t = _t(sample_config)
    ref = t.resolve_list("Vox-Message-Inbox")
    t.complete(ref, "g1")
    assert t.read_incomplete(ref) == []
    assert [i.id for i in t.read_completed(ref)] == ["g1"]


def test_complete_missing_raises(sample_config):
    """Transport contract: not-found must raise, never quietly succeed (FMA-2)."""
    t = _t(sample_config)
    with pytest.raises(LookupError):
        t.complete(t.resolve_list("Vox-Message-Inbox"), "no-such-id")


def test_adapter_only_calls_methods_the_real_service_has(sample_config):
    """The LIVE-3 guard: pin the adapter to the REAL library's surface.

    `post()` did not exist on pyicloud 2.6.5, yet the old double provided it, so
    the whole write path was certified against an API that was never there. This
    asserts the names against the installed class rather than against our fake.
    """
    real = pytest.importorskip("pyicloud.services.reminders").RemindersService
    for name in ("create", "get", "update", "list_reminders", "lists"):
        assert callable(getattr(real, name, None)), f"pyicloud has no {name}()"
    assert not hasattr(real, "post"), "the old adapter called a method that never existed"


def test_resolve_missing_raises(sample_config):
    t = _t(sample_config)
    with pytest.raises(LookupError):
        t.resolve_list("Nope")


def test_create_list_unsupported(sample_config):
    with pytest.raises(NotSupportedError):
        ICloudTransport(sample_config).create_list("X")


def test_retrying_recovers_on_503(monkeypatch):
    import voice_bridge.icloud as ic

    monkeypatch.setattr(ic.time, "sleep", lambda _s: None)
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("503 Service Unavailable")
        return "ok"

    assert ic._retrying(fn) == "ok" and calls["n"] == 3


def test_retrying_gives_up_on_persistent_503(monkeypatch):
    import voice_bridge.icloud as ic

    monkeypatch.setattr(ic.time, "sleep", lambda _s: None)
    with pytest.raises(RuntimeError):
        ic._retrying(lambda: (_ for _ in ()).throw(RuntimeError("503")), tries=2)


def test_retrying_reraises_non_throttle_immediately():
    import voice_bridge.icloud as ic

    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        raise ValueError("real bug")

    with pytest.raises(ValueError):
        ic._retrying(fn)
    assert calls["n"] == 1  # not retried
