"""SSH adapter routing and TLS identity regressions without external databases."""
import importlib
import socket
import ssl
import sys
from contextlib import contextmanager
from types import SimpleNamespace

import oracledb
import pymysql
import pytest

from baseskiff import adapters
from baseskiff.models import Connection

ENDPOINT = SimpleNamespace(host="127.0.0.1", port=43101, proxy_port=43102)
OPERATIONS = [
    ("test", ()), ("list_tables", ()), ("get_schema", ("records",)),
    ("fetch_rows", ("records",)), ("execute", ("SELECT 1",)),
    ("insert_row", ("records", {"id": 1})),
    ("update_row", ("records", {"id": 1}, {"value": "new"})),
    ("delete_rows", ("records", [{"id": 1}])),
]


@pytest.mark.parametrize("kind,adapter_class", [
    ("PostgreSQL", adapters.SQLAdapter), ("MySQL", adapters.SQLAdapter),
    ("Oracle", adapters.SQLAdapter), ("MongoDB", adapters.MongoAdapter),
    ("Redis", adapters.RedisAdapter),
])
@pytest.mark.parametrize("operation,args", OPERATIONS)
def test_each_public_operation_owns_one_route_and_keeps_logical_connection(
        monkeypatch, kind, adapter_class, operation, args):
    connection = Connection(name="Via SSH", kind=kind, host="private.db", ssh_enabled=True)
    events = []

    @contextmanager
    def endpoint(actual):
        assert actual is connection
        events.append("open")
        try:
            yield ENDPOINT
        finally:
            events.append("close")

    def invoke(self, *actual_args, **kwargs):
        assert events == ["open"]
        assert self.connection is connection
        assert self.endpoint is ENDPOINT
        assert actual_args[:len(args)] == args
        return "result"

    monkeypatch.setitem(sys.modules, "baseskiff.ssh", SimpleNamespace(ssh_endpoint=endpoint))
    monkeypatch.setattr(adapter_class, operation, invoke)
    adapter = adapters.adapter_for(connection)
    assert getattr(adapter, operation)(*args) == "result"
    assert events == ["open", "close"]
    assert connection.host == "private.db"
    assert not hasattr(adapter, "_open")
    assert not hasattr(adapter, "session")


def test_failed_database_operation_closes_route(monkeypatch):
    events = []

    @contextmanager
    def endpoint(_connection):
        events.append("open")
        try:
            yield ENDPOINT
        finally:
            events.append("close")

    def fail(self, _query):
        raise RuntimeError("database failure")

    monkeypatch.setitem(sys.modules, "baseskiff.ssh", SimpleNamespace(ssh_endpoint=endpoint))
    monkeypatch.setattr(adapters.SQLAdapter, "execute", fail)
    with pytest.raises(RuntimeError, match="database failure"):
        adapters.adapter_for(Connection(name="SSH", kind="MySQL", ssh_enabled=True)).execute("SELECT 1")
    assert events == ["open", "close"]


def test_direct_connection_does_not_load_ssh_transport(monkeypatch):
    original_import = importlib.import_module

    def checked_import(name, *args, **kwargs):
        assert "paramiko" not in name and name != "baseskiff.ssh"
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(importlib, "import_module", checked_import)
    connection = Connection(name="Direct", kind="PostgreSQL")
    assert type(adapters.adapter_for(connection)) is adapters.SQLAdapter


def test_sqlite_rejects_ssh_instead_of_opening_local_file():
    with pytest.raises(ValueError, match="SQLite.*SSH|SSH.*SQLite"):
        adapters.adapter_for(Connection(name="Invalid", kind="SQLite", ssh_enabled=True))


def test_postgres_preserves_logical_host_and_routes_hostaddr(monkeypatch):
    values = {}
    monkeypatch.setattr(adapters, "driver", lambda *_: SimpleNamespace(connect=lambda **kwargs: values.update(kwargs)))
    connection = Connection(name="PG TLS", kind="PostgreSQL", host="private.db", port=5433, ssl=True)
    adapters.SQLAdapter(connection, endpoint=ENDPOINT)._open()
    assert values["host"] == "private.db"
    assert values["hostaddr"] == ENDPOINT.host
    assert values["port"] == ENDPOINT.port
    assert values["sslmode"] == "require"


def test_mysql_preserves_tls_identity_when_using_connected_tunnel_socket(monkeypatch):
    values = {}
    fake_socket = SimpleNamespace(close=lambda: None)
    actual_connect = pymysql.connect

    def connect(**kwargs):
        values.update(kwargs)
        db = actual_connect(**kwargs)
        values["db"] = db
        db.connect = lambda sock: values.update(socket=sock)
        return db

    def create_connection(address, timeout):
        assert address == (ENDPOINT.host, ENDPOINT.port)
        assert timeout == 8
        return fake_socket

    monkeypatch.setattr(adapters, "driver", lambda *_: SimpleNamespace(connect=connect))
    monkeypatch.setattr(socket, "create_connection", create_connection)
    connection = Connection(name="MySQL TLS", kind="MySQL", host="private.db", port=3307, ssl=True)
    db = adapters.SQLAdapter(connection, endpoint=ENDPOINT)._open()
    assert values["host"] == db.host == "private.db"
    assert values["port"] == 3307
    assert values["defer_connect"] is True
    assert values["socket"] is fake_socket
    assert db.ctx.check_hostname is True
    assert db.ctx.verify_mode == ssl.CERT_REQUIRED


@pytest.mark.parametrize("tls", [False, True])
def test_oracle_routes_tcp_or_https_proxy_and_preserves_tcps_identity(monkeypatch, tls):
    values = {}
    db = SimpleNamespace()

    def connect(**kwargs):
        values.update(kwargs)
        return db

    monkeypatch.setattr(adapters, "driver", lambda *_: SimpleNamespace(makedsn=oracledb.makedsn, connect=connect))
    connection = Connection(name="Oracle", kind="Oracle", host="private.db", port=2484,
                            database="service", ssl=tls)
    assert adapters.SQLAdapter(connection, endpoint=ENDPOINT)._open() is db
    parameters = oracledb.ConnectParams(
        **{key: value for key, value in values.items() if key in {"https_proxy", "https_proxy_port"}})
    parameters.parse_connect_string(values["dsn"])
    assert parameters.host == ("private.db" if tls else ENDPOINT.host)
    assert parameters.port == (2484 if tls else ENDPOINT.port)
    assert parameters.protocol == ("tcps" if tls else "tcp")
    assert parameters.ssl_server_dn_match is True
    if tls:
        assert parameters.https_proxy == ENDPOINT.host
        assert parameters.https_proxy_port == ENDPOINT.proxy_port
    assert db.call_timeout == 10000


def test_mongo_tunnel_client_has_one_direct_target_and_is_closed(monkeypatch):
    values = {}
    class Client:
        def __getitem__(self, key):
            return key

        def close(self):
            values["closed"] = True

    client = Client()

    def connect(**kwargs):
        values.update(kwargs)
        return client

    monkeypatch.setattr(adapters, "driver", lambda *_: SimpleNamespace(MongoClient=connect))
    connection = Connection(name="Mongo", kind="MongoDB", host="private.db", port=27018, database="data")
    with adapters.MongoAdapter(connection, endpoint=ENDPOINT).session() as db:
        assert db == "data"
        assert values["host"] == ENDPOINT.host
        assert values["port"] == ENDPOINT.port
        assert values["directConnection"] is True
    assert values["closed"] is True


def test_mongo_tls_context_keeps_verification_and_changes_only_peer_identity(monkeypatch):
    import pymongo
    from pymongo.pool_options import PoolOptions

    values = {}

    def connect(**kwargs):
        values.update(kwargs)
        return pymongo.MongoClient(connect=False, **kwargs)

    monkeypatch.setattr(adapters, "driver", lambda *_: SimpleNamespace(MongoClient=connect))
    connection = Connection(name="Mongo TLS", kind="MongoDB", host="private.db", ssl=True)
    with adapters.MongoAdapter(connection, endpoint=ENDPOINT).session():
        pool_class = values["_pool_class"]
        original_context = ssl.create_default_context()
        options = PoolOptions(ssl_context=original_context)
        pool = pool_class((ENDPOINT.host, ENDPOINT.port), options)
        try:
            context = pool.opts._ssl_context
            assert options._ssl_context is original_context
            assert context.verify_mode == ssl.CERT_REQUIRED
            assert context.check_hostname is True
            calls = []
            monkeypatch.setattr(original_context, "wrap_socket", lambda *args, **kwargs: calls.append(kwargs))
            context.wrap_socket(object(), server_hostname=ENDPOINT.host)
            assert calls[0]["server_hostname"] == "private.db"
        finally:
            pool.close()


def test_redis_tunnel_pool_preserves_identity_and_closes_connections():
    import redis
    connection = Connection(name="Redis TLS", kind="Redis", host="private.db", port=6380, ssl=True)
    with adapters.RedisAdapter(connection, endpoint=ENDPOINT).session() as client:
        assert isinstance(client, redis.Redis)
        pool = client.connection_pool
        link = pool.make_connection()
        assert link.host == "private.db"
        assert link.port == 6380
        assert isinstance(link, redis.SSLConnection)
        assert link.check_hostname is True
        assert link.cert_reqs == ssl.CERT_REQUIRED


@pytest.fixture
def tls_server(tmp_path, monkeypatch):
    """A trusted local TLS peer whose certificate names a remote database host."""
    from datetime import datetime, timedelta, timezone
    from threading import Thread

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "SSH adapter test CA")])
    now = datetime.now(timezone.utc)
    ca = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
          .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(days=1))
          .not_valid_after(now + timedelta(days=1))
          .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
          .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
          .add_extension(x509.KeyUsage(True, False, False, False, False, True, True, False, False), critical=True)
          .sign(key, hashes.SHA256()))
    leaf = (x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "private.db")]))
            .issuer_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("private.db")]), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(key.public_key()), critical=False)
            .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .sign(key, hashes.SHA256()))
    ca_file, cert_file, key_file = (tmp_path / filename for filename in ("ca.pem", "server.pem", "server.key"))
    ca_file.write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    cert_file.write_bytes(leaf.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                          serialization.NoEncryption()))
    monkeypatch.setenv("SSL_CERT_FILE", str(ca_file))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_file, key_file)
    server_names = []
    context.set_servername_callback(lambda _socket, hostname, _context: server_names.append(hostname))
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    listener.settimeout(2)

    def serve():
        try:
            peer, _ = listener.accept()
            with peer:
                with context.wrap_socket(peer, server_side=True):
                    pass
        except (OSError, ssl.SSLError):
            pass

    worker = Thread(target=serve, daemon=True)
    worker.start()
    yield SimpleNamespace(host="127.0.0.1", port=listener.getsockname()[1]), server_names
    listener.close()
    worker.join(timeout=3)
    assert not worker.is_alive()


@pytest.mark.parametrize("kind", ["MongoDB", "Redis"])
@pytest.mark.parametrize("hostname", ["private.db", "wrong.db"])
def test_real_tls_over_local_route_validates_original_database_hostname(monkeypatch, tls_server, kind, hostname):
    endpoint, server_names = tls_server
    connection = Connection(name="TLS through SSH", kind=kind, host=hostname, ssl=True)
    if kind == "Redis":
        adapter = adapters.RedisAdapter(connection, endpoint=endpoint)
        with adapter.session() as client:
            link = client.connection_pool.make_connection()
            connect = link._connect
            if hostname == "wrong.db":
                with pytest.raises(ssl.SSLCertVerificationError):
                    connect()
            else:
                with connect() as secure:
                    assert secure.getpeername() == (endpoint.host, endpoint.port)
    else:
        import pymongo
        from pymongo.pool_options import PoolOptions

        values = {}

        def mongo_client(**kwargs):
            values.update(kwargs)
            return pymongo.MongoClient(connect=False, **kwargs)

        monkeypatch.setattr(adapters, "driver", lambda *_: SimpleNamespace(MongoClient=mongo_client))
        with adapters.MongoAdapter(connection, endpoint=endpoint).session():
            pool = values["_pool_class"]((endpoint.host, endpoint.port),
                                         PoolOptions(ssl_context=ssl.create_default_context()))
            try:
                raw = socket.create_connection((endpoint.host, endpoint.port), timeout=2)
                context = pool.opts._ssl_context
                if hostname == "wrong.db":
                    with pytest.raises(ssl.SSLCertVerificationError):
                        context.wrap_socket(raw, server_hostname=endpoint.host)
                    raw.close()
                else:
                    with context.wrap_socket(raw, server_hostname=endpoint.host) as secure:
                        assert secure.getpeername() == (endpoint.host, endpoint.port)
            finally:
                pool.close()
    assert server_names == [hostname]


def test_fetch_schema_and_data_sessions_share_one_tunnel(monkeypatch):
    from baseskiff.models import QueryResult

    events = []
    sessions = []

    @contextmanager
    def endpoint(_connection):
        events.append("open")
        try:
            yield ENDPOINT
        finally:
            events.append("close")

    @contextmanager
    def session(self):
        assert events == ["open"]
        assert self.endpoint is ENDPOINT
        sessions.append(self.endpoint)
        yield object()

    def query(self, _db, statement, values=(), limit=adapters.MAX_ROWS):
        if "information_schema.COLUMNS" in statement:
            return QueryResult(rows=[{"name": "id", "type": "int", "key_type": "PRI",
                                      "nullable": "NO", "def": None}])
        if "COUNT(*)" in statement:
            return QueryResult(rows=[{"count_rows": 1}])
        return QueryResult(columns=["id"], rows=[{"id": 42}])

    monkeypatch.setitem(sys.modules, "baseskiff.ssh", SimpleNamespace(ssh_endpoint=endpoint))
    monkeypatch.setattr(adapters.SQLAdapter, "session", session)
    monkeypatch.setattr(adapters.SQLAdapter, "_query", query)
    result = adapters.adapter_for(Connection(name="SSH MySQL", kind="MySQL", ssh_enabled=True)).fetch_rows("records")
    assert result.rows == [{"id": 42}]
    assert result.total == 1
    assert len(sessions) == 2
    assert events == ["open", "close"]


def test_mysql_failed_handshake_closes_the_tunnel_socket(monkeypatch):
    events = []
    sock = SimpleNamespace(close=lambda: events.append("socket closed"))

    def connect(sock):
        raise ValueError("handshake failed")

    monkeypatch.setattr(socket, "create_connection", lambda *args, **kwargs: sock)
    monkeypatch.setattr(adapters, "driver", lambda *_: SimpleNamespace(
        connect=lambda **kwargs: SimpleNamespace(connect=connect)))
    with pytest.raises(ValueError, match="handshake failed"):
        adapters.SQLAdapter(Connection(name="MySQL", kind="MySQL"), endpoint=ENDPOINT)._open()
    assert events == ["socket closed"]


def test_mongo_tls_pool_rejects_an_incompatible_private_driver_boundary():
    from baseskiff.routing import mongo_tls_pool

    with pytest.raises(RuntimeError, match="PyMongo version"):
        mongo_tls_pool("private.db")((ENDPOINT.host, ENDPOINT.port), object())


@pytest.mark.parametrize("invalid_context", [None, ssl._create_unverified_context()])
def test_mongo_tls_pool_cannot_disable_certificate_verification(invalid_context):
    from pymongo.pool_options import PoolOptions

    from baseskiff.routing import mongo_tls_pool

    with pytest.raises(RuntimeError, match="requires|verification"):
        mongo_tls_pool("private.db")((ENDPOINT.host, ENDPOINT.port), PoolOptions(ssl_context=invalid_context))
