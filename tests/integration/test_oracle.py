"""Opt-in Oracle Thin tests against a supplied application account.

Only uniquely named tables and views are created in the account's own schema.
No account, privilege, schema or server configuration is changed.
"""
import time
import uuid
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QThreadPool, QTimer
from PySide6.QtWidgets import QApplication

from baseskiff.adapters import SQLAdapter
from baseskiff.models import Connection
from baseskiff.ui.dialogs import RowDialog
from baseskiff.ui.window import MainWindow
from tests.ssh_test_server import TestSSHServer

pytestmark = pytest.mark.integration


def quote(identifier):
    return '"' + identifier.replace('"', '""') + '"'


@pytest.fixture
def oracle(live_config, live_service):
    config = live_config.get('oracle')
    if not config:
        pytest.skip('Add oracle to the private live-service configuration')
    connection = Connection(
        name='Live Oracle disposable objects', kind='Oracle',
        host=live_config['host'], port=config.get('port', 1521),
        database=config.get('database', 'FREEPDB1'),
        username=config['username'], password=config['password'])
    saved = live_service.save_connection(connection)
    prefix = 'baseskiff_it_' + uuid.uuid4().hex[:10]
    objects = []
    state = SimpleNamespace(service=live_service, id=saved.id,
                            connection=connection, saved=saved)
    state.execute = lambda query: live_service.execute(saved.id, query)
    state.table = lambda suffix='rows': prefix + '_' + suffix

    def create(name, definition, *, kind='TABLE'):
        state.execute(f'CREATE {kind} {quote(name)} {definition}')
        objects.append((kind, name))
        return name

    state.create = create
    try:
        yield state
    finally:
        # Reverse creation order drops child tables/views before their sources.
        adapter = SQLAdapter(connection)
        failures = []
        for kind, name in reversed(objects):
            try:
                adapter.execute(f'DROP {kind} {quote(name)}'
                                + (' PURGE' if kind == 'TABLE' else ''))
            except Exception as error:
                failures.append((name, str(error)))
        assert not failures, failures


def simple_table(oracle, suffix='rows'):
    return oracle.create(oracle.table(suffix),
                         '("id" NUMBER(10) PRIMARY KEY, "name" VARCHAR2(200 CHAR) NOT NULL)')


def wait_for_read_only_snapshot(connection, table):
    """Wait for Oracle's snapshot clock to recognize this newly created fixture."""
    import oracledb

    # Native-driver probes reproduced ORA-01466 for over 1.3 seconds after DDL.
    # This prepares the test table, without retrying any application operation.
    adapter = SQLAdapter(connection)
    deadline = time.monotonic() + 5
    while True:
        try:
            with adapter._open() as database:
                with database.cursor() as cursor:
                    cursor.execute('SET TRANSACTION READ ONLY')
                    cursor.execute(f'SELECT COUNT(*) FROM {quote(table)}')
                    assert cursor.fetchone() == (1,)
            return
        except oracledb.DatabaseError as error:
            if error.args[0].code != 1466 or time.monotonic() >= deadline:
                raise
            time.sleep(0.1)


def test_authentication_catalog_views_schema_and_composite_primary_keys(oracle):
    import oracledb

    assert oracledb.is_thin_mode()
    assert oracle.service.test_connection(oracle.connection) == 'Connected to Oracle successfully.'
    identity = oracle.execute("SELECT SYS_CONTEXT('USERENV', 'SESSION_USER') AS \"username\" FROM dual")
    assert identity.rows == [{'username': oracle.connection.username.upper()}]
    table = oracle.create(oracle.table(), '''(
        "tenant" NUMBER(10) NOT NULL,
        "id" NUMBER(10) NOT NULL,
        "name" VARCHAR2(200 CHAR) DEFAULT 'ready' NOT NULL,
        "optional" VARCHAR2(100 CHAR),
        PRIMARY KEY ("tenant", "id")
    )''')
    view = oracle.create(oracle.table('view'), f'AS SELECT * FROM {quote(table)}', kind='VIEW')
    catalog = {item.name: item for item in oracle.service.list_tables(oracle.id)}
    assert catalog[table].kind == 'table'
    assert view in catalog, 'Oracle views must be visible in the object sidebar'
    assert catalog[view].kind == 'view'
    schema = oracle.service.get_schema(oracle.id, table)
    assert [column.name for column in schema] == ['tenant', 'id', 'name', 'optional']
    columns = {column.name: column for column in schema}
    assert {column.name for column in schema if column.primary_key} == {'tenant', 'id'}
    assert not columns['tenant'].nullable and not columns['name'].nullable
    assert columns['optional'].nullable
    assert columns['name'].data_type == 'VARCHAR2'
    assert columns['name'].default.strip() == "'ready'"
    assert [column.name for column in oracle.service.get_schema(oracle.id, view)] == list(columns)


def test_typed_grid_crud_preserves_decimal_dates_unicode_and_complete_lobs(oracle):
    table = oracle.create(oracle.table(), '''(
        "id" NUMBER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
        "name" VARCHAR2(200 CHAR) NOT NULL,
        "amount" NUMBER(20,4) NOT NULL,
        "born" DATE NOT NULL,
        "happened" TIMESTAMP(6) NOT NULL,
        "details" CLOB,
        "binary_value" BLOB,
        "optional" VARCHAR2(100 CHAR)
    )''')
    text = '中文 🛶 O\'Reilly\n' * 6000 + 'CLOB-tail-100%_!literal'
    binary = bytes(range(256)) * 400
    expected = {'name': "中文 🛶 O'Reilly", 'amount': Decimal('1234567890123456.1234'),
                'born': date(2024, 2, 29), 'happened': datetime(2024, 2, 29, 12, 34, 56, 123456),
                'details': text, 'binary_value': binary, 'optional': None}
    assert oracle.service.insert_row(oracle.id, table, expected).affected == 1
    row = oracle.service.fetch_rows(oracle.id, table).rows[0]
    key = {'id': row['id']}
    assert oracle.service.fetch_rows(oracle.id, table, search='absent-from-all-fields').total == 0
    assert oracle.service.fetch_rows(oracle.id, table, search='100%_!literal').total == 1
    assert oracle.service.fetch_rows(oracle.id, table, search='100X_tail').total == 0
    assert row['amount'] == expected['amount'], 'NUMBER precision must survive a grid round trip'
    assert isinstance(row['amount'], Decimal)
    assert row['born'] == datetime(2024, 2, 29)
    for name in ('name', 'happened', 'details', 'binary_value', 'optional'):
        assert row[name] == expected[name]
    queried = oracle.execute(f'SELECT "details", "binary_value" FROM {quote(table)}').rows[0]
    # Both cursor/connection have already closed: no live LOB handle may escape.
    assert queried == {'details': text, 'binary_value': binary}
    changed = {'name': "更新 '; DROP TABLE ignored; --", 'amount': Decimal('-0.1250'),
               'details': 'updated 中文', 'binary_value': b'\x00\xffchanged'}
    assert oracle.service.update_row(oracle.id, table, key, changed).affected == 1
    updated = oracle.service.fetch_rows(oracle.id, table).rows[0]
    assert all(updated[name] == value for name, value in changed.items())
    assert oracle.service.delete_rows(oracle.id, table, [key]).affected == 1
    assert oracle.service.fetch_rows(oracle.id, table).total == 0


def test_pagination_sort_and_literal_search(oracle):
    table = simple_table(oracle)
    names = ['normal', '100% exact', 'under_score', 'bang!value', "O'Reilly", '中文 🛶', 'slash\\value']
    for index, name in enumerate(names, 1):
        oracle.service.insert_row(oracle.id, table, {'id': index, 'name': name})
    first = oracle.service.fetch_rows(oracle.id, table, page=1, page_size=3)
    second = oracle.service.fetch_rows(oracle.id, table, page=2, page_size=3)
    assert first.total == second.total == len(names)
    assert [row['id'] for row in first.rows] == [1, 2, 3]
    assert [row['id'] for row in second.rows] == [4, 5, 6]
    descending = oracle.service.fetch_rows(oracle.id, table, page_size=2, sort_column='id', descending=True)
    assert [row['id'] for row in descending.rows] == [7, 6]
    for needle, expected in [('%', names[1]), ('_', names[2]), ('!', names[3]),
                             ("O'Reilly", names[4]), ('中文', names[5]), ('\\', names[6])]:
        result = oracle.service.fetch_rows(oracle.id, table, search=needle)
        assert result.total == 1
        assert result.rows[0]['name'] == expected
    assert oracle.service.fetch_rows(oracle.id, table, search="' OR 1=1 --").total == 0
    assert oracle.service.fetch_rows(oracle.id, table, page=99).rows == []
    with pytest.raises(ValueError, match='Unknown sort column'):
        oracle.service.fetch_rows(oracle.id, table, sort_column='id DESC; DROP TABLE ignored')


def test_read_only_browsing_and_grid_primary_key_guards(oracle):
    import oracledb

    table = simple_table(oracle)
    oracle.service.insert_row(oracle.id, table, {'id': 1, 'name': 'unchanged'})
    wait_for_read_only_snapshot(oracle.connection, table)
    read_only = oracle.service.save_connection(replace(oracle.connection, id=oracle.id + '_ro', read_only=True))
    service, connection_id = oracle.service, read_only.id
    assert service.test_connection(read_only) == 'Connected to Oracle successfully.'
    assert any(item.name == table for item in service.list_tables(connection_id))
    assert service.get_schema(connection_id, table)
    assert service.fetch_rows(connection_id, table, search='unchanged').total == 1
    assert service.execute(connection_id, f'SELECT * FROM {quote(table)}').rows == [{'id': 1, 'name': 'unchanged'}]
    # SELECT passes the editor whitelist; the actual server transaction must
    # independently reject an attempt to acquire a write lock.
    with pytest.raises(oracledb.DatabaseError) as locked:
        service.execute(connection_id, f'SELECT * FROM {quote(table)} FOR UPDATE')
    assert locked.value.args[0].code == 1456
    for query in [f'INSERT INTO {quote(table)} VALUES (2, \'blocked\')',
                  f'UPDATE {quote(table)} SET "name"=\'blocked\'',
                  f'DELETE FROM {quote(table)}', f'DROP TABLE {quote(table)}',
                  'COMMIT', f'/* comment */ UPDATE {quote(table)} SET "name"=\'blocked\'']:
        with pytest.raises(PermissionError):
            service.execute(connection_id, query)
    for operation in [lambda: service.insert_row(connection_id, table, {'id': 2, 'name': 'blocked'}),
                      lambda: service.update_row(connection_id, table, {'id': 1}, {'name': 'blocked'}),
                      lambda: service.delete_rows(connection_id, table, [{'id': 1}])]:
        with pytest.raises(PermissionError):
            operation()
    for key in [{}, {'id': None}, {'id': 1, 'name': 'unchanged'}]:
        with pytest.raises(ValueError, match='every primary key'):
            service.update_row(oracle.id, table, key, {'name': 'blocked'})
    with pytest.raises(ValueError, match='Unknown column'):
        service.insert_row(oracle.id, table, {'unknown': 1})
    no_key = oracle.create(oracle.table('nopk'), '("name" VARCHAR2(100 CHAR))')
    with pytest.raises(ValueError, match='primary key'):
        service.insert_row(oracle.id, no_key, {'name': 'blocked'})
    assert service.fetch_rows(oracle.id, table).rows == [{'id': 1, 'name': 'unchanged'}]
    assert sum(not entry.success for entry in service.list_history()) == 15


def test_duplicate_and_foreign_key_failures_roll_back_batch_and_record_history(oracle):
    import oracledb

    parent = simple_table(oracle, 'parent')
    child = oracle.create(oracle.table('child'), f'''(
        "id" NUMBER(10) PRIMARY KEY,
        "parent_id" NUMBER(10) REFERENCES {quote(parent)}("id")
    )''')
    for index in (1, 2):
        oracle.service.insert_row(oracle.id, parent, {'id': index, 'name': f'parent {index}'})
    oracle.service.insert_row(oracle.id, child, {'id': 1, 'parent_id': 2})
    with pytest.raises(oracledb.IntegrityError) as duplicate:
        oracle.service.insert_row(oracle.id, parent, {'id': 1, 'name': 'duplicate'})
    assert duplicate.value.args[0].code == 1
    with pytest.raises(oracledb.IntegrityError) as foreign_key:
        oracle.service.delete_rows(oracle.id, parent, [{'id': 1}, {'id': 2}])
    assert foreign_key.value.args[0].code == 2292
    assert [row['id'] for row in oracle.service.fetch_rows(oracle.id, parent).rows] == [1, 2]
    failures = [entry for entry in oracle.service.list_history() if not entry.success]
    assert len(failures) == 2
    assert all(entry.error and entry.elapsed_ms >= 0 and entry.affected == 0 for entry in failures)
    assert all(entry.connection_id == oracle.id for entry in failures)
    assert any('duplicate' in entry.query for entry in failures)
    assert oracle.execute('SELECT 1 AS "recovered" FROM dual').rows == [{'recovered': 1}]


def test_result_cap_duplicate_labels_default_insert_and_statement_recovery(oracle):
    import oracledb

    table = oracle.create(oracle.table(), '''(
        "id" NUMBER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
        "name" VARCHAR2(100 CHAR) DEFAULT 'ready' NOT NULL
    )''')
    assert oracle.service.insert_row(oracle.id, table, {}).affected == 1
    assert oracle.service.fetch_rows(oracle.id, table).rows == [{'id': 1, 'name': 'ready'}]
    assert oracle.service.update_row(oracle.id, table, {'id': 1}, {}).affected == 0
    assert oracle.service.delete_rows(oracle.id, table, []).affected == 0
    with pytest.raises(oracledb.DatabaseError):
        oracle.execute(f'INSERT INTO {quote(table)} ("name") VALUES (\'first\'); '
                       f'INSERT INTO {quote(table)} ("name") VALUES (\'second\')')
    assert oracle.service.fetch_rows(oracle.id, table).total == 1
    oracle.execute(f'INSERT INTO {quote(table)} ("name") SELECT \'bulk\' FROM dual CONNECT BY LEVEL <= 1004')
    result = oracle.execute(f'SELECT * FROM {quote(table)} ORDER BY "id"')
    assert len(result.rows) == 1000 and result.truncated
    assert result.rows[-1]['id'] == 1000
    page = oracle.service.fetch_rows(oracle.id, table, page_size=2000)
    assert len(page.rows) == 1000 and page.total == 1005
    assert len(oracle.service.fetch_rows(oracle.id, table, page=2, page_size=1000).rows) == 5
    duplicate = oracle.execute('SELECT 1 AS "same", 2 AS "same", 3 AS "same_2" FROM dual')
    assert duplicate.columns == ['same', 'same_2', 'same_2_2']
    assert list(duplicate.rows[0].values()) == [1, 2, 3]
    assert any(not entry.success and entry.error for entry in oracle.service.list_history())


def test_quoted_identifiers_and_composite_key_mutations(oracle):
    table = oracle.create(oracle.table('odd'), '''(
        "tenant" NUMBER(10), "key" NUMBER(10),
        "select" VARCHAR2(100 CHAR), "column space" VARCHAR2(100 CHAR),
        PRIMARY KEY ("tenant", "key")
    )''')
    values = {'tenant': 2, 'key': 1, 'select': '中文', 'column space': "O'Reilly"}
    assert oracle.service.insert_row(oracle.id, table, values).affected == 1
    assert oracle.service.fetch_rows(oracle.id, table, search='中文', sort_column='select').rows == [values]
    with pytest.raises(ValueError, match='every primary key'):
        oracle.service.update_row(oracle.id, table, {'key': 1}, {'select': 'blocked'})
    keys = {'tenant': 2, 'key': 1}
    assert oracle.service.update_row(oracle.id, table, keys, {'column space': 'updated'}).affected == 1
    assert oracle.service.fetch_rows(oracle.id, table).rows[0]['column space'] == 'updated'
    assert oracle.service.delete_rows(oracle.id, table, [keys]).affected == 1


def idle(qtbot, window):
    qtbot.waitUntil(lambda: not window.busy and QThreadPool.globalInstance().activeThreadCount() == 0,
                   timeout=30000)


def test_native_qt_table_tabs_preserve_query_drafts_and_edit_the_original_table(oracle, qtbot, monkeypatch):
    left, right = oracle.table('left'), oracle.table('right')
    definition = '''("id" NUMBER(10) PRIMARY KEY, "name" VARCHAR2(200 CHAR) NOT NULL,
                    "amount" NUMBER(20,4), "born" DATE, "happened" TIMESTAMP(6))'''
    seed = {'id': 1, 'amount': Decimal('1000000000000000.0001'),
            'born': datetime(2024, 1, 1), 'happened': datetime(2024, 1, 1, 12, 30, 45, 123456)}
    for table, label in [(left, 'left original'), (right, 'right original')]:
        oracle.create(table, definition)
        oracle.service.insert_row(oracle.id, table, dict(seed, name=label))
    window = MainWindow(oracle.service, language='en')
    qtbot.addWidget(window)
    errors = []
    monkeypatch.setattr(window, 'show_error', errors.append)
    window.show()
    try:
        qtbot.waitUntil(lambda: window.current_connection is not None, timeout=15000)
        idle(qtbot, window)
        window.activate_connection(oracle.saved, left)
        idle(qtbot, window)
        assert window.current_result.rows == [dict(seed, name='left original')]
        window.query_editor.setPlainText('SELECT 11 AS "left_answer" FROM dual')
        window.run_button.click()
        idle(qtbot, window)
        assert window.query_result_table.item(0, 0).text() == '11'
        window.query_editor.setPlainText('SELECT 111 AS "unsaved_left_draft" FROM dual')
        window.open_table(right)
        idle(qtbot, window)
        assert window.current_result.rows == [dict(seed, name='right original')]
        window.query_editor.setPlainText('SELECT 22 AS "right_answer" FROM dual')
        window.run_button.click()
        idle(qtbot, window)
        indexes = [index for index in range(window.table_tabs.count())
                   if window.table_tabs.tabData(index) == (oracle.id, oracle.saved.database, left)]
        assert len(indexes) == 1
        window.table_tabs.setCurrentIndex(indexes[0])
        idle(qtbot, window)
        assert window.query_editor.toPlainText() == 'SELECT 111 AS "unsaved_left_draft" FROM dual'
        assert window.query_result_table.item(0, 0).text() == '11'
        failures = []

        def accept_edit():
            dialog = QApplication.activeModalWidget()
            try:
                assert isinstance(dialog, RowDialog)
                dialog.fields['name'].setText('left edited in Qt')
                dialog.fields['amount'].setText('1234567890123456.1234')
                dialog.accept()
                if dialog.isVisible():
                    failures.append(dialog.error_label.text())
                    dialog.reject()
            except Exception as error:
                failures.append(str(error))
                if dialog is not None:
                    dialog.reject()

        window.tabs.setCurrentIndex(0)
        window.data_table.selectRow(0)
        QTimer.singleShot(0, accept_edit)
        window.edit_selected_row()
        assert window._pending_grid_count() > 0
        window.commit_table_changes()
        idle(qtbot, window)
        assert not failures, failures
        assert not errors, errors
        assert oracle.service.fetch_rows(oracle.id, left).rows == [
            dict(seed, name='left edited in Qt', amount=Decimal('1234567890123456.1234'))]
        assert oracle.service.fetch_rows(oracle.id, right).rows == [dict(seed, name='right original')]

        def accept_add():
            dialog = QApplication.activeModalWidget()
            try:
                assert isinstance(dialog, RowDialog)
                values = {'id': '2', 'name': 'created in Qt', 'amount': '9876543210987654.9876',
                          'born': '2024-02-29', 'happened': '2024-02-29T12:34:56.123456'}
                for name, value in values.items():
                    dialog.null_checks[name].setChecked(False)
                    dialog.fields[name].setText(value)
                dialog.accept()
                if dialog.isVisible():
                    failures.append(dialog.error_label.text())
                    dialog.reject()
            except Exception as error:
                failures.append(str(error))
                if dialog is not None:
                    dialog.reject()

        QTimer.singleShot(0, accept_add)
        window.add_row()
        assert window._pending_grid_count() > 0
        window.commit_table_changes()
        idle(qtbot, window)
        assert not failures, failures
        assert not errors, errors
        created = oracle.service.fetch_rows(oracle.id, left, sort_column='id').rows[1]
        assert created == {'id': 2, 'name': 'created in Qt', 'amount': Decimal('9876543210987654.9876'),
                           'born': datetime(2024, 2, 29), 'happened': datetime(2024, 2, 29, 12, 34, 56, 123456)}
        window.open_table(right)
        idle(qtbot, window)
        assert window.query_editor.toPlainText() == 'SELECT 22 AS "right_answer" FROM dual'
        assert window.query_result_table.item(0, 0).text() == '22'
    finally:
        window.search_timer.stop()
        idle(qtbot, window)
        from PySide6.QtWidgets import QMessageBox
        monkeypatch.setattr(QMessageBox, 'warning', lambda *args: QMessageBox.StandardButton.Discard)
        window.close()
        idle(qtbot, window)


def test_real_ssh_tunnel_authentication_catalog_crud_and_readonly(oracle):
    table = simple_table(oracle)
    oracle.service.insert_row(oracle.id, table, {'id': 1, 'name': 'initial'})
    wait_for_read_only_snapshot(oracle.connection, table)
    with TestSSHServer() as ssh:
        connection = ssh.connection(oracle.connection.host, oracle.connection.port,
                                    kind='Oracle', database=oracle.connection.database,
                                    username=oracle.connection.username, password=oracle.connection.password)
        saved = oracle.service.save_connection(connection)
        assert oracle.service.test_connection(connection) == 'Connected to Oracle successfully.'
        assert any(item.name == table for item in oracle.service.list_tables(saved.id))
        assert any(column.primary_key for column in oracle.service.get_schema(saved.id, table))
        assert oracle.service.fetch_rows(saved.id, table).total == 1
        assert oracle.service.insert_row(saved.id, table, {'id': 2, 'name': '通过 SSH 添加'}).affected == 1
        assert oracle.service.update_row(saved.id, table, {'id': 2}, {'name': '通过 SSH 修改'}).affected == 1
        assert oracle.service.execute(saved.id, f'SELECT "name" FROM {quote(table)} WHERE "id"=2').rows == [{'name': '通过 SSH 修改'}]
        assert oracle.service.delete_rows(saved.id, table, [{'id': 2}]).affected == 1
        readonly = oracle.service.save_connection(replace(connection, read_only=True))
        assert oracle.service.fetch_rows(readonly.id, table).rows == [{'id': 1, 'name': 'initial'}]
        with pytest.raises(PermissionError):
            oracle.service.insert_row(readonly.id, table, {'id': 3, 'name': 'forbidden'})
        assert not oracle.service.list_history()[0].success
        assert len(ssh.destinations) >= 9
        assert all(target == (connection.host, connection.port) for target in ssh.destinations)
        assert all(method == 'password' for method, _ in ssh.auth_attempts)
