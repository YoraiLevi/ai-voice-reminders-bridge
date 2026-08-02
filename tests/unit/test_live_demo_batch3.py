"""Act 2 of the demo: a human challenging the CONFIGURABILITY surface.

They set `from_name Hello`, ran `vox-prompt`, and nothing changed. That is not a
bug in the prompt - `from_name` only ever tagged mailbox lines - but they had no
way to know, because `config show` printed `from_name` and `spoke_name` side by
side with identical values and neither said what it drove. Two knobs that look
like one knob.

That challenge is what produced the ruling behind most of this file: **a setting
must be able to keep the promise its presence makes.** Where it cannot, it stops
being a setting. `from_name` folds into `spoke_name`; the list NAMES stop being
settable at all, because selection moved to ids and editing a name now changes a
caption and nothing else.

The two bugs are the same bug twice: a screen rendered from a snapshot that had
stopped being true.
"""

from __future__ import annotations

import json

import pytest

from voice_bridge import commands, doctor, selection
from voice_bridge.config import SETTABLE_KEYS, field_help, load_config, parse_overrides, set_value
from voice_bridge.transport import ListRef


def _io(answers):
    out: list[str] = []
    it = iter(answers)
    return (lambda _p: next(it)), out.append, out


def _cfg(tmp_path, tmp_mailbox, **extra):
    cfg_file = tmp_path / "voice-bridge.json"
    cfg_file.write_text(
        json.dumps(
            {"mailbox_dir": str(tmp_mailbox), "state_dir": str(tmp_path / "state"), **extra}
        ),
        encoding="utf-8",
    )
    return load_config(cfg_file), cfg_file


# --------------------------------------------------------------------------- #
# 1 - from_name folds into spoke_name
# --------------------------------------------------------------------------- #


def test_from_name_is_always_the_spoke_name(tmp_path, tmp_mailbox):
    """One identity, one place to change it.

    A stored `from_name` used to win silently, so the mailbox tag and the phone
    persona could disagree with no surface saying which you were looking at.
    """
    cfg, _ = _cfg(tmp_path, tmp_mailbox, spoke_name="Phone-Claude", from_name="Hello")
    assert cfg.from_name == "Phone-Claude"


def test_an_old_config_naming_from_name_still_loads(tmp_path, tmp_mailbox):
    """Deprecating a setting is not a reason to break someone's file."""
    cfg, _ = _cfg(tmp_path, tmp_mailbox, from_name="Hello")
    assert cfg.spoke_name == "vox"


def test_setting_from_name_names_the_field_that_replaced_it():
    """A refusal that does not name the replacement verb is how mis-advice starts."""
    with pytest.raises(ValueError, match="spoke_name"):
        parse_overrides(["from_name=Hello"])


# --------------------------------------------------------------------------- #
# 2 - the list NAMES leave the settable surface
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("key", ["inbox_list", "output_list"])
def test_list_names_are_not_settable_and_point_at_the_picker(key):
    """Ruled verbatim: "we can also deprecate ... names completely since we are
    using ids only". Editing one changed a caption while the id kept pointing
    where it pointed - a setting whose only visible effect is a label."""
    with pytest.raises(ValueError, match="lists --select"):
        parse_overrides([f"{key}=Anything"])


@pytest.mark.parametrize("key", ["from_name", "inbox_list", "output_list"])
def test_a_deprecated_key_is_not_reported_as_unknown(key):
    """ "unknown setting" for a name the user just read in their own config file
    sends them hunting a typo that is not there."""
    with pytest.raises(ValueError) as err:
        parse_overrides([f"{key}=x"])
    assert "unknown setting" not in str(err.value)


@pytest.mark.parametrize("key", ["from_name", "inbox_list", "output_list"])
def test_config_fields_does_not_advertise_a_derived_key(key):
    assert key not in {k for k, _ in field_help()}
    assert key not in SETTABLE_KEYS


def test_the_program_can_still_cache_a_display_name(tmp_path):
    """The deprecation removes a KNOB, not the storage. The picker still records
    what the chosen list is really called."""
    cfg_file = tmp_path / "voice-bridge.json"
    cfg_file.write_text("{}", encoding="utf-8")
    set_value(cfg_file, "inbox_list", "Test new List", internal=True)
    assert json.loads(cfg_file.read_text(encoding="utf-8"))["inbox_list"] == "Test new List"


# --------------------------------------------------------------------------- #
# 3-4 - one vocabulary, and it is the user's seat
# --------------------------------------------------------------------------- #


def test_the_lists_markers_say_what_the_list_does_not_which_field_it_is(
    tmp_path, tmp_mailbox, capsys, fake_transport
):
    """`<- active outbox` was printed against the list literally titled
    "Vox-Message-Inbox": inverted, because it came from the config field names,
    which are written from the BRIDGE's seat."""
    ref = fake_transport.list_todo_lists()[0]
    cfg, _ = _cfg(tmp_path, tmp_mailbox, inbox_list_id=ref.id)
    commands.list_command(cfg, fake_transport, as_json=False)
    out = capsys.readouterr().out

    assert "<- SELECTED: you dictate into this one (phone -> Reminders -> PC)" in out
    assert "active inbox" not in out
    assert "active outbox" not in out


def test_the_json_rows_keep_the_field_vocabulary(tmp_path, tmp_mailbox, capsys, fake_transport):
    """Only the PROSE moves to the user's seat. A program reading --json wants the
    names that appear in the config."""
    ref = fake_transport.list_todo_lists()[0]
    cfg, _ = _cfg(tmp_path, tmp_mailbox, inbox_list_id=ref.id)
    commands.list_command(cfg, fake_transport, as_json=True)
    rows = json.loads(capsys.readouterr().out)

    assert any(r["role"] == "inbox" and r["active"] for r in rows)


def test_every_surface_draws_its_words_from_one_place():
    """The point of the fix is structural, not editorial: four screens had four
    vocabularies for two lists. If a surface ever hard-codes its own phrasing
    again, this is the test that should have caught it."""
    assert selection.ROLE_LABEL["inbox"] == "the list you dictate into"
    assert selection.ROLE_DIRECTION["inbox"] in selection.ROLE_SELECTED_MARK["inbox"]
    assert selection.ROLE_DIRECTION["outbox"] in selection.ROLE_SELECTED_MARK["outbox"]
    # The picker teaches the same directions it will later echo.
    _, teach = selection._ROLE_TEACH["inbox"]
    assert selection.ROLE_DIRECTION["inbox"] in teach


def test_the_role_menu_uses_the_shared_labels(tmp_path, tmp_mailbox, fake_transport):
    cfg, cfg_file = _cfg(tmp_path, tmp_mailbox)
    ask, show, out = _io(["d"])
    commands.select_command(
        cfg, fake_transport, config_path=cfg_file, ask=ask, show=show, is_tty=lambda: True
    )
    text = "\n".join(out)

    assert selection.ROLE_LABEL["inbox"] in text
    assert selection.ROLE_LABEL["outbox"] in text
    assert "dictations arrive in" not in text
    assert "replies go out to" not in text


# --------------------------------------------------------------------------- #
# 5 - BUG: the caption came from the config while the identity came from the id
# --------------------------------------------------------------------------- #


def test_the_display_name_is_read_from_the_account_by_id(tmp_path, tmp_mailbox, fake_transport):
    """Observed: after choosing "Test new List" the menu re-rendered captioned
    with the OLD name beside the NEW id. Two sources for one fact, and the stale
    one was the one on screen."""
    ref = fake_transport.add_list("Test new List")
    cfg, _ = _cfg(tmp_path, tmp_mailbox, inbox_list_id=ref.id, inbox_list="Vox-Message-Outbox")
    plan = selection.resolve_selection(cfg, fake_transport)
    inbox = next(r for r in plan.resolutions if r.role == "inbox")

    assert inbox.name == "Test new List", "the caption must follow the id, not the config"


def test_a_stale_selection_falls_back_to_the_cached_name(tmp_path, tmp_mailbox, fake_transport):
    """When the id matches nothing, the cached name is all anyone knows about the
    list that went missing - so it is shown rather than blanked."""
    cfg, _ = _cfg(tmp_path, tmp_mailbox, inbox_list_id="GONE", inbox_list="The Vanished List")
    plan = selection.resolve_selection(cfg, fake_transport)
    inbox = next(r for r in plan.resolutions if r.role == "inbox")

    assert inbox.status == "stale"
    assert inbox.name == "The Vanished List"


def test_nothing_selected_shows_no_caption_at_all(tmp_path, tmp_mailbox, fake_transport):
    """A name with no id behind it is a leftover, not a selection, and printing it
    would suggest something is chosen."""
    cfg, _ = _cfg(tmp_path, tmp_mailbox)
    plan = selection.resolve_selection(cfg, fake_transport)

    assert all(r.name == "" for r in plan.resolutions)


def test_doctor_refreshes_a_renamed_list_in_the_cache(tmp_path, tmp_mailbox, fake_transport):
    """The id is the identity, so a rename on the phone breaks nothing - but the
    phone prompt PRINTS the name, and a stale label reads as a broken tool."""
    ref = fake_transport.add_list("Renamed On The Phone")
    cfg, cfg_file = _cfg(tmp_path, tmp_mailbox, inbox_list_id=ref.id, inbox_list="Old Name")
    doctor._list_rows(cfg, fake_transport)

    assert json.loads(cfg_file.read_text(encoding="utf-8"))["inbox_list"] == "Renamed On The Phone"


# --------------------------------------------------------------------------- #
# 6 - BUG: a false "no longer exists" about a list chosen seconds earlier
# --------------------------------------------------------------------------- #


def test_a_list_created_mid_session_is_not_declared_missing(tmp_path, tmp_mailbox, fake_transport):
    """`r) refresh` exists so a list made on the phone WHILE the prompt is open can
    be chosen. Re-classifying that choice against the inventory from before it
    existed called it stale - so the menu announced "the selected list no longer
    exists" about a list the user had just picked off a refreshed screen.

    Mis-advice class, fourth appearance: it points at a repair for a healthy row.
    """
    cfg, cfg_file = _cfg(tmp_path, tmp_mailbox)

    # The user opens the menu, then creates a list on the phone, then refreshes.
    def ask(prompt: str) -> str:
        if "Choose 1-2, or d" in prompt:  # the role menu
            return "1" if not created else "d"
        if prompt.startswith("Choose"):  # the picker
            if not created:
                created.append(fake_transport.add_list("Made While You Waited"))
                return "r"
            return str(
                next(
                    i
                    for i, r in enumerate(fake_transport.list_todo_lists(), start=1)
                    if r.id == created[0].id
                )
            )
        return "d"

    created: list[ListRef] = []
    out: list[str] = []
    commands.select_command(
        cfg,
        fake_transport,
        config_path=cfg_file,
        ask=ask,
        show=out.append,
        is_tty=lambda: True,
    )
    text = "\n".join(out)

    assert "Made While You Waited" in text
    assert "no longer exists" not in text, "a false stale note is worse than no note"


# --------------------------------------------------------------------------- #
# 7 - every config subcommand carries its own help
# --------------------------------------------------------------------------- #


def test_every_config_subcommand_has_help():
    """Three of four printed bare, so the listing told you the verbs existed and
    nothing else."""
    from voice_bridge import cli

    parser = cli._build_parser()
    config_action = next(
        a
        for a in parser._subparsers._group_actions[0].choices["config"]._actions  # type: ignore[union-attr]
        if getattr(a, "choices", None) and "show" in a.choices
    )
    for name, sub in config_action.choices.items():
        assert sub.description or config_action._choices_actions, name

    helps = {c.dest: c.help for c in config_action._choices_actions}
    for verb in ("show", "fields", "get", "set"):
        assert helps.get(verb), f"`config {verb}` has no help string"


def test_config_show_separates_derived_from_settable(tmp_path, tmp_mailbox, capsys):
    """Printed together they read as one list of knobs - which is exactly how a
    user came to set `from_name` and wonder why nothing happened."""
    from voice_bridge import cli

    cfg, cfg_file = _cfg(tmp_path, tmp_mailbox, spoke_name="Phone-Claude")
    assert cli.main(["--config", str(cfg_file), "config", "show"]) == 0
    out = capsys.readouterr().out

    assert "derived - read-only" in out
    settable_half, _, derived_half = out.partition("derived - read-only")
    assert "spoke_name" in settable_half
    assert "from_name" in derived_half
    assert "inbox_list " in derived_half or "inbox_list" in derived_half
