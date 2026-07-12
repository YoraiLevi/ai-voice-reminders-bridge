"""Integration: provision on real Radicale, then doctor should read all-green."""

from __future__ import annotations

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

    rc = doctor_mod.run(radicale_config, t)
    assert rc == 0  # config + creds + auth + lists + ntfy + mailbox all GREEN


def test_doctor_reports_missing_creds(radicale_config):
    radicale_config.creds_env.unlink()  # remove the creds file
    t = CalDAVTransport(radicale_config)
    rc = doctor_mod.run(radicale_config, t)
    assert rc == 2  # RED: missing creds (+ auth fails)
