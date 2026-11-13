"""Server-side key discovery and individual Redis entry mutations."""
import time
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace

import fakeredis
import pytest

from baseskiff import adapters
from baseskiff.adapters import MAX_REDIS_KEYS, MAX_ROWS, RedisAdapter, SSHAdapter
from baseskiff.models import Connection
from baseskiff.service import DatabaseService


@pytest.fixture
def redis_browser(monkeypatch):
    client = fakeredis.FakeRedis(decode_responses=True)
    adapter = RedisAdapter(Connection(name="Browser", kind="Redis"))

    @contextmanager
    def session():
        yield client

    monkeypatch.setattr(adapter, "session", session)
    yield adapter, client
    client.close()


@pytest.mark.parametrize(("mode", "text", "expected"), [
    ("prefix", "用户:", ["用户:一", "用户:两个", "用户:二"]),
    ("prefix", "a[1]*?", ["a[1]*?tail"]),
    ("prefix", " space ", [" space key "]),
    ("exact", "a[1]*?tail", ["a[1]*?tail"]),
    ("exact", "", [""]),
])
def test_search_modes_preserve_literal_prefix_and_exact_keys(redis_browser, mode, text, expected):
    adapter, client = redis_browser
    for key in ["用户:一", "用户:二", "用户:两个", "other:用户", "a[1]*?tail", "a1", "a2", " space key ", ""]:
        client.set(key, "value")
    result = adapter.search_keys(text, mode)
    assert [key.name for key in result.keys] == expected
    assert not result.truncated
    assert all(key.kind == "string" for key in result.keys)


def test_server_search_finds_a_key_beyond_initial_preview(redis_browser):
    adapter, client = redis_browser
    client.mset({f"noise:{index:04}": "ignored" for index in range(MAX_REDIS_KEYS + 20)})
    client.set("target:needle", "found")
    assert adapter.search_keys().truncated
    assert [key.name for key in adapter.search_keys("target:").keys] == ["target:needle"]
    assert [key.name for key in adapter.search_keys("target:needle", "exact").keys] == ["target:needle"]


def test_exact_lookup_never_scans_and_missing_returns_empty(redis_browser, monkeypatch):
    adapter, client = redis_browser
    client.set("literal*?[]", "value")

    def forbidden(*args, **kwargs):
        raise AssertionError("Exact lookup must not scan the keyspace")

    monkeypatch.setattr(client, "scan", forbidden)
    assert [key.name for key in adapter.search_keys("literal*?[]", "exact").keys] == ["literal*?[]"]
    assert adapter.search_keys("missing", "exact").keys == []


def test_supported_key_search_modes_are_prefix_and_exact():
    assert adapters.REDIS_KEY_SEARCH_MODES == ("prefix", "exact")


def test_fuzzy_key_search_is_rejected_before_scanning(redis_browser, monkeypatch):
    adapter, client = redis_browser

    def forbidden(*_args, **_kwargs):
        raise AssertionError("An unsupported search mode must not scan Redis")

    monkeypatch.setattr(client, "scan", forbidden)
    with pytest.raises(ValueError, match="prefix.*exact"):
        adapter.search_keys("customer", "fuzzy")


@pytest.mark.parametrize("text", ["a*", "a?", "a[12]", r"a\b", " 用户 "])
def test_prefix_metacharacters_and_spaces_are_always_literal(redis_browser, text):
    adapter, client = redis_browser
    client.mset({text + ":one": "match", text + ":two": "match",
                 "before:" + text: "not a prefix", "a1:other": "not literal"})

    page = adapter.search_keys(text, "prefix")

    assert {key.name for key in page.keys} == {text + ":one", text + ":two"}
    assert not page.truncated


def test_scan_deduplicates_before_limit_and_retains_remaining_key(redis_browser, monkeypatch):
    adapter, client = redis_browser
    client.mset({"one": "1", "two": "2", "three": "3"})

    def repeated_scan(cursor, *, match, count):
        assert cursor == 0
        assert match == "*"
        return 0, ["one", "one", "two", "two", "three"]

    monkeypatch.setattr(client, "scan", repeated_scan)
    result = adapter.search_keys(limit=2)
    assert [key.name for key in result.keys] == ["one", "two"]
    assert result.truncated
    remaining = adapter.search_keys(limit=2, continuation=result.continuation)
    assert [key.name for key in remaining.keys] == ["three"]
    assert not remaining.truncated
    assert not adapter.search_keys(limit=3).truncated


@pytest.mark.parametrize("kind", ["hash", "list", "set", "zset"])
def test_typed_preview_reports_size_ttl_and_truncation(redis_browser, kind):
    adapter, client = redis_browser
    values = [str(index) for index in range(MAX_ROWS + 5)]
    if kind == "hash":
        client.hset("record", mapping=dict.fromkeys(values, "value"))
    elif kind == "list":
        client.rpush("record", *values)
    elif kind == "set":
        client.sadd("record", *values)
    else:
        client.zadd("record", dict.fromkeys(values, 2))
    client.pexpire("record", 60_000)
    result = adapter.read_key("record")
    assert (result.key, result.kind, result.size, result.truncated) == ("record", kind, 1005, True)
    assert len(result.value) == MAX_ROWS
    assert 59_000 <= result.ttl_ms <= 60_000


def test_string_and_missing_preview(redis_browser):
    adapter, client = redis_browser
    client.set("空格 ", "你好")
    value = adapter.read_key("空格 ")
    assert (value.value, value.size, value.ttl_ms, value.truncated) == ("你好", 6, -1, False)
    missing = adapter.read_key("missing")
    assert (missing.kind, missing.value, missing.size, missing.ttl_ms) == ("none", None, 0, -2)


def test_insert_typed_key_applies_value_and_ttl_atomically(redis_browser):
    adapter, client = redis_browser

    assert adapter.insert_row("", {"key": "profile", "type": "hash",
                                    "value": {"name": "Ada"}, "ttl": 60}).affected == 1
    assert client.hgetall("profile") == {"name": "Ada"}
    assert 59_000 <= client.pttl("profile") <= 60_000


def test_set_and_clear_key_ttl_preserves_value_and_validates_expected_ttl(redis_browser):
    adapter, client = redis_browser
    client.set("record", "value")
    assert adapter.set_key_ttl("record", 90).affected == 1
    assert 89_000 <= client.pttl("record") <= 90_000
    assert client.get("record") == "value"
    with pytest.raises(ValueError, match="changed|expired"):
        adapter.set_key_ttl("record", 90, expected_ttl_ms=-1)
    assert adapter.set_key_ttl("record", None).affected == 1
    assert client.pttl("record") == -1


def test_set_key_ttl_blocks_read_only_and_missing_keys(redis_browser):
    adapter, client = redis_browser
    adapter.connection = replace(adapter.connection, read_only=True)
    with pytest.raises(PermissionError):
        adapter.set_key_ttl("missing", 10)
    client.set("record", "value")
    with pytest.raises(PermissionError):
        adapter.set_key_ttl("record", 10)


def test_large_hash_single_field_changes_preserve_unrelated_fields_and_ttl(redis_browser):
    adapter, client = redis_browser
    client.hset("record", mapping={f"field:{i}": str(i) for i in range(MAX_ROWS + 10)})
    client.pexpire("record", 60_000)
    assert adapter.mutate_entry("record", "hash", "update", {"entry": "field:1009", "expected": "1009", "value": ""}).affected == 1
    assert client.hget("record", "field:1009") == ""
    adapter.mutate_entry("record", "hash", "add", {"entry": "新字段", "value": "new"})
    adapter.mutate_entry("record", "hash", "delete", {"entry": "field:0", "expected": "0"})
    assert client.hlen("record") == MAX_ROWS + 10
    assert client.hget("record", "field:1") == "1"
    assert client.hget("record", "新字段") == "new"
    assert not client.hexists("record", "field:0")
    assert 59_000 <= client.pttl("record") <= 60_000


@pytest.mark.parametrize(("kind", "seed", "action", "payload", "want"), [
    ("string", "before", "update", {"expected": "before", "value": ""}, ""),
    ("hash", {"": "old", "other": "stay"}, "update", {"entry": "", "expected": "old", "value": ""}, {"": "", "other": "stay"}),
    ("list", ["duplicate", "old", "duplicate"], "update", {"entry": 1, "expected": "old", "value": ""}, ["duplicate", "", "duplicate"]),
    ("list", ["duplicate", "old", "duplicate"], "delete", {"entry": 1, "expected": "old"}, ["duplicate", "duplicate"]),
    ("list", ["old"], "add", {"value": ""}, ["old", ""]),
    ("set", ["old", "stay"], "update", {"entry": "old", "value": ""}, ["", "stay"]),
    ("set", ["old", "stay"], "delete", {"entry": "old"}, ["stay"]),
    ("zset", {"old": 2, "stay": 3}, "update", {"entry": "old", "expected": "2", "value": "-1.5"}, {"old": -1.5, "stay": 3.0}),
    ("zset", {"old": 2, "stay": 3}, "delete", {"entry": "old", "expected": 2}, {"stay": 3.0}),
])
def test_individual_mutations_preserve_other_entries_and_ttl(redis_browser, kind, seed, action, payload, want):
    adapter, client = redis_browser
    if kind == "string":
        client.set("record", seed)
    elif kind == "hash":
        client.hset("record", mapping=seed)
    elif kind == "list":
        client.rpush("record", *seed)
    elif kind == "set":
        client.sadd("record", *seed)
    else:
        client.zadd("record", seed)
    client.pexpire("record", 60_000)
    adapter.mutate_entry("record", kind, action, payload)
    actual = adapter.read_key("record").value
    assert sorted(actual) == want if kind == "set" else actual == want
    assert 59_000 <= client.pttl("record") <= 60_000


@pytest.mark.parametrize("kind", ["string", "hash", "list", "set", "zset"])
def test_entry_add_can_create_missing_key(redis_browser, kind):
    adapter, client = redis_browser
    payload = {"entry": "", "value": "0" if kind == "zset" else ""}
    adapter.mutate_entry("", kind, "add", payload)
    assert client.type("") == kind


@pytest.mark.parametrize("payload", [{"entry": "member"}, {"value": "member"}])
def test_set_add_accepts_member_from_entry_or_value(redis_browser, payload):
    adapter, client = redis_browser
    adapter.mutate_entry("record", "set", "add", payload)
    assert client.smembers("record") == {"member"}


def test_replacing_last_set_member_preserves_ttl(redis_browser):
    adapter, client = redis_browser
    client.sadd("record", "only")
    client.pexpire("record", 60_000)
    adapter.mutate_entry("record", "set", "update", {"entry": "only", "value": "new"})
    assert client.smembers("record") == {"new"}
    assert 59_000 <= client.pttl("record") <= 60_000


@pytest.mark.parametrize("kind,same_member", [("string", False), ("set", False), ("set", True)])
def test_network_delay_before_commit_does_not_extend_expiry(redis_browser, monkeypatch, kind, same_member):
    adapter, client = redis_browser
    if kind == "string":
        client.set("record", "old")
        payload = {"expected": "old", "value": "new"}
    else:
        client.sadd("record", "old")
        payload = {"entry": "old", "value": "old" if same_member else "new"}
    client.pexpire("record", 5_000)
    deadline = time.monotonic() + client.pttl("record") / 1000
    original_pipeline = client.pipeline

    def delayed_pipeline(*args, **kwargs):
        transaction = original_pipeline(*args, **kwargs)
        execute = transaction.execute

        def delayed_execute(*args, **kwargs):
            # Only delay the final mutation, as a real network could, rather
            # than any optional nontransaction batch used to sample expiry.
            if transaction.explicit_transaction:
                time.sleep(0.3)
            return execute(*args, **kwargs)

        transaction.execute = delayed_execute
        return transaction

    monkeypatch.setattr(client, "pipeline", delayed_pipeline)
    adapter.mutate_entry("record", kind, "update", payload)
    expected_remaining = (deadline - time.monotonic()) * 1000
    assert abs(client.pttl("record") - expected_remaining) < 40


@pytest.mark.parametrize("score", [float("inf"), float("-inf")])
@pytest.mark.parametrize("action", ["update", "delete"])
def test_existing_infinite_sorted_set_score_can_be_checked_and_changed(redis_browser, score, action):
    adapter, client = redis_browser
    client.zadd("record", {"member": score, "other": 3})
    payload = {"entry": "member", "expected": score, "value": "2.5"}
    adapter.mutate_entry("record", "zset", action, payload)
    assert client.zscore("record", "member") == (2.5 if action == "update" else None)
    assert client.zscore("record", "other") == 3


@pytest.mark.parametrize("kind", ["hash", "list", "set", "zset"])
def test_expiration_before_exec_cannot_recreate_a_persistent_collection(redis_browser, monkeypatch, kind):
    adapter, client = redis_browser
    if kind == "hash":
        client.hset("record", "old", "value")
    elif kind == "list":
        client.rpush("record", "old")
    elif kind == "set":
        client.sadd("record", "old")
    else:
        client.zadd("record", {"old": 1})
    client.pexpire("record", 150)
    original_pipeline = client.pipeline

    def delayed_pipeline(*args, **kwargs):
        transaction = original_pipeline(*args, **kwargs)
        execute = transaction.execute

        def delayed_execute(*args, **kwargs):
            if transaction.explicit_transaction:
                time.sleep(0.25)
            return execute(*args, **kwargs)

        transaction.execute = delayed_execute
        return transaction

    monkeypatch.setattr(client, "pipeline", delayed_pipeline)
    # Natural expiration did not invalidate WATCH before Redis 6.0.9. This
    # in-process engine has the same behavior, so the final write still runs.
    adapter.mutate_entry("record", kind, "add", {"entry": "new", "value": "2"})
    assert not client.exists("record")


@pytest.mark.parametrize(("kind", "payload"), [
    ("string", {"expected": "old"}),
    ("hash", {"entry": "field", "expected": "old"}),
    ("list", {"entry": 0, "expected": "old"}),
    ("set", {"entry": "old"}),
    ("zset", {"entry": "member", "expected": 2}),
])
def test_delete_last_entry_removes_the_key(redis_browser, kind, payload):
    adapter, client = redis_browser
    if kind == "string":
        client.set("record", "old")
    elif kind == "hash":
        client.hset("record", "field", "old")
    elif kind == "list":
        client.rpush("record", "old")
    elif kind == "set":
        client.sadd("record", "old")
    else:
        client.zadd("record", {"member": 2})
    adapter.mutate_entry("record", kind, "delete", payload)
    assert not client.exists("record")


@pytest.mark.parametrize(("kind", "payload"), [
    ("hash", {"entry": "field", "expected": "stale", "value": "new"}),
    ("hash", {"entry": "missing", "expected": "", "value": "new"}),
    ("list", {"entry": 0, "expected": "stale", "value": "new"}),
    ("string", {"expected": "stale", "value": "new"}),
    ("set", {"entry": "missing", "value": "new"}),
    ("zset", {"entry": "member", "expected": 4, "value": 8}),
])
def test_stale_or_missing_entry_cannot_be_overwritten(redis_browser, kind, payload):
    adapter, client = redis_browser
    creators = {"string": lambda: client.set("record", "old"),
                "hash": lambda: client.hset("record", "field", "old"),
                "list": lambda: client.rpush("record", "old"),
                "set": lambda: client.sadd("record", "old"),
                "zset": lambda: client.zadd("record", {"member": 2})}
    creators[kind]()
    before = client.dump("record")
    with pytest.raises(ValueError, match="changed|missing|exist|Refresh|refresh"):
        adapter.mutate_entry("record", kind, "update", payload)
    assert client.dump("record") == before


@pytest.mark.parametrize(("kind", "action", "payload"), [
    ("hash", "add", {"entry": "field", "value": "new"}),
    ("hash", "update", {"entry": "field", "value": "unchecked"}),
    ("list", "update", {"entry": -1, "expected": "old", "value": "new"}),
    ("list", "update", {"entry": "0", "expected": "old", "value": "new"}),
    ("zset", "add", {"entry": "new", "value": "nan"}),
    ("zset", "add", {"entry": "new", "value": "inf"}),
    ("hash", "add", {"entry": "new", "value": {"nested": "object"}}),
])
def test_invalid_entries_never_modify_key(redis_browser, kind, action, payload):
    adapter, client = redis_browser
    if kind == "hash":
        client.hset("record", "field", "old")
    elif kind == "list":
        client.rpush("record", "old")
    else:
        client.zadd("record", {"member": 2})
    before = client.dump("record")
    with pytest.raises(ValueError):
        adapter.mutate_entry("record", kind, action, payload)
    assert client.dump("record") == before


@pytest.mark.parametrize("kind", ["set", "zset"])
def test_large_membership_collections_allow_single_entry_edits(redis_browser, kind):
    adapter, client = redis_browser
    if kind == "set":
        client.sadd("record", *[str(i) for i in range(MAX_ROWS + 5)])
        payload = {"entry": "1004", "value": "new"}
    else:
        client.zadd("record", {str(i): i for i in range(MAX_ROWS + 5)})
        payload = {"entry": "1004", "value": "4.5", "expected": 1004}
    adapter.mutate_entry("record", kind, "update", payload)
    assert client.sismember("record", "new") if kind == "set" else client.zscore("record", "1004") == 4.5


def test_large_list_delete_is_rejected_but_single_index_update_is_allowed(redis_browser):
    adapter, client = redis_browser
    client.rpush("record", *[str(i) for i in range(MAX_ROWS + 5)])
    with pytest.raises(ValueError, match="1000|truncat"):
        adapter.mutate_entry("record", "list", "delete", {"entry": 0, "expected": "0"})
    adapter.mutate_entry("record", "list", "update", {"entry": 1004, "expected": "1004", "value": "new"})
    assert client.lindex("record", 1004) == "new"
    assert client.llen("record") == 1005


@pytest.mark.parametrize("race", ["change", "expire", "type"])
def test_concurrent_changes_are_rejected_without_recreating_or_overwriting(redis_browser, monkeypatch, race):
    adapter, client = redis_browser
    client.hset("record", "field", "old")
    original_pipeline = client.pipeline

    def pipeline(*args, **kwargs):
        transaction = original_pipeline(*args, **kwargs)
        execute = transaction.execute

        def compete(*args, **kwargs):
            if race == "change":
                client.hset("record", "other", "concurrent")
            else:
                client.delete("record")
                if race == "type":
                    client.set("record", "new type")
            return execute(*args, **kwargs)

        transaction.execute = compete
        return transaction

    monkeypatch.setattr(client, "pipeline", pipeline)
    with pytest.raises(ValueError, match="concurrent|changed|expired"):
        adapter.mutate_entry("record", "hash", "update", {"entry": "field", "expected": "old", "value": "ours"})
    if race == "change":
        assert client.hgetall("record") == {"field": "old", "other": "concurrent"}
    elif race == "expire":
        assert not client.exists("record")
    else:
        assert client.get("record") == "new type"


def test_read_only_blocks_mutations_without_touching_data(redis_browser):
    adapter, client = redis_browser
    client.hset("record", "field", "old")
    adapter.connection = replace(adapter.connection, read_only=True)
    assert adapter.read_key("record").kind == "hash"
    with pytest.raises(PermissionError):
        adapter.mutate_entry("record", "hash", "update", {"entry": "field", "expected": "old", "value": "new"})
    assert client.hget("record", "field") == "old"


def test_service_records_successful_and_failed_entry_changes(redis_browser, monkeypatch, tmp_path):
    adapter, client = redis_browser
    service = DatabaseService(tmp_path)
    connection = service.save_connection(adapter.connection)
    monkeypatch.setattr("baseskiff.service.adapter_for", lambda actual: adapter)
    client.hset("record", "field", "old")
    assert service.search_redis_keys(connection.id, "record", "exact").keys[0].name == "record"
    assert service.read_redis_key(connection.id, "record").value == {"field": "old"}
    service.mutate_redis_entry(connection.id, "record", "hash", "update", {"entry": "field", "expected": "old", "value": "new"})
    with pytest.raises(ValueError):
        service.mutate_redis_entry(connection.id, "record", "hash", "delete", {"entry": "field", "expected": "old"})
    history = service.list_history()
    assert len(history) == 2
    assert not history[0].success and history[1].success
    assert all("record" in row.query and "hash" in row.query and "field" in row.query for row in history)
    assert history[1].affected == 1


def test_service_records_ttl_changes_in_history(redis_browser, monkeypatch, tmp_path):
    adapter, client = redis_browser
    service = DatabaseService(tmp_path)
    connection = service.save_connection(adapter.connection)
    monkeypatch.setattr("baseskiff.service.adapter_for", lambda actual: adapter)
    client.set("record", "old")
    service.set_redis_ttl(connection.id, "record", 42)
    assert 41_000 <= client.pttl("record") <= 42_000
    service.set_redis_ttl(connection.id, "record", None)
    assert client.pttl("record") == -1
    history = service.list_history()
    assert len(history) == 2
    assert all(item.success and 'TTL' in item.query for item in history)


def test_ssh_redis_browser_operations_use_one_tunnel(redis_browser, monkeypatch):
    direct, client = redis_browser
    events = []

    @contextmanager
    def endpoint(connection):
        events.append("open")
        try:
            yield SimpleNamespace(host="localhost", port=12345)
        finally:
            events.append("close")

    monkeypatch.setattr("baseskiff.ssh.ssh_endpoint", endpoint)
    monkeypatch.setattr(adapters, "_direct_adapter", lambda *args, **kwargs: direct)
    ssh = SSHAdapter(replace(direct.connection, ssh_enabled=True))
    client.set("record", "old")
    assert ssh.search_keys("record", "exact").keys[0].name == "record"
    assert ssh.read_key("record").value == "old"
    ssh.mutate_entry("record", "string", "update", {"expected": "old", "value": "new"})
    assert client.get("record") == "new"
    assert events == ["open", "close"] * 3


@pytest.fixture
def redis_raw_browser(monkeypatch):
    client = fakeredis.FakeRedis(decode_responses=False)
    adapter = RedisAdapter(Connection(name="Raw browser", kind="Redis"))

    @contextmanager
    def session():
        yield client

    monkeypatch.setattr(adapter, "session", session)
    yield adapter, client
    client.close()


@pytest.mark.parametrize("kind,seed", [
    ("string", b"\x81\xa1x\x01"),
    ("hash", {b"field": b"\x81\xa1x\x01"}),
    ("list", [b"\x81\xa1x\x01"]),
    ("set", [b"\x81\xa1x\x01"]),
])
def test_serialized_entry_mutation_accepts_explicit_raw_bytes(redis_raw_browser, kind, seed):
    adapter, client = redis_raw_browser
    key = b"serialized"
    replacement = b"\x81\xa1x\x02"
    if kind == "string":
        client.set(key, seed)
        payload = {"expected_bytes": seed, "value_bytes": replacement}
    elif kind == "hash":
        client.hset(key, mapping=seed)
        payload = {"entry": b"field", "expected_bytes": seed[b"field"], "value_bytes": replacement}
    elif kind == "list":
        client.rpush(key, *seed)
        payload = {"entry": 0, "expected_bytes": seed[0], "value_bytes": replacement}
    else:
        client.sadd(key, *seed)
        payload = {"entry": seed[0], "value_bytes": replacement}
    client.pexpire(key, 60_000)

    assert adapter.mutate_entry(key, kind, "update", payload).affected == 1
    if kind == "string":
        assert client.get(key) == replacement
    elif kind == "hash":
        assert client.hget(key, b"field") == replacement
    elif kind == "list":
        assert client.lindex(key, 0) == replacement
    else:
        assert client.smembers(key) == {replacement}
    assert 59_000 <= client.pttl(key) <= 60_000


def test_serialized_entry_mutation_rejects_stale_raw_bytes(redis_raw_browser):
    adapter, client = redis_raw_browser
    key = b"serialized"
    client.set(key, b"\x81\xa1x\x01")

    with pytest.raises(ValueError, match="changed"):
        adapter.mutate_entry(key, "string", "update", {
            "expected_bytes": b"\x81\xa1x\x00", "value_bytes": b"\x81\xa1x\x02",
        })
    assert client.get(key) == b"\x81\xa1x\x01"
