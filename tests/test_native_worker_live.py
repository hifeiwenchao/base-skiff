"""Opt-in native bridge checks; only randomly named disposable objects are touched."""
import json
import os
import shlex
import sqlite3
import uuid
from pathlib import Path

import pytest

from baseskiff.native.wire import decode, encode
from baseskiff.native.worker import Worker

pytestmark = pytest.mark.integration


@pytest.fixture
def native_live(tmp_path):
    config_path = os.environ.get('BASESKIFF_LIVE_CONFIG')
    if not config_path:
        pytest.skip('Set BASESKIFF_LIVE_CONFIG to test real native worker databases')
    config = json.loads(Path(config_path).read_text())
    worker = Worker(tmp_path)
    def call(method, params=None):
        return decode(json.loads(json.dumps(worker.call(method, decode(encode(params or {}))))))
    try:
        yield config, worker, call
    finally:
        worker.close()


def save(call, config, kind, database=None):
    keys = {'PostgreSQL': 'postgresql', 'MySQL': 'mysql', 'MongoDB': 'mongo', 'Redis': 'redis', 'Oracle': 'oracle'}
    values = config[keys[kind]]
    return call('save_connection', {'connection': {
        'name': 'Native disposable ' + kind, 'kind': kind, 'host': config['host'],
        'port': values['port'], 'username': 'root' if kind == 'MySQL' else values.get('username', ''),
        'password': values['root_password'] if kind == 'MySQL' else values.get('password', ''),
        'database': database if database is not None else values.get('database', '')}})


@pytest.mark.parametrize('kind', ['SQLite', 'PostgreSQL', 'MySQL', 'MongoDB', 'Redis', 'Oracle'])
def test_native_worker_live_connection_crud(kind, native_live):
    config, worker, call = native_live
    name = 'baseskiff_native_' + uuid.uuid4().hex[:12]
    schema_created = False
    if kind == 'SQLite':
        connection = call('list_connections')[0]
    else:
        connection = save(call, config, kind, name if kind == 'MongoDB' else None)
    cid = connection['id']
    if kind == 'MySQL':
        call('execute', {'connection_id': cid, 'query': f'CREATE DATABASE `{name}`'})
        schema_created = True
        connection['database'] = name
        connection = call('save_connection', {'connection': connection})
    call('test_connection', {'connection': connection})
    call('list_tables', {'connection_id': cid})
    if kind == 'Redis':
        try:
            call('insert_row', {'connection_id': cid, 'table': name, 'values': {'key': name, 'type': 'string', 'value': 'native'}})
            assert call('read_redis_key', {'connection_id': cid, 'key': name})['value'] == 'native'
            call('mutate_redis_entry', {'connection_id': cid, 'key': name, 'kind': 'string', 'action': 'update', 'payload': {'value': 'changed', 'expected': 'native'}})
            assert call('read_redis_key', {'connection_id': cid, 'key': name})['value'] == 'changed'
        finally:
            call('execute', {'connection_id': cid, 'query': 'DEL ' + name})
        return
    if kind == 'MongoDB':
        from bson import Decimal128, ObjectId
        original = {'_id': ObjectId(), 'name': 'native', 'exact': Decimal128('1.234500')}
        try:
            call('insert_row', {'connection_id': cid, 'table': name, 'values': original})
            row = call('fetch_rows', {'connection_id': cid, 'table': name})['rows'][0]
            assert row == original
            text = call('bson_to_text', {'value': row})
            assert call('bson_from_text', {'text': text}) == original
            call('update_row', {'connection_id': cid, 'table': name, 'keys': {'_id': original['_id']}, 'values': {'name': 'changed'}})
            assert call('fetch_rows', {'connection_id': cid, 'table': name})['rows'][0]['name'] == 'changed'
            call('delete_rows', {'connection_id': cid, 'table': name, 'keys_list': [{'_id': original['_id']}]})
        finally:
            call('execute', {'connection_id': cid, 'query': json.dumps({'drop': name})})
        return
    quote = '`' if kind == 'MySQL' else '"'
    table_sql = quote + name + quote
    call('execute', {'connection_id': cid, 'query': f'CREATE TABLE {table_sql} ({quote}id{quote} INTEGER PRIMARY KEY, {quote}name{quote} VARCHAR(100))'})
    try:
        call('apply_grid_changes', {'connection_id': cid, 'table': name, 'inserts': [{'id': 1, 'name': 'native'}]})
        assert call('fetch_rows', {'connection_id': cid, 'table': name})['rows'] == [{'id': 1, 'name': 'native'}]
        call('apply_grid_changes', {'connection_id': cid, 'table': name, 'updates': [[{'id': 1}, {'name': 'changed'}]]})
        assert call('fetch_rows', {'connection_id': cid, 'table': name})['rows'][0]['name'] == 'changed'
        call('apply_grid_changes', {'connection_id': cid, 'table': name, 'deletes': [{'id': 1}]})
        assert not call('fetch_rows', {'connection_id': cid, 'table': name})['rows']
        assert call('list_history', {'connection_id': cid})
    finally:
        call('drop_table', {'connection_id': cid, 'table': name})
        if schema_created:
            call('execute', {'connection_id': cid, 'query': f'DROP DATABASE `{name}`'})


def test_native_worker_live_redis_codec_ttl_and_continuations(native_live):
    config, worker, call = native_live
    connection = save(call, config, 'Redis', '0')
    cid = connection['id']
    prefix = 'baseskiff_native_' + uuid.uuid4().hex[:12] + ':'
    keys = [prefix + str(index) for index in range(600)]
    keys += [prefix + 'encoded', prefix + 'hash']
    scope = {'connection_id': cid, 'database': '0'}
    try:
        call('execute', {**scope, 'query': 'MSET ' + ' '.join(shlex.quote(key) + ' value' for key in keys[:600])})
        first = call('search_redis_keys', {**scope, 'text': prefix, 'mode': 'prefix'})
        assert len(first['keys']) <= 500 and first['continuation']
        all_keys = {item['name'] for item in first['keys']}
        page = first
        while page['continuation']:
            page = call('search_redis_keys', {**scope, 'text': prefix, 'mode': 'prefix', 'continuation': page['continuation']})
            all_keys.update(item['name'] for item in page['keys'])
        assert set(keys[:600]) <= all_keys
        payload = b'{"before":1}'
        call('insert_row', {**scope, 'table': keys[-2], 'values': {'key': keys[-2], 'type': 'string', 'value': payload, 'ttl': 60}})
        encoded = call('encode_preview_value', {'original': payload, 'format': 'json', 'text': '{"after":2}'})
        call('mutate_redis_entry', {**scope, 'key': keys[-2], 'kind': 'string', 'action': 'update', 'payload': {'value_bytes': encoded, 'expected_bytes': payload}})
        value = call('read_redis_key', {**scope, 'key': keys[-2]})
        assert 0 < value['ttl_ms'] <= 60_000
        assert json.loads(call('preview_value', {'value': value['value']})['text']) == {'after': 2}
        call('execute', {**scope, 'query': 'HSET ' + keys[-1] + ' ' + ' '.join(f'f{i} v{i}' for i in range(1200))})
        value = call('read_redis_key', {**scope, 'key': keys[-1]})
        while value['continuation']:
            value = call('continue_redis_value', {**scope, 'snapshot': value['snapshot']})
        assert len(value['value']) == 1200
    finally:
        call('execute', {**scope, 'query': 'DEL ' + ' '.join(keys)})


@pytest.mark.parametrize('kind', ['SQLite', 'PostgreSQL', 'MySQL', 'MongoDB', 'Redis', 'Oracle'])
def test_native_worker_live_read_only_blocks_writes(kind, native_live):
    config, worker, call = native_live
    marker = 'baseskiff_native_' + uuid.uuid4().hex[:12]
    connection = call('list_connections')[0] if kind == 'SQLite' else save(call, config, kind)
    cid = connection['id']
    connection['read_only'] = True
    call('save_connection', {'connection': connection})
    query = (json.dumps({'insertOne': marker, 'document': {'name': marker}}) if kind == 'MongoDB'
             else 'SET ' + marker + ' rejected' if kind == 'Redis'
             else 'DELETE FROM customers' if kind == 'SQLite'
             else 'DELETE FROM ' + marker)
    try:
        with pytest.raises(sqlite3.DatabaseError if kind == 'SQLite' else PermissionError):
            call('execute', {'connection_id': cid, 'query': query})
        with pytest.raises(PermissionError):
            call('insert_row', {'connection_id': cid, 'table': marker,
                                'values': {'key': marker, 'value': 'rejected'} if kind == 'Redis' else {'name': marker}})
    finally:
        connection['read_only'] = False
        call('save_connection', {'connection': connection})
