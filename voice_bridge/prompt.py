"""Render the phone-side prompt from its packaged template.

The template used to be rendered by blind `str.replace` inside the CLI handler,
which failed three ways (VOX-1/2/3):

* the maintainer's HTML comment was printed to the user *and* had its example
  tokens rewritten, so a note explaining the defaults became a confident, false
  claim that one list name equalled another;
* a placeholder the config could not fill shipped to the phone **literally**,
  with a success exit code - the model then reads `${something}` as if it were a
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
from .selection import missing_roles, missing_roles_message

_COMMENT_START = "<!--"
_COMMENT_END = "-->"


def _load_template(name: str = "vox.md") -> str:
    """The packaged template text. Separate function so tests can replace it."""
    return files("voice_bridge").joinpath(f"prompts/{name}").read_text(encoding="utf-8")


def _strip_comments(text: str) -> str:
    """Remove HTML comments - they are notes to maintainers, not phone instructions."""
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
    contains a token this version cannot fill - loudly, rather than shipping the
    token verbatim to the phone.
    """
    # REFUSE while either role is unselected. This is not a config file; it is
    # INSTRUCTIONS TO ANOTHER SYSTEM, and the one thing no local test can check is
    # whether the phone obeys them (GAP-3). Shipping a list name nobody selected
    # manufactures exactly the class of agent error we cannot catch: the model
    # dutifully files dictations into a list that is not the one being polled, and
    # every layer reports success.
    #
    # The evidence this is real: the last render that went out named ONE id in its
    # AUTHORITATIVE section and a made-up name in the body. The renderer already
    # knew a role was unsettled, and printed anyway.
    missing = missing_roles(cfg)
    if missing:
        raise CommandError(2, "\n       ".join(missing_roles_message(missing)))

    try:
        raw = _load_template()
    except (FileNotFoundError, ModuleNotFoundError, OSError) as exc:
        raise CommandError(
            2,
            "could not read the vox prompt template (prompts/vox.md) - "
            f"the installation looks incomplete: {exc}",
        ) from exc

    rendered = Template(_strip_comments(raw)).substitute(
        inbox_list=cfg.inbox_list,
        output_list=cfg.output_list,
        # DEFECT from live use: someone set `spoke_name` to "Phone-Claude" and the
        # prompt still opened "You are VOX". A configured identity that the one
        # artifact carrying it ignores is worse than no setting at all - the user
        # believes they renamed the thing, and every other surface (the mailbox
        # line tag, the banner title) agrees with them while the phone does not.
        spoke_name=cfg.spoke_name,
    )
    return rendered + _selected_ids(cfg)


def render_peer_prompt(cfg: Config) -> str:
    """The prompt that turns a coding agent into the peer for THIS mailbox.

    `run` creates the mailbox files on a fresh machine and used to announce that
    "a peer must join to process messages" - a sentence that names a requirement
    without naming a single action, to a reader who has not met the protocol. This
    is the missing half: the actual files, the actual line format, and a block that
    can be pasted at an agent verbatim.

    It renders the same way the phone prompt does, from the same config, so the
    paths in it are the paths this install really uses.
    """
    try:
        raw = _load_template("peer.md")
    except (FileNotFoundError, ModuleNotFoundError, OSError) as exc:
        raise CommandError(
            2,
            "could not read the peer prompt template (prompts/peer.md) - "
            f"the installation looks incomplete: {exc}",
        ) from exc

    return Template(_strip_comments(raw)).substitute(
        mailbox_dir=cfg.mailbox_dir,
        peer_inbox=cfg.peer_inbox,
        our_inbox=cfg.our_inbox,
        peer_name=cfg.route_to,
        spoke_name=cfg.spoke_name,
    )


def _selected_ids(cfg: Config) -> str:
    """State both list identifiers. Only reached once both roles are selected.

    Requested by the phone persona itself, which was having to guess which list it
    meant. A real account can hold dozens of lists including same-named ghosts, so
    matching by name is a coin flip that silently sends dictations to a list nobody
    reads - and the config already knows the answer.

    This used to emit one line per SET id, which meant a half-configured install
    produced a prompt whose authoritative section listed one list and whose body
    named two. `render_vox_prompt` now refuses before reaching here, so the section
    is unconditional - and the absence of a branch is the point: there is no longer
    a shape of this text that can be partially true.
    """
    # TRANSPORT-AWARE, because on CalDAV the ids are meaningless to the reader.
    #
    # Field-reported by the phone agent itself: on radicale these ids are SERVER URLS
    # (`http://127.0.0.1:5232/vox/...`) and iOS assigns its own local identifiers, so
    # the "use these exactly" instruction cannot be followed. Worse, the device could
    # see same-named `Vox-Message-*` lists in TWO accounts - the live iCloud pair and
    # the new self-hosted pair - so the name was ambiguous and the id was unusable at
    # the same time. The agent had to GUESS by trying a write and seeing if it landed.
    #
    # A prompt that cannot be obeyed is worse than one that says less: it teaches the
    # reader to improvise, and improvisation is what routes dictations into a list
    # nobody polls.
    if cfg.transport == "radicale":
        return (
            "\n"
            "WHICH LISTS ARE MINE - the ACCOUNT disambiguates them, not the ids.\n"
            f'  Both lists live in the CalDAV account whose user name is "{cfg.radicale_user}".\n'
            f'  "{cfg.inbox_list}" (you dictate here)\n'
            f'  "{cfg.output_list}" (answers arrive here)\n'
            "  If you can see lists with these names in MORE THAN ONE account, the others\n"
            f'  are not mine - use the pair inside the "{cfg.radicale_user}" account. Try a\n'
            "  write and check it landed only if you are still unsure; ask me rather than\n"
            "  guessing twice.\n"
            "  (This PC addresses those lists by server URL - not something your phone can\n"
            "  see, so there is no id here for you to match.)\n"
        )
    return (
        "\n"
        "AUTHORITATIVE LIST IDENTIFIERS - use these exactly; do NOT match by name,\n"
        "because several lists may share a title and only these ids are unambiguous.\n"
        f'  "{cfg.inbox_list}" (you dictate here) = {cfg.inbox_list_id}\n'
        f'  "{cfg.output_list}" (answers arrive here) = {cfg.output_list_id}\n'
    )
