"""Redis command behavior through redis-py's client and an in-process Redis engine."""
from contextlib import contextmanager

import fakeredis
import pytest

from baseskiff.adapters import MAX_ROWS, RedisAdapter
from baseskiff.models import Connection


@pytest.fixture
def redis_adapter(monkeypatch):
    client = fakeredis.FakeRedis(decode_responses=True)
    adapter = RedisAdapter(Connection(name="Redis tests", kind="Redis"))

    @contextmanager
    def session():
        yield client

    monkeypatch.setattr(adapter, "session", session)
    yield adapter, client
    client.close()


def seed(client, kind, count=2):
    if kind == "string":
        client.set("record", "original")
    elif kind == "hash":
        client.hset("record", mapping={f"field-{index}": str(index) for index in range(count)})
    elif kind == "list":
        client.rpush("record", *[str(index) for index in range(count)])
    elif kind == "set":
        client.sadd("record", *[str(index) for index in range(count)])
    elif kind == "zset":
        client.zadd("record", {str(index): index for index in range(count)})


@pytest.mark.parametrize("kind", ["hash", "list", "set", "zset"])
def test_large_collection_is_marked_truncated_and_cannot_be_overwritten(redis_adapter, kind):
    adapter, client = redis_adapter
    seed(client, kind, MAX_ROWS + 1)
    before = client.dump("record")
    result = adapter.fetch_rows("record")
    assert result.truncated
    assert len(result.rows[0]["value"]) == MAX_ROWS
    assert not any(column.primary_key for column in adapter.get_schema("record"))
    with pytest.raises(ValueError, match="1000|truncat|large"):
        adapter.update_row("record", {"key": "record"}, result.rows[0])
    assert client.dump("record") == before


@pytest.mark.parametrize(("kind", "value"), [
    ("string", "updated"), ("hash", {"name": "updated"}),
    ("list", ["new", "items"]), ("set", ["new", "items"]),
    ("zset", {"new": 4.5, "items": 8}),
])
def test_edit_preserves_existing_key_expiration(redis_adapter, kind, value):
    adapter, client = redis_adapter
    seed(client, kind)
    client.pexpire("record", 60_000)
    adapter.update_row("record", {"key": "record"}, {"type": kind, "value": value})
    assert 59_000 <= client.pttl("record") <= 60_000
    assert adapter.fetch_rows("record").rows[0]["value"] != "original"
    assert client.keys("*__baseskiff*") == []


@pytest.mark.parametrize(("kind", "value"), [
    ("zset", {"member": "not-a-number"}), ("zset", {"member": float("nan")}),
    ("hash", {"nested": {"invalid": "value"}}), ("list", ["okay", {"invalid": 1}]),
    ("set", [None]), ("zset", {}),
])
def test_invalid_replacement_keeps_original_value_and_ttl(redis_adapter, kind, value):
    adapter, client = redis_adapter
    seed(client, "zset")
    before = client.dump("record")
    client.pexpire("record", 60_000)
    with pytest.raises(ValueError):
        adapter.update_row("record", {"key": "record"}, {"type": kind, "value": value})
    assert client.dump("record") == before
    assert 59_000 <= client.pttl("record") <= 60_000
    assert client.keys("*__baseskiff*") == []


def test_valid_replacement_of_persistent_key_keeps_it_persistent(redis_adapter):
    adapter, client = redis_adapter
    seed(client, "string")
    adapter.update_row("record", {"key": "record"}, {"type": "hash", "value": {"name": "new"}})
    assert client.hgetall("record") == {"name": "new"}
    assert client.pttl("record") == -1


def race_before_first_commit(monkeypatch, client, competing_value):
    original_pipeline = client.pipeline
    raced = False

    def pipeline(*args, **kwargs):
        pipe = original_pipeline(*args, **kwargs)
        original_execute = pipe.execute

        def execute(*args, **kwargs):
            nonlocal raced
            if not raced:
                raced = True
                client.set("record", competing_value)
            return original_execute(*args, **kwargs)

        pipe.execute = execute
        return pipe

    monkeypatch.setattr(client, "pipeline", pipeline)


def test_concurrent_insert_cannot_overwrite_the_other_writer(redis_adapter, monkeypatch):
    adapter, client = redis_adapter
    race_before_first_commit(monkeypatch, client, "another writer")
    with pytest.raises(ValueError, match="changed|exists|concurrent"):
        adapter.insert_row("record", {"key": "record", "value": "our value"})
    assert client.get("record") == "another writer"
    assert client.keys("*__baseskiff*") == []


def test_concurrent_update_cannot_overwrite_the_other_writer(redis_adapter, monkeypatch):
    adapter, client = redis_adapter
    client.set("record", "original")
    race_before_first_commit(monkeypatch, client, "another writer")
    with pytest.raises(ValueError, match="changed|concurrent"):
        adapter.update_row("record", {"key": "record"}, {"value": "our value"})
    assert client.get("record") == "another writer"
    assert client.keys("*__baseskiff*") == []
