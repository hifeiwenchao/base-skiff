"""Bounded SQL browsing/export and cancellation regressions."""
import csv
import sqlite3
from contextlib import closing, contextmanager

import pytest

from baseskiff.adapters import SQLAdapter, SSHAdapter
from baseskiff.cancellation import CancellationToken, QueryCancelled
from baseskiff.models import Connection


@pytest.fixture
def database(tmp_path):
    path = tmp_path / 'data.sqlite'
    with closing(sqlite3.connect(path)) as db, db:
        db.execute('CREATE TABLE records(id INTEGER PRIMARY KEY, value TEXT)')
        db.executemany('INSERT INTO records VALUES (?, ?)', [(i, f'value {i}') for i in range(1, 1206)])
    return SQLAdapter(Connection(name='data', path=str(path))), path


def test_unknown_total_uses_lookahead_and_keyset_without_count(database, monkeypatch):
    adapter, _ = database
    queries = []
    original = adapter._query
    def query(db, sql, *args, **kwargs):
        queries.append(sql)
        return original(db, sql, *args, **kwargs)
    monkeypatch.setattr(adapter, '_query', query)
    first = adapter.fetch_rows('records', page_size=10, count_total=False)
    assert first.total_known is False and first.has_more
    assert len(first.rows) == 10 and first.next_cursor == 10
    second = adapter.fetch_rows('records', page=2, page_size=10, count_total=False, after_key=first.next_cursor)
    assert second.rows[0]['id'] == 11
    assert second.next_cursor == 20
    assert not any('COUNT(' in sql.upper() for sql in queries)
    assert 'OFFSET' not in queries[-1] and '"id" >' in queries[-1]
    last = adapter.fetch_rows('records', page=121, page_size=10, count_total=False)
    assert len(last.rows) == 5 and not last.has_more


def test_unsafe_keyset_sort_falls_back_to_offset(database):
    adapter, _ = database
    result = adapter.fetch_rows('records', page=2, page_size=10, count_total=False,
                                sort_column='value', after_key=1000)
    assert result.next_cursor is None
    expected = adapter.fetch_rows('records', page=2, page_size=10, sort_column='value')
    assert result.rows == expected.rows


def test_cancel_after_statement_rolls_back_before_commit(database, monkeypatch):
    adapter, path = database
    token = CancellationToken()
    original = adapter._query
    def query(db, sql, *args, **kwargs):
        result = original(db, sql, *args, **kwargs)
        token.cancel()
        return result
    monkeypatch.setattr(adapter, '_query', query)
    with pytest.raises(QueryCancelled):
        adapter.execute("UPDATE records SET value='changed'", cancel_token=token)
    with closing(sqlite3.connect(path)) as db:
        assert db.execute('SELECT value FROM records WHERE id=1').fetchone()[0] == 'value 1'


def test_ssh_passes_token_and_binds_inner_cancellation(monkeypatch):
    token = CancellationToken()
    cancelled = []
    class Direct:
        def cancel(self):
            cancelled.append(True)
        def execute(self, query, cancel_token=None):
            assert cancel_token is token
            token.cancel()
            return 'done'
    @contextmanager
    def endpoint(_connection):
        yield object()
    monkeypatch.setattr('baseskiff.ssh.ssh_endpoint', endpoint)
    monkeypatch.setattr('baseskiff.adapters._direct_adapter', lambda *_a, **_k: Direct())
    adapter = SSHAdapter(Connection(name='SSH'))
    assert adapter.execute('SELECT 1', cancel_token=token) == 'done'
    assert cancelled == [True]


def test_streaming_export_all_rows_and_filter_order(database, tmp_path):
    adapter, _ = database
    destination = tmp_path / 'all.csv'
    progress = []
    result = adapter.export_csv(destination, table='records', where_clause='id > 2',
                                order_by='id DESC', batch_size=37, progress=progress.append)
    with destination.open(newline='') as stream:
        rows = list(csv.reader(stream))
    assert len(rows) == 1204
    assert rows[1] == ['1205', 'value 1205'] and rows[-1] == ['3', 'value 3']
    assert result.affected == 1203
    assert len(progress) > 30 and progress[-1] == 1203


def test_cancel_export_preserves_existing_destination(database, tmp_path):
    adapter, _ = database
    destination = tmp_path / 'all.csv'
    destination.write_text('original')
    token = CancellationToken()
    with pytest.raises(QueryCancelled):
        adapter.export_csv(destination, table='records', batch_size=20,
                           cancel_token=token, progress=lambda _count: token.cancel())
    assert destination.read_text() == 'original'
    assert sorted(p.name for p in tmp_path.iterdir()) == ['all.csv', 'data.sqlite']


def test_query_export_rejects_writes_and_keeps_target(database, tmp_path):
    adapter, _ = database
    destination = tmp_path / 'query.csv'
    result = adapter.export_csv(destination, query='SELECT * FROM records', batch_size=43)
    assert result.affected == 1205
    with pytest.raises((ValueError, PermissionError)):
        adapter.export_csv(destination, query='DELETE FROM records RETURNING id')
    assert sum(1 for _ in destination.open()) == 1206


def test_timeout_defaults_and_custom_deadline(database, monkeypatch):
    adapter, _ = database
    assert adapter.connection.query_timeout == 10
    adapter.connection.query_timeout = 0.000001
    with pytest.raises(sqlite3.OperationalError, match='interrupted'):
        adapter.execute('WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM n WHERE x<100000) SELECT sum(x) FROM n')


def test_service_fetch_and_export_forward_options(database, tmp_path):
    from baseskiff.service import DatabaseService
    adapter, path = database
    service = DatabaseService(tmp_path / 'metadata')
    connection = service.save_connection(adapter.connection)
    result = service.fetch_rows(connection.id, 'records', page_size=5, count_total=False)
    assert not result.total_known and result.has_more
    target = tmp_path / 'service.csv'
    result = service.export_csv(connection.id, target, table='records', where_clause='id <= 12')
    assert result.affected == 12


def test_export_fetchmany_remains_bounded(tmp_path, monkeypatch):
    adapter = SQLAdapter(Connection(name='stream'))
    calls = []
    class Cursor:
        description = [('value',)]
        arraysize = 0
        remaining = 3000
        def execute(self, query):
            pass
        def fetchmany(self, size):
            calls.append(size)
            chunk = min(size, self.remaining)
            self.remaining -= chunk
            return [(i,) for i in range(chunk)]
        def close(self):
            pass
    class DB:
        def cursor(self):
            return Cursor()
    @contextmanager
    def session(_token=None):
        yield DB()
    monkeypatch.setattr(adapter, 'session', session)
    result = adapter.export_csv(tmp_path / 'bounded.csv', query='SELECT value FROM records', batch_size=23)
    assert result.affected == 3000 and max(calls) == 23


def test_explain_is_read_only_and_does_not_execute_select(database):
    adapter, _ = database
    result = adapter.explain('SELECT * FROM records WHERE id=5')
    assert result.rows and any('SEARCH' in str(row) for row in result.rows)
    with pytest.raises(ValueError):
        adapter.explain('DELETE FROM records')
    with pytest.raises(ValueError):
        adapter.explain('SELECT 1; DELETE FROM records')


def test_service_explain_preserves_read_only_connection(database, tmp_path):
    from baseskiff.service import DatabaseService
    adapter, _ = database
    adapter.connection.read_only = True
    service = DatabaseService(tmp_path / 'metadata')
    connection = service.save_connection(adapter.connection)
    result = service.explain(connection.id, 'SELECT * FROM records')
    assert result.rows


def test_maximum_page_still_has_lookahead(database):
    adapter, _ = database
    result = adapter.fetch_rows('records', page_size=1000, count_total=False)
    assert len(result.rows) == 1000 and result.has_more and result.next_cursor == 1000


def test_keyset_unknown_total_retains_loaded_lower_bound(database):
    adapter, _ = database
    result = adapter.fetch_rows('records', page=3, page_size=10, count_total=False, after_key=20)
    assert result.total == 30


def test_select_export_rejects_mysql_outfile_clause(database, tmp_path):
    adapter, _ = database
    with pytest.raises(ValueError):
        adapter.export_csv(tmp_path / 'unsafe.csv', query="SELECT id INTO OUTFILE '/tmp/unsafe' FROM records")


def test_select_validator_allows_literal_semicolon_and_comments(database):
    adapter, _ = database
    assert adapter.explain("/* report */ SELECT ';' AS value; -- trailing comment").rows


@pytest.mark.parametrize('query', [
    "SELECT 1 /*!50000 INTO OUTFILE '/tmp/exported' */",
    "SELECT 1 /*M!100100 INTO OUTFILE '/tmp/exported' */",
    "SELECT 1 /*m!100100 INTO OUTFILE '/tmp/exported' */",
    "SELECT 1 --1 INTO OUTFILE '/tmp/exported'",
    "SELECT 1 /* outer /* nested */ INTO OUTFILE '/tmp/exported' -- */",
    "SELECT 'value\\' INTO OUTFILE '/tmp/exported' -- '",
])
def test_mysql_select_validator_rejects_dialect_ambiguous_comments_and_escapes(query):
    adapter = SQLAdapter(Connection(name='mysql', kind='MySQL'))
    with pytest.raises(ValueError):
        adapter._select_statement(query)


def test_select_validator_allows_ordinary_mysql_comments_and_literals():
    adapter = SQLAdapter(Connection(name='mysql', kind='MySQL'))
    sql = "SELECT 'INTO; text' AS value /* report */ -- report\n"
    assert adapter._select_statement(sql) == sql.strip()


def test_cancellation_during_commit_does_not_report_rollback(database, monkeypatch):
    adapter, path = database
    token = CancellationToken()
    original_open = adapter._open
    cancellation_results = []
    class CommitCancellation:
        def __init__(self, db):
            self.db = db
        def __getattr__(self, name):
            return getattr(self.db, name)
        def commit(self):
            cancellation_results.append(token.cancel())
            self.db.commit()
    monkeypatch.setattr(adapter, '_open', lambda t=None: CommitCancellation(original_open(t)))
    result = adapter.execute("UPDATE records SET value='committed' WHERE id=1", cancel_token=token)
    assert result.affected == 1
    assert cancellation_results == [False]
    with closing(sqlite3.connect(path)) as db:
        assert db.execute('SELECT value FROM records WHERE id=1').fetchone()[0] == 'committed'


@pytest.mark.parametrize('demo', [False, True])
def test_sqlite_catalog_only_counts_demo_tables(database, monkeypatch, demo):
    adapter, _ = database
    adapter.connection.is_demo = demo
    queries = []
    original = adapter._query
    def query(db, sql, *args, **kwargs):
        queries.append(sql)
        return original(db, sql, *args, **kwargs)
    monkeypatch.setattr(adapter, '_query', query)
    catalog = adapter.list_tables()
    assert [table.name for table in catalog] == ['records']
    assert catalog[0].row_count == (1205 if demo else None)
    assert any('COUNT(' in sql for sql in queries) is demo


def test_mysql_exact_count_skips_catalog_estimate(monkeypatch):
    from baseskiff.models import ColumnInfo, QueryResult
    adapter = SQLAdapter(Connection(name='mysql', kind='MySQL'))
    monkeypatch.setattr(adapter, 'get_schema', lambda _table: [ColumnInfo('id', 'INT', True)])
    calls = []
    @contextmanager
    def session():
        yield object()
    def query(_db, sql, values=(), **kwargs):
        calls.append(sql)
        if 'COUNT(*)' in sql:
            return QueryResult(rows=[{'count_rows': 713}])
        if 'information_schema.TABLES' in sql:
            pytest.fail('Explicit exact count must not use TABLE_ROWS')
        return QueryResult(rows=[{'id': 1}])
    monkeypatch.setattr(adapter, 'session', session)
    monkeypatch.setattr(adapter, '_query', query)
    result = adapter.fetch_rows('records', count_total='exact')
    assert result.total == 713 and result.total_known
    assert sum('COUNT(*)' in sql for sql in calls) == 1


def test_service_forwards_explicit_exact_count(database, tmp_path, monkeypatch):
    from baseskiff.models import QueryResult
    from baseskiff.service import DatabaseService
    adapter, _ = database
    service = DatabaseService(tmp_path / 'metadata')
    connection = service.save_connection(adapter.connection)
    options = []
    class RecordingAdapter:
        def fetch_rows(self, _table, **kwargs):
            options.append(kwargs)
            return QueryResult()
    monkeypatch.setattr('baseskiff.service.adapter_for', lambda _c: RecordingAdapter())
    service.fetch_rows(connection.id, 'records', count_total='exact')
    assert options[0]['count_total'] == 'exact'


@pytest.mark.parametrize('size', [0, 7, 50])
def test_first_page_complete_result_knows_total_without_count(database, monkeypatch, size):
    adapter, _ = database
    queries = []
    original = adapter._query
    def query(db, sql, *args, **kwargs):
        queries.append(sql)
        return original(db, sql, *args, **kwargs)
    monkeypatch.setattr(adapter, '_query', query)
    result = adapter.fetch_rows('records', page_size=50, where_clause=f'id <= {size}', count_total=False)
    assert result.total_known
    assert result.total == size
    assert not result.has_more
    assert not any('COUNT(' in sql.upper() for sql in queries)


def test_cursor_result_does_not_claim_full_total(database):
    adapter, _ = database
    result = adapter.fetch_rows('records', page=1, page_size=50, count_total=False, after_key=1200)
    assert not result.total_known
    assert len(result.rows) == 5
