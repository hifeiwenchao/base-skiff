"""Opt-in logical database isolation over actual direct and SSH Redis clients."""
import shlex
from contextlib import nullcontext

import pytest
from redis import Redis

from baseskiff.models import Connection
from baseskiff.service import DatabaseService
from tests.ssh_test_server import TestSSHServer

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("route", ["direct", "ssh"])
def test_database_selection_isolates_reads_writes_and_history(live_config, live_service, run_tag, route):
    configuration = live_config.get("redis")
    if not configuration:
        pytest.skip("The live configuration does not provide Redis")
    options = dict(name="Redis databases " + route, kind="Redis", database="0",
                   username=configuration.get("username", ""), password=configuration.get("password", ""))
    key = run_tag + ":databases:" + route
    clients = [Redis(host=live_config["host"], port=configuration["port"], db=index,
                     username=options["username"] or None, password=options["password"] or None,
                     protocol=2, decode_responses=True, socket_connect_timeout=8, socket_timeout=10)
               for index in (0, 1)]
    written = False
    try:
        with TestSSHServer() if route == "ssh" else nullcontext() as ssh:
            connection = (ssh.connection(live_config["host"], configuration["port"], **options) if ssh else
                          Connection(host=live_config["host"], port=configuration["port"], **options))
            saved = live_service.save_connection(connection)
            databases = live_service.list_redis_databases(saved.id)
            if 1 not in {item.index for item in databases.databases}:
                pytest.skip("The Redis server provides only DB0")
            live_service.set_redis_database(saved.id, 1)
            live_service.execute(saved.id, "SET " + shlex.quote(key) + " db-zero", database=0)
            written = True
            live_service.execute(saved.id, "SET " + shlex.quote(key) + " db-one", database=1)
            assert live_service.read_redis_key(saved.id, key).value == "db-one"
            assert live_service.read_redis_key(saved.id, key, database=0).value == "db-zero"
            live_service.mutate_redis_entry(saved.id, key, "string", "update",
                                            {"expected": "db-zero", "value": "edited-zero"}, database=0)
            assert clients[0].get(key) == "edited-zero"
            assert clients[1].get(key) == "db-one"
            assert [item.name for item in live_service.search_redis_keys(saved.id, key, "exact", database=0).keys] == [key]
            refreshed = live_service.list_redis_databases(saved.id)
            assert all(item.key_count is None or item.key_count >= 1
                       for item in refreshed.databases if item.index in (0, 1))
            history = live_service.list_history()
            assert [(entry.database, entry.success) for entry in history[:3]] == [("0", True), ("1", True), ("0", True)]
            assert history[1].query == "SET " + shlex.quote(key) + " db-one"
            restored = next(item for item in DatabaseService(live_service.data_dir).list_connections() if item.id == saved.id)
            assert restored.database == "1"
            if ssh:
                assert ssh.destinations and all(target == (live_config["host"], configuration["port"])
                                                for target in ssh.destinations)
    finally:
        for client in clients:
            if written:
                client.delete(key)
            client.close()
