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
    history = next(item for item in (root.child(i) for i in range(root.childCount()))
                   if item.data(0, NODE_KIND_ROLE) == "sql_history")
    assert history.data(0, SCOPE_ROLE) == (window.connections[0].id, "playground.sqlite")
    assert history.text(0) == "History"
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
