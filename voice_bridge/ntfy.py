"""The single phone-banner sender. Best-effort by design: reads the topic from the
config's `ntfy_topic_file`, no-ops when absent, and never raises — a failed banner
must never break the reply that triggered it.
"""

from __future__ import annotations

import urllib.request

from .config import Config
from .mailbox import clip


def push(cfg: Config, text: str, *, click: str | None = None) -> bool:
    """POST one banner to `{cfg.ntfy_server}/{topic}`. Returns True if a request was
    sent, False if it was a no-op (no topic). Never raises.

    Title/Tags/Priority come from config (`{name}` in the title is replaced with the
    spoke name); the body is single-lined and clipped by `ntfy_body_limit`; `click`
    sets the ntfy Click header so tapping opens that URL.
    """
    try:
        topic_file = cfg.ntfy_topic_file
        if not topic_file.exists():
            return False
        topic = topic_file.read_text(encoding="utf-8").strip()
        if not topic:
            return False

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
        return True
    except Exception:
        return False  # notification is best-effort; never break the reply
