"""The `run` state machine - ensure everything, then bridge. Reuses setup for the
config gap; fills only what's missing (self-bootstrapping)."""

from __future__ import annotations

from pathlib import Path

from . import poller
from . import server as server_mod
from . import setup as setup_mod
from .config import Config, load_config, resolve_config_path
from .factory import make_transport
from .selection import missing_roles, missing_roles_message

#: Written into a mailbox we created, next to the two message files.
PEER_PROMPT_FILE = "PEER-PROMPT.md"


def peer_has_written(cfg: Config) -> bool:
    """Has anything ever arrived from the peer? Evidence, not liveness.

    Deliberately the weaker claim. We can see that a peer WROTE into our inbox at
    some point; we cannot see that one is running now, and saying "a peer has
    joined" from a file that only proves history would be the false-GREEN class
    this project keeps deleting.
    """
    try:
        return bool(cfg.our_inbox.read_text(encoding="utf-8").strip())
    except OSError:
        return False


def orientation(cfg: Config, *, interval: int, just_created: bool = False) -> list[str]:
    """What every run must say before it goes quiet.

    The explainer used to hang off mailbox CREATION, so only the one run that
    happened to create the mailbox ever oriented anybody. A user's transcript
    proved it: a bare `voice-bridge run` on an existing mailbox printed NOTHING
    until "stopped." - no files, no peer instructions, and no sign the program was
    even alive.

    Ruled: *"this kind of paragraph should be in the 'run' too so that the user
    knows about these information too."* Creation is an event; orientation is a
    state, and states are reported every time.

    The last line is the heartbeat the manager asked for: ONE truthful opening
    line, so a healthy idle bridge is distinguishable from a stuck one at a glance.
    No per-cycle noise - a log that scrolls while nothing happens is its own way of
    hiding what does.
    """
    width = max(len(cfg.peer_inbox.name), len(cfg.our_inbox.name))
    made = "  (just created)" if just_created else ""
    out = [
        f"mailbox: {cfg.mailbox_dir}{made}",
        f"  {cfg.peer_inbox.name:<{width}}  your dictations land here - your peer READS it",
        f"  {cfg.our_inbox.name:<{width}}  your peer WRITES replies here - they reach your phone",
    ]

    prompt_file = cfg.mailbox_dir / PEER_PROMPT_FILE
    if peer_has_written(cfg):
        out.append("")
        out.append("A peer has written here before, so one is set up.")
    else:
        out.append("")
        out.append("NOTHING IS PROCESSED UNTIL A PEER JOINS. Your dictations will arrive")
        out.append("in the file above and sit there until an agent reads them.")
        if prompt_file.exists():
            out.append(f"To make one, paste this at a coding agent:  {prompt_file}")
        else:
            out.append("To make one:  voice-bridge peer-prompt")

    out.append("")
    out.append(f"bridging: polling {cfg.inbox_list or 'your list'} every {interval}s")
    out.append("Ctrl-C to stop.")
    return out


def announce_new_mailbox(cfg: Config) -> list[str]:
    """Say the mailbox was CREATED, and drop the peer prompt beside it.

    Everything explanatory moved to `orientation`, which every run prints. What is
    left here is the one thing that is genuinely news: this directory did not exist
    a moment ago. Creation is an event, so it is announced once; what the files are
    and whether a peer exists are STATES, and states are reported every time.

    Writing the prompt file rather than only printing it matters: this output
    scrolls past while the bridge starts, and the thing you must paste at an agent
    should still be there tomorrow.

    NEVER OVERWRITTEN. An existing `PEER-PROMPT.md` may have been edited, or put
    there by another spoke; we only fill a gap.
    """
    from .prompt import render_peer_prompt

    out: list[str] = []
    target = cfg.mailbox_dir / PEER_PROMPT_FILE
    if not target.exists():
        try:
            target.write_text(render_peer_prompt(cfg), encoding="utf-8")
        except OSError as exc:  # pragma: no cover - unwritable mailbox dir
            out.append(f"  (could not write {target}: {exc})")
            out.append("  Print it instead with:  voice-bridge peer-prompt")
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

    # VALIDATE BEFORE ANY SIDE EFFECT. This check used to sit after the mailbox was
    # created and the peer-join instructions printed, so a run that was never going
    # to start first made a directory, made two files, and handed the user three
    # paragraphs of things to do - then said it could not run. Reading top-down,
    # every one of those was wasted, and two of them were actions.
    #
    # Cheap and offline, so there is no reason for it to be anywhere but first.
    unsettled = missing_roles(cfg)
    if unsettled:
        for line in missing_roles_message(unsettled):
            print(line)
        return 2

    # ensure mailbox files
    #
    # `(just created)` is a claim about the MAILBOX - that is the noun the line
    # names - so it is measured on the directory, not on the two files.
    #
    # Keyed off the files, it printed on every single run of a live session,
    # including back-to-back runs minutes apart: a peer that ejects removes its own
    # inbox file, so the next start legitimately re-touches one and the flag came
    # back true. An event that fires every time is a state wearing an event's
    # clothes - the exact inverse of the section-24 law, which this same block was
    # written to satisfy. Re-touching a message file is routine; a mailbox coming
    # into existence happens once.
    created_now = not cfg.mailbox_dir.exists()
    if not (cfg.peer_inbox.exists() and cfg.our_inbox.exists()):
        if require_mailbox:
            print(f"error: no mailbox at {cfg.mailbox_dir} (--require-mailbox set)")
            return 2
        cfg.mailbox_dir.mkdir(parents=True, exist_ok=True)
        cfg.peer_inbox.touch()
        cfg.our_inbox.touch()
        for line in announce_new_mailbox(cfg):
            print(line)

    # EVERY run, not only the one that happened to create the mailbox. Printed
    # before the loop goes quiet, so the last thing on screen while nothing is
    # happening explains what "nothing happening" means.
    for line in orientation(cfg, interval=interval or cfg.poll_interval, just_created=created_now):
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
