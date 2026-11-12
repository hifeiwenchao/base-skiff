"""Lossless arbitrary Redis bytes over direct connections and real SSH forwards."""
from contextlib import nullcontext

import pytest
from redis import Redis

from baseskiff.models import Connection
from tests.ssh_test_server import TestSSHServer

pytestmark = pytest.mark.integration

BINARY = b'\xde\x00\x01\xa4name\xa5hello'
FIELD = b'field:\xff'
MEMBER = b'member:\xfe'


@pytest.mark.parametrize('route', ['direct', 'ssh'])
def test_binary_payloads_and_identities_round_trip_through_service(
        live_config, live_service, run_tag, route):
    config = live_config['redis']
    raw = Redis(host=live_config['host'], port=config['port'],
                username=config.get('username') or None, password=config.get('password') or None,
                protocol=2, decode_responses=False)
    prefix = run_tag + ':binary:' + route + ':'
    kinds = ('string', 'hash', 'list', 'set', 'zset', 'stream')
    keys = {kind: prefix.encode() + b'\xff:' + kind.encode() for kind in kinds}
    query_key = prefix + 'query'
    try:
        raw.set(keys['string'], BINARY)
        raw.hset(keys['hash'], mapping={FIELD: BINARY, 'text': '{"enabled":true}'})
        raw.rpush(keys['list'], BINARY, 'keep')
        raw.sadd(keys['set'], MEMBER, 'keep')
        raw.zadd(keys['zset'], {MEMBER: 1.5, 'keep': 4})
        stream_id = raw.xadd(keys['stream'], {FIELD: BINARY, 'text': '中文'})
        raw.hset(query_key, mapping={FIELD: BINARY, 'text': '中文'})
        for key in keys.values():
            raw.pexpire(key, 120000)
        expected = {
            'string': BINARY,
            'hash': {FIELD: BINARY, 'text': '{"enabled":true}'},
            'list': [BINARY, 'keep'],
            'set': {MEMBER, 'keep'},
            'zset': {MEMBER: 1.5, 'keep': 4.0},
            'stream': [(stream_id.decode(), {FIELD: BINARY, 'text': '中文'})],
        }
        with TestSSHServer() if route == 'ssh' else nullcontext() as ssh:
            options = dict(name='Redis binary ' + route, kind='Redis', database='0',
                           username=config.get('username', ''), password=config.get('password', ''))
            connection = (ssh.connection(live_config['host'], config['port'], **options) if ssh else
                          Connection(host=live_config['host'], port=config['port'], **options))
            saved = live_service.save_connection(connection)
            found = live_service.search_redis_keys(saved.id, prefix).keys
            assert {item.name for item in found} == {*keys.values(), query_key}
            for kind, key in keys.items():
                before = raw.dump(key)
                snapshot = live_service.read_redis_key(saved.id, key)
                actual = set(snapshot.value) if kind == 'set' else snapshot.value
                assert snapshot.key == key and snapshot.kind == kind
                assert actual == expected[kind]
                assert snapshot.ttl_ms > 0 and not snapshot.truncated
                assert raw.dump(key) == before
                assert live_service.search_redis_keys(saved.id, key, 'exact').keys[0].name == key
            query = live_service.execute(saved.id, 'HGETALL ' + query_key)
            assert {row['key']: row['value'] for row in query.rows} == {FIELD: BINARY, 'text': '中文'}
            replacement = b'\xff\x00updated'
            for kind, payload in [
                ('string', {'expected': BINARY, 'value': replacement}),
                ('hash', {'entry': FIELD, 'expected': BINARY, 'value': replacement}),
                ('list', {'entry': 0, 'expected': BINARY, 'value': replacement}),
                ('set', {'entry': MEMBER, 'value': replacement}),
                ('zset', {'entry': MEMBER, 'expected': 1.5, 'value': 2.5}),
            ]:
                previous_ttl = raw.pttl(keys[kind])
                assert live_service.mutate_redis_entry(saved.id, keys[kind], kind, 'update', payload).affected == 1
                assert 0 < raw.pttl(keys[kind]) <= previous_ttl
            assert raw.get(keys['string']) == replacement
            assert raw.hgetall(keys['hash']) == {FIELD: replacement, b'text': b'{"enabled":true}'}
            assert raw.lrange(keys['list'], 0, -1) == [replacement, b'keep']
            assert raw.smembers(keys['set']) == {replacement, b'keep'}
            assert raw.zrange(keys['zset'], 0, -1, withscores=True) == [(MEMBER, 2.5), (b'keep', 4.0)]
            with pytest.raises(ValueError, match='changed'):
                live_service.mutate_redis_entry(saved.id, keys['hash'], 'hash', 'update',
                                                {'entry': FIELD, 'expected': BINARY, 'value': 'bad'})
            assert raw.hget(keys['hash'], FIELD) == replacement
            if ssh:
                assert ssh.destinations and all(target == (live_config['host'], config['port'])
                                                for target in ssh.destinations)
    finally:
        raw.delete(*keys.values(), query_key)
        raw.close()
