# /// script
# requires-python = ">=3.11"
# dependencies = ["bcrypt>=4"]
# ///
"""Create/replace the single Radicale user's htpasswd entry (bcrypt-hashed).

The password is NEVER hardcoded and never printed. It comes from, in order:
  1. $RADICALE_PASSWORD   (for non-interactive/CI use)
  2. an interactive getpass prompt (typed twice, must match)

The username comes from $RADICALE_USER or the first CLI arg, else prompts
(default "claude"). Output is written to `_secrets/users` in Apache htpasswd
format (`user:$2b$...`), which `config` reads with htpasswd_encryption = bcrypt.
`_secrets/` is git-ignored, so the hash never enters version control.

Usage:
    uv run make_user.py                 # prompts for user + password
    uv run make_user.py claude          # user "claude", prompts for password
    RADICALE_USER=claude RADICALE_PASSWORD=... uv run make_user.py   # non-interactive
"""

from __future__ import annotations

import getpass
import os
import sys
from pathlib import Path

import bcrypt

USERS = Path(__file__).resolve().parent / "_secrets" / "users"


def _resolve_user(argv: list[str]) -> str:
    if len(argv) > 1 and argv[1].strip():
        return argv[1].strip()
    env = os.environ.get("RADICALE_USER", "").strip()
    if env:
        return env
    try:
        entered = input("Radicale username [claude]: ").strip()
    except EOFError:
        entered = ""
    return entered or "claude"


def _resolve_password() -> str:
    env = os.environ.get("RADICALE_PASSWORD", "")
    if env:
        return env
    pw = getpass.getpass("Radicale password: ")
    if not pw:
        print("error: empty password", file=sys.stderr)
        raise SystemExit(2)
    again = getpass.getpass("Confirm password: ")
    if pw != again:
        print("error: passwords do not match", file=sys.stderr)
        raise SystemExit(2)
    return pw


def main(argv: list[str]) -> int:
    user = _resolve_user(argv)
    password = _resolve_password()

    digest = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")
    USERS.parent.mkdir(parents=True, exist_ok=True)

    # Preserve any OTHER users; replace this user's line if present.
    lines: list[str] = []
    if USERS.exists():
        for raw in USERS.read_text(encoding="utf-8").splitlines():
            if raw.strip() and not raw.split(":", 1)[0].strip() == user:
                lines.append(raw)
    lines.append(f"{user}:{digest}")
    USERS.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"wrote {USERS} for user {user!r} (bcrypt). Password not stored or printed.")
    print("Start the server with:  uv run run_radicale.py")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
