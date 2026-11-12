"""Resumable Redis key discovery keeps every loaded key across bounded batches."""
from contextlib import contextmanager
from dataclasses import replace

import fakeredis
import pytest

from baseskiff.adapters import RedisAdapter, SSHAdapter
from baseskiff.models import Connection
from baseskiff.service import DatabaseService


class ScanClient:
    def __init__(self, pages):
        self.pages = pages
        self.scans = []
        self.typed = []
        self.fail_type = False

    def scan(self, cursor=0, *, match=None, count=None):
        self.scans.append((cursor, match, count))
        return self.pages[cursor]

    @contextmanager
    def pipeline(self, *, transaction):
        assert transaction is False
        selected = []
        client = self

        class Pipeline:
            def type(self, key):
                selected.append(key)

            def execute(self):
                client.typed.append(tuple(selected))
                if client.fail_type:
                    raise RuntimeError('isolated type lookup failed')
                return [b'string'] * len(selected)

        yield Pipeline()


def adapter_for_client(monkeypatch, client, *, database='0'):
    adapter = RedisAdapter(Connection('Resumable keys', kind='Redis', database=database))
    sessions = []

    @contextmanager
    def session():
        sessions.append('open')
        try:
            yield client
        finally:
            sessions.append('close')

    monkeypatch.setattr(adapter, 'session', session)
    return adapter, sessions


def test_oversized_scan_response_is_buffered_and_typed_in_batches(monkeypatch):
    keys = [f'item:{index:04}'.encode() for index in range(1001)]
    client = ScanClient({0: (0, keys)})
    adapter, sessions = adapter_for_client(monkeypatch, client)
    first = adapter.search_keys('item:')
    assert len(first.keys) == 500
    assert first.continuation is not None, 'The rest of an oversized SCAN reply must remain resumable'
    second = adapter.search_keys('item:', continuation=first.continuation)
    third = adapter.search_keys('item:', continuation=second.continuation)
    assert [len(page.keys) for page in (first, second, third)] == [500, 500, 1]
    assert [page.truncated for page in (first, second, third)] == [True, True, False]
    assert third.continuation is None
    names = [item.name for page in (first, second, third) for item in page.keys]
    assert names == [f'item:{index:04}' for index in range(1001)]
    assert client.scans == [(0, 'item:*', 500)]
    assert [len(batch) for batch in client.typed] == [500, 500, 1]
    assert sessions == ['open', 'close'] * 3


@pytest.mark.parametrize('size', [500, 501, 1500])
def test_fake_redis_batches_eventually_return_all_keys(monkeypatch, size):
    with fakeredis.FakeRedis(decode_responses=False) as client:
        client.mset({f'item:{index:04}': 'value' for index in range(size)})
        adapter, _sessions = adapter_for_client(monkeypatch, client)
        page = adapter.search_keys('item:')
        loaded = [item.name for item in page.keys]
        batch_sizes = [len(page.keys)]
        for _ in range(10):
            if not page.truncated:
                break
            page = adapter.search_keys('item:', continuation=page.continuation)
            loaded.extend(item.name for item in page.keys)
            batch_sizes.append(len(page.keys))
        assert not page.truncated
        assert page.continuation is None
        assert len(loaded) == len(set(loaded)) == size
        assert set(loaded) == {f'item:{index:04}' for index in range(size)}
        assert all(amount <= 500 for amount in batch_sizes)


def test_duplicates_across_scan_calls_and_binary_names_keep_original_identity(monkeypatch):
    first = [f'item:{index:04}'.encode() for index in range(500)]
    second = first + [f'item:{index:04}'.encode() for index in range(500, 800)] + [b'item:\xff'] * 2
    client = ScanClient({0: (17, first + first[:100]), 17: (0, second)})
    adapter, _sessions = adapter_for_client(monkeypatch, client)
    page_one = adapter.search_keys('item:')
    page_two = adapter.search_keys('item:', continuation=page_one.continuation)
    loaded = [item.name for page in (page_one, page_two) for item in page.keys]
    assert len(loaded) == len(set(loaded)) == 801
    assert b'item:\xff' in loaded
    assert len(page_two.keys) == 301
    assert page_two.continuation is None
    assert [len(batch) for batch in client.typed] == [500, 301]


def test_sparse_prefix_returns_a_resumable_empty_batch_after_bounded_scans(monkeypatch):
    pages = {cursor: (cursor + 1, []) for cursor in range(8)}
    pages[8] = (0, [b'needle:found'])
    client = ScanClient(pages)
    adapter, _sessions = adapter_for_client(monkeypatch, client)
    first = adapter.search_keys('needle:')
    assert len(client.scans) == 8, 'A sparse prefix must not scan the entire database in one expansion'
    assert first.keys == []
    assert first.truncated and first.continuation is not None
    assert not client.typed
    second = adapter.search_keys('needle:', continuation=first.continuation)
    assert [item.name for item in second.keys] == ['needle:found']
    assert second.continuation is None
    assert len(client.scans) == 9


def test_failed_next_batch_can_retry_the_same_immutable_continuation(monkeypatch):
    keys = [f'item:{index:04}'.encode() for index in range(700)]
    client = ScanClient({0: (0, keys)})
    adapter, _sessions = adapter_for_client(monkeypatch, client)
    first = adapter.search_keys('item:')
    continuation = first.continuation
    before = repr(continuation)
    client.fail_type = True
    with pytest.raises(RuntimeError, match='isolated type lookup failed'):
        adapter.search_keys('item:', continuation=continuation)
    assert repr(continuation) == before
    client.fail_type = False
    retried = adapter.search_keys('item:', continuation=continuation)
    assert {item.name for item in retried.keys} == {f'item:{index:04}' for index in range(500, 700)}
    assert retried.continuation is None


@pytest.mark.parametrize('mismatch', ['connection', 'database', 'prefix', 'exact'])
@pytest.mark.parametrize('use_ssh', [False, True])
def test_continuation_is_bound_to_original_scope_and_search(monkeypatch, mismatch, use_ssh):
    client = ScanClient({0: (0, [f'item:{index:04}'.encode() for index in range(501)])})
    adapter, sessions = adapter_for_client(monkeypatch, client)
    page = adapter.search_keys('item:')
    assert page.continuation is not None
    if mismatch == 'connection':
        adapter.connection = replace(adapter.connection, id='unrelated-connection')
    elif mismatch == 'database':
        adapter.connection = replace(adapter.connection, database='1')
    text = 'other:' if mismatch == 'prefix' else 'item:'
    mode = 'exact' if mismatch == 'exact' else 'prefix'
    target = SSHAdapter(adapter.connection) if use_ssh else adapter
    monkeypatch.setattr('baseskiff.ssh.ssh_endpoint', lambda *_args: pytest.fail('Invalid continuation must not open SSH'))
    previous = list(sessions)
    with pytest.raises(ValueError, match='continuation'):
        target.search_keys(text, mode, continuation=page.continuation)
    assert sessions == previous, 'Invalid scope must fail before opening a connection'


def test_exact_search_never_scans_or_offers_continuation(monkeypatch):
    with fakeredis.FakeRedis(decode_responses=False) as client:
        client.set(b'item:*?\xff', b'value')
        adapter, _sessions = adapter_for_client(monkeypatch, client)
        monkeypatch.setattr(client, 'scan', lambda *args, **kwargs: pytest.fail('Exact search must not scan'))
        page = adapter.search_keys(b'item:*?\xff', 'exact')
        assert [item.name for item in page.keys] == [b'item:*?\xff']
        assert not page.truncated and page.continuation is None


@pytest.mark.parametrize('use_ssh', [False, True])
def test_service_resumes_explicit_database_with_new_sessions_and_same_ssh_route(tmp_path, monkeypatch, use_ssh):
    server = fakeredis.FakeServer()
    with fakeredis.FakeRedis(server=server, db=3, decode_responses=False) as client:
        client.mset({f'item:{index:04}': 'value' for index in range(700)})
    sessions = []
    endpoint = object()

    @contextmanager
    def session(adapter):
        sessions.append((adapter.connection.database, adapter.endpoint))
        with fakeredis.FakeRedis(server=server, db=int(adapter.connection.database), decode_responses=False) as client:
            yield client

    @contextmanager
    def tunnel(connection):
        assert connection.ssh_enabled
        yield endpoint

    monkeypatch.setattr(RedisAdapter, 'session', session)
    monkeypatch.setattr('baseskiff.ssh.ssh_endpoint', tunnel)
    service = DatabaseService(tmp_path)
    saved = service.save_connection(Connection('Resumable direct/SSH', kind='Redis', database='0',
                                                ssh_enabled=use_ssh, ssh_host='test.invalid',
                                                ssh_username='tester', ssh_auth_method='agent'))
    first = service.search_redis_keys(saved.id, 'item:', database='3')
    second = service.search_redis_keys(saved.id, 'item:', database='3', continuation=first.continuation)
    assert [len(first.keys), len(second.keys)] == [500, 200]
    assert second.continuation is None
    assert sessions == [('3', endpoint if use_ssh else None)] * 2
