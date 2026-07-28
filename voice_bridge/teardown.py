"""`reset` and `uninstall` - the two destructive commands, and their guardrails.

These are the only commands here that can lose something irreplaceable, so they
are built around one idea: **the user consents to a specific list of paths, not to
a category.** Everything else follows from that.

* The preview names every path first, present or not, so "(not present)" is part
  of the account rather than a silent omission. A preview that hid what it
  considered would make the answer mean less than it appears to.
* Kept paths are named too, with the reason they are kept. A file that survives a
  command called `uninstall` is a surprise unless the command said so.
* Confirmation is a typed word, not `y`. The `y/N` reflex is exactly what makes
  destructive prompts dangerous, and the extra second is the entire point.
* Removal is reported per path. A teardown that prints "done" while a locked file
  survived is the false-success class this project spends its time removing.

`reset` is surgical (config, and credentials only on request); `uninstall` removes
everything by default and takes `--keep-*` flags to opt out. That default was
ruled by the user after hearing the argument against it: they want a teardown that
is complete unless told otherwise.

**Nothing here touches an account.** Reminders lists and published gists live
elsewhere; a command that tidies a machine must not quietly reach into a cloud
account and delete lists the user may have made by hand.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .config import Config

Ask = Callable[[str], str]
Show = Callable[[str], None]


@dataclass(frozen=True)
class Artifact:
    """One path the command has an opinion about, and why."""

    path: Path
    label: str
    #: Set when the path is deliberately NOT removed. Named in the preview so a
    #: survivor is never a surprise.
    kept: str = ""

    @property
    def exists(self) -> bool:
        return self.path.exists()


@dataclass
class Plan:
    """What a run would do, before it does any of it."""

    verb: str  # "reset" | "uninstall"
    remove: list[Artifact] = field(default_factory=list)
    keep: list[Artifact] = field(default_factory=list)

    @property
    def confirm_word(self) -> str:
        return self.verb.upper()


def _settings_artifacts(cfg: Config, *, spare_lists: bool) -> list[Artifact]:
    """Everything this system stores ABOUT itself, swept from the code that writes it.

    `spare_lists` decides the fate of `radicale/collections/`, and the distinction is
    not cosmetic. On iCloud your lists live on your Apple account, which no local
    command touches. On Radicale they live in that directory - so a blanket state
    wipe would destroy your actual lists on one transport while explicitly refusing
    to on the other, for no reason you could see. "We do not delete your lists"
    should not depend on where you host them, so `reset` spares them and only
    `uninstall` - which leaves nothing by definition - takes them.
    """
    out = [
        Artifact(cfg.creds_env, "credentials"),
        Artifact(cfg.cookie_dir, "trusted session (cookies)"),
        Artifact(cfg.ntfy_topic_file, "banner topic"),
        Artifact(cfg.state_dir / "seen", "dedupe state and reply cursor"),
        Artifact(cfg.state_dir / "poller.pid", "poller liveness file"),
        Artifact(cfg.state_dir / "radicale" / "config", "generated server settings"),
        Artifact(cfg.state_dir / "radicale" / "users", "server login file"),
    ]
    if not spare_lists:
        out.append(Artifact(cfg.state_dir / "radicale", "self-hosted server, including its lists"))
        out.append(Artifact(cfg.state_dir, "state directory"))
    return out


def _mailbox_artifacts(cfg: Config) -> list[Artifact]:
    """The mailbox, named down to its message files.

    The user consents to losing *messages*, not to losing "a directory", so the
    preview lists the files by name. Anything else in there belongs to other
    spokes and is listed too, because it is just as gone.
    """
    out = [
        Artifact(cfg.peer_inbox, "messages you sent to your agents"),
        Artifact(cfg.our_inbox, "replies your agents sent you"),
    ]
    if cfg.mailbox_dir.is_dir():
        known = {cfg.peer_inbox, cfg.our_inbox}
        for extra in sorted(cfg.mailbox_dir.iterdir()):
            if extra not in known and extra.is_file():
                out.append(Artifact(extra, "another spoke's messages"))
    out.append(Artifact(cfg.mailbox_dir, "the mailbox directory"))
    return out


def plan_reset(cfg: Config, config_path: Path) -> Plan:
    """Back to *just installed, never configured*.

    No flags. Credentials are not an extra tier here, they are the point: this
    command exists so setup and login can be run again from zero, and a reset that
    left you logged in could not demo a login. Every partial anyone actually wants
    is already another command - change one list with `lists --select`, remove the
    software with `uninstall` - and a flag whose result is reachable by an existing
    verb is one more way to be wrong.
    """
    plan = Plan("reset")
    plan.remove.append(Artifact(config_path, "configuration"))
    plan.remove.extend(_settings_artifacts(cfg, spare_lists=True))
    plan.keep.append(
        Artifact(cfg.mailbox_dir, "your mailbox and its messages", kept="reset never removes it")
    )
    plan.keep.append(
        Artifact(
            cfg.state_dir / "radicale" / "collections",
            "your self-hosted lists and their contents",
            kept="your lists are yours on every transport",
        )
    )
    return plan


def plan_uninstall(cfg: Config, config_path: Path, *, keep_mailbox: bool = False) -> Plan:
    """As if never installed. Complete by default, one opt-out.

    `--keep-config` and `--keep-state` are deliberately absent: neither is an
    uninstall in any useful sense, and both land exactly where `reset` lands. Two
    ways to reach one state is the overlap this design removed. The answer to a
    partial is a sentence, not a flag - you wanted reset.
    """
    plan = Plan("uninstall")
    plan.remove.append(Artifact(config_path, "configuration"))
    plan.remove.extend(_settings_artifacts(cfg, spare_lists=False))

    if keep_mailbox:
        plan.keep.append(
            Artifact(cfg.mailbox_dir, "your mailbox and its messages", kept="--keep-mailbox")
        )
    else:
        plan.remove.extend(_mailbox_artifacts(cfg))

    # Never ours: the user chose this path and we only ever appended to it.
    plan.keep.append(
        Artifact(
            Path("--log-file"),
            "any log file you pointed us at",
            kept="you chose that path; we only append to it",
        )
    )
    return plan


def blocking_process(cfg: Config) -> str:
    """A live poller or server that makes removal unsafe, described, or "".

    Deleting state under a running poller strands its open files and re-delivers
    from a cleared dedupe list; deleting `collections/` under a running Radicale
    leaves a half-written store and a process serving files that no longer exist.
    Both are checked by PROBING the pid rather than trusting a pidfile, so a stale
    file from a crash does not lock the user out of their own teardown.
    """
    from . import poller

    pid = poller._another_poller_is_running(cfg)
    if pid is not None:
        return (
            f"a bridge is running (pid {pid}). Stop it first: press Ctrl-C in its "
            "window, or stop the service that started it."
        )

    if cfg.transport == "radicale":
        from . import server

        if server.status(cfg).get("pid"):
            return (
                "the Radicale server is running. Stop it first: voice-bridge radicale-server stop"
            )
    return ""


def preview(plan: Plan, *, show: Show) -> int:
    """Print the whole account: what goes, what stays, why. Returns removal count."""
    going = [a for a in plan.remove if a.exists]

    show("")
    show(f"{plan.verb} will DELETE:")
    for art in plan.remove:
        mark = "" if art.exists else "   (not present)"
        show(f"  {art.path}{mark}")
        show(f"      {art.label}")

    if plan.keep:
        show("")
        show("It will NOT touch:")
        for art in plan.keep:
            show(f"  {art.path}")
            show(f"      {art.label} - kept: {art.kept}")

    show("")
    show("Nothing on your Apple or GitHub account is touched: reminder lists and")
    show("published gists are not on this machine and are not this command's to remove.")

    return len(going)


def confirm(plan: Plan, *, ask: Ask, show: Show) -> bool:
    """Require the verb typed in full. Anything else means no."""
    word = plan.confirm_word
    try:
        answer = ask(f"Type {word} to confirm: ").strip()
    except EOFError:
        # A stream that ends is not consent, and this is the command where
        # taking an answer nobody gave cannot be undone.
        show("  no answer - nothing was removed.")
        return False
    if answer != word:
        show("  not confirmed - nothing was removed.")
        return False
    return True


def execute(plan: Plan, *, show: Show) -> int:
    """Remove what the plan said, reporting each path. Returns a failure count."""
    failures = 0
    for art in plan.remove:
        if not art.exists:
            continue
        try:
            if art.path.is_dir():
                shutil.rmtree(art.path)
            else:
                art.path.unlink()
            show(f"  removed {art.path}")
        except OSError as exc:
            # Reported, never swallowed: "done" while a locked file survived is
            # exactly the false success this project keeps removing.
            failures += 1
            show(f"  FAILED  {art.path}: {exc}")
    return failures


def run_teardown(
    cfg: Config,
    config_path: Path,
    *,
    verb: str,
    keep_mailbox: bool = False,
    assume_yes: bool = False,
    is_tty: Callable[[], bool] | None = None,
    ask: Ask = input,
    show: Show = print,
) -> int:
    """Preview, confirm, remove. 0 done or nothing to do; 2 refused or failed."""
    import sys

    tty = (is_tty or (lambda: sys.stdin.isatty()))()

    blocked = blocking_process(cfg)
    if blocked:
        show(f"error: cannot {verb} while {blocked}")
        return 2

    plan = (
        plan_reset(cfg, config_path)
        if verb == "reset"
        else plan_uninstall(cfg, config_path, keep_mailbox=keep_mailbox)
    )

    going = preview(plan, show=show)
    if going == 0:
        show("")
        show("Nothing to remove - none of those paths exist.")
        return 0

    if not assume_yes:
        if not tty:
            # Never prompt a pipe, and never assume consent for a destructive act.
            show("")
            show(f"error: {verb} needs confirmation and stdin is not a terminal.")
            show("       Re-run in a terminal, or pass --yes if you are certain.")
            return 2
        show("")
        if not confirm(plan, ask=ask, show=show):
            return 2

    show("")
    failures = execute(plan, show=show)
    show("")
    if failures:
        show(f"{verb} finished with {failures} path(s) it could not remove (listed above).")
        return 2
    if verb == "reset":
        # Name the path back, because the whole purpose is that you are about to
        # walk it: this command exists to put you at the start of setup again.
        show("reset complete. This machine is as if freshly installed.")
        show("Next:  voice-bridge setup")
    else:
        show("uninstall complete. Nothing of this system remains on this machine.")
        show("Re-install by running: voice-bridge setup")
    return 0
