"""factory selection + runner state-machine branches (no network)."""

from __future__ import annotations

import json

import pytest

from voice_bridge.caldav import CalDAVTransport
from voice_bridge.config import load_config
from voice_bridge.factory import make_transport
from voice_bridge.icloud import ICloudTransport
from voice_bridge.runner import run_command


def _write(tmp_path, tmp_mailbox, transport):
    p = tmp_path / "vb.json"
    p.write_text(
        json.dumps(
            {
                "transport": transport,
                "mailbox_dir": str(tmp_mailbox),
                "state_dir": str(tmp_path / "s"),
            }
        ),
        encoding="utf-8",
    )
    return p


def test_factory_selects_transport(sample_config, tmp_path, tmp_mailbox):
    assert isinstance(make_transport(sample_config), ICloudTransport)  # default icloud
    cfg = load_config(_write(tmp_path, tmp_mailbox, "radicale"))
    assert isinstance(make_transport(cfg), CalDAVTransport)


def test_run_dry_run_bootstraps_config(tmp_path, tmp_mailbox):
    p = tmp_path / "vb.json"  # absent → setup writes it
    rc = run_command(
        dry_run=True,
        config_path=str(p),
        transport="icloud",
        overrides={"mailbox_dir": str(tmp_mailbox), "state_dir": str(tmp_path / "s")},
    )
    assert rc == 0
    assert p.exists()  # the setup flow created the config


def test_run_require_mailbox_errors(tmp_path, tmp_mailbox):
    p = _write(tmp_path, tmp_mailbox, "icloud")  # config exists, but no mailbox files
    assert run_command(require_mailbox=True, once=True, config_path=str(p)) == 2


def test_icloud_login_missing_creds_returns_2(sample_config):
    from voice_bridge.login import icloud_login

    # sample_config's creds_env doesn't exist → missing creds, before any pyicloud import
    assert icloud_login(sample_config) == 2


def test_provision_reports_manual_step_when_lists_are_missing(sample_config, fake_transport):
    """Authenticated, but the two lists do not exist yet — iCloud cannot create them."""
    from voice_bridge import setup as setup_mod

    empty = type(fake_transport)()  # connects fine, has no lists
    lines = setup_mod.provision(sample_config, empty)
    text = " ".join(lines)
    assert "create TWO lists" in text
    # The SETUP_DONE marker is gone (SETUP-6): a machine token printed amid human
    # prose served neither reader. Structured output lives behind --json now.
    assert "SETUP_DONE" not in text


def test_provision_does_not_disguise_an_auth_failure_as_missing_lists(sample_config):
    """The conflation this fix removes.

    With no credentials the old code swallowed the auth error and printed "create
    the lists by hand" — so someone with a wrong password was sent to make lists
    they may already have had, while the real cause went unmentioned. Connection
    failures now propagate, and the setup flow turns them into "run icloud-login".
    """
    from voice_bridge import setup as setup_mod
    from voice_bridge.icloud import ICloudError

    with pytest.raises(ICloudError):
        setup_mod.provision(sample_config, ICloudTransport(sample_config))
