"""Redis key grouping preferences survive connection save/edit and app restart."""
import json
import sqlite3
from dataclasses import replace

import pytest

from baseskiff.models import Connection
from baseskiff.service import DatabaseService


def test_legacy_connections_default_to_colon_without_a_migration(tmp_path):
    service = DatabaseService(tmp_path)
    saved = service.save_connection(Connection(name="Legacy Redis", kind="Redis"))
    with sqlite3.connect(service.metadata_path) as database:
        payload = json.loads(database.execute(
            "SELECT payload FROM connections WHERE id=?", (saved.id,)).fetchone()[0])
        payload.pop("redis_separator", None)
        database.execute("UPDATE connections SET payload=? WHERE id=?",
                         (json.dumps(payload), saved.id))
    restarted = DatabaseService(tmp_path)
    legacy = next(c for c in restarted.list_connections() if c.id == saved.id)
    assert legacy.redis_separator == ":"


@pytest.mark.parametrize("separator", [":", "::", "", " ", "目录→"])
def test_separator_survives_save_edit_and_restart(tmp_path, separator):
    service = DatabaseService(tmp_path)
    connection = Connection(name="Redis grouping", kind="Redis", redis_separator=separator)
    saved = service.save_connection(connection)
    assert saved.redis_separator == separator
    service.save_connection(replace(saved, name="Renamed Redis", read_only=True))
    restarted = DatabaseService(tmp_path)
    loaded = next(c for c in restarted.list_connections() if c.id == saved.id)
    assert loaded.redis_separator == separator
    assert loaded.name == "Renamed Redis" and loaded.read_only


def test_separators_belong_to_each_connection(tmp_path):
    service = DatabaseService(tmp_path)
    first = service.save_connection(Connection(name="Colon", kind="Redis", redis_separator=":"))
    second = service.save_connection(Connection(name="Slash", kind="Redis", redis_separator="/"))
    service.save_connection(replace(first, redis_separator=""))
    loaded = {c.id: c for c in service.list_connections()}
    assert loaded[first.id].redis_separator == ""
    assert loaded[second.id].redis_separator == "/"
