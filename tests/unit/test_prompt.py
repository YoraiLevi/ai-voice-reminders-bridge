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


def _stub_template(monkeypatch, body, *, only: str = "vox.md") -> None:
    """Replace ONE template, leaving every other one real.

    `_load_template` used to be called bare for `vox.md`, so a zero-argument lambda was
    a faithful stub. It is now the loader for every prompt fragment - the ids sections
    included - and a stub that answers the same body to every name is a fake of a seam
    that no longer exists: it would hand the ids section the vox body and pass while
    testing nothing. Naming the target and delegating the rest keeps these tests about
    the one failure each is named for.
    """
    real = prompt._load_template

    def fake(name: str = "vox.md") -> str:
        if name != only:
            return real(name)
        if isinstance(body, BaseException):
            raise body
        return body

    monkeypatch.setattr(prompt, "_load_template", fake)


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
    _stub_template(monkeypatch, "hello ${future_field}")
    with pytest.raises(KeyError):
        prompt.render_vox_prompt(sample_config)


def test_missing_template_raises_command_error_not_traceback(sample_config, monkeypatch):
    """VOX-3: a packaging fault should explain itself and exit 2."""

    _stub_template(monkeypatch, FileNotFoundError("prompts/vox.md"))
    with pytest.raises(prompt.CommandError) as err:
        prompt.render_vox_prompt(sample_config)
    assert err.value.code == 2
    assert "vox.md" in err.value.msg or "prompt" in err.value.msg.lower()


def test_rendered_prompt_keeps_the_instructions(sample_config):
    """Guard against over-eager stripping removing real content."""
    out = prompt.render_vox_prompt(sample_config)
    assert "the VOICE of my agent system" in out
    assert len(out.splitlines()) > 20
    assert out.strip().startswith(f"You are {sample_config.spoke_name}")


# --------------------------------------------------------------------------- #
# UX-5 — give the phone the ids, so it never has to guess
# --------------------------------------------------------------------------- #


def test_selected_ids_are_carried_into_the_prompt(sample_config):
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


def test_an_unselected_role_refuses_to_render_at_all(unselected_config):
    """Stronger than the rule it replaces.

    This used to omit the identifier section when nothing was pinned, which left a
    prompt that still shipped LIST NAMES — and those names were fabricated
    defaults. A live render went out naming one id in its authoritative section and
    a made-up list in its body: the renderer already knew a role was unsettled and
    printed anyway.

    The prompt is instructions to ANOTHER SYSTEM, and no local test can check
    whether the phone obeys them (GAP-3), so a half-true one manufactures agent
    errors we cannot catch. It refuses.
    """
    with pytest.raises(prompt.CommandError) as err:
        prompt.render_vox_prompt(unselected_config)
    assert err.value.code == 2
    assert "lists --select" in err.value.msg


def test_a_rendered_prompt_always_names_BOTH_ids(sample_config):
    """With the refusal above, the identifier section has no partial shape left."""
    out = prompt.render_vox_prompt(sample_config)
    assert sample_config.inbox_list_id in out
    assert sample_config.output_list_id in out


def test_an_unterminated_comment_keeps_the_remaining_text(sample_config, monkeypatch):
    """A malformed template must not silently swallow the prompt.

    Dropping everything after a stray `<!--` would ship a truncated prompt that
    still looks like a prompt — the quiet failure this module exists to avoid.
    """
    _stub_template(monkeypatch, "<!-- never closed\nthe actual ${inbox_list} body")
    out = prompt.render_vox_prompt(sample_config)
    assert "the actual" in out and sample_config.inbox_list in out
