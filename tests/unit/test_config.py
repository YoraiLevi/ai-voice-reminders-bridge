"""Config: defaults, state-dir derivation, routing files, and --set overrides."""

from __future__ import annotations

import pytest

from voice_bridge.config import ConfigError, load_config, parse_overrides


def test_defaults_are_the_vox_spoke(sample_config):
    cfg = sample_config
    assert cfg.spoke_name == "vox"
    assert cfg.route_to == "manager"
    assert cfg.from_name == "vox"  # derived from spoke_name
    assert cfg.inbox_list == "Vox-Message-Inbox"
    assert cfg.output_list == "Vox-Message-Outbox"
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
    with pytest.raises(ValueError, match="unknown key"):
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
