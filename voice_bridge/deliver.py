"""`deliver` - surface long content the phone can't hold, by publishing a file as a
GitHub gist and pushing a tappable banner whose Click opens it.

This is the only command that hands your data to a third party, so it holds itself
to a higher bar than the rest of the tool on two counts:

**The question it asks.** The old prompt said "publish X as a gist to get a link?",
which understated it twice - it did not say the file's CONTENTS leave the machine,
and it implied a "private" gist is secret. It is not: GitHub's private gists are
*unlisted*, readable by anyone holding the URL. That matters here specifically,
because the URL is then sent through a notification topic which is itself a bearer
secret.

**What it reports afterwards.** The banner result was discarded, so a failed
notification printed the URL, never reached the phone, and still exited 0.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from . import ntfy
from .config import Config


def _is_tty() -> bool:
    return sys.stdin.isatty()


def _ask_consent(prompt: str) -> bool:  # pragma: no cover - interactive
    return input(prompt).strip().lower().startswith("y")


def _consent_prompt(file: str, *, public: bool) -> str:
    """Describe what is actually about to happen, in the words that matter."""
    if public:
        visibility = "It will be PUBLIC: listed on your profile, searchable, readable by anyone."
    else:
        visibility = (
            "A private gist is UNLISTED, not secret - anyone with the link can read it, "
            "and that link is about to be sent to your phone through a notification topic."
        )
    return (
        f"This uploads the contents of {file} to GitHub as a gist.\n{visibility}\nContinue? [y/N] "
    )


def deliver(
    cfg: Config,
    file: str,
    *,
    summary: str | None = None,
    public: bool = False,
    assume_yes: bool = False,
) -> int:
    """Publish `file` and notify. Exit 0 published (or declined), 2 refused / failed."""
    if not shutil.which("gh"):
        print("error: `gh` not installed / not on PATH - see https://cli.github.com")
        return 2

    if not assume_yes:
        if not _is_tty():
            # Refusing beats crashing on EOF, and beats uploading unasked: consent
            # for an external upload cannot be inferred from silence.
            print(
                "error: refusing to publish without confirmation - no terminal to ask on.\n"
                "       pass --yes if you are sure you want the file uploaded to GitHub."
            )
            return 2
        try:
            if not _ask_consent(_consent_prompt(file, public=public)):
                print("aborted - nothing published.")
                return 0
        except EOFError:  # isatty can lie; no answer means no consent
            print("error: refusing to publish without confirmation.")
            return 2

    cmd = ["gh", "gist", "create", *(["--public"] if public else []), str(file)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        print(f"error: gist create failed: {proc.stderr.strip()}")
        return 2

    url = (proc.stdout.strip().splitlines() or [""])[-1]
    # The URL is the deliverable, so it is printed FIRST and unconditionally: a
    # notification failure must never cost the user the link they just published.
    print(url)

    pushed = ntfy.push(cfg, summary or f"content ready: {Path(file).name}", click=url)
    if pushed.status != "sent":
        print(f"warn: published, but the phone was not notified ({pushed.detail}).")
    return 0
