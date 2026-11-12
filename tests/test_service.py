"""Behavioral coverage against real on-disk SQLite databases."""
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from baseskiff.models import Connection
from baseskiff.service import DatabaseService


@pytest.fixture
def service(tmp_path):
    return DatabaseService(tmp_path)


@pytest.fixture
def local(service, tmp_path):
    path = tmp_path / "work.sqlite"
    with sqlite3.connect(path) as db:
        db.executescript('''
            CREATE TABLE people (id INTEGER PRIMARY KEY, name TEXT NOT NULL, score INTEGER DEFAULT 0);
            INSERT INTO people(name, score) VALUES ('Alice', 30), ('Bob', 20), ('Chloé', 10);
            CREATE TABLE notes (body TEXT);
            CREATE TABLE "odd"" table" ("key id" INTEGER PRIMARY KEY, "select" TEXT);
            CREATE TABLE children (id INTEGER PRIMARY KEY, person_id INTEGER REFERENCES people(id));
            INSERT INTO children VALUES (1, 2);
        ''')
    return service.save_connection(Connection(name="Workspace", path=str(path)))


def test_playground_is_real_persistent_and_only_seeded_once(service, tmp_path):
    connections = service.list_connections()
    assert len(connections) == 1
    demo = connections[0]
    assert demo.name == "BaseSkiff Playground" and demo.is_demo
    assert {"customers", "products", "orders", "order_items", "categories", "activity_log"} <= {
        table.name for table in service.list_tables(demo.id)
    }
    assert service.fetch_rows(demo.id, "customers").total == 128
    service.execute(demo.id, "UPDATE customers SET name = 'A real edit' WHERE id = 1")
    reopened = DatabaseService(tmp_path)
    assert reopened.execute(demo.id, "SELECT name FROM customers WHERE id = 1").rows == [{"name": "A real edit"}]
    service.delete_connection(demo.id)
    assert DatabaseService(tmp_path).list_connections() == []


def test_browse_search_sort_pagination_and_schema(service, local):
    result = service.fetch_rows(local.id, "people", page=2, page_size=1, sort_column="score", descending=True)
    assert result.total == 3 and result.rows == [{"id": 2, "name": "Bob", "score": 20}]
    assert service.fetch_rows(local.id, "people", search="Chloé").total == 1
    assert service.fetch_rows(local.id, "people", search="%' OR 1=1 --").total == 0
    columns = service.get_schema(local.id, "people")
    assert [(column.name, column.primary_key) for column in columns] == [("id", True), ("name", False), ("score", False)]
    assert service.test_connection(local)


def test_crud_binds_values_and_quotes_identifiers(service, local):
    payload = "Robert'); DROP TABLE people;--"
    assert service.insert_row(local.id, 'odd" table', {"key id": 1, "select": payload}).affected == 1
    assert service.fetch_rows(local.id, 'odd" table').rows[0]["select"] == payload
    assert service.update_row(local.id, 'odd" table', {"key id": 1}, {"select": "updated"}).affected == 1
    assert service.delete_rows(local.id, 'odd" table', [{"key id": 1}]).affected == 1
    assert service.fetch_rows(local.id, "people").total == 3


def test_grid_writes_require_exact_primary_key_and_valid_columns(service, local):
    with pytest.raises(ValueError, match="primary key"):
        service.update_row(local.id, "people", {"name": "Alice"}, {"score": 99})
    with pytest.raises(ValueError, match="primary key"):
        service.insert_row(local.id, "notes", {"body": "no key"})
    with pytest.raises(ValueError):
        service.insert_row(local.id, "people", {"missing": "bad"})
    with pytest.raises(ValueError):
        service.fetch_rows(local.id, "people", sort_column="score DESC; DROP TABLE people")
    assert service.fetch_rows(local.id, "people").rows[0]["score"] == 30


def test_batch_delete_rolls_back_when_a_later_record_fails(service, local):
    with pytest.raises(Exception):
        service.delete_rows(local.id, "people", [{"id": 1}, {"id": 2}])
    assert service.execute(local.id, "SELECT id FROM people ORDER BY id").rows == [{"id": 1}, {"id": 2}, {"id": 3}]


@pytest.mark.parametrize("query", [
    "UPDATE people SET score = 9", "/* hidden */ DELETE FROM people",
    "WITH x AS (SELECT 1) DELETE FROM people", "PRAGMA user_version = 99",
    "ATTACH DATABASE ':memory:' AS other", "CREATE TABLE hidden (id INT)",
])
def test_read_only_connection_blocks_all_sql_mutations(service, local, query):
    local = service.save_connection(replace(local, read_only=True))
    with pytest.raises(Exception):
        service.execute(local.id, query)
    with pytest.raises(PermissionError):
        service.insert_row(local.id, "people", {"name": "denied"})
    assert service.execute(local.id, "WITH x AS (SELECT * FROM people) SELECT * FROM x").total == 3


def test_query_cap_and_durable_success_failure_history(service, local, tmp_path):
    result = service.execute(local.id, "WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM n WHERE x < 1100) SELECT x FROM n")
    assert len(result.rows) == 1000 and result.truncated
    with pytest.raises(Exception):
        service.execute(local.id, "SELECT missing FROM people")
    history = DatabaseService(tmp_path).list_history()
    assert history[0].success is False and history[0].error
    assert history[1].success is True and history[1].affected == 1000
    assert all(entry.elapsed_ms >= 0 and entry.connection_name == "Workspace" for entry in history)
    service.clear_history()
    assert service.list_history() == []


def test_passwords_never_enter_metadata_and_connection_edits_persist(service, local, tmp_path):
    secret = "never-write-this-password"
    saved = service.save_connection(replace(local, password=secret, name="Renamed", group="Development"))
    assert next(item for item in service.list_connections() if item.id == saved.id).name == "Renamed"
    for path in tmp_path.glob("*.sqlite*"):
        assert secret.encode() not in path.read_bytes()
    service.delete_connection(saved.id)
    assert saved.id not in [item.id for item in service.list_connections()]


def test_worker_calls_use_separate_connections(service, local):
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: service.fetch_rows(local.id, "people").total, range(12)))
    assert results == [3] * 12


def test_sql_json_editor_values_are_serialized_without_losing_structure(service, local):
    service.execute(local.id, "CREATE TABLE documents (id INTEGER PRIMARY KEY, payload JSON)")
    service.insert_row(local.id, "documents", {"id": 1, "payload": {"tags": ["design", "研发"], "enabled": True}})
    assert service.fetch_rows(local.id, "documents").rows[0]["payload"] == '{"tags": ["design", "研发"], "enabled": true}'
    service.update_row(local.id, "documents", {"id": 1}, {"payload": [1, 2, 3]})
    assert service.fetch_rows(local.id, "documents").rows[0]["payload"] == "[1, 2, 3]"
