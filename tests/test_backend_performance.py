"""Bounded database work without timing-sensitive assertions or live services."""
import sqlite3
from contextlib import closing, contextmanager

import pytest

from baseskiff.adapters import RedisAdapter, SQLAdapter, result_from_cursor
from baseskiff.models import Connection
from baseskiff.service import DatabaseService


@pytest.fixture
def sql_batch(tmp_path, monkeypatch):
    path = tmp_path / 'batch.sqlite'
    with closing(sqlite3.connect(path)) as db, db:
        db.executescript('''
            PRAGMA foreign_keys=ON;
            CREATE TABLE records (tenant TEXT, id INTEGER, value TEXT, PRIMARY KEY(tenant,id));
            CREATE TABLE children (tenant TEXT, id INTEGER,
                FOREIGN KEY(tenant,id) REFERENCES records(tenant,id));
        ''')
        db.executemany('INSERT INTO records VALUES (?, ?, ?)',
                       [(tenant, index, 'original') for tenant in ('a', 'b') for index in range(25)])
    adapter = SQLAdapter(Connection(name='Batch', path=str(path)))
    opens, statements = [], []
    original_open = adapter._open

    def counted_open():
        db = original_open()
        opens.append(db)
        db.set_trace_callback(statements.append)
        return db

    monkeypatch.setattr(adapter, '_open', counted_open)
    return adapter, path, opens, statements


@pytest.mark.parametrize('size', [1, 10, 50])
def test_batch_delete_reads_schema_once_for_all_composite_keys(sql_batch, size):
    adapter, path, opens, statements = sql_batch
    keys = [{'tenant': tenant, 'id': index} for tenant in ('a', 'b') for index in range(25)][:size]
    result = adapter.delete_rows('records', keys)
    assert result.affected == size
    with closing(sqlite3.connect(path)) as db, db:
        assert db.execute('SELECT COUNT(*) FROM records').fetchone()[0] == 50 - size
    assert sum('table_info' in query for query in statements) == 1
    assert len(opens) <= 2


@pytest.mark.parametrize('invalid', [
    {'id': 1}, {'tenant': 'a', 'id': None}, {'tenant': 'a', 'id': 1, 'value': 'extra'},
])
def test_batch_delete_validates_every_key_before_writing(sql_batch, invalid):
    adapter, path, _opens, statements = sql_batch
    with pytest.raises(ValueError, match='every primary key column'):
        adapter.delete_rows('records', [{'tenant': 'a', 'id': 0}, invalid])
    assert not any(query.startswith('DELETE') for query in statements)
    with closing(sqlite3.connect(path)) as db, db:
        assert db.execute('SELECT COUNT(*) FROM records').fetchone()[0] == 50


def test_batch_delete_keeps_one_transaction_and_rolls_back_on_failure(sql_batch):
    adapter, path, _opens, statements = sql_batch
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("INSERT INTO children VALUES ('a', 1)")
    with pytest.raises(sqlite3.IntegrityError):
        adapter.delete_rows('records', [{'tenant': 'a', 'id': 0}, {'tenant': 'a', 'id': 1}])
    assert sum(query == 'BEGIN ' for query in statements) == 1
    assert 'ROLLBACK' in statements
    with closing(sqlite3.connect(path)) as db, db:
        assert db.execute('SELECT COUNT(*) FROM records').fetchone()[0] == 50


def test_batch_delete_read_only_rejects_without_opening_database(sql_batch):
    adapter, _path, opens, _statements = sql_batch
    adapter.connection.read_only = True
    with pytest.raises(PermissionError, match='read-only'):
        adapter.delete_rows('records', [{'tenant': 'a', 'id': 0}])
    assert not opens


def test_cursor_column_collisions_keep_each_original_value():
    with closing(sqlite3.connect(':memory:')) as db, db:
        cursor = db.execute('SELECT 1 AS x, 2 AS x, 3 AS x_2, 4 AS x, 5 AS x_3, 6 AS x_2')
        result = result_from_cursor(cursor)
    assert result.columns == ['x', 'x_2', 'x_2_2', 'x_3', 'x_3_2', 'x_2_3']
    assert result.rows == [{'x': 1, 'x_2': 2, 'x_2_2': 3, 'x_3': 4, 'x_3_2': 5, 'x_2_3': 6}]


def test_cursor_repeated_names_do_not_repeat_quadratic_name_comparisons():
    comparisons = []

    class ColumnName(str):
        __hash__ = str.__hash__

        def __str__(self):
            return self

        def __eq__(self, other):
            comparisons.append(1)
            return super().__eq__(other)

    class Cursor:
        description = [(ColumnName('value'),) for _ in range(200)]

        def fetchmany(self, size):
            return [tuple(range(200))]

    result = result_from_cursor(Cursor())
    assert result.columns[:3] == ['value', 'value_2', 'value_3']
    assert result.columns[-1] == 'value_200'
    assert result.rows[0]['value_200'] == 199
    assert len(comparisons) <= 800


@pytest.mark.parametrize('size, expected', [(0, 0), (3, 3), (1005, 1000), (10005, 1000)])
def test_history_count_keeps_display_limit_without_reading_query_or_error(tmp_path, monkeypatch, size, expected):
    service = DatabaseService(tmp_path)
    with service._metadata() as db:
        db.executemany('INSERT INTO history VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', [
            (str(index), 'connection', 'Temporary', 'SELECT ' + 'body ' * 200,
             '2026-11-12T00:00:00+00:00', 1.0, 1, 1, 'error ' * 200, '')
            for index in range(size)
        ])
    original_metadata = service._metadata

    @contextmanager
    def metadata_without_bodies():
        with original_metadata() as db:
            def authorize(action, table, column, _database, _trigger):
                if action == sqlite3.SQLITE_READ and table == 'history' and column in {'query', 'error'}:
                    return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK
            db.set_authorizer(authorize)
            yield db

    monkeypatch.setattr(service, '_metadata', metadata_without_bodies)
    assert service.count_history() == expected
    service.clear_history()
    assert service.count_history() == 0


def redis_response(monkeypatch, response, query):
    adapter = RedisAdapter(Connection(name='Protocol boundary', kind='Redis'))

    class Client:
        def execute_command(self, *args):
            assert args == tuple(query.split())
            return response

    @contextmanager
    def session():
        yield Client()

    monkeypatch.setattr(adapter, 'session', session)
    return adapter.execute(query)


@pytest.mark.parametrize('container', [list, tuple])
def test_redis_query_only_decodes_the_displayed_top_level_rows(monkeypatch, container):
    decoded = []

    class Value(bytes):
        def decode(self, *args, **kwargs):
            decoded.append(1)
            return super().decode(*args, **kwargs)

    response = container(Value(f'row:{index}'.encode()) for index in range(10000))
    result = redis_response(monkeypatch, response, 'LRANGE records 0 -1')
    assert len(result.rows) == result.affected == result.total == 1000
    assert result.rows[0] == {'value': 'row:0'}
    assert result.rows[-1] == {'value': 'row:999'}
    assert result.truncated
    assert len(decoded) == 1000


@pytest.mark.parametrize('response, query, columns, rows', [
    (b'hello', 'GET greeting', ['result'], [{'result': 'hello'}]),
    (b'\xff', 'GET binary', ['result'], [{'result': b'\xff'}]),
    ([b'hello', b'\xff', {b'field': [b'value', b'\xfe']}], 'EXEC', ['value'],
     [{'value': 'hello'}, {'value': b'\xff'}, {'value': {'field': ['value', b'\xfe']}}]),
    ((0, [b'key', b'\xff']), 'SCAN 0', ['value'], [{'value': 0}, {'value': ['key', b'\xff']}]),
    ({b'field': [b'value', b'\xff']}, 'HGETALL record', ['key', 'value'],
     [{'key': 'field', 'value': ['value', b'\xff']}]),
    ([], 'MGET missing', [], []),
])
def test_redis_query_preserves_scalars_nested_binary_values_and_empty_shapes(
        monkeypatch, response, query, columns, rows):
    result = redis_response(monkeypatch, response, query)
    assert result.columns == columns
    assert result.rows == rows
    assert result.affected == result.total == len(rows)
    assert not result.truncated


def test_redis_query_set_response_keeps_all_members(monkeypatch):
    result = redis_response(monkeypatch, {b'text', b'\xff'}, 'SMEMBERS records')
    assert {row['value'] for row in result.rows} == {'text', b'\xff'}
    assert result.affected == 2
    assert not result.truncated


def test_sql_session_does_not_mask_the_original_query_error_when_rollback_fails(monkeypatch):
    class BrokenConnection:
        def commit(self):
            pass

        def rollback(self):
            raise RuntimeError('(0, "")')

        def close(self):
            pass

    adapter = SQLAdapter(Connection(name='Broken MySQL', kind='MySQL'))
    monkeypatch.setattr(adapter, '_open', lambda: BrokenConnection())
    with pytest.raises(TimeoutError, match='query timed out'):
        with adapter.session():
            raise TimeoutError('query timed out')


def test_mysql_unfiltered_page_uses_fast_catalog_row_estimate_instead_of_full_count(monkeypatch):
    adapter = SQLAdapter(Connection(name='Large MySQL', kind='MySQL', database='leisu_www'))
    monkeypatch.setattr(adapter, 'get_schema', lambda _table: [
        __import__('baseskiff.models', fromlist=['ColumnInfo']).ColumnInfo('id', 'BIGINT', True),
    ])
    calls = []

    @contextmanager
    def session():
        yield object()

    def query(_db, sql, values=(), **_kwargs):
        calls.append((sql, values))
        if 'information_schema.TABLES' in sql:
            return __import__('baseskiff.models', fromlist=['QueryResult']).QueryResult(rows=[{'count_rows': 2_750_392}])
        return __import__('baseskiff.models', fromlist=['QueryResult']).QueryResult(
            columns=['id'], rows=[{'id': 51}], total=1)

    monkeypatch.setattr(adapter, 'session', session)
    monkeypatch.setattr(adapter, '_query', query)
    result = adapter.fetch_rows('members', page=2, page_size=50)
    assert result.total == 2_750_392
    assert len(result.rows) == 1
    assert not any('COUNT(*)' in sql for sql, _values in calls)
    assert any('information_schema.TABLES' in sql for sql, _values in calls)
