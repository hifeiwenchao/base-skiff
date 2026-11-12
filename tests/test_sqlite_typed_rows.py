"""Typed native dialogs write usable values through SQLite's actual driver."""
import sqlite3
from contextlib import closing
from types import SimpleNamespace

import pytest

from baseskiff.models import Connection
from baseskiff.service import DatabaseService
from baseskiff.ui.dialogs import RowDialog


@pytest.fixture
def sqlite_rows(tmp_path):
    path = tmp_path / 'typed.sqlite'
    with closing(sqlite3.connect(path)):
        pass
    service = DatabaseService(tmp_path / 'app')
    saved = service.save_connection(Connection(name='Typed SQLite', kind='SQLite', path=str(path)))
    return SimpleNamespace(service=service, id=saved.id)


@pytest.mark.parametrize('data_type', ['NUMERIC', 'DECIMAL(12,2)'])
def test_numeric_dialog_insert_commits_to_sqlite(sqlite_rows, qtbot, data_type):
    service, connection_id = sqlite_rows.service, sqlite_rows.id
    service.execute(connection_id, f'CREATE TABLE records '
                    f'(id INTEGER PRIMARY KEY, name TEXT NOT NULL, amount {data_type} NOT NULL)')
    dialog = RowDialog(service.get_schema(connection_id, 'records'), language='en')
    qtbot.addWidget(dialog)
    dialog.fields['name'].setText('created in the native dialog')
    dialog.fields['amount'].setText('123.45')
    assert service.insert_row(connection_id, 'records', dialog.values()).affected == 1
    row = service.fetch_rows(connection_id, 'records').rows[0]
    assert row['id'] == 1 and row['name'] == 'created in the native dialog'
    # SQLite NUMERIC affinity may store a binary float. This checks a real
    # write without claiming arbitrary-precision numeric storage in SQLite.
    assert row['amount'] == pytest.approx(123.45)


@pytest.mark.parametrize('data_type', ['NUMERIC', 'DECIMAL(12,2)'])
def test_numeric_dialog_edit_commits_to_sqlite(sqlite_rows, qtbot, data_type):
    service, connection_id = sqlite_rows.service, sqlite_rows.id
    service.execute(connection_id, f'CREATE TABLE records '
                    f'(id INTEGER PRIMARY KEY, name TEXT NOT NULL, amount {data_type} NOT NULL)')
    service.execute(connection_id, "INSERT INTO records VALUES (1, 'original', 123.45)")
    original = service.fetch_rows(connection_id, 'records').rows[0]
    dialog = RowDialog(service.get_schema(connection_id, 'records'), original, language='en')
    qtbot.addWidget(dialog)
    dialog.fields['name'].setText('updated in the native dialog')
    dialog.fields['amount'].setText('678.90')
    values = dialog.values()
    values.pop('id')
    assert service.update_row(connection_id, 'records', {'id': 1}, values).affected == 1
    row = service.fetch_rows(connection_id, 'records').rows[0]
    assert row['id'] == 1 and row['name'] == 'updated in the native dialog'
    assert row['amount'] == pytest.approx(678.90)


def test_date_and_datetime_dialog_insert_and_edit_use_iso_text(sqlite_rows, qtbot):
    service, connection_id = sqlite_rows.service, sqlite_rows.id
    service.execute(connection_id, 'CREATE TABLE records '
                    '(id INTEGER PRIMARY KEY, born DATE NOT NULL, happened DATETIME NOT NULL)')
    dialog = RowDialog(service.get_schema(connection_id, 'records'), language='en')
    qtbot.addWidget(dialog)
    dialog.fields['born'].setText('2024-02-29')
    dialog.fields['happened'].setText('2024-02-29T12:34:56.123456')
    assert service.insert_row(connection_id, 'records', dialog.values()).affected == 1
    original = service.fetch_rows(connection_id, 'records').rows[0]
    assert original == {'id': 1, 'born': '2024-02-29', 'happened': '2024-02-29T12:34:56.123456'}
    edit = RowDialog(service.get_schema(connection_id, 'records'), original, language='en')
    qtbot.addWidget(edit)
    edit.fields['born'].setText('2025-01-02')
    edit.fields['happened'].setText('2025-01-02T03:04:05.654321')
    values = edit.values()
    values.pop('id')
    assert service.update_row(connection_id, 'records', {'id': 1}, values).affected == 1
    assert service.fetch_rows(connection_id, 'records').rows == [
        {'id': 1, 'born': '2025-01-02', 'happened': '2025-01-02T03:04:05.654321'}]
