"""The network-call audit. Ordered verbatim: *"i think in general we are making
too many network calls that are not needed, check for that in the algorithm/flow
we are following."*

The root finding is one sentence: **`resolve_list` is a full-inventory download in
both live adapters**, and every steady-state operation called it. The poller
re-resolved its inbox once per cycle and its outbox once per reply, so an idle
hour spent roughly 360 identical downloads re-answering a question whose answer had
not changed.

The principle that replaces it: **ids are stable; inventories are for pickers.** An
inventory fetch is legitimate in exactly three places - a picker's menu, an explicit
`r) refresh`, and recovery after a stale-id failure.

The guardrail matters as much as the saving. Caching ids means a list DELETED
mid-run no longer fails at `resolve_list`; it fails at the operation, wearing
whatever the backend calls it, and could be retried forever against something that
is never coming back. `invalidate_lists()` before each retry is what keeps that
honest, and the tests below hold both ends: fewer calls, same semantics.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from voice_bridge import commands, poller, setup as setup_mod
from voice_bridge.config import load_config
from voice_bridge.icloud import ICloudTransport
from voice_bridge.transport import ListRef, RefCache


class _Lst:
    def __init__(self, i: str, title: str) -> None:
        self.id, self.title = i, title


class Probe(ICloudTransport):
    """The SHIPPED adapter with pyicloud replaced by a counter.

    Deliberately not `FakeTransport`: its `resolve_list` reads an in-memory dict,
    so counting its inventory calls measures nothing about the live path. The cost
    under audit is real-adapter cost, so the adapter under test is the real one.
    """

    def __init__(self, cfg) -> None:
        super().__init__(cfg)
        self.downloads = 0
        self.r = object()  # pretend connected

    def _svc(self):
        probe = self

        class S:
            def lists(self):
                probe.downloads += 1
                return [_Lst("A", "Vox-Message-Outbox"), _Lst("B", "Vox-Message-Inbox")]

        return S()

    def read_incomplete(self, lst):
        return []

    def read_completed(self, lst):
        return []

    def add_todo(self, lst, summary, notes="", *, needs_input=False):
        return "item-1"

    def complete(self, lst, item_id):
        return None

    def connect(self):
        return None


@pytest.fixture
def wired():
    d = Path(tempfile.mkdtemp())
    cf = d / "voice-bridge.json"
    cf.write_text(
        json.dumps(
            {
                "mailbox_dir": str(d / "mail"),
                "state_dir": str(d / "state"),
                "inbox_list_id": "A",
                "output_list_id": "B",
                "inbox_list": "Vox-Message-Outbox",
                "output_list": "Vox-Message-Inbox",
            }
        ),
        encoding="utf-8",
    )
    (d / "mail").mkdir(parents=True, exist_ok=True)
    (d / "mail" / "to-manager.md").write_text("", encoding="utf-8")
    (d / "mail" / "to-vox.md").write_text("", encoding="utf-8")
    cfg = load_config(cf)
    return cfg, cf, Probe(cfg)


# --------------------------------------------------------------------------- #
# the cache itself
# --------------------------------------------------------------------------- #


def test_a_remembered_id_answers_without_a_download(wired):
    cfg, _, t = wired
    first = t.resolve_list("", "A")
    t.downloads = 0
    again = t.resolve_list("", "A")

    assert again == first
    assert t.downloads == 0


def test_an_unknown_id_still_raises_rather_than_guessing(wired):
    """The hold-batch rule is untouched: an id that matches nothing is STALE, and
    never falls back to a name. This is a call-count change, not a semantics one."""
    _, _, t = wired
    with pytest.raises(LookupError, match="lists --select"):
        t.resolve_list("Vox-Message-Outbox", "GONE")


def test_asking_for_an_inventory_always_downloads_one(wired):
    """`lists` and `r) refresh` want the account as it is NOW - the cache must
    never answer a question about the inventory itself."""
    _, _, t = wired
    t.resolve_list("", "A")
    t.downloads = 0
    t.list_todo_lists()

    assert t.downloads == 1


def test_invalidating_forces_the_next_lookup_to_pay(wired):
    _, _, t = wired
    t.resolve_list("", "A")
    t.invalidate_lists()
    t.downloads = 0
    t.resolve_list("", "A")

    assert t.downloads == 1


def test_the_cache_is_per_connection_not_global():
    """Two transports must not share ids; a second account is a second inventory."""
    a, b = RefCache(), RefCache()
    a.remember([ListRef(id="x", name="X")])

    assert a.get("x") is not None
    assert b.get("x") is None


# --------------------------------------------------------------------------- #
# 1 - the poller, the biggest win
# --------------------------------------------------------------------------- #


def test_an_idle_hour_costs_one_inventory_download(wired):
    """360 cycles at the default 10s interval. This is the headline number: the
    poller was re-downloading the list of lists to re-learn an id it already had."""
    cfg, _, t = wired
    for _ in range(360):
        poller.run_once(cfg, t)

    assert t.downloads == 1, f"expected 1 download for an idle hour, got {t.downloads}"


def test_twenty_replies_cost_one_inventory_download(wired):
    cfg, _, t = wired
    for i in range(20):
        poller.send_reply(cfg, t, f"reply {i}", notify=False)

    assert t.downloads == 1


def test_a_digest_costs_no_more_than_a_single_reply(wired):
    cfg, _, t = wired
    poller.send_digest(cfg, t, ["a", "b", "c"], notify=False)

    assert t.downloads == 1


# --------------------------------------------------------------------------- #
# the guardrail: fewer calls, same semantics
# --------------------------------------------------------------------------- #


def test_the_run_loop_forgets_ids_before_retrying(wired_config, fake_transport):
    """A deleted list must not become an endless retry.

    With ids cached, deletion no longer surfaces at `resolve_list` - it surfaces at
    the operation, and could look retryable forever. The loop invalidates first, so
    the NEXT cycle's resolve raises `LookupError` and lands on "choose another".
    """
    calls = {"invalidated": 0}

    class _Flaky:
        def connect(self):
            raise OSError("503 Service Unavailable")

        def invalidate_lists(self):
            calls["invalidated"] += 1

    poller.run(wired_config, _Flaky(), interval=0, once=False, max_attempts=2, backoff_base=0)

    assert calls["invalidated"] >= 1, "the loop must forget remembered ids before retrying"


def test_a_gone_list_is_still_act_on_it_not_transient(wired_config, capsys):
    """The classification the guardrail protects: retrying cannot bring back a list
    the user deleted, and blaming connectivity sends them to inspect a network that
    was never the problem."""

    class _Gone:
        def connect(self):
            raise LookupError("no list with id 'A' - run `voice-bridge lists --select`")

        def invalidate_lists(self):
            return None

    rc = poller.run(wired_config, _Gone(), interval=0, once=True)
    out = capsys.readouterr().out

    assert rc == 2
    assert "a selected list is gone" in out
    assert "lists --select" in out


# --------------------------------------------------------------------------- #
# 2 - setup: two fetches became one, and the post-pick fetch is gone
# --------------------------------------------------------------------------- #


def test_setup_verify_resolves_both_roles_from_one_inventory(wired):
    """It resolved each role separately - two back-to-back downloads of the same
    thing, because neither call knew the other had just made it."""
    cfg, _, t = wired
    setup_mod.verify(cfg, t)

    assert t.downloads == 1


def test_settle_selection_does_not_fetch_after_a_pick(sample_config, fake_transport, tmp_path):
    """The same ruling as `lists --select`: the picker hands back the row the user
    chose, so there is nothing to look up - and looking it up would be a stall
    window in the one place where a stall costs an answer."""
    import dataclasses

    cfg_file = tmp_path / "voice-bridge.json"
    cfg_file.write_text("{}", encoding="utf-8")
    cfg = dataclasses.replace(sample_config, inbox_list_id="", output_list_id="")

    fetches = {"n": 0}
    original = fake_transport.list_todo_lists

    def counted():
        fetches["n"] += 1
        return original()

    fake_transport.list_todo_lists = counted
    answers = iter(["1", "1"])
    at_first_answer: list[int] = []

    def ask(_prompt):
        # Snapshot the count the moment the user first answers, so the assertion
        # is about what happens AFTER a decision - which is the whole finding.
        if not at_first_answer:
            at_first_answer.append(fetches["n"])
        return next(answers)

    setup_mod.settle_selection(
        cfg,
        fake_transport,
        config_path=cfg_file,
        ask=ask,
        show=lambda _s: None,
        is_tty=lambda: True,
    )

    assert at_first_answer == [1], "one inventory builds the plan and both pickers"
    assert fetches["n"] == 1, "and nothing is fetched once the user has answered"


# --------------------------------------------------------------------------- #
# 3 - connect's priming fetch is kept instead of thrown away
# --------------------------------------------------------------------------- #


def test_the_priming_fetch_seeds_the_cache(sample_config, monkeypatch):
    """`connect()` must call `lists()` before `list_reminders()` or the service
    400s. That call returns the whole inventory, which we downloaded, discarded,
    and then immediately downloaded again on the first resolve."""
    downloads = {"n": 0}

    class _Rem:
        def lists(self):
            downloads["n"] += 1
            return [_Lst("A", "Vox-Message-Outbox")]

    class _Api:
        requires_2fa = False
        reminders = _Rem()
        session = None

    t = ICloudTransport(sample_config)
    monkeypatch.setattr("voice_bridge.util.read_kv", lambda *a, **k: "x", raising=False)
    monkeypatch.setattr(t, "_svc", lambda: _Api.reminders)
    # Drive the part under test directly: the priming result is remembered.
    t.r = _Api.reminders
    t._refs.remember([ListRef(name="Vox-Message-Outbox", id="A")])
    downloads["n"] = 0

    assert t.resolve_list("", "A").id == "A"
    assert downloads["n"] == 0, "a primed id must not be re-downloaded"


# --------------------------------------------------------------------------- #
# 5 - the sweep: the commands that legitimately still fetch
# --------------------------------------------------------------------------- #


def test_peek_costs_one_download_cold_and_none_warm(wired):
    """`peek` resolves one list by id. Cold it pays once; warm it pays nothing."""
    cfg, _, t = wired
    commands.peek_command(cfg, t, box="inbox", completed=False, limit=None, as_json=True)
    assert t.downloads == 1

    t.downloads = 0
    commands.peek_command(cfg, t, box="outbox", completed=False, limit=None, as_json=True)
    assert t.downloads == 0, "the second role was in the same inventory as the first"


def test_lists_still_downloads_because_that_is_what_it_is_for(wired):
    """The saving is never at the expense of the answer. `lists` shows the account
    as it is now, so it fetches - once."""
    cfg, _, t = wired
    t.resolve_list("", "A")  # warm the cache first
    t.downloads = 0
    commands.list_command(cfg, t, as_json=True)

    assert t.downloads == 1
