"""server: init writes config + bcrypt user + client creds; url + down-status. No network."""

from __future__ import annotations

import json
import sys

import pytest

from voice_bridge import server
from voice_bridge.config import load_config
from voice_bridge.util import read_secret


def _cfg(tmp_path, tmp_mailbox, port=5299):
    p = tmp_path / "voice-bridge.json"
    p.write_text(
        json.dumps(
            {
                "transport": "radicale",
                "mailbox_dir": str(tmp_mailbox),
                "state_dir": str(tmp_path / "state"),
                "radicale_port": port,
            }
        ),
        encoding="utf-8",
    )
    return load_config(p)


def test_init_writes_config_user_and_creds(tmp_path, tmp_mailbox):
    cfg = _cfg(tmp_path, tmp_mailbox, port=5299)
    server.init(cfg, user="tester", password="secret")
    p = server.paths(cfg)
    assert "hosts = 0.0.0.0:5299" in p.config.read_text(encoding="utf-8")
    assert p.users.read_text(encoding="utf-8").startswith("tester:$2b$")  # bcrypt, no plaintext
    creds = cfg.creds_env.read_text(encoding="utf-8")
    assert "ICLOUD_CALDAV_URL=http://127.0.0.1:5299" in creds
    assert "ICLOUD_APPLE_ID=tester" in creds
    assert "ICLOUD_APP_PASSWORD=secret" in creds


def test_client_url_from_port(tmp_path, tmp_mailbox):
    assert server.client_url(_cfg(tmp_path, tmp_mailbox, port=5300)) == "http://127.0.0.1:5300"


def test_status_when_down(tmp_path, tmp_mailbox):
    s = server.status(_cfg(tmp_path, tmp_mailbox, port=5399))
    assert s["reachable"] is False and s["pid"] is None and s["initialised"] is False


def test_stop_when_not_running(tmp_path, tmp_mailbox, capsys):
    assert server.stop(_cfg(tmp_path, tmp_mailbox, port=5399)) == 0
    assert "not running" in capsys.readouterr().out


# =========================================================================== #
# Phase F — credential isolation and honest failures (SERVER-1/6/7/9)
# =========================================================================== #


def _icloud_cfg(tmp_path, tmp_mailbox):
    """A config on the iCloud transport — the dangerous case for `init`."""
    p = tmp_path / "voice-bridge.json"
    p.write_text(
        json.dumps(
            {
                "transport": "icloud",
                "mailbox_dir": str(tmp_mailbox),
                "state_dir": str(tmp_path / "state"),
            }
        ),
        encoding="utf-8",
    )
    return load_config(p)


# --------------------------------------------------------------------------- #
# SERVER-7 — init must never write into the iCloud credentials file
# --------------------------------------------------------------------------- #


def test_init_never_overwrites_the_icloud_creds(tmp_path, tmp_mailbox):
    """The worst bug in this module: `creds_env` is `{state_dir}/{transport}.env`.

    Run `radicale-server init` while `transport=icloud` and it wrote Radicale
    credentials straight over `icloud.env` — destroying an Apple password that
    cost a 2FA round trip to obtain, with no warning and no way back.
    """
    cfg = _icloud_cfg(tmp_path, tmp_mailbox)
    cfg.creds_env.parent.mkdir(parents=True, exist_ok=True)
    cfg.creds_env.write_text(
        "ICLOUD_APPLE_ID=me@icloud.com\nICLOUD_PASSWORD=precious\n", encoding="utf-8"
    )

    server.init(cfg, user="tester", password="secret")

    assert read_secret(cfg.creds_env, "ICLOUD_PASSWORD") == "precious", (
        "the Apple credentials must survive untouched"
    )
    radicale_env = cfg.state_dir / "radicale.env"
    assert read_secret(radicale_env, "ICLOUD_APP_PASSWORD") == "secret"


def test_init_targets_radicale_env_even_on_the_radicale_transport(tmp_path, tmp_mailbox):
    """Explicit beats derived: the file is named for what it holds."""
    cfg = _cfg(tmp_path, tmp_mailbox)
    server.init(cfg, user="tester", password="secret")
    assert read_secret(cfg.state_dir / "radicale.env", "ICLOUD_APPLE_ID") == "tester"


# --------------------------------------------------------------------------- #
# SERVER-6 — rotating a password is a decision, not a side effect
# --------------------------------------------------------------------------- #


def test_init_refuses_to_clobber_existing_creds_without_force(tmp_path, tmp_mailbox, capsys):
    cfg = _cfg(tmp_path, tmp_mailbox)
    server.init(cfg, user="tester", password="first")

    with pytest.raises(FileExistsError):
        server.init(cfg, user="tester", password="second")

    assert read_secret(cfg.state_dir / "radicale.env", "ICLOUD_APP_PASSWORD") == "first"


def test_force_rotates_the_password(tmp_path, tmp_mailbox):
    cfg = _cfg(tmp_path, tmp_mailbox)
    server.init(cfg, user="tester", password="first")
    server.init(cfg, user="tester", password="second", force=True)
    assert read_secret(cfg.state_dir / "radicale.env", "ICLOUD_APP_PASSWORD") == "second"


# --------------------------------------------------------------------------- #
# SERVER-2 — the credentials file is owner-only where the platform allows
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_creds_file_is_owner_only(tmp_path, tmp_mailbox):
    cfg = _cfg(tmp_path, tmp_mailbox)
    server.init(cfg, user="tester", password="secret")
    mode = (cfg.state_dir / "radicale.env").stat().st_mode
    assert oct(mode)[-3:] == "600"


# --------------------------------------------------------------------------- #
# init warns about what it just exposed
# --------------------------------------------------------------------------- #


def test_init_warns_about_binding_publicly_over_plain_http(tmp_path, tmp_mailbox, capsys):
    """`0.0.0.0` plus plain HTTP means anyone who can reach the port can read
    every dictation. That deserves saying out loud, once, at the moment it is set."""
    cfg = _cfg(tmp_path, tmp_mailbox)
    server.init(cfg, user="tester", password="secret")
    out = capsys.readouterr().out.lower()
    assert "0.0.0.0" in out or "private" in out
    assert "http" in out


# --------------------------------------------------------------------------- #
# SERVER-9 — a missing optional dependency is a sentence, not a traceback
# --------------------------------------------------------------------------- #


def test_missing_bcrypt_explains_the_extra(tmp_path, tmp_mailbox, monkeypatch):
    cfg = _cfg(tmp_path, tmp_mailbox)

    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __import__

    def no_bcrypt(name, *a, **k):
        if name == "bcrypt":
            raise ImportError("No module named 'bcrypt'")
        return real_import(name, *a, **k)

    monkeypatch.setattr("builtins.__import__", no_bcrypt)
    with pytest.raises(server.ServerExtraMissing) as err:
        server.init(cfg, user="tester", password="secret")
    assert "server" in str(err.value)


def test_server_liveness_uses_the_one_probe(sample_config):
    """Found in a QA dress rehearsal, and it had teeth.

    This module carried its OWN `_alive` using `os.kill(pid, 0)` - the exact bug
    FMA-17 fixed in `status._alive`, missed because there were two copies. On
    Windows that call is not a query, so a RUNNING server was reported dead:
    `status` showed `pid: None`, `stop` would have said "not running" and deleted
    the pidfile, and - worst - `teardown` asks `server.status()` whether it is safe
    to delete the store, so `uninstall` would have removed `collections/` out from
    under a live server.

    Pinning the identity, not just the behaviour: a second copy is a second chance
    to be wrong, and this is the copy that was.
    """
    from voice_bridge import server, status

    assert server._alive is status._alive


def test_a_running_process_is_reported_as_running(sample_config, tmp_path):
    """The behaviour the identity above buys: our own pid is alive by definition."""
    import dataclasses
    import os

    from voice_bridge import server

    cfg = dataclasses.replace(sample_config, state_dir=tmp_path)
    paths = server.paths(cfg)
    paths.base.mkdir(parents=True, exist_ok=True)
    paths.pidfile.write_text(str(os.getpid()), encoding="utf-8")

    assert server.status(cfg)["pid"] == os.getpid(), "a live pid must be reported live"
