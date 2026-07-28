"""Manage the self-hosted Radicale CalDAV server - the server half of the Radicale
transport, folded in from the former root `radicale/` dir. Everything lives under
`state_dir/radicale/`; `init` also seeds the client creds so the two can't drift.

The `[server]` extra (`radicale`, `bcrypt`) is only needed to run the server.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .util import write_env


@dataclass(frozen=True)
class ServerPaths:
    base: Path
    config: Path
    users: Path
    storage: Path
    pidfile: Path


def paths(cfg: Config) -> ServerPaths:
    base = cfg.state_dir / "radicale"
    return ServerPaths(
        base=base,
        config=base / "config",
        users=base / "users",
        storage=base / "collections",
        pidfile=base / "server.pid",
    )


def client_url(cfg: Config) -> str:
    """The URL the PC-side client connects to (localhost, regardless of bind host)."""
    return f"http://127.0.0.1:{cfg.radicale_port}"


def _render_config(cfg: Config, p: ServerPaths, host: str, port: int) -> str:
    return f"""# voice-bridge managed Radicale config (generated - edit via config fields)
[server]
hosts = {host}:{port}
max_connections = 20

[auth]
type = htpasswd
htpasswd_filename = {p.users}
htpasswd_encryption = bcrypt

[rights]
type = owner_only

[storage]
filesystem_folder = {p.storage}

[logging]
level = info
"""


class ServerExtraMissing(RuntimeError):
    """The `[server]` extra is not installed. Says which, rather than tracebacking."""


def _make_user(users: Path, user: str, password: str) -> None:
    """Write a bcrypt htpasswd line `user:$2b$...` - the password is never stored plain."""
    try:
        import bcrypt
    except ImportError as exc:
        raise ServerExtraMissing(
            "the self-hosted server needs its optional dependencies - install voice-bridge[server]"
        ) from exc

    digest = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")
    users.parent.mkdir(parents=True, exist_ok=True)
    users.write_text(f"{user}:{digest}\n", encoding="utf-8")


def radicale_creds_path(cfg: Config) -> Path:
    """Where THIS server's credentials live - always `radicale.env`, never derived.

    `cfg.creds_env` is `{state_dir}/{transport}.env`, so on the iCloud transport it
    names `icloud.env`. Writing there would overwrite an Apple password that cost a
    two-factor round trip to obtain, silently and irrecoverably (SERVER-7). The
    file is named for what it holds, not for whichever transport happens to be
    selected when the command runs.
    """
    return cfg.state_dir / "radicale.env"


def init(
    cfg: Config,
    *,
    user: str | None = None,
    password: str | None = None,
    host: str | None = None,
    port: int | None = None,
    force: bool = False,
) -> None:
    """Generate the server config + bcrypt user + storage dir, and seed `radicale.env`
    so the transport can reach this server.

    Refuses to overwrite existing credentials unless `force` is set: rotating a
    password is a decision, and re-running `init` to change a port should not
    silently invalidate the account the phone is already using (SERVER-6).
    """
    p = paths(cfg)
    user = user or cfg.radicale_user
    password = password or os.environ.get("RADICALE_PASSWORD") or _prompt_password()
    host = host or cfg.radicale_host
    port = port or cfg.radicale_port

    creds_path = radicale_creds_path(cfg)
    if creds_path.exists() and not force:
        raise FileExistsError(
            f"{creds_path} already holds credentials - re-run with --force to rotate "
            "the password (the phone's CalDAV account will need updating to match)"
        )

    p.base.mkdir(parents=True, exist_ok=True)
    p.storage.mkdir(parents=True, exist_ok=True)
    p.config.write_text(_render_config(cfg, p, host, port), encoding="utf-8")
    _make_user(p.users, user, password)

    # 0600 where the platform supports it, and verbatim - the password may
    # contain anything the user typed.
    write_env(
        creds_path,
        {
            "ICLOUD_CALDAV_URL": client_url(cfg),
            "ICLOUD_APPLE_ID": user,
            "ICLOUD_APP_PASSWORD": password,
        },
    )

    print(f"server configured; credentials written to {creds_path}")
    if host in ("0.0.0.0", "::"):
        # Said once, at the moment it becomes true: anyone who can reach the port
        # can read every dictation, because this speaks plain HTTP.
        print(
            f"warning: binding {host} over plain HTTP - anyone who can reach port {port} "
            "can read your messages. Keep it on a private network or tunnel (no TLS here)."
        )


def _prompt_password() -> str:  # pragma: no cover - interactive
    import getpass

    pw = getpass.getpass("Radicale password (new): ")
    if not pw:
        raise ValueError("a Radicale password is required")
    return pw


def is_reachable(url: str, *, timeout: float = 2.0) -> bool:
    """True if the server answers at all (even 401) - i.e. it's up."""
    try:
        req = urllib.request.Request(url, method="GET")
        urllib.request.urlopen(req, timeout=timeout)
        return True
    except urllib.error.HTTPError:
        return True  # 401/405 etc. = server is up
    except Exception:
        return False


def _spawn(cfg: Config, *, detached: bool) -> subprocess.Popen:
    p = paths(cfg)
    cmd = [sys.executable, "-m", "radicale", "--config", str(p.config)]
    kwargs: dict = {}
    if detached:
        if os.name == "nt":
            # getattr so mypy on non-Windows doesn't flag these Windows-only constants
            # (CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS). Runtime path is nt-only.
            new_group = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            kwargs["creationflags"] = new_group | 0x00000008  # DETACHED_PROCESS
        else:
            kwargs["start_new_session"] = True
        log = open(p.base / "server.log", "ab")
        kwargs["stdout"] = log
        kwargs["stderr"] = log
    return subprocess.Popen(cmd, **kwargs)


def start(cfg: Config, *, background: bool = False) -> int:
    """Foreground (blocks) or detached background (writes a pidfile, waits until reachable)."""
    p = paths(cfg)
    if not p.config.exists():
        print(f"error: not initialised - run `voice-bridge radicale-server init` (no {p.config})")
        return 2
    if is_reachable(client_url(cfg)):
        print(f"already running at {client_url(cfg)}")
        return 0
    if not background:
        proc = _spawn(cfg, detached=False)
        try:
            return proc.wait()
        except KeyboardInterrupt:
            proc.terminate()
            return 0
    proc = _spawn(cfg, detached=True)
    p.pidfile.write_text(str(proc.pid), encoding="utf-8")
    import time

    for _ in range(30):
        if is_reachable(client_url(cfg)):
            print(f"started (pid {proc.pid}) at {client_url(cfg)}")
            return 0
        time.sleep(0.2)
    print("started but not reachable yet - check server.log")
    return 1


def _pid(cfg: Config) -> int | None:
    p = paths(cfg)
    if not p.pidfile.exists():
        return None
    try:
        return int(p.pidfile.read_text(encoding="utf-8").strip())
    except ValueError:
        return None


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def stop(cfg: Config) -> int:
    pid = _pid(cfg)
    if pid is None or not _alive(pid):
        print("not running.")
        paths(cfg).pidfile.unlink(missing_ok=True)
        return 0
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError as exc:
        print(f"error stopping pid {pid}: {exc}")
        return 2
    paths(cfg).pidfile.unlink(missing_ok=True)
    print(f"stopped (pid {pid}).")
    return 0


def status(cfg: Config) -> dict:
    pid = _pid(cfg)
    return {
        "url": client_url(cfg),
        "reachable": is_reachable(client_url(cfg)),
        "pid": pid if (pid and _alive(pid)) else None,
        "initialised": paths(cfg).config.exists(),
    }


def ensure_running(cfg: Config, *, spawn_child: bool = False) -> subprocess.Popen | None:
    """For `run`: if already reachable, no-op. Else spawn a CHILD (not detached - dies
    with the caller) when spawn_child, so `run --with-server` owns its lifecycle. Returns
    the child Popen (caller must terminate it) or None."""
    if is_reachable(client_url(cfg)):
        return None
    if not spawn_child:
        return None
    child = _spawn(cfg, detached=False)
    import time

    for _ in range(30):
        if is_reachable(client_url(cfg)):
            break
        time.sleep(0.2)
    return child
