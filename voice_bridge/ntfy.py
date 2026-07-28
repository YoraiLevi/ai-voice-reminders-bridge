"""The single phone-banner sender.

Best-effort by design: a failed banner must never break the reply that triggered
it, so `push` never raises. But "best effort" used to mean the caller learned
nothing - it returned a bare `False` for both *no topic configured* and *the POST
failed*, so a network outage was announced to the user as "no ntfy topic",
sending them to fix something that was already correct (NTFY-1).

`PushResult` separates the two. It stays truthy-compatible, because the reply
path only ever asks "did it go?".

**The topic is a bearer secret.** Anyone who learns it can POST to it, so a
leaked topic lets an attacker send banners that look like they came from your
bridge - including a `Click` URL leading anywhere, phishing a user who taps
these by habit. Choose a long random topic, and self-host ntfy if the content
matters (FMA-6).
"""

from __future__ import annotations

import urllib.request
import threading
from dataclasses import dataclass

from .config import Config
from .mailbox import clip


@dataclass(frozen=True)
class PushResult:
    """Outcome of one banner: `sent`, `no_topic`, or `failed`, plus why.

    Truthy only when actually sent, so existing callers that treat the result as
    a boolean keep their meaning.
    """

    status: str  # "sent" | "no_topic" | "failed"
    detail: str = ""

    def __bool__(self) -> bool:
        return self.status == "sent"


def push(cfg: Config, text: str, *, click: str | None = None) -> PushResult:
    """POST one banner to `{cfg.ntfy_server}/{topic}`. Never raises.

    Title/Tags/Priority come from config (`{name}` in the title is replaced with the
    spoke name); the body is single-lined and clipped by `ntfy_body_limit`; `click`
    sets the ntfy Click header so tapping opens that URL.
    """
    try:
        topic_file = cfg.ntfy_topic_file
        if not topic_file.exists():
            return PushResult("no_topic", f"no ntfy topic file at {topic_file}")
        topic = topic_file.read_text(encoding="utf-8").strip()
        if not topic:
            return PushResult("no_topic", f"ntfy topic file is empty: {topic_file}")

        body = clip(" ".join(str(text).split()), cfg.ntfy_body_limit)
        headers = {
            "Title": cfg.ntfy_title.replace("{name}", cfg.spoke_name),
            "Tags": cfg.ntfy_tags,
            "Priority": cfg.ntfy_priority,
        }
        if click:
            headers["Click"] = click
        req = urllib.request.Request(
            f"{cfg.ntfy_server}/{topic}",
            data=body.encode("utf-8"),
            headers=headers,
            method="POST",
        )
        urllib.request.urlopen(req, timeout=8)
        return PushResult("sent")
    except Exception as exc:
        # Best-effort: never break the reply. But record WHY, so the caller can
        # tell a real failure from an absent configuration.
        return PushResult("failed", f"{type(exc).__name__}: {exc}")


# --------------------------------------------------------------------------- #
# delayed delivery
# --------------------------------------------------------------------------- #

#: The timer used for delayed banners. A module attribute so tests can replace it
#: with something that fires on command instead of on a real clock.
_TIMER = threading.Timer

#: Banners waiting for their delay to elapse. Kept so shutdown can flush them
#: rather than losing them, and so tests can assert on what is outstanding.
_pending: list[tuple[object, Config, str, str | None]] = []
_pending_lock = threading.Lock()


def schedule(cfg: Config, text: str, *, click: str | None = None) -> None:
    """Send a banner after `cfg.notify_delay` seconds, without blocking the caller.

    The banner used to arrive a beat BEFORE the reminder it announces, because a
    push is a single fast POST while the reminder has to sync to the phone. You
    got buzzed, looked, and the thing was not there yet.

    **Mechanism, stated plainly: this is a thread, not async.** The poller is a
    synchronous loop, so there is no event loop to await on, and sleeping in the
    cycle would stall polling for every message. A `threading.Timer` costs one
    short-lived thread per banner and keeps the loop moving, which is the
    behaviour that was actually asked for.

    The timer threads are daemons so a hard kill never hangs, and `flush()` on
    shutdown fires anything still waiting rather than dropping it: a banner is a
    doorbell, and a doorbell that silently does not ring is the failure this
    project keeps hunting.
    """
    delay = max(float(getattr(cfg, "notify_delay", 0) or 0), 0.0)
    if delay <= 0:
        push(cfg, text, click=click)
        return

    entry: list[object] = [None]

    def _fire() -> None:
        with _pending_lock:
            _pending[:] = [p for p in _pending if p[0] is not entry[0]]
        push(cfg, text, click=click)

    timer = _TIMER(delay, _fire)
    entry[0] = timer
    with _pending_lock:
        _pending.append((timer, cfg, text, click))
    timer.daemon = True
    timer.start()


def flush() -> int:
    """Fire every waiting banner NOW, cancelling its timer. Returns how many.

    Called when the bridge stops. Waiting out the remaining delay would make
    Ctrl-C feel broken, and dropping the banner would lose a message the user was
    told they would receive, so it does neither: it rings immediately.
    """
    with _pending_lock:
        waiting = list(_pending)
        _pending.clear()
    for timer, cfg, text, click in waiting:
        timer.cancel()  # type: ignore[attr-defined]
        push(cfg, text, click=click)
    return len(waiting)


def pending_count() -> int:
    """How many banners are waiting. Observability for tests and `status`."""
    with _pending_lock:
        return len(_pending)
