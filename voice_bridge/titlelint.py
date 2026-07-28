"""Will the phone actually render this? - a machine-side renderability verdict.

Every instrument we had was blind to LIVE-5. pyicloud's decoder reads only
`value.string`, so a title whose CRDT scaffolding was malformed round-tripped
perfectly through the API while the phone displayed nothing. The only oracle was
a human holding a device.

This module removes that dependency. A CRDT document is *internally checkable*:
the content run, the attribute run and the replica clock each declare a length,
and all three must equal the UTF-16 length of the string they describe. When they
disagree the document is inconsistent, and an inconsistent document is what Apple
refuses to draw - which is a verdict a machine can reach on its own.

Two entry points, deliberately:

* `check_document` / `check_text` are pure and need no account, so the class is
  catchable in CI;
* `check_stored` fetches what the server actually holds, which is the only way to
  catch a document that was mangled in transit or written by an older build.
"""

from __future__ import annotations

import base64
import zlib
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Verdict:
    """Whether a title document is renderable, and why not if it isn't."""

    ok: bool
    text: str = ""
    problems: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.ok

    def describe(self) -> str:
        if self.ok:
            return f"renderable: {self.text!r}"
        return f"NOT renderable: {self.text!r} - " + "; ".join(self.problems)


def utf16_length(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2 if text else 0


def check_document(doc_b64: str) -> Verdict:
    """Validate one encoded CRDT document against itself.

    The check is self-contained: whatever string the document carries, every
    declared length must match that string's UTF-16 length. No reference copy is
    needed, which is why this works on records we did not write.
    """
    try:
        from pyicloud.services.reminders.protobuf import reminders_pb2, versioned_document_pb2
    except ImportError:  # pragma: no cover - pyicloud not installed
        return Verdict(False, problems=["pyicloud not installed"])

    try:
        raw = zlib.decompress(base64.b64decode(doc_b64))
        document = versioned_document_pb2.Document()
        document.ParseFromString(raw)
        if not document.version:
            return Verdict(False, problems=["document carries no version"])
        value = reminders_pb2.String()
        value.ParseFromString(document.version[0].data)
    except Exception as exc:
        return Verdict(False, problems=[f"undecodable: {type(exc).__name__}: {exc}"])

    text = value.string or ""
    expected = utf16_length(text)
    problems: list[str] = []

    content_runs = [s.length for s in value.substring if s.length]
    if expected and not content_runs:
        problems.append("no content run for non-empty text")
    for declared in content_runs:
        if declared != expected:
            problems.append(
                f"content run declares {declared}, text is {expected} UTF-16 units"
                + (" (astral characters counted as one)" if declared == len(text) else "")
            )

    for declared in (a.length for a in value.attributeRun):
        if declared != expected:
            problems.append(f"attribute run declares {declared}, text is {expected}")

    clocks = [rc.clock for c in value.timestamp.clock for rc in c.replicaClock]
    if expected and expected not in clocks:
        problems.append(f"replica clock {clocks} does not carry the text length {expected}")

    return Verdict(not problems, text, problems)


def check_text(text: str) -> Verdict:
    """Encode `text` the way a write would, then check it. Pure; no account."""
    from ._icloud_crdt import encode_crdt_document

    return check_document(encode_crdt_document(text))


def check_stored(service: object, reminder_id: str) -> Verdict:
    """Fetch what the SERVER holds for one reminder and check that.

    Reaches through pyicloud's private read path because the typed model discards
    the raw record - and the raw record is the only thing that shows what the
    phone will be asked to parse.
    """
    # Resolve the helpers from the module that already imported them, rather than
    # naming a source module. The first attempt guessed `_protocol` for the zone
    # constant, which actually lives in `_constants` - an assumed private surface,
    # inside the tool built to retire assumed private surfaces. `_reads` binds both
    # names because it uses them, and `_reads` is what we call, so taking them from
    # there cannot disagree with the call we are about to make.
    try:
        from pyicloud.services.reminders import _reads as reads_mod
    except ImportError as exc:  # pragma: no cover - pyicloud not installed
        return Verdict(False, problems=[f"cannot reach the raw read path: {exc}"])

    zone = getattr(reads_mod, "_REMINDERS_ZONE_REQ", None)
    as_record_name = getattr(reads_mod, "_as_record_name", None)
    if zone is None or as_record_name is None:  # pragma: no cover - upstream moved them
        return Verdict(
            False,
            problems=[
                "pyicloud's raw read helpers have moved; the stored-title check needs updating"
            ],
        )

    try:
        reads = service._reads  # type: ignore[attr-defined]
        resp = reads._get_raw().lookup(
            record_names=[as_record_name(reminder_id, "Reminder")],
            zone_id=zone,
        )
        for rec in resp.records:
            fields = getattr(rec, "fields", None) or {}
            entry = fields.get("TitleDocument") or {}
            doc = entry.get("value") if isinstance(entry, dict) else None
            if doc:
                return check_document(doc)
        return Verdict(False, problems=["no TitleDocument on the stored record"])
    except Exception as exc:
        return Verdict(False, problems=[f"lookup failed: {type(exc).__name__}: {exc}"])
