"""Opt-in PostgreSQL tests using a private config and an isolated schema.

Only a randomly named schema in the configured database is created and removed.
These tests do not create roles, change grants, or alter server settings.
"""
import uuid
from dataclasses import replace
from datetime import date, datetime, timezone
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


def table_sql(table):
    schema, name = table.split('.', 1)
    return f'{quote(schema)}.{quote(name)}'


def credentials(config):
    postgres = config.get('postgresql')
    if not postgres:
        pytest.skip('Add postgresql to BASESKIFF_LIVE_CONFIG to run PostgreSQL tests')
    return Connection(
        name='Live PostgreSQL disposable', kind='PostgreSQL',
        host=postgres.get('host', config.get('host', '127.0.0.1')),
        port=postgres.get('port', 5432), database=postgres['database'],
        username=postgres['username'], password=postgres['password'],
    )


@pytest.fixture(scope='module')
def postgresql_schema(live_config, run_tag):
    schema = run_tag + '_pg_' + uuid.uuid4().hex[:6]
    adapter = SQLAdapter(credentials(live_config))
    adapter.execute(f'CREATE SCHEMA {quote(schema)}')
    try:
        yield schema
    finally:
        adapter.execute(f'DROP SCHEMA {quote(schema)} CASCADE')


@pytest.fixture
def postgres(live_service, live_config, postgresql_schema, request):
    connection = credentials(live_config)
    saved = live_service.save_connection(connection)
    prefix = request.node.name.removeprefix('test_')[:28] + '_' + uuid.uuid4().hex[:6]
    state = SimpleNamespace(service=live_service, id=saved.id, saved=saved,
                            connection=connection, schema=postgresql_schema)
    state.execute = lambda query: live_service.execute(saved.id, query)
    state.table = lambda suffix='rows': postgresql_schema + '.' + prefix + '_' + suffix
    return state


def simple_table(postgres, name=None):
    table = name or postgres.table()
    postgres.execute(f'CREATE TABLE {table_sql(table)} (id INT PRIMARY KEY, name TEXT NOT NULL)')
    return table


def test_authentication_catalog_views_and_composite_schema(postgres):
    assert 'successfully' in postgres.service.test_connection(postgres.saved)
    identity = postgres.execute('SELECT current_user AS username, current_database() AS database').rows[0]
    assert identity == {'username': postgres.connection.username, 'database': postgres.connection.database}
    assert postgres.execute('SELECT version() AS version').rows[0]['version'].startswith('PostgreSQL ')
    assert postgres.execute("SELECT '100%' AS literal, 10 % 3 AS remainder").rows == [{'literal': '100%', 'remainder': 1}]
    table, view = postgres.table(), postgres.table('view')
    postgres.execute(f'''CREATE TABLE {table_sql(table)} (
        tenant_id INT NOT NULL, row_id INT NOT NULL,
        label VARCHAR(50) NOT NULL DEFAULT 'ready', optional_value TEXT,
        PRIMARY KEY(tenant_id, row_id))''')
    postgres.execute(f'CREATE VIEW {table_sql(view)} AS SELECT * FROM {table_sql(table)}')
    tables = {item.qualified_name: item for item in postgres.service.list_tables(postgres.id)}
    assert tables[table].kind == 'table'
    assert tables[view].kind == 'view'
    assert tables[table].schema == postgres.schema
    schema = postgres.service.get_schema(postgres.id, table)
    assert [column.name for column in schema] == ['tenant_id', 'row_id', 'label', 'optional_value']
    columns = {column.name: column for column in schema}
    assert {column.name for column in schema if column.primary_key} == {'tenant_id', 'row_id'}
    assert not columns['tenant_id'].nullable and not columns['label'].nullable
    assert columns['optional_value'].nullable
    assert columns['label'].data_type == 'character varying'
    assert "'ready'" in columns['label'].default


def test_grid_crud_preserves_numeric_dates_json_binary_and_null(postgres):
    table = postgres.table()
    postgres.execute(f'''CREATE TABLE {table_sql(table)} (
        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        name TEXT NOT NULL, amount NUMERIC(18,4) NOT NULL,
        born DATE NOT NULL, happened TIMESTAMPTZ NOT NULL,
        details JSONB NOT NULL, optional_value TEXT, binary_value BYTEA)''')
    expected = {'name': "中文 🛶 O'Reilly", 'amount': Decimal('123456789.1200'),
                'born': date(2024, 2, 29),
                'happened': datetime(2024, 2, 29, 12, 34, 56, tzinfo=timezone.utc),
                'details': {'text': '绿舟', 'nested': [1, True, None]},
                'optional_value': None, 'binary_value': b'\x00\xfftest'}
    assert postgres.service.insert_row(postgres.id, table, expected).affected == 1
    row = postgres.service.fetch_rows(postgres.id, table).rows[0]
    assert {key: row[key] for key in expected} == expected
    assert isinstance(row['amount'], Decimal)
    assert isinstance(row['details'], dict)
    key = {'id': row['id']}
    changed = {'name': "更新 '; DROP TABLE ignored; --", 'amount': Decimal('-0.1250'),
               'details': {'changed': ['a', '中文']}, 'binary_value': b'\xff\x00new'}
    assert postgres.service.update_row(postgres.id, table, key, changed).affected == 1
    row = postgres.service.fetch_rows(postgres.id, table).rows[0]
    assert {column: row[column] for column in changed} == changed
    assert postgres.service.delete_rows(postgres.id, table, [key]).affected == 1
    assert postgres.service.fetch_rows(postgres.id, table).total == 0


def test_pagination_literal_search_sort_and_quoted_identifiers(postgres):
    table = postgres.table(' odd."table')
    postgres.execute(f'CREATE TABLE {table_sql(table)} '
                     '(id INT PRIMARY KEY, "select" TEXT NOT NULL, "quote""column" TEXT)')
    names = ['normal', '100% exact', 'under_score', 'bang!value', "O'Reilly", '中文 🛶', 'slash\\value']
    for index, name in enumerate(names, 1):
        postgres.service.insert_row(postgres.id, table, {'id': index, 'select': name, 'quote"column': 'kept'})
    first = postgres.service.fetch_rows(postgres.id, table, page=1, page_size=3)
    second = postgres.service.fetch_rows(postgres.id, table, page=2, page_size=3)
    assert first.total == second.total == 7
    assert [row['id'] for row in first.rows] == [1, 2, 3]
    assert [row['id'] for row in second.rows] == [4, 5, 6]
    descending = postgres.service.fetch_rows(postgres.id, table, page_size=2, sort_column='id', descending=True)
    assert [row['id'] for row in descending.rows] == [7, 6]
    for needle, expected in [('%', names[1]), ('_', names[2]), ('!', names[3]),
                             ("O'Reilly", names[4]), ('中文', names[5]), ('\\', names[6])]:
        result = postgres.service.fetch_rows(postgres.id, table, search=needle, sort_column='select')
        assert result.total == 1
        assert result.rows[0]['select'] == expected
    assert postgres.service.fetch_rows(postgres.id, table, search="' OR 1=1 --").total == 0
    assert postgres.service.fetch_rows(postgres.id, table, page=99).rows == []
    with pytest.raises(ValueError, match='Unknown sort column'):
        postgres.service.fetch_rows(postgres.id, table, sort_column='id DESC; DROP TABLE ignored')
    assert postgres.service.update_row(postgres.id, table, {'id': 1}, {'quote"column': 'updated'}).affected == 1
    assert postgres.service.fetch_rows(postgres.id, table).rows[0]['quote"column'] == 'updated'


def test_primary_key_guards_default_insert_and_empty_mutations(postgres):
    no_key, compound, defaults = postgres.table('no_key'), postgres.table('compound'), postgres.table('defaults')
    postgres.execute(f'CREATE TABLE {table_sql(no_key)} (name TEXT)')
    for operation in [lambda: postgres.service.insert_row(postgres.id, no_key, {'name': 'blocked'}),
                      lambda: postgres.service.update_row(postgres.id, no_key, {'name': 'x'}, {'name': 'blocked'}),
                      lambda: postgres.service.delete_rows(postgres.id, no_key, [{'name': 'x'}])]:
        with pytest.raises(ValueError, match='primary key'):
            operation()
    postgres.execute(f'CREATE TABLE {table_sql(compound)} '
                     '(tenant INT, id INT, name TEXT, PRIMARY KEY (tenant, id))')
    postgres.service.insert_row(postgres.id, compound, {'tenant': 1, 'id': 1, 'name': 'safe'})
    for keys in [{}, {'id': 1}, {'tenant': 1, 'id': None}, {'tenant': 1, 'id': 1, 'name': 'safe'}]:
        with pytest.raises(ValueError, match='every primary key'):
            postgres.service.update_row(postgres.id, compound, keys, {'name': 'blocked'})
        with pytest.raises(ValueError, match='every primary key'):
            postgres.service.delete_rows(postgres.id, compound, [keys])
    with pytest.raises(ValueError, match='Unknown column'):
        postgres.service.insert_row(postgres.id, compound, {'unknown': 1})
    with pytest.raises(ValueError, match='Unknown column'):
        postgres.service.update_row(postgres.id, compound, {'tenant': 1, 'id': 1}, {'unknown': 1})
    assert postgres.service.fetch_rows(postgres.id, compound).rows == [{'tenant': 1, 'id': 1, 'name': 'safe'}]
    postgres.execute(f'CREATE TABLE {table_sql(defaults)} '
                     "(id INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, name TEXT NOT NULL DEFAULT 'ready')")
    assert postgres.service.insert_row(postgres.id, defaults, {}).affected == 1
    assert postgres.service.fetch_rows(postgres.id, defaults).rows == [{'id': 1, 'name': 'ready'}]
    assert postgres.service.delete_rows(postgres.id, defaults, []).affected == 0
    assert postgres.service.update_row(postgres.id, defaults, {'id': 1}, {}).affected == 0


def test_read_only_browsing_and_server_transaction_reject_hidden_writes(postgres):
    import psycopg

    table, function = simple_table(postgres), postgres.table('write_function')
    postgres.service.insert_row(postgres.id, table, {'id': 1, 'name': 'unchanged'})
    postgres.execute(f'''CREATE FUNCTION {table_sql(function)}() RETURNS INT
        LANGUAGE plpgsql AS $$ BEGIN
            INSERT INTO {table_sql(table)} VALUES (2, 'must not persist');
            RETURN 1;
        END $$''')
    read_only = postgres.service.save_connection(replace(postgres.connection, id=postgres.id + '_ro', read_only=True))
    service = postgres.service
    assert 'successfully' in service.test_connection(read_only)
    assert service.execute(read_only.id, f'SELECT * FROM {table_sql(table)}').rows == [{'id': 1, 'name': 'unchanged'}]
    assert any(item.qualified_name == table for item in service.list_tables(read_only.id))
    assert service.get_schema(read_only.id, table)
    assert service.fetch_rows(read_only.id, table, search='unchanged').total == 1
    for query in [f"INSERT INTO {table_sql(table)} VALUES (2, 'blocked')",
                  f"UPDATE {table_sql(table)} SET name='blocked'",
                  f'DELETE FROM {table_sql(table)}', f'DROP TABLE {table_sql(table)}',
                  'COMMIT', f"/* comment */ UPDATE {table_sql(table)} SET name='blocked'"]:
        with pytest.raises(PermissionError):
            service.execute(read_only.id, query)
    for operation in [lambda: service.insert_row(read_only.id, table, {'id': 2, 'name': 'blocked'}),
                      lambda: service.update_row(read_only.id, table, {'id': 1}, {'name': 'blocked'}),
                      lambda: service.delete_rows(read_only.id, table, [{'id': 1}])]:
        with pytest.raises(PermissionError):
            operation()
    # The editor's SELECT whitelist accepts this. The actual PostgreSQL
    # transaction must still prohibit a function from writing to the table.
    with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
        service.execute(read_only.id, f'SELECT {table_sql(function)}() AS attempted_write')
    assert service.fetch_rows(postgres.id, table).rows == [{'id': 1, 'name': 'unchanged'}]
    failures = [entry for entry in service.list_history() if not entry.success]
    assert len(failures) == 10
    assert any('read-only' in entry.error.lower() for entry in failures)


def test_errors_batch_rollback_single_statement_and_history(postgres):
    import psycopg

    parent, child = simple_table(postgres, postgres.table('parent')), postgres.table('child')
    postgres.execute(f'CREATE TABLE {table_sql(child)} (id INT PRIMARY KEY, parent_id INT '
                     f'REFERENCES {table_sql(parent)}(id))')
    for index in (1, 2):
        postgres.service.insert_row(postgres.id, parent, {'id': index, 'name': f'parent {index}'})
    postgres.service.insert_row(postgres.id, child, {'id': 1, 'parent_id': 2})
    with pytest.raises(psycopg.errors.UniqueViolation):
        postgres.service.insert_row(postgres.id, parent, {'id': 1, 'name': 'duplicate'})
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        postgres.service.delete_rows(postgres.id, parent, [{'id': 1}, {'id': 2}])
    assert [row['id'] for row in postgres.service.fetch_rows(postgres.id, parent).rows] == [1, 2]
    with pytest.raises(psycopg.errors.SyntaxError):
        postgres.execute(f"INSERT INTO {table_sql(parent)} VALUES (3, 'first'); "
                         f"INSERT INTO {table_sql(parent)} VALUES (4, 'second')")
    assert [row['id'] for row in postgres.service.fetch_rows(postgres.id, parent).rows] == [1, 2]
    postgres.execute(f"INSERT INTO {table_sql(parent)} VALUES (5, 'committed')")
    assert postgres.execute('SELECT 1 AS recovered').rows == [{'recovered': 1}]
    assert postgres.service.fetch_rows(postgres.id, parent).rows[-1] == {'id': 5, 'name': 'committed'}
    failures = [entry for entry in postgres.service.list_history() if not entry.success]
    assert len(failures) == 3
    assert all(entry.error and entry.elapsed_ms >= 0 and entry.affected == 0 for entry in failures)
    assert all(entry.connection_id == postgres.id for entry in failures)
    assert any('duplicate' in entry.query for entry in failures)
    assert any(entry.success and entry.affected == 1 and 'committed' in entry.query
               for entry in postgres.service.list_history())


def test_query_and_page_caps_preserve_totals_and_duplicate_labels(postgres):
    table = simple_table(postgres)
    postgres.execute(f"INSERT INTO {table_sql(table)} SELECT n, 'row ' || n FROM generate_series(1, 1005) n")
    result = postgres.execute(f'SELECT * FROM {table_sql(table)} ORDER BY id')
    assert len(result.rows) == 1000 and result.truncated
    assert result.rows[-1]['id'] == 1000
    page = postgres.service.fetch_rows(postgres.id, table, page_size=2000)
    assert len(page.rows) == 1000 and page.total == 1005
    assert len(postgres.service.fetch_rows(postgres.id, table, page=2, page_size=1000).rows) == 5
    duplicate = postgres.execute('SELECT 1 AS same, 2 AS same, 3 AS same_2')
    assert duplicate.columns == ['same', 'same_2', 'same_2_2']
    assert list(duplicate.rows[0].values()) == [1, 2, 3]


def drain(qtbot, window):
    qtbot.waitUntil(lambda: not window.busy
                   and QThreadPool.globalInstance().activeThreadCount() == 0, timeout=30000)


def test_native_table_tabs_retain_drafts_results_and_edit_target(postgres, qtbot, monkeypatch):
    left, right = simple_table(postgres, postgres.table('left')), simple_table(postgres, postgres.table('right'))
    postgres.execute(f'ALTER TABLE {table_sql(left)} ADD COLUMN amount NUMERIC(30,12) NOT NULL DEFAULT 0')
    for table, value in [(left, 'left original'), (right, 'right original')]:
        postgres.service.insert_row(postgres.id, table, {'id': 1, 'name': value})
    window = MainWindow(postgres.service, language='en')
    qtbot.addWidget(window)
    errors = []
    monkeypatch.setattr(window, 'show_error', errors.append)

    def activate(table):
        identity = postgres.id, postgres.saved.database, table
        matches = [index for index in range(window.table_tabs.count())
                   if window.table_tabs.tabData(index) == identity]
        assert len(matches) == 1
        window.table_tabs.setCurrentIndex(matches[0])
        drain(qtbot, window)
        assert window.current_table == table

    try:
        window.show()
        qtbot.waitUntil(lambda: window.current_connection is not None, timeout=15000)
        drain(qtbot, window)
        window.activate_connection(postgres.saved, left)
        drain(qtbot, window)
        assert window.current_result.rows == [{'id': 1, 'name': 'left original', 'amount': Decimal('0')}]
        window.query_editor.setPlainText('SELECT 11 AS left_answer')
        window.run_button.click()
        drain(qtbot, window)
        assert window.query_result_table.item(0, 0).text() == '11'
        window.query_editor.setPlainText('SELECT 111 AS unsaved_left_draft')
        window.open_table(right)
        drain(qtbot, window)
        window.query_editor.setPlainText('SELECT 22 AS right_answer')
        window.run_button.click()
        drain(qtbot, window)
        activate(left)
        assert window.query_editor.toPlainText() == 'SELECT 111 AS unsaved_left_draft'
        assert window.query_result_table.item(0, 0).text() == '11'
        assert window.tabs.currentIndex() == 1
        assert window.current_result.rows == [{'id': 1, 'name': 'left original', 'amount': Decimal('0')}]
        dialog_errors = []

        def accept_edit():
            dialog = QApplication.activeModalWidget()
            try:
                assert isinstance(dialog, RowDialog)
                dialog.fields['name'].setText('left edited from native dialog')
                dialog.accept()
            except Exception as error:
                dialog_errors.append(str(error))
                if dialog is not None:
                    dialog.reject()

        window.tabs.setCurrentIndex(0)
        window.data_table.selectRow(0)
        QTimer.singleShot(0, accept_edit)
        window.edit_selected_row()
        assert window._pending_grid_count() > 0
        window.commit_table_changes()
        drain(qtbot, window)
        assert not dialog_errors, dialog_errors
        assert window.current_result.rows == [{'id': 1, 'name': 'left edited from native dialog', 'amount': Decimal('0')}]
        precise_amount = '123456789012345678.123456789012'

        def accept_insert():
            dialog = QApplication.activeModalWidget()
            try:
                assert isinstance(dialog, RowDialog)
                dialog.fields['id'].setText('2')
                dialog.fields['name'].setText('precise numeric from native dialog')
                dialog.fields['amount'].setText(precise_amount)
                dialog.accept()
            except Exception as error:
                dialog_errors.append(str(error))
                if dialog is not None:
                    dialog.reject()

        QTimer.singleShot(0, accept_insert)
        window.add_row()
        assert window._pending_grid_count() > 0
        window.commit_table_changes()
        drain(qtbot, window)
        assert not dialog_errors, dialog_errors
        inserted = postgres.service.fetch_rows(postgres.id, left).rows[1]
        assert inserted['amount'] == Decimal(precise_amount)
        assert window.current_result.rows[1]['amount'] == Decimal(precise_amount)
        activate(right)
        assert window.current_result.rows == [{'id': 1, 'name': 'right original'}]
        assert window.query_editor.toPlainText() == 'SELECT 22 AS right_answer'
        assert window.query_result_table.item(0, 0).text() == '22'
        assert postgres.service.fetch_rows(postgres.id, left).rows[0]['name'] == 'left edited from native dialog'
        assert postgres.service.fetch_rows(postgres.id, right).rows[0]['name'] == 'right original'
        assert not errors, errors
    finally:
        window.search_timer.stop()
        drain(qtbot, window)
        from PySide6.QtWidgets import QMessageBox
        monkeypatch.setattr(QMessageBox, 'warning', lambda *args: QMessageBox.StandardButton.Discard)
        window.close()
        drain(qtbot, window)


def test_real_ssh_forward_crud_queries_history_and_read_only(postgres):
    table = simple_table(postgres)
    postgres.service.insert_row(postgres.id, table, {'id': 1, 'name': 'initial'})
    with TestSSHServer() as ssh:
        direct = postgres.connection
        connection = ssh.connection(direct.host, direct.port, kind='PostgreSQL',
                                    database=direct.database, username=direct.username, password=direct.password)
        saved = postgres.service.save_connection(connection)
        service = postgres.service
        assert 'successfully' in service.test_connection(saved)
        assert any(item.qualified_name == table for item in service.list_tables(saved.id))
        assert any(column.primary_key for column in service.get_schema(saved.id, table))
        assert service.fetch_rows(saved.id, table).rows == [{'id': 1, 'name': 'initial'}]
        assert service.insert_row(saved.id, table, {'id': 2, 'name': '通过 SSH 添加'}).affected == 1
        assert service.update_row(saved.id, table, {'id': 2}, {'name': '通过 SSH 修改'}).affected == 1
        assert service.execute(saved.id, f'SELECT name FROM {table_sql(table)} WHERE id=2').rows == [{'name': '通过 SSH 修改'}]
        assert service.delete_rows(saved.id, table, [{'id': 2}]).affected == 1
        readonly = service.save_connection(replace(connection, id=connection.id + '_ro', read_only=True))
        assert service.fetch_rows(readonly.id, table).total == 1
        with pytest.raises(PermissionError):
            service.insert_row(readonly.id, table, {'id': 3, 'name': 'blocked'})
        assert not service.list_history()[0].success
        assert len(ssh.destinations) >= 9
        assert all(destination == (direct.host, direct.port) for destination in ssh.destinations)
        assert all(method == 'password' for method, _ in ssh.auth_attempts)
    assert postgres.service.fetch_rows(postgres.id, table).rows == [{'id': 1, 'name': 'initial'}]
