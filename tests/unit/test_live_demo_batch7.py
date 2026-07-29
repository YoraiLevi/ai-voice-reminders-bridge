"""Presentation, program-wide. Ruled verbatim:

*"the presentation to the user interface is hard to digest. the 'choice'
instructions are at the top and after the 'dump' of the options it's hard to
process what to do given the user just observes an instruction to choose and how
to pick without being told why (the terminal needs scrolling up, which is an
action by the user) this is a inconvinent user experience we need to resolve
throught the app."*

**THE LAW: the prompt line's neighborhood is the interface.** In a terminal the
cursor sits at the bottom, so anything above the last screenful has scrolled away.
A layout that puts the teaching at the top and twenty-one lists in between charges
the user a scroll to answer a question we asked.

The testable form, and what most of this file asserts: **for every prompt, the
lines immediately above the cursor carry what is being chosen, why, the current
state, and the keys.** Long content goes above that block, never between it and
the cursor.

And two things were deleted rather than improved:

* `next: <following step>` - *"they don't stand out and they dont make sense and
  the user misses them"*. A forward reference answers a question nobody is asking.
* `[n/5]` counters - *"it's either hardcoded or self incrementing coded procedure
  and it's overhead. just make it an obvious title so if we change the flow we
  don't need to manually update the numbering."*
"""

from __future__ import annotations

import json

import pytest

from voice_bridge import commands, onboard, selection, teardown
from voice_bridge.config import load_config
from voice_bridge.layout import RULE, question_block
from voice_bridge.transport import ListRef


def _capture(answers):
    """(ask, show, lines) where `lines` records output AND the prompts inline.

    Prompts are recorded in place, so a test can ask what the user saw in the
    lines immediately before the cursor - which is the whole subject here.
    """
    lines: list[str] = []
    it = iter(answers)

    def ask(prompt: str) -> str:
        lines.append(f"<<PROMPT>>{prompt}")
        return next(it)

    return ask, lines.append, lines


def _block_before_prompt(lines: list[str]) -> list[str]:
    """The lines between the last question-block rule and the first prompt.

    This is the "neighborhood" the law is about: whatever is here is what a user
    can see without scrolling.
    """
    prompt_at = next(i for i, line in enumerate(lines) if line.startswith("<<PROMPT>>"))
    rule_at = max(i for i, line in enumerate(lines[:prompt_at]) if line == RULE)
    return lines[rule_at:prompt_at]


# --------------------------------------------------------------------------- #
# the law, asserted structurally
# --------------------------------------------------------------------------- #


def _long_inventory(n: int = 21) -> list[ListRef]:
    """The size that made this a bug. Twenty-one lists is a real account."""
    return [ListRef(id=f"L{i}", name=f"List {i}", count=i) for i in range(1, n + 1)]


def test_the_picker_puts_everything_needed_below_the_dump():
    """The original failure, exactly: teach at the top, dump 21 rows, then ask."""
    refs = _long_inventory()
    res = selection.Resolution("inbox", "", "inbox_list_id", "", "unselected", refs, "")
    ask, show, lines = _capture(["1"])
    selection.pick(res, ask=ask, show=show)

    block = "\n".join(_block_before_prompt(lines))
    assert "CHOOSING:" in block, "what is being chosen"
    assert "phone -> Reminders -> PC" in block, "why - which direction it carries"
    assert "CURRENTLY:" in block, "the current state"
    assert "1-21" in block and "r)" in block and "s)" in block, "the keys"


def test_nothing_the_answer_needs_lives_only_above_the_dump():
    """The teaching may still preview at the top - repetition costs a line and
    saves a scroll - but it must not exist ONLY there."""
    refs = _long_inventory()
    res = selection.Resolution("outbox", "", "output_list_id", "", "unselected", refs, "")
    ask, show, lines = _capture(["s"])
    selection.pick(res, ask=ask, show=show)

    block = "\n".join(_block_before_prompt(lines))
    assert "PC -> Reminders -> phone" in block


def test_the_block_is_the_last_thing_before_the_cursor():
    """Not merely present - LAST. A block followed by more content is a block that
    has scrolled away again."""
    refs = _long_inventory()
    res = selection.Resolution("inbox", "", "inbox_list_id", "", "unselected", refs, "")
    ask, show, lines = _capture(["1"])
    selection.pick(res, ask=ask, show=show)

    neighborhood = _block_before_prompt(lines)
    assert len(neighborhood) <= 14, f"the block must fit a screen, got {len(neighborhood)} lines"
    assert any(line.strip().startswith("1-21") for line in neighborhood)


def test_the_role_menu_carries_its_own_block(sample_config, fake_transport, tmp_path):
    cfg_file = tmp_path / "voice-bridge.json"
    cfg_file.write_text("{}", encoding="utf-8")
    ask, show, lines = _capture(["d"])
    commands.select_command(
        sample_config,
        fake_transport,
        config_path=cfg_file,
        ask=ask,
        show=show,
        is_tty=lambda: True,
    )

    block = "\n".join(_block_before_prompt(lines))
    assert "CHOOSING:" in block and "CURRENTLY:" in block
    assert "d)" in block


def test_the_notifications_step_carries_its_own_block(sample_config, tmp_path):
    ask, show, lines = _capture(["4"])
    onboard.step_notifications(sample_config, config_path=tmp_path / "c.json", ask=ask, show=show)

    block = "\n".join(_block_before_prompt(lines))
    assert "CHOOSING:" in block
    assert "MANUALLY" in block, "the cost of skipping, beside the cursor"
    assert "4)" in block


def test_the_teardown_confirm_restates_the_stakes_beside_the_cursor(installed_cfg):
    """The teardown preview is the longest dump the program prints, so what the
    user is agreeing to had scrolled past by the time they were asked."""
    cfg, cfg_file = installed_cfg
    plan = teardown.plan_uninstall(cfg, cfg_file, keep_mailbox=True)
    ask, show, lines = _capture([""])
    teardown.preview(plan, show=show)
    teardown.confirm(plan, ask=ask, show=show)

    block = "\n".join(_block_before_prompt(lines))
    assert "DELETED" in block
    assert "UNINSTALL" in block and "ALL CAPS" in block
    assert "surviving" in block, "what lives through it is part of the decision"


# --------------------------------------------------------------------------- #
# the deletions
# --------------------------------------------------------------------------- #


def test_no_step_counter_survives_anywhere(sample_config, tmp_path, monkeypatch):
    """A count is a fact about the flow duplicated into every step: adding or
    reordering one silently makes the rest wrong. Names cannot go stale that way."""
    monkeypatch.setattr(onboard, "copy_to_clipboard", lambda _t: True)
    out: list[str] = []
    onboard.welcome(out.append)
    onboard.step_credentials(sample_config, ask=lambda _p: "n", show=out.append, login=lambda: 0)
    onboard.step_notifications(
        sample_config, config_path=tmp_path / "c.json", ask=lambda _p: "4", show=out.append
    )
    onboard.step_phone_prompt(sample_config, ask=lambda _p: "n", show=out.append)

    joined = "\n".join(out)
    for stale in ("[1/5]", "[2/5]", "[3/5]", "[4/5]", "[5/5]", "5 steps"):
        assert stale not in joined, f"{stale} orphans itself the moment the flow changes"
    assert "== CREDENTIALS ==" in joined


def test_the_roadmap_lists_names_not_a_count():
    """The preamble is the one place orientation about the WHOLE belongs - and it
    survives a flow change only if it names the steps rather than counting them."""
    out: list[str] = []
    onboard.welcome(out.append)
    joined = "\n".join(out)

    assert "credentials" in joined and "notifications" in joined
    assert "5 steps" not in joined


def test_no_forward_reference_survives(sample_config, tmp_path):
    ask, show, lines = _capture(["4"])
    onboard.step_notifications(sample_config, config_path=tmp_path / "c.json", ask=ask, show=show)

    assert "next:" not in "\n".join(lines)


# --------------------------------------------------------------------------- #
# the block itself
# --------------------------------------------------------------------------- #


def test_the_block_always_states_the_current_value_even_when_there_is_none():
    """Its absence is itself the answer on a fresh install, and a user deciding
    whether to change something needs to know what they are changing FROM."""
    out: list[str] = []
    question_block(out.append, choosing="a thing", keys=["1)|do it"])

    assert any("nothing selected yet" in line for line in out)


def test_the_keys_are_column_aligned():
    """The eye should find the column of things it may type, not read prose."""
    meanings = ["choose that one", "refresh", "skip"]
    out: list[str] = []
    question_block(
        out.append,
        choosing="a thing",
        keys=[f"1-21|{meanings[0]}", f"r)|{meanings[1]}", f"s)|{meanings[2]}"],
    )
    # Where each MEANING begins - the column the eye scans down.
    starts = {line.index(meaning) for meaning in meanings for line in out if meaning in line}

    assert len(starts) == 1, f"meanings must start at one column, got {sorted(starts)}"


def test_the_separator_is_ascii():
    """This lands on a Windows console that may be cp1252, and a rule that arrives
    as mojibake is worse than one that is plain."""
    assert RULE.isascii()


@pytest.fixture
def installed_cfg(tmp_path, tmp_mailbox):
    cfg_file = tmp_path / "voice-bridge.json"
    cfg_file.write_text(
        json.dumps({"mailbox_dir": str(tmp_mailbox), "state_dir": str(tmp_path / "state")}),
        encoding="utf-8",
    )
    cfg = load_config(cfg_file)
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    cfg.creds_env.write_text("x", encoding="utf-8")
    cfg.peer_inbox.write_text("m", encoding="utf-8")
    cfg.our_inbox.write_text("m", encoding="utf-8")
    return cfg, cfg_file
