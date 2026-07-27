"""prompt: strict rendering of the phone-side template.

The template was rendered by blind `str.replace` in the CLI handler, which
produced three distinct failures (VOX-1/2/3):

* the maintainer's HTML comment was printed to the user, *and* the example
  tokens inside it were rewritten — turning "`${inbox_list}` = Vox-Message-Inbox"
  into a confident, false mapping of one list name onto another;
* a token the config did not supply would ship to the phone literally, with a
  success exit code, so a broken prompt looked like a working one;
* a missing template file raised a traceback instead of an explanation.
"""

from __future__ import annotations

import pytest

from voice_bridge import prompt


def test_render_substitutes_both_list_names(sample_config):
    out = prompt.render_vox_prompt(sample_config)
    assert sample_config.inbox_list in out
    assert sample_config.output_list in out


def test_render_strips_the_maintainer_comment(sample_config):
    """The comment is a note to us, not instructions for the phone."""
    out = prompt.render_vox_prompt(sample_config)
    assert "<!--" not in out
    assert "-->" not in out
    assert "Defaults shown if unrendered" not in out


def test_render_leaves_no_unsubstituted_tokens(sample_config):
    out = prompt.render_vox_prompt(sample_config)
    assert "${" not in out


def test_render_is_strict_about_unknown_tokens(sample_config, monkeypatch):
    """VOX-2: a placeholder we cannot fill must fail loudly, not ship literally.

    Shipping `${future_field}` to the phone with exit 0 hands the user a prompt
    that is quietly wrong — the model reads a literal token as if it were a list
    name.
    """
    monkeypatch.setattr(prompt, "_load_template", lambda: "hello ${future_field}")
    with pytest.raises(KeyError):
        prompt.render_vox_prompt(sample_config)


def test_missing_template_raises_command_error_not_traceback(sample_config, monkeypatch):
    """VOX-3: a packaging fault should explain itself and exit 2."""

    def boom():
        raise FileNotFoundError("prompts/vox.md")

    monkeypatch.setattr(prompt, "_load_template", boom)
    with pytest.raises(prompt.CommandError) as err:
        prompt.render_vox_prompt(sample_config)
    assert err.value.code == 2
    assert "vox.md" in err.value.msg or "prompt" in err.value.msg.lower()


def test_rendered_prompt_keeps_the_instructions(sample_config):
    """Guard against over-eager stripping removing real content."""
    out = prompt.render_vox_prompt(sample_config)
    assert "VOX" in out
    assert len(out.splitlines()) > 20
    assert out.strip().startswith("You are VOX")


# --------------------------------------------------------------------------- #
# UX-5 — give the phone the ids, so it never has to guess
# --------------------------------------------------------------------------- #


def test_pinned_ids_are_carried_into_the_prompt(sample_config):
    """Filed by the phone persona itself: it was guessing which list to use.

    A real account here holds 21 lists including same-named ghosts, so matching
    by name is a coin flip that silently sends dictations somewhere nobody reads.
    When ids are pinned, the prompt states them — the phone should not have to
    infer what the config already knows.
    """
    import dataclasses

    cfg = dataclasses.replace(
        sample_config, inbox_list_id="4694399A-AAAA", output_list_id="3D75266C-BBBB"
    )
    out = prompt.render_vox_prompt(cfg)
    assert "4694399A-AAAA" in out
    assert "3D75266C-BBBB" in out


def test_unpinned_config_says_nothing_about_ids(sample_config):
    """No pins, no claim — an id line that isn't authoritative is worse than none."""
    out = prompt.render_vox_prompt(sample_config)
    assert "identifier" not in out.lower()


def test_an_unterminated_comment_keeps_the_remaining_text(sample_config, monkeypatch):
    """A malformed template must not silently swallow the prompt.

    Dropping everything after a stray `<!--` would ship a truncated prompt that
    still looks like a prompt — the quiet failure this module exists to avoid.
    """
    monkeypatch.setattr(
        prompt, "_load_template", lambda: "<!-- never closed\nthe actual ${inbox_list} body"
    )
    out = prompt.render_vox_prompt(sample_config)
    assert "the actual" in out and sample_config.inbox_list in out
