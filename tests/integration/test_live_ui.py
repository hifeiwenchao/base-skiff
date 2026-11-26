"""Opt-in Qt workflows against disposable resources on actual local services."""
import json
import uuid
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QThreadPool, QTimer
from PySide6.QtWidgets import QApplication, QPlainTextEdit

from baseskiff.models import Connection
from baseskiff.ui.dialogs import ConnectionDialog, RowDialog
from baseskiff.ui.window import MainWindow

pytestmark = pytest.mark.integration


def _drain(qtbot, window):
    # Finished signals can enqueue catalog/schema/data workers. Let Qt deliver
    # all callbacks before deleting resources or destroying their window.
    qtbot.waitUntil(
        lambda: not window.busy and QThreadPool.globalInstance().activeThreadCount() == 0,
        timeout=30000,
    )


@pytest.fixture
def ui_data(request, live_config, run_tag):
    name = f'{run_tag}_ui_{uuid.uuid4().hex[:6]}'
    host = live_config['host']
    kind = request.param
    if kind == 'mysql':
        import pymysql

        config = live_config['mysql']
        raw = pymysql.connect(
            host=host, port=config['port'], user='root',
            password=config['root_password'], autocommit=True,
            cursorclass=pymysql.cursors.DictCursor,
        )
        with raw.cursor() as cursor:
            cursor.execute(f'CREATE DATABASE `{name}`')
        try:
            with raw.cursor() as cursor:
                cursor.execute(
                    f'CREATE TABLE `{name}`.typed_records ('
                    'id INT PRIMARY KEY AUTO_INCREMENT, '
                    'note VARCHAR(100) NOT NULL, amount DECIMAL(18, 4) NOT NULL, '
                    'recorded_at DATETIME(6) NOT NULL, binary_value BLOB NOT NULL, '
                    'payload JSON NOT NULL, optional_text VARCHAR(100) NULL)'
                )
                cursor.execute(
                    f'INSERT INTO `{name}`.typed_records '
                    '(note, amount, recorded_at, binary_value, payload, optional_text) '
                    'VALUES (%s, %s, %s, %s, %s, %s)',
                    ('initial', Decimal('1234567890.1234'), datetime(2026, 2, 3, 4, 5, 6, 123456),
                     b'\x00\xfflive', json.dumps({'enabled': True, 'items': [1, 2]}), None),
                )
            yield SimpleNamespace(
                kind=kind, raw=raw, name=name, table='typed_records',
                connection=Connection(
                    name=name, kind='MySQL', host=host, port=config['port'],
                    username='root', password=config['root_password'], database=name,
                ),
            )
        finally:
            with raw.cursor() as cursor:
                cursor.execute(f'DROP DATABASE `{name}`')
            raw.close()
    elif kind == 'mongo':
        from bson import Binary, Decimal128, Int64, ObjectId
        from pymongo import MongoClient

        config = live_config['mongo']
        raw = MongoClient(
            host=host, port=config['port'], username=config['username'],
            password=config['password'], authSource='admin',
            serverSelectionTimeoutMS=8000, socketTimeoutMS=10000,
        )
        try:
            raw[name].records.insert_many([
                {'_id': ObjectId(), 'note': 'first', 'mixed': {'nested': [1, 2]},
                 'variant': 42, 'first_only': 'not present in the second document',
                 'nested': {'identifier': ObjectId(), 'amount': Decimal128('12.50'),
                            'when': datetime(2026, 2, 3, 4, 5, 6, 123000), 'label': 'original'},
                 'amount': Decimal128('1234567890.1234'), 'counter': Int64(2**40),
                 'binary_value': Binary(b'\x00\xfflive', subtype=128),
                 'recorded_at': datetime(2026, 2, 3, 4, 5, 6, 123000)},
                {'_id': ObjectId(), 'note': 'second', 'mixed': 'scalar text', 'variant': '12',
                 'amount': Decimal128('2.5000'), 'counter': Int64(2**40 + 1),
                 'binary_value': Binary(b'other', subtype=128),
                 'recorded_at': datetime(2026, 3, 4, 5, 6, 7, 456000)},
            ])
            yield SimpleNamespace(
                kind=kind, raw=raw, name=name, table='records',
                connection=Connection(
                    name=name, kind='MongoDB', host=host, port=config['port'],
                    username=config['username'], password=config['password'], database=name,
                ),
            )
        finally:
            raw.drop_database(name)
            raw.close()
    elif kind == 'redis':
        from redis import Redis

        config = live_config['redis']
        raw = Redis(
            host=host, port=config['port'], username=config['username'] or None,
            password=config['password'] or None, decode_responses=True,
            socket_connect_timeout=8, socket_timeout=10,
        )
        key = name + ':hash'
        try:
            raw.hset(key, mapping={'name': 'initial', 'count': '2'})
            raw.pexpire(key, 120000)
            yield SimpleNamespace(
                kind=kind, raw=raw, name=name, table=key,
                connection=Connection(
                    name=name, kind='Redis', host=host, port=config['port'],
                    username=config['username'], password=config['password'], database='0',
                ),
            )
        finally:
            # No FLUSHDB: only this test's uniquely named key is removed.
            raw.delete(key)
            raw.close()
    else:
        raise AssertionError(f'Unknown live UI backend: {kind}')


@pytest.fixture
def live_window(qtbot, monkeypatch, live_service, ui_data):
    saved = live_service.save_connection(ui_data.connection)
    window = MainWindow(live_service, language='en')
    qtbot.addWidget(window)
    errors = []
    # Capture failures instead of opening a blocking message box in CI.
    monkeypatch.setattr(window, 'show_error', errors.append)
    try:
        window.show()
        qtbot.waitUntil(lambda: window.current_connection is not None, timeout=15000)
        _drain(qtbot, window)
        window.activate_connection(saved, ui_data.table)
        _drain(qtbot, window)
        assert not errors, errors
        assert window.current_table == ui_data.table
        assert window.data_table.rowCount() > 0
        assert window.schema_table.rowCount() == len(window.columns) > 0
        yield window, errors
    finally:
        _drain(qtbot, window)
        from PySide6.QtWidgets import QMessageBox
        monkeypatch.setattr(QMessageBox, 'warning', lambda *args: QMessageBox.StandardButton.Discard)
        window.close()
        _drain(qtbot, window)


def _query_and_history(qtbot, window, query):
    window.query_editor.setPlainText(query)
    window.run_button.click()
    _drain(qtbot, window)
    assert window._query_state == 'success', window.query_status.text()
    assert window.query_result_table.rowCount() > 0
    window.show_history()
    _drain(qtbot, window)
    entries = [entry for entry in window._history if entry.query == query]
    assert len(entries) == 1 and entries[0].success
    assert entries[0].connection_id == window.current_connection.id
    assert window.history_table.rowCount() >= 1


def _edit_row(window, row_index, changes):
    failures = []
    parsed = []

    def fill_dialog():
        dialog = QApplication.activeModalWidget()
        try:
            assert isinstance(dialog, RowDialog)
            for name, text in changes.items():
                field = dialog.fields[name]
                if callable(text):
                    current = field.toPlainText() if isinstance(field, QPlainTextEdit) else field.text()
                    text = text(current)
                if isinstance(field, QPlainTextEdit):
                    field.setPlainText(text)
                else:
                    field.setText(text)
            parsed.append(dialog.values())
            dialog.accept()
        except Exception as error:
            failures.append(str(error))
            if dialog is not None:
                dialog.reject()

    window.show_workspace()
    window.tabs.setCurrentIndex(0)
    window.data_table.selectRow(row_index)
    assert window.edit_row_button.isEnabled()
    QTimer.singleShot(0, fill_dialog)
    window.edit_selected_row()
    assert not failures, failures
    assert parsed
    if window.current_connection.kind in {'SQLite', 'MySQL', 'PostgreSQL', 'Oracle'}:
        assert window._pending_grid_count() > 0
        window.commit_table_changes()
    return parsed[0]


@pytest.mark.parametrize('ui_data', ['mysql'], indirect=True)
def test_mysql_widgets_load_schema_run_query_and_record_history(qtbot, live_window):
    window, errors = live_window
    assert {column.name for column in window.columns} >= {'id', 'amount', 'payload'}
    assert next(column for column in window.columns if column.name == 'id').primary_key
    assert window.total_rows == 1
    _query_and_history(qtbot, window, 'SELECT id, note, amount FROM typed_records ORDER BY id')
    assert window.query_result.rows[0]['amount'] == Decimal('1234567890.1234')
    assert not errors


@pytest.mark.parametrize('ui_data', ['mysql'], indirect=True)
def test_mysql_connection_dialog_tests_and_retains_session_password(qtbot, live_window):
    window, errors = live_window
    connection = window.current_connection
    failures = []

    def edit_connection():
        dialog = QApplication.activeModalWidget()
        try:
            assert isinstance(dialog, ConnectionDialog)
            assert dialog.password_edit.text() == ''
            dialog.test_button.click()
            _drain(qtbot, window)
            assert dialog.test_button.isEnabled()
            assert dialog.test_status.text().startswith('Connection successful')
            dialog.name_edit.setText(connection.name + ' renamed')
            dialog.save_button.click()
        except Exception as error:
            failures.append(str(error))
            if dialog is not None:
                dialog.reject()

    QTimer.singleShot(0, edit_connection)
    window.connection_dialog(connection)
    _drain(qtbot, window)
    assert not failures, failures
    assert not errors, errors
    assert window.current_connection.id == connection.id
    assert window.current_connection.name.endswith(' renamed')
    assert window.data_table.rowCount() == 1
    assert window.service._connection(connection.id).password


@pytest.mark.parametrize('ui_data', ['mysql'], indirect=True)
def test_mysql_real_grid_dialog_saves_typed_values(qtbot, ui_data, live_window):
    window, errors = live_window
    parsed = _edit_row(window, 0, {
        'note': 'edited from Qt', 'amount': '9876543210.4321',
        'recorded_at': '2026-07-08T09:10:11.654321', 'binary_value': '00abff',
        'payload': '{"enabled": false, "items": [3, 4]}',
    })
    assert parsed['amount'] == Decimal('9876543210.4321')
    assert parsed['binary_value'] == b'\x00\xab\xff'
    assert parsed['optional_text'] is None
    _drain(qtbot, window)
    assert not errors, errors
    assert window.current_result.rows[0]['note'] == 'edited from Qt'
    with ui_data.raw.cursor() as cursor:
        cursor.execute(f'SELECT * FROM `{ui_data.name}`.typed_records WHERE id=1')
        stored = cursor.fetchone()
    assert stored['amount'] == Decimal('9876543210.4321')
    assert stored['recorded_at'] == datetime(2026, 7, 8, 9, 10, 11, 654321)
    assert stored['binary_value'] == b'\x00\xab\xff'
    assert json.loads(stored['payload']) == {'enabled': False, 'items': [3, 4]}
    assert stored['optional_text'] is None


@pytest.mark.parametrize('ui_data', ['mongo'], indirect=True)
def test_mongo_widgets_query_history_and_preserve_real_bson(qtbot, ui_data, live_window):
    from bson import Binary, Decimal128, Int64

    window, errors = live_window
    original = window.current_result.rows[0]
    parsed = _edit_row(window, 0, {'note': 'edited from Qt'})
    assert isinstance(parsed['amount'], Decimal128)
    assert isinstance(parsed['counter'], Int64)
    assert isinstance(parsed['binary_value'], Binary)
    assert parsed['binary_value'].subtype == 128
    _drain(qtbot, window)
    assert not errors, errors
    stored = ui_data.raw[ui_data.name].records.find_one({'_id': original['_id']})
    assert stored == dict(original, note='edited from Qt')
    assert stored['binary_value'].subtype == 128
    query = json.dumps({'find': 'records', 'filter': {'note': 'edited from Qt'}})
    _query_and_history(qtbot, window, query)
    assert window.query_result.rows[0]['_id'] == original['_id']
    assert not errors


@pytest.mark.parametrize('ui_data', ['mongo'], indirect=True)
def test_mongo_grid_edits_scalar_in_heterogeneous_field(qtbot, ui_data, live_window):
    window, errors = live_window
    index = next(i for i, row in enumerate(window.current_result.rows) if row['note'] == 'second')
    original = window.current_result.rows[index]
    parsed = _edit_row(window, index, {'mixed': 'edited scalar text', 'variant': '00034'})
    assert parsed['mixed'] == 'edited scalar text'
    assert parsed['variant'] == '00034'
    _drain(qtbot, window)
    assert not errors, errors
    stored = ui_data.raw[ui_data.name].records.find_one({'_id': original['_id']})
    assert stored['mixed'] == 'edited scalar text'
    assert stored['variant'] == '00034'
    assert 'first_only' not in stored


@pytest.mark.parametrize('ui_data', ['mongo'], indirect=True)
def test_mongo_grid_edit_of_nested_sibling_preserves_bson_types(qtbot, ui_data, live_window):
    from bson import Decimal128, ObjectId

    window, errors = live_window
    index = next(i for i, row in enumerate(window.current_result.rows) if row['note'] == 'first')
    original = window.current_result.rows[index]
    _edit_row(window, index, {
        'nested': lambda text: json.dumps(dict(json.loads(text), label='edited sibling')),
    })
    _drain(qtbot, window)
    assert not errors, errors
    nested = ui_data.raw[ui_data.name].records.find_one({'_id': original['_id']})['nested']
    assert nested['label'] == 'edited sibling'
    assert isinstance(nested['identifier'], ObjectId)
    assert isinstance(nested['amount'], Decimal128)
    assert isinstance(nested['when'], datetime)
    assert nested == dict(original['nested'], label='edited sibling')


@pytest.mark.parametrize('ui_data', ['redis'], indirect=True)
def test_redis_widgets_edit_hash_preserve_ttl_run_query_and_history(qtbot, ui_data, live_window):
    window, errors = live_window
    before = ui_data.raw.pttl(ui_data.table)
    parsed = _edit_row(window, 0, {'value': '{"name":"edited from Qt","count":"3"}'})
    assert parsed['value'] == {'name': 'edited from Qt', 'count': '3'}
    _drain(qtbot, window)
    assert not errors, errors
    assert ui_data.raw.hgetall(ui_data.table) == parsed['value']
    assert 0 < ui_data.raw.pttl(ui_data.table) <= before
    _query_and_history(qtbot, window, f'HGETALL {ui_data.table}')
    assert {row['key']: row['value'] for row in window.query_result.rows} == parsed['value']
    assert not errors
