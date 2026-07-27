"""util: env-file parsing, atomic writes, lossless secret I/O, secret masking."""

from __future__ import annotations

import os
import sys

import pytest

from voice_bridge import config, mailbox, util


def test_read_env_parses_and_skips(tmp_path):
    f = tmp_path / "creds.env"
    f.write_text(
        "# a comment\n\nICLOUD_APPLE_ID=you@icloud.com\nexport ICLOUD_PASSWORD='p@ss'\nJUNK\n",
        encoding="utf-8",
    )
    env = util.read_env(f)
    assert env == {"ICLOUD_APPLE_ID": "you@icloud.com", "ICLOUD_PASSWORD": "p@ss"}
    assert util.read_kv(f, "ICLOUD_APPLE_ID") == "you@icloud.com"


def test_read_env_missing_file_is_empty(tmp_path):
    assert util.read_env(tmp_path / "nope.env") == {}


def test_masked_hides_secret():
    m = util.masked("devices@icloud.com")
    assert m.startswith("d***")
    assert "devices@icloud.com" not in m
    assert util.masked("") == "(unset)"


# --------------------------------------------------------------------------- #
# atomic_write (FMA-11) — a crash mid-write must not corrupt the original
# --------------------------------------------------------------------------- #


def test_atomic_write_roundtrip(tmp_path):
    p = tmp_path / "sub" / "f.json"
    util.atomic_write(p, '{"a": 1}\n')
    assert p.read_text(encoding="utf-8") == '{"a": 1}\n'


def test_atomic_write_replaces_existing(tmp_path):
    p = tmp_path / "f.txt"
    p.write_text("old", encoding="utf-8")
    util.atomic_write(p, "new")
    assert p.read_text(encoding="utf-8") == "new"


def test_atomic_write_crash_leaves_original_and_no_litter(tmp_path, monkeypatch):
    """The whole point: a failure mid-write must be a no-op, not a truncation.

    A direct `write_text` truncates the file before the new bytes land, so a crash
    there leaves a half-written config and every later command fails until a human
    repairs it. Swapping in the finished file with `os.replace` makes the update
    all-or-nothing.
    """
    p = tmp_path / "config.json"
    p.write_text("ORIGINAL", encoding="utf-8")

    def boom(*a, **k):
        raise OSError("disk went away")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        util.atomic_write(p, "REPLACEMENT")

    assert p.read_text(encoding="utf-8") == "ORIGINAL"
    assert [f.name for f in tmp_path.iterdir()] == ["config.json"], "temp file left behind"


def test_write_raw_is_atomic(tmp_path, monkeypatch):
    """The config writer must inherit the guarantee, not just have it available."""
    p = tmp_path / "voice-bridge.json"
    config.write_raw(p, {"poll_interval": 10})
    before = p.read_text(encoding="utf-8")

    monkeypatch.setattr(os, "replace", lambda *a, **k: (_ for _ in ()).throw(OSError("nope")))
    with pytest.raises(OSError):
        config.write_raw(p, {"poll_interval": 30})

    assert p.read_text(encoding="utf-8") == before


def test_cursor_and_seen_writes_are_atomic(tmp_path, monkeypatch):
    """Bookkeeping files too: a corrupt cursor re-delivers or strands replies."""
    cur = tmp_path / "reply.cursor"
    mailbox.save_cursor(cur, 128)
    seen = tmp_path / "seen.txt"
    seen.write_text("a\nb\n", encoding="utf-8")

    monkeypatch.setattr(os, "replace", lambda *a, **k: (_ for _ in ()).throw(OSError("nope")))
    with pytest.raises(OSError):
        mailbox.save_cursor(cur, 999)
    with pytest.raises(OSError):
        mailbox.compact_seen(seen, {"a"})

    assert cur.read_text(encoding="utf-8") == "128"
    assert seen.read_text(encoding="utf-8") == "a\nb\n"


# --------------------------------------------------------------------------- #
# lossless secret I/O (LOGIN-2) — an Apple password is not a shell token
# --------------------------------------------------------------------------- #

NASTY = [
    "p@ss w0rd",  # spaces
    '"quoted"',  # literal surrounding quotes
    "'single'",
    "ends with space ",
    "has#hash",
    "has=equals=signs",
    "unicode-ü-π-🔑",
    "  leading",
    r"back\slash",
]


@pytest.mark.parametrize("secret", NASTY)
def test_secret_roundtrip_is_verbatim(tmp_path, secret):
    """Every byte the user typed must come back, or logins fail inexplicably."""
    f = tmp_path / "creds.env"
    util.write_env(f, {"ICLOUD_APPLE_ID": "you@icloud.com", "ICLOUD_PASSWORD": secret})
    assert util.read_secret(f, "ICLOUD_PASSWORD") == secret
    assert util.read_secret(f, "ICLOUD_APPLE_ID") == "you@icloud.com"


def test_read_secret_is_verbatim_where_read_env_is_lossy(tmp_path):
    """Documents why both exist: `read_env` keeps shell-style convenience.

    Stripping quotes is right for a config-ish env file and wrong for a password,
    where the quotes may genuinely be part of the value.
    """
    f = tmp_path / "creds.env"
    f.write_text('ICLOUD_PASSWORD="literal quotes"\n', encoding="utf-8")
    assert util.read_secret(f, "ICLOUD_PASSWORD") == '"literal quotes"'
    assert util.read_env(f)["ICLOUD_PASSWORD"] == "literal quotes"


def test_read_secret_missing_key_and_file(tmp_path):
    f = tmp_path / "creds.env"
    f.write_text("A=1\n", encoding="utf-8")
    assert util.read_secret(f, "NOPE") is None
    assert util.read_secret(tmp_path / "absent.env", "A") is None


def test_write_env_preserves_unlisted_keys(tmp_path):
    f = tmp_path / "creds.env"
    util.write_env(f, {"A": "1", "B": "2"})
    util.write_env(f, {"B": "3"})
    assert util.read_secret(f, "A") == "1"
    assert util.read_secret(f, "B") == "3"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_write_env_is_owner_only(tmp_path):
    """The file holds a reusable Apple password in cleartext (hardening: #9)."""
    f = tmp_path / "creds.env"
    util.write_env(f, {"ICLOUD_PASSWORD": "hunter2"})
    assert oct(f.stat().st_mode)[-3:] == "600"
