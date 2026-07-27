"""The CRDT title encoding, corrected to UTF-16 lengths (LIVE-5).

Reminders created by the bridge rendered EMPTY on the phone whenever the text
contained an emoji, while plain text rendered fine — and every read-back through
the API looked correct the whole time.

The cause: pyicloud's encoder declares `len(text)` — Python CODEPOINTS — as the
length of the CRDT content run, the attribute run, and the replica clock. Apple's
topotext uses Foundation string semantics, i.e. UTF-16 CODE UNITS. Any astral
character (emoji) counts as two there and one in Python, so every emoji-bearing
title shipped a document whose declared lengths were short, and Apple's renderer
refused it.

Why no test could have caught it before: pyicloud's DECODER reads only
`value.string` and never inspects the structure, so encode → decode round-trips
clean no matter how malformed the scaffolding is. The API was a blind oracle; the
phone was the only instrument that could see it. These tests therefore assert the
STRUCTURE, which is the thing that was wrong.
"""

from __future__ import annotations

import base64
import zlib

import pytest

pytest.importorskip("pyicloud")

from voice_bridge import _icloud_crdt  # noqa: E402

ASTRAL = "🔑"  # one codepoint, TWO UTF-16 code units — the whole bug in one char


def _decode_structure(doc_b64: str):
    """Unpack our encoded document back into the protobuf the phone parses."""
    from pyicloud.services.reminders.protobuf import reminders_pb2, versioned_document_pb2

    raw = zlib.decompress(base64.b64decode(doc_b64))
    document = versioned_document_pb2.Document()
    document.ParseFromString(raw)
    version = document.version[0]
    value = reminders_pb2.String()
    value.ParseFromString(version.data)
    return value


def _utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


@pytest.mark.parametrize(
    "text",
    [
        "plain ascii",
        "[22:16][vox] hello",
        f"[22:16][vox] done {ASTRAL}",
        f"{ASTRAL}{ASTRAL} leading astral",
        "accent é and ü stay one unit each",
    ],
)
def test_declared_lengths_are_utf16_code_units(text):
    """The assertion that would have caught LIVE-5 without a phone."""
    value = _decode_structure(_icloud_crdt.encode_crdt_document(text))
    expected = _utf16_len(text)

    assert value.string == text, "the text itself must survive"

    content_runs = [s for s in value.substring if s.length]
    assert content_runs, "non-empty text needs a content run"
    assert content_runs[0].length == expected, (
        f"content run declares {content_runs[0].length}, UTF-16 length is {expected}"
    )
    assert [a.length for a in value.attributeRun] == [expected]
    clocks = [rc.clock for c in value.timestamp.clock for rc in c.replicaClock]
    assert expected in clocks, f"replica clock must carry the UTF-16 length, got {clocks}"


def test_ascii_is_unchanged_from_upstream():
    """Where the two agree, we must not have drifted — only astral text differs."""
    from pyicloud.services.reminders._protocol import _encode_crdt_document as upstream

    for text in ("plain ascii", "[22:16][vox] hello", ""):
        assert _icloud_crdt.encode_crdt_document(text) == upstream(text), text


def test_upstream_is_wrong_for_astral_text():
    """Pins the actual defect, so a fixed pyicloud makes this fail loudly."""
    from pyicloud.services.reminders._protocol import _encode_crdt_document as upstream

    text = f"hi {ASTRAL}"
    theirs = [s.length for s in _decode_structure(upstream(text)).substring if s.length]
    ours = [
        s.length
        for s in _decode_structure(_icloud_crdt.encode_crdt_document(text)).substring
        if s.length
    ]
    assert theirs == [len(text)], "upstream declares codepoints"
    assert ours == [_utf16_len(text)], "we declare UTF-16 code units"
    assert theirs != ours, "if these ever match, upstream is fixed — drop the patch"


def test_empty_text_has_no_content_run():
    value = _decode_structure(_icloud_crdt.encode_crdt_document(""))
    assert [s.length for s in value.substring] == [0, 0]
    assert list(value.attributeRun) == []


def test_patch_lands_on_the_site_that_is_actually_called():
    """The guard against a silent revert.

    `_writes` binds the encoder by name at import, so patching `_protocol` alone
    would do nothing while appearing to succeed. If a pyicloud upgrade renames or
    relocates it, this fails LOUDLY rather than quietly restoring the bug that
    only a human holding a phone can detect.
    """
    from pyicloud.services.reminders import _writes

    _icloud_crdt.install()
    assert _writes._encode_crdt_document is _icloud_crdt.encode_crdt_document


# --------------------------------------------------------------------------- #
# LIVE-7 — every private symbol we reach for must be pinned to the installed lib
# --------------------------------------------------------------------------- #


def test_raw_read_helpers_resolve_against_installed_pyicloud():
    """The guard that was missing, which is why LIVE-7 shipped.

    `check_stored` reaches into pyicloud's private read path. The first version
    imported the zone constant from `_protocol`, where it does not live — an
    assumed private surface, inside the tool built to retire assumed private
    surfaces, and invisible because only the never-unit-tested raw leg used it.

    Resolving from `_reads` — the module that both imports them and is the one we
    call — cannot disagree with the call itself. This asserts they are actually
    there, so a pyicloud reshuffle fails HERE rather than on someone's phone.
    """
    reads_mod = pytest.importorskip("pyicloud.services.reminders._reads")

    assert getattr(reads_mod, "_REMINDERS_ZONE_REQ", None) is not None, (
        "the zone constant moved; check_stored resolves it from this module"
    )
    assert callable(getattr(reads_mod, "_as_record_name", None)), (
        "the record-name helper moved; check_stored resolves it from this module"
    )


def test_check_stored_reports_rather_than_raises_without_a_service():
    """A diagnostic that explodes is worse than one that says it cannot tell."""
    from voice_bridge import titlelint

    verdict = titlelint.check_stored(object(), "Reminder/does-not-exist")
    assert verdict.ok is False
    assert verdict.problems, "it must say WHY it could not answer"
