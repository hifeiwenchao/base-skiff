"""Native table tabs retain state and mutation targets on actual local services."""
import uuid
from threading import Event
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QThreadPool, QTimer
from PySide6.QtWidgets import QApplication

from baseskiff.models import Connection
from baseskiff.ui.dialogs import RowDialog
from baseskiff.ui.window import MainWindow
from tests.redis_tree_helpers import database_node, node_scope

pytestmark = pytest.mark.integration


def drain(qtbot, window):
    qtbot.waitUntil(lambda: not window.busy
                   and QThreadPool.globalInstance().activeThreadCount() == 0, timeout=30000)


def open_window(qtbot, monkeypatch, service, connection, table):
    saved = service.save_connection(connection)
    window = MainWindow(service, language='en')
    qtbot.addWidget(window)
    errors = []
    monkeypatch.setattr(window, 'show_error', errors.append)
    try:
        window.show()
        qtbot.waitUntil(lambda: window.current_connection is not None
                       and window.data_table.rowCount() > 0, timeout=15000)
        drain(qtbot, window)
        window.activate_connection(saved, table)
        drain(qtbot, window)
        assert not errors, errors
        assert window.current_table == table
    except BaseException:
        close_window(qtbot, window)
        raise
    return window, saved, errors


def close_window(qtbot, window):
    window.search_timer.stop()
    window.redis_key_timer.stop()
    drain(qtbot, window)
    window.close()
    drain(qtbot, window)


def tab_index(window, connection_id, database, table):
    identity = connection_id, database, table
    matches = [index for index in range(window.table_tabs.count())
               if window.table_tabs.tabData(index) == identity]
    assert len(matches) == 1, f'Expected exactly one tab for {table!r} in {database!r}'
    return matches[0]


def activate(qtbot, window, connection_id, database, table):
    window.table_tabs.setCurrentIndex(tab_index(window, connection_id, database, table))
    drain(qtbot, window)
    assert window.current_table == table


@pytest.fixture
def mysql_workspace(qtbot, monkeypatch, live_config, live_service, run_tag):
    import pymysql

    config = live_config['mysql']
    name = f'{run_tag}_tabs_{uuid.uuid4().hex[:6]}'
    raw = pymysql.connect(host=live_config['host'], port=config['port'], user='root',
                          password=config['root_password'], autocommit=True,
                          cursorclass=pymysql.cursors.DictCursor,
                          connect_timeout=8, read_timeout=10, write_timeout=10)
    window = None
    created = False
    try:
        with raw.cursor() as cursor:
            cursor.execute(f'CREATE DATABASE `{name}`')
            created = True
            for table, note in [('left_records', 'left original'), ('right_records', 'right original')]:
                cursor.execute(f'CREATE TABLE `{name}`.`{table}` '
                               '(id INT PRIMARY KEY, note VARCHAR(100) NOT NULL)')
                cursor.execute(f'INSERT INTO `{name}`.`{table}` VALUES (1, %s)', (note,))
        window, saved, errors = open_window(qtbot, monkeypatch, live_service, Connection(
            name=name, kind='MySQL', host=live_config['host'], port=config['port'],
            username='root', password=config['root_password'], database=name), 'left_records')
        yield SimpleNamespace(window=window, saved=saved, errors=errors, raw=raw, name=name)
    finally:
        try:
            if window is not None:
                close_window(qtbot, window)
        finally:
            try:
                if created:
                    with raw.cursor() as cursor:
                        cursor.execute(f'DROP DATABASE `{name}`')
            finally:
                raw.close()


@pytest.fixture
def redis_workspace(qtbot, monkeypatch, live_config, live_service, run_tag):
    from redis import Redis

    config = live_config['redis']
    prefix = f'{run_tag}:tabs:{uuid.uuid4().hex[:6]}'
    text_key = prefix + ':text'
    binary_key = prefix.encode() + b':hash:\xff'
    shared_key = prefix + ':same-key'
    clients = [Redis(host=live_config['host'], port=config['port'], db=index,
                     username=config.get('username') or None, password=config.get('password') or None,
                     protocol=2, decode_responses=False, socket_connect_timeout=8, socket_timeout=10)
               for index in (0, 1)]
    window = None
    try:
        clients[0].set(text_key, 'original string')
        clients[0].hset(binary_key, mapping={'name': 'binary original', 'other': 'keep'})
        for index, client in enumerate(clients):
            client.hset(shared_key, mapping={'name': f'database-{index}', 'other': 'keep'})
        window, saved, errors = open_window(qtbot, monkeypatch, live_service, Connection(
            name=prefix, kind='Redis', host=live_config['host'], port=config['port'],
            username=config.get('username', ''), password=config.get('password', ''), database='0'), text_key)
        yield SimpleNamespace(window=window, saved=saved, errors=errors, clients=clients,
                              text_key=text_key, binary_key=binary_key, shared_key=shared_key)
    finally:
        try:
            if window is not None:
                close_window(qtbot, window)
        finally:
            for client in clients:
                try:
                    client.delete(text_key, binary_key, shared_key)
                finally:
                    client.close()


def test_mysql_table_tabs_retain_independent_query_drafts_and_results(qtbot, mysql_workspace):
    workspace = mysql_workspace
    window, saved = workspace.window, workspace.saved
    window.query_editor.setPlainText('SELECT 11 AS left_answer')
    window.run_button.click()
    drain(qtbot, window)
    assert window.query_result_table.item(0, 0).text() == '11'
    window.query_editor.setPlainText('SELECT 111 AS unsaved_left_draft')
    window.open_table('right_records')
    drain(qtbot, window)
    assert window.current_result.rows == [{'id': 1, 'note': 'right original'}]
    window.query_editor.setPlainText('SELECT 22 AS right_answer')
    window.run_button.click()
    drain(qtbot, window)

    activate(qtbot, window, saved.id, saved.database, 'left_records')
    assert window.query_editor.toPlainText() == 'SELECT 111 AS unsaved_left_draft'
    assert window.query_result_table.item(0, 0).text() == '11'
    assert 'Success' in window.query_status.text()
    assert window.tabs.currentIndex() == 1
    assert window.current_result.rows == [{'id': 1, 'note': 'left original'}]
    window.open_table('right_records')
    assert window.query_editor.toPlainText() == 'SELECT 22 AS right_answer'
    assert window.query_result_table.item(0, 0).text() == '22'
    assert not workspace.errors, workspace.errors


def test_mysql_edit_finishing_in_background_updates_only_its_table(qtbot, monkeypatch, mysql_workspace):
    workspace = mysql_workspace
    window, saved = workspace.window, workspace.saved
    window.open_table('right_records')
    drain(qtbot, window)
    activate(qtbot, window, saved.id, saved.database, 'left_records')
    started, release = Event(), Event()
    original = window.service.update_row

    def delayed_update(*args, **kwargs):
        started.set()
        if not release.wait(10):
            raise RuntimeError('Timed out waiting to complete the table-tab edit')
        return original(*args, **kwargs)

    monkeypatch.setattr(window.service, 'update_row', delayed_update)
    failures = []

    def accept_edit():
        dialog = QApplication.activeModalWidget()
        try:
            assert isinstance(dialog, RowDialog)
            dialog.fields['note'].setText('left edited while inactive')
            dialog.accept()
        except Exception as error:
            failures.append(str(error))
            if dialog is not None:
                dialog.reject()

    try:
        window.data_table.selectRow(0)
        QTimer.singleShot(0, accept_edit)
        window.edit_selected_row()
        assert not failures, failures
        qtbot.waitUntil(started.is_set, timeout=5000)
        window.table_tabs.setCurrentIndex(tab_index(window, saved.id, saved.database, 'right_records'))
        assert window.current_result.rows == [{'id': 1, 'note': 'right original'}]
    finally:
        release.set()
    drain(qtbot, window)
    assert window.current_table == 'right_records'
    assert window.current_result.rows == [{'id': 1, 'note': 'right original'}]
    activate(qtbot, window, saved.id, saved.database, 'left_records')
    assert window.current_result.rows == [{'id': 1, 'note': 'left edited while inactive'}]
    with workspace.raw.cursor() as cursor:
        cursor.execute(f'SELECT note FROM `{workspace.name}`.left_records WHERE id=1')
        assert cursor.fetchone()['note'] == 'left edited while inactive'
        cursor.execute(f'SELECT note FROM `{workspace.name}`.right_records WHERE id=1')
        assert cursor.fetchone()['note'] == 'right original'
    assert not workspace.errors, workspace.errors


def test_redis_tabs_keep_unsaved_string_and_binary_key_mutation_identity(qtbot, redis_workspace):
    workspace = redis_workspace
    window, saved = workspace.window, workspace.saved
    window.redis_view.string_editor.setPlainText('unsaved string\n00042')
    query = 'GET ' + workspace.text_key
    window.query_editor.setPlainText(query)
    window.tabs.setCurrentIndex(1)
    window.open_table(workspace.binary_key)
    drain(qtbot, window)
    assert window.current_table == workspace.binary_key
    assert window._redis_snapshot.key == workspace.binary_key
    window.redis_view.mutation_requested.emit('update', {
        'entry': 'name', 'expected': 'binary original', 'value': 'binary edited'})
    drain(qtbot, window)
    assert workspace.clients[0].hget(workspace.binary_key, 'name') == b'binary edited'

    activate(qtbot, window, saved.id, '0', workspace.text_key)
    assert window.redis_view.string_editor.toPlainText() == 'unsaved string\n00042'
    assert window.query_editor.toPlainText() == query
    assert window.tabs.currentIndex() == 1
    assert workspace.clients[0].get(workspace.text_key) == b'original string'
    activate(qtbot, window, saved.id, '0', workspace.binary_key)
    assert window._redis_snapshot.value['name'] == 'binary edited'
    assert window.table_tabs.tabData(window.table_tabs.currentIndex()) == (saved.id, '0', workspace.binary_key)
    assert not workspace.errors, workspace.errors


def test_redis_background_write_stays_in_origin_db_when_same_key_tab_opens(qtbot, monkeypatch, redis_workspace):
    workspace = redis_workspace
    window, saved = workspace.window, workspace.saved
    key = workspace.shared_key
    window.open_table(key)
    drain(qtbot, window)
    started, release = Event(), Event()
    original = window.service.mutate_redis_entry

    def delayed_mutation(*args, **kwargs):
        started.set()
        if not release.wait(10):
            raise RuntimeError('Timed out waiting to complete the Redis tab edit')
        return original(*args, **kwargs)

    monkeypatch.setattr(window.service, 'mutate_redis_entry', delayed_mutation)
    try:
        window.redis_view.mutation_requested.emit('update', {
            'entry': 'name', 'expected': 'database-0', 'value': 'edited DB0 in background'})
        qtbot.waitUntil(started.is_set, timeout=5000)
        database = database_node(window, saved, '1')
        assert node_scope(database) == (saved.id, '1')
        window.tree_clicked(database, 0)
        qtbot.waitUntil(lambda: window.current_connection.database == '1', timeout=10000)
        window.open_table(key)
        qtbot.waitUntil(lambda: window._redis_snapshot is not None
                       and window._redis_snapshot.value.get('name') == 'database-1', timeout=10000)
        assert window.table_tabs.tabData(window.table_tabs.currentIndex()) == (saved.id, '1', key)
    finally:
        release.set()
    drain(qtbot, window)
    assert window.current_connection.database == '1'
    assert window._redis_snapshot.value['name'] == 'database-1'
    assert workspace.clients[0].hget(key, 'name') == b'edited DB0 in background'
    assert workspace.clients[1].hget(key, 'name') == b'database-1'
    activate(qtbot, window, saved.id, '0', key)
    assert window.current_connection.database == '0'
    assert window._redis_search_owner == (saved.id, '0')
    assert node_scope(database_node(window, saved, '0')) == (saved.id, '0')
    assert window._redis_snapshot.value['name'] == 'edited DB0 in background'
    assert not workspace.errors, workspace.errors
