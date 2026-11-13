"""Custom table conditions and staged SQL grid edits."""
import sqlite3

import pytest
from PySide6.QtCore import QThreadPool

from baseskiff.models import Connection
from baseskiff.service import DatabaseService
from tests.test_table_tabs import idle


@pytest.fixture
def local_service(tmp_path):
    path = tmp_path / "query-controls.sqlite"
    with sqlite3.connect(path) as db:
        db.executescript(
            """
            CREATE TABLE people (id INTEGER PRIMARY KEY, name TEXT NOT NULL, score INTEGER);
            INSERT INTO people VALUES (1, 'Alice', 30), (2, 'Bob', 20), (3, 'Chloé', 10);
            """
        )
    service = DatabaseService(tmp_path)
    connection = service.save_connection(Connection(name="Query controls", path=str(path)))
    return service, connection


def test_custom_where_and_order_by_apply_to_page_and_total(local_service):
    service, connection = local_service

    result = service.fetch_rows(
        connection.id,
        "people",
        page=1,
        page_size=1,
        where_clause="score >= 20",
        order_by="score DESC",
    )

    assert result.total == 2
    assert result.rows == [{"id": 1, "name": "Alice", "score": 30}]


@pytest.mark.parametrize("value", ["id = 1; DELETE FROM people", "id = 1 -- comment", "id = 1 /* comment */"])
def test_custom_conditions_reject_multi_statement_and_comments(local_service, value):
    service, connection = local_service

    with pytest.raises(ValueError, match="condition"):
        service.fetch_rows(connection.id, "people", where_clause=value)


def test_custom_condition_accepts_optional_keywords(local_service):
    service, connection = local_service

    result = service.fetch_rows(
        connection.id,
        "people",
        where_clause="WHERE name = 'Bob'",
        order_by="ORDER BY id DESC",
    )

    assert result.rows == [{"id": 2, "name": "Bob", "score": 20}]


def test_sql_grid_changes_can_be_committed_as_one_transaction(local_service):
    service, connection = local_service

    result = service.apply_grid_changes(
        connection.id,
        "people",
        updates=[({"id": 1}, {"score": 31})],
        inserts=[{"id": 4, "name": "Dora", "score": 5}],
        deletes=[{"id": 2}],
    )

    assert result.affected == 3
    assert service.fetch_rows(connection.id, "people", order_by="id").rows == [
        {"id": 1, "name": "Alice", "score": 31},
        {"id": 3, "name": "Chloé", "score": 10},
        {"id": 4, "name": "Dora", "score": 5},
    ]


def test_sql_grid_changes_roll_back_as_one_transaction(local_service):
    service, connection = local_service

    with pytest.raises(Exception):
        service.apply_grid_changes(
            connection.id,
            "people",
            updates=[({"id": 1}, {"score": 31})],
            inserts=[{"id": 2, "name": "duplicate", "score": 1}],
        )

    assert service.fetch_rows(connection.id, "people", order_by="id").rows[0]["score"] == 30


def test_sql_grid_changes_reject_a_row_deleted_before_commit(local_service):
    service, connection = local_service
    service.delete_rows(connection.id, "people", [{"id": 1}])

    with pytest.raises(ValueError, match="changed or was deleted"):
        service.apply_grid_changes(
            connection.id,
            "people",
            updates=[({"id": 1}, {"score": 31})],
        )


@pytest.fixture
def window(qtbot, tmp_path, monkeypatch):
    from baseskiff.service import DatabaseService
    from baseskiff.ui.window import MainWindow

    widget = MainWindow(DatabaseService(tmp_path), language="en")
    qtbot.addWidget(widget)
    monkeypatch.setattr(widget, "show_error", lambda message: pytest.fail(message))
    widget.show()
    qtbot.waitUntil(lambda: not widget.busy and widget.data_table.rowCount() > 0
                   and QThreadPool.globalInstance().activeThreadCount() == 0, timeout=15000)
    yield widget
    idle(qtbot, widget)
    widget.close()


def test_table_conditions_apply_only_after_explicit_submit(window, qtbot):
    window.where_edit.setText("id <= 2")
    window.order_by_edit.setText("id DESC")
    qtbot.wait(400)
    assert window.total_rows == 128

    window.apply_table_query_button.click()
    idle(qtbot, window)

    assert window.total_rows == 2
    assert [row["id"] for row in window.current_result.rows] == [2, 1]


def test_table_cell_edit_is_staged_until_commit(window, qtbot):
    name_column = next(index for index in range(window.data_table.columnCount())
                       if window.data_table.horizontalHeaderItem(index).text() == "name")
    original = window.current_result.rows[0]["name"]
    item = window.data_table.item(0, name_column)
    item.setText("Staged table edit")
    qtbot.wait(50)

    assert window.data_table.item(0, name_column).text() == "Staged table edit"
    assert window.service.fetch_rows(window.current_connection.id, "customers").rows[0]["name"] == original
    assert window.commit_table_button.isEnabled()

    window.commit_table_button.click()
    idle(qtbot, window)
    assert window.service.fetch_rows(window.current_connection.id, "customers").rows[0]["name"] == "Staged table edit"
