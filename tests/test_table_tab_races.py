"""Delayed native workspace operations retain the table that started them."""
from dataclasses import dataclass

import pytest
from PySide6.QtCore import QThreadPool, QTimer
from PySide6.QtWidgets import QApplication

from baseskiff.models import ColumnInfo, QueryResult
from baseskiff.service import DatabaseService
from baseskiff.ui.window import MainWindow


@pytest.fixture
def window(qtbot, tmp_path, monkeypatch):
    widget = MainWindow(DatabaseService(tmp_path), language='en')
    monkeypatch.setattr(widget, 'show_error', lambda message: pytest.fail(message))
    qtbot.addWidget(widget)
    widget.show()
    qtbot.waitUntil(lambda: not widget.busy and widget.data_table.rowCount() > 0
                   and QThreadPool.globalInstance().activeThreadCount() == 0, timeout=15000)
    yield widget
    widget.search_timer.stop()
    qtbot.waitUntil(lambda: QThreadPool.globalInstance().activeThreadCount() == 0, timeout=15000)
    widget.close()


@dataclass
class Operation:
    name: str
    args: tuple
    kwargs: dict
    perform: object
    callback: object = None
    error: object = None
    delivered: bool = False


class DeferredOperations:
    """Keep the real SQLite operations while choosing when replies arrive."""

    def __init__(self, window, monkeypatch):
        self.operations = []
        for name in ('get_schema', 'fetch_rows', 'execute', 'update_row', 'insert_row', 'apply_grid_changes', 'list_tables'):
            method = getattr(window.service, name)

            def defer(*args, _method=method, _name=name, **kwargs):
                return Operation(_name, args, kwargs, lambda: _method(*args, **kwargs))

            monkeypatch.setattr(window.service, name, defer)

        def submit(function, callback=None, error=None):
            operation = function()
            assert isinstance(operation, Operation), 'Unexpected untracked database operation'
            operation.callback, operation.error = callback, error
            self.operations.append(operation)

        monkeypatch.setattr(window, 'submit', submit)

    def pending(self, name=None, table=None):
        return [operation for operation in self.operations
                if not operation.delivered and (name is None or operation.name == name)
                and (table is None or len(operation.args) > 1 and operation.args[1] == table)]

    def complete(self, operation, result=None):
        operation.delivered = True
        if result is None:
            result = operation.perform()
        if operation.callback:
            operation.callback(result)

    def fail(self, operation, message):
        operation.delivered = True
        assert operation.error is not None
        operation.error(message)

    def finish(self, name=None, table=None):
        # Callbacks may schedule another read; complete those too.
        for _ in range(30):
            pending = self.pending(name, table)
            if not pending:
                return
            self.complete(pending[0])
        pytest.fail('Database callbacks did not settle')


@pytest.fixture
def deferred(window, monkeypatch):
    return DeferredOperations(window, monkeypatch)


def tab_index(window, table):
    assert hasattr(window, 'table_tabs'), 'Opened tables need independent workspace tabs'
    matches = [index for index in range(window.table_tabs.count())
               if window.table_tabs.tabData(index)[2] == table]
    assert len(matches) == 1, f'Expected one open tab for {table!r}'
    return matches[0]


def activate(window, table):
    window.table_tabs.setCurrentIndex(tab_index(window, table))
    assert window.current_table == table


def cells(table):
    return [[table.item(row, column).text() if table.item(row, column) else ''
             for column in range(table.columnCount())] for row in range(table.rowCount())]


def marker_result(marker):
    return QueryResult(columns=['id', 'marker'], rows=[{'id': 1, 'marker': marker}], total=1)


def test_late_rows_and_schema_update_their_background_tab(window, deferred):
    window.open_table('orders')
    orders_rows = deferred.pending('fetch_rows', 'orders')[0]
    orders_schema = deferred.pending('get_schema', 'orders')[0]
    window.open_table('products')
    deferred.finish(table='products')
    products_rows, products_schema = cells(window.data_table), cells(window.schema_table)

    deferred.complete(orders_rows)
    deferred.complete(orders_schema)
    assert window.current_table == 'products'
    assert cells(window.data_table) == products_rows
    assert cells(window.schema_table) == products_schema

    activate(window, 'orders')
    assert window.data_table.rowCount() > 0
    assert 'customer_id' in [row[0] for row in cells(window.schema_table)]
    assert 'customer_id' in [window.data_table.horizontalHeaderItem(index).text()
                             for index in range(window.data_table.columnCount())]


def test_newer_refresh_wins_when_older_reply_arrives_after_return(window, deferred):
    window.open_table('orders')
    old_rows = deferred.pending('fetch_rows', 'orders')[0]
    deferred.finish('get_schema', 'orders')
    window.refresh_data()
    new_rows = deferred.pending('fetch_rows', 'orders')[-1]
    window.open_table('products')
    deferred.finish(table='products')
    deferred.complete(new_rows, marker_result('new rows'))
    activate(window, 'orders')
    assert cells(window.data_table) == [['1', 'new rows']]

    deferred.complete(old_rows, marker_result('stale rows'))
    assert cells(window.data_table) == [['1', 'new rows']]


def test_background_query_completion_restores_results_and_enabled_run(window, deferred):
    window.query_editor.setPlainText('SELECT 42 AS answer')
    window.run_query()
    query = deferred.pending('execute')[0]
    assert not window.run_button.isEnabled()
    window.open_table('orders')
    deferred.finish(table='orders')
    window.query_editor.setPlainText('SELECT 7 AS other_answer')
    assert window.run_button.isEnabled()

    deferred.complete(query)
    assert window.query_editor.toPlainText() == 'SELECT 7 AS other_answer'
    assert window.query_result_table.rowCount() == 0
    assert 'Running' not in window.query_status.text()
    activate(window, 'customers')
    assert window.tabs.currentIndex() == 1
    assert window.query_editor.toPlainText() == 'SELECT 42 AS answer'
    assert cells(window.query_result_table) == [['42']]
    assert 'Success' in window.query_status.text()
    assert window.run_button.isEnabled()


def test_background_query_error_is_shown_only_on_its_origin_tab(window, deferred):
    window.query_editor.setPlainText('SELECT missing_column FROM customers')
    window.run_query()
    query = deferred.pending('execute')[0]
    window.open_table('orders')
    deferred.finish(table='orders')
    deferred.fail(query, 'no such column: missing_column')
    assert 'missing_column' not in window.query_status.text()
    assert window.run_button.isEnabled()

    activate(window, 'customers')
    assert 'missing_column' in window.query_status.text()
    assert 'Running' not in window.query_status.text()
    assert window.query_result_table.rowCount() == 0
    assert window.run_button.isEnabled()


def test_closed_and_reopened_table_rejects_all_old_replies(window, deferred):
    window.open_table('orders')
    old_rows = deferred.pending('fetch_rows', 'orders')[0]
    old_schema = deferred.pending('get_schema', 'orders')[0]
    window.query_editor.setPlainText('SELECT 999 AS obsolete_query')
    window.run_query()
    old_query = deferred.pending('execute')[0]
    window.open_table('products')
    deferred.finish(table='products')
    window.table_tabs.tabCloseRequested.emit(tab_index(window, 'orders'))
    window.open_table('orders')
    new_schema_request = deferred.pending('get_schema', 'orders')[-1]
    deferred.complete(new_schema_request)
    # Keep the original callbacks available and deliver only the reopened read.
    new_rows = deferred.pending('fetch_rows', 'orders')[-1]
    deferred.complete(new_rows, marker_result('reopened rows'))
    new_schema = cells(window.schema_table)
    window.query_editor.setPlainText('SELECT 5 AS current_draft')

    deferred.complete(old_rows, marker_result('obsolete rows'))
    deferred.complete(old_schema, [ColumnInfo('obsolete_schema')])
    deferred.complete(old_query)
    assert cells(window.data_table) == [['1', 'reopened rows']]
    assert cells(window.schema_table) == new_schema
    assert window.query_editor.toPlainText() == 'SELECT 5 AS current_draft'
    assert window.query_result_table.rowCount() == 0
    assert '999' not in window.query_status.text()
    assert window.run_button.isEnabled()
    assert len([index for index in range(window.table_tabs.count())
                if window.table_tabs.tabData(index)[2] == 'orders']) == 1


def test_pending_filter_does_not_reset_another_tabs_page(window, deferred, qtbot):
    window.open_table('orders')
    deferred.finish(table='orders')
    window.change_page(1)
    deferred.finish('fetch_rows', 'orders')
    assert window.page == 2
    second_page = cells(window.data_table)
    activate(window, 'customers')
    window.data_search.setText('olivia')
    assert window.search_timer.isActive()
    activate(window, 'orders')
    qtbot.wait(450)
    deferred.finish()
    assert window.page == 2
    assert window.data_search.text() == ''
    assert cells(window.data_table) == second_page

    activate(window, 'customers')
    qtbot.wait(450)
    deferred.finish()
    assert window.data_search.text() == 'olivia'
    assert window.page == 1
    assert window.data_table.rowCount() > 0
    assert all('olivia' in ' '.join(row).lower() for row in cells(window.data_table))


def test_background_mutation_refreshes_origin_without_replacing_active_rows(window, deferred):
    window.data_table.selectRow(0)

    def accept_edit():
        dialog = QApplication.activeModalWidget()
        dialog.fields['name'].setText('Saved while inactive')
        dialog.accept()

    QTimer.singleShot(0, accept_edit)
    window.edit_selected_row()
    window.commit_table_changes()
    mutation = deferred.pending('apply_grid_changes', 'customers')[0]
    window.open_table('orders')
    deferred.finish(table='orders')
    visible_orders = cells(window.data_table)

    deferred.complete(mutation)
    deferred.finish()
    assert window.current_table == 'orders'
    assert cells(window.data_table) == visible_orders
    activate(window, 'customers')
    deferred.finish()
    name_column = [window.data_table.horizontalHeaderItem(index).text()
                   for index in range(window.data_table.columnCount())].index('name')
    assert window.data_table.item(0, name_column).text() == 'Saved while inactive'
