"""Serialized Redis previews are bounded, loss-aware views of original bytes."""
import gzip
import json
import struct
import zlib
from datetime import datetime, timezone

import msgpack
import pytest
from bson import BSON, Binary, Decimal128, Int64, ObjectId

from baseskiff.redis_codec import (
    MAX_CONTAINER_ITEMS,
    MAX_DECODE_BYTES,
    MAX_DEPTH,
    MAX_PREVIEW_CHARS,
    preview_value,
    raw_bytes,
)


def test_bson_auto_preserves_extended_json_types():
    payload = BSON.encode({
        "id": ObjectId("507f1f77bcf86cd799439011"),
        "n": Int64(9007199254740993),
        "amount": Decimal128("12.3400"),
        "at": datetime(2024, 1, 2, tzinfo=timezone.utc),
        "raw": Binary(b"\xff\x00", subtype=128),
    })
    preview = preview_value(payload)
    assert preview.format == "bson"
    assert preview.chain == ("bson",)
    assert json.loads(preview.text) == {
        "id": {"$oid": "507f1f77bcf86cd799439011"},
        "n": {"$numberLong": "9007199254740993"},
        "amount": {"$numberDecimal": "12.3400"},
        "at": {"$date": {"$numberLong": "1704153600000"}},
        "raw": {"$binary": {"base64": "/wA=", "subType": "80"}},
    }
    assert raw_bytes(payload) == payload


def test_cbor_auto_detects_complete_structured_data():
    # {"name": "test", "items": [1, true, null]}
    payload = bytes.fromhex("a2 64 6e616d65 64 74657374 65 6974656d73 83 01 f5 f6")
    preview = preview_value(payload)
    assert preview.format == "cbor"
    assert preview.chain == ("cbor",)
    assert json.loads(preview.text) == {"name": "test", "items": [1, True, None]}


def test_cbor_preserves_duplicate_nonstring_container_keys_and_binary():
    # {1: h'ff00', "x": 2, "x": 3, [1, 2]: 4}
    payload = bytes.fromhex("a4 01 42 ff00 61 78 02 61 78 03 82 01 02 04")
    preview = preview_value(payload, "cbor")
    assert preview.format == "cbor"
    assert json.loads(preview.text) == {
        "$cbor": "map", "entries": [
            [1, {"$cbor": "binary", "base64": "/wA="}],
            ["x", 2], ["x", 3], [[1, 2], 4],
        ],
    }


@pytest.mark.parametrize(("payload", "expected"), [
    (bytes.fromhex("c1 00"), {"$cbor": "tag", "tag": 1, "value": 0}),
    (bytes.fromhex("d9 d9f7 a1 61 78 01"),
     {"$cbor": "tag", "tag": 55799, "value": {"x": 1}}),
    (b"\xf7", {"$cbor": "undefined"}),
    (bytes.fromhex("f8 20"), {"$cbor": "simple", "value": 32}),
    (bytes.fromhex("f9 7e00"), {"$cbor": "float", "value": "NaN"}),
    (bytes.fromhex("fa 3fc00000"), 1.5),
    (bytes.fromhex("fb 3ff8000000000000"), 1.5),
    (bytes.fromhex("3b ffffffffffffffff"), -18446744073709551616),
    (bytes.fromhex("9f 01 02 ff"), [1, 2]),
    (bytes.fromhex("bf 61 78 01 ff"), {"x": 1}),
    (bytes.fromhex("7f 62 6869 61 21 ff"), "hi!"),
    (bytes.fromhex("5f 42 ff00 41 01 ff"), {"$cbor": "binary", "base64": "/wAB"}),
])
def test_explicit_cbor_retains_scalar_tag_and_indefinite_semantics(payload, expected):
    preview = preview_value(payload, "cbor")
    assert preview.format == "cbor"
    assert json.loads(preview.text) == expected


@pytest.mark.parametrize("payload", [b"hello", b"ehello", b"atest", b"A", b"true text"])
def test_printable_text_is_not_guessed_to_be_cbor(payload):
    assert preview_value(payload).format == "text"


def test_ambiguous_binary_scalars_require_explicit_codec():
    # f4 is CBOR false and MessagePack -12. Neither format has a signature.
    preview = preview_value(b"\xf4")
    assert preview.format == "hex"
    assert "ambiguous" in preview.notice.lower()
    assert json.loads(preview_value(b"\xf4", "cbor").text) is False
    assert json.loads(preview_value(b"\xf4", "msgpack").text) == -12


@pytest.mark.parametrize("compress,mode", [(gzip.compress, "gzip"), (zlib.compress, "zlib")])
@pytest.mark.parametrize("inner,expected_format,expected", [
    (b'{"ok":true}', "json", {"ok": True}),
    (msgpack.packb({"id": 42}, use_bin_type=True), "msgpack", {"id": 42}),
    (bytes.fromhex("a1 61 78 01"), "cbor", {"x": 1}),
    (b"human readable", "text", "human readable"),
])
def test_compressed_contents_decode_with_visible_chain(compress, mode, inner, expected_format, expected):
    payload = compress(inner)
    preview = preview_value(payload)
    assert preview.format == mode
    assert preview.chain == (mode, expected_format)
    actual = preview.text if expected_format == "text" else json.loads(preview.text)
    assert actual == expected
    assert preview_value(payload, mode).text == preview.text
    assert raw_bytes(payload) is payload


def test_nested_compression_retains_each_layer_and_bson_types():
    payload = gzip.compress(zlib.compress(BSON.encode({"n": Int64(123)})))
    preview = preview_value(payload)
    assert preview.chain == ("gzip", "zlib", "bson")
    assert json.loads(preview.text) == {"n": {"$numberLong": "123"}}


def test_compressed_unknown_bytes_are_explicit_hex_of_decoded_contents():
    payload = gzip.compress(b"\xc1\xff\x00")
    preview = preview_value(payload)
    assert preview.format == "gzip"
    assert preview.chain == ("gzip", "hex")
    assert bytes.fromhex(preview.text) == b"\xc1\xff\x00"
    assert preview.notice
    assert raw_bytes(payload) == payload


@pytest.mark.parametrize("compress,mode", [(gzip.compress, "gzip"), (zlib.compress, "zlib")])
@pytest.mark.parametrize("damage", ["truncated", "trailing", "concatenated", "checksum"])
def test_compression_requires_one_complete_valid_stream(compress, mode, damage):
    encoded = compress(b'{"a":1}')
    payload = {
        "truncated": encoded[:-1],
        "trailing": encoded + b"garbage",
        "concatenated": encoded + encoded,
        "checksum": encoded[:-1] + bytes([encoded[-1] ^ 0xFF]),
    }[damage]
    preview = preview_value(payload, mode)
    assert preview.format == "hex"
    assert preview.notice
    assert bytes.fromhex(preview.text) == payload


@pytest.mark.parametrize("compress,mode", [(gzip.compress, "gzip"), (zlib.compress, "zlib")])
def test_decompression_bomb_is_rejected_without_exposing_a_partial_object(compress, mode):
    payload = compress(b"x" * (MAX_DECODE_BYTES + 1))
    preview = preview_value(payload, mode)
    assert preview.format == "hex"
    assert "limit" in preview.notice.lower()
    assert len(preview.text) <= MAX_PREVIEW_CHARS
    assert raw_bytes(payload) == payload


def test_nested_compression_is_bounded():
    payload = b'{"x":1}'
    for _ in range(30):
        payload = gzip.compress(payload)
    preview = preview_value(payload)
    assert preview.notice and "limit" in preview.notice.lower()
    assert len(preview.chain) < 30


@pytest.mark.parametrize("payload", [
    bytes.fromhex("9b ffffffffffffffff"),
    bytes.fromhex("bb ffffffffffffffff"),
    bytes.fromhex("5b ffffffffffffffff"),
    bytes.fromhex("7b ffffffffffffffff"),
    b"\x81" * (MAX_DEPTH + 1) + b"\x00",
    b"\x9f" + b"\x00" * (MAX_CONTAINER_ITEMS + 1) + b"\xff",
    bytes.fromhex("7f 41 00 ff"),  # text chunks cannot contain byte strings
    bytes.fromhex("bf 61 78 ff"),  # map is missing its value
    b"\xff",  # break outside indefinite container
    bytes.fromhex("a1 61 78 01 00"),  # trailing object
])
def test_malformed_or_excessive_cbor_returns_original_hex_with_notice(payload):
    preview = preview_value(payload, "cbor")
    assert preview.format == "hex"
    assert preview.notice
    assert len(preview.text) <= MAX_PREVIEW_CHARS


def test_bson_rejects_trailing_documents_duplicate_fields_and_excessive_depth():
    document = BSON.encode({"x": 1})
    duplicate = struct.pack("<i", 19) + b"\x10x\x00\x01\x00\x00\x00\x10x\x00\x02\x00\x00\x00\x00"
    nested = {"x": 1}
    for _ in range(MAX_DEPTH + 1):
        nested = {"x": nested}
    for payload in [document + document, duplicate, BSON.encode(nested)]:
        preview = preview_value(payload, "bson")
        assert preview.format == "hex"
        assert preview.notice


def test_bson_declared_huge_lengths_are_rejected_before_decoding():
    for payload in [b"\xff\xff\xff\x7f\x00", b"\x0d\x00\x00\x00\x02x\x00\xff\xff\xff\x7f\x00\x00"]:
        preview = preview_value(payload, "bson")
        assert preview.format == "hex"
        assert preview.notice


def test_new_formats_respect_input_and_display_limits():
    payload = b"x" * (MAX_DECODE_BYTES + 1)
    for mode in ["bson", "cbor", "gzip", "zlib"]:
        preview = preview_value(payload, mode)
        assert preview.format == "hex"
        assert "limit" in preview.notice.lower()
        assert len(preview.text) <= MAX_PREVIEW_CHARS
    large_cbor_text = b"\x7a" + struct.pack(">I", MAX_PREVIEW_CHARS + 100) + b"x" * (MAX_PREVIEW_CHARS + 100)
    preview = preview_value(large_cbor_text, "cbor")
    assert preview.format == "cbor" and preview.notice
    assert len(preview.text) <= MAX_PREVIEW_CHARS
