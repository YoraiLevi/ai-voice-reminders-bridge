"""util: env-file parsing and secret masking."""

from __future__ import annotations

from voice_bridge import util


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
