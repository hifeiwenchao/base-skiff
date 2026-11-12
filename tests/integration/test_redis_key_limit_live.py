"""Bounded sidebar searches against real Redis over direct and SSH routes."""
import uuid
from contextlib import nullcontext

import pytest
from redis import Redis

from baseskiff.models import Connection
from tests.ssh_test_server import TestSSHServer

pytestmark = pytest.mark.integration


def _load_all_key_batches(service, connection_id, prefix, first_page=None):
    page = first_page or service.search_redis_keys(connection_id, prefix, "prefix")
    loaded = set()
    for batch in range(30):
        names = [item.name for item in page.keys]
        assert len(names) <= 500
        assert len(names) == len(set(names))
        assert loaded.isdisjoint(names)
        assert all(item.kind == "string" for item in page.keys)
        assert page.truncated is (page.continuation is not None)
        loaded.update(names)
        if page.continuation is None:
            assert not page.truncated
            return loaded
        if batch < 29:
            page = service.search_redis_keys(
                connection_id, prefix, "prefix", continuation=page.continuation)
    pytest.fail("Redis key discovery did not finish within 30 bounded batches")


@pytest.mark.parametrize("route", ["direct", "ssh"])
def test_key_search_limit_and_exact_boundary(live_config, live_service, run_tag, route):
    configuration = live_config.get("redis")
    if not configuration:
        pytest.skip("The live configuration does not provide Redis")
    host, port = live_config["host"], configuration["port"]
    prefix = f"{run_tag}:key-limit:{route}:{uuid.uuid4().hex[:8]}:"
    boundary_prefix = prefix + "boundary:"
    boundary_keys = [boundary_prefix + f"{index:04}" for index in range(500)]
    other_keys = [prefix + f"other:{index:04}" for index in range(100)]
    keys = boundary_keys + other_keys
    extra_key = boundary_prefix + "0500"
    raw = Redis(host=host, port=port, db=0,
                username=configuration.get("username") or None,
                password=configuration.get("password") or None,
                protocol=2, decode_responses=True, socket_connect_timeout=8, socket_timeout=10)
    try:
        raw.mset(dict.fromkeys(keys, "key-limit-test"))
        with TestSSHServer() if route == "ssh" else nullcontext() as ssh:
            options = dict(name="Redis key limit " + route, kind="Redis", database="0",
                           username=configuration.get("username", ""),
                           password=configuration.get("password", ""))
            connection = (ssh.connection(host, port, **options) if ssh else
                          Connection(host=host, port=port, **options))
            saved = live_service.save_connection(connection)

            prefix_page = live_service.search_redis_keys(saved.id, prefix, "prefix")
            returned = {item.name for item in prefix_page.keys}
            assert len(prefix_page.keys) == len(returned) <= 500
            assert returned <= set(keys)
            assert prefix_page.truncated
            assert prefix_page.continuation is not None
            assert all(item.kind == "string" for item in prefix_page.keys)

            # An exact lookup must still reach a key outside the first preview.
            omitted = sorted(set(keys) - {item.name for item in prefix_page.keys})[0]
            exact_page = live_service.search_redis_keys(saved.id, omitted, "exact")
            assert [item.name for item in exact_page.keys] == [omitted]
            assert not exact_page.truncated
            assert exact_page.continuation is None

            assert _load_all_key_batches(live_service, saved.id, prefix, prefix_page) == set(keys)
            assert _load_all_key_batches(live_service, saved.id, boundary_prefix) == set(boundary_keys)

            raw.set(extra_key, "one-key-over-limit")
            over_limit_page = live_service.search_redis_keys(saved.id, boundary_prefix, "prefix")
            assert len(over_limit_page.keys) <= 500
            assert {item.name for item in over_limit_page.keys} <= set(boundary_keys + [extra_key])
            assert over_limit_page.truncated
            assert over_limit_page.continuation is not None
            assert _load_all_key_batches(
                live_service, saved.id, boundary_prefix, over_limit_page) == set(boundary_keys + [extra_key])
            if ssh:
                assert ssh.destinations and all(target == (host, port) for target in ssh.destinations)
    finally:
        try:
            raw.delete(*keys, extra_key)
        finally:
            raw.close()
