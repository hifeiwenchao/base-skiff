"""Native two-step connection creation and direct editing with real local data."""
import sqlite3

import pytest
from PySide6.QtCore import QThreadPool, QTimer
from PySide6.QtWidgets import QApplication, QLabel

from baseskiff.service import DatabaseService
from baseskiff.ui.dialogs import ConnectionDialog
from baseskiff.ui.window import MainWindow


@pytest.fixture
def connection_window(qtbot, tmp_path):
    window = MainWindow(DatabaseService(tmp_path / 'workspace'), language='en')
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: not window.busy and window.data_table.rowCount() > 0
                   and QThreadPool.globalInstance().activeThreadCount() == 0, timeout=15000)
    yield window
    window.close()


def next_modal(action, failures):
    def run():
        dialog = QApplication.activeModalWidget()
        try:
            assert dialog is not None
            action(dialog)
        except Exception as error:
            failures.append(error)
            if dialog is not None:
                dialog.reject()
    QTimer.singleShot(0, run)


def test_new_connection_opens_selected_type_form_after_picker(connection_window):
    window = connection_window
    existing = window.service.list_connections()
    failures, stages = [], []

    def edit(dialog):
        assert isinstance(dialog, ConnectionDialog)
        stages.append('form')
        assert dialog.current_connection().kind == 'Redis'
        assert dialog.current_connection().port == 6379
        assert isinstance(dialog.kind_label, QLabel)
        assert dialog.kind_label.text() == 'Redis'
        assert dialog.redis_separator_edit.isVisible()
        dialog.reject()

    def choose(dialog):
        stages.append('picker')
        assert not isinstance(dialog, ConnectionDialog)
        next_modal(edit, failures)
        dialog.kind_buttons['Redis'].click()

    next_modal(choose, failures)
    window.add_connection()

    assert not failures, failures
    assert stages == ['picker', 'form']
    assert window.service.list_connections() == existing


def test_cancelling_type_selection_keeps_workspace_and_connections(connection_window):
    window = connection_window
    existing = window.service.list_connections()
    selected = window.current_connection
    window.query_editor.setPlainText('SELECT 42 AS draft')
    failures = []

    def cancel(dialog):
        assert hasattr(dialog, 'kind_buttons')
        dialog.cancel_button.click()

    next_modal(cancel, failures)
    window.connection_dialog()

    assert not failures, failures
    assert not window.busy
    assert window.current_connection is selected
    assert window.query_editor.toPlainText() == 'SELECT 42 AS draft'
    assert window.service.list_connections() == existing


def test_selected_sqlite_connection_is_saved_and_opens_real_rows(connection_window,
                                                               qtbot, tmp_path):
    window = connection_window
    window.toggle_language()
    path = tmp_path / 'selected.sqlite'
    with sqlite3.connect(path) as database:
        database.execute('CREATE TABLE notes (id INTEGER PRIMARY KEY, content TEXT)')
        database.execute("INSERT INTO notes VALUES (1, 'selected database')")
    failures = []

    def save(dialog):
        assert isinstance(dialog, ConnectionDialog)
        assert dialog.windowTitle() == '新建连接'
        assert dialog.current_connection().kind == 'SQLite'
        assert len(window.service.list_connections()) == 1
        dialog.name_edit.setText('新建测试连接')
        dialog.path_edit.setText(str(path))
        dialog.save_button.click()

    def choose(dialog):
        assert dialog.windowTitle() == '选择数据库类型'
        next_modal(save, failures)
        dialog.kind_buttons['SQLite'].click()

    next_modal(choose, failures)
    window.new_side_button.click()
    assert not failures, failures
    qtbot.waitUntil(lambda: not window.busy and window.current_table == 'notes'
                   and window.data_table.rowCount() == 1, timeout=15000)
    assert len(window.service.list_connections()) == 2
    assert window.current_connection.kind == 'SQLite'
    assert window.current_connection.path == str(path)
    assert window.current_result.rows == [{'id': 1, 'content': 'selected database'}]


def test_edit_connection_skips_picker_and_preserves_database_identity(connection_window, qtbot):
    window = connection_window
    original = window.current_connection
    failures = []

    def edit(dialog):
        assert isinstance(dialog, ConnectionDialog)
        assert dialog.windowTitle() == 'Edit connection'
        assert isinstance(dialog.kind_label, QLabel)
        assert dialog.kind_label.text() == 'SQLite'
        dialog.name_edit.setText('Renamed playground')
        dialog.save_button.click()

    next_modal(edit, failures)
    window.edit_current_connection()
    assert not failures, failures
    qtbot.waitUntil(lambda: not window.busy and window.current_connection.name == 'Renamed playground'
                   and window.data_table.rowCount() > 0, timeout=15000)
    saved = window.service.list_connections()
    assert len(saved) == 1
    assert saved[0].id == original.id
    assert saved[0].kind == 'SQLite'
    assert saved[0].path == original.path
