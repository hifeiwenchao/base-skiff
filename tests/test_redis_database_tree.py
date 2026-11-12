"""Redis browsing belongs to DB branches, including its native search controls."""
from contextlib import contextmanager

import fakeredis
import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, Qt, QThreadPool
from PySide6.QtWidgets import QApplication
from shiboken6 import isValid

from baseskiff.adapters import RedisAdapter
from baseskiff.models import (
    Connection,
    RedisDatabaseInfo,
    RedisDatabaseList,
    RedisKeyPage,
    TableInfo,
)
from baseskiff.service import DatabaseService
from baseskiff.ui.redis_tree import NODE_ID_ROLE, NODE_KIND_ROLE, iter_items
from baseskiff.ui.window import MainWindow
from tests.ui_mouse import double_click

SCOPE_ROLE = Qt.ItemDataRole.UserRole + 5


def db_node(window, index):
    return next(item for item in iter_items(window.tree.invisibleRootItem())
                if item.data(0, NODE_KIND_ROLE) == 'redis_database'
                and item.data(0, SCOPE_ROLE) == (window.redis_connection.id, str(index)))


def run_pending(window):
    """Deliver controlled worker results while exercising real service methods."""
    while window.requests:
        function, callback, error = window.requests.pop(0)
        try:
            result = function()
        except Exception as exc:
            if error is None:
                raise
            error(str(exc))
        else:
            if callback:
                callback(result)


@pytest.fixture
def window(qtbot, tmp_path, monkeypatch):
    widget = MainWindow(DatabaseService(tmp_path), language='en')
    qtbot.addWidget(widget)
    widget.show()
    qtbot.waitUntil(lambda: not widget.busy and widget.data_table.rowCount() > 0
                   and QThreadPool.globalInstance().activeThreadCount() == 0, timeout=15000)
    server = fakeredis.FakeServer()

    @contextmanager
    def session(adapter, *, database=None):
        index = int((adapter.connection.database or '0') if database is None else database)
        with fakeredis.FakeRedis(server=server, db=index, decode_responses=False) as client:
            yield client

    monkeypatch.setattr(RedisAdapter, 'session', session)
    connection = widget.service.save_connection(Connection('Scoped Redis', kind='Redis', database='0'))
    widget.redis_connection = connection
    widget.connections = [connection]
    widget.current_connection = connection
    widget.current_table = ''
    widget.tables = []
    catalog = RedisDatabaseList([RedisDatabaseInfo(0, 2), RedisDatabaseInfo(1, 1),
                                 RedisDatabaseInfo(2, 0)], complete=True)
    monkeypatch.setattr(widget.service, 'list_redis_databases', lambda _cid: catalog)
    widget.requests = []
    monkeypatch.setattr(widget, 'submit', lambda function, callback=None, error=None:
                        widget.requests.append((function, callback, error)))
    widget._redis_database_catalogs[connection.id] = catalog
    with fakeredis.FakeRedis(server=server, db=0) as client:
        client.set('team:one', 'zero')
        client.set(b'raw:\xff', b'\x81\xa1x\x01')
    with fakeredis.FakeRedis(server=server, db=1) as client:
        client.set('team:one', 'one')
    widget.rebuild_tree()
    widget.update_context()
    yield widget
    widget.redis_key_timer.stop()
    widget.close()


def test_connection_first_level_is_databases_including_empty_db(window):
    root = window.tree.topLevelItem(0)
    assert [root.child(i).data(0, NODE_KIND_ROLE) for i in range(root.childCount())] == [
        'redis_database', 'redis_database', 'redis_database']
    assert [root.child(i).text(0) for i in range(root.childCount())] == [
        'DB0  [2]', 'DB1  [1]', 'DB2  [0]']
    assert db_node(window, 2).data(0, NODE_ID_ROLE) == (
        'redis_database', window.redis_connection.id, '2')


def test_connection_activation_only_discovers_dbs_and_db_expansion_loads_once(window):
    calls = []
    search = window.service.search_redis_keys
    window.service.search_redis_keys = lambda cid, text='', mode='prefix', *, database=None: (
        calls.append((text, mode, database)) or search(cid, text, mode, database=database))
    window.activate_connection(window.redis_connection)
    run_pending(window)
    assert calls == [], 'Opening a connection must not scan keys before a DB is expanded'
    db_node(window, 0).setExpanded(True)
    run_pending(window)
    assert calls == [('', 'prefix', '0')]
    assert {item.data(0, Qt.ItemDataRole.UserRole)[1]
            for item in iter_items(db_node(window, 0))
            if item.data(0, NODE_KIND_ROLE) == 'key'} == {'team:one', b'raw:\xff'}
    db_node(window, 0).setExpanded(False)
    db_node(window, 0).setExpanded(True)
    run_pending(window)
    assert len(calls) == 1, 'Reopening a clean cached DB must be local'
    assert not any(item.data(0, NODE_KIND_ROLE) == 'key' for item in iter_items(db_node(window, 1)))


def test_search_controls_are_inside_only_the_active_expanded_db(window):
    db_node(window, 0).setExpanded(True)
    run_pending(window)
    database = db_node(window, 0)
    controls = database.child(0)
    assert controls.data(0, NODE_KIND_ROLE) == 'redis_search'
    host = window.tree.itemWidget(controls, 0)
    assert host is not None and host.isAncestorOf(window.redis_key_search)
    assert window.redis_key_search.isVisible()
    assert 'DB0' in window.redis_key_heading.text().replace(' ', '')
    database.setExpanded(False)
    assert not window.redis_key_search.isVisible()
    assert window.side_search.isVisible()


def test_search_survives_tree_rebuild_and_preserves_utf16_selection(window, qtbot):
    db_node(window, 0).setExpanded(True)
    run_pending(window)
    search = window.redis_key_search
    search.setText('team:😀:order')
    window.redis_key_timer.stop()
    search.setFocus()
    search.setSelection(5, 2)  # QLineEdit positions count UTF-16 units.
    qtbot.waitUntil(search.hasFocus)
    for _ in range(3):
        window.rebuild_tree()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        QApplication.processEvents()
        assert isValid(search)
        assert search.isVisible() and search.hasFocus()
        assert search.selectedText() == '😀'
        assert search.selectionStart() == 5 and search.cursorPosition() == 7
    window.redis_key_timer.stop()
    window.query_editor.setFocus()
    qtbot.waitUntil(window.query_editor.hasFocus)
    window.rebuild_tree()
    QApplication.processEvents()
    assert window.query_editor.hasFocus(), 'A background tree refresh must not steal editor focus'


@pytest.mark.parametrize('gesture', ['label', 'arrow', 'keyboard', 'double_click'])
def test_native_unloaded_db_expansion_loads_the_database_once(window, qtbot, gesture):
    database = db_node(window, 2)
    window.tree.scrollToItem(database)
    rectangle = window.tree.visualItemRect(database)
    label = QPoint(rectangle.left() + 38, rectangle.center().y())
    if gesture == 'keyboard':
        window.tree.setCurrentItem(database)
        window.tree.setFocus()
        qtbot.keyClick(window.tree, Qt.Key.Key_Right)
    elif gesture == 'double_click':
        double_click(window.tree.viewport(), label)
    else:
        point = label if gesture == 'label' else QPoint(
            rectangle.left() - window.tree.indentation() // 2, rectangle.center().y())
        qtbot.mouseClick(window.tree.viewport(), Qt.MouseButton.LeftButton, pos=point)
    run_pending(window)
    assert db_node(window, 2).isExpanded()
    assert window.current_connection.database == '2'
    assert window._redis_key_pages[(window.redis_connection.id, '2')].keys == []


def test_db_branches_keep_distinct_raw_key_scope(window):
    cid = window.redis_connection.id
    for database in ('0', '1'):
        window._redis_key_pages[(cid, database)] = RedisKeyPage([TableInfo(b'raw:\xff')])
    window.rebuild_tree()
    keys = [item for item in iter_items(window.tree.invisibleRootItem())
            if item.data(0, NODE_KIND_ROLE) == 'key']
    assert len(keys) == 2
    assert {item.data(0, SCOPE_ROLE) for item in keys} == {(cid, '0'), (cid, '1')}
    assert {item.data(0, NODE_ID_ROLE) for item in keys} == {
        ('key', cid, '0', b'raw:\xff'), ('key', cid, '1', b'raw:\xff')}
    assert all(item.data(0, Qt.ItemDataRole.UserRole) == (cid, b'raw:\xff') for item in keys)


def test_key_click_uses_its_db_after_browsing_another_database(window):
    for database in (0, 1):
        db_node(window, database).setExpanded(True)
        run_pending(window)
    assert window.current_connection.database == '1'
    zero_key = next(item for item in iter_items(db_node(window, 0))
                    if item.data(0, NODE_KIND_ROLE) == 'key'
                    and item.data(0, Qt.ItemDataRole.UserRole)[1] == 'team:one')
    window.tree_clicked(zero_key, 0)
    run_pending(window)
    assert window.current_connection.database == '0'
    assert window.current_table == 'team:one'
    assert window._redis_snapshot.value == 'zero'
    one_key = next(item for item in iter_items(db_node(window, 1))
                   if item.data(0, NODE_KIND_ROLE) == 'key'
                   and item.data(0, Qt.ItemDataRole.UserRole)[1] == 'team:one')
    window.tree_clicked(one_key, 0)
    run_pending(window)
    assert window.current_connection.database == '1'
    assert window._redis_snapshot.value == 'one'
    assert (window.redis_connection.id, '0', 'team:one') in window._workspace_tabs
    assert (window.redis_connection.id, '1', 'team:one') in window._workspace_tabs


def test_late_db_search_updates_its_branch_without_replacing_active_search(window):
    db_node(window, 0).setExpanded(True)
    run_pending(window)
    window.redis_key_search.setText('team:')
    window.redis_key_mode.setCurrentIndex(window.redis_key_mode.findData('exact'))
    window._search_redis_keys()
    old_request = window.requests.pop()
    db_node(window, 1).setExpanded(True)
    run_pending(window)
    assert window.current_connection.database == '1'
    window.redis_key_search.setText('current:')
    window.redis_key_timer.stop()
    old_request[1](RedisKeyPage([TableInfo('team:late')]))
    assert window.current_connection.database == '1'
    assert window.redis_key_search.text() == 'current:'
    assert window.redis_key_mode.currentData() == 'prefix'
    assert not any(item.data(0, NODE_KIND_ROLE) == 'key' and
                   item.data(0, Qt.ItemDataRole.UserRole)[1] == 'team:late'
                   for item in iter_items(db_node(window, 1)))
    assert any(item.data(0, NODE_KIND_ROLE) == 'key' and
               item.data(0, Qt.ItemDataRole.UserRole)[1] == 'team:late'
               for item in iter_items(db_node(window, 0)))
    db_node(window, 0).setExpanded(False)
    db_node(window, 0).setExpanded(True)
    run_pending(window)
    assert window.redis_key_search.text() == 'team:'
    assert window.redis_key_mode.currentData() == 'exact'


def test_language_and_refresh_preserve_explicit_db_and_connection_collapse(window):
    db_node(window, 0).setExpanded(True)
    run_pending(window)
    window.redis_key_search.setText('team:')
    window._search_redis_keys()
    run_pending(window)
    db_node(window, 0).setExpanded(False)
    window.toggle_language()
    assert not db_node(window, 0).isExpanded()
    assert not window.redis_key_search.isVisible()
    window.tree.topLevelItem(0).setExpanded(False)
    window.rebuild_tree()
    assert not window.tree.topLevelItem(0).isExpanded()
    assert not db_node(window, 0).isExpanded()
    assert not window.redis_key_search.isVisible()


def test_search_mode_popup_stays_open_during_catalog_refresh(window, qtbot):
    db_node(window, 0).setExpanded(True)
    run_pending(window)
    window._load_redis_databases()
    request = window.requests.pop()
    mode = window.redis_key_mode
    mode.showPopup()
    assert mode.view().isVisible()
    request[1](RedisDatabaseList([RedisDatabaseInfo(0, 3), RedisDatabaseInfo(1, 1),
                                RedisDatabaseInfo(2, 0)], complete=True))
    QApplication.processEvents()
    assert mode.view().isVisible(), 'A background count update must not dismiss the search mode menu'
    mode.hidePopup()
    qtbot.waitUntil(lambda: db_node(window, 0).text(0) == 'DB0  [3]')


@pytest.mark.parametrize('width', [200, 240, 560])
@pytest.mark.parametrize('language', ['zh', 'en'])
def test_embedded_search_fits_sidebar_and_keeps_inputs_visible(window, qtbot, width, language):
    window.language = language
    window.translator.set_language(language)
    window.retranslate()
    window.shell_splitter.setSizes([width, window.width() - width - 6])
    db_node(window, 0).setExpanded(True)
    run_pending(window)
    window.tree.scrollToItem(db_node(window, 0).child(0))
    QApplication.processEvents()
    search_row = db_node(window, 0).child(0)
    host = window.tree.itemWidget(search_row, 0)
    assert host is not None
    assert window.redis_key_search.isVisible() and window.redis_key_mode.isVisible()
    assert host.height() >= window.redis_search_group.sizeHint().height()
    search_rect = window.redis_key_search.rect()
    search_rect.moveTopLeft(window.redis_key_search.mapTo(window.tree.viewport(), search_rect.topLeft()))
    assert window.tree.viewport().rect().contains(search_rect)
    mode_rect = window.redis_key_mode.rect()
    mode_rect.moveTopLeft(window.redis_key_mode.mapTo(window.tree.viewport(), mode_rect.topLeft()))
    assert window.tree.viewport().rect().contains(mode_rect)
    assert window.redis_key_search.width() >= 60
    assert window.redis_key_mode.width() >= 65, 'Prefix/Exact must remain readable and clickable'
    assert window.redis_key_mode.height() >= 22


def test_search_control_context_menu_cannot_target_connection(window, monkeypatch):
    db_node(window, 0).setExpanded(True)
    run_pending(window)
    control = db_node(window, 0).child(0)
    assert control.data(0, NODE_KIND_ROLE) == 'redis_search'
    monkeypatch.setattr(window.tree, 'itemAt', lambda _point: control)
    menus = []
    monkeypatch.setattr('baseskiff.ui.window.QMenu', lambda *_args: menus.append('unexpected'))
    window.tree_menu(QPoint())
    assert menus == []


def test_catalog_failure_and_missing_counts_keep_database_navigation(window):
    window._redis_database_catalogs[window.redis_connection.id] = RedisDatabaseList(
        [RedisDatabaseInfo(0, None), RedisDatabaseInfo(3, None)], complete=False)
    window.rebuild_tree()
    root = window.tree.topLevelItem(0)
    assert root.child(0).text(0) == 'DB0'
    assert root.child(1).text(0) == 'DB3'
    assert 'another DB' in root.child(2).text(0)
    assert root.child(2).data(0, NODE_KIND_ROLE) != 'key'


def test_fast_db_expansion_honors_the_latest_requested_database(window):
    db_node(window, 1).setExpanded(True)
    # DB1 validation is still queued when the user expands another database.
    db_node(window, 2).setExpanded(True)
    run_pending(window)
    assert window.current_connection.database == '2'
    assert (window.redis_connection.id, '2') in window._redis_key_pages


def test_returning_to_original_db_supersedes_an_inflight_switch(window):
    db_node(window, 1).setExpanded(True)
    db_node(window, 0).setExpanded(True)
    run_pending(window)
    assert window.current_connection.database == '0'
    assert window.redis_key_heading.text().replace(' ', '').endswith('DB0')


def test_write_refresh_supersedes_a_search_started_before_the_write(window):
    db_node(window, 0).setExpanded(True)
    run_pending(window)
    window._search_redis_keys()
    stale = window.requests.pop()
    window.service.execute(window.redis_connection.id, 'SET created 1', database='0')
    window.refresh_catalog(window.current_connection)
    stale[1](RedisKeyPage([TableInfo('team:one')]))
    assert window.requests, 'A catalog invalidated by a write must schedule a fresh read'
    run_pending(window)
    assert 'created' in {table.name for table in
                         window._redis_key_pages[(window.redis_connection.id, '0')].keys}


def test_two_key_clicks_during_db_validation_open_the_latest_key(window):
    cid = window.redis_connection.id
    window.service.execute(cid, 'SET first first-value', database='1')
    window.service.execute(cid, 'SET second second-value', database='1')
    window._redis_key_pages[(cid, '1')] = RedisKeyPage([TableInfo('first'), TableInfo('second')])
    window.rebuild_tree()
    keys = {item.data(0, Qt.ItemDataRole.UserRole)[1]: item
            for item in iter_items(db_node(window, 1))
            if item.data(0, NODE_KIND_ROLE) == 'key'}
    window.tree_clicked(keys['first'], 0)
    window.tree_clicked(keys['second'], 0)
    run_pending(window)
    assert window.current_connection.database == '1'
    assert window.current_table == 'second'
    assert window._redis_snapshot.value == 'second-value'


def test_key_click_in_original_db_supersedes_a_pending_other_db_switch(window):
    db_node(window, 0).setExpanded(True)
    run_pending(window)
    zero_key = next(item for item in iter_items(db_node(window, 0))
                    if item.data(0, NODE_KIND_ROLE) == 'key'
                    and item.data(0, Qt.ItemDataRole.UserRole)[1] == 'team:one')
    db_node(window, 1).setExpanded(True)
    window.tree_clicked(zero_key, 0)
    run_pending(window)
    assert window.current_connection.database == '0'
    assert window.current_table == 'team:one'
    assert window._redis_snapshot.value == 'zero'


def test_search_clear_result_while_mode_menu_is_open_restores_folder_collapse(window, qtbot):
    def team():
        return next(item for item in iter_items(db_node(window, 0))
                    if item.data(0, NODE_KIND_ROLE) == 'redis_folder'
                    and item.text(0).startswith('team ('))

    db_node(window, 0).setExpanded(True)
    run_pending(window)
    team().setExpanded(False)
    window.redis_key_search.setText('team:')
    window._search_redis_keys()
    run_pending(window)
    assert team().isExpanded()
    window.redis_key_search.clear()
    window._search_redis_keys()
    result = window.requests.pop()
    window.redis_key_mode.showPopup()
    result[1](result[0]())
    assert window.redis_key_mode.view().isVisible()
    window.redis_key_mode.hidePopup()
    qtbot.waitUntil(lambda: not window._tree_rebuild_pending)
    assert not team().isExpanded(), 'Temporary search expansion must not become the saved folder state'


def test_reopening_connection_mounts_search_for_its_already_expanded_db(window, qtbot):
    db_node(window, 0).setExpanded(True)
    run_pending(window)
    window.tree.topLevelItem(0).setExpanded(False)
    window.rebuild_tree()
    assert not window.redis_key_search.isVisible()
    assert db_node(window, 0).isExpanded()
    window.tree.topLevelItem(0).setExpanded(True)
    qtbot.waitUntil(lambda: window.redis_key_search.isVisible(), timeout=1000)
    control = db_node(window, 0).child(0)
    assert control.data(0, NODE_KIND_ROLE) == 'redis_search'
    assert window.tree.itemWidget(control, 0).isAncestorOf(window.redis_key_search)
    assert not window.requests, 'Reopening a cached connection must not scan its keys again'


def test_tab_navigation_in_original_db_is_not_replaced_by_late_db_validation(window):
    cid = window.redis_connection.id
    db_node(window, 0).setExpanded(True)
    run_pending(window)
    window.open_table('team:one')
    run_pending(window)
    window.open_table(b'raw:\xff')
    run_pending(window)
    window.table_tabs.setCurrentIndex(window._tab_index((cid, '0', 'team:one')))
    run_pending(window)
    db_node(window, 1).setExpanded(True)
    window.table_tabs.setCurrentIndex(window._tab_index((cid, '0', b'raw:\xff')))
    assert window.current_table == b'raw:\xff'
    run_pending(window)
    assert window.current_connection.database == '0'
    assert window.current_table == b'raw:\xff'
    assert window._redis_snapshot.value == b'\x81\xa1x\x01'
