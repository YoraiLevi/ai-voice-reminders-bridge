"""Batch 17b: the phone needs a protocol for addressing lists, not just a name for them.

The human, verbatim: *"how do we ensure the vox agent doesn't get confused which ids to
use when using radicale too? they have hiccups without being told the ids"* - and the
field evidence behind it: the phone agent hit `save_failed` creating **by name**, then
discovered the iOS-local id itself by lookup-and-retry.

Batch 16 answered WHICH lists (the account disambiguates them). This answers HOW to
address them, and the answer is **this PC's own doctrine handed to the other side**:
resolve once, pin the id, never match by name again. That is exactly what `resolve_list`
and the pinned `*_list_id` config fields enforce here. The ids are different in kind -
ours are server URLs, the phone's are iOS-local - but the protocol is the same one, and
it is the only one that survives a second account holding same-titled lists.

The working procedure already existed on the phone. It was being **reinvented under
failure** instead of stated up front, which is the same shape as DEMO16-1: an instruction
that cannot be followed does not fail closed, it teaches the reader to improvise.

**The iCloud section deliberately does NOT get self-pinning.** It is field-verified with
ids this PC supplies and the phone accepts, and re-deriving from a name there is the one
move that could route a dictation into a same-titled list in another account. What it was
missing is only what to do when a supplied id STOPS working - without a rule the reader
improvises, and the nearest improvisation is the name match the section's own first two
lines forbid.
"""

from __future__ import annotations

import json

import pytest

from voice_bridge.config import load_config
from voice_bridge.prompt import render_vox_prompt


def _cfg(tmp_path, tmp_mailbox, **over):
    cfg_file = tmp_path / "voice-bridge.json"
    cfg_file.write_text(
        json.dumps(
            {
                "mailbox_dir": str(tmp_mailbox),
                "state_dir": str(tmp_path / "state"),
                "inbox_list": "Vox-Message-Outbox",
                "output_list": "Vox-Message-Inbox",
                "inbox_list_id": "in-id",
                "output_list_id": "out-id",
                **over,
            }
        ),
        encoding="utf-8",
    )
    return load_config(cfg_file)


@pytest.fixture
def radicale_prompt(tmp_path, tmp_mailbox) -> str:
    cfg = _cfg(
        tmp_path,
        tmp_mailbox,
        transport="radicale",
        inbox_list_id="http://127.0.0.1:5232/vox/vox-message-outbox/",
        output_list_id="http://127.0.0.1:5232/vox/vox-message-inbox/",
        radicale_user="vox",
    )
    return render_vox_prompt(cfg)


@pytest.fixture
def icloud_prompt(tmp_path, tmp_mailbox) -> str:
    return render_vox_prompt(_cfg(tmp_path, tmp_mailbox))


def test_radicale_tells_the_phone_to_pin_its_own_ids(radicale_prompt):
    """The missing half. The phone CAN see ids - its own - and the instruction it needed
    was to write them down and use them, which is the rule this PC follows."""
    assert "PIN THEM ONCE" in radicale_prompt
    assert "never by name again" in radicale_prompt
    assert "identifier your own tools give each one" in radicale_prompt


def test_radicale_says_what_to_do_when_a_pinned_id_fails(radicale_prompt):
    """A pinning rule with no failure clause is a prompt that cannot be obeyed the moment
    it stops working - and the improvisation it invites is the name match it just banned.
    Re-derive from the ACCOUNT, once, then stop and say so."""
    assert "re-derive it ONCE" in radicale_prompt
    assert "not from the name alone" in radicale_prompt
    assert "two failures is a changed setup" in radicale_prompt


def test_radicale_no_longer_licenses_write_and_see(radicale_prompt):
    """The old text ended by telling the phone to try a write and check whether it landed.
    That RATIFIED the improvisation the agent had been forced into - and improvising a
    write is how a dictation lands in a list nobody polls. The first action is resolve
    and pin, not write and look."""
    lowered = radicale_prompt.lower()
    assert "try a write" not in lowered
    assert "check it landed" not in lowered


def test_radicale_still_scopes_by_account_first(radicale_prompt):
    """Batch 16's answer is not replaced by 17b's - pinning is meaningless until the
    phone knows which pair to pin, and a second account with the same list names is the
    live configuration on the human's own device."""
    assert "the ACCOUNT disambiguates them" in radicale_prompt
    assert 'user name is "vox"' in radicale_prompt
    assert radicale_prompt.index("account whose user name") < radicale_prompt.index("PIN THEM ONCE")


def test_radicale_offers_no_id_it_cannot_back(radicale_prompt):
    """The server URLs stay out. They are unmatchable on the phone, and printing one under
    a heading about identifiers is what made the last version unfollowable (DEMO16-1)."""
    assert "127.0.0.1:5232" not in radicale_prompt
    assert "AUTHORITATIVE LIST IDENTIFIERS" not in radicale_prompt


def test_icloud_keeps_the_supplied_ids_and_does_not_learn_to_re_derive(icloud_prompt):
    """The considered NO. This prompt is field-verified with ids this PC supplies, and
    telling it to re-derive from a name is the one instruction that could route a
    dictation into a same-titled list in another account - the exact hazard the ids exist
    to remove. A working artifact does not get changed on a theory."""
    assert "AUTHORITATIVE LIST IDENTIFIERS" in icloud_prompt
    assert "= in-id" in icloud_prompt and "= out-id" in icloud_prompt
    assert "PIN THEM ONCE" not in icloud_prompt
    assert "re-derive" not in icloud_prompt


def test_icloud_says_what_to_do_when_a_supplied_id_dies(icloud_prompt):
    """The one real gap on that side, and it is a guard rather than a new procedure: the
    success path is untouched. Without it the reader improvises when an id fails, and the
    nearest improvisation is the name match the section's own opening forbids."""
    assert "stops working, tell me and stop" in icloud_prompt
    assert "do NOT fall back" in icloud_prompt
    assert "stale" in icloud_prompt


def test_both_transports_forbid_matching_by_name(radicale_prompt, icloud_prompt):
    """The one rule that must survive every branch of this section. Whatever else the two
    prompts say, neither may leave name-matching as an available move - it is the single
    failure that reports success on every layer while the dictation goes nowhere."""
    for text in (radicale_prompt, icloud_prompt):
        assert "by name" in text
        assert "never by name again" in text or "do NOT match by name" in text
