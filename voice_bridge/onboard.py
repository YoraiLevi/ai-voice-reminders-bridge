"""One command, one conversation: the guided path from nothing to a working bridge.

Every individual step already worked. The *sequence* did not: a new user finished
`setup` and still had to discover `icloud-login`, then a topic file, then
`--verify`, then `vox-prompt`, then `run` - five more commands, none of which
announced itself. Knowing each step exists is not the same as being led through
them, and the gap between those two is where people give up.

So this chains the whole journey and narrates it. Two rules govern every step,
both from the person who has to use it:

**Non-intrusive.** No step touches the user's machine or their accounts without
saying so first. The clipboard is asked about rather than overwritten - it may
hold something they care about - and every optional step states what skipping it
costs, then names the command that does it later.

**Explicit guidance.** At each point the user is told what is happening now and
what comes next, so the flow never feels like it is doing something on their
behalf that they cannot see.
"""

from __future__ import annotations

import contextlib
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable

from .config import Config, load_config, set_value
from .layout import question_block

Ask = Callable[[str], str]
Show = Callable[[str], None]


def _step(show: Show, title: str, *, does: str = "") -> None:
    """Announce the step by NAME, and say what it is for.

    Two things were removed here, both ruled from live use.

    **The `next: <following step>` line.** *"They don't stand out and they dont
    make sense and the user misses them."* A forward reference answers a question
    nobody is asking: the roadmap already exists once, at the top, where someone is
    deciding whether to begin. Repeating a fragment of it inside each step puts
    orientation about the FUTURE where a person is trying to act NOW.

    **The `[n/5]` counter.** *"It's either hardcoded or self incrementing coded
    procedure and it's overhead. Just make it an obvious title so if we change the
    flow we don't need to manually update the numbering."* Right, and the argument
    is maintenance rather than looks: a count is a fact about the whole flow,
    duplicated into every step, so adding or reordering one silently makes every
    other header wrong. A NAME cannot go stale that way.
    """
    show("")
    show(f"== {title.upper()} ==")
    if does:
        show(f"   {does}")


def _yes(ask: Ask, prompt: str, *, default: bool = True, show: Show = print) -> bool:
    """A yes/no question whose DEFAULT is shown and honoured on a bare Enter.

    **EOF is not the default - it is NO.** A bare Enter is a real keystroke from a
    real person, so honouring the shown default is right. A closed stream is not an
    answer at all, and this codebase's rule everywhere else is that no answer means
    change nothing.

    Collapsing the two was a real bug, found by running the flow: a scripted setup
    hit EOF on "Start the bridge now? [Y/n]", took the displayed Yes, and launched a
    poller that never returned. On iCloud that would have started polling a live
    account unattended, from a run nobody was watching.
    """
    suffix = "[Y/n]" if default else "[y/N]"
    while True:
        try:
            answer = ask(f"{prompt} {suffix}: ").strip().lower()
        except EOFError:
            return False
        if not answer:
            return default
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        # SAY why the question is coming round again. Re-asking in silence looks
        # identical to the program ignoring an answer it did in fact receive - a
        # transcript from live use shows the same question twice with nothing
        # between them, and neither the user nor we could tell from the output
        # whether input had been rejected or dropped.
        show(f"  not an answer - type y or n, or press Enter for {'yes' if default else 'no'}.")


#: 16 bytes = 128 bits. Widened from 8 on the ruling that a topic is a BEARER
#: CREDENTIAL, not a name: it is a URL on a public server, and anyone who knows it
#: reads every reply you ever receive. There is no account, no password and no
#: revocation - knowing the string IS the authorisation - so the only defence is
#: that it cannot be enumerated. 64 bits was already a large number and still the
#: wrong tier for a secret that never rotates and protects everything.
_TOPIC_BYTES = 16


def suggest_topic() -> str:
    """An unguessable ntfy topic: `vox-` plus 128 random bits, hex-encoded."""
    return f"vox-{secrets.token_hex(_TOPIC_BYTES)}"


# --------------------------------------------------------------------------- #
# step 1 - credentials
# --------------------------------------------------------------------------- #


def step_credentials(cfg: Config, *, ask: Ask, show: Show, login: Callable[[], int]) -> bool:
    """Offer to run the login flow inline. Returns False if it still is not set up."""
    _step(show, "Credentials", does="lets this reach the Reminders on your phone")
    if cfg.creds_env.exists() and cfg.creds_env.read_text(encoding="utf-8").strip():
        show(f"  already configured: {cfg.creds_env}")
        return True

    show(f"  No credentials yet. Stored in {cfg.creds_env}, this machine only.")
    if not _yes(ask, "  Set them up now?", show=show):
        show("  skipped - nothing will connect until you run:  voice-bridge icloud-login")
        return False

    return login() == 0


# --------------------------------------------------------------------------- #
# step 3 - notifications
# --------------------------------------------------------------------------- #


def step_notifications(cfg: Config, *, config_path: Path, ask: Ask, show: Show) -> bool:
    """Choose how banners reach the phone, including a self-hosted server.

    Presented as four real options rather than a yes/no, because "do you want
    notifications" hides the choice that actually matters: whose server carries
    them. Someone running their own ntfy must not have to skip the step and go
    hand-edit a config afterwards.
    """
    _step(show, "Notifications (optional)", does="buzz your phone when a reply lands")
    if cfg.ntfy_topic_file.exists() and cfg.ntfy_topic_file.read_text(encoding="utf-8").strip():
        show(f"  already configured: {cfg.ntfy_topic_file}")
        return True

    # "A banner the moment a reply arrives" described a thing the user has not seen
    # yet, in a word they may not use for it. PUSH NOTIFICATION is what it is
    # called on the device, and the cost of declining is stated as an action they
    # will have to take rather than as a mild inconvenience - once, in the block.
    #
    # THE OPTIONS ARE LISTED ONCE, in the block. An earlier version printed them
    # above AND in the block, in different words, which is worse than repeating
    # them verbatim: two descriptions of one option invite the reader to work out
    # whether they are the same option. Only genuinely long content belongs above -
    # here, the generated topic string, which is 36 characters nobody needs while
    # they are still deciding whether they want notifications at all.
    show("  Delivered by the ntfy app on your phone.")
    suggested = suggest_topic()
    show(f"  A topic is ready if you want one:  {suggested}")

    question_block(
        show,
        choosing="how push notifications reach your phone",
        why="Without one, replies still arrive - you just have to look MANUALLY.",
        current="none configured",
        keys=[
            "1)|use the private topic above (nothing to type)",
            "2)|a topic you already subscribe to",
            "3)|your own ntfy server, and a topic on it",
            "4)|skip - no notifications until you set one later",
        ],
    )

    while True:
        # Only option 3 changes the server, and subscribing on the WRONG server is
        # a silent failure - the app shows a topic that simply never fires. So the
        # server is named alongside the topic exactly when it is not the default.
        server_note = ""
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
            server = server.rstrip("/")
            set_value(config_path, "ntfy_server", server)
            server_note = server
        elif choice == "4":
            show("  skipped - no banners. Set one later by writing a topic to:")
            show(f"    {cfg.ntfy_topic_file}")
            return False
        else:
            show("  please choose 1, 2, 3 or 4.")
            continue

        cfg.ntfy_topic_file.parent.mkdir(parents=True, exist_ok=True)
        cfg.ntfy_topic_file.write_text(topic + "\n", encoding="utf-8")
        # NAME THE TOPIC in the instruction, for a generated one and a typed one
        # alike. "Subscribe to that topic" points at a string the user must scroll
        # back to find, or - for option 1 - never typed at all and cannot be
        # expected to have memorised. The step is not done until the phone is
        # subscribed, so the thing they must type on the phone belongs in the
        # sentence telling them to type it.
        show(f"  ACCEPTED - topic saved to {cfg.ntfy_topic_file}")
        show(f"  Ensure you are subscribed to  {topic}  in the ntfy app on your phone.")
        if server_note:
            show(f"  Server: {server_note}")
        return True


# --------------------------------------------------------------------------- #
# step 4 - the test message
# --------------------------------------------------------------------------- #


def step_test_message(
    cfg: Config,
    *,
    ask: Ask,
    show: Show,
    run_verify: Callable[[], dict],
    probe: str,
) -> bool:
    """Run the round trip, say what should have appeared, and ASK.

    The old version printed `[ok  ] a message makes the round trip` and moved on.
    That line is true and insufficient: it reports what the two machines agreed
    about between themselves, and the user is standing there holding the device
    that the whole product is for. A green tick they cannot corroborate is exactly
    the false-confidence shape this project keeps removing - and if their phone
    showed nothing, the flow ended anyway, with no next step and no way to say so.

    So the machine result is stated as the machine result, what they should be able
    to SEE is listed explicitly, and then they are asked. A "no" is not a failure
    to report - it is the beginning of the part we can actually help with.
    """
    _step(show, "A test message", does="send one message the whole way round and check it")

    show("  This tests the MACHINE round trip: a message out to your list and back.")
    show("  It cannot see your phone's screen - only you can confirm that half.")
    if not _yes(ask, "  Send a real message round-trip now?", show=show):
        show("  skipped - check it later with:  voice-bridge setup --verify")
        return False

    # A LOOP, not recursion: a retry must not re-ask permission to send. Someone
    # who just answered "send it again" has already given it, and asking twice for
    # the same consent is how a retry starts to feel like a loop you cannot leave.
    while True:
        result = run_verify()
        machine_ok = bool(result["dictation_delivered"] and result["reply_delivered"])
        show(f"  [{'ok  ' if machine_ok else 'FAIL'}] machine round trip")
        if not machine_ok:
            show("       the message did not complete the trip - `voice-bridge doctor` says what.")
            return False

        topic = _topic_of(cfg)
        show("")
        show("  On your phone you should now see:")
        show(f"    - a reminder titled  {probe}")
        show(f"      in the list  {cfg.output_list}")
        show("      It is marked DONE immediately, so look under Completed if you missed it.")
        if topic:
            show(f"    - a push notification with the same text (topic {topic})")
        else:
            show("    - no push notification: you skipped that step.")

        if _yes(ask, "  Did you see them?", show=show):
            show("  ACCEPTED - the bridge works end to end.")
            return True

        # Never a dead end. Each line is a thing they can do NOW, ordered by how
        # often it is the actual cause.
        show("")
        show("  Most likely, in order:")
        show("    1. Sync lag. iCloud is not instant - wait a moment and look again.")
        # The id is the unambiguous half, so it is shown when there is one - and
        # simply omitted when there is not, rather than printed as an empty [].
        chosen = (
            f"{cfg.output_list}  [{cfg.output_list_id}]" if cfg.output_list_id else cfg.output_list
        )
        show(f"    2. Wrong list. You chose  {chosen} -")
        show("       check that is the list you are looking at on the phone.")
        if topic:
            show(f"    3. Not subscribed. Open the ntfy app and subscribe to  {topic}")
            show(f"       on  {cfg.ntfy_server}")
        else:
            show("    3. No notifications configured - re-run setup to add them.")
        show("")
        if not _yes(ask, "  Send it again?", show=show):
            show("  Moving on. Re-test any time with:  voice-bridge setup --verify")
            return False


def _topic_of(cfg: Config) -> str:
    """The configured banner topic, or "" - read the same way ntfy reads it."""
    try:
        return cfg.ntfy_topic_file.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


# --------------------------------------------------------------------------- #
# step 5 - the phone prompt
# --------------------------------------------------------------------------- #


def _copy_windows(text: str) -> bool:
    """Put text on the Windows clipboard without going through a codepage.

    `clip.exe` decodes whatever is piped to it using the CONSOLE CODE PAGE, not
    UTF-8. Measured on this machine, piping UTF-8 bytes for "A-B rocket 🚀":

        chcp 65001  ->  A-B rocket 🚀        (clean)
        chcp 1252   ->  Aâ€"B rocket ðŸš€    (mangled)
        chcp  437   ->  AΓÇöB rocket ≡ƒÜÇ    (worse)

    A default Windows console is 1252 or 437, so the old pipe was correct only by
    the accident of the terminal it happened to run in - and the failure is silent
    and lands in the user's clipboard.

    So the text never crosses a codepage boundary: it is written as UTF-8 bytes to
    a file, and PowerShell is told explicitly to read it as UTF-8 and hand the
    resulting string to the clipboard API. Every step names its encoding, which is
    what makes emoji and any future non-ASCII safe rather than lucky.
    """
    tmp = tempfile.NamedTemporaryFile(  # noqa: SIM115 - closed before PowerShell reads it
        mode="wb", suffix=".txt", delete=False
    )
    try:
        tmp.write(text.encode("utf-8"))
        tmp.close()
        subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                f"Set-Clipboard -Value (Get-Content -Raw -Encoding UTF8 -LiteralPath '{tmp.name}')",
            ],
            check=True,
            capture_output=True,
        )
        return True
    except (OSError, subprocess.SubprocessError):
        return False
    finally:
        with contextlib.suppress(OSError):
            os.unlink(tmp.name)


def copy_to_clipboard(text: str) -> bool:
    """Best-effort clipboard write. False when there is no tool to do it with.

    Encoding-correct on every platform: what comes back out must equal what went
    in, including em dashes and emoji.
    """
    if sys.platform == "win32":
        return _copy_windows(text)

    if sys.platform == "darwin":
        cmd = ["pbcopy"]
    elif shutil.which("wl-copy"):
        cmd = ["wl-copy"]
    elif shutil.which("xclip"):
        cmd = ["xclip", "-selection", "clipboard"]
    else:
        return False
    try:
        # pbcopy/wl-copy/xclip take bytes as given; no console codepage involved.
        subprocess.run(cmd, input=text.encode("utf-8"), check=True)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def step_phone_prompt(cfg: Config, *, ask: Ask, show: Show) -> None:
    """Hand over the prompt - asking before touching the clipboard.

    Overwriting a clipboard unasked is a small theft of something the user may
    have been holding on purpose. The prompt is long enough that copying it is
    genuinely useful, which is exactly why it has to be a question.
    """
    from .prompt import render_vox_prompt

    _step(show, "Your phone prompt", does="the instructions you paste into the app on your phone")
    text = render_vox_prompt(cfg)

    show("  The instruction set your phone runs. It carries the lists you chose -")
    show("  names and ids - so the phone never has to guess which list you meant.")

    # PRINTED UNCONDITIONALLY, and before the question. Showing it only when the
    # copy is declined would make the prompt feel like a consolation prize for
    # saying no, and it leaves someone who said yes with nothing on screen to
    # check against - while the clipboard is the one thing here that belongs to
    # the user, so taking it stays a question with a NO default.
    show("")
    show(text)
    show("")
    show("  ^ paste the text above into the Claude app on your phone.")
    show("  You can print it again any time with:  voice-bridge vox-prompt")

    if _yes(ask, "  Also copy it to your clipboard?", default=False, show=show):
        if copy_to_clipboard(text):
            show("  copied.")
        else:
            show("  no clipboard tool available here - copy the text above by hand.")


# --------------------------------------------------------------------------- #
# the whole conversation
# --------------------------------------------------------------------------- #


def welcome(show: Show) -> None:
    # Ruled: "in general don't baby the user." The reassurance about asking first
    # is kept because it is a factual constraint on the program's behaviour, not
    # encouragement - but it is stated once, flatly, and not repeated per step.
    show("Setting up your voice bridge - talk to your agents from your phone.")
    show("")
    # NAMES, not a count - the same maintenance trap as the per-step `[n/5]`. A
    # count is a fact about the flow stated in a second place, so changing the flow
    # silently makes it false; a list of names stays honest.
    show("  Steps: credentials, your lists, notifications, a test, the phone prompt.")
    show("  Nothing changes on your machine or your accounts without asking first.")
    show("  Every step can be skipped; each names the command to do it later.")


def farewell(show: Show, *, ready: bool) -> None:
    show("")
    if ready:
        show("You are set up. Start the bridge with:  voice-bridge run")
        show("Then talk to your phone - dictations arrive in your mailbox, replies come back.")
        return
    show("Setup is incomplete - the steps you skipped are named above, each with its")
    show("command. `voice-bridge doctor` will tell you what is still missing.")


def reload_config(path: Path) -> Config:
    """Re-read after a step wrote to the file, so later steps see the new state."""
    return load_config(path)
