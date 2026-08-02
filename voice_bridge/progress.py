"""Say what is happening before it happens, so waiting never looks like hanging.

Ruled from live use: *"currently it still takes a long time to list the list
options but it looks like its hanging for not reason, when the command runs we
needs to see some kind of textual response that it's processing/networking or else
it looks like the program is just stuck. this issue is in general across the entire
cli system, we need active feedback to the user on actions that can take time."*

Three decisions, each deliberate.

**BEFORE, never after.** A line printed when the work completes reassures nobody -
it arrives exactly when the reassurance is no longer needed. The whole value is in
the seconds between.

**STDERR, never stdout.** `voice-bridge vox-prompt | clip` must pipe the prompt and
nothing else; that lesson was paid for once already, and progress chatter is
precisely the sort of thing that would quietly corrupt it.

**Elapsed only when it was actually slow.** Printing `0.1s` after every fast call
is noise that trains people to stop reading; printing nothing after a twelve-second
stall throws away the one fact that explains the wait. So the marker appears past a
threshold, which makes its presence itself meaningful - if you see a duration, that
step is why you waited.

The coverage question, and it is the same shape as the monitor-silence rule: for
every place this could be called, ask *"if the network stalled RIGHT HERE for
thirty seconds, what would the user be staring at?"* If the answer is a blank
terminal, that place needs a line.
"""

from __future__ import annotations

import sys
from contextlib import contextmanager
from time import perf_counter
from typing import Iterator

#: Below this, a duration is noise. Above it, the user felt the wait and deserves
#: to know which step spent it.
_SLOW_SECONDS = 1.0

#: Set False by `-q`. Progress is reassurance, and someone who asked for quiet has
#: said they do not want it.
_enabled = True


def configure(*, quiet: bool = False) -> None:
    global _enabled
    _enabled = not quiet


@contextmanager
def suspended() -> Iterator[None]:
    """Silence progress for a block. For the POLL LOOP, and only that.

    The two tiers are deliberate and the manager's order named the boundary: *"do
    NOT print per-cycle noise; one truthful opening line."* An interactive command
    runs once and a person is watching it, so every wait deserves a line. The
    poller runs the same three calls every ten seconds for hours - announcing each
    would bury the events that matter under the ones that do not, and a log that
    scrolls while nothing happens is its own way of hiding what does.

    A stall inside the loop is not left silent: the run loop logs the failure with
    its class and retries, which is the feedback that path actually needs. What it
    must not do is narrate success.
    """
    global _enabled
    was = _enabled
    _enabled = False
    try:
        yield
    finally:
        _enabled = was


@contextmanager
def step(what: str) -> Iterator[None]:
    """Announce `what` on stderr, run the block, then report if it dragged.

    `what` should name the OPERATION AND ITS TARGET - "reading your lists", not
    "working" - because the point is to make a stall attributable. A generic
    spinner tells you the program is alive; a named step tells you what it is
    waiting on, which is the difference between reassurance and information.
    """
    if not _enabled:
        yield
        return

    print(f"  {what}...", file=sys.stderr, flush=True)
    started = perf_counter()
    try:
        yield
    finally:
        elapsed = perf_counter() - started
        if elapsed >= _SLOW_SECONDS:
            print(f"  ... {elapsed:.1f}s", file=sys.stderr, flush=True)
