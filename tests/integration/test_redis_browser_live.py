"""Native Redis key search and per-entry editing through direct and SSH routes."""
from contextlib import nullcontext

import pytest
from PySide6.QtCore import Qt, QThreadPool
from PySide6.QtWidgets import QMessageBox, QPushButton
from redis import Redis

from baseskiff.models import Connection
from baseskiff.ui.window import MainWindow
from tests.redis_tree_helpers import database_node, node_scope
from tests.ssh_test_server import TestSSHServer

pytestmark = pytest.mark.integration


def idle(qtbot, window):
    qtbot.waitUntil(lambda: not window.busy and QThreadPool.globalInstance().activeThreadCount() == 0,
                   timeout=30000)


@pytest.mark.parametrize('route', ['direct', 'ssh'])
def test_key_search_and_native_hash_field_crud(qtbot, monkeypatch, live_config, live_service, run_tag, route):
    config = live_config['redis']
    raw = Redis(host=live_config['host'], port=config['port'],
                username=config.get('username') or None, password=config.get('password') or None,
                protocol=2, decode_responses=True)
    prefix = run_tag + ':' + route
    key = prefix + ':cart:42'
    other = prefix + ':cart:420'
    try:
        raw.hset(key, mapping={'name': 'original', 'other': 'keep'})
        raw.pexpire(key, 120000)
        raw.set(other, 'not exact')
        with TestSSHServer() if route == 'ssh' else nullcontext() as ssh:
            options = dict(name='Redis browser ' + route, kind='Redis', database='0',
                           username=config.get('username', ''), password=config.get('password', ''))
            connection = (ssh.connection(live_config['host'], config['port'], **options) if ssh else
                          Connection(host=live_config['host'], port=config['port'], **options))
            saved = live_service.save_connection(connection)
            window = MainWindow(live_service, language='en')
            qtbot.addWidget(window)
            errors = []
            monkeypatch.setattr(window, 'show_error', errors.append)
            window.show()
            try:
                qtbot.waitUntil(lambda: window.current_connection is not None, timeout=15000)
                idle(qtbot, window)
                window.activate_connection(saved, key)
                idle(qtbot, window)
                assert window.redis_view.table.rowCount() == 2
                database = database_node(window, saved, '0')
                database.parent().setExpanded(True)
                database.setExpanded(True)
                qtbot.waitUntil(lambda: window.redis_search_group.isVisible(), timeout=15000)
                idle(qtbot, window)
                assert window.redis_search_group.isVisible()
                database = database_node(window, saved, '0')
                assert node_scope(database) == (saved.id, '0')
                host = window.tree.itemWidget(database.child(0), 0)
                assert host.isAncestorOf(window.redis_search_group)
                assert [window.redis_key_mode.itemData(index)
                        for index in range(window.redis_key_mode.count())] == ['prefix', 'exact']
                assert [window.redis_key_mode.itemText(index)
                        for index in range(window.redis_key_mode.count())] == ['Prefix', 'Exact']
                assert window.redis_key_mode.currentData() == 'prefix'
                for mode, term, wanted in [('prefix', prefix + ':cart:', {key, other}),
                                           ('exact', key, {key})]:
                    window.redis_key_mode.setCurrentIndex(window.redis_key_mode.findData(mode))
                    window.redis_key_search.setText(term)
                    window.redis_key_search.returnPressed.emit()
                    idle(qtbot, window)
                    assert {table.name for table in window.tables} == wanted
                view = window.redis_view
                row = next(i for i in range(view.table.rowCount()) if view.table.item(i, 0).text() == 'name')
                view.table.selectRow(row)
                qtbot.mouseClick(view.edit_button, Qt.MouseButton.LeftButton)
                dialog = view.entry_dialog
                assert dialog.entry_edit.isReadOnly()
                dialog.value_editor.setPlainText('00042\ntrue')
                qtbot.mouseClick(dialog.save_button, Qt.MouseButton.LeftButton)
                idle(qtbot, window)
                assert raw.hgetall(key) == {'name': '00042\ntrue', 'other': 'keep'}
                assert 90000 < raw.pttl(key) <= 120000
                qtbot.mouseClick(view.add_button, Qt.MouseButton.LeftButton)
                dialog = view.entry_dialog
                dialog.entry_edit.setText('new:field')
                dialog.value_editor.setPlainText('{"enabled":false}')
                qtbot.mouseClick(dialog.save_button, Qt.MouseButton.LeftButton)
                idle(qtbot, window)
                assert raw.hget(key, 'new:field') == '{"enabled":false}'
                row = next(i for i in range(view.table.rowCount()) if view.table.item(i, 0).text() == 'new:field')
                monkeypatch.setattr(QMessageBox, 'question', lambda *args, **kwargs: QMessageBox.StandardButton.Yes)
                delete = view.table.cellWidget(row, 2).findChild(QPushButton, 'redisRowDelete')
                qtbot.mouseClick(delete, Qt.MouseButton.LeftButton)
                idle(qtbot, window)
                assert not raw.hexists(key, 'new:field')
                assert raw.hget(key, 'other') == 'keep'
                operations = [entry for entry in live_service.list_history() if entry.query.startswith('REDIS ')]
                assert len(operations) == 3 and all(entry.success for entry in operations)
                window.toggle_language()
                assert window.redis_key_mode.currentText() == '精确'
                assert [window.redis_key_mode.itemText(index)
                        for index in range(window.redis_key_mode.count())] == ['前缀', '精确']
                assert view.table.horizontalHeaderItem(0).text() == '字段'
                assert not errors, errors
                if ssh:
                    assert ssh.destinations and all(target == (live_config['host'], config['port'])
                                                    for target in ssh.destinations)
            finally:
                idle(qtbot, window)
                window.close()
    finally:
        raw.delete(key, other)
        raw.close()


def test_real_redis_typed_operations_preserve_other_entries(live_config, live_service, run_tag):
    config = live_config['redis']
    raw = Redis(host=live_config['host'], port=config['port'],
                username=config.get('username') or None, password=config.get('password') or None,
                decode_responses=True, protocol=2)
    prefix = run_tag + ':typed'
    keys = [prefix + ':' + kind for kind in ('string', 'hash', 'list', 'set', 'zset', 'stream')]
    try:
        string, hash_key, list_key, set_key, zset, stream = keys
        raw.set(string, '001')
        raw.hset(hash_key, mapping={str(index): str(index) for index in range(1010)})
        raw.rpush(list_key, 'first', 'duplicate', 'duplicate')
        raw.sadd(set_key, 'old', 'other')
        raw.zadd(zset, {'one': 1.0, 'two': 2.0})
        raw.xadd(stream, {'message': 'hello'})
        for key in keys:
            raw.pexpire(key, 120000)
        connection = live_service.save_connection(Connection(
            name='Redis typed service', kind='Redis', host=live_config['host'], port=config['port'],
            username=config.get('username', ''), password=config.get('password', '')))
        for key, kind in zip(keys, ('string', 'hash', 'list', 'set', 'zset', 'stream')):
            snapshot = live_service.read_redis_key(connection.id, key)
            assert snapshot.kind == kind and snapshot.ttl_ms > 0
        assert live_service.read_redis_key(connection.id, hash_key).truncated
        for key, kind, action, payload in [
            (string, 'string', 'update', {'value': 'true', 'expected': '001'}),
            (hash_key, 'hash', 'update', {'entry': '1009', 'value': 'changed', 'expected': '1009'}),
            (list_key, 'list', 'delete', {'entry': 2, 'expected': 'duplicate'}),
            (set_key, 'set', 'update', {'entry': 'old', 'value': 'new'}),
            (zset, 'zset', 'update', {'entry': 'one', 'value': '3.5', 'expected': 1.0}),
        ]:
            assert live_service.mutate_redis_entry(connection.id, key, kind, action, payload).affected == 1
        assert raw.get(string) == 'true'
        assert raw.hlen(hash_key) == 1010 and raw.hget(hash_key, '0') == '0'
        assert raw.hget(hash_key, '1009') == 'changed'
        assert raw.lrange(list_key, 0, -1) == ['first', 'duplicate']
        assert raw.smembers(set_key) == {'new', 'other'}
        assert raw.zrange(zset, 0, -1, withscores=True) == [('two', 2.0), ('one', 3.5)]
        assert all(90000 < raw.pttl(key) <= 120000 for key in keys)
    finally:
        raw.delete(*keys)
        raw.close()
