"""Batch 18: every piece of prompt prose is a template, and they all render one way.

Ruled by the human. `vox.md` and `peer.md` were already templates; the two list-identifier
sections were Python string literals assembled with f-strings inside `_selected_ids`. Now
they are `prompts/ids-radicale.md` and `prompts/ids-icloud.md`, loaded through the same
`_render` path - strip maintainer comments, then `string.Template.substitute` (strict).

**Which fragment applies is logic and stays in code. What it says is prose and lives in a
file.** That split is the whole point: the conditional is a branch on `cfg.transport`,
which belongs where branches belong, while the text can be edited, diffed and reviewed
without opening a `.py`.

What these tests are really guarding is that the doctrine went WITH the text. VOX-2 and
VOX-3 were bought on `vox.md` alone; a fragment that loaded leniently, or blew up with a
traceback when its file was missing, would reintroduce both one file over - and it would
do it in the section that tells the phone where to put dictations.

Byte-identity of the rendered output for both transports is proved separately, by
rendering from fixed synthetic configs at this ref and at `b882ef3` and diffing - the same
instrument used for 17b, because a claim that a refactor changed nothing is exactly the
kind that should be measured rather than reasoned about.
"""

from __future__ import annotations

import json

import pytest

from voice_bridge import prompt
from voice_bridge.config import load_config
from voice_bridge.errors import CommandError


def _cfg(tmp_path, tmp_mailbox, **over):
    cfg_file = tmp_path / "voice-bridge.json"
    cfg_file.write_text(
        json.dumps(
            {
                "mailbox_dir": str(tmp_mailbox),
                "state_dir": str(tmp_path / "state"),
                "inbox_list": "Vox-Message-Outbox",
                "output_list": "Vox-Message-Inbox",
                "inbox_list_id": "CK-INBOX-GUID",
                "output_list_id": "CK-OUTBOX-GUID",
                **over,
            }
        ),
        encoding="utf-8",
    )
    return load_config(cfg_file)


def _radicale(tmp_path, tmp_mailbox):
    return _cfg(
        tmp_path,
        tmp_mailbox,
        transport="radicale",
        radicale_user="vox",
        inbox_list_id="http://127.0.0.1:5232/vox/vox-message-outbox/",
        output_list_id="http://127.0.0.1:5232/vox/vox-message-inbox/",
    )


@pytest.mark.parametrize("name", ["vox.md", "peer.md", "ids-radicale.md", "ids-icloud.md"])
def test_every_prompt_fragment_is_packaged(name):
    """The wheel ships `voice_bridge/` wholesale, so a new template is included by
    accident rather than by decision - which is fine until someone changes the build.
    Naming the files here means a packaging change fails in the suite instead of on a
    stranger's machine, where the symptom is a prompt command that exits 2."""
    assert prompt._load_template(name).strip(), f"prompts/{name} is missing or empty"


@pytest.mark.parametrize("transport", ["icloud", "radicale"])
def test_both_transports_render_with_nothing_left_unfilled(transport, tmp_path, tmp_mailbox):
    """The one property that must hold for every fragment on every transport. A literal
    `${...}` reaching the phone is read as a list name (VOX-2), and the ids section is the
    worst place in the prompt for that to happen."""
    cfg = (
        _radicale(tmp_path, tmp_mailbox) if transport == "radicale" else _cfg(tmp_path, tmp_mailbox)
    )

    out = prompt.render_vox_prompt(cfg)

    assert "${" not in out
    assert "$inbox_list" not in out and "$radicale_user" not in out


@pytest.mark.parametrize("missing", ["ids-icloud.md", "ids-radicale.md"])
def test_a_missing_fragment_explains_itself_and_exits_2(
    missing, tmp_path, tmp_mailbox, monkeypatch
):
    """VOX-3 was bought on `vox.md`. Moving text into new files would have re-bought it
    with a traceback if the fragments loaded on their own path - and a traceback from a
    packaging fault tells the user nothing they can act on."""
    real = prompt._load_template

    def fake(name: str = "vox.md") -> str:
        if name == missing:
            raise FileNotFoundError(f"prompts/{missing}")
        return real(name)

    monkeypatch.setattr(prompt, "_load_template", fake)
    cfg = _radicale(tmp_path, tmp_mailbox) if "radicale" in missing else _cfg(tmp_path, tmp_mailbox)

    with pytest.raises(CommandError) as err:
        prompt.render_vox_prompt(cfg)

    assert err.value.code == 2
    assert missing in err.value.msg


def test_a_fragment_is_as_strict_about_unknown_tokens_as_the_main_template(
    tmp_path, tmp_mailbox, monkeypatch
):
    """`safe_substitute` in a fragment would fail exactly where it hurts most: the section
    naming the lists. Strictness is a property of the shared render path, so it cannot be
    forgotten by whoever adds the next template."""
    real = prompt._load_template
    monkeypatch.setattr(
        prompt,
        "_load_template",
        lambda name="vox.md": (
            "ids for ${a_field_nobody_supplies}" if name == "ids-icloud.md" else real(name)
        ),
    )

    with pytest.raises(KeyError):
        prompt.render_vox_prompt(_cfg(tmp_path, tmp_mailbox))


def test_a_fragment_gets_its_maintainer_comments_stripped_too(tmp_path, tmp_mailbox, monkeypatch):
    """VOX-1's other half. A note left in a fragment must not reach the phone, and it must
    not have its example tokens rewritten into a confident false claim on the way."""
    real = prompt._load_template
    monkeypatch.setattr(
        prompt,
        "_load_template",
        lambda name="vox.md": (
            "<!-- note to us: $inbox_list is an example -->KEPT LINE\n"
            if name == "ids-icloud.md"
            else real(name)
        ),
    )

    out = prompt.render_vox_prompt(_cfg(tmp_path, tmp_mailbox))

    assert "note to us" not in out
    assert "KEPT LINE" in out


def test_the_blank_line_before_the_section_survives_a_trimmed_template(
    tmp_path, tmp_mailbox, monkeypatch
):
    """The separator is a JOIN, not text. Leading it inside the file would make the
    template's first line invisible to whoever edits it, and any editor that trims leading
    whitespace would silently reflow the prompt. It is added by the joiner instead."""
    real = prompt._load_template
    monkeypatch.setattr(
        prompt,
        "_load_template",
        lambda name="vox.md": (
            "FIRST LINE OF THE SECTION\n" if name == "ids-icloud.md" else real(name)
        ),
    )

    out = prompt.render_vox_prompt(_cfg(tmp_path, tmp_mailbox))

    assert "\n\nFIRST LINE OF THE SECTION\n" in out


def test_the_selection_is_still_a_branch_on_transport(tmp_path, tmp_mailbox):
    """The conditional stayed in code, which is the half of the ruling that is easy to
    overshoot: a template system tempts you to push the CHOICE into the templates too, and
    then the answer to "which text does radicale get?" is no longer readable in one place.
    """
    icloud = prompt.render_vox_prompt(_cfg(tmp_path, tmp_mailbox))
    radicale = prompt.render_vox_prompt(_radicale(tmp_path, tmp_mailbox))

    assert "AUTHORITATIVE LIST IDENTIFIERS" in icloud and "PIN THEM ONCE" not in icloud
    assert "PIN THEM ONCE" in radicale and "AUTHORITATIVE LIST IDENTIFIERS" not in radicale
