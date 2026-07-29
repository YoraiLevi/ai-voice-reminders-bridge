"""The question block - everything needed to answer, immediately above the cursor.

Ruled from live use, and the reasoning is worth keeping because it is not a taste
argument. In a terminal the cursor sits at the BOTTOM. Whatever the program printed
first has scrolled away, so anything the user needs in order to answer must be in
the last few lines - or answering costs them a scroll, which is an action we forced
on them to make up for our layout.

The user's words: *"the 'choice' instructions are at the top and after the 'dump'
of the options it's hard to process what to do given the user just observes an
instruction to choose and how to pick without being told why (the terminal needs
scrolling up, which is an action by the user)."*

Our list picker was the worst case: it taught the direction of travel at the top,
then printed twenty-one lists, then asked. On a real account the teaching was
off-screen by the time it mattered.

**THE LAW: nothing may exist ONLY above a long dump that the answer requires.**
Headers may still preview - repetition costs a line and saves a scroll - but the
block immediately before the prompt carries all four of:

    what is being chosen · why (which direction it carries) · the current state ·
    the keys that are accepted

Long content goes ABOVE the block. The block never goes above long content.
"""

from __future__ import annotations

from typing import Callable

Show = Callable[[str], None]

#: ASCII on purpose. This lands on a Windows console that may be cp1252, and a
#: separator that arrives as mojibake is worse than one that is plain.
RULE = "-" * 62


def question_block(
    show: Show,
    *,
    choosing: str,
    why: str = "",
    current: str = "",
    keys: list[str] | None = None,
) -> None:
    """Print the four facts a question needs, just above where it is asked.

    `current` is included even when it is "nothing yet": a user deciding whether to
    change something needs to know what they are changing FROM, and its absence is
    itself the answer on a fresh install.

    Every argument is a plain string rather than a rendered line, so the caller
    cannot accidentally reorder the block or drop a part of it - the shape is here,
    once, and the call sites supply content.
    """
    show("")
    show(RULE)
    show(f"CHOOSING: {choosing}")
    if why:
        show(f"          {why}")
    show(f"CURRENTLY: {current or 'nothing selected yet'}")
    if keys:
        show("")
        # Aligned on the key, so the eye finds the column of things it may type
        # rather than reading prose. Each entry is "KEY|meaning"; the split is done
        # here so no call site can drift out of alignment with another.
        pairs = [k.split("|", 1) if "|" in k else (k, "") for k in keys]
        width = max(len(k) for k, _ in pairs)
        for key, meaning in pairs:
            show(f"  {key:<{width}}  {meaning}".rstrip())
