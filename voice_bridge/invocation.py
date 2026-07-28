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
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

#: What the messages say. Correct whenever the console script is on PATH.
CANONICAL = "voice-bridge"


def on_path() -> bool:
    """True when typing `voice-bridge` in a fresh shell would work."""
    return shutil.which(CANONICAL) is not None


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
    if on_path():
        return CANONICAL

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
    """
    if on_path():
        return ""
    form = working_form()
    if form == CANONICAL:  # pragma: no cover - unreachable while on_path() is False
        return ""
    return (
        f"note: `{CANONICAL}` is not on your PATH here - "
        f"read every `{CANONICAL} ...` below as `{form} ...`."
    )
