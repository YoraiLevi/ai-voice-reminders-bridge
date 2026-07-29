"""Silence, in two forms, from live act-1 use.

**Waiting looked like hanging.** *"currently it still takes a long time to list the
list options but it looks like its hanging for not reason... we need active
feedback to the user on actions that can take time."*

**And a healthy bridge looked like nothing at all.** A bare `voice-bridge run` on an
EXISTING mailbox printed nothing before `stopped.` - because the explainer was
conditioned on mailbox CREATION, so only the run that happened to create one ever
oriented anybody.

Both are the same defect wearing different clothes: **the program knew something
the user needed and did not say it.** One during work, one before waiting.

The coverage question, which is the transferable part: *"if the network stalled
RIGHT HERE for thirty seconds, what would the user be staring at?"* Anywhere the
answer is a blank terminal is a missing line.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from voice_bridge import progress, runner
from voice_bridge.config import load_config


@pytest.fixture(autouse=True)
def _loud():
    """Progress is on by default; `-q` turns it off. Restore either way."""
    progress.configure(quiet=False)
    yield
    progress.configure(quiet=False)


def _cfg(tmp_path, tmp_mailbox, **extra):
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
                **extra,
            }
        ),
        encoding="utf-8",
    )
    cfg = load_config(cfg_file)
    cfg.peer_inbox.parent.mkdir(parents=True, exist_ok=True)
    cfg.peer_inbox.touch()
    cfg.our_inbox.touch()
    return cfg, cfg_file


# --------------------------------------------------------------------------- #
# 2 - active feedback
# --------------------------------------------------------------------------- #


def test_the_line_appears_BEFORE_the_work(capsys):
    """The whole value is in the seconds between. A line printed when the work
    finishes arrives exactly when the reassurance is no longer needed."""
    seen: list[str] = []

    with progress.step("reading your lists"):
        seen.append(capsys.readouterr().err)

    assert "reading your lists" in seen[0], "the user must see it while waiting, not after"


def test_progress_goes_to_stderr_so_a_pipe_stays_clean(capsys):
    with progress.step("connecting to iCloud"):
        pass
    captured = capsys.readouterr()

    assert "connecting" in captured.err
    assert captured.out == "", "`vox-prompt | clip` must pipe the prompt and nothing else"


def test_a_fast_step_reports_no_duration(capsys):
    """`0.1s` after every quick call is noise that trains people to stop reading."""
    with progress.step("something quick"):
        pass

    assert "s\n" not in capsys.readouterr().err.replace("lists\n", "")


def test_a_slow_step_reports_its_duration(capsys, monkeypatch):
    """And its presence is itself the signal: if you see a duration, that step is
    why you waited."""
    ticks = iter([0.0, 12.5])
    monkeypatch.setattr(progress, "perf_counter", lambda: next(ticks))

    with progress.step("reading your lists"):
        pass

    assert "12.5s" in capsys.readouterr().err


def test_quiet_silences_progress(capsys):
    """Someone who asked for quiet has said they do not want reassurance."""
    progress.configure(quiet=True)
    with progress.step("connecting to iCloud"):
        pass

    assert capsys.readouterr().err == ""


def test_the_line_survives_an_exception(capsys, monkeypatch):
    """A step that FAILS is exactly when the user most needs to know which step it
    was - so the duration is reported from a `finally`, not skipped on the way out."""
    ticks = iter([0.0, 30.0])
    monkeypatch.setattr(progress, "perf_counter", lambda: next(ticks))

    with pytest.raises(RuntimeError), progress.step("reading your lists"):
        raise RuntimeError("stalled")

    err = capsys.readouterr().err
    assert "reading your lists" in err and "30.0s" in err


def test_every_network_boundary_announces_itself():
    """The coverage question, asserted rather than remembered.

    Both adapters implement `connect` and `list_todo_lists`, and those are the two
    places every command waits on. If a third network entry point appears without a
    `step(...)`, this is what should catch it.
    """
    import ast

    for name in ("icloud.py", "caldav.py"):
        source = (Path(progress.__file__).parent / name).read_text(encoding="utf-8")
        tree = ast.parse(source)
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.FunctionDef):
                continue
            if fn.name not in ("connect", "list_todo_lists"):
                continue
            calls = [
                n.func.id
                for n in ast.walk(fn)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            ]
            assert "step" in calls, f"{name}:{fn.name} waits on the network in silence"


# --------------------------------------------------------------------------- #
# 1 + 3 - orientation on EVERY run
# --------------------------------------------------------------------------- #


def test_orientation_is_not_conditioned_on_creating_the_mailbox(tmp_path, tmp_mailbox):
    """The defect exactly: a bare `run` on an EXISTING mailbox printed nothing.

    Creation is an event, so it is announced once. What the files are, and whether
    a peer exists, are STATES - and states are reported every time.
    """
    cfg, _ = _cfg(tmp_path, tmp_mailbox)
    text = "\n".join(runner.orientation(cfg, interval=10))

    assert cfg.peer_inbox.name in text and cfg.our_inbox.name in text
    assert "READS" in text and "WRITES" in text


def test_the_creation_line_carries_only_what_is_actually_news(tmp_path, tmp_mailbox):
    """`announce_new_mailbox` keeps the one fact that is new - this directory did
    not exist a moment ago - and no longer duplicates the orientation."""
    cfg, _ = _cfg(tmp_path, tmp_mailbox)
    lines = runner.announce_new_mailbox(cfg)

    assert any("created at" in line for line in lines)
    assert not any("READS" in line for line in lines), "state belongs to orientation"


def test_run_says_it_is_polling_and_how_to_stop(tmp_path, tmp_mailbox):
    """ONE truthful opening line, so a healthy idle bridge is distinguishable from
    a stuck one at a glance. No per-cycle noise: a log that scrolls while nothing
    happens is its own way of hiding what does."""
    cfg, _ = _cfg(tmp_path, tmp_mailbox)
    text = "\n".join(runner.orientation(cfg, interval=25))

    assert "polling" in text
    assert "Vox-Message-Outbox" in text, "name the list, not just the fact"
    assert "every 25s" in text, "the real interval, not the default"
    assert "Ctrl-C" in text


def test_a_mailbox_nobody_has_written_to_says_nothing_is_processed(tmp_path, tmp_mailbox):
    cfg, _ = _cfg(tmp_path, tmp_mailbox)
    text = "\n".join(runner.orientation(cfg, interval=10))

    assert "NOTHING IS PROCESSED UNTIL A PEER JOINS" in text
    assert "peer-prompt" in text


def test_a_mailbox_a_peer_has_written_to_says_so_instead(tmp_path, tmp_mailbox):
    cfg, _ = _cfg(tmp_path, tmp_mailbox)
    cfg.our_inbox.write_text("- [09:00] (manager) alive\n", encoding="utf-8")
    text = "\n".join(runner.orientation(cfg, interval=10))

    assert "NOTHING IS PROCESSED" not in text
    assert "A peer has written here before" in text


def test_the_peer_claim_is_evidence_not_liveness(tmp_path, tmp_mailbox):
    """Deliberately the weaker claim. We can see that a peer WROTE at some point;
    we cannot see that one is RUNNING, and saying so from a file that only proves
    history would be the false-GREEN class this project keeps deleting."""
    cfg, _ = _cfg(tmp_path, tmp_mailbox)
    cfg.our_inbox.write_text("- [09:00] (manager) alive\n", encoding="utf-8")
    text = "\n".join(runner.orientation(cfg, interval=10))

    assert "has written here before" in text
    assert "is running" not in text
    assert "is live" not in text


def test_the_prompt_file_is_named_when_it_exists(tmp_path, tmp_mailbox):
    cfg, _ = _cfg(tmp_path, tmp_mailbox)
    target = cfg.mailbox_dir / runner.PEER_PROMPT_FILE
    target.write_text("paste me", encoding="utf-8")
    text = "\n".join(runner.orientation(cfg, interval=10))

    assert str(target) in text


def test_orientation_never_touches_stdin_or_the_network(tmp_path, tmp_mailbox, monkeypatch):
    """It runs on every start, including in a service with no terminal, so it must
    be pure file reads - anything else would make starting the bridge conditional
    on the very thing the bridge is about to test."""
    monkeypatch.setattr(sys, "stdin", None)
    cfg, _ = _cfg(tmp_path, tmp_mailbox)

    assert runner.orientation(cfg, interval=10)
