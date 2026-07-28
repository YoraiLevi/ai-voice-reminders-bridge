"""Config: defaults, state-dir derivation, routing files, and --set overrides."""

from __future__ import annotations

import dataclasses

import pytest

from voice_bridge import config
from voice_bridge.config import ENV_VAR, ConfigError, load_config, parse_overrides


def test_defaults_are_the_vox_spoke(sample_config):
    cfg = sample_config
    assert cfg.spoke_name == "vox"
    assert cfg.route_to == "manager"
    assert cfg.from_name == "vox"  # derived from spoke_name
    # UX-1: the list names read from the USER's seat — they dictate into their
    # OUTbox, so that is the list the bridge reads (`inbox_list`).
    assert cfg.inbox_list == "Vox-Message-Outbox"
    assert cfg.output_list == "Vox-Message-Inbox"
    assert cfg.transport == "icloud"
    assert cfg.radicale_host == "0.0.0.0"
    assert cfg.radicale_port == 5232
    assert cfg.radicale_user == "vox"


def test_radicale_port_is_int_via_set(tmp_path, tmp_mailbox):
    from voice_bridge.config import parse_overrides

    assert parse_overrides(["radicale_port=5299"]) == {"radicale_port": 5299}


def test_routing_files_derive_from_names(sample_config):
    # our inbox = to-<spoke>.md ; peer inbox = to-<route_to>.md
    assert sample_config.our_inbox.name == "to-vox.md"
    assert sample_config.peer_inbox.name == "to-manager.md"


def test_state_paths_derive_under_state_dir(sample_config):
    sd = sample_config.state_dir
    assert sample_config.creds_env == sd / "icloud.env"
    assert sample_config.cookie_dir == sd / "pyicloud-cookies"
    assert sample_config.ntfy_topic_file == sd / "ntfy-topic.txt"
    # dedupe bookkeeping is private (under state_dir), not in the shared mailbox
    assert sample_config.state_dir in sample_config.seen_file.parents


def test_spoke_name_override_reroutes_files(tmp_path, tmp_mailbox):
    cfg = load_config(
        overrides={"spoke_name": "vox-work", "route_to": "w3"}, path=_write(tmp_path, tmp_mailbox)
    )
    assert cfg.our_inbox.name == "to-vox-work.md"
    assert cfg.peer_inbox.name == "to-w3.md"
    assert cfg.from_name == "vox-work"


def test_overrides_win_over_file(tmp_path, tmp_mailbox):
    cfg = load_config(
        overrides={"poll_interval": 30, "inbox_list": "Custom"}, path=_write(tmp_path, tmp_mailbox)
    )
    assert cfg.poll_interval == 30
    assert cfg.inbox_list == "Custom"


def test_parse_overrides_coerces_ints_and_validates():
    out = parse_overrides(["poll_interval=30", "inbox_list=To X", "ntfy_body_limit=150"])
    assert out == {"poll_interval": 30, "inbox_list": "To X", "ntfy_body_limit": 150}


def test_parse_overrides_rejects_unknown_key():
    with pytest.raises(ValueError, match="unknown setting"):
        parse_overrides(["bogus=1"])


def test_parse_overrides_requires_equals():
    with pytest.raises(ValueError, match="KEY=VALUE"):
        parse_overrides(["justakey"])


def test_bad_json_raises_config_error(tmp_path):
    bad = tmp_path / "voice-bridge.json"
    bad.write_text("{ not json", encoding="utf-8")
    with pytest.raises(ConfigError, match="invalid JSON"):
        load_config(bad)


# --- helpers ---------------------------------------------------------------
def _write(tmp_path, tmp_mailbox):
    import json

    p = tmp_path / "voice-bridge.json"
    p.write_text(
        json.dumps({"mailbox_dir": str(tmp_mailbox), "state_dir": str(tmp_path / "state")}),
        encoding="utf-8",
    )
    return p


# --------------------------------------------------------------------------- #
# CFG-1 — one resolver for read AND write
# --------------------------------------------------------------------------- #


def test_resolve_prefers_explicit_over_everything(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_VAR, str(tmp_path / "from-env.json"))
    explicit = tmp_path / "explicit.json"
    path, provider = config.resolve_config_path(explicit)
    assert path == explicit
    assert "--config" in provider


def test_resolve_honours_the_env_var(tmp_path, monkeypatch):
    target = tmp_path / "from-env.json"
    monkeypatch.setenv(ENV_VAR, str(target))
    path, provider = config.resolve_config_path(None)
    assert path == target
    assert ENV_VAR in provider


def test_resolve_falls_back_to_project_local(tmp_path, monkeypatch):
    monkeypatch.delenv(ENV_VAR, raising=False)
    monkeypatch.chdir(tmp_path)
    local = tmp_path / ".claude" / "voice-bridge.json"
    local.parent.mkdir(parents=True)
    local.write_text("{}", encoding="utf-8")
    path, provider = config.resolve_config_path(None)
    assert path == local
    assert ".claude" in provider


def test_resolve_for_read_reports_no_file_but_for_write_names_the_target(tmp_path, monkeypatch):
    """Reading with nothing present means "use defaults"; writing still needs a target."""
    monkeypatch.delenv(ENV_VAR, raising=False)
    monkeypatch.chdir(tmp_path)
    assert config.resolve_config_path(None)[0] is None
    write_target, _ = config.resolve_config_path(None, must_exist=False)
    assert write_target == tmp_path / ".claude" / "voice-bridge.json"


def test_set_then_get_round_trips_under_the_env_var(tmp_path, monkeypatch):
    """The CFG-1 regression: `set` wrote one file while `get` read another.

    With $VOICE_BRIDGE_CONFIG pointing off-default, `config set` used to write
    ./.claude/voice-bridge.json while every read honoured the env var — so a
    setting appeared to save and then silently did nothing.
    """
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "elsewhere" / "vb.json"
    target.parent.mkdir()
    monkeypatch.setenv(ENV_VAR, str(target))

    write_path, _ = config.resolve_config_path(None, must_exist=False)
    config.set_value(write_path, "poll_interval", "42")

    assert target.exists(), "set must write the file reads resolve to"
    assert not (tmp_path / ".claude").exists(), "must not write the project-local default"
    assert load_config(None).poll_interval == 42


# --------------------------------------------------------------------------- #
# CFG-3 — as_dict mirrors the dataclass and cannot drift
# --------------------------------------------------------------------------- #


def test_as_dict_covers_every_dataclass_field(sample_config):
    """Hand-typed views drift. Deriving from the dataclass makes drift impossible."""
    declared = {f.name for f in dataclasses.fields(sample_config)}
    assert declared <= set(sample_config.as_dict()), (
        f"fields missing from as_dict: {sorted(declared - set(sample_config.as_dict()))}"
    )


def test_config_get_finds_a_previously_unknown_field(sample_config):
    """`seen_file` is a real field that `config get` used to reject as unknown."""
    d = sample_config.as_dict()
    for field in ("seen_file", "reply_cursor_file", "our_inbox", "peer_inbox"):
        assert d.get(field), f"{field} should be visible to `config get`"


def test_as_dict_values_are_all_strings(sample_config):
    assert all(isinstance(v, str) for v in sample_config.as_dict().values())


# --------------------------------------------------------------------------- #
# CFG-4 — an error names the provider that chose the path
# --------------------------------------------------------------------------- #


def test_missing_config_error_names_the_env_var(tmp_path, monkeypatch):
    """Otherwise "config file not found" is unactionable: found by *what*?"""
    monkeypatch.setenv(ENV_VAR, str(tmp_path / "ghost.json"))
    with pytest.raises(ConfigError) as err:
        load_config(None)
    assert ENV_VAR in str(err.value)
    assert "ghost.json" in str(err.value)


def test_missing_explicit_config_names_the_flag(tmp_path):
    with pytest.raises(ConfigError) as err:
        load_config(tmp_path / "nope.json")
    assert "--config" in str(err.value)
