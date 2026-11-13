"""DB selection must isolate queued operations and visible Redis workspaces."""
from dataclasses import replace
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QThreadPool

from baseskiff.models import Connection, QueryResult, TableInfo
from baseskiff.service import DatabaseService
from baseskiff.ui.redis_tree import NODE_ID_ROLE, NODE_KIND_ROLE
from baseskiff.ui.window import MainWindow
from tests.redis_tree_helpers import database_node, key_children, node_scope, seed_database


@pytest.fixture
def window(qtbot, tmp_path, monkeypatch):
    widget = MainWindow(DatabaseService(tmp_path), language='en')
    qtbot.addWidget(widget)
    widget.show()
    qtbot.waitUntil(lambda: not widget.busy and widget.data_table.rowCount() > 0
                   and QThreadPool.globalInstance().activeThreadCount() == 0, timeout=15000)
    redis = widget.service.save_connection(Connection('Redis DBs', kind='Redis', database='0'))
    widget.connections.append(redis)
    widget.current_connection = redis
    widget.current_table = ''
    widget.tables = []
    requests = []
    monkeypatch.setattr(widget, 'submit', lambda function, callback=None, error=None:
                        requests.append((function, callback, error)))
    widget.requests = requests
    widget._redis_database_catalogs[redis.id] = catalog()
    seed_database(widget, redis, [])
    widget.update_context()
    yield widget
    widget.close()


def catalog():
    return SimpleNamespace(databases=[SimpleNamespace(index=i, key_count=42 if i == 0 else 0)
                                     for i in range(4)], complete=True)


def expand_database(window, qtbot, database):
    previous = len(window.requests)
    item = database_node(window, window.current_connection, database)
    window.tree_clicked(item, 0)
    qtbot.waitUntil(lambda: len(window.requests) > previous)
    return window.requests[-1]


def test_db_nodes_belong_to_redis_and_search_follows_active_scope(window):
    connection = window.current_connection
    item = database_node(window, connection)
    assert item.parent().data(0, NODE_KIND_ROLE) == 'connection'
    assert node_scope(item) == (connection.id, '0')
    assert window.redis_search_group.isVisible()
    assert not hasattr(window, 'redis_database_combo')
    window.current_connection = window.connections[0]
    window.update_context()
    assert not window.redis_search_group.isVisible()
    assert database_node(window, connection).parent() is not None


def test_catalog_displays_empty_databases_and_key_counts(window, qtbot):
    window._load_redis_databases()
    window.requests[-1][1](catalog())
    qtbot.waitUntil(lambda: database_node(window, window.current_connection, '1').text(0) == 'DB1  [0]')
    root = database_node(window, window.current_connection).parent()
    assert root.childCount() == 4
    assert root.child(0).text(0) == 'DB0  [42]'
    assert root.child(1).text(0) == 'DB1  [0]'
    assert node_scope(root.child(0)) == (window.current_connection.id, '0')


def test_async_count_refresh_keeps_open_search_mode_menu_visible(window, qtbot):
    combo = window.redis_key_mode
    combo.showPopup()
    assert combo.view().isVisible()
    window._load_redis_databases()
    window.requests[-1][1](catalog())
    assert combo.view().isVisible()
    assert combo.currentData() == 'prefix'
    combo.hidePopup()
    qtbot.waitUntil(lambda: database_node(window, window.current_connection, '1').text(0) == 'DB1  [0]')
    assert window.redis_search_group.isVisible()


def test_successful_switch_clears_previous_keys_and_updates_connection(window, qtbot):
    old = window.current_connection
    seed_database(window, old, [TableInfo('old:key')])
    window.current_table = 'old:key'
    switch = expand_database(window, qtbot, '2')
    # Until validation succeeds the workspace still identifies its original DB.
    assert window.current_connection.database == '0'
    assert window.current_table == 'old:key'
    switch[1](replace(old, database='2'))
    qtbot.waitUntil(lambda: window.current_connection.database == '2')
    assert next(c for c in window.connections if c.id == old.id).database == '2'
    assert window.current_table == ''
    assert window._connection_tables[old.id] == []
    assert node_scope(database_node(window, old, '2')) == (old.id, '2')
    assert window._redis_key_pages[(old.id, '0')].keys == [TableInfo('old:key')]


def test_failed_switch_retains_original_data_and_selection(window, qtbot, monkeypatch):
    errors = []
    monkeypatch.setattr(window, 'show_error', errors.append)
    window.current_table = 'retained:key'
    switch = expand_database(window, qtbot, '1')
    switch[2]('DB index is out of range')
    qtbot.waitUntil(lambda: bool(errors))
    assert window.current_connection.database == '0'
    assert window.current_table == 'retained:key'
    assert window._redis_search_owner == (window.current_connection.id, '0')
    assert window.redis_search_group.isEnabled()
    assert errors == ['DB index is out of range']


def test_reactivating_original_db_during_switch_still_applies_success(window, qtbot):
    original = window.current_connection
    window.activate_connection(original)
    switch = expand_database(window, qtbot, '1')
    window.activate_connection(original)
    switch[1](replace(original, database='1'))
    qtbot.waitUntil(lambda: window.current_connection.database == '1')
    assert window._redis_search_owner == (original.id, '1')


def test_queued_read_and_search_keep_their_database(window, monkeypatch):
    calls = []
    monkeypatch.setattr(window.service, 'read_redis_key',
                        lambda cid, key, *, database=None: calls.append(('read', database)))
    monkeypatch.setattr(window.service, 'search_redis_keys',
                        lambda cid, text='', mode='prefix', *, database=None: calls.append(('search', database)))
    window.current_table = 'same:key'
    window.refresh_data()
    read = window.requests[-1][0]
    window._search_redis_keys()
    search = window.requests[-1][0]
    window.current_connection = replace(window.current_connection, database='1')
    read()
    search()
    assert calls == [('read', '0'), ('search', '0')]


def test_queued_write_keeps_db_and_does_not_refresh_same_key_in_other_db(window, monkeypatch):
    calls = []
    monkeypatch.setattr(window.service, 'mutate_redis_entry',
                        lambda *args, database=None: calls.append(database))
    window.current_table = 'same:key'
    window._redis_snapshot = SimpleNamespace(key='same:key', kind='hash')
    window._mutate_redis_entry('update', {'entry': 'a', 'value': 'new', 'expected': 'old'})
    function, callback, _ = window.requests[-1]
    window.current_connection = replace(window.current_connection, database='1')
    count = len(window.requests)
    function()
    callback(QueryResult(affected=1))
    assert calls == ['0']
    assert len(window.requests) == count
    assert not window._redis_writes


def test_new_key_draft_keeps_database_if_selection_changes_during_dialog(window, monkeypatch):
    from baseskiff.ui import window as window_module
    calls = []
    class DraftDialog:
        def __init__(self, *args, **kwargs):
            pass
        def setWindowTitle(self, title):
            pass
        def exec(self):
            window.current_connection = replace(window.current_connection, database='1')
            window._generation += 1
            return True
        def values(self):
            return {'key': 'draft:key', 'type': 'string', 'value': 'draft in DB0'}
    monkeypatch.setattr(window_module, 'RedisKeyDialog', DraftDialog)
    monkeypatch.setattr(window.service, 'insert_row',
                        lambda *args, database=None: calls.append(database))
    window.add_row()
    window.requests[-1][0]()
    assert calls == ['0']


def test_query_is_bound_to_database_at_run_time(window, monkeypatch):
    calls = []
    monkeypatch.setattr(window.service, 'execute',
                        lambda cid, query, *, database=None: calls.append((query, database)))
    window.query_editor.setPlainText('SET query:key zero')
    window.run_query()
    execute = window.requests[-1][0]
    window.current_connection = replace(window.current_connection, database='1')
    execute()
    assert calls == [('SET query:key zero', '0')]


def test_db_searches_are_separate_and_stale_results_are_ignored(window):
    old = window.current_connection
    window.redis_key_search.setText('original:')
    window._search_redis_keys()
    stale = window.requests[-1][1]
    window.activate_connection(replace(old, database='1'))
    assert window.redis_key_search.text() == ''
    stale(SimpleNamespace(keys=[TableInfo('original:stale')], truncated=False))
    assert window.tables == []
    window.activate_connection(old)
    assert window.redis_key_search.text() == 'original:'


def test_changing_db_does_not_store_search_expansion_as_original_state(window):
    connection = window.current_connection
    root = seed_database(window, connection, [TableInfo('team:cart:42')])
    folder = key_children(root)[0]
    identity = folder.data(0, NODE_ID_ROLE)
    folder.setExpanded(False)
    window.redis_key_search.setText('team:')
    window._search_redis_keys()
    window.requests[-1][1](SimpleNamespace(keys=[TableInfo('team:cart:42')], truncated=False))
    assert window._tree_expansion[identity] is False
    window.activate_connection(replace(connection, database='1'))
    assert window._tree_expansion[identity] is False


def test_old_catalog_cannot_replace_another_connections_active_scope(window, qtbot):
    window._load_redis_databases()
    old = window.requests[-1][1]
    other = Connection('Other Redis', kind='Redis', database='5')
    window.connections.append(other)
    window.activate_connection(other)
    old(catalog())
    assert window.current_connection.id == other.id
    assert window._redis_search_owner == (other.id, '5')
    qtbot.waitUntil(lambda: node_scope(database_node(window, other)) == (other.id, '5'))
    assert database_node(window, window.connections[-2], '1').text(0) == 'DB1  [0]'


def test_history_reopen_selects_original_database(window, monkeypatch):
    entry = SimpleNamespace(connection_id=window.current_connection.id, database='3', query='GET original:key')
    window._history = [entry]
    calls = []
    monkeypatch.setattr(window, 'activate_connection',
                        lambda connection, **kwargs: calls.append((connection.database, kwargs)))
    window.reopen_history(0)
    assert calls == [('3', {'open_query': 'GET original:key'})]
