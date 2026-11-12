"""Regression checks for workspace review findings."""
import sqlite3

from PySide6.QtWidgets import QMessageBox

from baseskiff.service import DatabaseService
from baseskiff.ui.window import MainWindow


def test_deleting_last_page_returns_to_a_valid_page(qtbot, monkeypatch, tmp_path):
    window = MainWindow(DatabaseService(tmp_path), language="en")
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: not window.busy and window.data_table.rowCount() > 0, timeout=15000)
    with sqlite3.connect(window.current_connection.path) as database:
        database.execute("CREATE TABLE review_rows (id INTEGER PRIMARY KEY, name TEXT)")
        database.executemany("INSERT INTO review_rows VALUES (?, ?)", [(i, f"Row {i}") for i in range(1, 129)])
    errors = []
    monkeypatch.setattr(window, "show_error", errors.append)
    window.open_table("review_rows")
    qtbot.waitUntil(lambda: not window.busy and window.total_rows == 128, timeout=15000)
    window.change_page(2)
    qtbot.waitUntil(lambda: not window.busy and window.data_table.rowCount() == 28, timeout=15000)
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    window.data_table.selectAll()
    window.delete_selected_rows()
    qtbot.waitUntil(lambda: not window.busy and window.total_rows == 100, timeout=15000)
    assert not errors
    assert window.page == 2
    assert window.data_table.rowCount() == 50
    window.close()
