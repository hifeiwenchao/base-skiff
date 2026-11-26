"""Lossless JSON values. Never stringify unknown objects or coerce exact numbers."""
from __future__ import annotations

import base64
import math
import uuid
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import date, datetime, time
from decimal import Decimal

SAFE_INTEGER = 2**53 - 1
MAX_DEPTH = 100


def encode(value, _depth=0):
    if _depth > MAX_DEPTH:
        raise ValueError('Value nesting limit exceeded.')
    def child(item):
        return encode(item, _depth + 1)
    if value is None or isinstance(value, (str, bool)):
        return value
    # BSON Binary subclasses bytes; preserve its subtype before ordinary bytes.
    if type(value).__module__.startswith('bson'):
        from bson import json_util
        return {'$type': 'bson', 'value': json_util.dumps(value, json_options=json_util.CANONICAL_JSON_OPTIONS)}
    if isinstance(value, int):
        return value if -SAFE_INTEGER <= value <= SAFE_INTEGER else {'$type': 'integer', 'value': str(value)}
    if isinstance(value, float):
        return value if math.isfinite(value) else {'$type': 'float', 'value': repr(value)}
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {'$type': 'bytes', 'value': base64.b64encode(value).decode('ascii')}
    if isinstance(value, Decimal):
        return {'$type': 'decimal', 'value': str(value)}
    if isinstance(value, (datetime, date, time)):
        return {'$type': type(value).__name__, 'value': value.isoformat()}
    if isinstance(value, uuid.UUID):
        return {'$type': 'uuid', 'value': str(value)}
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: child(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        if all(isinstance(key, str) for key in value) and '$type' not in value:
            return {key: child(item) for key, item in value.items()}
        return {'$type': 'map', 'value': [[child(key), child(item)] for key, item in value.items()]}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [child(item) for item in value]
    raise TypeError(f'Unsupported wire value: {type(value).__name__}')


def decode(value, _depth=0):
    if _depth > MAX_DEPTH:
        raise ValueError('Value nesting limit exceeded.')
    def child(item):
        return decode(item, _depth + 1)
    if isinstance(value, list):
        return [child(item) for item in value]
    if not isinstance(value, dict):
        return value
    if '$type' not in value:
        return {key: child(item) for key, item in value.items()}
    kind, raw = value['$type'], value.get('value')
    if kind == 'bytes':
        return base64.b64decode(raw, validate=True)
    if kind == 'integer':
        return int(raw)
    if kind == 'decimal':
        return Decimal(raw)
    if kind == 'float':
        if raw not in {'nan', 'inf', '-inf'}:
            raise ValueError('Invalid nonfinite float.')
        return float(raw)
    if kind in {'datetime', 'date', 'time'}:
        return {'datetime': datetime, 'date': date, 'time': time}[kind].fromisoformat(raw)
    if kind == 'uuid':
        return uuid.UUID(raw)
    if kind == 'bson':
        from bson import json_util
        return json_util.loads(raw)
    if kind == 'map':
        return {child(key): child(item) for key, item in raw}
    if kind == 'handle':
        return value  # Resolution requires the owning worker and request scope.
    raise ValueError(f'Unknown wire type: {kind}')
