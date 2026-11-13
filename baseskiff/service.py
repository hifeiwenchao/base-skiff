"""Persistent metadata and database operations shared by desktop workers.

Every operation opens and closes its own database handle. No Qt or database
connection object crosses a worker boundary.
"""
import json
import os
import sqlite3
import sys
import time
import uuid
from dataclasses import asdict, replace
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from threading import RLock

from .adapters import adapter_for, redis_database_index
from .cancellation import CancellationToken, QueryCancelled  # noqa: F401 - public compatibility exports
from .credentials import LocalCredentials
from .demo import create_playground
from .models import (
    DATABASE_TYPES,
    Connection,
    HistoryEntry,
    QueryResult,
    RedisDatabaseList,
    RedisKeyPage,
    RedisValue,
)
from .redis_codec import json_safe

SECRET_FIELDS = ('password', 'ssh_password', 'ssh_passphrase')


def secret_key(connection_id, field):
    return connection_id if field == 'password' else f'{connection_id}:{field}'


def public_connection(connection):
    return replace(connection, **dict.fromkeys(SECRET_FIELDS, ''))


def connection_payload(connection):
    values = asdict(connection)
    for field in SECRET_FIELDS:
        values.pop(field, None)
    return values


def history_value(value):
    """Describe raw binary identities without changing operation arguments."""
    return json.dumps(json_safe(value), ensure_ascii=False, default=str)


def history_table(table):
    return history_value(table) if isinstance(table, bytes) else table


def history_sql_identifier(identifier, kind, *, qualified=False):
    """Render a SQL identifier for the operation history preview."""
    if isinstance(identifier, bytes):
        return history_value(identifier)
    text = str(identifier)
    quote = '`' if kind == 'MySQL' else '"'
    parts = text.split('.', 1) if qualified and kind in {'PostgreSQL', 'Oracle'} and '.' in text else [text]
    return '.'.join(quote + part.replace(quote, quote * 2) + quote for part in parts)


def history_sql_literal(value):
    """Render a bound value as a readable SQL literal without executing it."""
    if value is None:
        return 'NULL'
    if isinstance(value, bool):
        return 'TRUE' if value else 'FALSE'
    if isinstance(value, (int, float, Decimal)):
        return str(value)
    if isinstance(value, (date, datetime)):
        value = value.isoformat()
    elif isinstance(value, (dict, list, tuple)):
        value = json.dumps(json_safe(value), ensure_ascii=False, default=str)
    elif isinstance(value, bytes):
        return "X'" + value.hex() + "'"
    else:
        value = str(value)
    return "'" + value.replace("'", "''") + "'"


def grid_history_sql(kind, table, updates=(), inserts=(), deletes=()):
    """Describe the exact staged SQL statements committed as one transaction."""
    table_sql = history_sql_identifier(table, kind, qualified=True)
    statements = []
    for keys, values in updates:
        assignments = ', '.join(
            f'{history_sql_identifier(name, kind)} = {history_sql_literal(value)}'
            for name, value in values.items())
        where = ' AND '.join(
            f'{history_sql_identifier(name, kind)} = {history_sql_literal(value)}'
            for name, value in keys.items())
        statements.append(f'UPDATE {table_sql} SET {assignments} WHERE {where};')
    for values in inserts:
        if values:
            columns = ', '.join(history_sql_identifier(name, kind) for name in values)
            literals = ', '.join(history_sql_literal(value) for value in values.values())
            statements.append(f'INSERT INTO {table_sql} ({columns}) VALUES ({literals});')
        else:
            statements.append(f'INSERT INTO {table_sql} DEFAULT VALUES;')
    for keys in deletes:
        where = ' AND '.join(
            f'{history_sql_identifier(name, kind)} = {history_sql_literal(value)}'
            for name, value in keys.items())
        statements.append(f'DELETE FROM {table_sql} WHERE {where};')
    return '\n'.join(statements)


def default_data_dir() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "BaseSkiff"
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "BaseSkiff"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "baseskiff"


class DatabaseService:
    def __init__(self, data_dir=None):
        self.data_dir = Path(data_dir) if data_dir is not None else default_data_dir()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.metadata_path = self.data_dir / "metadata.sqlite"
        self._lock = RLock()
        self._credentials = LocalCredentials(self.data_dir / 'credentials.key')
        with self._metadata() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS connections (
                    id TEXT PRIMARY KEY, payload TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS history (
                    id TEXT PRIMARY KEY, connection_id TEXT NOT NULL, connection_name TEXT NOT NULL,
                    query TEXT NOT NULL, timestamp TEXT NOT NULL, elapsed_ms REAL NOT NULL,
                    affected INTEGER NOT NULL, success INTEGER NOT NULL, error TEXT NOT NULL DEFAULT '',
                    database TEXT NOT NULL DEFAULT '');
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS credentials (key TEXT PRIMARY KEY, value BLOB NOT NULL);
                CREATE TABLE IF NOT EXISTS sql_consoles (
                    connection_id TEXT NOT NULL,
                    database TEXT NOT NULL,
                    content TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (connection_id, database));
                CREATE TABLE IF NOT EXISTS redis_searches (
                    id TEXT PRIMARY KEY,
                    connection_id TEXT NOT NULL,
                    database TEXT NOT NULL,
                    text TEXT NOT NULL,
                    mode TEXT NOT NULL CHECK(mode IN ('prefix', 'exact')),
                    created_at TEXT NOT NULL,
                    last_used_at TEXT NOT NULL,
                    is_favorite INTEGER NOT NULL DEFAULT 0 CHECK(is_favorite IN (0, 1)),
                    UNIQUE(connection_id, database, text));
                CREATE INDEX IF NOT EXISTS idx_redis_searches_scope_time
                    ON redis_searches(connection_id, database, last_used_at DESC);
            ''')
            db.execute("BEGIN IMMEDIATE")
            if "database" not in {row["name"] for row in db.execute("PRAGMA table_info(history)")}:
                db.execute("ALTER TABLE history ADD COLUMN database TEXT NOT NULL DEFAULT ''")
            if not db.execute("SELECT 1 FROM settings WHERE key='initialized'").fetchone():
                path = self.data_dir / "playground.sqlite"
                create_playground(path)
                connection = Connection(name="BaseSkiff Playground", path=str(path.resolve()),
                                        group="Local", is_demo=True)
                payload = connection_payload(connection)
                db.execute("INSERT INTO connections VALUES (?, ?, ?)",
                           (connection.id, json.dumps(payload), self._now()))
                db.execute("INSERT INTO settings VALUES ('initialized','1')")
        try:
            self.metadata_path.chmod(0o600)
        except OSError:
            pass

    @staticmethod
    def _now():
        return datetime.now(timezone.utc).isoformat(timespec="milliseconds")

    def _metadata(self):
        # sqlite3's context manager commits/rolls back but does not close. This
        # context wrapper prevents an accumulating set of worker-owned handles.
        from contextlib import contextmanager

        @contextmanager
        def transaction():
            with self._lock:
                db = sqlite3.connect(self.metadata_path, timeout=10)
                db.row_factory = sqlite3.Row
                try:
                    with db:
                        yield db
                finally:
                    db.close()
        return transaction()

    def _password(self, connection_id):
        with self._metadata() as db:
            row = db.execute('SELECT value FROM credentials WHERE key=?', (connection_id,)).fetchone()
        return self._credentials.decrypt(row['value']) if row is not None else ''

    def list_connections(self):
        with self._metadata() as db:
            rows = db.execute("SELECT payload FROM connections ORDER BY created_at, rowid").fetchall()
        # The password field is intentionally absent in list results. A dialog
        # editing a connection can leave it blank to preserve its stored secret.
        return [public_connection(Connection(**json.loads(row["payload"]))) for row in rows]

    def _connection(self, connection_id):
        with self._metadata() as db:
            row = db.execute("SELECT payload FROM connections WHERE id=?", (connection_id,)).fetchone()
        if row is None:
            raise ValueError("This connection no longer exists.")
        connection = Connection(**json.loads(row["payload"]))
        return self._hydrate_secrets(connection)

    def _hydrate_secrets(self, connection):
        return replace(connection, **{
            field: getattr(connection, field) or self._password(secret_key(connection.id, field))
            for field in SECRET_FIELDS})

    def save_connection(self, connection: Connection):
        from .ssh import validate_ssh

        if connection.kind not in DATABASE_TYPES:
            raise ValueError(f"Unsupported database type: {connection.kind}")
        if not connection.name.strip():
            raise ValueError("Give this connection a name.")
        if connection.kind == "SQLite" and not (connection.path or connection.database):
            raise ValueError("Choose a SQLite database file.")
        if connection.port and not 1 <= int(connection.port) <= 65535:
            raise ValueError("Port must be between 1 and 65535.")
        validate_ssh(connection)
        connection = replace(connection, name=connection.name.strip())
        payload = connection_payload(connection)
        with self._metadata() as db:
            exists = db.execute("SELECT 1 FROM connections WHERE id=?", (connection.id,)).fetchone() is not None
            db.execute("INSERT INTO connections VALUES (?, ?, ?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
                       (connection.id, json.dumps(payload, ensure_ascii=False), self._now()))
            # The metadata write obtains SQLite's writer lock before key-file
            # creation. Concurrent service instances never read a partially
            # written new key or publish different keys for the same database.
            for field in SECRET_FIELDS:
                value = getattr(connection, field)
                key = secret_key(connection.id, field)
                if value:
                    reference = db.execute('SELECT value FROM credentials LIMIT 1').fetchone()
                    token = self._credentials.encrypt(value, reference['value'] if reference else None)
                    db.execute('INSERT INTO credentials VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                               (key, token))
                elif not exists:
                    db.execute('DELETE FROM credentials WHERE key=?', (key,))
        return public_connection(connection)

    def delete_connection(self, connection_id):
        with self._metadata() as db:
            db.execute("DELETE FROM connections WHERE id=?", (connection_id,))
            db.execute("DELETE FROM redis_searches WHERE connection_id=?", (connection_id,))
            db.execute("DELETE FROM sql_consoles WHERE connection_id=?", (connection_id,))
            for field in SECRET_FIELDS:
                db.execute('DELETE FROM credentials WHERE key=?', (secret_key(connection_id, field),))

    def test_connection(self, connection):
        connection = self._hydrate_secrets(connection)
        return adapter_for(connection).test()

    def _operation_connection(self, connection_id, *, database=None):
        connection = self._connection(connection_id)
        if database is not None and connection.kind != "Redis":
            raise ValueError("A database override requires a Redis connection.")
        if connection.kind == "Redis":
            index = redis_database_index((connection.database or 0) if database is None else database)
            connection = replace(connection, database=str(index))
        return connection

    def list_tables(self, connection_id, *, database=None):
        return adapter_for(self._operation_connection(connection_id, database=database)).list_tables()

    def _redis_adapter(self, connection_id, *, database=None):
        connection = self._operation_connection(connection_id, database=database)
        if connection.kind != "Redis":
            raise ValueError("This operation requires a Redis connection.")
        return adapter_for(connection)

    def list_redis_databases(self, connection_id) -> RedisDatabaseList:
        return self._redis_adapter(connection_id).list_databases()

    def set_redis_database(self, connection_id, index):
        index = redis_database_index(index)
        connection = self._operation_connection(connection_id, database=index)
        adapter_for(connection).test()
        with self._metadata() as db:
            # Merge the selected database into the latest payload: a connection
            # edit may have completed while the network test was in progress.
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT payload FROM connections WHERE id=?", (connection_id,)).fetchone()
            if row is None:
                raise ValueError("This connection no longer exists.")
            payload = json.loads(row["payload"])
            if payload["kind"] != "Redis":
                raise ValueError("This operation requires a Redis connection.")
            payload["database"] = str(index)
            db.execute("UPDATE connections SET payload=? WHERE id=?",
                       (json.dumps(payload, ensure_ascii=False), connection_id))
        return public_connection(Connection(**payload))

    def search_redis_keys(self, connection_id, text="", mode="prefix", *, database=None, continuation=None) -> RedisKeyPage:
        adapter = self._redis_adapter(connection_id, database=database)
        if continuation is None:
            return adapter.search_keys(text, mode)
        return adapter.search_keys(text, mode, continuation=continuation)

    def read_redis_key(self, connection_id, key, *, database=None) -> RedisValue:
        return self._redis_adapter(connection_id, database=database).read_key(key)

    def mutate_redis_entry(self, connection_id, key, kind, action, payload, *, database=None) -> QueryResult:
        description = f"REDIS {action.upper()} {kind} {history_value(key)} {history_value(payload)}"

        def mutate(adapter):
            if adapter.connection.kind != "Redis":
                raise ValueError("This operation requires a Redis connection.")
            return adapter.mutate_entry(key, kind, action, payload)

        return self._perform(connection_id, description, mutate, database=database)

    def set_redis_ttl(self, connection_id, key, seconds, *, expected_ttl_ms=None, database=None) -> QueryResult:
        description = f"REDIS TTL {history_value(key)} {history_value(seconds)}"

        def mutate(adapter):
            if adapter.connection.kind != "Redis":
                raise ValueError("This operation requires a Redis connection.")
            return adapter.set_key_ttl(key, seconds, expected_ttl_ms=expected_ttl_ms)

        return self._perform(connection_id, description, mutate, database=database)

    def get_schema(self, connection_id, table, *, database=None):
        return adapter_for(self._operation_connection(connection_id, database=database)).get_schema(table)

    def list_indexes(self, connection_id, table, *, database=None):
        return adapter_for(self._operation_connection(connection_id, database=database)).list_indexes(table)

    def rename_table(self, connection_id, table, new_name, *, database=None):
        connection = self._operation_connection(connection_id, database=database)
        query = (f"RENAME TABLE {history_sql_identifier(table, connection.kind, qualified=True)} TO "
                 f"{history_sql_identifier(new_name, connection.kind)}"
                 if connection.kind == "MySQL" else
                 f"ALTER TABLE {history_sql_identifier(table, connection.kind, qualified=True)} RENAME TO "
                 f"{history_sql_identifier(new_name, connection.kind)}")
        return self._perform(connection_id, query,
                             lambda adapter: adapter.rename_table(table, new_name), database=database)

    def truncate_table(self, connection_id, table, *, database=None):
        connection = self._operation_connection(connection_id, database=database)
        query = (f"DELETE FROM {history_sql_identifier(table, connection.kind, qualified=True)}"
                 if connection.kind == "SQLite" else
                 f"TRUNCATE TABLE {history_sql_identifier(table, connection.kind, qualified=True)}")
        return self._perform(connection_id, query,
                             lambda adapter: adapter.truncate_table(table), database=database)

    def add_column(self, connection_id, table, name, data_type="TEXT", *, database=None):
        connection = self._operation_connection(connection_id, database=database)
        query = (f"ALTER TABLE {history_sql_identifier(table, connection.kind, qualified=True)} ADD COLUMN "
                 f"{history_sql_identifier(name, connection.kind)} {data_type}")
        return self._perform(connection_id, query,
                             lambda adapter: adapter.add_column(table, name, data_type), database=database)

    def drop_column(self, connection_id, table, name, *, database=None):
        connection = self._operation_connection(connection_id, database=database)
        query = (f"ALTER TABLE {history_sql_identifier(table, connection.kind, qualified=True)} DROP COLUMN "
                 f"{history_sql_identifier(name, connection.kind)}")
        return self._perform(connection_id, query,
                             lambda adapter: adapter.drop_column(table, name), database=database)

    def drop_table(self, connection_id, table, *, database=None):
        connection = self._operation_connection(connection_id, database=database)
        query = f"DROP TABLE {history_sql_identifier(table, connection.kind, qualified=True)}"
        return self._perform(connection_id, query,
                             lambda adapter: adapter.drop_table(table), database=database)

    def create_index(self, connection_id, table, name, columns, unique=False, *, database=None):
        connection = self._operation_connection(connection_id, database=database)
        index_sql = history_sql_identifier(name, connection.kind)
        table_sql = history_sql_identifier(table, connection.kind, qualified=True)
        columns_sql = ", ".join(history_sql_identifier(column, connection.kind) for column in columns)
        query = f"CREATE {'UNIQUE ' if unique else ''}INDEX {index_sql} ON {table_sql} ({columns_sql})"
        return self._perform(connection_id, query,
                             lambda adapter: adapter.create_index(table, name, columns, unique), database=database)

    def drop_index(self, connection_id, table, name, *, database=None):
        connection = self._operation_connection(connection_id, database=database)
        index_sql = history_sql_identifier(name, connection.kind)
        table_sql = history_sql_identifier(table, connection.kind, qualified=True)
        query = (f"DROP INDEX {index_sql} ON {table_sql}" if connection.kind == "MySQL"
                 else f"DROP INDEX {index_sql}")
        return self._perform(connection_id, query,
                             lambda adapter: adapter.drop_index(table, name), database=database)

    def fetch_rows(self, connection_id, table, page=1, page_size=50, search="", sort_column="", descending=False,
                   where_clause="", order_by="", *, database=None):
        start = time.perf_counter()
        result = adapter_for(self._operation_connection(connection_id, database=database)).fetch_rows(
            table, page=page, page_size=page_size, search=search,
            sort_column=sort_column, descending=descending,
            where_clause=where_clause, order_by=order_by)
        result.elapsed_ms = (time.perf_counter() - start) * 1000
        return result

    def _perform(self, connection_id, description, operation, *, database=None, cancel_token=None):
        connection = self._operation_connection(connection_id, database=database)
        start = time.perf_counter()
        result, error, success = QueryResult(), "", False
        adapter = None
        bound_callback = None
        try:
            if cancel_token is not None:
                cancel_token.raise_if_cancelled()
            adapter = adapter_for(connection)
            if cancel_token is not None:
                bound_callback = getattr(adapter, 'cancel', None)
                if bound_callback is not None:
                    cancel_token.bind(bound_callback)
                cancel_token.raise_if_cancelled()
            result = operation(adapter)
            success = True
            return result
        except Exception as exception:
            error = str(exception)
            raise
        finally:
            if cancel_token is not None and bound_callback is not None:
                cancel_token.unbind(bound_callback)
            elapsed = (time.perf_counter() - start) * 1000
            result.elapsed_ms = elapsed
            history_database = connection.database or (
                Path(connection.path).name if connection.kind == "SQLite" else "")
            with self._metadata() as db:
                db.execute("INSERT INTO history (id, connection_id, connection_name, query, timestamp, elapsed_ms, affected, success, error, database) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                           (uuid.uuid4().hex, connection_id, connection.name, description, self._now(),
                            elapsed, result.affected, int(success), error, history_database))

    def execute(self, connection_id, query, *, database=None, cancel_token=None):
        if cancel_token is None:
            def operation(adapter):
                return adapter.execute(query)
        else:
            def operation(adapter):
                return adapter.execute(query, cancel_token=cancel_token)
        return self._perform(connection_id, query,
                             operation,
                             database=database, cancel_token=cancel_token)

    def insert_row(self, connection_id, table, values, *, database=None):
        description = f"INSERT {history_table(table)} {history_value(values)}"
        return self._perform(connection_id, description, lambda adapter: adapter.insert_row(table, values), database=database)

    def update_row(self, connection_id, table, keys, values, *, database=None):
        description = f"UPDATE {history_table(table)} SET {history_value(values)} WHERE {history_value(keys)}"
        return self._perform(connection_id, description, lambda adapter: adapter.update_row(table, keys, values), database=database)

    def delete_rows(self, connection_id, table, keys_list, *, database=None):
        description = f"DELETE {history_table(table)} WHERE {history_value(keys_list)}"
        return self._perform(connection_id, description, lambda adapter: adapter.delete_rows(table, keys_list), database=database)

    def apply_grid_changes(self, connection_id, table, *, updates=(), inserts=(), deletes=(), database=None) -> QueryResult:
        updates, inserts, deletes = list(updates or ()), list(inserts or ()), list(deletes or ())
        connection = self._operation_connection(connection_id, database=database)
        if connection.kind in {'SQLite', 'PostgreSQL', 'MySQL', 'Oracle'}:
            description = grid_history_sql(connection.kind, table, updates, inserts, deletes)
        else:
            description = f"COMMIT GRID {history_table(table)}"

        def mutate(adapter):
            if adapter.connection.kind not in {"SQLite", "PostgreSQL", "MySQL", "Oracle"}:
                raise ValueError("Staged grid editing requires a SQL table.")
            return adapter.apply_grid_changes(table, updates=updates, inserts=inserts, deletes=deletes)

        return self._perform(connection_id, description, mutate, database=database)

    def list_history(self, connection_id=None, database=None):
        conditions, values = [], []
        if connection_id is not None:
            conditions.append("connection_id=?")
            values.append(str(connection_id))
        if database is not None:
            conditions.append("database=?")
            values.append(str(database))
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        with self._metadata() as db:
            rows = db.execute(f"SELECT * FROM history{where} ORDER BY rowid DESC LIMIT 1000", values).fetchall()
        return [HistoryEntry(**{**dict(row), "success": bool(row["success"])}) for row in rows]

    def load_console(self, connection_id, database=''):
        with self._metadata() as db:
            row = db.execute(
                "SELECT content FROM sql_consoles WHERE connection_id=? AND database=?",
                (str(connection_id), '' if database is None else str(database)),
            ).fetchone()
        return row["content"] if row else ''

    def save_console(self, connection_id, database, content):
        if not isinstance(content, str):
            raise ValueError("Console content must be text.")
        connection_id = str(connection_id)
        database = '' if database is None else str(database)
        with self._metadata() as db:
            db.execute(
                """INSERT INTO sql_consoles (connection_id, database, content, updated_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(connection_id, database) DO UPDATE SET
                       content=excluded.content, updated_at=excluded.updated_at""",
                (connection_id, database, content, self._now()),
            )

    def count_history(self):
        """Count the displayed history without loading full query/error bodies."""
        with self._metadata() as db:
            return db.execute("SELECT COUNT(*) FROM (SELECT 1 FROM history ORDER BY rowid DESC LIMIT 1000)").fetchone()[0]

    def clear_history(self, connection_id=None, database=None):
        conditions, values = [], []
        if connection_id is not None:
            conditions.append("connection_id=?")
            values.append(str(connection_id))
        if database is not None:
            conditions.append("database=?")
            values.append(str(database))
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        with self._metadata() as db:
            db.execute(f"DELETE FROM history{where}", values)

    def list_redis_search_history(self, connection_id, database):
        """Return recent searches for one Redis connection and database."""
        with self._metadata() as db:
            rows = db.execute(
                """SELECT id, connection_id, database, text, mode, created_at,
                          last_used_at, is_favorite
                   FROM redis_searches
                   WHERE connection_id=? AND database=?
                   ORDER BY is_favorite DESC, last_used_at DESC, rowid DESC""",
                (str(connection_id), str(database)),
            ).fetchall()
        return [{**dict(row), "is_favorite": bool(row["is_favorite"])} for row in rows]

    def record_redis_search(self, connection_id, database, text, mode):
        """Record one explicitly submitted search without storing result keys."""
        if mode not in {"prefix", "exact"}:
            raise ValueError("Redis search history mode must be prefix or exact.")
        if not isinstance(text, str):
            raise ValueError("Redis search history text must be a string.")
        if text == "":
            return None
        connection_id, database = str(connection_id), str(database)
        now = self._now()
        with self._metadata() as db:
            row = db.execute(
                "SELECT id, created_at, is_favorite FROM redis_searches "
                "WHERE connection_id=? AND database=? AND text=?",
                (connection_id, database, text),
            ).fetchone()
            if row is None:
                identifier = uuid.uuid4().hex
                db.execute(
                    """INSERT INTO redis_searches
                       (id, connection_id, database, text, mode, created_at, last_used_at, is_favorite)
                       VALUES (?, ?, ?, ?, ?, ?, ?, 0)""",
                    (identifier, connection_id, database, text, mode, now, now),
                )
                favorite = False
                created_at = now
            else:
                identifier = row["id"]
                db.execute(
                    "UPDATE redis_searches SET mode=?, last_used_at=? WHERE id=?",
                    (mode, now, identifier),
                )
                favorite = bool(row["is_favorite"])
                created_at = row["created_at"]
            # Keep at most 100 non-favorite rows globally. Favorites are never
            # selected for eviction and therefore may exceed that bound.
            db.execute(
                """DELETE FROM redis_searches
                   WHERE is_favorite=0 AND id IN (
                       SELECT id FROM redis_searches
                       WHERE is_favorite=0
                       ORDER BY last_used_at DESC, rowid DESC
                       LIMIT -1 OFFSET 100)"""
            )
            return {
                "id": identifier,
                "connection_id": connection_id,
                "database": database,
                "text": text,
                "mode": mode,
                "created_at": created_at,
                "last_used_at": now,
                "is_favorite": favorite,
            }

    def set_redis_search_favorite(self, identifier, favorite):
        if not isinstance(favorite, bool):
            raise ValueError("Redis search favorite must be a boolean.")
        with self._metadata() as db:
            cursor = db.execute("UPDATE redis_searches SET is_favorite=? WHERE id=?",
                                (int(favorite), str(identifier)))
        return cursor.rowcount > 0

    def delete_redis_search_history(self, identifier):
        with self._metadata() as db:
            cursor = db.execute("DELETE FROM redis_searches WHERE id=?", (str(identifier),))
        return cursor.rowcount > 0
