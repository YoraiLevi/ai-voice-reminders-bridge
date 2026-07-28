"""Thirteen findings from a human running guided setup twice, end to end.

They are grouped here rather than scattered into the per-module suites because
they share one cause and one ruling. The cause: **copy written from the seat of
someone who already knows how this works.** The ruling, verbatim: *"in general
don't baby the user"* - which is not a request for terseness but for respect. Say
the true thing, name the consequence, and do not pad it with reassurance.

Three were defects rather than wording: a configured spoke name the phone prompt
ignored, transient-failure advice that sent a correctly-logged-in user back to
re-enter working credentials, and a yes/no question that re-asked in silence.
"""

from __future__ import annotations

import json

import pytest

from voice_bridge import onboard, prompt, runner, selection, setup as setup_mod
from voice_bridge.config import load_config
from voice_bridge.transport import ListRef


def _io(answers):
    out: list[str] = []
    it = iter(answers)
    return (lambda _p: next(it)), out.append, out


def _resolution(role="inbox", *, current="", status="unselected", candidates=None):
    return selection.Resolution(
        role=role,
        name="A List",
        field="inbox_list_id" if role == "inbox" else "output_list_id",
        current=current,
        status=status,
        candidates=candidates if candidates is not None else [ListRef(id="i1", name="A List")],
        chosen=current if status == "selected" else "",
    )


# --------------------------------------------------------------------------- #
# 1 - the name questions were pre-selection-era residue
# --------------------------------------------------------------------------- #


def test_setup_no_longer_asks_for_list_names(sample_config, monkeypatch):
    """The picker writes the chosen list's REAL name, so an answer here is deleted
    by the same run that collected it - the user types something and watches it be
    ignored, with no way to tell that is correct."""
    monkeypatch.setattr(setup_mod, "_is_tty", lambda: True)
    asked: list[str] = []
    monkeypatch.setattr(setup_mod, "_ask", lambda field, label, default: asked.append(field) or "")
    setup_mod.prompt_fields(sample_config, preset={})

    assert "inbox_list" not in asked
    assert "output_list" not in asked


# --------------------------------------------------------------------------- #
# 2 - the picker must teach at the point of choice
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("role", "must_say", "direction"),
    [
        ("inbox", "OUTBOX", "phone -> Reminders -> PC"),
        ("outbox", "INBOX", "PC -> Reminders -> phone"),
    ],
)
def test_the_picker_header_teaches_which_direction_this_list_carries(role, must_say, direction):
    """A first-time user does not know these words yet, and cannot be told once at
    the top and expected to still hold it two prompts later."""
    ask, show, out = _io(["s"])
    selection.pick(_resolution(role), ask=ask, show=show)
    text = "\n".join(out)

    assert must_say in text
    assert direction in text


def test_the_teaching_words_are_from_the_users_seat_not_the_configs(sample_config):
    """The crossing that cost us once (UX-1): the list you DICTATE into is your
    OUTBOX and the bridge's `inbox_list`. If this ever reads "inbox" for the
    dictation role, the two seats have been confused again."""
    ask, show, out = _io(["s"])
    selection.pick(_resolution("inbox"), ask=ask, show=show)
    header = "\n".join(out)

    assert "receive your dictations" in header
    assert "your OUTBOX" in header


# --------------------------------------------------------------------------- #
# 3 - "skip, change nothing" is a lie when there is nothing selected
# --------------------------------------------------------------------------- #


def test_skip_states_the_consequence_when_nothing_is_selected_yet():
    """From-zero setup: skipping leaves the install unable to run, and the label
    promised the opposite."""
    ask, show, out = _io(["s"])
    selection.pick(_resolution(current=""), ask=ask, show=show)
    text = "\n".join(out)

    assert "the bridge cannot run until a list is selected" in text
    assert "skip, change nothing" not in text


def test_skip_keeps_its_old_label_when_there_IS_something_to_keep():
    """The exit is never hidden - only relabelled - and re-running setup on a
    configured machine must still be the safe, boring thing it always was."""
    ask, show, out = _io(["s"])
    choice = selection.pick(_resolution(current="i1", status="selected"), ask=ask, show=show)
    text = "\n".join(out)

    assert "skip, change nothing" in text
    assert choice.action == "skip"


# --------------------------------------------------------------------------- #
# 5 - acceptance must be explicit, program-wide
# --------------------------------------------------------------------------- #


def test_a_chosen_list_is_echoed_as_ACCEPTED():
    """An echo alone is ambiguous: it reads the same whether the program took the
    answer, wants it checked, or is about to reject it."""
    out: list[str] = []
    selection.confirm_selection(ListRef(id="i1", name="A List"), role="inbox", show=out.append)
    assert out and out[0].strip().startswith("ACCEPTED")


def test_a_typed_config_answer_is_echoed_as_ACCEPTED(sample_config, monkeypatch, capsys):
    monkeypatch.setattr(setup_mod, "_is_tty", lambda: True)
    monkeypatch.setattr(
        setup_mod,
        "_ask",
        lambda field, label, default: "Phone-Claude" if field == "spoke_name" else "",
    )
    setup_mod.prompt_fields(sample_config, preset={})

    printed = capsys.readouterr().out
    assert "ACCEPTED" in printed
    assert "Phone-Claude" in printed


# --------------------------------------------------------------------------- #
# 6-8 - notifications: say what it is, and name the topic
# --------------------------------------------------------------------------- #


def test_notifications_are_called_push_notifications_and_the_cost_is_manual_looking(
    sample_config, tmp_path
):
    """ "A banner the moment a reply arrives" described a thing in a word the user
    may not use for it, and priced skipping it as a mild inconvenience."""
    ask, show, out = _io(["4"])
    onboard.step_notifications(
        sample_config, config_path=tmp_path / "voice-bridge.json", ask=ask, show=show
    )
    text = "\n".join(out)

    assert "PUSH NOTIFICATION" in text.upper()
    assert "MANUALLY" in text


def test_a_generated_topic_carries_128_bits(sample_config):
    """A topic is a BEARER CREDENTIAL on a public server: no account, no password,
    no revocation. Knowing the string is the authorisation."""
    topic = onboard.suggest_topic()
    body = topic.removeprefix("vox-")

    assert len(body) == 32, "32 hex chars = 128 bits"
    int(body, 16)  # raises if it is not hex
    assert onboard.suggest_topic() != topic


def test_the_subscribe_instruction_names_the_topic_for_a_generated_one(sample_config, tmp_path):
    """Option 1's topic is one the user never typed and cannot be expected to have
    memorised, so "subscribe to that topic" points at nothing they can act on."""
    ask, show, out = _io(["1"])
    onboard.step_notifications(
        sample_config, config_path=tmp_path / "voice-bridge.json", ask=ask, show=show
    )
    saved = sample_config.ntfy_topic_file.read_text(encoding="utf-8").strip()
    text = "\n".join(out)

    assert saved
    assert f"subscribed to  {saved}" in text
    assert "ACCEPTED" in text


def test_the_subscribe_instruction_names_the_topic_for_a_typed_one(sample_config, tmp_path):
    ask, show, out = _io(["2", "my-own-topic"])
    onboard.step_notifications(
        sample_config, config_path=tmp_path / "voice-bridge.json", ask=ask, show=show
    )
    assert "subscribed to  my-own-topic" in "\n".join(out)


def test_a_custom_server_is_named_too(sample_config, tmp_path):
    """Subscribing on the wrong server is a silent failure: the app shows a topic
    that simply never fires."""
    cfg_file = tmp_path / "voice-bridge.json"
    cfg_file.write_text("{}", encoding="utf-8")
    ask, show, out = _io(["3", "https://ntfy.example.test", "t1"])
    onboard.step_notifications(sample_config, config_path=cfg_file, ask=ask, show=show)
    text = "\n".join(out)

    assert "ntfy.example.test" in text
    assert "subscribed to  t1" in text


# --------------------------------------------------------------------------- #
# 9 - the test message becomes see-and-confirm
# --------------------------------------------------------------------------- #


def _result(ok=True):
    return {
        "dictation_delivered": ok,
        "reply_delivered": ok,
        "banner_sent": ok,
        "banner_detail": "",
        "title_renderable": None,
        "title_detail": "",
    }


def test_the_test_step_says_what_it_cannot_check(sample_config):
    """A green tick the user cannot corroborate is the false-confidence shape this
    project keeps removing."""
    ask, show, out = _io(["n"])
    onboard.step_test_message(
        sample_config, ask=ask, show=show, run_verify=lambda: _result(), probe="P"
    )
    text = "\n".join(out)

    assert "MACHINE round trip" in text
    assert "cannot see your phone's screen" in text


def test_the_test_step_lists_what_should_be_visible_then_asks(sample_config):
    sample_config.ntfy_topic_file.parent.mkdir(parents=True, exist_ok=True)
    sample_config.ntfy_topic_file.write_text("vox-abc\n", encoding="utf-8")

    ask, show, out = _io(["y", "y"])  # send it, then yes I saw them
    ok = onboard.step_test_message(
        sample_config, ask=ask, show=show, run_verify=lambda: _result(), probe="PROBE-TITLE"
    )
    text = "\n".join(out)

    assert ok is True
    assert "PROBE-TITLE" in text
    assert sample_config.output_list in text
    assert "Completed" in text, "the probe is completed immediately; say where to look"
    assert "vox-abc" in text
    assert "ACCEPTED" in text


def test_saying_no_is_never_a_dead_end(sample_config):
    """A "no" is not a failure to report - it is the start of the part we can help
    with. The old flow printed an ok-line and moved on regardless."""
    ask, show, out = _io(["y", "n", "n"])  # send, did not see them, do not resend
    ok = onboard.step_test_message(
        sample_config, ask=ask, show=show, run_verify=lambda: _result(), probe="P"
    )
    text = "\n".join(out)

    assert ok is False
    assert "Sync lag" in text
    assert sample_config.output_list_id in text or "Wrong list" in text
    assert "setup --verify" in text


def test_saying_no_can_retry_in_place(sample_config):
    runs = []

    def run_verify():
        runs.append(1)
        return _result()

    ask, show, _ = _io(["y", "n", "y", "y"])  # send, missed, resend, saw it
    ok = onboard.step_test_message(
        sample_config, ask=ask, show=show, run_verify=run_verify, probe="P"
    )
    assert ok is True
    assert len(runs) == 2


# --------------------------------------------------------------------------- #
# 10 - DEFECT: the configured spoke name was ignored by the prompt
# --------------------------------------------------------------------------- #


def test_the_phone_prompt_uses_the_configured_spoke_name(tmp_path, tmp_mailbox):
    """They set "Phone-Claude" and the prompt still opened "You are VOX".

    A configured identity that the one artifact carrying it ignores is worse than
    no setting at all: every other surface - the mailbox line tag, the banner title
    - agrees with the user, and only the phone disagrees.
    """
    cfg_file = tmp_path / "voice-bridge.json"
    cfg_file.write_text(
        json.dumps(
            {
                "spoke_name": "Phone-Claude",
                "mailbox_dir": str(tmp_mailbox),
                "state_dir": str(tmp_path / "state"),
                # Both roles selected: the prompt refuses to render without them.
                "inbox_list_id": "i1",
                "output_list_id": "o1",
                "inbox_list": "In",
                "output_list": "Out",
            }
        ),
        encoding="utf-8",
    )
    out = prompt.render_vox_prompt(load_config(cfg_file))

    assert "Phone-Claude" in out
    assert "You are VOX" not in out


# --------------------------------------------------------------------------- #
# 11 - "a peer must join" named a requirement, not an action
# --------------------------------------------------------------------------- #


def test_a_new_mailbox_names_its_files_and_what_each_is_for(sample_config):
    lines = runner.announce_new_mailbox(sample_config)
    text = "\n".join(lines)

    assert sample_config.peer_inbox.name in text
    assert sample_config.our_inbox.name in text
    assert "READS" in text and "WRITES" in text
    assert "a peer must join to process messages" not in text


def test_a_new_mailbox_gets_a_paste_ready_peer_prompt(sample_config):
    """Written, not only printed: this output scrolls past while the bridge starts,
    and the thing you must paste at an agent should still be there tomorrow."""
    runner.announce_new_mailbox(sample_config)
    written = (sample_config.mailbox_dir / runner.PEER_PROMPT_FILE).read_text(encoding="utf-8")

    assert str(sample_config.peer_inbox) in written
    assert str(sample_config.our_inbox) in written
    assert sample_config.route_to in written


def test_an_existing_peer_prompt_is_never_overwritten(sample_config):
    """It may have been edited, or put there by another spoke. We fill a gap."""
    target = sample_config.mailbox_dir / runner.PEER_PROMPT_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("MINE", encoding="utf-8")

    runner.announce_new_mailbox(sample_config)
    assert target.read_text(encoding="utf-8") == "MINE"


def test_the_peer_prompt_renders_every_token(sample_config):
    """`Template.substitute` raises on anything it cannot fill, so a token this
    version does not know can never ship to a reader as literal `${...}`."""
    text = prompt.render_peer_prompt(sample_config)

    assert "${" not in text
    assert "<!--" not in text, "maintainer notes are not instructions"


# --------------------------------------------------------------------------- #
# 12 - DEFECT: transient failures were given authentication advice
# --------------------------------------------------------------------------- #


def test_a_transient_failure_is_not_answered_with_relogin(tmp_path, monkeypatch, capsys):
    """Live run 1 died mid-lists AFTER a successful login and a successful first
    pick, and was told to run `icloud-login`. The credentials were fine.

    A message that names a REMEDY is a claim about the CAUSE - third appearance of
    this class, so it gets a test of its own.
    """
    import requests

    from voice_bridge import factory

    def boom(_cfg):
        raise requests.ConnectionError("Request failed")

    monkeypatch.setattr(factory, "make_transport", boom)
    monkeypatch.setattr(setup_mod, "make_transport", boom)
    monkeypatch.setattr(setup_mod, "_is_tty", lambda: False)

    code = setup_mod.run_setup(config_path=tmp_path / "voice-bridge.json")
    out = capsys.readouterr().out

    assert code == 0
    assert "icloud-login" not in out, "re-entering working credentials fixes nothing"
    assert "temporary" in out.lower()
    assert "voice-bridge setup" in out


def test_an_auth_failure_still_gets_the_login_advice(tmp_path, monkeypatch, capsys):
    """The other half of the split: the advice must still be RIGHT when the cause
    really is authentication."""
    from voice_bridge import setup as s
    from voice_bridge.icloud import ICloudError

    def boom(_cfg):
        raise ICloudError("no credentials")

    monkeypatch.setattr(s, "make_transport", boom)
    monkeypatch.setattr(s, "_is_tty", lambda: False)

    code = s.run_setup(config_path=tmp_path / "voice-bridge.json")
    out = capsys.readouterr().out

    assert code == 0
    assert "icloud-login" in out


# --------------------------------------------------------------------------- #
# 13 - a question that re-asks in silence looks like a question being ignored
# --------------------------------------------------------------------------- #


def test_an_unusable_answer_says_why_it_is_asking_again():
    """A transcript from live use shows the same question twice with nothing
    between them. Neither the user nor we could tell from the output whether input
    had been rejected or dropped."""
    ask, show, out = _io(["maybe", "y"])
    assert onboard._yes(ask, "Start the bridge now?", show=show) is True
    assert any("not an answer" in line for line in out)


def test_a_bare_enter_still_takes_the_shown_default():
    """A real keystroke from a real person honours what the prompt displayed. Only
    a CLOSED STREAM is refused - that distinction is the whole of section 21."""
    ask, show, _ = _io([""])
    assert onboard._yes(ask, "q", default=True, show=show) is True

    ask, show, _ = _io([""])
    assert onboard._yes(ask, "q", default=False, show=show) is False
