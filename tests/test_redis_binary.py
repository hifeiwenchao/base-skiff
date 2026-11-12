"""Binary Redis payloads retain their bytes across reads and optimistic writes."""
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace

import fakeredis
import pytest
import redis

from baseskiff.adapters import MAX_REDIS_KEYS, RedisAdapter
from baseskiff.models import Connection

BINARY = b'\xde\x00\x01\xa4name\xa5hello'
FIELD = b'field:\xff'
MEMBER = b'member:\xfe'


@pytest.fixture
def binary_redis(monkeypatch):
    client = fakeredis.FakeRedis(decode_responses=False)
    adapter = RedisAdapter(Connection('Binary Redis', kind='Redis'))

    @contextmanager
    def session():
        yield client

    monkeypatch.setattr(adapter, 'session', session)
    yield adapter, client
    client.close()


@pytest.mark.parametrize('kind', ['string', 'hash', 'list', 'set', 'zset', 'stream'])
def test_binary_payloads_are_lossless_in_every_type(binary_redis, kind):
    adapter, client = binary_redis
    key = b'typed:\xff:' + kind.encode()
    if kind == 'string':
        client.set(key, BINARY)
        expected = BINARY
    elif kind == 'hash':
        client.hset(key, mapping={FIELD: BINARY, 'text': '中文'})
        expected = {FIELD: BINARY, 'text': '中文'}
    elif kind == 'list':
        client.rpush(key, BINARY, '中文')
        expected = [BINARY, '中文']
    elif kind == 'set':
        client.sadd(key, MEMBER)
        expected = [MEMBER]
    elif kind == 'zset':
        client.zadd(key, {MEMBER: 1.5})
        expected = {MEMBER: 1.5}
    else:
        stream_id = client.xadd(key, {FIELD: BINARY, 'text': '中文'})
        expected = [(stream_id.decode(), {FIELD: BINARY, 'text': '中文'})]
    client.pexpire(key, 120000)
    before = client.dump(key)
    snapshot = adapter.read_key(key)
    assert snapshot.key == key and snapshot.kind == kind
    assert snapshot.value == expected
    assert 0 < snapshot.ttl_ms <= 120000
    assert not snapshot.truncated
    assert client.dump(key) == before
    assert adapter.get_schema(key)[0].primary_key == (kind != 'stream')
    assert adapter.fetch_rows(key).rows[0]['value'] == expected


def test_search_returns_mixed_binary_keys_with_original_identity(binary_redis):
    adapter, client = binary_redis
    client.mset({b'text:one': 'text', b'text:\xff': BINARY, b'text:\x00': BINARY})
    page = adapter.search_keys('text:')
    assert [key.name for key in page.keys] == ['text:\x00', 'text:one', b'text:\xff']
    assert all(key.kind == 'string' for key in page.keys)
    assert adapter.search_keys(b'text:\xff', 'exact').keys[0].name == b'text:\xff'
    assert adapter.search_keys(b'text:one', 'exact').keys[0].name == 'text:one'
    with pytest.raises(ValueError, match='text'):
        adapter.search_keys(b'text:', 'prefix')


def test_binary_key_search_still_caps_distinct_matches(binary_redis):
    adapter, client = binary_redis
    client.mset({b'key:\xff:' + str(index).encode(): BINARY for index in range(MAX_REDIS_KEYS + 1)})
    page = adapter.search_keys()
    assert len(page.keys) == MAX_REDIS_KEYS and page.truncated
    assert all(isinstance(key.name, bytes) for key in page.keys)


@pytest.mark.parametrize(('kind', 'seed', 'payload', 'verify'), [
    ('string', lambda c, k: c.set(k, BINARY), {'expected': BINARY, 'value': b'\xffnew'},
     lambda c, k: c.get(k) == b'\xffnew'),
    ('hash', lambda c, k: c.hset(k, FIELD, BINARY), {'entry': FIELD, 'expected': BINARY, 'value': b'\xffnew'},
     lambda c, k: c.hget(k, FIELD) == b'\xffnew'),
    ('list', lambda c, k: c.rpush(k, BINARY), {'entry': 0, 'expected': BINARY, 'value': b'\xffnew'},
     lambda c, k: c.lindex(k, 0) == b'\xffnew'),
    ('set', lambda c, k: c.sadd(k, MEMBER), {'entry': MEMBER, 'value': b'\xffnew'},
     lambda c, k: c.smembers(k) == {b'\xffnew'}),
    ('zset', lambda c, k: c.zadd(k, {MEMBER: 1.5}), {'entry': MEMBER, 'expected': 1.5, 'value': 2},
     lambda c, k: c.zscore(k, MEMBER) == 2),
])
def test_binary_entry_updates_preserve_expiry(binary_redis, kind, seed, payload, verify):
    adapter, client = binary_redis
    key = b'mutate:\xff'
    seed(client, key)
    client.pexpire(key, 120000)
    deadline = client.pexpiretime(key)
    assert adapter.mutate_entry(key, kind, 'update', payload).affected == 1
    assert verify(client, key)
    assert abs(client.pexpiretime(key) - deadline) <= 2


def test_text_expected_values_match_raw_responses_without_losing_bytes(binary_redis):
    adapter, client = binary_redis
    client.hset('record', mapping={'text': 'old', FIELD: BINARY})
    adapter.mutate_entry('record', 'hash', 'update', {'entry': 'text', 'expected': 'old', 'value': 'new'})
    assert client.hgetall('record') == {b'text': b'new', FIELD: BINARY}
    with pytest.raises(ValueError, match='changed'):
        adapter.mutate_entry('record', 'hash', 'update', {'entry': FIELD, 'expected': b'\xffwrong', 'value': 'bad'})
    assert client.hget('record', FIELD) == BINARY


@pytest.mark.parametrize('kind', ['hash', 'list', 'set', 'zset'])
def test_binary_entry_deletion_and_creation(binary_redis, kind):
    adapter, client = binary_redis
    key = b'delete:\xff'
    entry = 0 if kind == 'list' else FIELD
    payload = {'entry': entry, 'value': 1.5 if kind == 'zset' else BINARY}
    if kind == 'list':
        payload.pop('entry')
    adapter.mutate_entry(key, kind, 'add', payload)
    delete = {'entry': entry, 'expected': 1.5 if kind == 'zset' else BINARY}
    adapter.mutate_entry(key, kind, 'delete', delete)
    assert not client.exists(key)


def test_binary_query_results_normalize_nested_hash_scan_and_scores(binary_redis):
    adapter, client = binary_redis
    client.hset('hash', mapping={FIELD: BINARY, 'text': 'hello'})
    client.rpush('list', BINARY, 'hello')
    client.zadd('zset', {MEMBER: 2})
    assert adapter.execute('HGETALL hash').rows == [
        {'key': FIELD, 'value': BINARY}, {'key': 'text', 'value': 'hello'}]
    assert adapter.execute('LRANGE list 0 -1').rows == [{'value': BINARY}, {'value': 'hello'}]
    assert adapter.execute('HSCAN hash 0').rows[1]['value'] == {FIELD: BINARY, 'text': 'hello'}
    assert adapter.execute('ZRANGE zset 0 -1 WITHSCORES').rows == [{'value': [MEMBER, 2.0]}]


def test_binary_whole_key_replacement_retains_native_values(binary_redis):
    adapter, client = binary_redis
    key = b'whole:\xff'
    client.hset(key, mapping={FIELD: BINARY, 'text': 'hello'})
    client.pexpire(key, 120000)
    adapter.update_row(key, {'key': key}, {})
    assert client.hgetall(key) == {FIELD: BINARY, b'text': b'hello'}
    assert client.pttl(key) > 0
    adapter.insert_row('', {'key': b'new:\xfe', 'type': 'string', 'value': BINARY})
    assert client.get(b'new:\xfe') == BINARY


def test_unknown_types_fall_back_to_read_only_dump(binary_redis, monkeypatch):
    adapter, client = binary_redis
    client.set('module', BINARY)
    dumped = client.dump('module')
    monkeypatch.setattr(client, 'type', lambda key: b'vector-module')
    snapshot = adapter.read_key('module')
    assert snapshot.kind == 'vector-module' and snapshot.value == dumped
    assert snapshot.size == len(dumped) and not snapshot.truncated
    assert not adapter.get_schema('module')[0].primary_key


def test_unknown_type_cannot_be_replaced_by_the_generic_grid(binary_redis, monkeypatch):
    adapter, client = binary_redis
    client.set('module', BINARY)
    pipeline = client.pipeline

    def module_pipeline(*args, **kwargs):
        transaction = pipeline(*args, **kwargs)
        monkeypatch.setattr(transaction, 'type', lambda key: b'vector-module')
        return transaction

    monkeypatch.setattr(client, 'pipeline', module_pipeline)
    with pytest.raises(ValueError, match='read-only'):
        adapter.update_row('module', {'key': 'module'}, {'type': 'string', 'value': 'replacement'})
    assert client.get('module') == BINARY


def test_json_module_returns_json_source_without_serializing_it(binary_redis, monkeypatch):
    adapter, client = binary_redis
    client.set('module', '{}')
    monkeypatch.setattr(client, 'type', lambda key: b'ReJSON-RL')
    original_execute = client.execute_command

    def execute(*args, **kwargs):
        if args == ('JSON.GET', 'module'):
            return '{"name":"中文"}'.encode()
        return original_execute(*args, **kwargs)

    monkeypatch.setattr(client, 'execute_command', execute)
    snapshot = adapter.read_key('module')
    assert snapshot.kind == 'ReJSON-RL'
    assert snapshot.value == '{"name":"中文"}'
    assert not adapter.get_schema('module')[0].primary_key


def test_binary_mutations_still_obey_read_only(binary_redis):
    adapter, client = binary_redis
    adapter.connection = replace(adapter.connection, read_only=True)
    client.set(b'key:\xff', BINARY)
    with pytest.raises(PermissionError, match='read-only'):
        adapter.mutate_entry(b'key:\xff', 'string', 'update', {'value': 'bad', 'expected': BINARY})
    assert client.get(b'key:\xff') == BINARY


def test_session_does_not_decode_binary_responses(monkeypatch):
    module = SimpleNamespace(Redis=fakeredis.FakeRedis, RedisError=redis.RedisError)
    monkeypatch.setattr('baseskiff.adapters.driver', lambda name: module)
    adapter = RedisAdapter(Connection('Raw transport', kind='Redis'))
    with adapter.session() as client:
        client.set('record', BINARY)
        assert client.get('record') == BINARY
        assert client.type('record') == b'string'


def test_binary_conflict_watch_rejects_a_concurrent_write(binary_redis, monkeypatch):
    adapter, client = binary_redis
    key = b'watch:\xff'
    client.hset(key, FIELD, BINARY)
    pipeline = client.pipeline
    changed = False

    def racing_pipeline(*args, **kwargs):
        transaction = pipeline(*args, **kwargs)
        execute = transaction.execute

        def raced_execute(*values, **options):
            nonlocal changed
            if transaction.explicit_transaction and not changed:
                changed = True
                client.hset(key, FIELD, b'\xffconcurrent')
            return execute(*values, **options)

        monkeypatch.setattr(transaction, 'execute', raced_execute)
        return transaction

    monkeypatch.setattr(client, 'pipeline', racing_pipeline)
    with pytest.raises(ValueError, match='concurrently'):
        adapter.mutate_entry(key, 'hash', 'update', {'entry': FIELD, 'expected': BINARY, 'value': 'bad'})
    assert client.hget(key, FIELD) == b'\xffconcurrent'
