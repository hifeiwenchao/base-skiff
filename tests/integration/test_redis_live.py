"""Opt-in Redis integration tests; only uniquely prefixed test keys are modified.

BASESKIFF_LIVE_CONFIG points to a private JSON file, never committed credentials.
"""
import shlex
import uuid
from contextlib import contextmanager
from dataclasses import replace

import pytest
import redis

from baseskiff.adapters import MAX_REDIS_KEYS, MAX_ROWS, RedisAdapter
from baseskiff.models import Connection

pytestmark = pytest.mark.integration


class RedisEnvironment:
    """Default object repr deliberately excludes connection credentials."""


@pytest.fixture
def redis_env(live_config, live_service, run_tag):
    config = live_config.get("redis")
    if not config:
        pytest.skip("The live configuration does not provide Redis")
    environment = RedisEnvironment()
    environment.prefix = f"{run_tag}:redis:{uuid.uuid4().hex[:8]}:"
    environment.service = live_service
    connection = Connection(
        name="Redis live integration",
        kind="Redis",
        host=live_config["host"],
        port=int(config["port"]),
        database="0",
        username=config.get("username", ""),
        password=config.get("password", ""),
    )
    saved = live_service.save_connection(connection)
    environment.connection_id = saved.id
    environment.client = redis.Redis(
        host=connection.host,
        port=connection.port,
        username=connection.username or None,
        password=connection.password or None,
        db=0,
        protocol=2,
        decode_responses=True,
        socket_connect_timeout=8,
        socket_timeout=10,
    )
    try:
        environment.client.ping()
        yield environment
    finally:
        # Never FLUSHDB: preserve any unrelated or concurrently created data.
        owned_keys = list(environment.client.scan_iter(match=environment.prefix + "*", count=100))
        if owned_keys:
            environment.client.delete(*owned_keys)
        assert list(environment.client.scan_iter(match=environment.prefix + "*", count=100)) == []
        environment.client.close()


def _key(environment, suffix="record"):
    return environment.prefix + suffix


def _fetch(environment, key, **kwargs):
    return environment.service.fetch_rows(environment.connection_id, key, **kwargs)


def _raw_value(client, key, kind):
    if kind == "string":
        return client.get(key)
    if kind == "hash":
        return client.hgetall(key)
    if kind == "list":
        return client.lrange(key, 0, -1)
    if kind == "set":
        return client.smembers(key)
    return dict(client.zrange(key, 0, -1, withscores=True))


def test_authentication_connection_metadata_and_key_listing(redis_env):
    env = redis_env
    key = _key(env, "你好 key with spaces")
    env.client.set(key, "a real key")
    saved = next(c for c in env.service.list_connections() if c.id == env.connection_id)
    assert saved.password == ""
    assert "successfully" in env.service.test_connection(saved)
    tables = env.service.list_tables(env.connection_id)
    assert len(tables) <= MAX_REDIS_KEYS
    assert all(table.kind in {"string", "hash", "list", "set", "zset", "stream"} for table in tables)
    # A bounded sidebar is not required to return our key in an occupied DB.
    if env.client.dbsize() <= MAX_REDIS_KEYS:
        assert key in {table.name for table in tables}
    with pytest.raises(redis.AuthenticationError):
        env.service.test_connection(replace(saved, password="invalid-" + uuid.uuid4().hex))
    assert env.client.get(key) == "a real key"


@pytest.mark.parametrize(("kind", "original", "updated"), [
    ("string", "初始 value", "更新 'quoted' value $()"),
    ("hash", {"名字": "张 三", "plan": "basic"}, {"名字": "李 四", "plan": "pro"}),
    ("list", ["first item", "你好"], ["更新 item", "second", "second"]),
    ("set", ["first item", "你好"], ["更新 item", "second", "second"]),
    ("zset", {"你好 member": 1, "first item": 2.5}, {"更新 item": -2.5, "second": 3}),
])
def test_grid_crud_and_ttl_for_every_supported_type(redis_env, kind, original, updated):
    env = redis_env
    key = _key(env)
    inserted = env.service.insert_row(env.connection_id, key, {"key": key, "type": kind, "value": original})
    assert inserted.affected == 1
    assert env.client.type(key) == kind
    result = _fetch(env, key)
    assert result.total == 1
    assert result.rows[0]["key"] == key
    assert result.rows[0]["type"] == kind
    assert not result.truncated
    assert any(column.primary_key for column in env.service.get_schema(env.connection_id, key))
    initial_expected = set(original) if kind == "set" else original
    assert _raw_value(env.client, key, kind) == initial_expected
    env.client.pexpire(key, 60_000)
    before_ttl = env.client.pttl(key)
    changed = env.service.update_row(env.connection_id, key, {"key": key}, {"type": kind, "value": updated})
    assert changed.affected == 1
    expected = set(updated) if kind == "set" else updated
    assert _raw_value(env.client, key, kind) == expected
    assert 0 < env.client.pttl(key) <= before_ttl
    assert env.client.pttl(key) >= before_ttl - 5_000
    removed = env.service.delete_rows(env.connection_id, key, [{"key": key}])
    assert removed.affected == 1
    assert env.client.exists(key) == 0
    assert _fetch(env, key).rows == []


def test_persistent_key_and_type_change_preserve_data(redis_env):
    env = redis_env
    key = _key(env)
    env.client.set(key, "original")
    env.service.update_row(env.connection_id, key, {"key": key}, {"type": "hash", "value": {"name": "新 value"}})
    assert env.client.hgetall(key) == {"name": "新 value"}
    assert env.client.pttl(key) == -1


@pytest.mark.parametrize(("kind", "invalid"), [
    ("zset", {"member": "not-a-number"}),
    ("zset", {"member": float("nan")}),
    ("zset", {"member": float("inf")}),
    ("hash", {"field": {"nested": "value"}}),
    ("hash", ["wrong shape"]),
    ("list", ["first", {"nested": 1}]),
    ("set", [None]),
    ("zset", {}),
])
def test_invalid_replacement_preserves_original_value_and_expiration(redis_env, kind, invalid):
    env = redis_env
    key = _key(env)
    env.client.zadd(key, {"original member": 3.5})
    env.client.pexpire(key, 60_000)
    before = env.client.dump(key)
    before_ttl = env.client.pttl(key)
    with pytest.raises(ValueError):
        env.service.update_row(env.connection_id, key, {"key": key}, {"type": kind, "value": invalid})
    assert env.client.dump(key) == before
    assert 0 < env.client.pttl(key) <= before_ttl
    assert env.client.pttl(key) >= before_ttl - 5_000


@pytest.mark.parametrize("kind", ["hash", "list", "set", "zset"])
def test_large_collection_preview_cannot_overwrite_unloaded_members(redis_env, kind):
    env = redis_env
    key = _key(env)
    members = [f"member-{index:04d}" for index in range(MAX_ROWS + 1)]
    if kind == "hash":
        env.client.hset(key, mapping={member: str(index) for index, member in enumerate(members)})
    elif kind == "list":
        env.client.rpush(key, *members)
    elif kind == "set":
        env.client.sadd(key, *members)
    else:
        env.client.zadd(key, {member: index for index, member in enumerate(members)})
    before = env.client.dump(key)
    preview = _fetch(env, key)
    assert preview.truncated
    assert len(preview.rows[0]["value"]) == MAX_ROWS
    assert not any(column.primary_key for column in env.service.get_schema(env.connection_id, key))
    with pytest.raises(ValueError, match="1000|truncat|large"):
        env.service.update_row(env.connection_id, key, {"key": key}, preview.rows[0])
    assert env.client.dump(key) == before


def test_unicode_search_missing_key_and_pagination(redis_env):
    env = redis_env
    key = _key(env)
    env.client.set(key, "你好 World")
    assert _fetch(env, key, search="你好").rows[0]["value"] == "你好 World"
    assert _fetch(env, key, search="WORLD").total == 1
    assert _fetch(env, key, search="not found").rows == []
    assert _fetch(env, key, page=2).rows == []
    assert _fetch(env, key, page=2).total == 1
    assert _fetch(env, _key(env, "missing")).total == 0


def test_query_commands_preserve_literal_unicode_and_spaces(redis_env):
    env = redis_env
    key = _key(env, "带 空格 key")
    value = "你好 literal 'quotes' \"double\" $() ; spaces"
    env.service.execute(env.connection_id, shlex.join(["SET", key, value]))
    assert env.client.get(key) == value
    response = env.service.execute(env.connection_id, shlex.join(["GET", key]))
    assert response.rows == [{"result": value}]
    assert response.elapsed_ms > 0
    deleted = env.service.execute(env.connection_id, shlex.join(["DEL", key]))
    assert deleted.rows == [{"result": 1}]


def test_query_results_are_bounded_for_real_large_reply(redis_env):
    env = redis_env
    key = _key(env)
    env.client.rpush(key, *[str(index) for index in range(MAX_ROWS + 1)])
    response = env.service.execute(env.connection_id, shlex.join(["LRANGE", key, "0", "-1"]))
    assert response.truncated
    assert len(response.rows) == MAX_ROWS
    assert response.rows[0] == {"value": "0"}
    assert env.client.llen(key) == MAX_ROWS + 1


def test_read_only_connections_allow_reads_and_deny_query_and_grid_writes(redis_env):
    env = redis_env
    key = _key(env)
    env.client.set(key, "unchanged")
    connection = env.service._connection(env.connection_id)
    readonly = env.service.save_connection(replace(connection, id=uuid.uuid4().hex, read_only=True))
    assert env.service.execute(readonly.id, shlex.join(["GET", key])).rows == [{"result": "unchanged"}]
    assert env.service.fetch_rows(readonly.id, key).rows[0]["value"] == "unchanged"
    attempts = [
        lambda: env.service.execute(readonly.id, shlex.join(["SET", key, "overwritten"])),
        lambda: env.service.insert_row(readonly.id, _key(env, "new"), {"value": "new"}),
        lambda: env.service.update_row(readonly.id, key, {"key": key}, {"value": "overwritten"}),
        lambda: env.service.delete_rows(readonly.id, key, [{"key": key}]),
    ]
    for attempt in attempts:
        with pytest.raises(PermissionError):
            attempt()
    assert env.client.get(key) == "unchanged"
    assert env.client.exists(_key(env, "new")) == 0


def test_query_and_mutation_success_and_failure_are_recorded(redis_env):
    env = redis_env
    key = _key(env)
    env.service.clear_history()
    env.service.insert_row(env.connection_id, key, {"value": "history value"})
    env.service.execute(env.connection_id, shlex.join(["GET", key]))
    with pytest.raises(redis.ResponseError):
        env.service.execute(env.connection_id, shlex.join(["HGETALL", key]))
    with pytest.raises(ValueError, match="already exists"):
        env.service.insert_row(env.connection_id, key, {"value": "overwrite"})
    entries = env.service.list_history()
    assert len(entries) == 4
    assert [entry.success for entry in reversed(entries)] == [True, True, False, False]
    assert all(entry.connection_id == env.connection_id and entry.elapsed_ms > 0 for entry in entries)
    assert all(entry.error for entry in entries if not entry.success)
    assert env.client.get(key) == "history value"
    env.service.clear_history()
    assert env.service.list_history() == []


@pytest.mark.parametrize("operation", ["insert", "update"])
def test_actual_concurrent_writer_is_not_overwritten(redis_env, monkeypatch, operation):
    env = redis_env
    key = _key(env)
    if operation == "update":
        env.client.set(key, "original")
    adapter = RedisAdapter(env.service._connection(env.connection_id))
    original_pipeline = env.client.pipeline
    raced = False

    def pipeline(*args, **kwargs):
        pipe = original_pipeline(*args, **kwargs)
        original_execute = pipe.execute

        def execute(*args, **kwargs):
            nonlocal raced
            # Inject the real competing write just before the final RENAME/MULTI.
            if not raced and any(command[0][0] == "RENAME" for command in pipe.command_stack):
                raced = True
                env.client.set(key, "another writer")
            return original_execute(*args, **kwargs)

        pipe.execute = execute
        return pipe

    @contextmanager
    def session():
        yield env.client

    monkeypatch.setattr(env.client, "pipeline", pipeline)
    monkeypatch.setattr(adapter, "session", session)
    with pytest.raises(ValueError, match="changed concurrently"):
        if operation == "insert":
            adapter.insert_row(key, {"key": key, "value": "our value"})
        else:
            adapter.update_row(key, {"key": key}, {"value": "our value"})
    assert raced
    assert env.client.get(key) == "another writer"


@pytest.mark.parametrize("query", ["", "SUBSCRIBE test-channel", "BLPOP test-key 0", "MONITOR"])
def test_empty_interactive_and_blocking_commands_fail_before_execution(redis_env, query):
    with pytest.raises(ValueError):
        redis_env.service.execute(redis_env.connection_id, query)


def test_existing_and_missing_grid_keys_fail_without_creating_or_losing_data(redis_env):
    env = redis_env
    key = _key(env)
    env.client.set(key, "original")
    with pytest.raises(ValueError, match="already exists"):
        env.service.insert_row(env.connection_id, key, {"value": "overwrite"})
    with pytest.raises(ValueError, match="no longer exists"):
        env.service.update_row(env.connection_id, _key(env, "missing"), {"key": _key(env, "missing")}, {"value": "new"})
    with pytest.raises(ValueError, match="primary key"):
        env.service.delete_rows(env.connection_id, key, [{"wrong": key}])
    assert env.client.get(key) == "original"
    assert env.client.exists(_key(env, "missing")) == 0
