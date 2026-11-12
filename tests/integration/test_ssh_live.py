"""Real database operations through a real ephemeral loopback SSH server."""
import uuid
from dataclasses import replace

import pytest
from PySide6.QtCore import QThreadPool

from baseskiff.ui.dialogs import ConnectionDialog
from baseskiff.ui.window import MainWindow
from tests.ssh_test_server import TestSSHServer

pytestmark = pytest.mark.integration


@pytest.fixture(params=['mysql', 'mongo', 'redis'])
def tunneled_case(request, live_config, live_service, run_tag):
    kind = request.param
    config = live_config[kind]
    name = f'{run_tag}_ssh_{uuid.uuid4().hex[:8]}'
    host = live_config['host']
    with TestSSHServer() as ssh:
        if kind == 'mysql':
            import pymysql
            raw = pymysql.connect(host=host, port=config['port'], user='root', password=config['root_password'], autocommit=True)
            with raw.cursor() as cursor:
                cursor.execute(f'CREATE DATABASE `{name}`')
                cursor.execute(f'CREATE TABLE `{name}`.records (id INT PRIMARY KEY AUTO_INCREMENT, name VARCHAR(120), value INT)')
                cursor.execute(f'INSERT INTO `{name}`.records (name,value) VALUES (\'initial\',1)')
            table = 'records'
            connection = ssh.connection(host, config['port'], kind='MySQL', database=name,
                                        username='root', password=config['root_password'])
        elif kind == 'mongo':
            from pymongo import MongoClient
            raw = MongoClient(host=host, port=config['port'], username=config['username'], password=config['password'])
            raw[name].records.insert_one({'name': 'initial', 'value': 1})
            table = 'records'
            connection = ssh.connection(host, config['port'], kind='MongoDB', database=name,
                                        username=config['username'], password=config['password'])
        else:
            from redis import Redis
            raw = Redis(host=host, port=config['port'], username=config['username'], password=config['password'], decode_responses=True)
            table = name + ':string'
            raw.set(table, 'initial', ex=120)
            connection = ssh.connection(host, config['port'], kind='Redis', database='0',
                                        username=config['username'], password=config['password'])
        try:
            saved = live_service.save_connection(connection)
            yield live_service, saved, table, raw, ssh, kind
        finally:
            if kind == 'mysql':
                with raw.cursor() as cursor:
                    cursor.execute(f'DROP DATABASE `{name}`')
                raw.close()
            elif kind == 'mongo':
                raw.drop_database(name)
                raw.close()
            else:
                raw.delete(*list(raw.scan_iter(match=name + '*')))
                raw.close()


def test_database_crud_queries_history_and_readonly_through_ssh(tunneled_case):
    service, connection, table, raw, ssh, kind = tunneled_case
    assert 'successfully' in service.test_connection(connection)
    assert any(item.name == table for item in service.list_tables(connection.id))
    schema = service.get_schema(connection.id, table)
    assert any(column.primary_key for column in schema)
    initial = service.fetch_rows(connection.id, table)
    assert initial.total == 1
    if kind == 'redis':
        before = raw.pttl(table)
        service.update_row(connection.id, table, {'key': table}, {'value': '通过 SSH 修改'})
        assert raw.get(table) == '通过 SSH 修改' and 0 < raw.pttl(table) <= before
        created = table + ':new'
        service.insert_row(connection.id, table, {'key': created, 'type': 'string', 'value': 'new'})
        result = service.execute(connection.id, f'GET {created}')
        assert result.rows == [{'result': 'new'}]
        assert service.delete_rows(connection.id, table, [{'key': created}]).affected == 1
    else:
        assert service.insert_row(connection.id, table, {'name': '通过 SSH 添加', 'value': 23}).affected == 1
        added = next(row for row in service.fetch_rows(connection.id, table).rows if row['value'] == 23)
        key = {'id': added['id']} if kind == 'mysql' else {'_id': added['_id']}
        service.update_row(connection.id, table, key, {'name': '通过 SSH 修改'})
        assert any(row['name'] == '通过 SSH 修改' for row in service.fetch_rows(connection.id, table).rows)
        query = 'SELECT COUNT(*) AS total FROM records' if kind == 'mysql' else '{"find":"records","filter":{"value":23}}'
        assert service.execute(connection.id, query).rows
        assert service.delete_rows(connection.id, table, [key]).affected == 1
    readonly = service.save_connection(replace(connection, read_only=True))
    assert service.fetch_rows(readonly.id, table).total == 1
    with pytest.raises(PermissionError):
        service.insert_row(readonly.id, table, {'name': 'forbidden'})
    assert not service.list_history()[0].success
    assert len(ssh.destinations) >= 9
    assert all(destination == (connection.host, connection.port) for destination in ssh.destinations)
    assert all(method == 'password' for method, _ in ssh.auth_attempts)


def test_qt_connection_test_and_query_use_ssh(tunneled_case, qtbot, monkeypatch):
    service, connection, table, raw, ssh, kind = tunneled_case
    window = MainWindow(service, language='en')
    qtbot.addWidget(window)
    errors = []
    monkeypatch.setattr(window, 'show_error', errors.append)
    window.show()
    def idle():
        return not window.busy and QThreadPool.globalInstance().activeThreadCount() == 0
    try:
        qtbot.waitUntil(lambda: window.current_connection is not None, timeout=15000)
        qtbot.waitUntil(idle, timeout=30000)
        dialog = ConnectionDialog(parent=window, connection=connection, language='en')
        qtbot.addWidget(dialog)
        dialog.show()
        dialog.test_requested.connect(lambda candidate: window.test_dialog_connection(dialog, candidate))
        dialog.test_button.click()
        qtbot.waitUntil(lambda: 'successful' in dialog.test_status.text(), timeout=30000)
        dialog.reject()
        window.activate_connection(connection, table)
        qtbot.waitUntil(idle, timeout=30000)
        assert not errors, errors
        assert window.current_table == table and window.data_table.rowCount() == 1
        assert 'SSH' in window.connection_badge.text()
        query = 'SELECT * FROM records' if kind == 'mysql' else '{"find":"records","filter":{}}' if kind == 'mongo' else f'GET {table}'
        window.query_editor.setPlainText(query)
        window.run_query()
        qtbot.waitUntil(lambda: window._query_state in {'success', 'failed'}, timeout=30000)
        qtbot.waitUntil(idle, timeout=30000)
        assert window._query_state == 'success', window.query_status.text()
        assert window.query_result_table.rowCount() == 1
        assert len(ssh.destinations) >= 5
    finally:
        qtbot.waitUntil(idle, timeout=30000)
        window.close()
        qtbot.waitUntil(idle, timeout=30000)
