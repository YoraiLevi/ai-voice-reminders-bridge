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

from voice_bridge.selection import (
    Choice,
    Resolution,
    pick,
    resolve_selection,
)
from voice_bridge.transport import FakeTransport, ListRef


@pytest.fixture
def sample_config(unselected_config):
    """This whole suite is about CHOOSING, so it starts from nothing chosen.

    The shared `sample_config` now ships with both roles selected, because both are
    a hard requirement everywhere else and an unselected config is a specific
    broken state rather than a normal one. Overriding it once here says that this
    module means the other thing, instead of repeating the swap in fourteen
    signatures.
    """
    return unselected_config


def _cfg(sample_config, **kw):
    return dataclasses.replace(sample_config, **kw)


def _by_role(plan, role: str) -> Resolution:
    return next(r for r in plan.resolutions if r.role == role)


# --------------------------------------------------------------------------- #
# resolve_selection — the single engine
# --------------------------------------------------------------------------- #


def test_an_id_that_exists_is_selected(sample_config, ghost_transport):
    """The only way a list is ever used: someone chose its id."""
    inbox = _by_role(
        resolve_selection(_cfg(sample_config, inbox_list_id="L9"), ghost_transport), "inbox"
    )
    assert inbox.status == "selected"
    assert inbox.chosen == "L9"
    assert inbox.current == "L9"


def test_existing_lists_are_never_auto_selected(sample_config, ghost_transport):
    """The load-bearing negative test.

    Name matching is gone, so nothing is chosen on the user's behalf — not even
    when exactly one list carries the configured name, and not when one candidate
    is the obvious-looking pick. Every automatic choice was invisible and
    permanent, which is the failure selecting exists to remove.
    """
    live = [r for r in ghost_transport.list_todo_lists() if r.id == "L9"][0]
    for i in range(40):  # the most tempting heuristic there is: "use the full one"
        ghost_transport.add_todo(live, f"item {i}")

    inbox = _by_role(resolve_selection(sample_config, ghost_transport), "inbox")

    assert inbox.status == "unselected"
    assert inbox.chosen == "", "an unselected role must not smuggle in a pick"


def test_a_lone_exact_name_match_is_still_not_selected(sample_config):
    """The case that used to auto-resolve, and the one the ruling targeted."""
    t = FakeTransport()
    t.add_list("Vox-Message-Outbox", "A1")
    t.add_list("Vox-Message-Inbox", "B1")

    plan = resolve_selection(sample_config, t)

    assert _by_role(plan, "inbox").status == "unselected"
    assert _by_role(plan, "outbox").status == "unselected"
    assert plan.selected == []


def test_a_selection_pointing_at_nothing_is_stale(sample_config, ghost_transport):
    """Distinct from unselected, because the remedies differ: a stale id must be
    cleared or replaced, where an unselected role has never been answered."""
    inbox = _by_role(
        resolve_selection(_cfg(sample_config, inbox_list_id="GONE"), ghost_transport), "inbox"
    )
    assert inbox.status == "stale"
    assert inbox.current == "GONE"
    assert inbox.chosen == ""


def test_the_candidates_are_the_whole_inventory(sample_config, ghost_transport):
    """Not a name-filtered shortlist. Choosing by id means every list is offerable,
    and it retires the case-twin special case — twins are just two ordinary rows.
    """
    inbox = _by_role(resolve_selection(sample_config, ghost_transport), "inbox")
    assert {c.id for c in inbox.candidates} == {"L1", "L2", "L9"}


def test_case_twins_need_no_special_handling(sample_config):
    """They were a hazard only while names decided anything."""
    t = FakeTransport()
    t.add_list("Vox-Message-Outbox", "C1")
    t.add_list("vox-message-outbox", "C2")

    inbox = _by_role(resolve_selection(sample_config, t), "inbox")
    assert inbox.status == "unselected"
    assert {c.id for c in inbox.candidates} == {"C1", "C2"}


def test_an_empty_account_has_nothing_to_offer(sample_config):
    inbox = _by_role(resolve_selection(sample_config, FakeTransport()), "inbox")
    assert inbox.status == "unselected"
    assert inbox.candidates == []


def test_both_roles_are_resolved_independently(sample_config, ghost_transport):
    plan = resolve_selection(_cfg(sample_config, inbox_list_id="L9"), ghost_transport)
    assert _by_role(plan, "inbox").status == "selected"
    assert _by_role(plan, "outbox").status == "unselected"


def test_each_resolution_names_the_config_field_to_write(sample_config, ghost_transport):
    """Callers must never map role->field themselves; that mapping is the crossing
    that UX-1 already proved easy to get backwards."""
    plan = resolve_selection(sample_config, ghost_transport)
    assert _by_role(plan, "inbox").field == "inbox_list_id"
    assert _by_role(plan, "outbox").field == "output_list_id"


def test_a_caller_can_reclassify_against_refs_it_already_has(sample_config, ghost_transport):
    """The interactive menu refreshes this way — no second call, and no second
    copy of the classification rules."""
    refs = ghost_transport.list_todo_lists()
    plan = resolve_selection(_cfg(sample_config, inbox_list_id="L1"), ghost_transport, refs=refs)
    assert _by_role(plan, "inbox").status == "selected"


# --------------------------------------------------------------------------- #
# pick — the ONE picker component
# --------------------------------------------------------------------------- #


def _resolution(status=None, current="", candidates=None) -> Resolution:
    """A resolution for driving the picker.

    The status DEFAULTS from `current`, because the two are not independent: an
    id that is set and valid is "selected", and no id is "unselected". Letting
    them drift apart in a fixture would let a test assert behaviour the real
    resolver can never produce.
    """
    return Resolution(
        role="inbox",
        name="Vox-Message-Outbox",
        field="inbox_list_id",
        current=current,
        status=status or ("selected" if current else "unselected"),
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


def test_choosing_a_number_selects_that_id():
    choice, _ = _run(_resolution(), ["2"])
    assert choice.action == "select"
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


def test_keep_and_clear_are_hidden_when_there_is_no_selection():
    """Offering to clear nothing is noise on a fresh machine."""
    _, shown = _run(_resolution(current=""), ["1"])
    lowered = shown.lower()
    assert "keep" not in lowered
    assert "clear" not in lowered


def test_keep_and_clear_appear_once_a_selection_exists():
    _, shown = _run(_resolution(current="L1"), ["k"])
    lowered = shown.lower()
    assert "keep" in lowered and "clear" in lowered


def test_the_keep_label_says_what_choosing_it_does():
    """A bare "k) keep" left a real user unsure whether it meant keep-and-stop or
    keep-and-carry-on, so the label now states the consequence."""
    _, shown = _run(_resolution(current="L1"), ["k"])
    assert "keep this selection as it is" in shown


def test_the_skip_label_does_not_claim_to_quit():
    """It advances rather than exiting; "quit" described neither (ruled live)."""
    _, shown = _run(_resolution(current="L1"), ["k"])
    assert "s) skip" in shown
    assert "quit" not in shown.lower()


def test_the_current_selection_is_marked_in_the_rows():
    _, shown = _run(_resolution(current="L9"), ["k"])
    marked = [ln for ln in shown.splitlines() if "L9" in ln or "12" in ln]
    assert any("current" in ln.lower() or "←" in ln or "<-" in ln for ln in marked)


def test_keep_changes_nothing():
    choice, _ = _run(_resolution(current="L1"), ["k"])
    assert choice.action == "keep"
    assert choice.list_id == ""


def test_clear_is_available_and_distinct_from_skipping():
    """`clear` means "forget the selection"; `skip` means "leave this one alone".
    Collapsing them would make moving on look like an edit."""
    assert _run(_resolution(current="L1"), ["c"])[0].action == "clear"
    assert _run(_resolution(current="L1"), ["s"])[0].action == "skip"
    # `q` was the key before this was relabelled; it still works, undocumented,
    # so an existing habit does not silently do something else.
    assert _run(_resolution(current="L1"), ["q"])[0].action == "skip"


def test_the_header_counts_the_rows_it_is_showing():
    """The header cannot disagree with the list underneath it."""
    three = [
        ListRef(name="Vox-Message-Outbox", id="L1", count=1),
        ListRef(name="Vox-Message-Outbox", id="L2", count=2),
        ListRef(name="vox-message-outbox", id="L3", count=3),
    ]
    _, shown = _run(_resolution(candidates=three), ["1"])
    assert "3" in shown.splitlines()[0]


def test_case_twins_are_shown_with_their_exact_names():
    """Twins needed a special note only while names decided something. Now they are
    two ordinary rows — but the exact case must still be printed, because with the
    id abbreviated it is all that tells them apart on screen."""
    twins = [
        ListRef(name="Vox-Message-Outbox", id="L1"),
        ListRef(name="vox-message-outbox", id="L2"),
    ]
    _, shown = _run(_resolution(candidates=twins), ["1"])
    assert "Vox-Message-Outbox" in shown
    assert "vox-message-outbox" in shown


@pytest.mark.parametrize("bad", ["", "0", "9", "banana", "-1"])
def test_unusable_answers_re_ask_rather_than_guessing(bad):
    """An out-of-range number must never fall through to a default pick — that is
    the silent wrong choice arriving by another door."""
    choice, shown = _run(_resolution(), [bad, "1"])
    assert choice.action == "select"
    assert choice.list_id == "L1"
    assert "?" in shown or "choose" in shown.lower()


def test_end_of_input_skips_instead_of_hanging_or_guessing():
    """isatty can lie (MSYS, Git Bash). If the stream ends, treat it as "no answer"
    and change nothing — never block, never invent a pick."""

    def _eof(_prompt):
        raise EOFError

    assert pick(_resolution(), ask=_eof, show=lambda _s: None).action == "skip"


# --------------------------------------------------------------------------- #
# lists --select — the management door
# --------------------------------------------------------------------------- #


def _select_run(cfg, transport, answers, tmp_path, tty=True):
    """Drive select_command with scripted answers.

    The flow is menu-driven: answers are (role choice, picker answer, …, "d").
    """
    import json as _json

    from voice_bridge.commands import select_command

    path = tmp_path / "voice-bridge.json"
    if not path.exists():
        path.write_text("{}", encoding="utf-8")
    out: list[str] = []
    it = iter(answers)
    code = select_command(
        cfg,
        transport,
        config_path=path,
        ask=lambda _p: next(it),
        show=out.append,
        is_tty=lambda: tty,
    )
    written = _json.loads(path.read_text(encoding="utf-8"))
    return code, "\n".join(out), written


def test_selecting_writes_the_chosen_id_to_the_right_field(
    sample_config, ghost_transport, tmp_path
):
    """The role->field crossing is the one UX-1 proved easy to invert."""
    # The picker now offers the FULL inventory (L1, L2, L9), so row 3 is L9 —
    # choosing a list is no longer confined to those sharing the configured name.
    code, out, written = _select_run(sample_config, ghost_transport, ["1", "3", "d"], tmp_path)
    assert code == 0
    assert written["inbox_list_id"] == "L9"
    # The confirmation echoes what was chosen — name and id — because a numbered
    # menu tells you which ROW you picked, not which list you now own.
    assert "L9" in out
    assert written["inbox_list"] == "Vox-Message-Outbox", "the NAME travels with the id"


def test_clearing_writes_an_empty_selection(sample_config, ghost_transport, tmp_path):
    cfg = _cfg(sample_config, inbox_list_id="L1")
    _, out, written = _select_run(cfg, ghost_transport, ["1", "c", "d"], tmp_path)
    assert written["inbox_list_id"] == ""
    assert "cleared" in out


def test_quitting_writes_nothing(sample_config, ghost_transport, tmp_path):
    cfg = _cfg(sample_config, inbox_list_id="L1")
    _, out, written = _select_run(cfg, ghost_transport, ["1", "s", "d"], tmp_path)
    assert "inbox_list_id" not in written
    assert "unchanged" in out


def test_a_change_reminds_you_to_re_copy_the_phone_prompt(sample_config, ghost_transport, tmp_path):
    """The pins are baked into the rendered prompt, so a changed pin the phone
    never receives is a bridge that silently talks to the wrong list."""
    _, out, _ = _select_run(sample_config, ghost_transport, ["1", "1", "d"], tmp_path)
    assert "vox-prompt" in out


def test_an_unchanged_run_does_not_nag_about_the_prompt(sample_config, ghost_transport, tmp_path):
    _, out, _ = _select_run(sample_config, ghost_transport, ["d"], tmp_path)
    assert "vox-prompt" not in out


def test_a_stale_selection_is_explained_before_the_picker(sample_config, ghost_transport, tmp_path):
    cfg = _cfg(sample_config, inbox_list_id="GONE")
    _, out, written = _select_run(cfg, ghost_transport, ["1", "1", "d"], tmp_path)
    assert "no longer exists" in out
    assert written["inbox_list_id"] == "L1", "a re-pick must replace the dead id"


def test_a_missing_list_sends_you_to_setup_not_an_empty_menu(sample_config, tmp_path):
    _, out, written = _select_run(sample_config, FakeTransport(), ["1", "d"], tmp_path)
    assert "setup" in out
    assert "inbox_list_id" not in written, "nothing to choose means nothing to write"


def test_non_interactive_refuses_rather_than_hanging(sample_config, ghost_transport, tmp_path):
    """A prompt written to a pipe blocks for ever, or reads EOF and takes an answer
    nobody gave. Both are worse than exiting with instructions."""
    code, out, written = _select_run(sample_config, ghost_transport, [], tmp_path, tty=False)
    assert code == 2
    assert "config set inbox_list_id" in out, "it must name the non-interactive way through"
    assert "inbox_list_id" not in written


# --------------------------------------------------------------------------- #
# setup's door — ask on first run; never infer from a name
# --------------------------------------------------------------------------- #


def _settle(cfg, transport, answers, tmp_path, tty=True, created=None):
    import json as _json

    from voice_bridge.setup import settle_selection

    path = tmp_path / "voice-bridge.json"
    if not path.exists():
        path.write_text("{}", encoding="utf-8")
    out: list[str] = []
    it = iter(answers)
    n = settle_selection(
        cfg,
        transport,
        config_path=path,
        created=created,
        ask=lambda _p: next(it),
        show=out.append,
        is_tty=lambda: tty,
    )
    return n, "\n".join(out), _json.loads(path.read_text(encoding="utf-8"))


def test_setup_asks_even_when_one_list_matches_the_name(sample_config, tmp_path):
    """The exact path the ruling removed.

    A single exact name match used to be recorded silently. It is now a question,
    because a matching title was never evidence of intent — only the cheapest
    available guess.
    """
    t = FakeTransport()
    t.add_list("Vox-Message-Outbox", "A1")
    t.add_list("Vox-Message-Inbox", "B1")

    n, out, written = _settle(sample_config, t, ["1", "2"], tmp_path)

    assert n == 2
    assert written["inbox_list_id"] == "A1"
    assert written["output_list_id"] == "B1"
    assert "1)" in out, "it must have shown a picker rather than deciding"


def test_setup_selects_a_list_it_just_created_without_asking(sample_config, tmp_path):
    """Not an inference: we hold the id `create_list` returned moments earlier.

    Only Radicale can create lists; on iCloud they are made by hand, so nothing
    reaches this branch there.
    """
    t = FakeTransport()
    t.add_list("Vox-Message-Outbox", "NEW1")
    t.add_list("Vox-Message-Inbox", "NEW2")

    n, out, written = _settle(
        sample_config,
        t,
        [],
        tmp_path,
        created={"inbox_list_id": "NEW1", "output_list_id": "NEW2"},
    )

    assert n == 2
    assert written["inbox_list_id"] == "NEW1"
    assert written["output_list_id"] == "NEW2"
    assert "just created" in out


def test_setup_never_prompts_without_a_terminal(sample_config, ghost_transport, tmp_path):
    """The isatty/EOF doctrine: a prompt to a pipe blocks for ever, or reads EOF
    and takes an answer nobody gave. Warn loudly and name the fix instead."""
    n, out, written = _settle(sample_config, ghost_transport, [], tmp_path, tty=False)

    assert n == 0, "it must not choose on the user's behalf"
    assert "inbox_list_id" not in written
    assert "lists --select" in out, "a warning that names no remedy is just noise"
    assert "warning" in out.lower()


def test_declining_at_setup_leaves_it_unselected(sample_config, ghost_transport, tmp_path):
    n, out, written = _settle(sample_config, ghost_transport, ["s", "s"], tmp_path)
    assert "inbox_list_id" not in written
    assert n == 0


def test_setup_does_not_overwrite_a_selection_you_already_chose(
    sample_config, ghost_transport, tmp_path
):
    """Re-running setup must not silently move a deliberate choice."""
    cfg = _cfg(sample_config, inbox_list_id="L1")
    n, _, written = _settle(cfg, ghost_transport, ["s"], tmp_path)
    assert "inbox_list_id" not in written, "an existing selection is left exactly as it was"
    assert n == 0


def test_setup_offers_a_replacement_when_the_selection_went_stale(
    sample_config, ghost_transport, tmp_path
):
    cfg = _cfg(sample_config, inbox_list_id="GONE")
    n, out, written = _settle(cfg, ghost_transport, ["1", "s"], tmp_path)
    assert "no longer exists" in out
    assert written["inbox_list_id"] == "L1"
    assert n == 1


# --------------------------------------------------------------------------- #
# refresh — the action that makes this usable while sync catches up
# --------------------------------------------------------------------------- #


def test_refresh_picks_up_a_list_that_only_just_synced():
    """The flow the whole feature exists for.

    iCloud cannot create lists over the API, so the user makes one in Reminders
    *while this prompt is open* — and sync takes its time. Without refresh they
    would have to abandon setup, wait an unknown while, and start again.
    """
    t = FakeTransport()
    t.add_list("Old List", "OLD")
    t.add_unsynced_list("Vox-Message-Outbox", "NEW")  # created on the phone, not synced

    resolution = Resolution(
        role="inbox",
        name="Vox-Message-Outbox",
        field="inbox_list_id",
        current="",
        status="unselected",
        candidates=t.list_todo_lists(),
        chosen="",
    )

    answers = iter(["r", "2"])  # refresh, then choose the list that appeared
    shown: list[str] = []

    def _ask(_prompt):
        answer = next(answers)
        if answer == "r":
            t.reveal()  # sync catches up between the keystroke and the re-read
        return answer

    choice = pick(resolution, ask=_ask, show=shown.append, refresh=t.list_todo_lists)

    assert choice.action == "select"
    assert choice.list_id == "NEW", "the newly synced list must be choosable"
    assert "r) refresh" in "\n".join(shown)


def test_refresh_is_offered_even_when_the_account_looks_empty():
    """The first-run case: nothing exists yet, so a picker with no rows and no way
    to re-read would be a dead end."""
    resolution = Resolution(
        role="inbox",
        name="Vox-Message-Outbox",
        field="inbox_list_id",
        current="",
        status="unselected",
        candidates=[],
        chosen="",
    )
    shown: list[str] = []
    choice = pick(resolution, ask=lambda _p: "s", show=shown.append, refresh=lambda: [])
    joined = "\n".join(shown)

    assert choice.action == "skip"
    assert "r) refresh" in joined
    assert "no lists yet" in joined


def test_the_confirmation_echoes_name_and_id():
    """A numbered menu tells you which ROW you picked, not which list you now own."""
    from voice_bridge.selection import confirm_selection

    shown: list[str] = []
    confirm_selection(
        ListRef(name="Vox-Message-Outbox", id="List/4694", count=3, color="blue"),
        role="inbox",
        show=shown.append,
    )
    line = "\n".join(shown)
    assert "Vox-Message-Outbox" in line and "List/4694" in line
    assert "dictate into" in line


def test_keep_is_not_offered_for_a_selection_that_no_longer_exists():
    """Found by reading the picker's own output on a stale role.

    "Keep this selection as it is" for a list that has been deleted offers to
    keep something broken. Clearing it still makes sense — that is the repair —
    so `c` stays and only `k` goes.
    """
    stale = Resolution(
        role="inbox",
        name="Vox-Message-Outbox",
        field="inbox_list_id",
        current="List/GONE",
        status="stale",
        candidates=[ListRef(name="Something", id="L1")],
        chosen="",
    )
    _, shown = _run(stale, ["s"])

    assert "keep this selection" not in shown
    assert "c) clear this selection" in shown, "clearing a dead id is the repair"


def test_keep_is_offered_for_a_live_selection():
    live = Resolution(
        role="inbox",
        name="Vox-Message-Outbox",
        field="inbox_list_id",
        current="L1",
        status="selected",
        candidates=[ListRef(name="Something", id="L1")],
        chosen="L1",
    )
    _, shown = _run(live, ["s"])
    assert "k) keep this selection as it is" in shown
