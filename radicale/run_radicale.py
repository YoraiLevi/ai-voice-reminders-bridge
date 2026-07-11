# /// script
# requires-python = ">=3.11"
# dependencies = ["radicale>=3.2,<4", "bcrypt>=4"]
# ///
"""Launch the phone-bridge Radicale CalDAV server.

`uv run run_radicale.py` auto-installs Radicale + bcrypt into an ephemeral env
(no venv to manage), chdir's to this directory so the relative paths in `config`
resolve, then starts Radicale bound to 0.0.0.0:5232 per that config.

The server hosts the "Claude Inbox" / "Claude Output" VTODO lists that BOTH the
iPhone (Reminders → Add CalDAV Account) and the Windows bridge read/write — a
CloudKit-free bus, replacing iCloud CalDAV (which is dead for phone↔Windows).

Before first launch, create the single user's htpasswd file:
    uv run make_user.py            # prompts for user + password (bcrypt-hashed)

Then, from radicale/:
    uv run run_radicale.py         # serves forever; Ctrl-C to stop

The phone reaches it at  http://<this-host-tailnet-name>:5232/  (see OWNER-SETUP.md).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG = HERE / "config"
USERS = HERE / "_secrets" / "users"


def main() -> int:
    if not USERS.exists():
        print(
            f"error: no htpasswd file at {USERS}\n"
            "Create the Radicale user first:  uv run make_user.py",
            file=sys.stderr,
        )
        return 2

    # Radicale resolves the relative paths in `config` against the CWD.
    os.chdir(HERE)

    # Radicale reads its config path from argv; hand it ours and delegate to its
    # own entry point (same as running `radicale --config config`).
    sys.argv = ["radicale", "--config", str(CONFIG)]
    from radicale.__main__ import run

    print(f"starting Radicale with {CONFIG} (storage: {HERE / '_data' / 'collections'})")
    run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
