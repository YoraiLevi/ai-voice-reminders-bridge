"""One command, one conversation: the guided path from nothing to a working bridge.

Every individual step already worked. The *sequence* did not: a new user finished
`setup` and still had to discover `icloud-login`, then a topic file, then
`--verify`, then `vox-prompt`, then `run` — five more commands, none of which
announced itself. Knowing each step exists is not the same as being led through
them, and the gap between those two is where people give up.

So this chains the whole journey and narrates it. Two rules govern every step,
both from the person who has to use it:

**Non-intrusive.** No step touches the user's machine or their accounts without
saying so first. The clipboard is asked about rather than overwritten — it may
hold something they care about — and every optional step states what skipping it
costs, then names the command that does it later.

**Explicit guidance.** At each point the user is told what is happening now and
what comes next, so the flow never feels like it is doing something on their
behalf that they cannot see.
"""

from __future__ import annotations

import secrets
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable

from .config import Config, load_config, set_value

Ask = Callable[[str], str]
Show = Callable[[str], None]

TOTAL_STEPS = 5


def _step(show: Show, n: int, title: str, *, then: str = "") -> None:
    """Announce the step and what follows it, so the path is always visible."""
    show("")
    show(f"[{n}/{TOTAL_STEPS}] {title}")
    if then:
        show(f"        next: {then}")


def _yes(ask: Ask, prompt: str, *, default: bool = True) -> bool:
    """A yes/no question whose DEFAULT is shown and honoured on a bare Enter."""
    suffix = "[Y/n]" if default else "[y/N]"
    while True:
        try:
            answer = ask(f"{prompt} {suffix}: ").strip().lower()
        except EOFError:
            return default
        if not answer:
            return default
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False


def suggest_topic() -> str:
    """An unguessable ntfy topic.

    A banner topic is a public URL on a public server: anyone who knows the name
    can read every reply. So the suggestion is random rather than something
    memorable like "vox-alice" — memorable is exactly what makes it guessable.
    """
    return f"vox-{secrets.token_hex(8)}"


# --------------------------------------------------------------------------- #
# step 1 — credentials
# --------------------------------------------------------------------------- #


def step_credentials(cfg: Config, *, ask: Ask, show: Show, login: Callable[[], int]) -> bool:
    """Offer to run the login flow inline. Returns False if it still is not set up."""
    _step(show, 1, "Credentials", then="choosing your lists")
    if cfg.creds_env.exists() and cfg.creds_env.read_text(encoding="utf-8").strip():
        show(f"  already configured: {cfg.creds_env}")
        return True

    show("  No credentials yet — they are what lets this reach your Reminders.")
    show(f"  They are stored in {cfg.creds_env}, on this machine only.")
    if not _yes(ask, "  Set them up now?"):
        show("  skipped — nothing will connect until you run:  voice-bridge icloud-login")
        return False

    return login() == 0


# --------------------------------------------------------------------------- #
# step 3 — notifications
# --------------------------------------------------------------------------- #


def step_notifications(cfg: Config, *, config_path: Path, ask: Ask, show: Show) -> bool:
    """Choose how banners reach the phone, including a self-hosted server.

    Presented as four real options rather than a yes/no, because "do you want
    notifications" hides the choice that actually matters: whose server carries
    them. Someone running their own ntfy must not have to skip the step and go
    hand-edit a config afterwards.
    """
    _step(show, 3, "Notifications (optional)", then="a test message")
    if cfg.ntfy_topic_file.exists() and cfg.ntfy_topic_file.read_text(encoding="utf-8").strip():
        show(f"  already configured: {cfg.ntfy_topic_file}")
        return True

    show("  A banner on your phone the moment a reply arrives. Without one, replies")
    show("  still arrive in your list — you just have to look.")
    show("")
    suggested = suggest_topic()
    show(f"    1) use a private topic I generate for you   ({suggested})")
    show("    2) enter a topic you already use")
    show("    3) use your own ntfy server (its URL, and a topic)")
    show("    4) skip for now")

    while True:
        try:
            choice = ask("  Choose 1-4: ").strip()
        except EOFError:
            choice = "4"

        if choice == "1":
            topic = suggested
        elif choice == "2":
            try:
                topic = ask("  topic: ").strip()
            except EOFError:
                return False
            if not topic:
                continue
        elif choice == "3":
            try:
                server = ask(f"  server URL [{cfg.ntfy_server}]: ").strip() or cfg.ntfy_server
                topic = ask("  topic: ").strip()
            except EOFError:
                return False
            if not topic:
                continue
            set_value(config_path, "ntfy_server", server.rstrip("/"))
        elif choice == "4":
            show("  skipped — no banners. Set one later by writing a topic to:")
            show(f"    {cfg.ntfy_topic_file}")
            return False
        else:
            show("  please choose 1, 2, 3 or 4.")
            continue

        cfg.ntfy_topic_file.parent.mkdir(parents=True, exist_ok=True)
        cfg.ntfy_topic_file.write_text(topic + "\n", encoding="utf-8")
        show(f"  saved to {cfg.ntfy_topic_file}")
        show("  Subscribe to that topic in the ntfy app on your phone to receive banners.")
        return True


# --------------------------------------------------------------------------- #
# step 5 — the phone prompt
# --------------------------------------------------------------------------- #


def copy_to_clipboard(text: str) -> bool:
    """Best-effort clipboard write. False when there is no tool to do it with."""
    if sys.platform == "win32":
        cmd = ["clip"]
    elif sys.platform == "darwin":
        cmd = ["pbcopy"]
    elif shutil.which("wl-copy"):
        cmd = ["wl-copy"]
    elif shutil.which("xclip"):
        cmd = ["xclip", "-selection", "clipboard"]
    else:
        return False
    try:
        subprocess.run(cmd, input=text.encode("utf-8"), check=True)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def step_phone_prompt(cfg: Config, *, ask: Ask, show: Show) -> None:
    """Hand over the prompt — asking before touching the clipboard.

    Overwriting a clipboard unasked is a small theft of something the user may
    have been holding on purpose. The prompt is long enough that copying it is
    genuinely useful, which is exactly why it has to be a question.
    """
    from .prompt import render_vox_prompt

    _step(show, 5, "Your phone prompt", then="starting the bridge")
    text = render_vox_prompt(cfg)

    show("  This is the instruction set your phone runs. It carries the lists you")
    show("  chose — their names and their ids — so the phone never has to guess.")

    if _yes(ask, "  Copy it to your clipboard?", default=False) and copy_to_clipboard(text):
        show("  copied. Paste it into the Claude app on your phone.")
        return

    show("")
    show(text)
    show("")
    show("  ^ paste the text above into the Claude app on your phone.")
    show("  You can print it again any time with:  voice-bridge vox-prompt")


# --------------------------------------------------------------------------- #
# the whole conversation
# --------------------------------------------------------------------------- #


def welcome(show: Show) -> None:
    show("Setting up your voice bridge — talk to your agents from your phone.")
    show("")
    show(f"  {TOTAL_STEPS} steps: credentials, lists, notifications, a test, the phone prompt.")
    show("  Nothing on your machine or your accounts changes without asking first,")
    show("  and every step can be skipped — each one tells you how to do it later.")


def farewell(show: Show, *, ready: bool) -> None:
    show("")
    if ready:
        show("You are set up. Start the bridge with:  voice-bridge run")
        show("Then talk to your phone — dictations arrive in your mailbox, replies come back.")
        return
    show("Setup is incomplete — the steps you skipped are named above, each with its")
    show("command. `voice-bridge doctor` will tell you what is still missing.")


def reload_config(path: Path) -> Config:
    """Re-read after a step wrote to the file, so later steps see the new state."""
    return load_config(path)
