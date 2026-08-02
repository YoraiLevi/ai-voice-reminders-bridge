"""The third hole, and the double wait the feedback law exposed.

**Third raw traceback, third hole, same flow.** `setup` grew transient handling
around credentials (batch 2), then around the lists phase (batch 5), and the TEST
MESSAGE phase raised a raw `PyiCloudAPIResponseException` the first time iCloud was
slow - after the lists and the topic had already been saved.

Spot-fixing a third hole would have been the fourth mistake. The manager's order
was explicit: *"third hole in the same flow means enumerate, don't spot-fix."* So
every network-bound phase in the guided flow goes through `transient.guarded`, and
a test walks the AST to prove no phase is reached any other way. That is the same
move as the AST prompt test: stop relying on remembering.

**And the feedback law paid for itself immediately.** The batch-8 progress lines
made a pre-existing defect visible for the first time: `reading your lists... 7.1s`
followed by `reading your lists... 6.7s`, back to back, on every inventory command.
`connect()` was doing pyicloud's required priming fetch and then the display read
downloaded the same thing again. Nobody had noticed because nothing had ever said
what the program was waiting on.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from voice_bridge import progress, setup as setup_mod, transient
from voice_bridge.config import load_config
from voice_bridge.icloud import ICloudTransport


@pytest.fixture(autouse=True)
def _loud():
    progress.configure(quiet=False)
    yield
    progress.configure(quiet=False)


class _Lst:
    def __init__(self, i: str, title: str) -> None:
        self.id, self.title = i, title


class Probe(ICloudTransport):
    """The shipped adapter with pyicloud replaced by a counter.

    `connect` is NOT stubbed this time. That is the whole point: the batch-6 table
    reported `lists = 1` because its probe overrode `connect()` to a no-op, so the
    priming fetch never happened in the measurement. The instrument hid the very
    thing the user was waiting on.
    """

    def __init__(self, cfg) -> None:
        super().__init__(cfg)
        self.downloads = 0
        self.r = object()  # already "connected"; priming state is what we measure

    def _svc(self):
        probe = self

        class S:
            def lists(self):
                probe.downloads += 1
                return [_Lst("A", "Vox-Message-Outbox"), _Lst("B", "Vox-Message-Inbox")]

            def list_reminders(self, list_id, include_completed=False):
                return type("R", (), {"reminders": []})()

        return S()


@pytest.fixture
def probe(tmp_path, tmp_mailbox):
    cfg_file = tmp_path / "voice-bridge.json"
    cfg_file.write_text(
        json.dumps(
            {
                "mailbox_dir": str(tmp_mailbox),
                "state_dir": str(tmp_path / "state"),
                "inbox_list_id": "A",
                "output_list_id": "B",
                "inbox_list": "Vox-Message-Outbox",
                "output_list": "Vox-Message-Inbox",
            }
        ),
        encoding="utf-8",
    )
    cfg = load_config(cfg_file)
    return cfg, Probe(cfg)


# --------------------------------------------------------------------------- #
# 2 - the double inventory fetch
# --------------------------------------------------------------------------- #


def test_listing_the_inventory_downloads_it_ONCE(probe):
    """`reading your lists... 7.1s` then `... 6.7s`, back to back. The priming
    fetch and the display fetch were two separate downloads of one thing."""
    _, t = probe
    t.list_todo_lists()

    assert t.downloads == 1, f"one user-visible wait, not two (got {t.downloads})"


def test_reading_items_does_not_add_a_second_wait(probe):
    """Priming is a SIDE EFFECT of the first inventory read now, so an operation
    that needs it finds it already done."""
    cfg, t = probe
    lst = t.resolve_list("", "A")
    t.downloads = 0
    t.read_incomplete(lst)

    assert t.downloads == 0


def test_an_operation_with_no_prior_inventory_still_primes(probe):
    """The requirement is real: pyicloud 400s on `list_reminders` before a `lists()`
    call. Lazy must not mean never."""
    _, t = probe
    t.read_incomplete(type("L", (), {"id": "A", "name": "x"})())

    assert t.downloads == 1, "the operation primed itself"


def test_priming_happens_once_across_many_operations(probe):
    _, t = probe
    lst = t.resolve_list("", "A")
    for _ in range(10):
        t.read_incomplete(lst)

    assert t.downloads == 1


# --------------------------------------------------------------------------- #
# 1 - enumerate, don't spot-fix
# --------------------------------------------------------------------------- #

#: The network-bound phases of the guided flow. Each reaches the transport, so each
#: must be inside classification - which for this flow means inside `guarded(...)`
#: or inside the lists-phase retry loop that predates it.
_NETWORK_PHASES = {"verify", "provision", "settle_selection"}


def _guarded_calls(tree: ast.AST) -> set[int]:
    """Line numbers of every call that sits inside a `guarded(...)` argument."""
    inside: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id == "guarded":
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Call):
                        inside.add(sub.lineno)
    return inside


def test_no_network_phase_is_reached_outside_classification():
    """Three holes in one flow means the wrapper is not the fix - being the ONLY way
    in is. This walks `setup.py` and reports any phase call that is neither inside
    `guarded(...)` nor inside the lists retry loop.
    """
    source = (Path(setup_mod.__file__)).read_text(encoding="utf-8")
    tree = ast.parse(source)
    guarded_lines = _guarded_calls(tree)

    # The lists phase has its own retry loop, written before `guarded` existed; it
    # provides the same classification, so calls inside it are accounted for.
    loop_ranges = [
        (n.lineno, max(getattr(x, "lineno", n.lineno) for x in ast.walk(n)))
        for n in ast.walk(tree)
        if isinstance(n, ast.Try)
        and any(isinstance(h.type, ast.Name) and h.type.id == "Exception" for h in n.handlers)
    ]

    unguarded = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
            continue
        if node.func.id not in _NETWORK_PHASES:
            continue
        if node.lineno in guarded_lines:
            continue
        if any(lo <= node.lineno <= hi for lo, hi in loop_ranges):
            continue
        unguarded.append(f"{node.func.id}() at setup.py:{node.lineno}")

    assert not unguarded, "network phases outside classification: " + ", ".join(unguarded)


def test_guarded_returns_the_value_on_success(sample_config):
    result = transient.guarded(
        lambda: {"ok": True}, sample_config, ask=lambda _p: "n", is_tty=lambda: True
    )
    assert result == {"ok": True}


def test_guarded_retries_in_place_and_keeps_going(sample_config):
    import requests

    attempts = []

    def flaky():
        attempts.append(1)
        if len(attempts) == 1:
            raise requests.Timeout("read timed out")
        return "second time lucky"

    result = transient.guarded(
        flaky, sample_config, ask=lambda _p: "y", show=lambda _s: None, is_tty=lambda: True
    )

    assert result == "second time lucky"
    assert len(attempts) == 2


def test_guarded_returns_None_when_the_user_gives_up(sample_config):
    """An abandoned phase is NOT a failed one, and the caller decides what it means
    for the rest of the flow - only it knows whether later steps can stand without
    this one."""
    import requests

    def always_stalls():
        raise requests.Timeout("read timed out")

    result = transient.guarded(
        always_stalls,
        sample_config,
        ask=lambda _p: "n",
        show=lambda _s: None,
        is_tty=lambda: True,
    )
    assert result is None


def test_guarded_does_not_dress_a_real_error_as_a_blip(sample_config):
    from voice_bridge.icloud import ICloudError

    with pytest.raises(ICloudError):
        transient.guarded(
            lambda: (_ for _ in ()).throw(ICloudError("bad password")),
            sample_config,
            ask=lambda _p: "y",
            show=lambda _s: None,
            is_tty=lambda: True,
        )


def test_an_abandoned_round_trip_says_so_rather_than_reporting_failure(sample_config):
    """Blaming the setup for the network is the mis-advice class again."""
    from voice_bridge import onboard

    out: list[str] = []
    answers = iter(["y"])
    ok = onboard.step_test_message(
        sample_config,
        ask=lambda _p: next(answers),
        show=out.append,
        run_verify=lambda: None,
        probe="P",
    )
    text = "\n".join(out)

    assert ok is False
    assert "not completed" in text
    assert "FAIL" not in text, "an unfinished trip is not a failed one"


# --------------------------------------------------------------------------- #
# 3 - the probe operations announce themselves
# --------------------------------------------------------------------------- #


def test_sending_to_the_phone_announces_itself(probe, capsys):
    """30+ seconds of silence after answering Y, then a traceback. The batch-8
    instrumentation covered `connect` and the inventory and missed the writes."""
    _, t = probe
    t._svc = lambda: type(  # noqa: SLF001 - the seam under test
        "S", (), {"create": lambda self, **kw: type("C", (), {"id": "i1", "title": kw["title"]})()}
    )()
    t._primed = True
    t.add_todo(type("L", (), {"id": "A", "name": "x"})(), "probe message")

    assert "probe message" in capsys.readouterr().err


def test_every_network_touching_operation_is_wrapped():
    """Extended past batch 8's two methods, as ordered.

    Every Transport method in the live adapters that reaches the network must
    announce itself - the coverage question turned into a rule.
    """
    wanted = {"connect", "list_todo_lists", "resolve_list", "_query", "add_todo", "complete"}
    for name in ("icloud.py",):
        tree = ast.parse((Path(progress.__file__).parent / name).read_text(encoding="utf-8"))
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.FunctionDef) or fn.name not in wanted:
                continue
            calls = [
                n.func.id
                for n in ast.walk(fn)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            ]
            assert "step" in calls, f"{name}:{fn.name} waits on the network in silence"


def test_the_poll_loop_stays_quiet():
    """Ruled: no per-cycle noise. Interactive commands announce every wait because
    a person is watching one; the poller makes the same calls every ten seconds for
    hours, and narrating them would bury the events that matter."""
    tree = ast.parse((Path(progress.__file__).parent / "poller.py").read_text(encoding="utf-8"))
    suspended = [
        n for n in ast.walk(tree) if isinstance(n, ast.Attribute) and n.attr == "suspended"
    ]
    assert suspended, "the run loop must suspend progress"


def test_suspend_restores_whatever_was_there_before(capsys):
    progress.configure(quiet=False)
    with progress.suspended():
        with progress.step("quiet please"):
            pass
        assert capsys.readouterr().err == ""

    with progress.step("loud again"):
        pass
    assert "loud again" in capsys.readouterr().err
