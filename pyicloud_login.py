# /// script
# requires-python = ">=3.11"
# dependencies = ["pyicloud"]
# ///
"""One-time pyicloud login + 2FA + a Reminders read proof (config-driven).

Reads the modern (CloudKit) iCloud Reminders that CalDAV cannot see, via Apple's
own private web API. First run needs an interactive 2FA code; because this runs
under a non-interactive tool, the code is picked up from a FILE that the manager
drops once the owner reads it off their trusted device. After a successful login
the session is TRUSTED and cached in the config's cookie_dir, so later runs skip
2FA until Apple expires the token (~60 days).

Creds (never in chat/git) — locations come from the resolved config:
  <creds_env>              -> ICLOUD_APPLE_ID=...  and optionally ICLOUD_PASSWORD
  <creds_env dir>/icloud-main.env -> ICLOUD_PASSWORD=...  (MAIN Apple ID password,
                              NOT the app-specific one; if not already in creds_env)
2FA code hand-off:
  <creds_env dir>/2fa_code.txt  -> the 6-digit code (manager writes it, script eats it)

Never prints the password.

Usage:
  uv run pyicloud_login.py
  uv run pyicloud_login.py --config path/to/voice-bridge.json
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from config import ConfigError, load_config


def _read_kv(path: Path, key: str) -> str | None:
    if not path.exists():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith(key + "="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", metavar="PATH", help="explicit voice-bridge.json")
    args = ap.parse_args(argv)

    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    from pyicloud import PyiCloudService

    creds_env = cfg.creds_env
    auth_dir = creds_env.parent
    main_env = auth_dir / "icloud-main.env"
    code_file = auth_dir / "2fa_code.txt"

    apple_id = _read_kv(creds_env, "ICLOUD_APPLE_ID")
    # ICLOUD_PASSWORD may live in creds_env (where the owner put it) or a
    # dedicated icloud-main.env — accept either.
    password = _read_kv(creds_env, "ICLOUD_PASSWORD") or _read_kv(main_env, "ICLOUD_PASSWORD")
    if not apple_id:
        print(f"MISSING ICLOUD_APPLE_ID in {creds_env}")
        return 2
    if not password:
        print(f"MISSING ICLOUD_PASSWORD in {main_env} — create that file with one line: "
              "ICLOUD_PASSWORD=<your MAIN Apple ID password>")
        return 2

    cfg.cookie_dir.mkdir(parents=True, exist_ok=True)
    print(f"logging in as {apple_id[:1]}***@... (password hidden)", flush=True)
    api = PyiCloudService(apple_id, password, cookie_directory=str(cfg.cookie_dir))

    if api.requires_2fa:
        code_file.unlink(missing_ok=True)
        print("2FA REQUIRED — Apple just sent a 6-digit code to your trusted devices.", flush=True)
        print(f"Waiting up to 5 min for the code (drop into {code_file})...", flush=True)
        code = None
        for _ in range(100):
            if code_file.exists():
                code = code_file.read_text(encoding="utf-8").strip()
                if code:
                    break
            time.sleep(3)
        if not code:
            print("TIMEOUT — no 2FA code arrived.")
            return 3
        ok = api.validate_2fa_code(code)
        code_file.unlink(missing_ok=True)
        print("2FA code accepted:", ok, flush=True)
        if not ok:
            return 4
        if not api.is_trusted_session:
            try:
                api.trust_session()
                print("session trusted (future runs skip 2FA)", flush=True)
            except Exception as exc:
                print(f"warn: trust_session failed ({exc}); may re-prompt next run")
    else:
        print("already authenticated via a cached trusted session", flush=True)

    # --- proof: read the real Reminders ---
    r = api.reminders
    try:
        r.refresh()
    except Exception:
        pass
    try:
        lists = list(r.lists())
    except Exception as exc:
        print(f"REMINDERS READ FAILED: {type(exc).__name__}: {exc}")
        return 5
    print(f"REMINDERS OK — {len(lists)} list(s) visible via pyicloud:", flush=True)
    for lst in lists[:30]:
        title = getattr(lst, "title", None) or getattr(lst, "name", None) or repr(lst)
        print("   -", title)
    return 0


if __name__ == "__main__":
    sys.exit(main())
