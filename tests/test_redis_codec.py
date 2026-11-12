"""Read-only Redis previews must never alter or reject the original bytes."""
import base64
import json

import pytest

from baseskiff.redis_codec import (
    MAX_DECODE_BYTES,
    MAX_PREVIEW_CHARS,
    ValuePreview,
    json_safe,
    preview_value,
    raw_bytes,
    safe_text,
)


def test_json_and_utf8_text_are_detected_before_messagepack():
    preview = preview_value('{"name":"用户","enabled":true}')
    assert isinstance(preview, ValuePreview)
    assert preview.format == "json"
    assert json.loads(preview.text) == {"name": "用户", "enabled": True}
    assert "\n" in preview.text
    assert preview_value(b"plain text\nwith tabs\t").format == "text"
    assert preview_value(b"42").format == "json"
    assert preview_value(b"").text == ""


def test_messagepack_common_map_is_readable_json():
    preview = preview_value(b"\x82\xa4name\xa4test\xa5count\x03")
    assert preview.format == "msgpack"
    assert json.loads(preview.text) == {"name": "test", "count": 3}


def test_messagepack_binary_extensions_and_timestamps_keep_their_type():
    # [bin8(ff 00), fixext1(type 2, 80), timestamp32(seconds 3)]
    preview = preview_value(b"\x93\xc4\x02\xff\x00\xd4\x02\x80\xd6\xff\x00\x00\x00\x03")
    assert preview.format == "msgpack"
    assert json.loads(preview.text) == [
        {"$msgpack": "binary", "base64": "/wA="},
        {"$msgpack": "extension", "code": 2, "base64": "gA=="},
        {"$msgpack": "timestamp", "seconds": 3, "nanoseconds": 0},
    ]


def test_messagepack_nonstring_and_duplicate_map_keys_keep_all_entries():
    preview = preview_value(b"\x83\x01\xa1a\xa1x\x02\xa1x\x03", "msgpack")
    assert json.loads(preview.text) == {
        "$msgpack": "map", "entries": [[1, "a"], ["x", 2], ["x", 3]],
    }
    array_key = preview_value(b"\x81\x92\x01\x02\xa1v", "msgpack")
    assert json.loads(array_key.text)["entries"] == [[[1, 2], "v"]]


def test_manual_messagepack_can_decode_printable_fixints():
    assert preview_value(b"A").format == "text"
    assert json.loads(preview_value(b"A", "msgpack").text) == 65


@pytest.mark.parametrize("value", [b"\xc1\xff\x00", b"\x81\xa1x", b"\x81\xa1x\x01\x00"])
def test_unknown_binary_and_incomplete_or_multiple_objects_fall_back_to_hex(value):
    preview = preview_value(value)
    assert preview.format == "hex"
    assert "00" in preview.text or "81" in preview.text
    assert preview_value(value, "msgpack").notice


def test_manual_text_escapes_invalid_utf8_and_controls_without_lossy_replacement():
    preview = preview_value(b"a\xff\x00b", "text")
    assert preview.format == "text"
    assert "\\xff" in preview.text and "\\x00" in preview.text
    assert "\ufffd" not in preview.text
    assert preview.notice


def test_hex_and_base64_formats_represent_the_exact_bytes():
    value = b"\x00\xff\xc3\xa9"
    assert bytes.fromhex(preview_value(value, "hex").text) == value
    assert base64.b64decode(preview_value(value, "base64").text) == value
    assert raw_bytes(value) is value
    assert raw_bytes("用户") == "用户".encode()


@pytest.mark.parametrize("mode", ["json", "msgpack", "unknown"])
def test_invalid_requested_formats_return_displayable_error(mode):
    preview = preview_value(b"not valid here", mode)
    assert preview.notice
    assert preview.text
    assert "\x00" not in preview.text


def test_safe_text_retains_human_text_and_marks_binary_unambiguously():
    assert safe_text("用户:一") == "用户:一"
    assert safe_text("a\x00b\n\ud800") == "a\\x00b\\n\\ud800"
    assert safe_text("用户".encode()) == "用户"
    binary = safe_text(b"\xff\x00")
    assert "binary" in binary and "ff 00" in binary
    assert "\x00" not in binary


def test_json_escaped_surrogates_and_controls_are_safe_for_qt():
    preview = preview_value(b'{"data":"\\ud800\\u0000"}')
    assert preview.format == "json"
    assert "\ud800" not in preview.text and "\x00" not in preview.text
    assert json.loads(preview.text) == {"data": "\ud800\x00"}


def test_large_values_limit_preview_without_truncating_raw_bytes():
    value = b"x" * (MAX_DECODE_BYTES + 100)
    preview = preview_value(value)
    assert len(preview.text) <= MAX_PREVIEW_CHARS
    assert preview.notice
    assert raw_bytes(value) == value
    large_base64 = preview_value(value, "base64")
    assert len(large_base64.text) <= MAX_PREVIEW_CHARS
    assert large_base64.notice


def test_deep_messagepack_and_json_return_notice_instead_of_crashing():
    packed = b"\x91" * 1000 + b"\xc0"
    assert preview_value(packed, "msgpack").notice
    nested_json = "[" * 1000 + "0" + "]" * 1000
    assert preview_value(nested_json, "json").notice


def test_wide_messagepack_container_is_bounded():
    # array32 header claiming an enormous array must not allocate that array.
    preview = preview_value(b"\xdd\x7f\xff\xff\xff", "msgpack")
    assert preview.notice


def test_messagepack_nan_is_explicit_json_friendly_data():
    preview = preview_value(b"\xcb\x7f\xf8\x00\x00\x00\x00\x00\x00", "msgpack")
    assert json.loads(preview.text) == {"$msgpack": "float", "value": "NaN"}


def test_json_safe_preserves_binary_values_and_nonstring_map_keys():
    assert json_safe({"plain": b"\xff\x00"}) == {"plain": {"$binary": "/wA="}}
    assert json_safe({b"raw": b"value"}) == {
        "$map": [[{"$binary": "cmF3"}, {"$binary": "dmFsdWU="}]],
    }
    assert json.loads(json.dumps(json_safe({b"raw": [1, "text"]}), allow_nan=False))


def test_json_safe_bounds_depth_and_wide_values():
    value = [0]
    for _ in range(1000):
        value = [value]
    assert "$truncated" in json.dumps(json_safe(value))
