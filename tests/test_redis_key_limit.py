"""The Redis sidebar bounds key discovery independently of value/query rows."""
from contextlib import contextmanager

import fakeredis
import pytest

from baseskiff.adapters import RedisAdapter
from baseskiff.models import Connection, RedisKeyPage, TableInfo
from baseskiff.service import DatabaseService
from baseskiff.ui.window import MainWindow


@pytest.fixture
def redis_keys(monkeypatch):
    client = fakeredis.FakeRedis(decode_responses=True)
    adapter = RedisAdapter(Connection("Key limit", kind="Redis"))

    @contextmanager
    def session():
        yield client

    monkeypatch.setattr(adapter, "session", session)
    yield adapter, client
    client.close()


@pytest.mark.parametrize("size", [499, 500, 501, 600])
def test_default_search_returns_at_most_500_keys(redis_keys, size):
    adapter, client = redis_keys
    client.mset({f"item:{index:04}": "value" for index in range(size)})

    page = adapter.search_keys("item:")

    assert len(page.keys) == min(size, 500)
    assert page.truncated is (size > 500)
    assert all(key.kind == "string" for key in page.keys)


def test_default_scan_preserves_overflow_and_types_only_current_batch(monkeypatch):
    scanned, typed, counts = [], [], []

    class Pipeline:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def type(self, key):
            typed.append(key)

        def execute(self):
            return ["string"] * len(typed)

    class Client:
        def scan(self, cursor, *, match, count):
            assert cursor == 0
            assert match == "*"
            counts.append(count)
            for index in range(600):
                key = f"item:{index:04}"
                scanned.append(key)
                # Redis SCAN may repeat keys before the distinct-key limit.
                scanned.append(key)
            return 0, scanned

        def pipeline(self, *, transaction):
            assert transaction is False
            return Pipeline()

    @contextmanager
    def session():
        yield Client()

    adapter = RedisAdapter(Connection("Bounded scan", kind="Redis"))
    monkeypatch.setattr(adapter, "session", session)

    page = adapter.search_keys()

    assert len(set(scanned)) == 600
    assert counts == [500]
    assert [key.name for key in page.keys] == [f"item:{index:04}" for index in range(500)]
    assert typed == [key.name for key in page.keys]
    assert page.truncated
    assert page.continuation is not None
    assert len(page.continuation.pending) == 100


@pytest.mark.parametrize(("limit", "expected"), [(7, 7), (1000, 500)])
def test_explicit_search_limit_can_reduce_but_not_exceed_sidebar_cap(redis_keys, limit, expected):
    adapter, client = redis_keys
    client.mset({f"item:{index:04}": "value" for index in range(600)})

    page = adapter.search_keys(limit=limit)

    assert len(page.keys) == expected
    assert page.truncated


@pytest.mark.parametrize("use_ssh", [False, True])
def test_service_search_keeps_500_key_cap_over_direct_and_ssh_routes(tmp_path, monkeypatch, use_ssh):
    client = fakeredis.FakeRedis(decode_responses=True)
    client.mset({f"item:{index:04}": "value" for index in range(600)})
    selected = []
    endpoint = object()

    @contextmanager
    def session(adapter):
        selected.append((adapter.connection.database, adapter.endpoint))
        yield client

    @contextmanager
    def tunnel(connection):
        assert connection.ssh_enabled
        yield endpoint

    monkeypatch.setattr(RedisAdapter, "session", session)
    monkeypatch.setattr("baseskiff.ssh.ssh_endpoint", tunnel)
    service = DatabaseService(tmp_path)
    connection = service.save_connection(Connection("Key limit", kind="Redis", database="2",
                                                     ssh_enabled=use_ssh, ssh_host="test.invalid",
                                                     ssh_username="tester", ssh_auth_method="agent"))
    try:
        page = service.search_redis_keys(connection.id, "item:", database="3")

        assert len(page.keys) == 500
        assert page.truncated
        assert selected == [("3", endpoint if use_ssh else None)]
    finally:
        client.close()


@pytest.mark.parametrize(("language", "expected"), [
    ("en", "500 keys loaded · Up to 500 per load"),
    ("zh", "已加载 500 个键 · 每次最多加载 500 个"),
])
def test_truncated_sidebar_status_displays_per_load_limit(qtbot, tmp_path, monkeypatch, language, expected):
    monkeypatch.setattr(MainWindow, "_open_initial", lambda _window: None)
    window = MainWindow(DatabaseService(tmp_path), language=language)
    qtbot.addWidget(window)
    connection = Connection("Key limit", kind="Redis")
    window.current_connection = connection
    window._redis_key_pages[window._redis_scope(connection)] = RedisKeyPage(
        [TableInfo(f"item:{index:04}") for index in range(500)], truncated=True)

    window._sync_redis_search()

    assert window.redis_key_status.text() == expected
    window.close()
