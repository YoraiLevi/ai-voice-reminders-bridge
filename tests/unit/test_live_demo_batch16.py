"""The phone half of act 5. The round trip WORKS - and the prompt could not be obeyed.

Dictations from a real iPhone crossed over HTTPS/tailscale into the mailbox. In the
same session the phone agent reported that it could not follow the prompt's own
instructions: on radicale the "AUTHORITATIVE LIST IDENTIFIERS" are **server URLs**
(`http://127.0.0.1:5232/vox/...`), iOS assigns its own local identifiers, and the
device could see same-named `Vox-Message-*` lists in TWO accounts - the live iCloud
pair and the new self-hosted pair.

So the name was ambiguous and the id was unusable at the same time, and the agent had
to guess by attempting a write and seeing whether it landed. Its own words: *"duplicate
titles with different IDs make routing ambiguous."*

A prompt that cannot be obeyed is worse than one that says less: it teaches the reader
to improvise, and improvisation is how a dictation ends up in a list nobody polls.
"""

from __future__ import annotations

import json

from voice_bridge.config import load_config
from voice_bridge.prompt import render_vox_prompt


def _cfg(tmp_path, tmp_mailbox, transport: str, **extra):
    cfg_file = tmp_path / "voice-bridge.json"
    cfg_file.write_text(
        json.dumps(
            {
                "transport": transport,
                "mailbox_dir": str(tmp_mailbox),
                "state_dir": str(tmp_path / "state"),
                "inbox_list_id": "http://127.0.0.1:5299/vox/vox-message-outbox/"
                if transport == "radicale"
                else "CK-INBOX-GUID",
                "output_list_id": "http://127.0.0.1:5299/vox/vox-message-inbox/"
                if transport == "radicale"
                else "CK-OUTBOX-GUID",
                "inbox_list": "Vox-Message-Outbox",
                "output_list": "Vox-Message-Inbox",
                **extra,
            }
        ),
        encoding="utf-8",
    )
    return load_config(cfg_file)


# --------------------------------------------------------------------------- #
# 1 - the prompt must be obeyable on the transport it is rendered for
# --------------------------------------------------------------------------- #


def test_the_radicale_prompt_does_not_hand_the_phone_a_server_url(tmp_path, tmp_mailbox):
    """The defect exactly. A URL this PC uses to reach the server is not something the
    phone can match, and printing it under the word AUTHORITATIVE invites the reader to
    try."""
    text = render_vox_prompt(_cfg(tmp_path, tmp_mailbox, "radicale"))

    assert "http://127.0.0.1:5299" not in text, "a server URL is not a phone-side id"
    assert "AUTHORITATIVE LIST IDENTIFIERS" not in text, "there are none on this transport"


def test_the_radicale_prompt_disambiguates_by_ACCOUNT(tmp_path, tmp_mailbox):
    """What IS knowable and IS visible on the device: the CalDAV account's user name.

    Not the server host - the phone may reach it by tailnet name, LAN address or
    anything else, and we cannot know which. The account's user name is a field the
    human typed on the phone and can see in Settings.
    """
    text = render_vox_prompt(_cfg(tmp_path, tmp_mailbox, "radicale", radicale_user="vox"))

    assert "CalDAV account whose user name is" in text
    assert '"vox"' in text
    # The CLAIM is what this pins, not the sentence carrying it: the prompt must name
    # the ambiguity the phone actually sees. Batch 17b reworded the line while adding
    # the pinning section, so the assertion follows the wording rather than freezing
    # copy that is expected to keep improving.
    assert "OTHER accounts on this phone" in text, "name the ambiguity the phone actually sees"


def test_the_radicale_prompt_says_why_there_is_no_id_to_match(tmp_path, tmp_mailbox):
    """Silence would leave the reader looking for the ids the iCloud prompt has. Saying
    the ids are server-side is what stops the hunt."""
    text = render_vox_prompt(_cfg(tmp_path, tmp_mailbox, "radicale"))

    assert "server URL" in text
    assert "which your phone never sees" in text


def test_the_icloud_prompt_KEEPS_ITS_SUPPLIED_IDS(tmp_path, tmp_mailbox):
    """CloudKit ids are real identifiers the Reminders side exposes, and that prompt has
    been field-verified working. The fix is transport-awareness, not the removal of a
    section that earns its place on the transport it was written for.

    RENAMED from `..._is_UNCHANGED`, which stopped being true in 17b: that batch added a
    failure clause to this section (what to do when a supplied id dies). The success path
    is untouched, which is what this test has always actually guarded - a name promising
    byte-equality while quietly passing through an edit is the same silent-claim problem
    the prompt work keeps turning up.
    """
    text = render_vox_prompt(_cfg(tmp_path, tmp_mailbox, "icloud"))

    assert "AUTHORITATIVE LIST IDENTIFIERS" in text
    assert "CK-INBOX-GUID" in text
    assert "CK-OUTBOX-GUID" in text


def test_neither_prompt_ever_names_a_list_nobody_selected(tmp_path, tmp_mailbox):
    """Unchanged and re-pinned: the refusal predates this batch and must survive it,
    because a prompt is instructions to a system no local test can check (GAP-3)."""
    import pytest

    from voice_bridge.errors import CommandError

    cfg = _cfg(tmp_path, tmp_mailbox, "radicale")
    bare = tmp_path / "bare.json"
    bare.write_text(
        json.dumps(
            {
                "transport": "radicale",
                "mailbox_dir": str(tmp_mailbox),
                "state_dir": str(tmp_path / "state"),
            }
        ),
        encoding="utf-8",
    )

    render_vox_prompt(cfg)  # both roles selected: fine
    with pytest.raises(CommandError):
        render_vox_prompt(load_config(bare))


# --------------------------------------------------------------------------- #
# 2 - the seed, answered by the field
# --------------------------------------------------------------------------- #


def test_the_list_seed_is_created_COMPLETE(tmp_path, tmp_mailbox, monkeypatch):
    """F3, greenlit by a phone.

    The seed exists because an empty CalDAV collection does not sync to iOS. Being
    INCOMPLETE in the dictation list made it indistinguishable from a dictation, and the
    poller bridged it - the peer agent's first ever message was "Vox-Message-Outbox is
    live... Safe to delete."

    Marking it done hung on one fact only a device could supply: does iOS sync a
    collection whose only item is completed? **It does** - the human watched both lists
    appear. So the seed is created complete and nothing has to recognise it by its text.
    """
    from voice_bridge.caldav import CalDAVTransport

    saved: list[dict] = []

    class _Cal:
        def save_todo(self, **kw):
            saved.append(kw)

    cfg = _cfg(tmp_path, tmp_mailbox, "radicale")
    t = CalDAVTransport(cfg)
    monkeypatch.setattr(t, "_p", lambda: type("P", (), {"make_calendar": lambda *a, **k: None})())

    # The list must be ABSENT first, or `create_list` takes its idempotent early return
    # and never seeds anything - which is what a naive always-succeeds stub produced, and
    # the assertion below caught. The stub has to model the state the code branches on.
    calls: list[str] = []

    def _resolve(self, name, list_id=""):
        calls.append(name)
        if len(calls) == 1:
            raise LookupError(name)  # not there yet
        return _Ref(name)  # created a moment ago

    monkeypatch.setattr(CalDAVTransport, "resolve_list", _resolve)
    t._cals = {"id-1": _Cal()}

    import voice_bridge.caldav as caldav_mod

    monkeypatch.setattr(caldav_mod, "_creds", lambda _c: ("u", "p", "http://127.0.0.1:5299"))
    t.create_list("Vox-Message-Outbox")

    assert saved, "the seed is still written - an empty collection does not sync to iOS"
    assert saved[0].get("status") == "COMPLETED", "and it is created already done"


class _Ref:
    def __init__(self, name: str) -> None:
        self.id, self.name = "id-1", name


# --------------------------------------------------------------------------- #
# 3 + 5 - copy, and the bind that a bare init would have written
# --------------------------------------------------------------------------- #


def test_the_create_lists_offer_explains_itself_without_the_other_branch(capsys):
    """Human verbatim: *"'in whatever client you use (the same way the iCloud path
    works).' this doesn't make any sense"* - right twice. It assumed they have a CalDAV
    client, and it explained this path by comparing it to one they may never have taken.
    """
    from voice_bridge.setup import ask_radicale_creation

    out: list[str] = []
    ask_radicale_creation(ask=lambda _p: "y", show=out.append)
    text = " ".join(out)

    assert "the same way the iCloud path works" not in text
    assert "whatever client you use" not in text
    assert "any CalDAV app connected to it" in text


def test_the_default_bind_is_loopback(tmp_path, tmp_mailbox):
    """Batch 14 made TLS-in-front the working posture, so the LAN needs no access - and a
    near-miss made it urgent: `radicale-server init --force` run without `--config`
    mid-triage wrote a server config into whichever config resolved, and with the old
    default that config bound every interface."""
    cfg = _cfg(tmp_path, tmp_mailbox, "radicale")

    assert cfg.radicale_host == "127.0.0.1"


def test_a_loopback_bind_offers_no_phone_url_and_that_is_correct(tmp_path, tmp_mailbox):
    """The two changes agree with each other: with a loopback bind there is no reachable
    HTTP address to advertise, which is right, because the phone would refuse a plain-HTTP
    one anyway."""
    from voice_bridge import server

    assert server.phone_urls(_cfg(tmp_path, tmp_mailbox, "radicale")) == []
