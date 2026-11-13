"""Native tab menus operate on the tab that was right-clicked."""
import pytest
from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtWidgets import QApplication, QMenu

from tests.test_table_tab_races import DeferredOperations
from tests.test_table_tabs import idle, tab_index
from tests.test_table_tabs import window as window


def open_menu(window, point, operation):
    """Choose a real QAction from the popup while its native event loop runs."""
    outcomes = []

    def select():
        popup = QApplication.activePopupWidget()
        if not isinstance(popup, QMenu):
            outcomes.append('No tab context menu opened')
            return
        try:
            operation(popup)
        except BaseException as error:
            outcomes.append(error)
        finally:
            popup.close()

    QTimer.singleShot(0, select)
    window.table_tabs.customContextMenuRequested.emit(point)
    QApplication.processEvents()
    assert outcomes == []


def open_tables(window, qtbot):
    for table in ('orders', 'products'):
        window.open_table(table)
        idle(qtbot, window)
    window.query_editor.setPlainText('SELECT products -- retained draft')


@pytest.mark.parametrize('language,labels', [
    ('en', ['Close tab', 'Close other tabs', 'Close all tabs']),
    ('zh', ['关闭当前标签页', '关闭其他标签页', '关闭所有标签页']),
])
def test_tab_context_menu_closes_the_clicked_inactive_tab(window, qtbot, language, labels):
    open_tables(window, qtbot)
    window.language = language
    window.translator.set_language(language)
    window.retranslate()
    point = window.table_tabs.tabRect(tab_index(window, 'orders')).center()

    def choose(menu):
        assert [action.text() for action in menu.actions()] == labels
        menu.actions()[0].trigger()

    open_menu(window, point, choose)
    assert window.table_tabs.count() == 2
    assert window.current_table == 'products'
    assert {window.table_tabs.tabData(i)[2] for i in range(window.table_tabs.count())} == {
        'customers', 'products'}
    assert window.query_editor.toPlainText() == 'SELECT products -- retained draft'


def test_close_other_tabs_keeps_clicked_tab_and_its_saved_draft(window, qtbot):
    window.query_editor.setPlainText('SELECT customers -- keep this draft')
    open_tables(window, qtbot)
    kept = window.table_tabs.tabData(tab_index(window, 'customers'))
    point = window.table_tabs.tabRect(tab_index(window, 'customers')).center()
    open_menu(window, point, lambda menu: menu.actions()[1].trigger())
    idle(qtbot, window)
    assert window.table_tabs.count() == 1
    assert window.table_tabs.tabData(0) == kept
    assert window.current_table == 'customers'
    assert window.current_result.rows and window.total_rows == 128
    assert window.query_editor.toPlainText() == 'SELECT customers -- keep this draft'
    assert set(window._workspace_tabs) == {kept}


def test_menu_keeps_clicked_identity_if_tabs_are_reordered_while_open(window, qtbot):
    open_tables(window, qtbot)
    original = window.table_tabs.tabData(tab_index(window, 'orders'))
    point = window.table_tabs.tabRect(tab_index(window, 'orders')).center()

    def move_and_close(menu):
        window.table_tabs.moveTab(tab_index(window, 'orders'), 0)
        menu.actions()[0].trigger()

    open_menu(window, point, move_and_close)
    assert original not in window._workspace_tabs
    assert {window.table_tabs.tabData(i)[2] for i in range(window.table_tabs.count())} == {
        'customers', 'products'}


def test_single_tab_disables_close_others_and_close_current_leaves_empty_workspace(window):
    def choose(menu):
        assert menu.actions()[0].isEnabled()
        assert not menu.actions()[1].isEnabled()
        menu.actions()[0].trigger()

    open_menu(window, window.table_tabs.tabRect(0).center(), choose)
    assert window.table_tabs.count() == 0
    assert window._active_workspace is None
    assert not window.current_table


@pytest.mark.parametrize('extra_tables', [(), ('orders', 'products')])
def test_close_all_tabs_leaves_empty_workspace_and_preserves_source_data(window, qtbot, extra_tables):
    for table in extra_tables:
        window.open_table(table)
        idle(qtbot, window)
    connection = window.current_connection
    connections = window.service.list_connections()
    tables = list(window._connection_tables[connection.id])
    source_rows = window.service.fetch_rows(connection.id, 'customers').rows
    window.tree.topLevelItem(0).setExpanded(False)

    def choose(menu):
        assert menu.actions()[2].isEnabled()
        menu.actions()[2].trigger()

    open_menu(window, window.table_tabs.tabRect(0).center(), choose)
    idle(qtbot, window)

    assert window.table_tabs.count() == 0
    assert window._workspace_tabs == {}
    assert window._active_workspace is None
    assert window.current_connection is connection
    assert window.current_table == ''
    assert window.data_table.rowCount() == window.query_result_table.rowCount() == 0
    assert window.query_editor.toPlainText() == ''
    assert window.service.list_connections() == connections
    assert window._connection_tables[connection.id] == tables
    assert not window.tree.topLevelItem(0).isExpanded()
    assert window.service.fetch_rows(connection.id, 'customers').rows == source_rows


def test_close_all_tabs_does_not_reload_dirty_background_tabs(window, qtbot, monkeypatch):
    window.open_table('orders')
    idle(qtbot, window)
    window.data_search.setText('unfinished filter')
    window.open_table('products')
    idle(qtbot, window)
    window.open_table('customers')
    idle(qtbot, window)
    reads = []
    fetch_rows = window.service.fetch_rows

    def observed(*args, **kwargs):
        reads.append(args[1])
        return fetch_rows(*args, **kwargs)

    monkeypatch.setattr(window.service, 'fetch_rows', observed)
    open_menu(window, window.table_tabs.tabRect(tab_index(window, 'orders')).center(),
              lambda menu: menu.actions()[2].trigger())
    idle(qtbot, window)

    assert window.table_tabs.count() == 0
    assert reads == []


def test_close_all_tabs_rejects_late_rows_schema_and_query_results(window, monkeypatch):
    deferred = DeferredOperations(window, monkeypatch)
    window.open_table('orders')
    window.query_editor.setPlainText('SELECT 999 AS closed_query')
    window.run_query()
    window.open_table('products')
    open_menu(window, window.table_tabs.tabRect(tab_index(window, 'orders')).center(),
              lambda menu: menu.actions()[2].trigger())

    deferred.finish()

    assert window.table_tabs.count() == 0
    assert window._workspace_tabs == {}
    assert window._active_workspace is None
    assert window.current_table == ''
    assert window.data_table.rowCount() == window.schema_table.rowCount() == 0
    assert window.query_result_table.rowCount() == 0
    assert window.query_editor.toPlainText() == ''
    assert window.query_status.text() == ''


def test_right_click_on_empty_tab_bar_space_does_not_open_a_menu(window, monkeypatch):
    menus = []
    monkeypatch.setattr('baseskiff.ui.window.QMenu', lambda *_args: menus.append('unexpected'))
    window.table_tabs.customContextMenuRequested.emit(QPoint(window.table_tabs.width() - 2, 2))
    assert menus == []


def test_native_right_click_keeps_active_tab_and_draft_when_menu_is_cancelled(window, qtbot):
    open_tables(window, qtbot)
    current = window._active_workspace
    point = window.table_tabs.tabRect(tab_index(window, 'orders')).center()
    qtbot.mousePress(window.table_tabs, Qt.MouseButton.RightButton, pos=point)
    assert window._active_workspace is current
    assert window.current_table == 'products'
    assert window.query_editor.toPlainText() == 'SELECT products -- retained draft'
    qtbot.mouseRelease(window.table_tabs, Qt.MouseButton.RightButton, pos=point)
    open_menu(window, point, lambda _menu: None)
    assert window._active_workspace is current
    assert window.table_tabs.count() == 3
