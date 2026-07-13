"""`deliver` — surface long content the phone can't hold: publish a file as a *private*
GitHub gist (with confirmation), then push a tappable ntfy banner whose Click opens it.
Opt-in and never silent — the one useful piece of the removed deliver_content.sh."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from . import ntfy
from .config import Config


def _confirm(file: str) -> bool:  # pragma: no cover - interactive
    return input(f"publish {file} as a gist to get a link? [y/N] ").strip().lower().startswith("y")


def deliver(
    cfg: Config,
    file: str,
    *,
    summary: str | None = None,
    public: bool = False,
    assume_yes: bool = False,
) -> int:
    if not shutil.which("gh"):
        print("error: `gh` not installed / not on PATH — see https://cli.github.com")
        return 2
    if not assume_yes and not _confirm(file):
        print("aborted — nothing published.")
        return 0
    cmd = ["gh", "gist", "create", *(["--public"] if public else []), str(file)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        print(f"error: gist create failed: {proc.stderr.strip()}")
        return 2
    url = (proc.stdout.strip().splitlines() or [""])[-1]
    ntfy.push(cfg, summary or f"content ready: {Path(file).name}", click=url)
    print(url)
    return 0
