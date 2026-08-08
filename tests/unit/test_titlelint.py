"""The renderability check itself — the instrument has to be trustworthy.

`titlelint` exists so nobody needs a phone to know whether a title will display.
That makes it the one module where "we think it works" is not good enough: a lint
that passes everything is indistinguishable from no lint, and would have quietly
restored exactly the blindness it was built to remove.

So these tests drive it from both sides — a document it must accept, and one it
must reject — plus the paths where it cannot answer at all, because a diagnostic
that raises is worse than one that says "I don't know, and here is why".
"""

from __future__ import annotations

import base64
import zlib

import pytest

pytest.importorskip("pyicloud")

from voice_bridge import titlelint  # noqa: E402
from voice_bridge._icloud_crdt import encode_crdt_document  # noqa: E402

ASTRAL = "🔑"


def test_utf16_length_counts_code_units_not_codepoints():
    assert titlelint.utf16_length("abc") == 3
    assert titlelint.utf16_length(ASTRAL) == 2, "an astral char is two UTF-16 units"
    assert titlelint.utf16_length("") == 0


# --------------------------------------------------------------------------- #
# it must ACCEPT what the phone can render
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("text", ["plain", "[22:16][vox] hello", f"done {ASTRAL}", "é ü ß", ""])
def test_correctly_encoded_documents_pass(text):
    verdict = titlelint.check_document(encode_crdt_document(text))
    assert verdict.ok, verdict.describe()
    assert bool(verdict) is True
    assert "renderable" in verdict.describe()


def test_check_text_encodes_then_checks():
    assert titlelint.check_text(f"hi {ASTRAL}").ok


# --------------------------------------------------------------------------- #
# it must REJECT what the phone cannot — the negative control
# --------------------------------------------------------------------------- #


def test_the_upstream_defect_is_caught_and_named():
    """If this ever passes, the lint has gone blind and LIVE-5 can recur unseen."""
    from pyicloud.services.reminders._protocol import _encode_crdt_document as upstream

    verdict = titlelint.check_document(upstream(f"emoji {ASTRAL} here"))

    assert not verdict.ok
    assert bool(verdict) is False
    joined = " ".join(verdict.problems)
    assert "content run declares" in joined
    assert "astral characters counted as one" in joined, "it should say WHY, not just that"
    assert "NOT renderable" in verdict.describe()


def test_plain_text_from_upstream_still_passes():
    """The defect is astral-only; flagging ASCII would make the lint cry wolf."""
    from pyicloud.services.reminders._protocol import _encode_crdt_document as upstream

    assert titlelint.check_document(upstream("plain ascii")).ok


# --------------------------------------------------------------------------- #
# it must say "I cannot tell" rather than raise
# --------------------------------------------------------------------------- #


def test_garbage_is_reported_not_raised():
    verdict = titlelint.check_document("this is not base64 zlib protobuf")
    assert not verdict.ok
    assert any("undecodable" in p for p in verdict.problems)


def test_a_document_without_a_version_is_reported():
    from pyicloud.services.reminders.protobuf import versioned_document_pb2

    empty = versioned_document_pb2.Document()
    empty.serializationVersion = 0
    encoded = base64.b64encode(zlib.compress(empty.SerializeToString())).decode()

    verdict = titlelint.check_document(encoded)
    assert not verdict.ok
    assert any("no version" in p for p in verdict.problems)


def test_a_missing_content_run_is_caught():
    """Text present but no run describing it — the phone has nothing to lay out."""
    from pyicloud.services.reminders.protobuf import reminders_pb2, versioned_document_pb2

    value = reminders_pb2.String()
    value.string = "orphaned text"  # deliberately no substring/attributeRun at all
    version = versioned_document_pb2.Version()
    version.serializationVersion = 0
    version.data = value.SerializeToString()
    document = versioned_document_pb2.Document()
    document.serializationVersion = 0
    document.version.append(version)
    encoded = base64.b64encode(zlib.compress(document.SerializeToString())).decode()

    verdict = titlelint.check_document(encoded)
    assert not verdict.ok
    assert any("no content run" in p for p in verdict.problems)


# --------------------------------------------------------------------------- #
# the stored-record leg — the one that shipped broken because nothing drove it
# --------------------------------------------------------------------------- #


class _FakeRecord:
    def __init__(self, doc: str | None):
        self.fields = {"TitleDocument": {"value": doc}} if doc else {}


class _FakeResp:
    def __init__(self, records):
        self.records = records


class _FakeRaw:
    def __init__(self, records):
        self._records = records
        self.calls: list[dict] = []

    def lookup(self, record_names, zone_id):
        self.calls.append({"record_names": record_names, "zone_id": zone_id})
        return _FakeResp(self._records)


class _FakeReads:
    def __init__(self, records):
        self._raw = _FakeRaw(records)

    def _get_raw(self):
        return self._raw


class _FakeService:
    def __init__(self, records):
        self._reads = _FakeReads(records)


def test_stored_document_is_fetched_and_checked():
    """This leg had no test at all, which is exactly why LIVE-7 shipped."""
    service = _FakeService([_FakeRecord(encode_crdt_document(f"stored {ASTRAL}"))])
    verdict = titlelint.check_stored(service, "Reminder/ABC")
    assert verdict.ok, verdict.describe()
    assert service._reads._raw.calls, "it must actually perform the lookup"


def test_a_stored_bad_document_is_caught():
    from pyicloud.services.reminders._protocol import _encode_crdt_document as upstream

    service = _FakeService([_FakeRecord(upstream(f"bad {ASTRAL}"))])
    assert not titlelint.check_stored(service, "Reminder/ABC").ok


def test_a_record_without_a_title_document_is_reported():
    service = _FakeService([_FakeRecord(None)])
    verdict = titlelint.check_stored(service, "Reminder/ABC")
    assert not verdict.ok
    assert any("no TitleDocument" in p for p in verdict.problems)


class _TypedRecord:
    """The shape a REAL account returns, measured 2026-08-09 against pyicloud's
    reminders client: `fields["TitleDocument"]` is a `CKFieldOpen` RootModel wrapping
    a `CKEncryptedBytesField`, whose `.value` is the raw deflate bytes - not a dict,
    and not base64. The dict in `_FakeRecord` above is the shape the check ASSUMED,
    which is why every fake passed while every live record failed (LIVE-6)."""

    def __init__(self, raw: bytes):
        class _Inner:
            value = raw

        class _Open:
            root = _Inner()

        self.fields = {"TitleDocument": _Open()}


def _raw_document(text: str) -> bytes:
    """What the server stores: the encoded document, deflated, without the base64."""
    return base64.b64decode(encode_crdt_document(text))


def test_the_typed_field_model_a_live_account_returns_is_read():
    """LIVE-6: `isinstance(entry, dict)` sent every live record down the "no
    TitleDocument" path, so `verify` told users with good titles that their phone
    would render nothing. Both live legs were [ok]; only this verdict was wrong."""
    service = _FakeService([_TypedRecord(_raw_document("titlelint probe DELETE ME"))])

    verdict = titlelint.check_stored(service, "Reminder/ABC")

    assert verdict.ok, verdict.describe()
    assert verdict.text == "titlelint probe DELETE ME"


def test_raw_deflate_bytes_are_accepted_alongside_base64():
    """Same document, both encodings, same verdict - the branch is on type, so
    neither can shadow the other."""
    text = f"both ways {ASTRAL}"
    as_b64 = encode_crdt_document(text)

    assert titlelint.check_document(as_b64).ok
    assert titlelint.check_document(base64.b64decode(as_b64)).ok


def test_an_unreadable_title_field_is_UNDETERMINED_not_a_failure():
    """The blind spot must not wear a defect's clothes. If pyicloud moves the field
    model again, the check has to say it cannot tell - not accuse the title."""

    class _Alien:
        fields = {"TitleDocument": object()}

    verdict = titlelint.check_stored(_FakeService([_Alien()]), "Reminder/ABC")

    assert verdict.ok is False
    assert verdict.undetermined is True
    assert "cannot read" in verdict.describe()
    assert "NOT renderable" not in verdict.describe()


def test_a_failing_lookup_is_reported_not_raised():
    class _Boom:
        _reads = property(lambda self: (_ for _ in ()).throw(RuntimeError("network gone")))

    verdict = titlelint.check_stored(_Boom(), "Reminder/ABC")
    assert not verdict.ok
    assert any("network gone" in p for p in verdict.problems)
