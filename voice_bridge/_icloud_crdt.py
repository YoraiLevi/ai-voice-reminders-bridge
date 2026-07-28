"""Corrected CRDT title/notes encoding for Apple Reminders (LIVE-5).

Reminders created through pyicloud rendered EMPTY on the phone whenever the text
contained an emoji, while plain text rendered fine - and every read-back through
the API looked correct throughout.

The cause is one expression. pyicloud's `_encode_crdt_document` declares
`len(text)` - Python CODEPOINTS - as the length of the CRDT content run, the
attribute run, and the replica clock. Apple's topotext uses Foundation string
semantics: UTF-16 CODE UNITS. An astral character (any emoji) is two units there
and one in Python, so an emoji-bearing title shipped a document whose declared
lengths were short, and Apple's renderer refused to display it.

The API could never reveal this: pyicloud's decoder reads only `value.string` and
never inspects the scaffolding, so an encode → decode round trip succeeds however
malformed the structure is. Only the phone parses it.

This module is a faithful copy of upstream's encoder with that single computation
corrected, installed over the name `_writes` actually calls. It is deliberately
NOT a fork of pyicloud: the surface is one private function, and a copy that
close is cheap to drop once upstream is fixed. `test_icloud_crdt.py` pins both
the correction and the install site, so a pyicloud upgrade that renames the
symbol - or fixes the bug - fails loudly instead of silently reverting to a
defect only a human holding a phone could detect.

Upstream: pyicloud/services/reminders/_protocol.py::_encode_crdt_document (2.6.5).
"""

from __future__ import annotations

import base64
import zlib

#: Apple's own replica id, copied from upstream so documents stay recognisable.
_REPLICA_UUID = bytes.fromhex("d46bcae41b8766c18d75efe35c9145c3")
_CLOCK_MAX = 0xFFFF_FFFF


def utf16_length(text: str) -> int:
    """Length in UTF-16 code units - what Foundation counts, not what Python does."""
    return len(text.encode("utf-16-le")) // 2 if text else 0


def encode_crdt_document(text: str) -> str:
    """Encode a string as an Apple versioned topotext CRDT document.

    Identical to upstream except that every declared length is in UTF-16 code
    units rather than Python codepoints.
    """
    from pyicloud.services.reminders.protobuf import reminders_pb2, versioned_document_pb2

    text_length = utf16_length(text)

    value = reminders_pb2.String()
    value.string = text

    sentinel = value.substring.add()
    sentinel.charID.replicaID = 0
    sentinel.charID.clock = 0
    sentinel.length = 0
    sentinel.timestamp.replicaID = 0
    sentinel.timestamp.clock = 0
    sentinel.child.append(1)

    if text_length > 0:
        content = value.substring.add()
        content.charID.replicaID = 1
        content.charID.clock = 0
        content.length = text_length
        content.timestamp.replicaID = 1
        content.timestamp.clock = 0
        content.child.append(2)

    terminal = value.substring.add()
    terminal.charID.replicaID = 0
    terminal.charID.clock = _CLOCK_MAX
    terminal.length = 0
    terminal.timestamp.replicaID = 0
    terminal.timestamp.clock = _CLOCK_MAX

    timestamp_clock = value.timestamp.clock.add()
    timestamp_clock.replicaUUID = _REPLICA_UUID
    content_clock = timestamp_clock.replicaClock.add()
    content_clock.clock = text_length
    sentinel_clock = timestamp_clock.replicaClock.add()
    sentinel_clock.clock = 1

    if text_length > 0:
        attribute_run = value.attributeRun.add()
        attribute_run.length = text_length

    version = versioned_document_pb2.Version()
    version.serializationVersion = 0
    version.minimumSupportedVersion = 0
    version.data = value.SerializeToString()

    document = versioned_document_pb2.Document()
    document.serializationVersion = 0
    document.version.append(version)

    return base64.b64encode(zlib.compress(document.SerializeToString())).decode("utf-8")


def install() -> bool:
    """Point pyicloud's write path at the corrected encoder. Idempotent.

    `_writes` does `from ._protocol import _encode_crdt_document`, binding the
    name at import - so the module that CALLS it is the one that must be patched.
    Patching `_protocol` alone would appear to work and change nothing.
    """
    try:
        from pyicloud.services.reminders import _writes
    except ImportError:  # pragma: no cover - pyicloud not installed
        return False

    if not hasattr(_writes, "_encode_crdt_document"):  # pragma: no cover - upstream moved it
        return False

    _writes._encode_crdt_document = encode_crdt_document
    return True
