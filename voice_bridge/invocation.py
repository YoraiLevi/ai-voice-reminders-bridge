"""How to type this command, on the machine it is currently running on.

Every next-step line in this program says `voice-bridge <verb>`, which is correct
for an installed package and wrong inside a `uv` project checkout, where the
console script exists in `.venv/Scripts` and is not on `PATH`. A user followed our
own advice and got "voice-bridge : The term 'voice-bridge' is not recognized" -
the tool told them to run something it had just proved they could not run.

We do not rewrite forty message strings for this. Every one of them is correct in
the installed case, which is the shipping case, and a sweep would trade a real
class of typo for a cosmetic gain. Instead the mismatch is DETECTED and named
once, at the point it starts to matter, so the reader can translate the rest.

**And then the detector was silent for the same user, for the opposite reason.**
Under `uv run` the console script IS on our PATH - uv prepends the project venv
for the process it spawns - so nothing looked wrong, while the advice was read and
retyped in the PARENT PowerShell, where it is not there. The check was answering
"is it on MY path" when the messages depend on "will it be on the path of the
shell this text gets pasted into". Same defect twice: silent when it mattered,
because a condition we arranged was mistaken for a fact about the world.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

#: What the messages say. Correct whenever the console script is on PATH.
CANONICAL = "voice-bridge"

#: Set by `uv run` in the child it spawns, and by nothing else that matters here.
#: Activating a venv by hand exports `VIRTUAL_ENV` and edits `PATH` - it does not
#: set this - which is exactly the pair we must tell apart: an activated venv HAS
#: put the script on the user's own shell PATH, and warning them would be noise.
#: Verified rather than assumed: a throwaway `uv run` leaves exactly
#: `{UV, UV_RUN_RECURSION_DEPTH}` behind.
_UV_RUN_MARKER = "UV_RUN_RECURSION_DEPTH"


def borrowed_scripts_dir() -> Path | None:
    """The directory that is on OUR PATH because someone lent it to us, or None.

    `sys.prefix` rather than `VIRTUAL_ENV`, on the same grounds as `argv[0]` below:
    it is true by construction about the interpreter actually running, not an
    environment variable that anyone may have exported by hand.
    """
    if _UV_RUN_MARKER not in os.environ:
        return None
    return Path(sys.prefix) / ("Scripts" if os.name == "nt" else "bin")


def on_path() -> bool:
    """True when `voice-bridge` is findable from THIS process's PATH.

    Deliberately the narrow question. `typeable()` asks the one the messages
    actually depend on, and the gap between them was a live defect.
    """
    return shutil.which(CANONICAL) is not None


def typeable() -> bool:
    """Will `voice-bridge` work in the shell the user is about to type into?

    We cannot see that shell. What we can do is subtract the directory we know was
    lent to us and ask again: if the command is still findable, it is installed
    somewhere the user's own PATH reaches too, and there is nothing to say. So a
    globally installed tool that merely happens to be launched through `uv run`
    gets no false alarm - the claim stays something checked rather than assumed.
    """
    borrowed = borrowed_scripts_dir()
    if borrowed is None:
        return on_path()

    rest = os.pathsep.join(
        entry
        for entry in os.environ.get("PATH", "").split(os.pathsep)
        if entry and Path(entry) != borrowed
    )
    return shutil.which(CANONICAL, path=rest) is not None


def working_form() -> str:
    """The invocation that WILL work here, derived from how we were launched.

    Three ways this program runs, and they need three different answers:

    * an installed console script - `voice-bridge`;
    * `python -m voice_bridge.cli` - the module form is what the user typed, so it
      is what they can type again;
    * a project checkout under `uv` - the script exists but only inside the
      environment, and `uv run` is the documented way to reach it.

    Derived from `sys.argv[0]` and the filesystem rather than guessed: argv[0] is
    the one thing that is true by construction about how we got here.
    """
    if typeable():
        return CANONICAL

    if borrowed_scripts_dir() is not None:
        # Not a guess and not a filesystem hunt: `uv run` is how this very process
        # came to exist, so restating it is the one form we know reproduces it.
        return f"uv run {CANONICAL}"

    argv0 = Path(sys.argv[0] or "")
    stem = argv0.stem.lower()

    if stem in {"cli", "__main__"} or not stem:
        return f"{Path(sys.executable).name} -m voice_bridge.cli"

    # A console script that PATH cannot find means we are inside somebody's
    # environment. If a project marker sits above it, `uv run` is the form that
    # reproduces exactly this environment; otherwise the absolute path is the only
    # honest answer, because we cannot know what would put it on PATH.
    for parent in argv0.resolve().parents:
        if (parent / "pyproject.toml").exists() or (parent / "uv.lock").exists():
            return f"uv run {CANONICAL}"
    return str(argv0)


def path_note() -> str:
    """A one-line translation note, or "" when the messages are already right.

    Empty in the normal installed case, on purpose: a warning that fires when
    nothing is wrong is the kind people learn to scroll past, and then miss the
    time it matters.

    ONE wording for both causes - a bare shell that never had the script, and a
    borrowed PATH that has it only for us. The reader's next action is identical in
    both, and a second sentence saying the same thing differently is how the two
    drift apart. It says "the shell you type into" rather than "here", because
    under `uv run` "here" is precisely where it does work.
    """
    if typeable():
        return ""
    form = working_form()
    if form == CANONICAL:  # pragma: no cover - unreachable while typeable() is False
        return ""
    return (
        f"note: `{CANONICAL}` will not be on the PATH of the shell you type into - "
        f"read every `{CANONICAL} ...` below as `{form} ...`."
    )
