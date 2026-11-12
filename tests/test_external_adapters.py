"""Driver boundary regressions; live external servers are intentionally not required."""
import ssl
from types import SimpleNamespace

import oracledb
import pymysql

from baseskiff.adapters import SQLAdapter
from baseskiff.models import Connection


class Cursor:
    description = None
    rowcount = 0

    def __init__(self, rows=()):
        self.rows = rows
        self.executions = []

    def execute(self, query, values=(), **kwargs):
        self.executions.append((query, values, kwargs))

    def fetchmany(self, count):
        return self.rows[:count]

    def close(self):
        pass


def test_postgres_uses_extended_protocol_even_without_bound_parameters():
    adapter = SQLAdapter(Connection(name="Postgres", kind="PostgreSQL"))
    cursor = Cursor()
    # Psycopg's prepare=True selects extended Parse/Bind protocol, whose Parse
    # message accepts a single SQL statement. No-bind simple protocol accepts
    # a batch, allowing COMMIT to escape a preceding read-only transaction.
    adapter._query(SimpleNamespace(cursor=lambda: cursor), "SELECT 1; COMMIT; UPDATE accounts SET balance=0")
    assert cursor.executions[0][2].get("prepare") is True


def test_mysql_tls_enforces_system_trust_and_hostname(monkeypatch):
    actual_connect = pymysql.connect
    module = SimpleNamespace(connect=lambda **kwargs: actual_connect(defer_connect=True, **kwargs))
    monkeypatch.setattr("baseskiff.adapters.driver", lambda *_: module)
    adapter = SQLAdapter(Connection(name="MySQL TLS", kind="MySQL", ssl=True))
    db = adapter._open()
    assert db.ctx.verify_mode == ssl.CERT_REQUIRED
    assert db.ctx.check_hostname is True


class LiveLOB(oracledb.LOB):
    """A real LOB subtype with a controlled connection-dependent read boundary."""
    def __init__(self, state, value):
        self.state = state
        self.value = value

    def read(self, *args, **kwargs):
        if self.state.closed:
            raise RuntimeError("LOB connection is closed")
        return self.value

    def __del__(self):
        pass


def test_oracle_lobs_are_materialized_while_the_connection_is_open():
    adapter = SQLAdapter(Connection(name="Oracle", kind="Oracle"))
    state = SimpleNamespace(closed=False)
    cursor = Cursor([(LiveLOB(state, "long text"), LiveLOB(state, b"binary content"))])
    cursor.description = [("text",), ("binary",)]
    result = adapter._query(SimpleNamespace(cursor=lambda: cursor), "SELECT text, binary FROM records")
    state.closed = True
    assert result.rows == [{"text": "long text", "binary": b"binary content"}]
