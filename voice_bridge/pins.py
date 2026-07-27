"""Which list did you actually mean? — one resolver, one picker (UX-6).

A list NAME is not an identity. A real account accumulates lists, and among them
sit same-named ghosts left behind by earlier experiments. Resolving by name picks
one of them at random, forever, and **silently**: the dictation lands somewhere
real, the command exits 0, and nobody is reading that list. So the config stores
an `id`, and this module is how an id gets chosen.

Two rules shape everything here.

**One engine.** `resolve_pins` is the only place that decides whether a pin is
settled, ambiguous, missing or dangling. `setup`, `lists --pin` and `doctor` are
doors onto it, not re-implementations of it. Three resolvers would be three
chances to disagree about the exact question the feature exists to settle — the
same lesson CFG-1 taught about configuration having one resolver.

**No tie-break, ever.** When several lists match, this module hands the choice
back. It will not prefer the fuller list, the newer one, or the first returned.
On an account with ghosts the wrong automatic pick is invisible and permanent,
which is the failure pinning exists to remove — so a heuristic here would
reintroduce it wearing a helpful face. Loudly ambiguous beats silently wrong.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from .config import Config
from .transport import ListRef, Transport

# role -> (configured-name attribute, pinned-id attribute)
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
    """What we know about ONE of the two pins, and what remains to be decided."""

    role: str  # "inbox" | "outbox"
    name: str  # the configured list name
    field: str  # the config field a decision writes to
    current: str  # the id pinned today ("" if none)
    status: str  # chosen | ambiguous | missing | dangling
    candidates: list[ListRef]  # every list matching the name (case-folded)
    chosen: str  # the settled id, "" unless status == "chosen"

    @property
    def needs_input(self) -> bool:
        """True when only a human can settle this."""
        return self.status in ("ambiguous", "dangling")


@dataclass(frozen=True)
class PinPlan:
    """Both roles resolved. The properties are views, so callers filter by meaning
    rather than by re-testing status strings and drifting apart."""

    resolutions: list[Resolution] = field(default_factory=list)

    def _of(self, status: str) -> list[Resolution]:
        return [r for r in self.resolutions if r.status == status]

    @property
    def chosen(self) -> list[Resolution]:
        return self._of("chosen")

    @property
    def ambiguous(self) -> list[Resolution]:
        return self._of("ambiguous")

    @property
    def missing(self) -> list[Resolution]:
        return self._of("missing")

    @property
    def dangling(self) -> list[Resolution]:
        return self._of("dangling")


@dataclass(frozen=True)
class Choice:
    """What a human decided at the picker."""

    action: str  # pin | keep | clear | quit
    list_id: str = ""


def _matches(name: str, refs: list[ListRef]) -> list[ListRef]:
    """Every list matching `name`, compared CASE-INSENSITIVELY.

    A real account carried `Team Inbox` alongside `Team inbox`. Exact matching
    finds precisely one of them, reports no ambiguity at all, and pins it with
    total confidence — while the user meant the other and was never asked. Folding
    case for DETECTION is what makes the twin visible; the picker then displays
    the exact case, because that difference is the only thing telling them apart.
    """
    wanted = name.casefold()
    return [r for r in refs if r.name.casefold() == wanted]


def resolve_pins(cfg: Config, t: Transport) -> PinPlan:
    """Work out, for both roles, whether the pin is settled or needs a human.

    One backend call serves both roles and every candidate's metadata, so the
    whole plan — including everything the picker will display — costs a single
    round trip.
    """
    refs = t.list_todo_lists()
    resolutions: list[Resolution] = []

    for role, (name_attr, id_attr) in _ROLES.items():
        name = str(getattr(cfg, name_attr))
        current = str(getattr(cfg, id_attr) or "")
        candidates = _matches(name, refs)

        if current:
            # A pin outranks the name — that is the entire point of pinning. But a
            # pin that resolves to nothing is DANGLING, not missing: the remedies
            # differ (clear the pin vs create the list), and reporting one as the
            # other sends the user to the wrong place.
            pinned = next((r for r in refs if r.id == current), None)
            if pinned is not None:
                resolutions.append(
                    Resolution(role, name, id_attr, current, "chosen", candidates, current)
                )
                continue
            resolutions.append(Resolution(role, name, id_attr, current, "dangling", candidates, ""))
            continue

        if not candidates:
            status, chosen = "missing", ""
        elif len(candidates) == 1:
            status, chosen = "chosen", candidates[0].id
        else:
            # NO tie-break. See the module docstring.
            status, chosen = "ambiguous", ""
        resolutions.append(Resolution(role, name, id_attr, current, status, candidates, chosen))

    return PinPlan(resolutions)


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
    marker = "   <- current pin" if current and ref.id == current else ""
    return f"{row}   [{ref.id}]{marker}"


def _header(resolution: Resolution) -> str:
    """States the number of rows it is about to show, so the prose can never
    disagree with the list underneath it."""
    n = len(resolution.candidates)
    names = {r.name for r in resolution.candidates}
    folded = {r.name.casefold() for r in resolution.candidates}
    twin = "" if len(names) == len(folded) else " (one differs only by capitalisation)"
    where = "receive your dictations" if resolution.role == "inbox" else "carry replies back"
    return f'{n} lists match "{resolution.name}"{twin} — which should {where}?'


def pick(
    resolution: Resolution,
    *,
    ask: Callable[[str], str],
    show: Callable[[str], None],
) -> Choice:
    """Ask a human to settle one pin. Returns the decision; writes nothing.

    `ask` and `show` are injected so this is testable without a terminal, and so
    the caller — not this function — decides whether asking is even allowed. It
    must never be called on a non-TTY: a prompt written to a pipe either blocks
    for ever or reads EOF and takes an answer nobody gave.

    `keep` and `clear` appear ONLY when a pin exists. On a fresh machine there is
    nothing to keep and nothing to clear, and offering both is noise.
    """
    show(_header(resolution))
    for i, ref in enumerate(resolution.candidates, start=1):
        show(f"  {i}) {_describe(ref, current=resolution.current)}")

    extra = ["k) keep the current pin", "c) clear the pin"] if resolution.current else []
    show("  " + "      ".join([*extra, "q) quit, change nothing"]))

    top = len(resolution.candidates)
    letters = "/".join([*(["k", "c"] if resolution.current else []), "q"])
    prompt = f"Choose 1-{top}, or {letters}: "

    while True:
        try:
            answer = ask(prompt).strip().lower()
        except EOFError:
            # isatty lies under MSYS/Git Bash. A stream that ends is "no answer",
            # which means change nothing — never block, never invent a pick.
            return Choice("quit")

        if answer == "q":
            return Choice("quit")
        if resolution.current and answer in ("k", "c"):
            return Choice("keep" if answer == "k" else "clear")
        if answer.isdigit() and 1 <= int(answer) <= top:
            return Choice("pin", resolution.candidates[int(answer) - 1].id)

        # An unusable answer must re-ask. Falling through to a default would be the
        # silent wrong choice arriving by another door.
        show(f"  not one of the options — choose 1-{top}, or {letters}.")


__all__ = ["Choice", "PinPlan", "Resolution", "pick", "resolve_pins"]
