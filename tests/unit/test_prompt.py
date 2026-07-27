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
