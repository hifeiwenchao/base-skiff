from threading import Event
from time import monotonic

from baseskiff.models import QueryResult
from baseskiff.service import DatabaseService
from baseskiff.ui.window import MainWindow


def ready_window(qtbot, tmp_path):
    window = MainWindow(DatabaseService(tmp_path), language='en')
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: not window.busy and window.data_table.rowCount() > 0, timeout=15000)
    return window


def test_cancel_callback_does_not_block_gui(qtbot, tmp_path, monkeypatch):
    window = ready_window(qtbot, tmp_path)
    window.show_console(window.connections[0])
    started, release, callback_started, callback_release = Event(), Event(), Event(), Event()

    def interrupt():
        callback_started.set()
        callback_release.wait(2)

    def execute(_cid, _query, *, cancel_token=None, database=None):
        cancel_token.bind(interrupt)
        started.set()
        release.wait(3)
        return QueryResult()

    monkeypatch.setattr(window.service, 'execute', execute)
    window.query_editor.setPlainText('SELECT 1')
    window.run_query()
    qtbot.waitUntil(started.is_set)
    try:
        before = monotonic()
        window.cancel_query()
        assert monotonic() - before < 0.3
        # Cancel immediately changes state; a blocking driver callback runs off-thread.
        assert not callback_release.is_set()
        assert window._query_state == 'cancelled'
        qtbot.waitUntil(callback_started.is_set)
        assert window.run_button.isEnabled()
    finally:
        release.set()
        callback_release.set()
        qtbot.waitUntil(lambda: not window.busy)
        window.close()


def test_cancel_token_belongs_to_console(qtbot, tmp_path, monkeypatch):
    from dataclasses import replace
    window = ready_window(qtbot, tmp_path)
    first = window.connections[0]
    second = window.service.save_connection(replace(first, id='second', name='Second'))
    window.connections = window.service.list_connections()
    started, release, tokens = Event(), Event(), {}

    def execute(cid, _query, *, cancel_token=None, database=None):
        tokens[cid] = cancel_token
        started.set()
        release.wait(3)
        return QueryResult()

    monkeypatch.setattr(window.service, 'execute', execute)
    try:
        window.show_console(first)
        window.query_editor.setPlainText('SELECT 1')
        window.run_query()
        qtbot.waitUntil(lambda: first.id in tokens)
        window.show_console(second)
        window.query_editor.setPlainText('SELECT 2')
        window.run_query()
        qtbot.waitUntil(lambda: second.id in tokens)
        window.show_console(first)
        window.cancel_query()
        assert tokens[first.id].cancelled
        assert not tokens[second.id].cancelled
    finally:
        release.set()
        qtbot.waitUntil(lambda: not window.busy)
        window.close()


def test_restart_restores_order_filters_and_loads_only_active_tab(qtbot, tmp_path):
    window = ready_window(qtbot, tmp_path)
    window.open_table('orders')
    qtbot.waitUntil(lambda: not window.busy)
    state = window._active_workspace
    state.where_clause = window.where_clause = 'id > 2'
    window.where_edit.setText('id > 2')
    window.open_table('products')
    qtbot.waitUntil(lambda: not window.busy)
    window.close()
    second = MainWindow(DatabaseService(tmp_path), language='en')
    qtbot.addWidget(second)
    second.show()
    qtbot.waitUntil(lambda: not second.busy and second.current_table == 'products')
    assert [second.table_tabs.tabData(i)[2] for i in range(second.table_tabs.count())] == ['customers', 'orders', 'products']
    orders = next(s for s in second._workspace_tabs.values() if s.table == 'orders')
    assert orders.where_clause == 'id > 2'
    assert orders.current_result.rows == []
    second.close()


def test_large_table_uses_count_free_paging_with_next_page(qtbot, tmp_path):
    from dataclasses import replace
    window = ready_window(qtbot, tmp_path)
    connection = window.service.save_connection(replace(window.connections[0], id='real', name='Real', is_demo=False))
    window.connections = window.service.list_connections()
    window.current_connection = connection
    window.open_table('customers')
    qtbot.waitUntil(lambda: not window.busy)
    assert not window.current_result.total_known
    assert window.next_button.isEnabled()
    assert 'total not counted' in window.record_count.text()
    first_id = window.current_result.rows[-1]['id']
    window.change_page(1)
    qtbot.waitUntil(lambda: not window.busy)
    assert window.page == 2
    assert window.current_result.rows[0]['id'] > first_id
    window.count_total_button.click()
    qtbot.waitUntil(lambda: not window.busy)
    assert window.current_result.total_known and window.total_rows == 128
    window.close()


def test_named_consoles_are_independent_and_persist(qtbot, tmp_path):
    window = ready_window(qtbot, tmp_path)
    connection = window.connections[0]
    window.show_console(connection)
    window.query_editor.setPlainText('SELECT 1')
    window.show_console(connection, document_id='analysis', document_name='Analysis')
    window.query_editor.setPlainText('SELECT 2')
    window.show_console(connection)
    assert window.query_editor.toPlainText() == 'SELECT 1'
    window.show_console(connection, document_id='analysis')
    assert window.query_editor.toPlainText() == 'SELECT 2'
    window.close()
    second = MainWindow(DatabaseService(tmp_path), language='en')
    qtbot.addWidget(second)
    second.show()
    qtbot.waitUntil(lambda: second._console_scope is not None)
    assert second._console_scope[-1] == 'analysis'
    assert second.query_editor.toPlainText() == 'SELECT 2'
    second.close()


def test_full_export_includes_rows_beyond_visible_page(qtbot, tmp_path, monkeypatch):
    import csv

    from PySide6.QtWidgets import QFileDialog
    window = ready_window(qtbot, tmp_path)
    target = tmp_path / 'all.csv'
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a, **kw: (str(target), 'CSV'))
    window.export_all_data()
    qtbot.waitUntil(lambda: not window.busy)
    with target.open(encoding='utf-8-sig', newline='') as stream:
        rows = list(csv.reader(stream))
    assert len(rows) == 129
    assert len(window.current_result.rows) == 50
    assert not window._export_jobs
    window.close()


def test_structure_dialog_applies_only_after_explicit_apply(qtbot, tmp_path):
    from baseskiff.ui.sql_structure import SqlStructureDialog
    window = ready_window(qtbot, tmp_path)
    connection = window.current_connection
    window.modify_sql_structure(connection, 'customers')
    qtbot.waitUntil(lambda: window.findChild(SqlStructureDialog) is not None and not window.busy)
    dialog = window.findChild(SqlStructureDialog)
    dialog.add_column_button.click()
    dialog.name_edit.setText('optimization_note')
    dialog.type_edit.setText('TEXT')
    dialog.stage_button.click()
    assert 'optimization_note' not in [c.name for c in window.service.get_schema(connection.id, 'customers')]
    dialog.apply_button.click()
    qtbot.waitUntil(lambda: not window.busy)
    assert 'optimization_note' in [c.name for c in window.service.get_schema(connection.id, 'customers')]
    assert dialog.statements == []
    dialog.close()
    window.close()


def test_sql_console_cached_completion_and_explain(qtbot, tmp_path):
    window = ready_window(qtbot, tmp_path)
    window.show_console(window.current_connection)
    window.query_editor.setPlainText('SELECT cus')
    cursor = window.query_editor.textCursor()
    cursor.movePosition(cursor.MoveOperation.End)
    window.query_editor.setTextCursor(cursor)
    assert 'customers' in window.query_editor.completion_candidates()
    window.query_editor.setPlainText('SELECT * FROM customers WHERE id=1')
    window.run_query(explain=True)
    qtbot.waitUntil(lambda: not window.busy)
    assert window.query_result.rows
    assert 'detail' in window.query_result.columns
    window.close()


def test_query_timeout_is_editable_and_saved(qtbot, tmp_path):
    from baseskiff.ui.dialogs import ConnectionDialog
    service = DatabaseService(tmp_path)
    original = service.list_connections()[0]
    dialog = ConnectionDialog(connection=original, language='en')
    qtbot.addWidget(dialog)
    dialog.timeout_spin.setValue(45)
    service.save_connection(dialog.current_connection())
    assert service.list_connections()[0].query_timeout == 45


def test_cancel_close_keeps_all_tabs_pending_edits(qtbot, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    window = ready_window(qtbot, tmp_path)
    first = window._active_workspace
    first.pending_updates['x'] = {'keys': {'id': 1}, 'values': {'name': 'draft'}}
    # Register a second pending tab without switching away from the first draft.
    from baseskiff.ui.workspace_tabs import WorkspaceTab
    second = WorkspaceTab(window.current_connection, 'orders')
    second.pending_inserts.append({'name': 'draft'})
    window._workspace_tabs[second.identity] = second
    window._register_table_tab(second)
    answers = iter([QMessageBox.StandardButton.Discard, QMessageBox.StandardButton.Cancel])
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: next(answers, QMessageBox.StandardButton.Discard))
    window.close()
    assert window.isVisible()
    assert first.pending_updates and second.pending_inserts
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: QMessageBox.StandardButton.Discard)
    window.close()


def test_complete_small_table_shows_actual_total_without_count(qtbot, tmp_path):
    from dataclasses import replace
    window = ready_window(qtbot, tmp_path)
    connection = window.service.save_connection(replace(window.connections[0], id='small', name='Small', is_demo=False))
    window.connections = window.service.list_connections()
    window.current_connection = connection
    window.open_table('customers')
    qtbot.waitUntil(lambda: not window.busy)
    window.where_edit.setText('id <= 7')
    window.apply_table_query()
    qtbot.waitUntil(lambda: not window.busy)
    assert window.record_count.text() == 'Showing 1–7 of 7 records'
    assert not window.next_button.isEnabled()
    assert not window.count_total_button.isChecked()
    window.close()
