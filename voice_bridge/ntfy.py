"""The single phone-banner sender.

Best-effort by design: a failed banner must never break the reply that triggered
it, so `push` never raises. But "best effort" used to mean the caller learned
nothing — it returned a bare `False` for both *no topic configured* and *the POST
failed*, so a network outage was announced to the user as "no ntfy topic",
sending them to fix something that was already correct (NTFY-1).

`PushResult` separates the two. It stays truthy-compatible, because the reply
path only ever asks "did it go?".

**The topic is a bearer secret.** Anyone who learns it can POST to it, so a
leaked topic lets an attacker send banners that look like they came from your
bridge — including a `Click` URL leading anywhere, phishing a user who taps
these by habit. Choose a long random topic, and self-host ntfy if the content
matters (FMA-6).
"""

from __future__ import annotations

import urllib.request
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
