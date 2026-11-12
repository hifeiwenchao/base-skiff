"""Bounded, read-only display codecs for Redis values.

Nothing here deserializes executable Python objects or changes a stored value.
``raw_bytes`` is the export path; previews and labels may be shortened for the UI.
MessagePack type markers are display metadata, not a format for writing values.
"""
from __future__ import annotations

import base64
import json
import math
from dataclasses import dataclass
from typing import Any

import msgpack

MAX_DECODE_BYTES = 1024 * 1024
MAX_PREVIEW_CHARS = 200_000
MAX_CONTAINER_ITEMS = 20_000
MAX_DEPTH = 64
MAX_LABEL_CHARS = 4096


@dataclass(frozen=True)
class ValuePreview:
    format: str
    text: str
    notice: str = ""


@dataclass
class _MapPairs:
    """Keep MessagePack duplicate, binary, and even container map keys intact."""

    pairs: list[tuple[Any, Any]]


class _PreviewLimit(ValueError):
    pass


def raw_bytes(value: str | bytes) -> bytes:
    """Return complete bytes without applying any preview limit."""
    if isinstance(value, bytes):
        return value
    return value.encode("utf-8", errors="surrogatepass")


def _escape(text: str, *, layout: bool, limit: int) -> tuple[str, bool]:
    parts = []
    length = 0
    shortened = False
    escapes = {"\n": r"\n", "\r": r"\r", "\t": r"\t"}
    for character in text:
        if character.isprintable() or (layout and character in "\n\t"):
            piece = character
        elif character in escapes:
            piece = escapes[character]
        else:
            code = ord(character)
            piece = f"\\x{code:02x}" if code <= 255 else (
                f"\\u{code:04x}" if code <= 65535 else f"\\U{code:08x}"
            )
        if length + len(piece) > limit:
            shortened = True
            break
        parts.append(piece)
        length += len(piece)
    return "".join(parts), shortened


def safe_text(value: str | bytes) -> str:
    """Produce a safe tree/table label while keeping raw identifiers elsewhere.

    A binary marker deliberately differs from decoded text. Labels are limited
    to 4,096 characters; never use these labels to address a Redis key/member.
    """
    if isinstance(value, bytes):
        try:
            decoded = value.decode("utf-8")
        except UnicodeDecodeError:
            decoded = None
        if decoded is None or any(not char.isprintable() for char in decoded):
            count = min(len(value), (MAX_LABEL_CHARS - 64) // 3)
            suffix = "; truncated" if count < len(value) else ""
            return f"<binary: {value[:count].hex(' ')}; {len(value)} bytes{suffix}>"
        value = decoded
    if len(value) <= MAX_LABEL_CHARS - 16 and (not value or value.isprintable()):
        return value
    text, shortened = _escape(value, layout=False, limit=MAX_LABEL_CHARS - 16)
    return text + ("… (truncated)" if shortened else "")


def _convert(
    value: Any, *, packed: bool, depth: int, budget: list[int], strict: bool,
) -> Any:
    if depth > MAX_DEPTH or budget[0] <= 0:
        if strict:
            raise _PreviewLimit("Structure exceeds preview depth or item limit.")
        return {"$truncated": "depth or item limit"}
    budget[0] -= 1
    if value is None or isinstance(value, (bool, str, int)):
        return value
    if isinstance(value, float):
        if math.isfinite(value):
            return value
        name = "NaN" if math.isnan(value) else ("Infinity" if value > 0 else "-Infinity")
        return {"$msgpack": "float", "value": name} if packed else {"$float": name}
    if isinstance(value, (bytes, bytearray, memoryview)):
        encoded = base64.b64encode(value).decode("ascii")
        return {"$msgpack": "binary", "base64": encoded} if packed else {"$binary": encoded}
    if isinstance(value, msgpack.Timestamp):
        return {"$msgpack": "timestamp", "seconds": value.seconds, "nanoseconds": value.nanoseconds}
    if isinstance(value, msgpack.ExtType):
        return {"$msgpack": "extension", "code": value.code,
                "base64": base64.b64encode(value.data).decode("ascii")}

    def convert(item: Any) -> Any:
        return _convert(item, packed=packed, depth=depth + 1, budget=budget, strict=strict)

    if isinstance(value, (dict, _MapPairs)):
        pairs = value.pairs if isinstance(value, _MapPairs) else value.items()
        size = len(value.pairs) if isinstance(value, _MapPairs) else len(value)
        if strict and size > MAX_CONTAINER_ITEMS:
            raise _PreviewLimit("Map exceeds preview item limit.")
        pairs = list(pairs)
        ordinary = all(isinstance(key, str) for key, _ in pairs)
        ordinary = ordinary and len({key for key, _ in pairs}) == len(pairs)
        if ordinary:
            result = {}
            for key, item in pairs:
                if budget[0] <= 0:
                    if strict:
                        raise _PreviewLimit("Map exceeds preview item limit.")
                    result["$truncated"] = "item limit"
                    break
                result[key] = convert(item)
            return result
        entries = []
        for key, item in pairs:
            if budget[0] <= 0:
                if strict:
                    raise _PreviewLimit("Map exceeds preview item limit.")
                entries.append({"$truncated": "item limit"})
                break
            entries.append([convert(key), convert(item)])
        return {"$msgpack": "map", "entries": entries} if packed else {"$map": entries}
    if isinstance(value, (list, tuple, set, frozenset)):
        if strict and len(value) > MAX_CONTAINER_ITEMS:
            raise _PreviewLimit("Array exceeds preview item limit.")
        result = []
        for item in value:
            if budget[0] <= 0:
                if strict:
                    raise _PreviewLimit("Array exceeds preview item limit.")
                result.append({"$truncated": "item limit"})
                break
            result.append(convert(item))
        return result
    # Preserve the existing JSON display of other database scalars such as
    # Decimal, datetime and BSON ObjectId; only binary values need new markers.
    return str(value)


def json_safe(value: Any) -> Any:
    """Return JSON data with explicit bytes/map markers and bounded nesting.

    Scalar bytes are complete Base64; this helper does not truncate export data.
    Containers deeper than 64 or exceeding 20,000 visited values carry an explicit
    ``$truncated`` marker. MessagePack-specific values retain their type markers.
    """
    return _convert(value, packed=False, depth=0, budget=[MAX_CONTAINER_ITEMS], strict=False)


def _formatted(value: Any) -> tuple[str, bool]:
    text = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)
    return _escape(text, layout=True, limit=MAX_PREVIEW_CHARS)


def _json(value: str) -> Any:
    # Reject excessive nesting before json.loads builds nested Python objects.
    depth = 0
    quoted = escaped = False
    for char in value:
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "[{":
            depth += 1
            if depth > MAX_DEPTH:
                raise _PreviewLimit("JSON exceeds preview depth limit.")
        elif char in "]}":
            depth -= 1

    def invalid_constant(value: str) -> None:
        raise ValueError("Non-JSON numeric constant")

    decoded = json.loads(value, parse_constant=invalid_constant)
    return _convert(decoded, packed=False, depth=0, budget=[MAX_CONTAINER_ITEMS], strict=True)


def _msgpack(value: bytes) -> Any:
    decoded = msgpack.unpackb(
        value, raw=False, strict_map_key=False, object_pairs_hook=_MapPairs,
        max_str_len=MAX_DECODE_BYTES, max_bin_len=MAX_DECODE_BYTES,
        max_ext_len=MAX_DECODE_BYTES, max_array_len=MAX_CONTAINER_ITEMS,
        max_map_len=MAX_CONTAINER_ITEMS,
    )
    return _convert(decoded, packed=True, depth=0, budget=[MAX_CONTAINER_ITEMS], strict=True)


def _hex(value: bytes, notice: str = "") -> ValuePreview:
    count = min(len(value), (MAX_PREVIEW_CHARS + 1) // 3)
    if count < len(value):
        notice = _notice(notice, "Preview truncated; export retains all bytes.")
    return ValuePreview("hex", value[:count].hex(" "), notice)


def _notice(*messages: str) -> str:
    return " ".join(message for message in messages if message)


def preview_value(value: str | bytes, format: str = "auto") -> ValuePreview:
    """Preview bytes as text/JSON/MessagePack/Hex/Base64 without changing them.

    Auto gives text precedence over MessagePack to avoid treating ordinary ASCII
    as fixints. Only a complete MessagePack single object is accepted. The binary
    fallback is always available, including when a manually chosen codec fails.
    """
    data = raw_bytes(value)
    if format not in {"auto", "text", "json", "msgpack", "hex", "base64"}:
        return _hex(data, "Unknown preview format; showing hexadecimal bytes.")
    if format == "hex":
        return _hex(data)
    if format == "base64":
        count = min(len(data), (MAX_PREVIEW_CHARS // 4) * 3)
        notice = "Preview truncated; export retains all bytes." if count < len(data) else ""
        return ValuePreview("base64", base64.b64encode(data[:count]).decode("ascii"), notice)

    oversized = len(data) > MAX_DECODE_BYTES
    notice = "Value exceeds decode limit (1 MiB)." if oversized else ""
    sample = data[:MAX_DECODE_BYTES]
    try:
        text = sample.decode("utf-8")
        utf8 = True
    except UnicodeDecodeError:
        text = sample.decode("utf-8", errors="backslashreplace")
        utf8 = False

    if format == "text":
        rendered, shortened = _escape(text, layout=True, limit=MAX_PREVIEW_CHARS)
        if not utf8 or rendered != text:
            notice = _notice(notice, "Invalid UTF-8 or control characters are shown as escapes.")
        if shortened or oversized:
            notice = _notice(notice, "Preview truncated; export retains all bytes.")
        return ValuePreview("text", rendered, notice)

    if format in {"auto", "json"} and not oversized and utf8:
        try:
            decoded = _json(text)
            rendered, shortened = _formatted(decoded)
            return ValuePreview("json", rendered,
                                "Preview truncated; export retains all bytes." if shortened else "")
        except (ValueError, TypeError, RecursionError, OverflowError):
            if format == "json":
                return _hex(data, "Unable to decode as JSON or structure exceeds preview limits; showing Hex.")
    elif format == "json":
        return _hex(data, _notice(notice, "Unable to decode as JSON; showing hexadecimal bytes."))

    if format == "auto" and utf8 and all(char.isprintable() or char in "\n\t" for char in text):
        rendered, shortened = _escape(text, layout=True, limit=MAX_PREVIEW_CHARS)
        if shortened or oversized:
            notice = _notice(notice, "Preview truncated; export retains all bytes.")
        return ValuePreview("text", rendered, notice)

    if not oversized:
        try:
            decoded = _msgpack(data)
            rendered, shortened = _formatted(decoded)
            return ValuePreview("msgpack", rendered,
                                "Preview truncated; export retains all bytes." if shortened else "")
        except (ValueError, TypeError, RecursionError, OverflowError, msgpack.UnpackException):
            if format == "msgpack":
                notice = "Unable to decode a complete MessagePack object or structure exceeds preview limits."
    return _hex(data, notice)
