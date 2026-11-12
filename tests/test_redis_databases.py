"""Redis database discovery, isolated operations and durable selection/history."""
import sqlite3
from contextlib import contextmanager
from dataclasses import replace

import fakeredis
import pytest
import redis

from baseskiff.adapters import RedisAdapter, SSHAdapter
from baseskiff.models import Connection
from baseskiff.service import DatabaseService


class DiscoveryClient:
    def __init__(self, configuration=None, keyspace=None):
        self.configuration = {"databases": "4"} if configuration is None else configuration
        self.keyspace = {"db1": {"keys": 7, "expires": 0, "avg_ttl": 0},
                         "db3": {"keys": 11, "expires": 2, "avg_ttl": 1000}} if keyspace is None else keyspace
        self.cluster = {"cluster_enabled": 0}

    def config_get(self, name):
        assert name == "databases"
        if isinstance(self.configuration, Exception):
            raise self.configuration
        return self.configuration

    def info(self, section):
        assert section in {"keyspace", "cluster"}
        result = self.keyspace if section == "keyspace" else self.cluster
        if isinstance(result, Exception):
            raise result
        return result


@pytest.fixture
def discovery(monkeypatch):
    client = DiscoveryClient()
    selected = []

    @contextmanager
    def session(adapter, *, database=None):
        selected.append((adapter.connection.database if database is None else str(database), adapter.endpoint))
        yield client

    monkeypatch.setattr(RedisAdapter, "session", session)
    return client, selected


def test_discovery_includes_empty_databases_and_uses_db0_to_recover_stale_selection(discovery):
    _client, selected = discovery
    result = RedisAdapter(Connection(name="Redis", kind="Redis", database="99")).list_databases()
    assert [(item.index, item.key_count) for item in result.databases] == [(0, 0), (1, 7), (2, 0), (3, 11)]
    assert result.complete
    assert selected == [("0", None)]


@pytest.mark.parametrize("configuration", [
    redis.ResponseError("unknown command CONFIG"),
    redis.exceptions.NoPermissionError("not permitted"),
    {},
    {"databases": "invalid"},
])
def test_discovery_falls_back_to_default_range_and_keeps_current_and_observed_databases(discovery, configuration):
    client, _selected = discovery
    client.configuration = configuration
    client.keyspace = {"db20": {"keys": 9, "expires": 0, "avg_ttl": 0}}
    result = RedisAdapter(Connection(name="Redis", kind="Redis", database="27")).list_databases()
    assert [item.index for item in result.databases] == list(range(16)) + [20, 27]
    assert result.databases[16].key_count == 9
    assert result.databases[0].key_count == 0
    assert not result.complete


def test_discovery_keeps_unknown_counts_when_info_is_denied(discovery):
    client, _selected = discovery
    client.keyspace = redis.exceptions.NoPermissionError("INFO is not permitted")
    result = RedisAdapter(Connection(name="Redis", kind="Redis")).list_databases()
    assert [(item.index, item.key_count) for item in result.databases] == [(0, None), (1, None), (2, None), (3, None)]
    assert result.complete


@pytest.mark.parametrize("configuration", [{"databases": "16"}, redis.exceptions.NoPermissionError("CONFIG is not permitted")])
def test_cluster_discovery_offers_only_db0_even_when_config_reports_more_databases(discovery, configuration):
    client, _selected = discovery
    client.configuration = configuration
    client.cluster = {"cluster_enabled": 1}
    client.keyspace = {"db0": {"keys": 9, "expires": 0, "avg_ttl": 0}}
    result = RedisAdapter(Connection(name="Redis Cluster", kind="Redis")).list_databases()
    assert [(item.index, item.key_count) for item in result.databases] == [(0, 9)]
    assert result.complete


def test_optional_cluster_metadata_denial_preserves_normal_database_discovery(discovery):
    client, _selected = discovery
    client.cluster = redis.exceptions.NoPermissionError("INFO CLUSTER is not permitted")
    result = RedisAdapter(Connection(name="Redis", kind="Redis")).list_databases()
    assert [(item.index, item.key_count) for item in result.databases] == [(0, 0), (1, 7), (2, 0), (3, 11)]
    assert result.complete


@pytest.mark.parametrize("field", ["configuration", "keyspace", "cluster"])
@pytest.mark.parametrize("error", [redis.AuthenticationError("credentials rejected"),
                                   redis.ConnectionError("server unavailable"),
                                   redis.TimeoutError("request timed out")])
def test_discovery_does_not_hide_authentication_or_network_errors(discovery, field, error):
    client, _selected = discovery
    setattr(client, field, error)
    with pytest.raises(type(error), match=str(error)):
        RedisAdapter(Connection(name="Redis", kind="Redis")).list_databases()


def test_ssh_database_discovery_uses_the_same_forwarded_route(discovery, monkeypatch):
    _client, selected = discovery
    endpoint = object()

    @contextmanager
    def tunnel(connection):
        assert connection.ssh_enabled
        yield endpoint

    monkeypatch.setattr("baseskiff.ssh.ssh_endpoint", tunnel)
    connection = Connection(name="SSH Redis", kind="Redis", database="2", ssh_enabled=True)
    result = SSHAdapter(connection).list_databases()
    assert [(item.index, item.key_count) for item in result.databases] == [(0, 0), (1, 7), (2, 0), (3, 11)]
    assert selected == [("0", endpoint)]


@pytest.fixture
def redis_service(tmp_path, monkeypatch):
    server = fakeredis.FakeServer()

    @contextmanager
    def session(adapter, *, database=None):
        index = int((adapter.connection.database or "0") if database is None else database)
        if not 0 <= index < 16:
            raise redis.ResponseError("DB index is out of range")
        with fakeredis.FakeRedis(server=server, db=index, decode_responses=True) as client:
            yield client

    monkeypatch.setattr(RedisAdapter, "session", session)
    service = DatabaseService(tmp_path)
    saved = service.save_connection(Connection(name="Redis", kind="Redis", database="0",
                                               password="isolated-database-test-secret"))
    yield service, saved, server


def test_database_selection_persists_only_after_server_validation(redis_service, tmp_path):
    service, saved, _server = redis_service
    selected = service.set_redis_database(saved.id, 3)
    assert selected.database == "3"
    assert selected.password == ""
    restored = next(item for item in DatabaseService(tmp_path).list_connections() if item.id == saved.id)
    assert restored.database == "3"
    assert service._connection(saved.id).password == "isolated-database-test-secret"
    with pytest.raises(redis.ResponseError, match="out of range"):
        service.set_redis_database(saved.id, 16)
    assert service._connection(saved.id).database == "3"


@pytest.mark.parametrize("index", [-1, True, False, 1.5, "1.5", "", None, "bad"])
def test_invalid_database_selection_leaves_metadata_unchanged(redis_service, index):
    service, saved, _server = redis_service
    with pytest.raises(ValueError, match="nonnegative integer"):
        service.set_redis_database(saved.id, index)
    assert service._connection(saved.id).database == "0"


def test_selection_preserves_fields_changed_while_network_validation_was_running(redis_service, monkeypatch):
    service, saved, _server = redis_service

    def validate(_adapter):
        service.save_connection(replace(saved, name="Renamed", read_only=True, redis_separator="/"))

    monkeypatch.setattr(RedisAdapter, "test", validate)
    selected = service.set_redis_database(saved.id, "2")
    assert (selected.name, selected.database, selected.read_only, selected.redis_separator) == ("Renamed", "2", True, "/")
    assert service._connection(saved.id).password == "isolated-database-test-secret"


def test_selection_does_not_recreate_a_connection_removed_during_validation(redis_service, monkeypatch):
    service, saved, _server = redis_service
    monkeypatch.setattr(RedisAdapter, "test", lambda _adapter: service.delete_connection(saved.id))
    with pytest.raises(ValueError, match="no longer exists"):
        service.set_redis_database(saved.id, 1)
    assert saved.id not in {item.id for item in service.list_connections()}


def test_service_discovery_is_read_only_and_can_recover_a_stale_remembered_database(redis_service):
    service, saved, _server = redis_service
    service.save_connection(replace(saved, database="99"))
    result = service.list_redis_databases(saved.id)
    assert [item.index for item in result.databases] == list(range(16)) + [99]
    assert not result.complete  # fakeredis intentionally does not implement CONFIG GET.
    assert service._connection(saved.id).database == "99"


def test_explicit_database_reads_ignore_later_remembered_selection(redis_service):
    service, saved, server = redis_service
    with fakeredis.FakeRedis(server=server, db=0, decode_responses=True) as first:
        first.set("shared", "db-zero")
        first.set("only-zero", "0")
    with fakeredis.FakeRedis(server=server, db=2, decode_responses=True) as second:
        second.set("shared", "db-two")
        second.set("only-two", "2")
        second.hset("large", mapping={str(index): "v" for index in range(1001)})
    service.set_redis_database(saved.id, 2)
    assert {item.name for item in service.list_tables(saved.id, database="0")} == {"shared", "only-zero"}
    assert [item.name for item in service.search_redis_keys(saved.id, "only-", database="0").keys] == ["only-zero"]
    assert service.read_redis_key(saved.id, "shared", database="0").value == "db-zero"
    assert service.fetch_rows(saved.id, "shared", database="0").rows[0]["value"] == "db-zero"
    assert service.get_schema(saved.id, "large", database="0")[0].primary_key
    assert not service.get_schema(saved.id, "large", database="2")[0].primary_key
    assert service.execute(saved.id, "GET shared", database="0").rows == [{"result": "db-zero"}]
    assert service.execute(saved.id, "GET shared").rows == [{"result": "db-two"}]
    assert service._connection(saved.id).database == "2"


def test_explicit_database_writes_keep_same_named_keys_isolated_and_record_the_database(redis_service):
    service, saved, _server = redis_service
    for index in (0, 2):
        service.execute(saved.id, 'SET shared "original"', database=index)
    service.set_redis_database(saved.id, 2)
    service.insert_row(saved.id, "new", {"key": "new", "type": "string", "value": "created"}, database=0)
    service.update_row(saved.id, "shared", {"key": "shared"}, {"type": "string", "value": "updated"}, database=0)
    service.mutate_redis_entry(saved.id, "shared", "string", "update", {"expected": "updated", "value": "edited"}, database=0)
    service.delete_rows(saved.id, "new", [{"key": "new"}], database=0)
    assert service.read_redis_key(saved.id, "shared", database=0).value == "edited"
    assert service.read_redis_key(saved.id, "shared", database=2).value == "original"
    assert service.read_redis_key(saved.id, "new", database=0).kind == "none"
    with pytest.raises(ValueError, match="missing or changed"):
        service.mutate_redis_entry(saved.id, "shared", "string", "update", {"expected": "wrong", "value": "bad"}, database=0)
    history = service.list_history()
    assert [(item.database, item.success) for item in history[:5]] == [("0", False), ("0", True), ("0", True), ("0", True), ("0", True)]
    assert history[-1].query == 'SET shared "original"'
    assert history[-1].database == "0"
    assert history[-2].database == "2"


@pytest.mark.parametrize("operation", [
    lambda service, cid: service.list_tables(cid, database=1),
    lambda service, cid: service.get_schema(cid, "customers", database=1),
    lambda service, cid: service.fetch_rows(cid, "customers", database=1),
    lambda service, cid: service.execute(cid, "SELECT 1", database=1),
    lambda service, cid: service.insert_row(cid, "customers", {}, database=1),
    lambda service, cid: service.update_row(cid, "customers", {}, {}, database=1),
    lambda service, cid: service.delete_rows(cid, "customers", [], database=1),
    lambda service, cid: service.search_redis_keys(cid, database=1),
    lambda service, cid: service.read_redis_key(cid, "key", database=1),
    lambda service, cid: service.mutate_redis_entry(cid, "key", "string", "delete", {}, database=1),
    lambda service, cid: service.list_redis_databases(cid),
    lambda service, cid: service.set_redis_database(cid, 1),
])
def test_database_overrides_and_selector_apis_reject_non_redis_connections(tmp_path, operation):
    service = DatabaseService(tmp_path)
    demo = service.list_connections()[0]
    with pytest.raises(ValueError, match="Redis"):
        operation(service, demo.id)


def test_history_migrates_existing_rows_without_losing_queries(tmp_path):
    metadata_path = tmp_path / "metadata.sqlite"
    with sqlite3.connect(metadata_path) as metadata:
        metadata.execute("CREATE TABLE history (id TEXT PRIMARY KEY, connection_id TEXT NOT NULL, connection_name TEXT NOT NULL, query TEXT NOT NULL, timestamp TEXT NOT NULL, elapsed_ms REAL NOT NULL, affected INTEGER NOT NULL, success INTEGER NOT NULL, error TEXT NOT NULL DEFAULT '')")
        metadata.execute("INSERT INTO history VALUES ('old', 'old-connection', 'Old database', 'GET old-key', '2026-01-01T00:00:00+00:00', 1, 1, 1, '')")
    service = DatabaseService(tmp_path)
    entry = service.list_history()[0]
    assert (entry.query, entry.database) == ("GET old-key", "")
    demo = service.list_connections()[0]
    service.execute(demo.id, "SELECT 1")
    assert service.list_history()[0].database == ""
    assert DatabaseService(tmp_path).list_history()[-1].query == "GET old-key"
