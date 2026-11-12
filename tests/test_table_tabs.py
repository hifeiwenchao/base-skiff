"""Resident table navigation using real SQLite data and native Qt tabs."""
import sqlite3

import pytest
from PySide6.QtCore import Qt, QThreadPool
from PySide6.QtWidgets import QMessageBox

from baseskiff.models import Connection
from baseskiff.service import DatabaseService
from baseskiff.ui.window import MainWindow


def idle(qtbot, window):
    qtbot.waitUntil(lambda: not window.busy and not window.search_timer.isActive()
                   and QThreadPool.globalInstance().activeThreadCount() == 0, timeout=15000)


def tab_index(window, table, cid=None):
    cid = cid or window.current_connection.id
    return next(index for index in range(window.table_tabs.count())
                if window.table_tabs.tabData(index)[0] == cid
                and window.table_tabs.tabData(index)[2] == table)


@pytest.fixture
def window(qtbot, tmp_path, monkeypatch):
    widget = MainWindow(DatabaseService(tmp_path), language='en')
    qtbot.addWidget(widget)
    monkeypatch.setattr(widget, 'show_error', lambda message: pytest.fail(message))
    widget.show()
    qtbot.waitUntil(lambda: not widget.busy and widget.current_table == 'customers'
                   and widget.data_table.rowCount() > 0, timeout=15000)
    idle(qtbot, widget)
    yield widget
    idle(qtbot, widget)
    widget.close()


def test_open_tables_remain_resident_and_repeat_open_reuses_tab(window, qtbot):
    assert window.table_tabs.count() == 1
    window.open_table('orders')
    idle(qtbot, window)
    assert window.table_tabs.count() == 2
    assert window.current_table == 'orders' and window.total_rows == 192
    window.open_table('customers')
    idle(qtbot, window)
    assert window.table_tabs.count() == 2
    assert window.current_table == 'customers' and window.total_rows == 128
    assert window.table_tabs.currentIndex() == tab_index(window, 'customers')


def test_each_table_keeps_data_controls_results_and_query_draft(window, qtbot, monkeypatch):
    window.page_size_combo.setCurrentText('25')
    idle(qtbot, window)
    window.data_search.setText('@example.com')
    idle(qtbot, window)
    window.sort_rows(0)
    idle(qtbot, window)
    window.change_page(1)
    idle(qtbot, window)
    window.query_editor.setPlainText("SELECT 'customers' AS origin")
    window.run_query()
    idle(qtbot, window)
    draft = 'SELECT * FROM customers WHERE -- unfinished'
    window.query_editor.setPlainText(draft)
    cursor = window.query_editor.textCursor()
    cursor.setPosition(7)
    window.query_editor.setTextCursor(cursor)
    window.tabs.setCurrentIndex(2)
    window.data_table.selectRow(3)
    window.data_table.setColumnWidth(0, 220)
    saved_rows = list(window.current_result.rows)
    calls = []
    fetch = window.service.fetch_rows
    def observed(*args, **kwargs):
        calls.append((args, kwargs))
        return fetch(*args, **kwargs)
    monkeypatch.setattr(window.service, 'fetch_rows', observed)
    window.open_table('orders')
    idle(qtbot, window)
    window.query_editor.setPlainText('SELECT * FROM orders -- separate draft')
    count = len(calls)

    window.table_tabs.setCurrentIndex(tab_index(window, 'customers'))
    idle(qtbot, window)

    assert window.page == 2 and window.page_size == 25
    assert window.page_size_combo.currentText() == '25'
    assert window.data_search.text() == '@example.com'
    assert window.sort_column == 'id' and not window.descending
    assert window.current_result.rows == saved_rows
    assert window.query_editor.toPlainText() == draft
    assert window.query_editor.textCursor().position() == 7
    assert window.query_result.rows == [{'origin': 'customers'}]
    assert window.tabs.currentIndex() == 2
    assert window.data_table.columnWidth(0) == 220
    assert window.data_table.selectionModel().selectedRows()[0].row() == 3
    assert len(calls) == count


def test_native_tab_clicks_and_language_switch_preserve_both_tables(window, qtbot):
    window.query_editor.setPlainText('customers draft')
    window.open_table('products')
    idle(qtbot, window)
    assert window.table_tabs.tabsClosable()
    assert window.table_tabs.isMovable()
    for language in ('zh', 'en'):
        if window.language != language:
            window.toggle_language()
        qtbot.mouseClick(window.table_tabs, Qt.MouseButton.LeftButton,
                         pos=window.table_tabs.tabRect(tab_index(window, 'customers')).center())
        assert window.current_table == 'customers'
        assert window.query_editor.toPlainText() == 'customers draft'
        assert window.table_tabs.count() == 2
        qtbot.mouseClick(window.table_tabs, Qt.MouseButton.LeftButton,
                         pos=window.table_tabs.tabRect(tab_index(window, 'products')).center())
        assert window.current_table == 'products'
    assert all(window.table_tabs.tabToolTip(index) for index in range(2))


@pytest.mark.parametrize('select_backwards', [False, True])
def test_query_cursor_and_selection_preserve_non_bmp_text(window, qtbot, select_backwards):
    draft = "SELECT '😀😀' AS note;"
    window.query_editor.setPlainText(draft)
    cursor = window.query_editor.textCursor()
    cursor.movePosition(cursor.MoveOperation.End)
    if select_backwards:
        cursor.setPosition(cursor.position() - 4, cursor.MoveMode.KeepAnchor)
    window.query_editor.setTextCursor(cursor)
    expected = cursor.position(), cursor.anchor(), cursor.selectedText()
    window.open_table('orders')
    idle(qtbot, window)
    window.open_table('customers')
    idle(qtbot, window)
    restored = window.query_editor.textCursor()
    assert window.query_editor.toPlainText() == draft
    assert (restored.position(), restored.anchor(), restored.selectedText()) == expected


def test_native_close_button_follows_table_after_reordering(window, qtbot):
    window.open_table('orders')
    idle(qtbot, window)
    window.open_table('products')
    idle(qtbot, window)
    window.table_tabs.moveTab(tab_index(window, 'customers'), 2)
    window.table_tabs.setCurrentIndex(tab_index(window, 'orders'))
    index = tab_index(window, 'customers')
    button = (window.table_tabs.tabButton(index, window.table_tabs.ButtonPosition.RightSide)
              or window.table_tabs.tabButton(index, window.table_tabs.ButtonPosition.LeftSide))
    assert button is not None
    qtbot.mouseClick(button, Qt.MouseButton.LeftButton)
    assert window.table_tabs.count() == 2 and window.current_table == 'orders'
    assert {window.table_tabs.tabData(i)[2] for i in range(2)} == {'orders', 'products'}


def test_closing_background_active_and_last_tab_preserves_connections(window, qtbot):
    window.open_table('orders')
    idle(qtbot, window)
    window.open_table('products')
    idle(qtbot, window)
    window.table_tabs.tabCloseRequested.emit(tab_index(window, 'customers'))
    assert window.current_table == 'products' and window.table_tabs.count() == 2
    window.table_tabs.tabCloseRequested.emit(window.table_tabs.currentIndex())
    assert window.current_table == 'orders' and window.table_tabs.count() == 1
    window.table_tabs.tabCloseRequested.emit(0)
    idle(qtbot, window)
    assert window.table_tabs.count() == 0 and window.current_table == ''
    assert window.data_table.rowCount() == 0
    assert window.connections
    window.open_table('customers')
    idle(qtbot, window)
    assert window.table_tabs.count() == 1 and window.total_rows == 128


def second_database(window, tmp_path):
    path = tmp_path / 'second.sqlite'
    with sqlite3.connect(path) as db:
        db.executescript("CREATE TABLE customers(id INTEGER PRIMARY KEY, note TEXT);"
                         "INSERT INTO customers VALUES(1,'second database');")
    connection = window.service.save_connection(Connection('Second source', path=str(path)))
    window.connections = window.service.list_connections()
    return connection


def test_same_table_name_in_two_connections_has_independent_tabs(window, qtbot, tmp_path):
    first = window.current_connection
    window.query_editor.setPlainText('first connection draft')
    second = second_database(window, tmp_path)
    window.activate_connection(second, 'customers')
    idle(qtbot, window)
    assert window.table_tabs.count() == 2
    assert window.current_result.rows == [{'id': 1, 'note': 'second database'}]
    window.query_editor.setPlainText('second connection draft')
    window.table_tabs.setCurrentIndex(tab_index(window, 'customers', first.id))
    idle(qtbot, window)
    assert window.current_connection.id == first.id and window.total_rows == 128
    assert window.query_editor.toPlainText() == 'first connection draft'
    window.table_tabs.setCurrentIndex(tab_index(window, 'customers', second.id))
    assert window.query_editor.toPlainText() == 'second connection draft'


def test_removing_connection_closes_only_its_tabs(window, qtbot, tmp_path, monkeypatch):
    first = window.current_connection
    window.open_table('orders')
    idle(qtbot, window)
    second = second_database(window, tmp_path)
    window.activate_connection(second, 'customers')
    idle(qtbot, window)
    monkeypatch.setattr(QMessageBox, 'question', lambda *args, **kwargs: QMessageBox.StandardButton.Yes)
    window.remove_connection(first)
    idle(qtbot, window)
    assert window.table_tabs.count() == 1
    assert window.table_tabs.tabData(0)[0] == second.id
    assert window.current_connection.id == second.id
    assert window.current_result.rows == [{'id': 1, 'note': 'second database'}]
