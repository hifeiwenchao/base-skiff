"""All Redis collection entries remain accessible through bounded continuations."""
from contextlib import nullcontext

import pytest
from redis import Redis

from baseskiff.models import Connection
from tests.ssh_test_server import TestSSHServer

pytestmark = pytest.mark.integration


@pytest.mark.parametrize('route', ['direct', 'ssh'])
@pytest.mark.parametrize('kind', ['hash', 'list', 'set', 'zset'])
def test_collection_continuation_preserves_all_entries_and_bytes(
        live_config, live_service, run_tag, route, kind):
    config = live_config.get('redis')
    if not config:
        pytest.skip('The live configuration does not provide Redis')
    host, port = live_config['host'], config['port']
    raw = Redis(host=host, port=port, db=0, protocol=2, decode_responses=False,
                username=config.get('username') or None, password=config.get('password') or None)
    key = f'{run_tag}:continuation:{route}:{kind}:'.encode() + b'\xff'
    members = [f'entry{i:04}'.encode() for i in range(1204)] + [b'\xff\x00binary']
    binary_value = b'\xfe\x00payload'
    try:
        if kind == 'hash':
            raw.hset(key, mapping={member: binary_value for member in members})
        elif kind == 'list':
            raw.rpush(key, *members)
        elif kind == 'set':
            raw.sadd(key, *members)
        else:
            raw.zadd(key, {member: i + 0.25 for i, member in enumerate(members)})
        raw.pexpire(key, 120000)
        before = raw.dump(key)
        with TestSSHServer() if route == 'ssh' else nullcontext() as ssh:
            options = dict(name=f'Redis continuation {kind} {route}', kind='Redis', database='0',
                           username=config.get('username', ''), password=config.get('password', ''))
            candidate = (ssh.connection(host, port, **options) if ssh else
                         Connection(host=host, port=port, **options))
            saved = live_service.save_connection(candidate)
            snapshot = live_service.read_redis_key(saved.id, key, database='0')
            assert snapshot.key == key and snapshot.kind == kind
            assert snapshot.size == 1205
            assert len(snapshot.value) <= 1000
            assert snapshot.continuation is not None
            previous = snapshot
            for _ in range(30):
                snapshot = live_service.continue_redis_value(saved.id, snapshot, database='0')
                assert snapshot.key == key and snapshot.kind == kind
                assert len(snapshot.value) - len(previous.value) <= 1000
                assert len(previous.value) <= len(snapshot.value)
                previous = snapshot
                if snapshot.continuation is None:
                    break
            assert snapshot.continuation is None and not snapshot.truncated
            assert len(snapshot.value) == 1205
            decoded = [member.decode() if member != members[-1] else member for member in members]
            if kind == 'hash':
                assert snapshot.value == {member: binary_value for member in decoded}
            elif kind == 'list':
                assert snapshot.value == decoded
            elif kind == 'set':
                assert set(snapshot.value) == set(decoded)
            else:
                assert snapshot.value == {member: i + 0.25 for i, member in enumerate(decoded)}
            assert 0 < raw.pttl(key) <= 120000
            assert raw.dump(key) == before
            if ssh:
                assert ssh.destinations and all(target == (host, port) for target in ssh.destinations)
    finally:
        raw.delete(key)
        raw.close()
