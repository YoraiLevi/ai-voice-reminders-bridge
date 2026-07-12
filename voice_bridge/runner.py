"""The `run` state machine — ensure everything, then bridge. Reuses setup for the
config gap; fills only what's missing (self-bootstrapping)."""

from __future__ import annotations

from pathlib import Path

from . import poller
from . import setup as setup_mod
from .config import default_config_path, load_config
from .factory import make_transport


def run_command(
    *,
    mailbox: str | None = None,
    transport: str | None = None,
    require_mailbox: bool = False,
    interval: int | None = None,
    once: bool = False,
    dry_run: bool = False,
    config_path: str | Path | None = None,
    overrides: dict | None = None,
) -> int:
    path = Path(config_path) if config_path else default_config_path()
    ov = dict(overrides or {})
    if mailbox:
        ov["mailbox_dir"] = mailbox
    if transport:
        ov["transport"] = transport

    # ensure config: none -> setup flow (reuse), which writes it
    if not path.exists():
        setup_mod.run_setup(config_path=path, transport=transport or "icloud", overrides=ov)

    cfg = load_config(path, overrides=ov)

    if dry_run:
        return poller.dry_run(cfg)

    # ensure mailbox files
    if not (cfg.peer_inbox.exists() and cfg.our_inbox.exists()):
        if require_mailbox:
            print(f"error: no mailbox at {cfg.mailbox_dir} (--require-mailbox set)")
            return 2
        cfg.mailbox_dir.mkdir(parents=True, exist_ok=True)
        cfg.peer_inbox.touch()
        cfg.our_inbox.touch()
        print(f"mailbox ready at {cfg.mailbox_dir} — a peer must join to process messages")

    t = make_transport(cfg)
    return poller.run(cfg, t, once=once, interval=interval)
