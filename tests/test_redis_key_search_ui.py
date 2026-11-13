"""Redis server searches are independent from the generic local tree filter."""
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QThreadPool

from baseskiff.models import Connection, TableInfo
from baseskiff.service import DatabaseService
from baseskiff.ui.window import MainWindow
from tests.redis_tree_helpers import database_node, key_children, seed_database


@pytest.fixture
def window(qtbot, tmp_path):
    widget = MainWindow(DatabaseService(tmp_path), language='en')
    qtbot.addWidget(widget)
    widget.show()
    qtbot.waitUntil(lambda: not widget.busy and widget.data_table.rowCount() > 0
                   and QThreadPool.globalInstance().activeThreadCount() == 0, timeout=15000)
    widget.connections.append(Connection('Redis search', kind='Redis', database='2'))
    widget.current_connection = widget.connections[-1]
    widget.current_table = ''
    widget.tables = []
    seed_database(widget, widget.current_connection, [])
    widget.update_context()
    yield widget
    qtbot.waitUntil(lambda: not widget.busy, timeout=15000)
    widget.close()


def test_search_controls_belong_to_current_redis_connection(window):
    assert window.redis_search_group.isVisible()
    database = database_node(window, window.current_connection)
    assert database.parent().text(0) == 'Redis search'
    assert 'DB2' in window.redis_key_heading.text()
    control = database.child(0)
    host = window.tree.itemWidget(control, 0)
    assert host.isAncestorOf(window.redis_search_group)
    assert window.redis_key_mode.currentData() == 'prefix'
    window.current_connection = window.connections[0]
    window.update_context()
    assert not window.redis_search_group.isVisible()


@pytest.mark.parametrize(('language', 'labels'), [('en', ['Prefix', 'Exact']), ('zh', ['前缀', '精确'])])
def test_search_mode_picker_offers_only_prefix_and_exact(window, language, labels):
    if window.language != language:
        window.toggle_language()

    combo = window.redis_key_mode
    assert [combo.itemData(index) for index in range(combo.count())] == ['prefix', 'exact']
    assert [combo.itemText(index) for index in range(combo.count())] == labels
    assert combo.currentData() == 'prefix'
    assert 'fuzzy' not in combo.toolTip().lower()
    assert '模糊' not in combo.toolTip()


@pytest.mark.parametrize('entrypoint', ['switch', 'activate'])
def test_legacy_fuzzy_state_restores_as_prefix_without_losing_text(window, monkeypatch, entrypoint):
    redis = window.current_connection
    window.current_connection = window.connections[0]
    window.update_context()
    text = ' term[*]? '
    scope = window._redis_scope(redis)
    window._redis_key_searches[scope] = (text, 'fuzzy')
    requests, calls = [], []
    monkeypatch.setattr(window, 'submit', lambda function, callback=None, error=None:
                        requests.append((function, callback, error)))

    def search(cid, query='', mode='prefix', *, database=None):
        calls.append((cid, query, mode, database))
        return SimpleNamespace(keys=[], truncated=False)

    monkeypatch.setattr(window.service, 'search_redis_keys', search)
    if entrypoint == 'activate':
        window.activate_connection(redis)
    else:
        window.current_connection = redis
        window.update_context()

    assert window._redis_key_searches[scope] == (text, 'prefix')
    assert window.redis_key_search.text() == text
    assert window.redis_key_mode.currentData() == 'prefix'
    if entrypoint == 'activate':
        # Connection activation discovers DB metadata; searching is DB-scoped.
        assert calls == []
        window._search_redis_keys()
        requests[-1][0]()
        assert calls == [(redis.id, text, 'prefix', redis.database)]


@pytest.mark.parametrize('mode,text', [('prefix', ' team:'), ('prefix', '*cart?*'), ('exact', ' key[*] ')])
def test_search_queries_server_without_stripping_key_text(window, qtbot, monkeypatch, mode, text):
    calls = []
    def search(cid, text='', mode='prefix', *, database=None):
        calls.append((cid, text, mode))
        return SimpleNamespace(keys=[TableInfo('found:outside:initial:page', kind='hash')], truncated=True)
    monkeypatch.setattr(window.service, 'search_redis_keys', search, raising=False)
    window.redis_key_mode.setCurrentIndex(window.redis_key_mode.findData(mode))
    window.redis_key_search.setText(text)
    window.redis_key_search.returnPressed.emit()
    qtbot.waitUntil(lambda: bool(calls) and not window.busy, timeout=10000)
    assert calls[-1] == (window.current_connection.id, text, mode)
    assert [table.name for table in window.tables] == ['found:outside:initial:page']
    assert window.current_table == ''
    assert '500' in window.redis_key_status.text()
    assert not window._tree_search_text
    assert window._connection_tables[window.connections[0].id]


def test_editing_draft_preserves_submitted_response_until_next_submission(window, monkeypatch):
    requests = []
    monkeypatch.setattr(window, 'submit', lambda function, callback=None, error=None:
                        requests.append((function, callback, error)))
    window.redis_key_search.setText('old:')
    window._search_redis_keys()
    window.redis_key_search.setText('new:')
    requests[-1][1](SimpleNamespace(keys=[TableInfo('old:submitted')], truncated=False))
    assert [table.name for table in window.tables] == ['old:submitted']
    assert window.redis_key_search.text() == 'new:'
    assert len(requests) == 1
    window._search_redis_keys()
    requests[-1][1](SimpleNamespace(keys=[TableInfo('new:fresh')], truncated=False))
    assert [table.name for table in window.tables] == ['new:fresh']


@pytest.mark.parametrize('mode', ['prefix', 'exact'])
def test_search_state_and_results_survive_language_change(window, monkeypatch, mode):
    requests = []
    monkeypatch.setattr(window, 'submit', lambda function, callback=None, error=None:
                        requests.append((function, callback, error)))
    window.redis_key_mode.setCurrentIndex(window.redis_key_mode.findData(mode))
    window.redis_key_search.setText('user:')
    window._search_redis_keys()
    requests[-1][1](SimpleNamespace(keys=[TableInfo('user:42')], truncated=False))
    window.toggle_language()
    assert window.redis_key_search.text() == 'user:'
    assert window.redis_key_mode.currentData() == mode
    assert window.redis_key_mode.currentText() == {'prefix': '前缀', 'exact': '精确'}[mode]
    assert [table.name for table in window.tables] == ['user:42']


def test_connection_switch_ignores_old_response_and_restores_its_search(window, monkeypatch):
    requests = []
    monkeypatch.setattr(window, 'submit', lambda function, callback=None, error=None:
                        requests.append((function, callback, error)))
    redis = window.current_connection
    window.redis_key_search.setText('customer:')
    window._search_redis_keys()
    old_callback = requests[-1][1]
    window.activate_connection(window.connections[0])
    old_callback(SimpleNamespace(keys=[TableInfo('wrong:connection')], truncated=False))
    assert not window.tables
    window.activate_connection(redis)
    assert window.redis_key_search.text() == 'customer:'


def test_server_search_reveals_paths_and_clear_restores_collapsed_folders(window, monkeypatch):
    requests = []
    monkeypatch.setattr(window, 'submit', lambda function, callback=None, error=None:
                        requests.append((function, callback, error)))
    connection = window.current_connection
    root = seed_database(window, connection, [TableInfo('team:cart:42')])
    key_children(root)[0].setExpanded(False)
    window.redis_key_search.setText('team:')
    window._search_redis_keys()
    requests[-1][1](SimpleNamespace(keys=[TableInfo('team:cart:42')], truncated=False))
    root = database_node(window, connection)
    branch = key_children(root)[0]
    assert branch.isExpanded() and branch.child(0).isExpanded()
    window.redis_key_search.clear()
    window._search_redis_keys()
    requests[-1][1](SimpleNamespace(keys=[TableInfo('team:cart:42')], truncated=False))
    root = database_node(window, connection)
    assert not key_children(root)[0].isExpanded()


def test_new_search_supersedes_earlier_search_failure(window, monkeypatch):
    requests, errors = [], []
    monkeypatch.setattr(window, 'submit', lambda function, callback=None, error=None:
                        requests.append((function, callback, error)))
    monkeypatch.setattr(window, 'show_error', errors.append)
    window._search_redis_keys()
    initial_error = requests[-1][2]
    window.redis_key_search.setText('latest:')
    window._search_redis_keys()
    requests[-1][1](SimpleNamespace(keys=[TableInfo('latest:42')], truncated=False))
    initial_error('obsolete network error')
    assert errors == []
    assert 'failed' not in window.connection_badge.text().lower()
