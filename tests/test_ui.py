"""Native UI behavior exercised against an actual local SQLite database."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from baseskiff.service import DatabaseService
from baseskiff.ui.window import MainWindow


def test_window_loads_real_sample_rows(qtbot, tmp_path):
    window = MainWindow(DatabaseService(tmp_path), language="en")
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: window.data_table.rowCount() > 0, timeout=15000)
    assert window.current_table == "customers"
    assert "BaseSkiff" in window.windowTitle()
    assert window.data_table.columnCount() >= 6
    assert window.total_rows >= 100
    window.close()


def test_language_toggle_preserves_workspace(qtbot, tmp_path):
    window = MainWindow(DatabaseService(tmp_path), language="zh")
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: window.data_table.rowCount() > 0, timeout=15000)
    connection = window.current_connection.id
    window.toggle_language()
    assert window.language == "en"
    assert window.add_row_button.text() == "Add record"
    assert window.current_connection.id == connection
    window.close()


def test_query_executes_and_history_records(qtbot, tmp_path):
    window = MainWindow(DatabaseService(tmp_path), language="en")
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: not window.busy and window.data_table.rowCount() > 0, timeout=15000)
    window.tabs.setCurrentIndex(1)
    window.query_editor.setPlainText("SELECT COUNT(*) AS customer_count FROM customers")
    window.run_query()
    qtbot.waitUntil(lambda: window.query_result_table.rowCount() == 1 and not window.busy, timeout=15000)
    assert int(window.query_result_table.item(0, 0).text()) >= 100
    window.toggle_language()
    assert '执行成功' in window.query_status.text()
    window.show_history()
    qtbot.waitUntil(lambda: window.history_table.rowCount() >= 1, timeout=15000)
    window.close()


def test_language_switch_does_not_allow_parallel_query_execution(qtbot, tmp_path):
    from threading import Event
    service = DatabaseService(tmp_path)
    window = MainWindow(service, language="en")
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: not window.busy and window.data_table.rowCount() > 0, timeout=15000)
    original = service.execute
    started, release = Event(), Event()
    calls = []
    def delayed_execute(cid, query):
        calls.append(query)
        started.set()
        release.wait(5)
        return original(cid, query)
    service.execute = delayed_execute
    window.query_editor.setPlainText('SELECT 1 AS value')
    try:
        window.run_query()
        qtbot.waitUntil(started.is_set)
        window.toggle_language()
        assert window.run_button.isEnabled()
        assert window.run_button.text() == '中断查询'
        window.run_query()
        assert len(calls) == 1
    finally:
        release.set()
        qtbot.waitUntil(lambda: not window.busy, timeout=10000)
        window.close()


def test_add_and_edit_dialogs_persist_real_records(qtbot, tmp_path):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    window = MainWindow(DatabaseService(tmp_path), language="en")
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: not window.busy and window.data_table.rowCount() > 0, timeout=15000)
    def fill_new():
        dialog = QApplication.activeModalWidget()
        dialog.fields['name'].setText('Test Customer')
        dialog.fields['email'].setText('test@example.com')
        dialog.accept()
    QTimer.singleShot(0, fill_new)
    window.add_row()
    assert window.total_rows == 129
    assert window.service.fetch_rows(window.current_connection.id, 'customers').total == 128
    assert window.commit_table_button.isEnabled()
    window.commit_table_button.click()
    qtbot.waitUntil(lambda: window.total_rows == 129 and not window.busy, timeout=15000)
    window.data_search.setText('test@example.com')
    qtbot.waitUntil(lambda: window.total_rows == 1 and not window.busy, timeout=15000)
    window.data_table.selectRow(0)
    def edit_name():
        dialog = QApplication.activeModalWidget()
        dialog.fields['name'].setText('Edited Customer')
        dialog.accept()
    QTimer.singleShot(0, edit_name)
    window.edit_selected_row()
    assert window.current_result.rows[0]['name'] == 'Edited Customer'
    assert window.service.fetch_rows(window.current_connection.id, 'customers', search='test@example.com').rows[0]['name'] == 'Test Customer'
    window.commit_table_button.click()
    qtbot.waitUntil(lambda: not window.busy and window.current_result.rows[0]['name'] == 'Edited Customer', timeout=15000)
    assert len(window.service.list_history()) == 2
    window.close()


def test_ddl_refreshes_table_catalog_without_leaving_editor(qtbot, tmp_path):
    window = MainWindow(DatabaseService(tmp_path), language="en")
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: not window.busy and window.data_table.rowCount() > 0, timeout=15000)
    window.query_editor.setPlainText('CREATE TABLE notes (id INTEGER PRIMARY KEY, content TEXT)')
    window.run_query()
    qtbot.waitUntil(lambda: not window.busy and any(t.name == 'notes' for t in window.tables), timeout=15000)
    assert window.tabs.currentIndex() == 1
    window.query_editor.setPlainText('DROP TABLE customers')
    # Foreign-key constraints intentionally protect the linked sample table;
    # delete the newly created unreferenced table to verify a successful DROP.
    window.query_editor.setPlainText('DROP TABLE notes')
    window.run_query()
    qtbot.waitUntil(lambda: not window.busy and not any(t.name == 'notes' for t in window.tables), timeout=15000)
    assert window.tabs.currentIndex() == 1
    window.close()
