"""`setup` — the guided path, and a verification that actually verifies.

Four things were wrong, and they share a theme: the command claimed more than it
had done.

* it never prompted, despite being the documented guided path (SETUP-1);
* an authentication failure was reported as "create the lists on your phone",
  naming the wrong blocker entirely (SETUP-2 — fixed in the transport-seam work,
  pinned here);
* nothing checked that a message could actually round-trip, so "setup complete"
  meant "files exist" (SETUP-3);
* a machine marker, `SETUP_DONE`, was printed in the middle of human prose
  (SETUP-6).
"""

from __future__ import annotations

import json

import pytest

from voice_bridge import setup as setup_mod


# --------------------------------------------------------------------------- #
# SETUP-1 — prompt on a terminal, never off one
# --------------------------------------------------------------------------- #


def test_prompt_fields_offers_defaults_and_accepts_enter(sample_config, monkeypatch):
    """Enter keeps the default, so the guided path is safe to hold down."""
    monkeypatch.setattr(setup_mod, "_is_tty", lambda: True)
    monkeypatch.setattr(setup_mod, "_ask", lambda field, label, default: "")
    got = setup_mod.prompt_fields(sample_config, preset={})
    assert got == {}, "an empty answer must mean 'keep the default', not 'set empty'"


def test_prompt_fields_records_what_the_user_typed(sample_config, monkeypatch):
    monkeypatch.setattr(setup_mod, "_is_tty", lambda: True)
    answers = {"inbox_list": "My Inbox", "spoke_name": ""}
    monkeypatch.setattr(setup_mod, "_ask", lambda field, label, default: answers.get(field, ""))
    got = setup_mod.prompt_fields(sample_config, preset={})
    assert got["inbox_list"] == "My Inbox"
    assert "spoke_name" not in got


def test_preset_fields_are_not_prompted(sample_config, monkeypatch):
    """`--set` pre-answers a field; asking again would be noise."""
    monkeypatch.setattr(setup_mod, "_is_tty", lambda: True)
    asked = []
    monkeypatch.setattr(setup_mod, "_ask", lambda field, label, default: asked.append(field) or "")
    setup_mod.prompt_fields(sample_config, preset={"inbox_list": "Preset"})
    assert "inbox_list" not in asked


def test_no_tty_prompts_nothing(sample_config, monkeypatch):
    """A scripted run must not block on a question nobody can answer."""
    monkeypatch.setattr(setup_mod, "_is_tty", lambda: False)

    def explode(*a, **k):
        raise AssertionError("must not prompt without a terminal")

    monkeypatch.setattr(setup_mod, "_ask", explode)
    assert setup_mod.prompt_fields(sample_config, preset={}) == {}


# --------------------------------------------------------------------------- #
# SETUP-3 — "verified" must mean a message actually moved
# --------------------------------------------------------------------------- #


def test_verify_round_trips_a_probe_and_cleans_up(sample_config, fake_transport, fake_ntfy):
    """The claim under test is end-to-end delivery, so the test asserts arrival.

    A probe is placed in the inbox list, drained into the mailbox file, a reply is
    posted to the outbox list and pushed — then both probes are removed, because a
    verification that litters the user's phone is its own bug.
    """
    sample_config.mailbox_dir.mkdir(parents=True, exist_ok=True)
    sample_config.peer_inbox.touch()
    sample_config.our_inbox.touch()
    sample_config.ntfy_topic_file.parent.mkdir(parents=True, exist_ok=True)
    sample_config.ntfy_topic_file.write_text("topic", encoding="utf-8")

    report = setup_mod.verify(sample_config, fake_transport)

    assert report["dictation_delivered"] is True
    assert report["reply_delivered"] is True
    assert report["banner_sent"] is True
    assert fake_ntfy, "a banner must really have been posted, not merely reported"

    inbox = fake_transport.resolve_list(sample_config.inbox_list, "")
    outbox = fake_transport.resolve_list(sample_config.output_list, "")
    assert fake_transport.read_incomplete(inbox) == [], "the probe must be cleaned up"
    assert fake_transport.read_incomplete(outbox) == [], "the reply probe must be cleaned up"


def test_verify_reports_failure_rather_than_claiming_success(sample_config, fake_transport):
    """With no topic configured the banner leg fails — and must SAY so."""
    sample_config.mailbox_dir.mkdir(parents=True, exist_ok=True)
    sample_config.peer_inbox.touch()
    sample_config.our_inbox.touch()

    report = setup_mod.verify(sample_config, fake_transport)
    assert report["dictation_delivered"] is True
    assert report["banner_sent"] is False
    assert "no_topic" in report["banner_detail"]


# --------------------------------------------------------------------------- #
# SETUP-6 — machine output belongs behind --json
# --------------------------------------------------------------------------- #


def test_setup_done_marker_is_gone(sample_config, fake_transport, capsys):
    lines = setup_mod.provision(sample_config, fake_transport)
    assert not any("SETUP_DONE" in ln for ln in lines)


def test_json_output_is_machine_readable(sample_config, fake_transport, capsys):
    setup_mod.report(sample_config, fake_transport, as_json=True)
    payload = json.loads(capsys.readouterr().out)
    assert "transport" in payload and "lists" in payload


# --------------------------------------------------------------------------- #
# SETUP-2 — the split landed with the transport seam; pinned here
# --------------------------------------------------------------------------- #


def test_auth_failure_is_not_reported_as_missing_lists(sample_config, boom_transport):
    """Telling someone with a bad password to go make lists names the wrong blocker."""
    from voice_bridge.icloud import ICloudError

    t = boom_transport(ICloudError("session needs 2FA"))
    with pytest.raises(ICloudError):
        setup_mod.provision(sample_config, t)
