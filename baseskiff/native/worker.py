"""Bounded JSONL RPC server. stdout is exclusively protocol output."""
from __future__ import annotations

import argparse
import base64
import inspect
import json
import sys
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import fields, is_dataclass
from pathlib import Path

from .. import redis_codec, sql_editor
from ..cancellation import CancellationToken, QueryCancelled
from ..data_transfer import CsvOptions, preview_csv
from ..models import (
    ColumnInfo,
    Connection,
    IndexInfo,
    QueryResult,
    RedisKeyContinuation,
    RedisValue,
    RedisValueContinuation,
)
from ..service import DatabaseService
from ..sql_structure import ConstraintInfo, StructureDraft
from .wire import decode, encode

VERSION = 1
MAX_MESSAGE_BYTES = 16 * 1024 * 1024
CHUNK_THRESHOLD_BYTES = 8 * 1024 * 1024
CHUNK_BYTES = 512 * 1024
MAX_RESPONSE_BYTES = 256 * 1024 * 1024


def response_frames(response):
    """Bound memory while framing large results; stdout writes provide backpressure.

    Spool the encoded response before publishing any chunk so an oversize result
    has one ordinary terminal error, never a silently truncated chunk stream.
    """
    encoder = json.JSONEncoder(ensure_ascii=True, allow_nan=False, separators=(',', ':'))
    with tempfile.SpooledTemporaryFile(max_size=CHUNK_THRESHOLD_BYTES, mode='w+b') as spool:
        size = 0
        for part in encoder.iterencode(response):
            encoded = part.encode('utf-8')
            size += len(encoded)
            if size > MAX_RESPONSE_BYTES:
                error = Worker._error(response.get('id'), ValueError(
                    'Response exceeds the 256 MiB limit; narrow the query or export to a file.'))
                yield encoder.encode(error).encode('utf-8')
                return
            spool.write(encoded)
        spool.seek(0)
        if size <= CHUNK_THRESHOLD_BYTES:
            yield spool.read()
            return
        index = 0
        while data := spool.read(CHUNK_BYTES):
            frame = {'version': VERSION, 'id': response.get('id'), 'chunk': {
                'index': index, 'data': base64.b64encode(data).decode('ascii'),
                'final': spool.tell() == size}}
            yield encoder.encode(frame).encode('utf-8')
            index += 1
# Explicit allowlist: names are never resolved from untrusted input with getattr.
SERVICE_METHODS = (
    'list_connections', 'save_connection', 'delete_connection', 'test_connection',
    'list_tables', 'list_redis_databases', 'set_redis_database', 'search_redis_keys',
    'read_redis_key', 'continue_redis_value', 'mutate_redis_entry', 'set_redis_ttl',
    'get_schema', 'list_indexes', 'list_constraints', 'rename_table', 'truncate_table',
    'add_column', 'drop_column', 'drop_table', 'create_index', 'drop_index',
    'fetch_rows', 'execute', 'explain', 'insert_row', 'update_row', 'delete_rows',
    'apply_grid_changes', 'export_csv', 'import_csv', 'list_history', 'count_history',
    'clear_history', 'load_console', 'save_console', 'list_console_documents',
    'save_console_document', 'delete_console_document', 'list_redis_search_history',
    'record_redis_search', 'set_redis_search_favorite', 'delete_redis_search_history',
)


class Worker:
    def __init__(self, data_dir=None, *, max_workers=4, max_pending=32, max_handles=256,
                 handle_ttl=1800):
        self.service = DatabaseService(data_dir)
        self.registry = {name: getattr(self.service, name) for name in SERVICE_METHODS}
        self.registry.update({
            'preview_value': redis_codec.preview_value,
            'encode_preview_value': redis_codec.encode_preview_value,
            'preview_csv': preview_csv,
            'csv_preview': self.csv_preview,
            'format_sql': sql_editor.format_sql,
            'current_statement': sql_editor.current_statement,
            'current_statement_span': sql_editor.current_statement_span,
            'completion_context': sql_editor.completion_context,
            'completion_scope': sql_editor.completion_scope,
            'completion_fragment': sql_editor.completion_fragment,
            'structure_preview': self.structure_preview,
            'apply_structure': self.apply_structure,
            'bson_to_text': self.bson_to_text,
            'bson_from_text': self.bson_from_text,
            'plan_nodes': self.plan_nodes,
        })
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix='native-db')
        self._slots = threading.BoundedSemaphore(max_pending)
        self._lock = threading.RLock()
        self._requests = {}
        self._handles = {}
        self.max_handles, self.handle_ttl = max_handles, handle_ttl

    def hello(self):
        return {'version': VERSION, 'application': 'BaseSkiff',
                'methods': sorted([*self.registry, 'hello', 'cancel', 'release']),
                'max_message_bytes': MAX_MESSAGE_BYTES,
                'chunked_responses': True, 'max_response_bytes': MAX_RESPONSE_BYTES,
                'qt_loaded': any(name.startswith(('PySide', 'PyQt')) for name in sys.modules)}

    @staticmethod
    def plan_nodes(result):
        from ..query_plan import normalize_query_plan
        return normalize_query_plan(QueryResult(**result))

    @staticmethod
    def csv_preview(source, options=None, limit=20):
        path = Path(source)
        before = path.stat()
        columns, rows = preview_csv(source, options, limit)
        after = path.stat()
        fingerprint = (after.st_size, after.st_mtime_ns)
        if fingerprint != (before.st_size, before.st_mtime_ns):
            raise ValueError('CSV changed during preview; preview the file again.')
        return {'columns': columns, 'rows': rows, 'stat': fingerprint}

    @staticmethod
    def bson_to_text(value):
        from bson import json_util
        return json_util.dumps(value, json_options=json_util.CANONICAL_JSON_OPTIONS,
                               ensure_ascii=True, indent=2)

    @staticmethod
    def bson_from_text(text):
        from bson import json_util
        return json_util.loads(text)

    def apply_structure(self, connection_id, table, columns, indexes=(), constraints=(), actions=()):
        # Match the existing Qt structure workflow: generated, individually
        # validated statements run through the service with history/read-only
        # checks. DDL may commit per statement, so never claim atomic rollback.
        connection = next((item for item in self.service.list_connections()
                           if item.id == connection_id), None)
        if connection is None:
            raise ValueError('This connection no longer exists.')
        preview = self.structure_preview(connection.kind, table, columns, indexes,
                                         constraints, actions)
        statements = preview['statements']
        completed = 0
        for statement in statements:
            try:
                self.service.execute(connection_id, statement)
            except Exception as error:
                raise RuntimeError(
                    f'Completed {completed}/{len(statements)} statements; remaining steps were not executed. '
                    f'Reopen to inspect the actual structure.\n{error}') from error
            completed += 1
        try:
            return {'completed': completed, 'total': len(statements),
                    'columns': self.service.get_schema(connection_id, table),
                    'indexes': self.service.list_indexes(connection_id, table),
                    'constraints': self.service.list_constraints(connection_id, table)}
        except Exception as error:
            raise RuntimeError(
                f'Completed {completed}/{len(statements)} statements; metadata refresh failed. '
                f'Reopen to inspect the actual structure.\n{error}') from error

    @staticmethod
    def structure_preview(kind, table, columns, indexes=(), constraints=(), actions=()):
        draft = StructureDraft(kind, table, [ColumnInfo(**v) for v in columns],
                               [IndexInfo(**v) for v in indexes],
                               [ConstraintInfo(**v) for v in constraints])
        operations = {'add_column': draft.add_column, 'alter_column': draft.alter_column,
                      'drop_column': draft.drop_column, 'create_index': draft.create_index,
                      'drop_index': draft.drop_index, 'create_constraint': draft.create_constraint,
                      'drop_constraint': draft.drop_constraint}
        for action in actions:
            if action['method'] not in operations:
                raise ValueError('Unknown structure action.')
            operations[action['method']](**action.get('params', {}))
        return {'statements': draft.statements, 'columns': draft.columns,
                'indexes': draft.indexes, 'constraints': draft.constraints}

    def _handle(self, value, scope):
        with self._lock:
            now = time.monotonic()
            self._handles = {key: item for key, item in self._handles.items()
                             if item[2] > now}
            if len(self._handles) >= self.max_handles:
                raise ValueError('Handle limit reached; release unused handles and refresh.')
            identifier = uuid.uuid4().hex
            self._handles[identifier] = (value, scope, now + self.handle_ttl)
        return {'$type': 'handle', 'value': identifier, 'kind': type(value).__name__}

    def _resolve(self, value, scope):
        if isinstance(value, dict):
            if value.get('$type') == 'handle':
                with self._lock:
                    item = self._handles.get(value.get('value'))
                if item is None or item[2] <= time.monotonic():
                    raise ValueError('Expired or unknown handle; refresh the view.')
                if item[1] != scope:
                    raise ValueError('Handle belongs to a different connection or database.')
                return item[0]
            return {key: self._resolve(item, scope) for key, item in value.items()}
        if isinstance(value, list):
            return [self._resolve(item, scope) for item in value]
        return value

    def _result(self, value, scope):
        if isinstance(value, (RedisKeyContinuation, RedisValueContinuation)):
            return self._handle(value, scope)
        if is_dataclass(value) and not isinstance(value, type):
            result = {field.name: self._result(getattr(value, field.name), scope) for field in fields(value)}
            if isinstance(value, RedisValue):
                result['snapshot'] = self._handle(value, scope)
            return result
        if isinstance(value, dict):
            # encode preserves binary/non-string keys and marker-shaped user dictionaries.
            if '$type' in value or not all(isinstance(key, str) for key in value):
                return encode(value)
            return {key: self._result(item, scope) for key, item in value.items()}
        if isinstance(value, (list, tuple, set, frozenset)):
            return [self._result(item, scope) for item in value]
        return encode(value)

    def call(self, method, params, token=None, progress=None):
        if method == 'hello':
            return self.hello()
        if method == 'release':
            identifiers = params.get('handles', [])
            if not isinstance(identifiers, list) or not all(isinstance(v, str) for v in identifiers):
                raise ValueError('handles must be an array of identifiers.')
            with self._lock:
                for identifier in identifiers:
                    self._handles.pop(identifier, None)
            return {'released': len(identifiers)}
        if method not in self.registry:
            raise ValueError(f'Unknown method: {method}')
        scope = (params.get('connection_id'), str(params.get('database')))
        arguments = self._resolve(dict(params), scope)
        if method in {'save_connection', 'test_connection'}:
            arguments['connection'] = Connection(**arguments['connection'])
        if method in {'import_csv', 'preview_csv', 'csv_preview'} and arguments.get('options') is not None:
            arguments['options'] = CsvOptions(**arguments['options'])
        operation = self.registry[method]
        signature = inspect.signature(operation)
        if any(key in arguments for key in ('cancel_token', 'progress')):
            raise ValueError('Worker-owned callbacks cannot be supplied by clients.')
        cancellation_supported = True
        if method == 'execute':
            connection = next((item for item in self.service.list_connections()
                               if item.id == arguments.get('connection_id')), None)
            cancellation_supported = connection is not None and connection.kind in {
                'SQLite', 'PostgreSQL', 'MySQL', 'Oracle'}
        if 'cancel_token' in signature.parameters and cancellation_supported:
            arguments['cancel_token'] = token or CancellationToken()
        if 'progress' in signature.parameters and progress is not None:
            arguments['progress'] = progress
        signature.bind(**arguments)
        if token is not None:
            token.raise_if_cancelled()
        return self._result(operation(**arguments), scope)

    @staticmethod
    def _error(identifier, exception):
        # Errors are sent only to the requesting UI. Never log queries, params or secrets.
        return {'version': VERSION, 'id': identifier,
                'error': {'message': str(exception), 'cancelled': isinstance(exception, QueryCancelled)}}

    def submit(self, request, send):
        identifier = request.get('id') if isinstance(request, dict) else None
        try:
            if not isinstance(request, dict) or request.get('version') != VERSION:
                raise ValueError('Unsupported protocol version.')
            if not isinstance(identifier, str) or not identifier or len(identifier) > 256:
                raise ValueError('Request id must be a nonempty string of at most 256 characters.')
            method, params = request.get('method'), request.get('params', {})
            if not isinstance(method, str) or not isinstance(params, dict):
                raise ValueError('method must be a string and params an object.')
            with self._lock:
                if identifier in self._requests:
                    raise ValueError('Duplicate active request id.')
            if method == 'cancel':
                with self._lock:
                    token = self._requests.get(params.get('request_id'))
                # Callback may enter a driver lock; keep stdin reader independent.
                accepted = token.cancel(dispatch=lambda callback: threading.Thread(
                    target=self._interrupt, args=(callback,), daemon=True).start()) if token else False
                send({'version': VERSION, 'id': identifier, 'result': {'accepted': accepted}})
                return
            if not self._slots.acquire(blocking=False):
                raise ValueError('Worker queue is full; retry after an active request completes.')
            token = CancellationToken()
            with self._lock:
                self._requests[identifier] = token
            def run():
                try:
                    def progress(*args, **kwargs):
                        send({'version': VERSION, 'id': identifier,
                              'progress': encode({'args': args, **kwargs})})
                    result = self.call(method, decode(params), token, progress)
                    response = {'version': VERSION, 'id': identifier, 'result': result}
                except Exception as exception:
                    response = self._error(identifier, exception)
                    if method in {'execute', 'explain'} and isinstance(params.get('query'), str):
                        from ..sql_diagnostics import structured_sql_error
                        response['error'].update(structured_sql_error(exception, params['query']))
                finally:
                    with self._lock:
                        self._requests.pop(identifier, None)
                    self._slots.release()
                send(response)
            self._executor.submit(run)
        except Exception as exception:
            send(self._error(identifier, exception))

    @staticmethod
    def _interrupt(callback):
        try:
            callback()
        except Exception:
            pass  # Driver cancellation races with natural completion.

    def close(self):
        self._executor.shutdown(wait=True)
        with self._lock:
            self._handles.clear()


def main(argv=None):
    parser = argparse.ArgumentParser(description='BaseSkiff native service worker')
    parser.add_argument('--data-dir')
    args = parser.parse_args(argv)
    worker = Worker(args.data_dir)
    output_lock = threading.Lock()
    def send(response):
        for payload in response_frames(response):
            with output_lock:
                try:
                    sys.stdout.buffer.write(payload + b'\n')
                    sys.stdout.buffer.flush()
                except BrokenPipeError:
                    return
    try:
        while True:
            line = sys.stdin.buffer.readline(MAX_MESSAGE_BYTES + 1)
            if not line:
                break
            if len(line) > MAX_MESSAGE_BYTES:
                send(Worker._error(None, ValueError('Request exceeds the 16 MiB limit.')))
                # Discard this frame without allocating an unbounded line.
                while line and not line.endswith(b'\n'):
                    line = sys.stdin.buffer.readline(MAX_MESSAGE_BYTES + 1)
                continue
            try:
                worker.submit(json.loads(line), send)
            except (ValueError, UnicodeError):
                send(Worker._error(None, ValueError('Invalid JSON request.')))
    finally:
        worker.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
