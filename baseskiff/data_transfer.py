"""Streaming CSV conversion and transactional import; no Qt dependencies."""
from __future__ import annotations

import codecs
import csv
import json
import math
import re
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal

from .cancellation import QueryCancelled
from .models import QueryResult

# Allow ordinary large TEXT cells while retaining a finite parser limit.
csv.field_size_limit(max(csv.field_size_limit(), 16 * 1024 * 1024))


@dataclass(frozen=True)
class CsvOptions:
    encoding: str = 'utf-8-sig'
    delimiter: str = ','
    null_value: str = '\\N'

    def validate(self):
        codecs.lookup(self.encoding)
        if len(self.delimiter) != 1 or self.delimiter in '\r\n"':
            raise ValueError('CSV delimiter must be a single non-quote character.')


def _read_csv_row(reader):
    try:
        return next(reader)
    except StopIteration:
        return None
    except (csv.Error, UnicodeError) as error:
        raise ValueError(f'CSV line {max(1, reader.line_num)}: {error}') from error


def _csv_rows(reader):
    while True:
        row = _read_csv_row(reader)
        if row is None:
            return
        yield row


def preview_csv(source, options=None, limit=20):
    options = options or CsvOptions()
    options.validate()
    with open(source, encoding=options.encoding, newline='') as stream:
        reader = csv.reader(stream, delimiter=options.delimiter, strict=True)
        header = _read_csv_row(reader) or []
        if not header:
            raise ValueError('CSV has no header row.')
        rows = []
        for row in _csv_rows(reader):
            if len(row) != len(header):
                raise ValueError(f'CSV line {reader.line_num}: expected {len(header)} fields, received {len(row)}.')
            if len(rows) >= limit:
                break
            rows.append(row)
        return header, rows


def convert_csv_value(value, column, options):
    if value == options.null_value:
        return None
    declaration = column.data_type.upper().strip()
    match = re.match(r'[A-Z][A-Z0-9_]*', declaration)
    kind = match.group() if match else declaration
    if 'BOOL' in kind:
        if value.lower() in {'true', '1', 'yes'}:
            return True
        if value.lower() in {'false', '0', 'no'}:
            return False
        raise ValueError('Expected true/false or 1/0.')
    if re.search(r'\b(?:TINYINT|SMALLINT|MEDIUMINT|BIGINT|INTEGER|INT|INT2|INT4|INT8)\b', kind):
        return int(value)
    if any(token in kind for token in ('NUMERIC', 'DECIMAL', 'NUMBER', 'MONEY')):
        result = Decimal(value)
        if not result.is_finite():
            raise ValueError('Expected a finite decimal.')
        return result
    if any(token in kind for token in ('REAL', 'FLOAT', 'DOUBLE')):
        result = float(value)
        if not math.isfinite(result):
            raise ValueError('Expected a finite number.')
        return result
    if 'TIMESTAMP' in kind or 'DATETIME' in kind:
        return datetime.fromisoformat(value)
    if kind == 'DATE':
        # Oracle DATE includes time; CSV export must round-trip it losslessly.
        return datetime.fromisoformat(value) if 'T' in value or ' ' in value.strip() else date.fromisoformat(value)
    if kind.startswith('TIME'):
        return time.fromisoformat(value)
    if kind in {'JSON', 'JSONB'}:
        json.loads(value)
    if any(token in kind for token in ('BLOB', 'BINARY', 'BYTEA', 'RAW')):
        raise ValueError('Binary columns require a dedicated binary editor; omit this column from mapping.')
    return value


def import_csv(adapter, source, *, table, mapping, options=None, cancel_token=None,
               progress=None, batch_size=500, expected_stat=None):
    from pathlib import Path

    from .adapters import require_writable

    require_writable(adapter.connection)
    options = options or CsvOptions()
    options.validate()
    if expected_stat is not None:
        stat = Path(source).stat()
        if (stat.st_size, stat.st_mtime_ns) != tuple(expected_stat):
            raise ValueError('CSV changed after preview. Open it again before importing.')
    if not mapping or any(not isinstance(index, int) or index < 0 for index in mapping):
        raise ValueError('Choose CSV columns to import.')
    if len(set(mapping.values())) != len(mapping):
        raise ValueError('Each target column may be mapped only once.')
    columns = {column.name: column for column in adapter.get_schema(table)}
    if not set(mapping.values()) <= columns.keys():
        raise ValueError('Unknown mapped table column.')
    selected = list(mapping.items())
    sql = f'INSERT INTO {adapter.table_sql(table)} ({", ".join(adapter.quote(name) for _, name in selected)}) VALUES ({", ".join(adapter.binds(len(selected)))})'
    count = 0
    callback = adapter.cancel
    if cancel_token is not None:
        cancel_token.raise_if_cancelled()
        cancel_token.bind(callback)
    try:
        with open(source, encoding=options.encoding, newline='') as stream:
            reader = csv.reader(stream, delimiter=options.delimiter, strict=True)
            header = _read_csv_row(reader) or []
            if not header or max(mapping) >= len(header):
                raise ValueError('CSV header does not match the mapping.')
            with adapter.session(cancel_token) as db:
                for row in _csv_rows(reader):
                    if cancel_token is not None:
                        cancel_token.raise_if_cancelled()
                    try:
                        if len(row) != len(header):
                            raise ValueError(f'Expected {len(header)} fields, received {len(row)}.')
                        values = [convert_csv_value(row[index], columns[name], options) for index, name in selected]
                        if adapter.kind == 'SQLite':
                            values = [value.isoformat() if isinstance(value, time) else value for value in values]
                        adapter._query(db, sql, values)
                    except QueryCancelled:
                        raise
                    except Exception as error:
                        if cancel_token is not None:
                            cancel_token.raise_if_cancelled()
                        raise ValueError(f'CSV line {reader.line_num}: {error}') from error
                    count += 1
                    if progress is not None and count % max(1, batch_size) == 0:
                        progress(count)
                if progress is not None:
                    progress(count)
                if cancel_token is not None:
                    cancel_token.raise_if_cancelled()
        return QueryResult(affected=count, total=count)
    finally:
        if cancel_token is not None:
            cancel_token.unbind(callback)
