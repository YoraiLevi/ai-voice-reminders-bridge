"""One resolver, one picker — the engine behind every pin verb (UX-6).

The whole feature exists to end a silent failure: a list NAME is not an identity,
so resolving by name on an account with duplicates picks one at random, forever,
and without a symptom. The tests below are therefore mostly about refusing to
choose — the resolver must report ambiguity rather than resolve it, because any
tie-break it invented would recreate the exact silence pinning removes.
"""

from __future__ import annotations

import dataclasses

import pytest

from voice_bridge.pins import (
    Choice,
    Resolution,
    pick,
    resolve_pins,
)
from voice_bridge.transport import FakeTransport, ListRef


def _cfg(sample_config, **kw):
    return dataclasses.replace(sample_config, **kw)


def _by_role(plan, role: str) -> Resolution:
    return next(r for r in plan.resolutions if r.role == role)


# --------------------------------------------------------------------------- #
# resolve_pins — the single engine
# --------------------------------------------------------------------------- #


def test_a_single_match_is_chosen_without_asking(sample_config):
    """Nothing to disambiguate means nothing to ask. Silence is correct here."""
    t = FakeTransport()
    t.add_list("Vox-Message-Outbox", "A1")
    t.add_list("Vox-Message-Inbox", "B1")

    plan = resolve_pins(sample_config, t)

    inbox = _by_role(plan, "inbox")
    assert inbox.status == "chosen"
    assert inbox.chosen == "A1"
    assert not plan.ambiguous


def test_duplicate_names_are_ambiguous_not_resolved(sample_config, ghost_transport):
    """Two lists, one name: the resolver must hand the choice back, not make it."""
    plan = resolve_pins(sample_config, ghost_transport)

    inbox = _by_role(plan, "inbox")
    assert inbox.status == "ambiguous"
    assert {c.id for c in inbox.candidates} == {"L1", "L9"}
    assert inbox.chosen == "", "an ambiguous resolution must not smuggle in a pick"
    assert plan.ambiguous


def test_no_tie_break_even_when_one_candidate_looks_obvious(sample_config, ghost_transport):
    """The load-bearing negative test.

    One ghost is empty and one holds 40 items — the single most tempting heuristic
    there is. It must STILL be ambiguous: on a real account the fuller list is
    just as likely to be the abandoned one, and a wrong automatic pick is both
    invisible and permanent.
    """
    live = [r for r in ghost_transport.list_todo_lists() if r.id == "L9"][0]
    for i in range(40):
        ghost_transport.add_todo(live, f"item {i}")

    assert _by_role(resolve_pins(sample_config, ghost_transport), "inbox").status == "ambiguous"


def test_names_differing_only_by_case_are_one_conflict(sample_config):
    """A real account carried `Team Inbox` and `Team inbox`.

    Exact matching finds precisely ONE `Vox-Message-outbox`, reports no ambiguity,
    and pins it with total confidence — while the user meant the other one and was
    never asked. Case-insensitive DETECTION is what makes the twin visible.
    """
    t = FakeTransport()
    t.add_list("Vox-Message-Outbox", "C1")
    t.add_list("vox-message-outbox", "C2")
    t.add_list("Vox-Message-Inbox", "D1")

    inbox = _by_role(resolve_pins(_cfg(sample_config), t), "inbox")

    assert inbox.status == "ambiguous"
    assert {c.id for c in inbox.candidates} == {"C1", "C2"}
    assert {c.name for c in inbox.candidates} == {"Vox-Message-Outbox", "vox-message-outbox"}, (
        "detection folds case; DISPLAY must keep it, since it is the only difference"
    )


def test_a_valid_pin_settles_it_even_among_ghosts(sample_config, ghost_transport):
    """This is what a pin is FOR — the duplicates stop mattering."""
    inbox = _by_role(
        resolve_pins(_cfg(sample_config, inbox_list_id="L9"), ghost_transport), "inbox"
    )
    assert inbox.status == "chosen"
    assert inbox.chosen == "L9"
    assert inbox.current == "L9"


def test_a_pin_pointing_at_nothing_is_dangling_not_missing(sample_config, ghost_transport):
    """Distinct from `missing`, because the remedies differ: a dangling pin must be
    CLEARED, while a missing list must be CREATED."""
    inbox = _by_role(
        resolve_pins(_cfg(sample_config, inbox_list_id="GONE"), ghost_transport), "inbox"
    )
    assert inbox.status == "dangling"
    assert inbox.current == "GONE"
    assert {c.id for c in inbox.candidates} == {"L1", "L9"}, (
        "the live candidates are still offered, so a re-pick needs no second lookup"
    )


def test_no_list_by_that_name_is_missing(sample_config):
    inbox = _by_role(resolve_pins(sample_config, FakeTransport()), "inbox")
    assert inbox.status == "missing"
    assert inbox.candidates == []


def test_both_roles_are_resolved_independently(sample_config, ghost_transport):
    plan = resolve_pins(sample_config, ghost_transport)
    assert _by_role(plan, "inbox").status == "ambiguous"
    assert _by_role(plan, "outbox").status == "chosen"
    assert _by_role(plan, "outbox").chosen == "L2"


def test_each_resolution_names_the_config_field_to_write(sample_config, ghost_transport):
    """Callers must never map role->field themselves; that mapping is the crossing
    that UX-1 already proved easy to get backwards."""
    plan = resolve_pins(sample_config, ghost_transport)
    assert _by_role(plan, "inbox").field == "inbox_list_id"
    assert _by_role(plan, "outbox").field == "output_list_id"


# --------------------------------------------------------------------------- #
# pick — the ONE picker component
# --------------------------------------------------------------------------- #


def _resolution(status="ambiguous", current="", candidates=None) -> Resolution:
    return Resolution(
        role="inbox",
        name="Vox-Message-Outbox",
        field="inbox_list_id",
        current=current,
        status=status,
        candidates=candidates
        or [
            ListRef(name="Vox-Message-Outbox", id="L1", count=0, color="blue"),
            ListRef(name="Vox-Message-Outbox", id="L9", count=12, color="red"),
        ],
        chosen="",
    )


def _run(resolution, answers: list[str]) -> tuple[Choice, str]:
    out: list[str] = []
    it = iter(answers)
    choice = pick(resolution, ask=lambda _prompt: next(it), show=out.append)
    return choice, "\n".join(out)


def test_choosing_a_number_pins_that_id():
    choice, _ = _run(_resolution(), ["2"])
    assert choice.action == "pin"
    assert choice.list_id == "L9"


def test_the_picker_shows_the_metadata_that_actually_exists():
    """Item count and colour are what the user SEES on the phone, so a row can be
    matched to the list they recognise. They are also free — already in the list
    data — so rendering the picker costs no extra API calls.
    """
    _, shown = _run(_resolution(), ["1"])
    assert "12" in shown and "blue" in shown


def test_the_picker_never_shows_a_date():
    """RemindersList in pyicloud 2.6.5 carries no creation or modification date.
    A plausible "last modified" column would be a confident fabrication — the
    exact failure this feature exists to remove."""
    _, shown = _run(_resolution(), ["1"])
    for word in ("modified", "created", "ago", "date"):
        assert word not in shown.lower()


def test_keep_and_clear_are_hidden_when_there_is_no_pin():
    """Offering to clear nothing is noise on a fresh machine."""
    _, shown = _run(_resolution(current=""), ["1"])
    lowered = shown.lower()
    assert "keep" not in lowered
    assert "clear" not in lowered


def test_keep_and_clear_appear_once_a_pin_exists():
    _, shown = _run(_resolution(current="L1"), ["k"])
    lowered = shown.lower()
    assert "keep" in lowered and "clear" in lowered


def test_the_current_pin_is_marked_in_the_rows():
    _, shown = _run(_resolution(current="L9"), ["k"])
    marked = [ln for ln in shown.splitlines() if "L9" in ln or "12" in ln]
    assert any("current" in ln.lower() or "←" in ln or "<-" in ln for ln in marked)


def test_keep_changes_nothing():
    choice, _ = _run(_resolution(current="L1"), ["k"])
    assert choice.action == "keep"
    assert choice.list_id == ""


def test_clear_is_available_and_distinct_from_quitting():
    """`clear` means "forget the pin"; `quit` means "leave the config alone".
    Collapsing them would make an abort look like an edit."""
    assert _run(_resolution(current="L1"), ["c"])[0].action == "clear"
    assert _run(_resolution(current="L1"), ["q"])[0].action == "quit"


def test_the_header_counts_the_rows_it_is_showing():
    """The header cannot disagree with the list underneath it."""
    three = [
        ListRef(name="Vox-Message-Outbox", id="L1", count=1),
        ListRef(name="Vox-Message-Outbox", id="L2", count=2),
        ListRef(name="vox-message-outbox", id="L3", count=3),
    ]
    _, shown = _run(_resolution(candidates=three), ["1"])
    assert "3" in shown.splitlines()[0]


def test_a_case_only_difference_is_called_out():
    """Two rows that look almost identical need the reason stated, or the user
    assumes the picker is repeating itself."""
    twins = [
        ListRef(name="Vox-Message-Outbox", id="L1"),
        ListRef(name="vox-message-outbox", id="L2"),
    ]
    _, shown = _run(_resolution(candidates=twins), ["1"])
    assert "capitalisation" in shown.lower() or "case" in shown.lower()


@pytest.mark.parametrize("bad", ["", "0", "9", "banana", "-1"])
def test_unusable_answers_re_ask_rather_than_guessing(bad):
    """An out-of-range number must never fall through to a default pick — that is
    the silent wrong choice arriving by another door."""
    choice, shown = _run(_resolution(), [bad, "1"])
    assert choice.action == "pin"
    assert choice.list_id == "L1"
    assert "?" in shown or "choose" in shown.lower()


def test_end_of_input_quits_instead_of_hanging_or_guessing():
    """isatty can lie (MSYS, Git Bash). If the stream ends, treat it as "no answer"
    and change nothing — never block, never invent a pick."""

    def _eof(_prompt):
        raise EOFError

    assert pick(_resolution(), ask=_eof, show=lambda _s: None).action == "quit"


# --------------------------------------------------------------------------- #
# lists --pin — the management door
# --------------------------------------------------------------------------- #


def _pin_run(cfg, transport, answers, tmp_path, tty=True):
    """Drive pin_command with scripted answers; return (exit code, output, config)."""
    import json as _json

    from voice_bridge.commands import pin_command

    path = tmp_path / "voice-bridge.json"
    if not path.exists():
        path.write_text("{}", encoding="utf-8")
    out: list[str] = []
    it = iter(answers)
    code = pin_command(
        cfg,
        transport,
        config_path=path,
        ask=lambda _p: next(it),
        show=out.append,
        is_tty=lambda: tty,
    )
    written = _json.loads(path.read_text(encoding="utf-8"))
    return code, "\n".join(out), written


def test_pinning_writes_the_chosen_id_to_the_right_field(sample_config, ghost_transport, tmp_path):
    """The role->field crossing is the one UX-1 proved easy to invert."""
    code, out, written = _pin_run(sample_config, ghost_transport, ["2", "1"], tmp_path)
    assert code == 0
    assert written["inbox_list_id"] == "L9"
    assert "pinned L9" in out


def test_clearing_writes_an_empty_pin(sample_config, ghost_transport, tmp_path):
    cfg = _cfg(sample_config, inbox_list_id="L1")
    _, out, written = _pin_run(cfg, ghost_transport, ["c", "q"], tmp_path)
    assert written["inbox_list_id"] == ""
    assert "cleared" in out


def test_quitting_writes_nothing(sample_config, ghost_transport, tmp_path):
    cfg = _cfg(sample_config, inbox_list_id="L1")
    _, out, written = _pin_run(cfg, ghost_transport, ["q", "q"], tmp_path)
    assert "inbox_list_id" not in written
    assert "unchanged" in out


def test_a_change_reminds_you_to_re_copy_the_phone_prompt(sample_config, ghost_transport, tmp_path):
    """The pins are baked into the rendered prompt, so a changed pin the phone
    never receives is a bridge that silently talks to the wrong list."""
    _, out, _ = _pin_run(sample_config, ghost_transport, ["1", "1"], tmp_path)
    assert "vox-prompt" in out


def test_an_unchanged_run_does_not_nag_about_the_prompt(sample_config, ghost_transport, tmp_path):
    _, out, _ = _pin_run(sample_config, ghost_transport, ["q", "q"], tmp_path)
    assert "vox-prompt" not in out


def test_a_dangling_pin_is_explained_before_the_picker(sample_config, ghost_transport, tmp_path):
    cfg = _cfg(sample_config, inbox_list_id="GONE")
    _, out, written = _pin_run(cfg, ghost_transport, ["1", "q"], tmp_path)
    assert "no longer exists" in out
    assert written["inbox_list_id"] == "L1", "a re-pick must replace the dead id"


def test_a_missing_list_sends_you_to_setup_not_an_empty_menu(sample_config, tmp_path):
    _, out, written = _pin_run(sample_config, FakeTransport(), [], tmp_path)
    assert "setup" in out
    assert "inbox_list_id" not in written, "nothing to choose means nothing to write"


def test_non_interactive_refuses_rather_than_hanging(sample_config, ghost_transport, tmp_path):
    """A prompt written to a pipe blocks for ever, or reads EOF and takes an answer
    nobody gave. Both are worse than exiting with instructions."""
    code, out, written = _pin_run(sample_config, ghost_transport, [], tmp_path, tty=False)
    assert code == 2
    assert "config set inbox_list_id" in out, "it must name the non-interactive way through"
    assert "inbox_list_id" not in written


# --------------------------------------------------------------------------- #
# setup's door — pin from the very beginning, ask only when it must
# --------------------------------------------------------------------------- #


def _settle(cfg, transport, answers, tmp_path, tty=True):
    import json as _json

    from voice_bridge.setup import settle_pins

    path = tmp_path / "voice-bridge.json"
    if not path.exists():
        path.write_text("{}", encoding="utf-8")
    out: list[str] = []
    it = iter(answers)
    n = settle_pins(
        cfg,
        transport,
        config_path=path,
        ask=lambda _p: next(it),
        show=out.append,
        is_tty=lambda: tty,
    )
    return n, "\n".join(out), _json.loads(path.read_text(encoding="utf-8"))


def test_setup_pins_an_unambiguous_list_without_asking(sample_config, tmp_path):
    """Nothing to ask, so it must not ask — but it MUST still record the id.

    Pinning at setup is what stops the system guessing later: once a same-named
    ghost appears there is no way to tell which list was originally meant.
    """
    t = FakeTransport()
    t.add_list("Vox-Message-Outbox", "A1")
    t.add_list("Vox-Message-Inbox", "B1")

    n, out, written = _settle(sample_config, t, [], tmp_path)  # no answers available

    assert n == 2, "asking nothing is not the same as doing nothing"
    assert written["inbox_list_id"] == "A1"
    assert written["output_list_id"] == "B1"
    assert out == "", "silence is the correct interaction here"


def test_setup_asks_when_a_name_is_ambiguous(sample_config, ghost_transport, tmp_path):
    n, out, written = _settle(sample_config, ghost_transport, ["2"], tmp_path)
    assert written["inbox_list_id"] == "L9", "the human's pick, not a guess"
    assert written["output_list_id"] == "L2", "the unambiguous one is pinned silently"
    assert n == 2
    assert "1)" in out and "2)" in out


def test_setup_never_prompts_without_a_terminal(sample_config, ghost_transport, tmp_path):
    """The isatty/EOF doctrine: a prompt to a pipe blocks for ever, or reads EOF
    and takes an answer nobody gave. Warn loudly and name the fix instead."""
    n, out, written = _settle(sample_config, ghost_transport, [], tmp_path, tty=False)

    assert "inbox_list_id" not in written, "it must not guess between the ghosts"
    assert "lists --pin" in out, "a warning that names no remedy is just noise"
    assert "warning" in out.lower()
    assert written["output_list_id"] == "L2", "the unambiguous role is still pinned"
    assert n == 1


def test_declining_at_setup_leaves_it_unpinned_and_says_so(
    sample_config, ghost_transport, tmp_path
):
    n, out, written = _settle(sample_config, ghost_transport, ["q"], tmp_path)
    assert "inbox_list_id" not in written
    assert "still ambiguous" in out
    assert n == 1, "the outbox is unambiguous and still gets pinned"


def test_setup_does_not_overwrite_a_pin_you_already_chose(sample_config, ghost_transport, tmp_path):
    """Re-running setup must not silently move a deliberate pin."""
    cfg = _cfg(sample_config, inbox_list_id="L1")
    n, _, written = _settle(cfg, ghost_transport, [], tmp_path)
    assert "inbox_list_id" not in written, "an existing pin is left exactly as it was"
    assert n == 1
