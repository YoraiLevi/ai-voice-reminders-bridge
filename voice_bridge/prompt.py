"""Render the phone-side prompt from its packaged template.

The template used to be rendered by blind `str.replace` inside the CLI handler,
which failed three ways (VOX-1/2/3):

* the maintainer's HTML comment was printed to the user *and* had its example
  tokens rewritten, so a note explaining the defaults became a confident, false
  claim that one list name equalled another;
* a placeholder the config could not fill shipped to the phone **literally**,
  with a success exit code — the model then reads `${something}` as if it were a
  list name, and a broken prompt is indistinguishable from a working one;
* a missing template file produced a traceback rather than an explanation.

`string.Template.substitute` fixes the second by construction: unlike
`safe_substitute` (or `str.replace`), it *raises* on anything it cannot fill, so
an unrenderable prompt can never be printed as if it were fine.
"""

from __future__ import annotations

from importlib.resources import files
from string import Template

from .config import Config
from .errors import CommandError

_COMMENT_START = "<!--"
_COMMENT_END = "-->"


def _load_template() -> str:
    """The packaged template text. Separate function so tests can replace it."""
    return files("voice_bridge").joinpath("prompts/vox.md").read_text(encoding="utf-8")


def _strip_comments(text: str) -> str:
    """Remove HTML comments — they are notes to maintainers, not phone instructions."""
    out = []
    rest = text
    while True:
        start = rest.find(_COMMENT_START)
        if start == -1:
            out.append(rest)
            break
        end = rest.find(_COMMENT_END, start)
        if end == -1:  # unterminated: keep the remainder rather than silently dropping it
            out.append(rest)
            break
        out.append(rest[:start])
        rest = rest[end + len(_COMMENT_END) :]
    return "".join(out).lstrip("\n")


def render_vox_prompt(cfg: Config) -> str:
    """The prompt to paste into the phone, rendered for this configuration.

    Raises `CommandError(2)` if the template is missing, and `KeyError` if it
    contains a token this version cannot fill — loudly, rather than shipping the
    token verbatim to the phone.
    """
    try:
        raw = _load_template()
    except (FileNotFoundError, ModuleNotFoundError, OSError) as exc:
        raise CommandError(
            2,
            "could not read the vox prompt template (prompts/vox.md) — "
            f"the installation looks incomplete: {exc}",
        ) from exc

    rendered = Template(_strip_comments(raw)).substitute(
        inbox_list=cfg.inbox_list,
        output_list=cfg.output_list,
    )
    return rendered + _selected_ids(cfg)


def _selected_ids(cfg: Config) -> str:
    """State the list identifiers when the config pins them, otherwise say nothing.

    Requested by the phone persona itself, which was having to guess which list it
    meant. A real account can hold dozens of lists including same-named ghosts, so
    matching by name is a coin flip that silently sends dictations to a list nobody
    reads — and the config already knows the answer.

    Nothing is emitted when the pins are absent: an identifier line that is not
    authoritative is worse than no line at all, because it invites the same
    guessing while looking like fact.
    """
    if not (cfg.inbox_list_id or cfg.output_list_id):
        return ""

    lines = [
        "",
        "AUTHORITATIVE LIST IDENTIFIERS — use these exactly; do NOT match by name,",
        "because several lists may share a title and only these ids are unambiguous.",
    ]
    if cfg.inbox_list_id:
        lines.append(f'  "{cfg.inbox_list}" (you dictate here) = {cfg.inbox_list_id}')
    if cfg.output_list_id:
        lines.append(f'  "{cfg.output_list}" (answers arrive here) = {cfg.output_list_id}')
    return "\n".join(lines) + "\n"
