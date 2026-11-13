"""Workspace chrome keeps the data area compact and scopes SQL history."""

from baseskiff.models import Connection
from baseskiff.service import DatabaseService
from baseskiff.ui.redis_tree import NODE_KIND_ROLE, SCOPE_ROLE
from baseskiff.ui.window import MainWindow


def test_object_heading_region_is_hidden(qtbot, tmp_path):
    window = MainWindow(DatabaseService(tmp_path), language="en")
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: not window.busy and window.data_table.rowCount() > 0, timeout=15000)
    assert not window.table_heading_wrap.isVisible()
    window.close()


def test_sql_connection_exposes_scoped_history_child(qtbot, tmp_path):
    window = MainWindow(DatabaseService(tmp_path), language="en")
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: not window.busy and window.data_table.rowCount() > 0, timeout=15000)
    root = window.tree.topLevelItem(0)
    console = next(item for item in (root.child(i) for i in range(root.childCount()))
                   if item.data(0, NODE_KIND_ROLE) == "sql_console")
    history = next(item for item in (root.child(i) for i in range(root.childCount()))
                   if item.data(0, NODE_KIND_ROLE) == "sql_history")
    tables = next(item for item in (root.child(i) for i in range(root.childCount()))
                   if item.data(0, NODE_KIND_ROLE) == "sql_tables")
    assert history.data(0, SCOPE_ROLE) == (window.connections[0].id, "playground.sqlite")
    assert history.text(0) == "History"
    assert tables.text(0) == "Tables"
    assert tables.parent() is root
    assert tables.childCount() == len(window._connection_tables[window.connections[0].id])
    assert tables.child(0).data(0, NODE_KIND_ROLE) == "table"
    assert console.data(0, SCOPE_ROLE) == (window.connections[0].id, "playground.sqlite")
    assert console.text(0) == "Console"
    window.close()


def test_sql_console_is_scoped_persistent_and_separate_from_table_tabs(qtbot, tmp_path):
    service = DatabaseService(tmp_path)
    window = MainWindow(service, language="en")
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: not window.busy and window.data_table.rowCount() > 0, timeout=15000)
    connection = window.connections[0]

    window.show_console(connection, "playground.sqlite")
    assert window._console_scope == (connection.id, "playground.sqlite")
    assert window.console_page.isVisible()
    assert not window.tabs.isVisible()
    assert not window.tabs.isTabVisible(1)
    window.query_editor.setPlainText("SELECT 42 AS answer")
    qtbot.waitUntil(lambda: service.load_console(connection.id, "playground.sqlite") == "SELECT 42 AS answer",
                    timeout=3000)
    window.open_table("customers")
    assert window._console_scope is None
    assert window.tabs.isVisible()
    window.show_console(connection, "playground.sqlite")
    assert window.query_editor.toPlainText() == "SELECT 42 AS answer"
    window.close()


def test_history_filters_by_connection_and_database(tmp_path):
    service = DatabaseService(tmp_path)
    import sqlite3
    first_path, second_path = tmp_path / "first.sqlite", tmp_path / "second.sqlite"
    sqlite3.connect(first_path).close()
    sqlite3.connect(second_path).close()
    first = service.save_connection(Connection("First", kind="SQLite", path=str(first_path)))
    second = service.save_connection(Connection("Second", kind="SQLite", path=str(second_path)))
    service.execute(first.id, "SELECT 1")
    service.execute(second.id, "SELECT 2")
    first_db = "first.sqlite"
    assert [entry.connection_id for entry in service.list_history(first.id, first_db)] == [first.id]
    assert service.list_history(first.id, "second.sqlite") == []
    assert service.list_history(second.id, "second.sqlite")[0].connection_id == second.id
