"""Integration: provision on real Radicale, then doctor should read all-green."""

from __future__ import annotations

from pathlib import Path

from voice_bridge import doctor as doctor_mod
from voice_bridge import setup as setup_mod
from voice_bridge.caldav import CalDAVTransport


def test_provision_then_doctor_green(radicale_config):
    t = CalDAVTransport(radicale_config, native_alarm=False)
    msgs = setup_mod.provision(radicale_config, t)
    assert any("created" in m.lower() or "verified" in m.lower() for m in msgs)

    # a topic file makes the ntfy check green too
    radicale_config.ntfy_topic_file.parent.mkdir(parents=True, exist_ok=True)
    radicale_config.ntfy_topic_file.write_text("topic", encoding="utf-8")

    # ...and the mailbox has to be set up for real now. The survey used to create
    # it, which is precisely the thing DOCTOR-4 removed: a check that manufactures
    # the state it reports on cannot fail, and "your mailbox path is wrong" is
    # exactly what a misconfigured user needs to be told.
    radicale_config.mailbox_dir.mkdir(parents=True, exist_ok=True)
    radicale_config.peer_inbox.touch()
    radicale_config.our_inbox.touch()

    # Both roles must be SELECTED for the lists row to be green — an unset id is
    # no longer quietly resolved by name, so it is a reportable state.
    import dataclasses

    from voice_bridge.config import set_value

    # The names come from SUGGESTED_NAMES, which is what `provision` just created -
    # the config's cached names are empty until something is selected.
    from voice_bridge.setup import SUGGESTED_NAMES

    ids = {
        "inbox_list_id": t.resolve_list(SUGGESTED_NAMES["inbox"], "").id,
        "output_list_id": t.resolve_list(SUGGESTED_NAMES["outbox"], "").id,
    }
    for field, value in ids.items():
        set_value(Path(radicale_config.source), field, value)
    radicale_config = dataclasses.replace(radicale_config, **ids)

    rc = doctor_mod.run(radicale_config, t)
    assert rc == 0  # config + creds + auth + lists + ntfy + mailbox all GREEN


def test_doctor_reports_missing_creds(radicale_config):
    radicale_config.creds_env.unlink()  # remove the creds file
    t = CalDAVTransport(radicale_config)
    rc = doctor_mod.run(radicale_config, t)
    assert rc == 2  # RED: missing creds (+ auth fails)
