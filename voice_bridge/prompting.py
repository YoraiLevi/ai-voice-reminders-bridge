"""One place that reads from a person, so Ctrl-C means the same thing everywhere.

Every interactive command used bare `input()` / `getpass()`, so pressing Ctrl-C at a
prompt raised `KeyboardInterrupt` straight through to the top and printed a
traceback - including, once, interleaved into the middle of a half-written sentence
about what the command would delete. The run loop had always handled Ctrl-C
gracefully; the prompts never did.

Ctrl-C at a question is the most ordinary thing a person does: it means *stop, I did
not mean to start this*. Answering it with a stack trace suggests something broke,
at the exact moment the user was being careful.

So there is ONE wrapper rather than a `try` around every call site. That is the same
lesson `_alive` taught the hard way: a second copy is a second chance to be wrong,
and the copy that is missed is the one that matters.
"""

from __future__ import annotations

import getpass as _getpass


class Cancelled(Exception):
    """The user pressed Ctrl-C at a prompt. Not an error - a decision.

    Carried to the CLI's single top-level handler, which prints one line and exits.
    Nothing between here and there needs to know about it, which is the point.
    """


def ask(prompt: str) -> str:
    """Read a line. Ctrl-C becomes `Cancelled`; EOF still raises `EOFError`.

    The two are deliberately different. EOF means "nobody is there", which callers
    answer with their own safe default. Ctrl-C means "a person is here and wants
    out", which should stop the command rather than pick an answer for them.
    """
    try:
        return input(prompt)
    except KeyboardInterrupt:
        print()  # the ^C lands mid-line; start the message on a clean one
        raise Cancelled from None


def ask_secret(prompt: str) -> str:
    """`ask` for something that must not echo."""
    try:
        return _getpass.getpass(prompt)
    except KeyboardInterrupt:
        print()
        raise Cancelled from None
