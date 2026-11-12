"""Sample database creation closes its connection on commit and rollback."""
import sqlite3
from contextlib import closing

import pytest

from baseskiff.demo import create_playground


@pytest.mark.parametrize(('fail_insert', 'customer_count', 'category_count'), [
    pytest.param(False, 128, 4, id='commit'),
    pytest.param(True, 0, 0, id='rollback'),
])
def test_playground_closes_its_database_without_garbage_collection(
        tmp_path, monkeypatch, fail_insert, customer_count, category_count):
    original_connect = sqlite3.connect
    connections = []

    def authorize(action, first, _second, _database, _trigger):
        # SQLite itself rejects this write after customers/categories were added.
        if action == sqlite3.SQLITE_INSERT and first == 'products':
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    def connect(*args, **kwargs):
        connection = original_connect(*args, **kwargs)
        if fail_insert:
            connection.set_authorizer(authorize)
        # Retain the real connection so dropping a local reference cannot close it.
        connections.append(connection)
        return connection

    monkeypatch.setattr(sqlite3, 'connect', connect)
    path = tmp_path / 'playground.sqlite'
    try:
        if fail_insert:
            with pytest.raises(sqlite3.DatabaseError, match='not authorized'):
                create_playground(path)
        else:
            create_playground(path)

        with closing(original_connect(path)) as reopened:
            assert reopened.execute('SELECT COUNT(*) FROM customers').fetchone()[0] == customer_count
            assert reopened.execute('SELECT COUNT(*) FROM categories').fetchone()[0] == category_count

        assert connections
        for connection in connections:
            with pytest.raises(sqlite3.ProgrammingError, match='closed database'):
                connection.execute('SELECT 1')
    finally:
        for connection in connections:
            connection.close()
