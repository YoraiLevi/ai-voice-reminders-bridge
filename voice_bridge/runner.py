"""The `run` state machine - ensure everything, then bridge. Reuses setup for the
config gap; fills only what's missing (self-bootstrapping)."""

from __future__ import annotations

from pathlib import Path

from . import poller
from . import server as server_mod
from . import setup as setup_mod
from .config import Config, load_config, resolve_config_path
from .factory import make_transport

#: Written into a mailbox we created, next to the two message files.
PEER_PROMPT_FILE = "PEER-PROMPT.md"


def announce_new_mailbox(cfg: Config) -> list[str]:
    """Explain the mailbox we just made, and write the peer prompt into it.

    The old line was `mailbox ready at <dir> - a peer must join to process
    messages`. Every word of that is true and none of it is actionable: it names a
    requirement, not a step, in vocabulary the reader has not met yet. A user who
    had just uninstalled and re-run landed here with a working phone, an empty
    mailbox, and no idea what a peer was or how one joins.

    So we say what the two files are, and drop a paste-ready prompt beside them.
    Writing the file rather than only printing it matters: this output scrolls past
    while the bridge starts, and the thing you must paste at an agent should still
    be there tomorrow.

    NEVER OVERWRITTEN. An existing `PEER-PROMPT.md` may have been edited, or put
    there by another spoke; we only fill a gap.
    """
    from .prompt import render_peer_prompt

    width = max(len(cfg.peer_inbox.name), len(cfg.our_inbox.name))
    out = [f"mailbox created at {cfg.mailbox_dir}"]
    out.append(f"  {cfg.peer_inbox.name:<{width}}  your dictations land here - your peer READS it")
    out.append(
        f"  {cfg.our_inbox.name:<{width}}  your peer WRITES replies here - they reach your phone"
    )

    target = cfg.mailbox_dir / PEER_PROMPT_FILE
    if not target.exists():
        try:
            target.write_text(render_peer_prompt(cfg), encoding="utf-8")
        except OSError as exc:  # pragma: no cover - unwritable mailbox dir
            out.append(f"  (could not write {target}: {exc})")
            out.append("  Print it instead with:  voice-bridge peer-prompt")
            return out
    out.append("")
    out.append("NOTHING IS PROCESSED UNTIL A PEER JOINS. To make one, paste this at a")
    out.append("coding agent on this machine:")
    out.append(f"  {target}")
    out.append("Or print it again with:  voice-bridge peer-prompt")
    return out


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
    with_server: bool = False,
    force: bool = False,
) -> int:
    path, _ = resolve_config_path(config_path, must_exist=False)
    assert path is not None  # must_exist=False always names a target
    ov = dict(overrides or {})
    if mailbox:
        ov["mailbox_dir"] = mailbox
    if transport:
        ov["transport"] = transport

    # RUN-4: --dry-run must write NOTHING. It ran the setup flow first, so asking
    # "what would this do?" on a fresh machine created a config file as a side
    # effect - the one thing a dry run promises not to do. Resolve in memory
    # instead, from defaults if no file exists.
    if dry_run:
        cfg = load_config(path, overrides=ov) if path.exists() else load_config(None, overrides=ov)
        return poller.dry_run(cfg)

    # ensure config: none -> setup flow (reuse), which writes it
    if not path.exists():
        setup_mod.run_setup(config_path=path, transport=transport or "icloud", overrides=ov)

    cfg = load_config(path, overrides=ov)

    # ensure mailbox files
    if not (cfg.peer_inbox.exists() and cfg.our_inbox.exists()):
        if require_mailbox:
            print(f"error: no mailbox at {cfg.mailbox_dir} (--require-mailbox set)")
            return 2
        cfg.mailbox_dir.mkdir(parents=True, exist_ok=True)
        cfg.peer_inbox.touch()
        cfg.our_inbox.touch()
        for line in announce_new_mailbox(cfg):
            print(line)

    # ensure the Radicale server (child lifecycle owned here) + lists
    child = None
    if cfg.transport == "radicale":
        url = server_mod.client_url(cfg)
        if not server_mod.is_reachable(url):
            if with_server:
                child = server_mod.ensure_running(cfg, spawn_child=True)
            else:
                print(
                    f"radicale server not reachable at {url} - "
                    "`voice-bridge radicale-server start --background`"
                )
        if server_mod.is_reachable(url):
            try:  # ensure the two lists exist (idempotent)
                setup_mod.provision(cfg, make_transport(cfg))
            except Exception as exc:  # pragma: no cover - network
                print(f"warn: could not ensure lists: {exc}")

    t = make_transport(cfg)
    try:
        return poller.run(cfg, t, once=once, interval=interval, force=force)
    finally:
        if child is not None:
            child.terminate()
