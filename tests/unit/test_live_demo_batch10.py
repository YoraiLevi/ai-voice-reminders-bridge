"""The detector that was silent for the same user, for the opposite reason.

Batch 4 added the invocation note because the tool printed advice its own user
could not run. Tonight the user hit "voice-bridge : The term 'voice-bridge' is not
recognized" AGAIN - from `setup`'s own stopped-here advice - and this time the
note said nothing at all.

The reason is worth more than the fix: **under `uv run` the console script IS on
our PATH**, because uv prepends the project venv for the process it spawns. So
`on_path()` was satisfied, the note stayed empty, and the user retyped the advice
in their PARENT PowerShell, which never had it. The check was answering *"is it on
MY path"* when every message depends on *"will it be on the path of the shell this
text gets pasted into"* - a different question with, until now, the same answer in
every case anybody had tested.

That makes this the third instance of one habit: a condition we arranged, read as
a fact about the world. The coverage delta (batch 4), the PATH override that never
reached the child (batch 4 again), and now a PATH we were LENT and treated as the
user's own.

The fix does not touch the forty message strings - they are still right in the
shipping case. It corrects the question, and it keeps the answer evidence-based:
we subtract only the directory we know was lent to us and look again, so a
globally installed tool merely launched through `uv run` gets no false alarm.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from voice_bridge import invocation

MARKER = invocation._UV_RUN_MARKER  # noqa: SLF001 - the env contract under test


def _install(directory: Path, *, exe: bool = True) -> None:
    """Put a findable `voice-bridge` in `directory`.

    The execute bit is not decoration: `shutil.which` requires it on POSIX and
    ignores it on Windows, and CI runs both. Without the chmod these tests would
    pass on the machine that wrote them and fail on half the matrix - which is the
    environment-dependent-truth class this whole batch is about.
    """
    (directory / "voice-bridge").write_text("", encoding="utf-8")
    (directory / "voice-bridge").chmod(0o755)
    if exe:
        (directory / "voice-bridge.exe").write_text("", encoding="utf-8")
        (directory / "voice-bridge.exe").chmod(0o755)


@pytest.fixture
def under_uv_run(monkeypatch, tmp_path):
    """The live situation: a project venv on OUR PATH and on nobody else's."""
    scripts = tmp_path / ".venv" / ("Scripts" if os.name == "nt" else "bin")
    scripts.mkdir(parents=True)
    monkeypatch.setenv(MARKER, "1")
    monkeypatch.setattr(invocation.sys, "prefix", str(tmp_path / ".venv"))
    monkeypatch.setenv("PATH", os.pathsep.join([str(scripts), str(tmp_path / "elsewhere")]))
    return scripts


# --------------------------------------------------------------------------- #
# the detection: what we borrowed, and from whom
# --------------------------------------------------------------------------- #


def test_a_borrowed_path_is_recognised_as_borrowed(under_uv_run):
    assert invocation.borrowed_scripts_dir() == under_uv_run


def test_an_ordinary_shell_has_borrowed_nothing(plain_shell):
    """No marker, no claim. The absence is the whole signal."""
    assert invocation.borrowed_scripts_dir() is None


def test_an_activated_venv_is_NOT_treated_as_borrowed(monkeypatch, tmp_path, plain_shell):
    """The pair that must not be confused.

    Activating a venv by hand exports VIRTUAL_ENV and edits PATH - the script
    really is typeable in that user's shell, and a note there would be noise. Only
    `uv run` lends a PATH to one child and leaves the parent untouched, and only
    `uv run` sets the marker.
    """
    monkeypatch.setenv("VIRTUAL_ENV", str(tmp_path / ".venv"))
    monkeypatch.setattr(invocation.sys, "prefix", str(tmp_path / ".venv"))

    assert invocation.borrowed_scripts_dir() is None, "VIRTUAL_ENV alone proves nothing"


# --------------------------------------------------------------------------- #
# 1 - the question the messages actually depend on
# --------------------------------------------------------------------------- #


def test_on_path_still_says_yes_which_is_why_it_was_the_wrong_question(under_uv_run):
    """Pinning the defect itself, not just its repair.

    `on_path()` is not wrong about anything - it answers what it says. It was the
    wrong thing to ASK, and this test exists so a future reader can see that the
    two questions genuinely disagree here rather than take it on faith.
    """
    _install(under_uv_run)

    assert invocation.on_path() is True
    assert invocation.typeable() is False, "not in the shell the user will type into"


def test_the_note_fires_under_uv_run(under_uv_run):
    """The live failure, in one assertion: setup's stopped-here advice was printed
    with no translation note, and the user pasted it into a shell that could not
    run it."""
    _install(under_uv_run)

    note = invocation.path_note()

    assert note, "the case that was structurally silent"
    assert "uv run voice-bridge" in note, "name the form that works"
    assert "shell you type into" in note


def test_the_uv_form_is_not_a_filesystem_guess(under_uv_run, monkeypatch):
    """No pyproject.toml anywhere above us here, and it still answers `uv run`.

    Batch 4 hunted for a project marker because all it had was argv[0]. When the
    marker is present we know how this process was launched, so the form is that
    command restated - and a checkout whose layout we do not recognise still gets
    the right advice.
    """
    _install(under_uv_run)
    monkeypatch.setattr(invocation.sys, "argv", [str(under_uv_run / "voice-bridge.exe")])

    assert invocation.working_form() == "uv run voice-bridge"


# --------------------------------------------------------------------------- #
# 2 - no false alarm: the claim stays checked, not assumed
# --------------------------------------------------------------------------- #


def test_a_globally_installed_tool_run_through_uv_gets_no_warning(under_uv_run, tmp_path):
    """The over-fire this design exists to avoid.

    "Under `uv run`, warn" would be the easy rule and it would lie to everyone who
    has voice-bridge installed properly and happens to use `uv run` anyway. We
    subtract ONLY the directory we know was lent to us and look again; if the
    command survives that subtraction, the user's own PATH reaches it.
    """
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    _install(under_uv_run)
    _install(elsewhere)

    assert invocation.typeable() is True
    assert invocation.path_note() == "", "nothing is wrong, so say nothing"


def test_subtraction_is_limited_to_the_one_borrowed_entry(under_uv_run, tmp_path):
    """A defensive check on scope: the rest of PATH is evidence, and only the lent
    directory is removed from it."""
    borrowed = invocation.borrowed_scripts_dir()
    assert borrowed is not None
    remaining = [e for e in os.environ["PATH"].split(os.pathsep) if e and Path(e) != borrowed]

    assert remaining == [str(tmp_path / "elsewhere")]


# --------------------------------------------------------------------------- #
# the installed case is untouched
# --------------------------------------------------------------------------- #


def test_the_shipping_case_is_still_silent(monkeypatch, plain_shell):
    """An installed package prints no note, which is the point of having one."""
    monkeypatch.setattr(invocation.shutil, "which", lambda _n, path=None: "/usr/bin/voice-bridge")

    assert invocation.typeable() is True
    assert invocation.working_form() == "voice-bridge"
    assert invocation.path_note() == ""


def test_the_note_is_one_wording_for_both_causes(under_uv_run, monkeypatch):
    """Two causes, one sentence, because the reader's next action is identical.

    A borrowed PATH and a shell that never had the script are different diagnoses
    and the same instruction: read `voice-bridge ...` as `<form> ...`. Two sentences
    saying that differently is how the two drift apart - the defect batch 7 fixed
    in the picker, where one option was described twice in different words.
    """
    _install(under_uv_run)
    borrowed_note = invocation.path_note()

    monkeypatch.delenv(MARKER)
    monkeypatch.setattr(invocation.shutil, "which", lambda _n, path=None: None)
    monkeypatch.setattr(invocation.sys, "argv", ["/x/voice_bridge/cli.py"])
    bare_note = invocation.path_note()

    stem = "will not be on the PATH of the shell you type into"
    assert stem in borrowed_note and stem in bare_note
    assert borrowed_note != bare_note, "same sentence, different form named"
