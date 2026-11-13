"""Bounded Redis value previews and safe serialization round trips.

Preview decoding never executes application code or changes a stored value.
``raw_bytes`` is the export path, while ``encode_preview_value`` is the
explicit opt-in path used by the editor after the user submits decoded data.
Previews and labels may be shortened for the UI.
"""
from __future__ import annotations

import base64
import gzip
import json
import math
import struct
import zlib
from dataclasses import dataclass
from typing import Any

import msgpack
from bson import BSON, json_util
from bson.codec_options import CodecOptions, DatetimeConversion
from bson.errors import BSONError

MAX_DECODE_BYTES = 1024 * 1024
MAX_PREVIEW_CHARS = 200_000
MAX_CONTAINER_ITEMS = 20_000
MAX_DEPTH = 64
MAX_LABEL_CHARS = 4096
MAX_COMPRESSION_LAYERS = 4


@dataclass(frozen=True)
class ValuePreview:
    format: str
    text: str
    notice: str = ""
    chain: tuple[str, ...] = ()


@dataclass
class _MapPairs:
    """Keep duplicate, binary, and even container map keys intact."""

    pairs: list[tuple[Any, Any]]


@dataclass
class _CBORTag:
    tag: int
    value: Any


@dataclass
class _CBORSimple:
    value: int


class _PreviewLimit(ValueError):
    pass


def raw_bytes(value: str | bytes) -> bytes:
    """Return complete bytes without applying any preview limit."""
    if isinstance(value, bytes):
        # ``bson.BSON`` subclasses bytes and overloads ``decode`` with BSON
        # decoding. Convert subclasses so all codecs see the regular bytes API.
        return value if type(value) is bytes else bytes(value)
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
        value = raw_bytes(value)
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
    value: Any, *, packed: bool | str, depth: int, budget: list[int], strict: bool,
) -> Any:
    if depth > MAX_DEPTH or budget[0] <= 0:
        if strict:
            raise _PreviewLimit("Structure exceeds preview depth or item limit.")
        return {"$truncated": "depth or item limit"}
    budget[0] -= 1
    marker = "$msgpack" if packed is True else (f"${packed}" if packed else "")
    if value is None or isinstance(value, (bool, str, int)):
        return value
    if isinstance(value, float):
        if math.isfinite(value):
            return value
        name = "NaN" if math.isnan(value) else ("Infinity" if value > 0 else "-Infinity")
        return {marker: "float", "value": name} if marker else {"$float": name}
    if isinstance(value, (bytes, bytearray, memoryview)):
        encoded = base64.b64encode(value).decode("ascii")
        return {marker: "binary", "base64": encoded} if marker else {"$binary": encoded}
    if isinstance(value, msgpack.Timestamp):
        return {"$msgpack": "timestamp", "seconds": value.seconds, "nanoseconds": value.nanoseconds}
    if isinstance(value, msgpack.ExtType):
        return {"$msgpack": "extension", "code": value.code,
                "base64": base64.b64encode(value.data).decode("ascii")}

    def convert(item: Any) -> Any:
        return _convert(item, packed=packed, depth=depth + 1, budget=budget, strict=strict)

    if isinstance(value, _CBORTag):
        return {"$cbor": "tag", "tag": value.tag, "value": convert(value.value)}
    if isinstance(value, _CBORSimple):
        return {"$cbor": "undefined"} if value.value == 23 else {
            "$cbor": "simple", "value": value.value,
        }
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
        return {marker: "map", "entries": entries} if marker else {"$map": entries}
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


class _CBORReader:
    """Read CBOR data without semantic tag hooks or declared-length allocation.

    Maps retain their original pairs. Tags remain data, including shared-value
    tags, so a reference or an application tag can never invoke custom code.
    """

    def __init__(self, data: bytes):
        self.data = data
        self.position = 0
        self.remaining = MAX_CONTAINER_ITEMS

    def take(self, length: int) -> bytes:
        if length < 0 or length > len(self.data) - self.position:
            raise ValueError("Incomplete CBOR value.")
        start = self.position
        self.position += length
        return self.data[start:self.position]

    def argument(self, info: int) -> int | None:
        if info < 24:
            return info
        if info in (24, 25, 26, 27):
            return int.from_bytes(self.take(1 << (info - 24)), "big")
        if info == 31:
            return None
        raise ValueError("Reserved CBOR additional information.")

    def at_break(self) -> bool:
        if self.position >= len(self.data):
            raise ValueError("Incomplete indefinite CBOR container.")
        if self.data[self.position] != 255:
            return False
        self.position += 1
        return True

    def read(self, depth: int = 0) -> Any:
        if depth > MAX_DEPTH or self.remaining <= 0:
            raise _PreviewLimit("CBOR exceeds preview depth or item limit.")
        self.remaining -= 1
        initial = self.take(1)[0]
        major, info = initial >> 5, initial & 31
        if major == 7 and info in (25, 26, 27):
            width, code = {25: (2, ">e"), 26: (4, ">f"), 27: (8, ">d")}[info]
            return struct.unpack(code, self.take(width))[0]
        argument = self.argument(info)
        if argument is None and major not in (2, 3, 4, 5):
            raise ValueError("Unexpected CBOR break or indefinite scalar.")
        if major in (0, 1):
            return argument if major == 0 else -1 - argument
        if major in (2, 3):
            if argument is not None:
                data = self.take(argument)
                return data if major == 2 else data.decode("utf-8")
            chunks = []
            while not self.at_break():
                initial = self.data[self.position]
                if initial >> 5 != major or initial & 31 == 31:
                    raise ValueError("Invalid indefinite CBOR string chunk.")
                chunks.append(self.read(depth + 1))
            return b"".join(chunks) if major == 2 else "".join(chunks)
        if major in (4, 5):
            if argument is not None and argument * (2 if major == 5 else 1) > self.remaining:
                raise _PreviewLimit("CBOR container exceeds preview item limit.")
            items = []
            while (not self.at_break()) if argument is None else len(items) < argument:
                key = self.read(depth + 1)
                items.append((key, self.read(depth + 1)) if major == 5 else key)
            return _MapPairs(items) if major == 5 else items
        if major == 6:
            return _CBORTag(argument, self.read(depth + 1))
        if info == 24 and argument < 32:
            raise ValueError("Reserved or non-well-formed CBOR simple value.")
        if argument == 20:
            return False
        if argument == 21:
            return True
        if argument == 22:
            return None
        return _CBORSimple(argument)


def _cbor(value: bytes) -> Any:
    reader = _CBORReader(value)
    decoded = reader.read()
    if reader.position != len(value):
        raise ValueError("Trailing bytes after CBOR object.")
    return _convert(decoded, packed="cbor", depth=0, budget=[MAX_CONTAINER_ITEMS], strict=True)


class _BSONScanner:
    """Validate bounds and loss-prone BSON structures before native decoding."""

    def __init__(self, data: bytes):
        self.data = data
        self.position = 0
        self.remaining = MAX_CONTAINER_ITEMS

    def take(self, length: int, end: int) -> bytes:
        if length < 0 or length > end - self.position:
            raise ValueError("Incomplete BSON value.")
        start = self.position
        self.position += length
        return self.data[start:self.position]

    def integer(self, end: int) -> int:
        return int.from_bytes(self.take(4, end), "little", signed=True)

    def cstring(self, end: int) -> bytes:
        stop = self.data.find(b"\x00", self.position, end)
        if stop < 0:
            raise ValueError("Unterminated BSON name.")
        return self.take(stop + 1 - self.position, end)[:-1]

    def string(self, end: int) -> None:
        length = self.integer(end)
        if length < 1 or self.take(length, end)[-1] != 0:
            raise ValueError("Invalid BSON string.")

    def document(self, end: int, depth: int = 0, *, array: bool = False) -> None:
        if depth > MAX_DEPTH or self.remaining <= 0:
            raise _PreviewLimit("BSON exceeds preview depth or item limit.")
        self.remaining -= 1
        start = self.position
        size = self.integer(end)
        if size < 5 or size > end - start or self.data[start + size - 1] != 0:
            raise ValueError("Invalid BSON document length.")
        end = start + size - 1
        names = set()
        fixed = {1: 8, 6: 0, 7: 12, 8: 1, 9: 8, 10: 0, 16: 4, 17: 8, 18: 8, 19: 16,
                 127: 0, 255: 0}
        while self.position < end:
            kind = self.take(1, end)[0]
            name = self.cstring(end)
            if name in names or (array and name != str(len(names)).encode("ascii")):
                raise ValueError("Duplicate BSON fields or nonsequential array keys cannot be shown losslessly.")
            names.add(name)
            if self.remaining <= 0:
                raise _PreviewLimit("BSON exceeds preview item limit.")
            self.remaining -= 1
            if kind in fixed:
                self.take(fixed[kind], end)
            elif kind in (2, 13, 14):
                self.string(end)
            elif kind == 12:
                self.string(end)
                self.take(12, end)
            elif kind in (3, 4):
                self.document(end, depth + 1, array=kind == 4)
            elif kind == 5:
                length = self.integer(end)
                self.take(1, end)  # subtype; the BSON decoder validates old binary subtypes
                self.take(length, end)
            elif kind == 11:
                self.cstring(end)
                self.cstring(end)
            elif kind == 15:
                scope_start = self.position
                scope_size = self.integer(end)
                if scope_size < 14 or scope_size > end - scope_start:
                    raise ValueError("Invalid BSON code-with-scope length.")
                scope_end = scope_start + scope_size
                self.string(scope_end)
                self.document(scope_end, depth + 1)
                if self.position != scope_end:
                    raise ValueError("Trailing bytes in BSON code scope.")
            else:
                # Undefined, Symbol and DBPointer lose their original BSON type
                # in PyMongo/Extended JSON. Keep their bytes visible in Hex.
                raise ValueError("Unsupported or loss-prone legacy BSON type.")
        self.take(1, end + 1)


def _bson(value: bytes) -> Any:
    scanner = _BSONScanner(value)
    scanner.document(len(value))
    if scanner.position != len(value):
        raise ValueError("Trailing bytes after BSON document.")
    decoded = BSON(value).decode(CodecOptions(datetime_conversion=DatetimeConversion.DATETIME_AUTO))
    # Canonical Extended JSON preserves Int64, Decimal128, ObjectId, dates,
    # binary subtypes, timestamps and code as data rather than Python reprs.
    return json.loads(json_util.dumps(decoded, json_options=json_util.CANONICAL_JSON_OPTIONS))


def _compression_format(value: bytes) -> str:
    if value.startswith(b"\x1f\x8b"):
        return "gzip"
    if len(value) >= 2 and value[0] & 15 == 8 and value[0] >> 4 <= 7:
        if int.from_bytes(value[:2], "big") % 31 == 0:
            return "zlib"
    return ""


def _decompress(value: bytes, format: str) -> bytes:
    decoder = zlib.decompressobj(31 if format == "gzip" else 15)
    decoded = decoder.decompress(value, MAX_DECODE_BYTES + 1)
    if len(decoded) > MAX_DECODE_BYTES or decoder.unconsumed_tail:
        raise _PreviewLimit("Decompressed value exceeds decode limit (1 MiB).")
    if not decoder.eof or decoder.unused_data:
        raise ValueError("Expected one complete compressed stream with no trailing bytes.")
    return decoded


def _hex(value: bytes, notice: str = "") -> ValuePreview:
    count = min(len(value), (MAX_PREVIEW_CHARS + 1) // 3)
    if count < len(value):
        notice = _notice(notice, "Preview truncated; export retains all bytes.")
    return ValuePreview("hex", value[:count].hex(" "), notice)


def _notice(*messages: str) -> str:
    return " ".join(message for message in messages if message)


def _render_decoded(kind: str, decoded: Any) -> ValuePreview:
    rendered, shortened = _formatted(decoded)
    return ValuePreview(kind, rendered,
                        "Preview truncated; export retains all bytes." if shortened else "", (kind,))


def _preview_uncompressed(data: bytes, requested: str, *, auto: bool = False) -> ValuePreview:
    """Decode one uncompressed object. The caller owns compression chain metadata."""
    oversized = len(data) > MAX_DECODE_BYTES
    notice = "Value exceeds decode limit (1 MiB)." if oversized else ""
    if requested in {"hex", "base64"}:
        return preview_value(data, requested)
    if oversized:
        return _hex(data, notice)
    try:
        text = data.decode("utf-8")
        utf8 = True
    except UnicodeDecodeError:
        text = data.decode("utf-8", errors="backslashreplace")
        utf8 = False

    if requested == "text":
        rendered, shortened = _escape(text, layout=True, limit=MAX_PREVIEW_CHARS)
        if not utf8 or rendered != text:
            notice = _notice(notice, "Invalid UTF-8 or control characters are shown as escapes.")
        if shortened:
            notice = _notice(notice, "Preview truncated; export retains all bytes.")
        return ValuePreview("text", rendered, notice, ("text",))

    if requested in {"auto", "json"} and utf8:
        try:
            return _render_decoded("json", _json(text))
        except (ValueError, TypeError, RecursionError, OverflowError):
            if requested == "json":
                return _hex(data, "Unable to decode as JSON or structure exceeds preview limits; showing Hex.")
    if requested == "auto" and utf8 and all(char.isprintable() or char in "\n\t" for char in text):
        rendered, shortened = _escape(text, layout=True, limit=MAX_PREVIEW_CHARS)
        if shortened:
            notice = _notice(notice, "Preview truncated; export retains all bytes.")
        return ValuePreview("text", rendered, notice, ("text",))

    decoders: list[tuple[str, Any]] = []
    if requested in {"auto", "bson"}:
        decoders.append(("bson", _bson))
    if requested in {"auto", "cbor"}:
        decoders.append(("cbor", _cbor))
    if requested in {"auto", "msgpack"}:
        decoders.append(("msgpack", _msgpack))
    errors: list[str] = []
    for kind, decoder in decoders:
        try:
            # A CBOR/MessagePack one-byte scalar has no identifying marker;
            # auto leaves such values in Hex to avoid claiming the wrong type.
            if requested == "auto" and len(data) == 1 and data[0] >= 0xF0:
                continue
            decoded = decoder(data)
            return _render_decoded(kind, decoded)
        except (ValueError, TypeError, RecursionError, OverflowError, BSONError,
                msgpack.UnpackException, _PreviewLimit) as exc:
            errors.append(str(exc))
    if requested in {"bson", "cbor", "msgpack"}:
        return _hex(data, f"Unable to decode as {requested.upper()} or structure exceeds preview limits; showing Hex.")
    if requested == "json":
        return _hex(data, "Unable to decode as JSON or structure exceeds preview limits; showing Hex.")
    ambiguous = "Ambiguous binary scalar; choose JSON/MessagePack/CBOR explicitly."
    return _hex(data, _notice(notice, ambiguous if len(data) == 1 and data[0] >= 0xF0 else
                              "Unable to identify a complete serialized object; showing Hex."))


def preview_value(value: str | bytes, format: str = "auto") -> ValuePreview:
    """Preview serialized Redis bytes without changing or executing them.

    Auto recognizes complete JSON, BSON, CBOR, MessagePack and bounded gzip/zlib
    streams. Compression formats are retained as the outer ``format`` and the
    inner codecs are exposed in ``chain`` for the UI label.
    """
    data = raw_bytes(value)
    allowed = {"auto", "text", "json", "msgpack", "bson", "cbor", "gzip", "zlib", "hex", "base64"}
    if format not in allowed:
        return _hex(data, "Unknown preview format; showing hexadecimal bytes.")
    if format == "hex":
        return _hex(data)
    if format == "base64":
        count = min(len(data), (MAX_PREVIEW_CHARS // 4) * 3)
        notice = "Preview truncated; export retains all bytes." if count < len(data) else ""
        return ValuePreview("base64", base64.b64encode(data[:count]).decode("ascii"), notice, ("base64",))

    compressed = format if format in {"gzip", "zlib"} else (_compression_format(data) if format == "auto" else "")
    if compressed:
        try:
            layers = [compressed]
            inner_data = _decompress(data, compressed)
            while len(layers) < MAX_COMPRESSION_LAYERS:
                nested = _compression_format(inner_data)
                if not nested:
                    break
                layers.append(nested)
                inner_data = _decompress(inner_data, nested)
            if _compression_format(inner_data):
                raise _PreviewLimit("Compressed value exceeds nested decode limit.")
            inner = _preview_uncompressed(inner_data, "auto", auto=True)
            chain = tuple(layers) + (inner.chain or (inner.format,))
            if inner.format == "hex":
                return ValuePreview(compressed, inner.text, _notice(inner.notice,
                    "Compressed contents are not a recognized complete serialization."), chain)
            return ValuePreview(compressed, inner.text, inner.notice, chain)
        except (_PreviewLimit, ValueError, zlib.error, OverflowError) as exc:
            if format in {"gzip", "zlib"}:
                prefix = "Value exceeds decode limit (1 MiB)." if len(data) > MAX_DECODE_BYTES else ""
                return _hex(data, _notice(prefix, f"Unable to decode complete {compressed.upper()} stream: {exc}"))
            return _hex(data, f"Unable to decode complete {compressed.upper()} stream: {exc}")

    if format in {"gzip", "zlib"}:
        return _hex(data, f"Unable to decode complete {format.upper()} stream.")
    return _preview_uncompressed(data, format)


def _edited_json(text: str) -> Any:
    """Parse an editor value with the same bounds used by the preview path."""
    if not isinstance(text, str):
        raise ValueError("Edited value must be text.")
    if len(text.encode("utf-8", errors="surrogatepass")) > MAX_DECODE_BYTES:
        raise ValueError("Edited value exceeds the 1 MiB limit.")
    try:
        return _json(text)
    except (ValueError, TypeError, RecursionError, OverflowError) as exc:
        raise ValueError(f"Invalid JSON value: {exc}") from exc


def _decode_edit_marker(value: Any, kind: str) -> Any:
    """Turn preview markers back into values understood by a writer."""
    if isinstance(value, list):
        return [_decode_edit_marker(item, kind) for item in value]
    if not isinstance(value, dict):
        return value

    marker = value.get(f"${kind}")
    if kind == "msgpack" and marker == "binary":
        encoded = value.get("base64")
        if not isinstance(encoded, str):
            raise ValueError("MessagePack binary marker needs a Base64 string.")
        try:
            return base64.b64decode(encoded, validate=True)
        except (ValueError, base64.binascii.Error) as exc:
            raise ValueError("MessagePack binary marker has invalid Base64.") from exc
    if kind == "msgpack" and marker == "extension":
        code, encoded = value.get("code"), value.get("base64")
        if not isinstance(code, int) or isinstance(code, bool) or not -128 <= code <= 127:
            raise ValueError("MessagePack extension code must be between -128 and 127.")
        if not isinstance(encoded, str):
            raise ValueError("MessagePack extension marker needs a Base64 string.")
        try:
            return msgpack.ExtType(code, base64.b64decode(encoded, validate=True))
        except (ValueError, base64.binascii.Error) as exc:
            raise ValueError("MessagePack extension marker has invalid Base64.") from exc
    if kind == "msgpack" and marker == "timestamp":
        seconds, nanos = value.get("seconds"), value.get("nanoseconds")
        if (isinstance(seconds, bool) or not isinstance(seconds, int) or
                isinstance(nanos, bool) or not isinstance(nanos, int) or not 0 <= nanos < 1_000_000_000):
            raise ValueError("MessagePack timestamp is invalid.")
        try:
            return msgpack.Timestamp(seconds, nanos)
        except (ValueError, OverflowError) as exc:
            raise ValueError("MessagePack timestamp is out of range.") from exc
    if kind == "msgpack" and marker == "float":
        names = {"NaN": math.nan, "Infinity": math.inf, "-Infinity": -math.inf}
        if value.get("value") not in names:
            raise ValueError("MessagePack float marker is invalid.")
        return names[value["value"]]
    if kind == "msgpack" and marker == "map":
        entries = value.get("entries")
        if not isinstance(entries, list):
            raise ValueError("MessagePack map marker needs an entries array.")
        return _MapPairs([
            (_decode_edit_marker(pair[0], kind), _decode_edit_marker(pair[1], kind))
            for pair in entries if isinstance(pair, list) and len(pair) == 2
        ]) if all(isinstance(pair, list) and len(pair) == 2 for pair in entries) else _invalid_marker()
    if kind == "cbor" and marker == "binary":
        encoded = value.get("base64")
        if not isinstance(encoded, str):
            raise ValueError("CBOR binary marker needs a Base64 string.")
        try:
            return base64.b64decode(encoded, validate=True)
        except (ValueError, base64.binascii.Error) as exc:
            raise ValueError("CBOR binary marker has invalid Base64.") from exc
    if kind == "cbor" and marker == "tag":
        tag, nested = value.get("tag"), value.get("value")
        if isinstance(tag, bool) or not isinstance(tag, int) or tag < 0:
            raise ValueError("CBOR tag must be a non-negative integer.")
        return _CBORTag(tag, _decode_edit_marker(nested, kind))
    if kind == "cbor" and marker == "simple":
        simple = value.get("value")
        if isinstance(simple, bool) or not isinstance(simple, int) or not 0 <= simple <= 255:
            raise ValueError("CBOR simple value is invalid.")
        return _CBORSimple(simple)
    if kind == "cbor" and marker == "undefined":
        return _CBORSimple(23)
    if kind == "cbor" and marker == "float":
        names = {"NaN": math.nan, "Infinity": math.inf, "-Infinity": -math.inf}
        if value.get("value") not in names:
            raise ValueError("CBOR float marker is invalid.")
        return names[value["value"]]
    if kind == "cbor" and marker == "map":
        entries = value.get("entries")
        if not isinstance(entries, list) or not all(isinstance(pair, list) and len(pair) == 2 for pair in entries):
            raise ValueError("CBOR map marker needs an entries array.")
        return _MapPairs([(_decode_edit_marker(pair[0], kind), _decode_edit_marker(pair[1], kind))
                          for pair in entries])

    return {key: _decode_edit_marker(item, kind) for key, item in value.items()}


def _invalid_marker() -> Any:
    raise ValueError("Serialized map marker contains an invalid entry.")


def _pack_msgpack(value: Any) -> bytes:
    packer = msgpack.Packer(use_bin_type=True)

    def pack(item: Any) -> bytes:
        if isinstance(item, _MapPairs):
            return packer.pack_map_header(len(item.pairs)) + b"".join(
                pack(key) + pack(element) for key, element in item.pairs)
        if isinstance(item, dict):
            return packer.pack_map_header(len(item)) + b"".join(
                pack(key) + pack(element) for key, element in item.items())
        if isinstance(item, (list, tuple)):
            return packer.pack_array_header(len(item)) + b"".join(pack(element) for element in item)
        if isinstance(item, (set, frozenset)):
            return packer.pack_array_header(len(item)) + b"".join(pack(element) for element in item)
        try:
            return packer.pack(item)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"Unsupported MessagePack value: {exc}") from exc

    return pack(value)


def _cbor_header(major: int, value: int) -> bytes:
    if value < 0:
        raise ValueError("CBOR lengths and tags cannot be negative.")
    if value < 24:
        return bytes([(major << 5) | value])
    if value <= 0xFF:
        return bytes([(major << 5) | 24, value])
    if value <= 0xFFFF:
        return bytes([(major << 5) | 25]) + struct.pack(">H", value)
    if value <= 0xFFFFFFFF:
        return bytes([(major << 5) | 26]) + struct.pack(">I", value)
    if value <= 0xFFFFFFFFFFFFFFFF:
        return bytes([(major << 5) | 27]) + struct.pack(">Q", value)
    raise ValueError("CBOR integer or length is too large.")


def _pack_cbor(value: Any) -> bytes:
    if value is None:
        return b"\xf6"
    if value is False:
        return b"\xf4"
    if value is True:
        return b"\xf5"
    if isinstance(value, int) and not isinstance(value, bool):
        return _cbor_header(0, value) if value >= 0 else _cbor_header(1, -1 - value)
    if isinstance(value, float):
        return b"\xfb" + struct.pack(">d", value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        data = bytes(value)
        return _cbor_header(2, len(data)) + data
    if isinstance(value, str):
        data = value.encode("utf-8")
        return _cbor_header(3, len(data)) + data
    if isinstance(value, _CBORTag):
        return _cbor_header(6, value.tag) + _pack_cbor(value.value)
    if isinstance(value, _CBORSimple):
        if value.value < 24:
            return bytes([0xE0 | value.value])
        if value.value <= 0xFF:
            return b"\xf8" + bytes([value.value])
        raise ValueError("CBOR simple value must be between 0 and 255.")
    if isinstance(value, _MapPairs):
        pairs = value.pairs
        return _cbor_header(5, len(pairs)) + b"".join(_pack_cbor(key) + _pack_cbor(item) for key, item in pairs)
    if isinstance(value, dict):
        return _cbor_header(5, len(value)) + b"".join(_pack_cbor(key) + _pack_cbor(item)
                                                     for key, item in value.items())
    if isinstance(value, (list, tuple, set, frozenset)):
        return _cbor_header(4, len(value)) + b"".join(_pack_cbor(item) for item in value)
    raise ValueError(f"Unsupported CBOR value: {type(value).__name__}")


def _encode_inner(text: str, kind: str) -> bytes:
    if kind == "text":
        try:
            return text.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ValueError("Text contains characters that cannot be encoded as UTF-8.") from exc
    if kind == "json":
        return json.dumps(_edited_json(text), ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if kind == "msgpack":
        return _pack_msgpack(_decode_edit_marker(_edited_json(text), "msgpack"))
    if kind == "cbor":
        return _pack_cbor(_decode_edit_marker(_edited_json(text), "cbor"))
    if kind == "bson":
        try:
            decoded = json_util.loads(text)
            if not isinstance(decoded, dict):
                raise ValueError("BSON root must be a JSON object.")
            return BSON.encode(decoded)
        except (ValueError, TypeError, BSONError, RecursionError, OverflowError) as exc:
            raise ValueError(f"Invalid BSON Extended JSON: {exc}") from exc
    raise ValueError(f"Format {kind!r} is not editable.")


def encode_preview_value(original: str | bytes, format: str | tuple[str, ...], text: str) -> bytes:
    """Encode an edited preview while retaining the detected serialization chain.

    ``format`` may be a preview chain such as ``("gzip", "msgpack")`` or a
    single format name. ``auto`` derives the chain from ``original``. Hex,
    Base64 and unknown/failed previews intentionally remain read-only.
    """
    raw = raw_bytes(original)
    if isinstance(format, (tuple, list)):
        chain = tuple(format)
    elif format == "auto":
        detected = preview_value(raw, "auto")
        chain = detected.chain or (detected.format,)
    elif format in {"gzip", "zlib"}:
        detected = preview_value(raw, format)
        chain = detected.chain or (detected.format,)
    else:
        chain = (format,)
    if not chain or chain[-1] in {"hex", "base64", "auto"}:
        raise ValueError("This value does not have an editable serialization format.")
    if len(chain) > MAX_COMPRESSION_LAYERS + 1:
        raise ValueError("Serialization chain is too deep to edit safely.")
    if any(layer not in {"gzip", "zlib"} for layer in chain[:-1]):
        raise ValueError("Unsupported serialization wrapper.")
    data = _encode_inner(text, chain[-1])
    if len(data) > MAX_DECODE_BYTES:
        raise ValueError("Encoded value exceeds the 1 MiB limit.")
    for layer in reversed(chain[:-1]):
        data = gzip.compress(data, mtime=0) if layer == "gzip" else zlib.compress(data)
        if len(data) > MAX_DECODE_BYTES:
            raise ValueError("Encoded compressed value exceeds the 1 MiB limit.")
    return data
