from contextlib import contextmanager
from dataclasses import replace

import fakeredis
import pytest
from PySide6.QtWidgets import QTreeWidgetItem

from baseskiff.adapters import MAX_ROWS, RedisAdapter
from baseskiff.models import Connection, TableInfo
from baseskiff.ui.redis_tree import append_redis_keys


@pytest.fixture
def backend(monkeypatch):
    client = fakeredis.FakeRedis()
    adapter = RedisAdapter(Connection("Batches", kind="Redis"))

    @contextmanager
    def session():
        yield client

    monkeypatch.setattr(adapter, "session", session)
    return adapter, client


@pytest.mark.parametrize("kind", ["hash", "list", "set", "zset"])
def test_collection_continuation_loads_every_identity(backend, kind):
    adapter, client = backend
    values = {f"{i}".encode(): f"v{i}".encode() for i in range(1205)}
    values[b"\xff"] = b"binary"
    if kind == "hash":
        client.hset("key", mapping=values)
    elif kind == "list":
        client.rpush("key", *values)
    elif kind == "set":
        client.sadd("key", *values)
    else:
        client.zadd("key", {key: i for i, key in enumerate(values)})
    snapshot = adapter.read_key("key")
    loaded = dict(snapshot.value) if kind in {"hash", "zset"} else list(snapshot.value)
    assert len(loaded) <= MAX_ROWS
    assert snapshot.continuation is not None
    for _ in range(20):
        page = adapter.read_key("key", continuation=snapshot.continuation)
        if isinstance(loaded, dict):
            loaded.update(page.value)
        else:
            loaded.extend(page.value)
        snapshot = page
        if snapshot.continuation is None:
            break
    assert snapshot.continuation is None
    assert len(loaded) == len(values)
    assert b"\xff" in loaded


def test_continuation_rejects_wrong_scope_and_changed_type(backend):
    adapter, client = backend
    client.rpush("key", *range(1100))
    token = adapter.read_key("key").continuation
    with pytest.raises(ValueError):
        adapter.read_key("other", continuation=token)
    with pytest.raises(ValueError):
        adapter.read_key("key", continuation=replace(token, database="1"))
    client.set("key", "new")
    with pytest.raises(ValueError, match="changed|expired"):
        adapter.read_key("key", continuation=token)


def test_incremental_tree_retains_nodes_and_counts(qapp):
    root = QTreeWidgetItem()
    connection = Connection("Tree", kind="Redis")
    first = append_redis_keys(root, connection, [TableInfo("a:1")], "empty", "keys")
    folder, leaf = root.child(0), first["a:1"]
    second = append_redis_keys(root, connection, [TableInfo("a:2"), TableInfo("a:1")], "empty", "keys")
    assert root.childCount() == 1
    assert root.child(0) is folder
    assert second["a:1"] is leaf
    assert folder.childCount() == 2
    assert folder.text(0) == "a (2)"


def test_scan_overflow_deduplication_and_bounded_sparse_calls(backend, monkeypatch):
    adapter, client = backend
    client.hset("key", mapping={"first": "v"})
    calls = []

    def scan(key, cursor=0, count=0):
        calls.append(cursor)
        if cursor == 0:
            return 1, {f"f{i}".encode(): b"v" for i in range(1200)}
        return 0, {b"f0": b"v", b"last": b"v"}

    monkeypatch.setattr(client, "hscan", scan)
    first = adapter.read_key("key")
    second = adapter.read_key("key", continuation=first.continuation)
    assert len(first.value) == MAX_ROWS
    assert len(second.value) == 201
    assert set(first.value).isdisjoint(second.value)
    assert second.continuation is None

    def sparse(key, cursor=0, count=0):
        calls.append(cursor)
        return cursor + 1, {}

    monkeypatch.setattr(client, "hscan", sparse)
    calls.clear()
    sparse_page = adapter.read_key("key")
    assert len(calls) == 8
    assert sparse_page.continuation is not None


def test_tree_cache_drops_detached_and_deleted_nodes(qapp):
    from shiboken6 import delete

    root = QTreeWidgetItem()
    connection = Connection("Tree", kind="Redis")
    append_redis_keys(root, connection, [TableInfo("a:1")], "empty", "keys")
    for child in root.takeChildren():
        delete(child)
    leaves = append_redis_keys(root, connection, [TableInfo("b:1")], "empty", "keys")
    assert set(leaves) == {"b:1"}
    assert root.childCount() == 1


def test_widget_continuation_shows_loaded_rows_and_keeps_large_list_delete_disabled(qtbot, backend):
    from baseskiff.ui.redis_value import RedisValueWidget

    adapter, client = backend
    client.rpush("key", *range(1001))
    snapshot = adapter.read_key("key")
    widget = RedisValueWidget(language="en")
    qtbot.addWidget(widget)
    widget.set_value(snapshot)
    assert not widget.load_more_button.isHidden()
    with qtbot.waitSignal(widget.load_more_requested):
        widget.load_more_button.click()
    page = adapter.read_key("key", continuation=snapshot.continuation)
    merged = replace(page, value=[*snapshot.value, *page.value])
    widget.set_value(merged)
    assert widget.table.rowCount() == 1001
    widget.table.selectRow(0)
    assert not widget.delete_button.isEnabled()
    assert widget.load_more_button.isHidden()


def test_widget_append_retains_loaded_items(qtbot, backend):
    from baseskiff.ui.redis_value import RedisValueWidget

    adapter, client = backend
    client.rpush("key", *range(1001))
    first = adapter.read_key("key")
    last = adapter.read_key("key", continuation=first.continuation)
    widget = RedisValueWidget(language="en")
    qtbot.addWidget(widget)
    widget.set_value(first)
    original = widget.table.item(0, 0)
    widget.set_value(replace(last, value=[*first.value, *last.value]))
    assert widget.table.item(0, 0) is original


def test_service_continuation_merges_without_mutating_previous(backend, monkeypatch):
    from baseskiff.service import DatabaseService

    adapter, client = backend
    client.hset("key", mapping={f"f{i}": f"v{i}" for i in range(1200)})
    service = DatabaseService.__new__(DatabaseService)
    monkeypatch.setattr(service, "_redis_adapter", lambda connection_id, database=None: adapter)
    first = service.read_redis_key(adapter.connection.id, "key")
    original = dict(first.value)
    result = service.continue_redis_value(adapter.connection.id, first)
    assert len(result.value) == 1200
    assert first.value == original
    assert result.continuation is None
    assert service.continue_redis_value(adapter.connection.id, result) is result


def test_tree_incremental_append_keeps_mounted_search_widget(qtbot):
    from PySide6.QtWidgets import QLineEdit, QTreeWidget

    from baseskiff.ui.redis_tree import NODE_KIND_ROLE

    tree = QTreeWidget()
    qtbot.addWidget(tree)
    root = QTreeWidgetItem(tree)
    search = QTreeWidgetItem(root, ["Search"])
    search.setData(0, NODE_KIND_ROLE, "redis_search")
    editor = QLineEdit()
    tree.setItemWidget(search, 0, editor)
    connection = Connection("Tree", kind="Redis")
    append_redis_keys(root, connection, [TableInfo("z:2")], "empty", "keys")
    append_redis_keys(root, connection, [TableInfo("a:1")], "empty", "keys")
    assert root.child(0) is search
    assert tree.itemWidget(search, 0) is editor
    assert root.child(1).text(0) == "a (1)"


def test_window_collection_load_more_preserves_origin_and_rows(qtbot, tmp_path, monkeypatch):
    from baseskiff.models import (
        ColumnInfo,
        RedisDatabaseInfo,
        RedisDatabaseList,
        RedisKeyPage,
        RedisValue,
        RedisValueContinuation,
    )
    from baseskiff.service import DatabaseService
    from baseskiff.ui.window import MainWindow

    window = MainWindow(DatabaseService(tmp_path), language='en')
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: not window.busy and window.current_table == 'customers')
    connection = window.service.save_connection(Connection('paged', kind='Redis', database='0'))
    window.connections = window.service.list_connections()
    errors = []
    monkeypatch.setattr(window, 'show_error', errors.append)
    monkeypatch.setattr(window.service, 'list_redis_databases', lambda *a, **kw: RedisDatabaseList([RedisDatabaseInfo(0,1)]))
    monkeypatch.setattr(window.service, 'search_redis_keys', lambda *a, **kw: RedisKeyPage([]))
    monkeypatch.setattr(window.service, 'get_schema', lambda *a, **kw: [ColumnInfo('key',primary_key=True),ColumnInfo('value')])
    window.current_connection = connection
    first = RedisValue('key','hash',{'one':'1'},size=2,truncated=True,
        continuation=RedisValueContinuation(connection.id,'0',b'key','hash',cursor=1))
    monkeypatch.setattr(window.service,'read_redis_key',lambda *args,**kwargs:first)
    window.open_table('key')
    qtbot.waitUntil(lambda: not window.busy)
    initial_item = window.redis_view.table.item(0,0)
    calls = []
    def more(cid,snapshot,*,database=None):
        calls.append((cid,database,snapshot.key))
        return RedisValue('key','hash',{'one':'1','two':'2'},size=2)
    monkeypatch.setattr(window.service,'continue_redis_value',more)
    window.redis_view.load_more_button.click()
    qtbot.waitUntil(lambda: not window.busy)
    assert calls == [(connection.id,'0','key')]
    assert window.redis_view.table.rowCount() == 2
    assert window.redis_view.table.item(0,0) is initial_item
    assert window.redis_view.load_more_button.isHidden()
    window.close()
