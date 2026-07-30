"""Batch 13's first finding, from walking the Radicale runbook for real.

`voice-bridge setup` on a config whose transport is `radicale` **converted it to
iCloud and cleared both list pins.** Not a theory - the sandbox config was rewritten
and setup announced it in its own output:

    transport (icloud/radicale) [radicale]: transport changed: radicale -> icloud

`--transport` on `setup` carried `default="icloud"`, so a flag nobody typed arrived at
`write_config` as an explicit answer and stamped iCloud over the decision on disk.
Batch 11 then correctly recognised that as a transport switch and cleared the two list
ids with it - **the batch-11 fix made the destruction thorough while making the
branching correct.**

Section 26 says a request is not a decision. This is the same lesson one layer up: a
flag's DEFAULT is not even a request, because nobody asked for it. `run --transport`
never had a default and was never affected; the asymmetry was one line.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from voice_bridge import cli, onboard, setup as setup_mod
from voice_bridge.config import load_config


def _radicale_cfg(tmp_path, tmp_mailbox) -> Path:
    cfg_file = tmp_path / "voice-bridge.json"
    cfg_file.write_text(
        json.dumps(
            {
                "transport": "radicale",
                "mailbox_dir": str(tmp_mailbox),
                "state_dir": str(tmp_path / "state"),
                "inbox_list_id": "rad-in",
                "output_list_id": "rad-out",
                "inbox_list": "Vox-Message-Outbox",
                "output_list": "Vox-Message-Inbox",
            }
        ),
        encoding="utf-8",
    )
    return cfg_file


def test_a_bare_setup_does_not_convert_an_existing_transport(tmp_path, tmp_mailbox, monkeypatch):
    """The defect exactly: no `--transport`, no answer, and radicale survives.

    EOF at the preamble is the live shape - `prompt_fields` returns no answers - so the
    only thing left saying "icloud" was the flag default.
    """
    cfg_file = _radicale_cfg(tmp_path, tmp_mailbox)
    monkeypatch.setattr(setup_mod, "_is_tty", lambda: True)
    monkeypatch.setattr(setup_mod, "_ask", lambda *_a, **_k: (_ for _ in ()).throw(EOFError()))
    monkeypatch.setattr(onboard, "step_radicale_credentials", lambda *_a, **_k: False)

    setup_mod.run_setup(config_path=cfg_file)
    cfg = load_config(cfg_file)

    assert cfg.transport == "radicale", "a bare setup must not change the backend"
    assert cfg.inbox_list_id == "rad-in", "and must not clear the pins"
    assert cfg.output_list_id == "rad-out"


def test_setups_transport_flag_has_no_default():
    """Where the defect actually lived. Guarded at the parser, because a default here
    is indistinguishable downstream from a value the user typed."""
    parser = cli._build_parser()
    sub = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    flag = next(a for a in sub.choices["setup"]._actions if a.option_strings == ["--transport"])

    assert flag.default is None, "a flag nobody typed must not arrive as an answer"


def test_run_and_setup_agree_that_no_flag_means_no_opinion():
    """`run --transport` was always safe. The bug was the ASYMMETRY, so this pins both."""
    parser = cli._build_parser()
    sub = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    for cmd in ("run", "setup"):
        flag = next(a for a in sub.choices[cmd]._actions if a.option_strings == ["--transport"])
        assert flag.default is None, f"{cmd} --transport carries a default"


def test_an_explicit_flag_still_switches(tmp_path, tmp_mailbox, monkeypatch, fake_transport):
    """The fix must not make the flag useless: asked for, it still decides - and the
    batch-11 clearing is still correct WHEN a human actually chose the switch."""
    cfg_file = _radicale_cfg(tmp_path, tmp_mailbox)
    monkeypatch.setattr(setup_mod, "_is_tty", lambda: False)
    monkeypatch.setattr(setup_mod, "make_transport", lambda _cfg: fake_transport)
    monkeypatch.setattr(setup_mod, "settle_selection", lambda *a, **k: 0)

    setup_mod.run_setup(config_path=cfg_file, transport="icloud")
    cfg = load_config(cfg_file)

    assert cfg.transport == "icloud"
    assert cfg.inbox_list_id == "", "an intentional switch still clears foreign pins"


def test_a_preamble_answer_beats_the_flag(tmp_path, tmp_mailbox, monkeypatch):
    """Precedence, stated: what the person just answered wins over what they passed
    earlier in the same command line."""
    cfg_file = _radicale_cfg(tmp_path, tmp_mailbox)
    monkeypatch.setattr(setup_mod, "_is_tty", lambda: True)
    monkeypatch.setattr(
        setup_mod, "_ask", lambda field, *_a, **_k: "radicale" if field == "transport" else ""
    )
    monkeypatch.setattr(onboard, "step_radicale_credentials", lambda *_a, **_k: False)

    setup_mod.run_setup(config_path=cfg_file, transport="icloud")

    assert load_config(cfg_file).transport == "radicale"


def test_a_first_run_with_nothing_at_all_still_gets_a_transport(
    tmp_path, tmp_mailbox, monkeypatch, fake_transport
):
    """The one case where choosing IS ours: no file, no flag, no answer."""
    cfg_file = tmp_path / "voice-bridge.json"
    monkeypatch.setattr(setup_mod, "_is_tty", lambda: False)
    monkeypatch.setattr(setup_mod, "make_transport", lambda _cfg: fake_transport)
    monkeypatch.setattr(setup_mod, "settle_selection", lambda *a, **k: 0)

    setup_mod.run_setup(config_path=cfg_file, overrides={"mailbox_dir": str(tmp_mailbox)})

    assert load_config(cfg_file).transport == "icloud"


# --------------------------------------------------------------------------- #
# P1 #2 - a cursor into a file that no longer exists
# --------------------------------------------------------------------------- #
#
# Observed in the batch-13 sandbox, with the phone played by a raw caldav client:
#
#   cursor 92 (from a file our own clean exit had deleted)
#   new to-vox.md, 108 bytes, THREE reply lines
#   run --once  ->  ONE reminder: "[05:10][vox] ger) LINE-THREE"
#
# Two replies gone silently, the third mangled from mid-word. This is FMA-14, which
# sat on the accepted-risk register as "an unobserved truncate-then-regrow CAN strand
# lines". Both halves of that pricing were wrong: the trigger is our OWN eject, so it
# fires on every stop/start, and the outcome is loss plus a corrupt delivery.
#
# An accepted risk is a claim about frequency and blast radius, and both were guesses
# until something ran.


def _reply(cfg, text: str) -> None:
    cfg.our_inbox.parent.mkdir(parents=True, exist_ok=True)
    with cfg.our_inbox.open("a", encoding="utf-8") as fh:
        fh.write(text + "\n")


def test_a_recreated_inbox_is_read_from_the_start(sample_config, fake_transport):
    """The exact observed shape: same path, new file, LONGER than the stale cursor.

    A length check cannot catch this - 108 > 92 - which is why the fix is identity.
    """
    from voice_bridge import poller
    from voice_bridge.mailbox import load_cursor

    _reply(sample_config, "- [10:00] (manager) consumed before the restart")
    poller.drain_replies(sample_config, fake_transport)
    stale = load_cursor(sample_config.reply_cursor_file)
    assert stale > 0, "the cursor must have advanced, or this test proves nothing"

    # The peer replaces the file while we are down - and it comes back LONGER.
    sample_config.our_inbox.unlink()
    for line in (
        "- [10:05] (manager) LINE-ONE",
        "- [10:05] (manager) LINE-TWO",
        "- [10:05] (manager) LINE-THREE-is-a-long-line-to-exceed-the-old-offset",
    ):
        _reply(sample_config, line)
    assert sample_config.our_inbox.stat().st_size > stale, "reproduce the LONGER case"

    poller.drain_replies(sample_config, fake_transport)
    delivered = " ".join(
        it.title + " " + (it.notes or "")
        for it in fake_transport.read_incomplete(
            fake_transport.resolve_list("", sample_config.output_list_id)
        )
    )

    assert "LINE-ONE" in delivered, "a reply was silently lost"
    assert "LINE-TWO" in delivered
    assert "LINE-THREE" in delivered
    assert "ger) LINE" not in delivered, "and one arrived as a fragment from mid-word"


def test_appending_does_not_reset_the_cursor(sample_config, fake_transport):
    """The guard on the fix, and it earned its place immediately: the first signature
    hashed a fixed 512-byte head, so for any file under 512 bytes every append changed
    the identity, every cursor reset to 0, and every reply would have been re-sent
    forever. A duplicate storm in place of a dropped line is not a fix.

    The cap is gone now - FMA-14's register entry had specified "full prefix, no cap"
    before any of this was built, and it was right."""
    from voice_bridge import poller
    from voice_bridge.mailbox import load_cursor

    _reply(sample_config, "- [10:00] (manager) first")
    poller.drain_replies(sample_config, fake_transport)
    first = load_cursor(sample_config.reply_cursor_file)

    _reply(sample_config, "- [10:01] (manager) second")
    poller.drain_replies(sample_config, fake_transport)

    assert load_cursor(sample_config.reply_cursor_file) > first
    items = fake_transport.read_incomplete(
        fake_transport.resolve_list("", sample_config.output_list_id)
    )
    firsts = [it for it in items if "first" in (it.title + (it.notes or ""))]
    assert len(firsts) == 1, "an append must not re-send what was already delivered"


def test_the_eject_clears_the_cursor_it_invalidates(sample_config):
    """The immediate half of the fix. Deleting our inbox while keeping a cursor into it
    is what turned a protocol convention into data loss."""
    from voice_bridge import poller
    from voice_bridge.mailbox import save_cursor

    _reply(sample_config, "- [10:00] (manager) something")
    save_cursor(sample_config.reply_cursor_file, 10, path=sample_config.our_inbox)
    assert sample_config.reply_cursor_file.exists()

    poller.announce_eject(sample_config)

    assert not sample_config.our_inbox.exists(), "the eject still removes our inbox"
    assert not sample_config.reply_cursor_file.exists(), "and no longer leaves a cursor into it"


def test_an_old_bare_integer_cursor_still_loads(tmp_path):
    """Upgrade path: a cursor written before signatures existed has no second line, and
    an absent signature must mean "cannot verify", never "mismatch" - or the upgrade
    itself re-sends every reply."""
    from voice_bridge.mailbox import load_cursor, resolved_cursor

    cursor = tmp_path / "old.cursor"
    cursor.write_text("42", encoding="utf-8")
    data = b"x" * 100

    assert load_cursor(cursor) == 42
    assert resolved_cursor(cursor, data) == 42, "no signature is not a failed signature"


def test_a_rewritten_consumed_region_is_detected(tmp_path):
    """Stronger than identity-by-recreation: the signature covers the bytes we claim to
    have read, so a peer rewriting THOSE is caught even at the same length."""
    from voice_bridge.mailbox import resolved_cursor, save_cursor

    f = tmp_path / "inbox.md"
    f.write_text("- aaaa\n- bbbb\n", encoding="utf-8")
    cursor = tmp_path / "c.cursor"
    save_cursor(cursor, 7, path=f)

    assert resolved_cursor(cursor, f.read_bytes()) == 7
    f.write_text("- zzzz\n- bbbb\n", encoding="utf-8")  # same length, different bytes
    assert resolved_cursor(cursor, f.read_bytes()) == 0


# --------------------------------------------------------------------------- #
# the banner leg, finally wearing its own clothes
# --------------------------------------------------------------------------- #


def _legs(**over):
    base = {
        "dictation_delivered": True,
        "reply_delivered": True,
        "banner_sent": True,
        "banner_detail": "sent",
        "title_renderable": None,
        "title_detail": "",
    }
    base.update(over)
    return base


def test_an_unconfigured_banner_is_not_a_failure():
    """Walking the sim put `[FAIL] notification sent` back on screen four lines above
    `verified:` - the exact shape the human called out in batch 11, which I fixed for
    the title leg and left standing here. Not configured is not a failure at all."""
    out: list[str] = []
    rc = setup_mod.report_verification(
        _legs(banner_sent=False, banner_detail="no_topic: no ntfy topic file at ..."),
        show=out.append,
    )
    text = "\n".join(out)

    assert rc == 0
    assert "[FAIL]" not in text, "best-effort must not wear a contract leg's dress"
    assert "[ -- ] notification not configured" in text
    assert "verified: a message makes the round trip." in text


def test_a_failed_banner_is_a_warning_not_a_failure():
    """A message that arrived without its doorbell still arrived."""
    out: list[str] = []
    rc = setup_mod.report_verification(
        _legs(banner_sent=False, banner_detail="failed: connection refused"), show=out.append
    )
    text = "\n".join(out)

    assert rc == 0
    assert "[warn] notification not sent - the message still arrived" in text
    assert "[FAIL]" not in text


def test_the_two_delivery_legs_still_fail_loudly():
    """The contract is unchanged: only the legs that ARE the contract can fail it."""
    out: list[str] = []
    rc = setup_mod.report_verification(_legs(reply_delivered=False), show=out.append)
    text = "\n".join(out)

    assert rc == 2
    assert "[FAIL] reply reached the outbox list" in text
    assert "verified:" not in text


# --------------------------------------------------------------------------- #
# F5 - the URL a phone can actually use
# --------------------------------------------------------------------------- #


def _server_cfg(tmp_path, tmp_mailbox, host: str):
    import json as _json

    cfg_file = tmp_path / "voice-bridge.json"
    cfg_file.write_text(
        _json.dumps(
            {
                "transport": "radicale",
                "mailbox_dir": str(tmp_mailbox),
                "state_dir": str(tmp_path / "state"),
                "radicale_host": host,
                "radicale_port": 5299,
            }
        ),
        encoding="utf-8",
    )
    return load_config(cfg_file)


def test_a_loopback_bind_offers_no_phone_url(tmp_path, tmp_mailbox):
    """There genuinely is no address a phone can use, so offering one would be a lie -
    and this is the case where the old single-line output was correct."""
    from voice_bridge import server

    assert server.phone_urls(_server_cfg(tmp_path, tmp_mailbox, "127.0.0.1")) == []


def test_a_wildcard_bind_names_the_addresses_that_answer(tmp_path, tmp_mailbox):
    """Measured in the sim before this was written: a `0.0.0.0` bind answered HTTP 200
    on the tailnet address, the LAN address and loopback, while `url` printed loopback -
    so following the runbook produced an iOS account that could never connect."""
    from voice_bridge import server

    urls = server.phone_urls(_server_cfg(tmp_path, tmp_mailbox, "0.0.0.0"))

    assert urls, "a wildcard bind does answer on some address"
    for label, url in urls:
        assert label in ("tailscale", "local network")
        assert url.startswith("http://") and url.endswith(":5299/")
        assert "127.0.0.1" not in url, "loopback is not a phone URL"


def test_the_url_command_still_prints_the_client_url_first(
    tmp_path, tmp_mailbox, capsys, monkeypatch
):
    """The PC-side client reads the first line, so it must not move - the fix ADDS
    candidates, it does not replace the answer another caller depends on."""
    from voice_bridge import cli, server

    cfg = _server_cfg(tmp_path, tmp_mailbox, "0.0.0.0")
    monkeypatch.setattr(cli, "path_note", lambda: "")
    monkeypatch.setattr(server, "phone_urls", lambda _c: [("tailscale", "http://100.1.2.3:5299/")])
    cli.main(["--config", str(cfg.source), "radicale-server", "url"])
    out = capsys.readouterr().out.splitlines()

    assert out[0] == "http://127.0.0.1:5299"
    assert "for a phone on your tailscale" in out[1]
    assert "100.1.2.3" in out[1]
