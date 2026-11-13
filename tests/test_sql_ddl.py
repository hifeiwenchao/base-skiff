"""SQL table context actions require explicit confirmation for destructive DDL."""
import sqlite3

from PySide6.QtWidgets import QInputDialog, QMessageBox

from baseskiff.models import Connection, IndexInfo
from baseskiff.service import DatabaseService
from baseskiff.ui.window import MainWindow


def test_drop_sql_table_requires_exact_name_then_second_confirmation(qtbot, tmp_path, monkeypatch):
    window = MainWindow(DatabaseService(tmp_path), language='en')
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: not window.busy and window.data_table.rowCount() > 0, timeout=15000)
    connection = Connection('DDL', kind='SQLite', path=str(tmp_path / 'ddl.sqlite'))
    calls = []
    monkeypatch.setattr(window.service, 'execute', lambda *args, **kwargs: calls.append((args, kwargs)))
    monkeypatch.setattr(QInputDialog, 'getText', lambda *args, **kwargs: ('wrong', True))
    window.drop_sql_table(connection, 'customers')
    assert calls == []

    monkeypatch.setattr(QInputDialog, 'getText', lambda *args, **kwargs: ('customers', True))
    confirmations = []
    monkeypatch.setattr(QMessageBox, 'question', lambda *args, **kwargs: confirmations.append(args) or QMessageBox.StandardButton.No)
    window.drop_sql_table(connection, 'customers')
    assert len(confirmations) == 1
    assert calls == []
    window.close()


def test_sql_index_catalog_and_drop_use_selected_index_without_name_confirmation(tmp_path):
    service = DatabaseService(tmp_path)
    connection = Connection('Indexes', kind='SQLite', path=str(tmp_path / 'indexes.sqlite'))
    sqlite3.connect(connection.path).close()
    service.save_connection(connection)
    service.execute(connection.id, 'CREATE TABLE records (id INTEGER PRIMARY KEY, name TEXT, code TEXT)')
    service.execute(connection.id, 'CREATE UNIQUE INDEX records_code_uq ON records(code)')
    service.execute(connection.id, 'CREATE INDEX records_name_idx ON records(name)')

    indexes = service.list_indexes(connection.id, 'records')
    assert {item.name for item in indexes} >= {'records_code_uq', 'records_name_idx'}
    selected = next(item for item in indexes if item.name == 'records_name_idx')
    assert selected.columns == ('name',) and not selected.unique

    service.drop_index(connection.id, 'records', selected.name)
    assert selected.name not in {item.name for item in service.list_indexes(connection.id, 'records')}


def test_drop_sql_index_chooses_from_catalog_without_name_input(qtbot, tmp_path, monkeypatch):
    window = MainWindow(DatabaseService(tmp_path), language='en')
    qtbot.addWidget(window)
    connection = Connection('Indexes', kind='SQLite', path=str(tmp_path / 'indexes.sqlite'))
    sqlite3.connect(connection.path).close()
    connection = window.service.save_connection(connection)
    calls = []
    monkeypatch.setattr(window.service, 'list_indexes', lambda *args, **kwargs: [
        IndexInfo('records_name_idx', ('name',)), IndexInfo('records_code_uq', ('code',), True)])
    monkeypatch.setattr(window.service, 'drop_index', lambda *args, **kwargs: calls.append((args, kwargs)))
    monkeypatch.setattr(window, 'submit', lambda function, callback=None, error=None: callback(function()))
    monkeypatch.setattr(QInputDialog, 'getItem', lambda *args, **kwargs: ('records_name_idx  ·  name', True))
    monkeypatch.setattr(QInputDialog, 'getText', lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('index deletion must not request a name')))
    monkeypatch.setattr(QMessageBox, 'question', lambda *args, **kwargs: QMessageBox.StandardButton.Yes)

    window.drop_sql_index(connection, 'records')

    assert calls and calls[0][0][1:] == ('records', 'records_name_idx')
    window.close()
