"""iCloud 2FA login (folds in pyicloud_login.py). The code can come from a flag, a
file, stdin, or an interactive prompt."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TextIO

from .config import Config
from .util import read_kv


def resolve_code(
    *, code: str | None, code_file: str | None, code_stdin: bool, stdin: TextIO | None = None
) -> str:
    """Pick the 2FA code from (in order) --code, --code-file, stdin, or a prompt. Pure
    apart from the optional prompt, so the flag/file/stdin selection is unit-testable."""
    if code:
        return code.strip()
    if code_file:
        return Path(code_file).read_text(encoding="utf-8").strip()
    if code_stdin:
        return (stdin or sys.stdin).readline().strip()
    return input("Enter the 6-digit 2FA code: ").strip()


def icloud_login(
    cfg: Config, *, code: str | None = None, code_file: str | None = None, code_stdin: bool = False
) -> int:
    apple_id = read_kv(cfg.creds_env, "ICLOUD_APPLE_ID")
    password = read_kv(cfg.creds_env, "ICLOUD_PASSWORD")
    if not apple_id or not password:
        print(f"error: missing ICLOUD_APPLE_ID / ICLOUD_PASSWORD in {cfg.creds_env}")
        return 2
    try:
        from pyicloud import PyiCloudService
    except ImportError:
        print("error: pyicloud not installed — `pip install 'voice-bridge[icloud]'`")
        return 2

    cfg.cookie_dir.mkdir(parents=True, exist_ok=True)
    api = PyiCloudService(apple_id, password, cookie_directory=str(cfg.cookie_dir))
    if not getattr(api, "requires_2fa", False):
        print("session already trusted.")
        return 0
    value = resolve_code(code=code, code_file=code_file, code_stdin=code_stdin)
    if not value:
        print("error: no 2FA code provided")
        return 2
    if api.validate_2fa_code(value):
        print("trusted; session cached (~60 days).")
        return 0
    print("error: 2FA code rejected")
    return 1
