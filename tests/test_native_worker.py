import json
import subprocess
import sys
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from baseskiff.native.wire import decode, encode
from baseskiff.native.worker import Worker


def test_lossless_values_and_marker_collision():
    value = {b'key': [b'\x00\xff', 2**63, Decimal('1.000000000000001'), date(2026, 1, 2),
                       datetime(2026, 1, 2, tzinfo=timezone.utc)], '$type': 'ordinary'}
    assert decode(json.loads(json.dumps(encode(value)))) == value
    collision = {'$type': 'bytes', 'value': 'not binary'}
    assert decode(encode(collision)) == collision


def test_real_sqlite_crud_history_and_whitelist(tmp_path):
    worker = Worker(tmp_path)
    connection = worker.call('list_connections', {})[0]
    cid = connection['id']
    assert not connection['password']
    worker.call('execute', {'connection_id': cid, 'query': 'CREATE TABLE native_test (id INTEGER PRIMARY KEY, value BLOB)'})
    worker.call('apply_grid_changes', {'connection_id': cid, 'table': 'native_test', 'inserts': [{'id': 1, 'value': b'\xff\x00'}]})
    result = worker.call('fetch_rows', {'connection_id': cid, 'table': 'native_test'})
    assert decode(result)['rows'] == [{'id': 1, 'value': b'\xff\x00'}]
    assert worker.call('list_history', {'connection_id': cid})
    with pytest.raises(ValueError, match='Unknown method'):
        worker.call('_connection', {'connection_id': cid})
    worker.close()


def test_bson_roundtrip():
    from bson import Binary, Decimal128, ObjectId, Regex, Timestamp
    values = [ObjectId(), Decimal128('1.234500'), Binary(b'\x00', 128), Regex('a', 'i'), Timestamp(1, 2)]
    decoded = decode(json.loads(json.dumps(encode(values))))
    assert decoded == values
    assert type(decoded[2]) is Binary
    assert decoded[2].subtype == 128


def test_subprocess_protocol_eof_and_no_qt(tmp_path):
    requests = [dict(version=1, id='hello', method='hello', params={}),
                dict(version=1, id='list', method='list_connections', params={}),
                dict(version=1, id='bad', method='__dict__', params={})]
    proc = subprocess.run([sys.executable, '-m', 'baseskiff.native.worker', '--data-dir', str(tmp_path)],
                          input=''.join(json.dumps(r) + '\n' for r in requests), text=True,
                          capture_output=True, timeout=15)
    assert proc.returncode == 0, proc.stderr
    responses = {r['id']: r for r in map(json.loads, proc.stdout.splitlines())}
    assert responses['hello']['result']['version'] == 1
    assert responses['hello']['result']['qt_loaded'] is False
    assert responses['list']['result'][0]['kind'] == 'SQLite'
    assert 'Unknown method' in responses['bad']['error']['message']


def test_independent_cancel_and_duplicate_id(tmp_path):
    from threading import Event

    worker = Worker(tmp_path, max_workers=1, max_pending=2)
    started = Event()
    def slow(cancel_token):
        started.set()
        while not cancel_token.cancelled:
            cancel_token._event.wait(.01)
        cancel_token.raise_if_cancelled()
    worker.registry['slow'] = slow
    output = []
    worker.submit({'version': 1, 'id': 'slow', 'method': 'slow', 'params': {}}, output.append)
    assert started.wait(2)
    worker.submit({'version': 1, 'id': 'slow', 'method': 'hello', 'params': {}}, output.append)
    worker.submit({'version': 1, 'id': 'cancel', 'method': 'cancel', 'params': {'request_id': 'slow'}}, output.append)
    worker.close()
    assert any(r.get('result', {}).get('accepted') is True for r in output)
    assert any(r.get('error', {}).get('cancelled') is True for r in output)
    assert any('Duplicate' in r.get('error', {}).get('message', '') for r in output)


def test_handles_are_opaque_scoped_expiring_and_released(tmp_path):
    from baseskiff.models import RedisKeyContinuation, RedisKeyPage
    worker = Worker(tmp_path, max_handles=2)
    continuation = RedisKeyContinuation('redis', '0', '', 5, (b'\xff',))
    worker.registry['page'] = lambda connection_id, database: RedisKeyPage([], True, continuation)
    result = worker.call('page', {'connection_id': 'redis', 'database': '0'})
    handle = result['continuation']
    assert handle['$type'] == 'handle'
    assert 'pending' not in handle
    assert worker._resolve(handle, ('redis', '0')) is continuation
    with pytest.raises(ValueError, match='different'):
        worker._resolve(handle, ('redis', '1'))
    worker.call('release', {'handles': [handle['value']]})
    with pytest.raises(ValueError, match='refresh'):
        worker._resolve(handle, ('redis', '0'))
    worker.close()


def test_csv_export_and_local_helpers(tmp_path):
    worker = Worker(tmp_path / 'data')
    cid = worker.call('list_connections', {})[0]['id']
    destination = tmp_path / 'export.csv'
    worker.call('export_csv', {'connection_id': cid, 'destination': str(destination),
                              'query': "SELECT 9007199254740993 AS precise, 'hello' AS value"})
    preview = worker.call('preview_csv', {'source': str(destination)})
    assert '9007199254740993' in str(preview)
    assert worker.call('preview_value', {'value': b'{"x":1}'})['format'] == 'json'
    draft = worker.call('structure_preview', {'kind': 'SQLite', 'table': 't', 'columns': [],
                        'actions': [{'method': 'add_column', 'params': {'name': 'n', 'data_type': 'TEXT'}}]})
    assert draft['statements'] == ['ALTER TABLE "t" ADD COLUMN "n" TEXT']
    worker.close()


def test_actual_sqlite_query_cancel(tmp_path):
    from threading import Event
    worker = Worker(tmp_path, max_workers=1)
    cid = worker.call('list_connections', {})[0]['id']
    responses = []
    done = Event()
    def send(response):
        responses.append(response)
        if response['id'] == 'query':
            done.set()
    worker.submit({'version': 1, 'id': 'query', 'method': 'execute', 'params': {
        'connection_id': cid,
        'query': 'WITH RECURSIVE n(x) AS (VALUES(0) UNION ALL SELECT x+1 FROM n WHERE x<1000000000) SELECT sum(x) FROM n'}}, send)
    worker.submit({'version': 1, 'id': 'cancel', 'method': 'cancel', 'params': {'request_id': 'query'}}, send)
    assert done.wait(5)
    assert next(r for r in responses if r['id'] == 'query')['error']['cancelled'] is True
    worker.close()


def test_queue_bound_and_protocol_validation(tmp_path):
    from threading import Event
    worker = Worker(tmp_path, max_workers=1, max_pending=1)
    finish = Event()
    worker.registry['wait'] = lambda: finish.wait(3)
    output = []
    worker.submit({'version': 1, 'id': 'one', 'method': 'wait'}, output.append)
    worker.submit({'version': 1, 'id': 'two', 'method': 'hello'}, output.append)
    worker.submit({'version': 2, 'id': 'version', 'method': 'hello'}, output.append)
    worker.submit({'version': 1, 'id': 3, 'method': 'hello'}, output.append)
    assert any('queue is full' in r.get('error', {}).get('message', '') for r in output)
    assert any('version' in r.get('error', {}).get('message', '') for r in output)
    finish.set()
    worker.close()


def test_bson_edit_helpers_preserve_types(tmp_path):
    from bson import Binary, Decimal128, ObjectId
    worker = Worker(tmp_path)
    original = {'_id': ObjectId(), 'price': Decimal128('123.4500'), 'blob': Binary(b'\xff', 128)}
    text = worker.call('bson_to_text', {'value': original})
    assert '$oid' in text and '$numberDecimal' in text and '$binary' in text
    assert decode(worker.call('bson_from_text', {'text': text})) == original
    worker.close()


def test_apply_structure_reports_partial_failure_and_stops(tmp_path):
    worker = Worker(tmp_path)
    cid = worker.call('list_connections', {})[0]['id']
    worker.call('execute', {'connection_id': cid, 'query': 'CREATE TABLE draft_test (id INTEGER)'})
    arguments = {'connection_id': cid, 'table': 'draft_test',
                 'columns': [{'name': 'id', 'data_type': 'INTEGER'}], 'indexes': [], 'constraints': [],
                 'actions': [
                     {'method': 'add_column', 'params': {'name': 'first', 'data_type': 'TEXT'}},
                     {'method': 'create_index', 'params': {'name': 'already_exists', 'columns': ['id']}},
                     {'method': 'add_column', 'params': {'name': 'never', 'data_type': 'TEXT'}}]}
    worker.call('execute', {'connection_id': cid, 'query': 'CREATE INDEX already_exists ON draft_test(id)'})
    with pytest.raises(RuntimeError, match='Completed 1/3 statements; remaining steps were not executed'):
        worker.call('apply_structure', arguments)
    assert [column['name'] for column in worker.call('get_schema', {'connection_id': cid, 'table': 'draft_test'})] == ['id', 'first']
    worker.close()


def test_apply_structure_success_and_read_only(tmp_path):
    worker = Worker(tmp_path)
    connection = worker.call('list_connections', {})[0]
    worker.call('execute', {'connection_id': connection['id'], 'query': 'CREATE TABLE structure_test (id INTEGER)'})
    arguments = {'connection_id': connection['id'], 'table': 'structure_test',
                 'columns': [{'name': 'id', 'data_type': 'INTEGER'}],
                 'actions': [{'method': 'add_column', 'params': {'name': 'first', 'data_type': 'TEXT'}}]}
    result = worker.call('apply_structure', arguments)
    assert result['completed'] == result['total'] == 1
    assert [column['name'] for column in result['columns']] == ['id', 'first']
    connection['read_only'] = True
    worker.call('save_connection', {'connection': connection})
    arguments['columns'] = result['columns']
    arguments['actions'][0]['params']['name'] = 'blocked'
    with pytest.raises(RuntimeError, match='Completed 0/1'):
        worker.call('apply_structure', arguments)
    worker.close()


def test_csv_preview_returns_exact_server_fingerprint(tmp_path):
    worker = Worker(tmp_path / 'data')
    path = tmp_path / 'input.csv'
    path.write_text('id,name\n1,test\n', encoding='utf-8')
    result = worker.call('csv_preview', {'source': str(path), 'options': {'encoding': 'utf-8'}})
    assert result['columns'] == ['id', 'name']
    assert result['rows'] == [['1', 'test']]
    assert decode(result['stat']) == [path.stat().st_size, path.stat().st_mtime_ns]
    cid = worker.call('list_connections', {})[0]['id']
    worker.call('execute', {'connection_id': cid, 'query': 'CREATE TABLE csv_stat (id INTEGER, name TEXT)'})
    worker.call('import_csv', {'connection_id': cid, 'source': str(path), 'table': 'csv_stat',
                              'mapping': {0: 'id', 1: 'name'}, 'expected_stat': decode(result['stat'])})
    assert worker.call('fetch_rows', {'connection_id': cid, 'table': 'csv_stat'})['rows'] == [{'id': 1, 'name': 'test'}]
    worker.close()


def test_subprocess_streams_large_response_chunks(tmp_path):
    import base64
    worker = Worker(tmp_path)
    cid = worker.call('list_connections', {})[0]['id']
    worker.close()
    request = {'version': 1, 'id': 'large', 'method': 'execute', 'params': {
        'connection_id': cid, 'query': 'SELECT randomblob(12*1024*1024) AS payload'}}
    proc = subprocess.run([sys.executable, '-m', 'baseskiff.native.worker', '--data-dir', str(tmp_path)],
                          input=json.dumps(request) + '\n', text=True, capture_output=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    frames = list(map(json.loads, proc.stdout.splitlines()))
    assert len(frames) > 1
    assert all(len(json.dumps(frame).encode()) < 1024*1024 for frame in frames)
    assert [frame['chunk']['index'] for frame in frames] == list(range(len(frames)))
    assert all(not frame['chunk']['final'] for frame in frames[:-1])
    assert frames[-1]['chunk']['final'] is True
    response = json.loads(b''.join(base64.b64decode(frame['chunk']['data']) for frame in frames))
    assert response['id'] == 'large'
    assert len(decode(response['result'])['rows'][0]['payload']) == 12*1024*1024


def test_response_cap_fails_before_any_partial_chunks(monkeypatch):
    from baseskiff.native import worker
    monkeypatch.setattr(worker, 'MAX_RESPONSE_BYTES', 100)
    monkeypatch.setattr(worker, 'CHUNK_THRESHOLD_BYTES', 20)
    frames = list(worker.response_frames({'version': 1, 'id': 'cap', 'result': 'x'*101}))
    assert len(frames) == 1
    response = json.loads(frames[0])
    assert 'chunk' not in response
    assert response['id'] == 'cap'
    assert 'limit' in response['error']['message']


def test_plan_nodes_wraps_existing_query_plan_normalizer(tmp_path):
    worker = Worker(tmp_path)
    cid = worker.call('list_connections', {})[0]['id']
    result = worker.call('explain', {'connection_id': cid, 'query': 'SELECT * FROM customers WHERE id=1'})
    nodes = worker.call('plan_nodes', {'result': result})
    assert nodes and nodes[0]['id'] and 'customers' in nodes[0]['label']
    worker.close()


@pytest.mark.parametrize('kind', ['MongoDB', 'Redis', 'SQLite'])
def test_execute_only_supplies_cancellation_for_sql_adapters(tmp_path, kind):
    worker = Worker(tmp_path)
    connection = worker.call('save_connection', {'connection': {'name': 'target', 'kind': kind, 'path': str(tmp_path/'db.sqlite')}})
    received = []
    def execute(connection_id, query, cancel_token=None):
        received.append(cancel_token)
    worker.registry['execute'] = execute
    worker.call('execute', {'connection_id': connection['id'], 'query': 'test'})
    assert (received[0] is not None) is (kind == 'SQLite')
    worker.close()


def test_execute_errors_include_driver_source_coordinates(tmp_path):
    from threading import Event
    from types import SimpleNamespace
    worker = Worker(tmp_path)
    cid = worker.call('list_connections', {})[0]['id']
    class DriverError(RuntimeError):
        diag = SimpleNamespace(statement_position='10')
    def fail(connection_id, query, cancel_token=None):
        raise DriverError('invalid column')
    worker.registry['execute'] = fail
    done = Event()
    output = []
    def send(response):
        output.append(response)
        done.set()
    worker.submit({'version': 1, 'id': 'bad-sql', 'method': 'execute', 'params': {
        'connection_id': cid, 'query': 'SELECT\n  bad'}}, send)
    assert done.wait(2)
    assert output[0]['error']['position'] == 10
    assert output[0]['error']['line'] == 2
    assert output[0]['error']['column'] == 3
    worker.close()
