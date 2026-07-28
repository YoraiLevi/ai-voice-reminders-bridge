"""Which list did you actually mean? — one resolver, one picker (UX-6).

A list NAME is not an identity. A real account accumulates lists, and among them
sit same-named ghosts left behind by earlier experiments. Resolving by name picks
one of them at random, forever, and **silently**: the dictation lands somewhere
real, the command exits 0, and nobody is reading that list. So the config stores
an `id`, and this module is how an id gets chosen.

Two rules shape everything here.

**One engine.** `resolve_selection` is the only place that decides whether a list
is selected, unselected or stale. `setup`, `lists --select` and `doctor` are doors
onto it, not re-implementations of it. Three resolvers would be three
chances to disagree about the exact question the feature exists to settle — the
same lesson CFG-1 taught about configuration having one resolver.

**Nothing is ever inferred.** A list is used because someone chose it, never
because its title matched. There is no tie-break, no "obvious" candidate, and no
name resolution left to be clever with: on an account with duplicate titles any
automatic pick is invisible and permanent, which is the failure this exists to
remove. Asking is the whole feature. Loudly unselected beats silently wrong.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from .config import Config
from .transport import ListRef, Transport

# role -> (configured-name attribute, selected-id attribute)
# The crossing is deliberate and already cost us once (UX-1): the list NAMES are
# written from the phone user's seat, the FIELD names from the bridge's. The
# user's outbox is the bridge's inbox. Callers must never redo this mapping by
# hand — they read `Resolution.field`.
_ROLES: dict[str, tuple[str, str]] = {
    "inbox": ("inbox_list", "inbox_list_id"),
    "outbox": ("output_list", "output_list_id"),
}


@dataclass(frozen=True)
class Resolution:
    """What we know about ONE of the two selections, and what remains to be decided."""

    role: str  # "inbox" | "outbox"
    name: str  # the configured list name
    field: str  # the config field a decision writes to
    current: str  # the id selected today ("" if none)
    status: str  # selected | unselected | stale
    candidates: list[ListRef]  # the full inventory, for the picker to show
    chosen: str  # the settled id, "" unless status == "selected"

    @property
    def needs_input(self) -> bool:
        """True when only a human can settle this."""
        return self.status in ("unselected", "stale")


@dataclass(frozen=True)
class SelectionPlan:
    """Both roles resolved. The properties are views, so callers filter by meaning
    rather than by re-testing status strings and drifting apart."""

    resolutions: list[Resolution] = field(default_factory=list)

    def _of(self, status: str) -> list[Resolution]:
        return [r for r in self.resolutions if r.status == status]

    @property
    def selected(self) -> list[Resolution]:
        return self._of("selected")

    @property
    def unselected(self) -> list[Resolution]:
        return self._of("unselected")

    @property
    def stale(self) -> list[Resolution]:
        return self._of("stale")


@dataclass(frozen=True)
class Choice:
    """What a human decided at the picker."""

    action: str  # select | keep | clear | skip
    list_id: str = ""


def resolve_selection(
    cfg: Config, t: Transport, *, refs: list[ListRef] | None = None
) -> SelectionPlan:
    """Work out, for both roles, whether a list is selected — by ID, never by name.

    Three states, and none of them involves matching a title:

    * **selected** — the stored id resolves to a list that exists;
    * **stale** — an id is stored but nothing has it any more;
    * **unselected** — no id is stored, so a human must choose.

    Name matching is gone entirely. It was never a way of knowing which list you
    meant, only a way of guessing quietly: a name is not unique, so on any account
    with duplicates it picked one at random and never said so. Every path that used
    to auto-resolve a name now asks instead, which is why the ambiguous/missing
    distinction disappeared with it — there is nothing to be ambiguous about when
    nothing is inferred.

    `candidates` is therefore the FULL inventory, with the current selection marked,
    rather than a name-filtered shortlist. That also retires the case-twin special
    case: `Team Inbox` and `Team inbox` are simply two ordinary rows.

    One backend call serves both roles and every candidate's metadata. Pass `refs`
    to re-classify against a listing already in hand, which is how the interactive
    menu refreshes after a change without a second call or a second copy of these
    rules.
    """
    refs = list(refs) if refs is not None else t.list_todo_lists()
    resolutions: list[Resolution] = []

    for role, (name_attr, id_attr) in _ROLES.items():
        name = str(getattr(cfg, name_attr))
        current = str(getattr(cfg, id_attr) or "")

        if not current:
            status, chosen = "unselected", ""
        elif any(r.id == current for r in refs):
            status, chosen = "selected", current
        else:
            # STALE, not "missing": the remedy is to clear or re-choose the id,
            # not to create a list. Reporting one as the other sends the user to
            # the wrong place.
            status, chosen = "stale", ""
        resolutions.append(Resolution(role, name, id_attr, current, status, refs, chosen))

    return SelectionPlan(resolutions)


# --------------------------------------------------------------------------- #
# the picker — ONE component, used by every conflict site
# --------------------------------------------------------------------------- #


def _describe(ref: ListRef, *, current: str) -> str:
    """One candidate row: only facts the backend really returned."""
    bits = [f"{ref.name}"]
    if ref.count is not None:
        bits.append(f"{ref.count} item{'' if ref.count == 1 else 's'}")
    if ref.color:
        bits.append(ref.color)
    if ref.badge_emblem:
        bits.append(ref.badge_emblem)
    if ref.is_group:
        bits.append("(group)")
    row = "   ".join(bits)
    marker = "   <- current selection" if current and ref.id == current else ""
    return f"{row}   [{ref.id}]{marker}"


def _header(resolution: Resolution, *, count: int | None = None) -> str:
    """Ask the question, and state how many rows follow so the prose can never
    disagree with the list underneath it.

    It no longer says how many lists "match", because nothing is matched any more:
    every list on the account is offered and the choice is made by id. The
    configured name is shown only as a hint about which role this is.
    """
    n = len(resolution.candidates) if count is None else count
    where = "receive your dictations" if resolution.role == "inbox" else "carry replies back"
    plural = "list" if n == 1 else "lists"
    return f"Which list should {where}?  ({n} {plural} on this account)"


def pick(
    resolution: Resolution,
    *,
    ask: Callable[[str], str],
    show: Callable[[str], None],
    refresh: Callable[[], list[ListRef]] | None = None,
) -> Choice:
    """Ask a human to choose one list. Returns the decision; writes nothing.

    `ask` and `show` are injected so this is testable without a terminal, and so
    the caller — not this function — decides whether asking is even allowed. It
    must never be called on a non-TTY: a prompt written to a pipe either blocks
    for ever or reads EOF and takes an answer nobody gave.

    **`refresh` is what makes this usable on a phone.** iCloud cannot create lists
    over the API, so the user makes them in Reminders *while this prompt is open* —
    and sync takes its time. Without a way to re-read the account they would have
    to abandon setup, wait, and start again, guessing at how long is long enough.
    With it, they create the list, press `r` until it appears, and carry on.

    `keep` and `clear` appear ONLY when a selection exists. On a fresh machine
    there is nothing to keep and nothing to clear, and offering both is noise.
    """
    candidates = list(resolution.candidates)

    while True:
        show(_header(resolution, count=len(candidates)))
        for i, ref in enumerate(candidates, start=1):
            show(f"  {i}) {_describe(ref, current=resolution.current)}")
        if not candidates:
            show("  (this account has no lists yet)")

        # CHANGING is what this command is for, so the change action is stated on
        # its own line, directly under the rows, before anything else is offered.
        # An earlier layout listed only navigation verbs beneath them, and a real
        # user read the whole screen as a viewer with no way to edit.
        show("")
        if candidates:
            show(f"  Type 1-{len(candidates)} to CHOOSE that list.")

        # Every other label states what CHOOSING IT DOES, because two of them did
        # not and a user had to discover the difference by trying them (both ruled
        # from live use): "quit" did not quit — it moved on — and a bare "keep"
        # gave no hint whether it meant keep-and-stop or keep-and-carry-on.
        extra = (
            ["k) keep this selection as it is", "c) clear this selection"]
            if resolution.current
            else []
        )
        show("  or:  " + "      ".join([*extra, "r) refresh the list", "s) skip, change nothing"]))

        top = len(candidates)
        letters = "/".join([*(["k", "c"] if resolution.current else []), "r", "s"])
        prompt = f"Choose 1-{top}, or {letters}: " if top else f"Choose {letters}: "

        while True:
            try:
                answer = ask(prompt).strip().lower()
            except EOFError:
                # isatty lies under MSYS/Git Bash. A stream that ends is "no
                # answer": change nothing, never block, never invent a pick.
                return Choice("skip")

            # `q` stays accepted but undocumented: it was the key before this was
            # relabelled, and silently breaking a habit is worse than a branch.
            if answer in ("s", "q"):
                return Choice("skip")
            if answer == "r":
                if refresh is None:
                    show("  (nothing to refresh here)")
                    continue
                candidates = list(refresh())
                show("")
                break  # redraw the whole screen with the new listing
            if resolution.current and answer in ("k", "c"):
                return Choice("keep" if answer == "k" else "clear")
            if answer.isdigit() and 1 <= int(answer) <= top:
                return Choice("select", candidates[int(answer) - 1].id)

            # An unusable answer must re-ask. Falling through to a default would
            # be the silent wrong choice arriving by another door.
            show(f"  not one of the options — choose {'1-%d, or ' % top if top else ''}{letters}.")


def confirm_selection(ref: ListRef, *, role: str, show: Callable[[str], None]) -> None:
    """Show exactly what was chosen, after it is chosen.

    Asked for directly: choosing from a numbered menu tells you which ROW you
    picked, not which list you now own. Echoing the name, the id and the metadata
    closes that gap while the user can still act on it — and the id is what the
    phone prompt will carry, so seeing it here is the same fact they will see there.
    """
    where = "dictations arrive in" if role == "inbox" else "replies go out to"
    show(f"  {where}: {_describe(ref, current=ref.id).replace('   <- current selection', '')}")


__all__ = [
    "Choice",
    "SelectionPlan",
    "Resolution",
    "confirm_selection",
    "pick",
    "resolve_selection",
]
