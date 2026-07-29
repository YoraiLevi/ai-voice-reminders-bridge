"""One answer to "the network stalled" - shared by every interactive command.

`setup` grew a careful transient path in batch 2: classify, say it is temporary,
offer to try again, keep what was already settled. `lists --select` did not, and
batch 3 then ADDED a network call to it. On a slow iCloud day that call raised a
60-second read timeout and escaped as a raw traceback, mid-pick, after the user
had chosen - and their choice was lost with it.

That is the `_alive` lesson in its most expensive form yet: not two copies that
drifted, but ONE copy and one gap, where the gap was on the path a person was
standing in. So the handling lives here and both commands call it.

Two rules the shape encodes:

**A blip must not cost an answer.** Whatever the user already decided is held by
the caller across the retry; nobody re-answers a question because a socket stalled.

**A blip must not look like a bug.** A traceback says the tool is broken. A
temporary failure says the network is busy, and those need different faces.
"""

from __future__ import annotations

from typing import Callable

from .config import Config
from .errors import is_transient

Ask = Callable[[str], str]
Show = Callable[[str], None]


def report(exc: BaseException, cfg: Config, *, show: Show = print) -> None:
    """Say what failed, in enough detail to act on, and log it.

    Asked for directly: *"log what happened? it took a really long time? fix?
    investigate?"*. The middle complaint is the sharp one - a failure reported
    with neither a duration nor an exception class is indistinguishable from the
    program hanging, so the user cannot tell whether to wait or to quit.

    The CLASS is named rather than only the message, because "Request failed" is
    what a library says for every network fault and identifies nothing.
    """
    from . import log as log_mod

    log_mod.get().warning(
        "transient transport failure: %s: %s (icloud_timeout=%ss)",
        type(exc).__name__,
        exc,
        cfg.icloud_timeout,
    )
    show(f"  the transport did not answer: {exc}")
    show(f"  That is a temporary failure, not a setup problem ({type(exc).__name__}).")
    show(f"  Calls give up after {cfg.icloud_timeout:g}s; -v logs the detail, --log-file keeps it.")


def offer_retry(
    exc: BaseException,
    cfg: Config,
    *,
    ask: Ask,
    show: Show = print,
    is_tty: Callable[[], bool],
) -> bool:
    """Classify, report, and ask. True means the caller should try again.

    Re-raises anything that is NOT transient, because an auth failure or a bug
    dressed as "want to try again?" is the mis-advice class wearing a friendly
    face - it invites the user to repeat something that cannot work.

    Never asks without a terminal: a prompt written to a pipe blocks forever or
    reads EOF and takes an answer nobody gave.
    """
    from .onboard import _yes

    if not is_transient(exc):
        raise exc

    report(exc, cfg, show=show)
    if not is_tty():
        show("  Re-run when the transport is responding again.")
        return False
    return _yes(ask, "  Try again now?", show=show)
