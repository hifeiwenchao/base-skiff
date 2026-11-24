"""Real database adapters. Optional network drivers are imported only on use."""
import csv
import importlib
import json
import math
import os
import re
import shlex
import socket
import sqlite3
import ssl
import tempfile
import time
import uuid
from contextlib import contextmanager
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from itertools import islice
from pathlib import Path
from threading import RLock

from .cancellation import QueryCancelled
from .models import (
    DEFAULT_PORTS,
    ColumnInfo,
    Connection,
    IndexInfo,
    QueryResult,
    RedisDatabaseInfo,
    RedisDatabaseList,
    RedisKeyContinuation,
    RedisKeyPage,
    RedisValue,
    TableInfo,
)

MAX_ROWS = 1000
MAX_REDIS_KEYS = 500
MAX_REDIS_SCAN_CALLS = 8
REDIS_KEY_SEARCH_MODES = ("prefix", "exact")
_MISSING = object()


def _redis_decode(value):
    """Decode UTF-8 only when lossless, including nested Redis responses.

    Redis bulk strings are arbitrary bytes. Keep undecodable payloads and
    identities intact rather than replacing bytes or guessing their encoding.
    """
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return value
    if isinstance(value, dict):
        return {_redis_decode(key): _redis_decode(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redis_decode(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redis_decode(item) for item in value)
    if isinstance(value, set):
        return {_redis_decode(item) for item in value}
    return value


def _redis_kind(value):
    # TYPE is a protocol token, unlike a key, field, member or stored value.
    return value.decode("ascii") if isinstance(value, bytes) else value


def _redis_bytes(value):
    return value.encode("utf-8") if isinstance(value, str) else value


def driver(name: str, package: str | None = None):
    try:
        return importlib.import_module(name)
    except ImportError as error:
        raise RuntimeError(f"Install the optional '{package or name}' driver to use this database.") from error


def result_from_cursor(cursor, limit=MAX_ROWS) -> QueryResult:
    if not cursor.description:
        return QueryResult(affected=max(cursor.rowcount, 0))
    columns = []
    used, suffixes = set(), {}
    for item in cursor.description:
        name = str(item[0])
        candidate = name
        suffix = suffixes.get(name, 2)
        while candidate in used:
            candidate = f"{name}_{suffix}"
            suffix += 1
        suffixes[name] = suffix
        used.add(candidate)
        columns.append(candidate)
    values = cursor.fetchmany(limit + 1)
    rows = [dict(zip(columns, row)) for row in values[:limit]]
    return QueryResult(columns, rows, affected=len(rows), total=len(rows), truncated=len(values) > limit)


def require_writable(connection: Connection):
    if connection.read_only:
        raise PermissionError("This connection is read-only.")


def redis_database_index(value):
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError("Redis database must be a nonnegative integer, for example 0.")
    try:
        index = int(value)
    except ValueError:
        raise ValueError("Redis database must be a nonnegative integer, for example 0.") from None
    if index < 0:
        raise ValueError("Redis database must be a nonnegative integer, for example 0.")
    return index


def _redis_key_search_database(connection, text, mode, continuation):
    if mode not in REDIS_KEY_SEARCH_MODES:
        raise ValueError("Redis search mode must be prefix or exact.")
    if not isinstance(text, str) and not (mode == "exact" and isinstance(text, bytes)):
        raise ValueError("Redis key search must be text.")
    database = str(redis_database_index(connection.database or 0))
    if continuation is not None and (
            not isinstance(continuation, RedisKeyContinuation) or mode != "prefix"
            or continuation.connection_id != connection.id
            or continuation.database != database or continuation.prefix != text):
        raise ValueError("Redis key continuation does not match this connection, database or search.")
    return database


class SQLAdapter:
    def __init__(self, connection: Connection, *, endpoint=None):
        self.connection = connection
        self.endpoint = endpoint
        self.kind = connection.kind
        self.placeholder = "?" if self.kind == "SQLite" else "%s"
        self._active_db = None
        self._active_db_lock = RLock()

    def quote(self, identifier: str) -> str:
        if not identifier or "\x00" in identifier:
            raise ValueError("Invalid database identifier.")
        char = "`" if self.kind == "MySQL" else '"'
        return char + identifier.replace(char, char * 2) + char

    def table_sql(self, table: str) -> str:
        if self.kind in {"PostgreSQL", "Oracle"} and "." in table:
            return ".".join(self.quote(part) for part in table.split(".", 1))
        return self.quote(table)

    @staticmethod
    def _custom_fragment(fragment, keyword, label):
        """Normalize a read-only WHERE/ORDER BY fragment from the grid toolbar."""
        if fragment is None:
            return ""
        if not isinstance(fragment, str):
            raise ValueError(f"Invalid {label} condition.")
        value = fragment.strip()
        if re.match(rf"^{re.escape(keyword)}\b", value, re.IGNORECASE):
            value = value[len(keyword):].strip()
        if not value:
            return ""
        if any(token in value for token in ("\x00", ";", "--", "/*", "*/")):
            raise ValueError(f"Invalid {label} condition: multiple statements and comments are not allowed.")
        return value

    def binds(self, count: int, offset=0) -> list[str]:
        if self.kind == "Oracle":
            return [f":{index + offset + 1}" for index in range(count)]
        return [self.placeholder] * count

    def _timeout(self):
        value = float(self.connection.query_timeout)
        if not math.isfinite(value) or value <= 0:
            raise ValueError("Query timeout must be a positive number of seconds.")
        return value

    def _open(self, cancel_token=None):
        if cancel_token is not None:
            cancel_token.raise_if_cancelled()
        connection = self.connection
        timeout = self._timeout()
        port = connection.port or DEFAULT_PORTS.get(self.kind, 0)
        if self.kind == "SQLite":
            path = Path(connection.path or connection.database).expanduser().resolve()
            if not path.is_file():
                raise FileNotFoundError(f"SQLite database does not exist: {path}")
            db = sqlite3.connect(path.as_uri() + ("?mode=ro" if connection.read_only else "?mode=rw"),
                                 uri=True, timeout=timeout)
            db.execute("PRAGMA foreign_keys = ON")
            deadline = time.monotonic() + timeout
            db.set_progress_handler(lambda: int((cancel_token is not None and cancel_token.cancelled)
                                                 or time.monotonic() > deadline), 5000)
            if connection.read_only:
                db.execute("PRAGMA query_only = ON")
                allowed = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION,
                           sqlite3.SQLITE_RECURSIVE, sqlite3.SQLITE_TRANSACTION}

                def authorize(action, first, second, _database, _trigger):
                    if action == sqlite3.SQLITE_PRAGMA:
                        return sqlite3.SQLITE_OK if first in {"table_info", "table_xinfo", "index_list", "index_info", "foreign_key_list"} else sqlite3.SQLITE_DENY
                    if action == sqlite3.SQLITE_FUNCTION and (second or "").lower() in {"load_extension", "writefile", "readfile"}:
                        return sqlite3.SQLITE_DENY
                    return sqlite3.SQLITE_OK if action in allowed else sqlite3.SQLITE_DENY

                db.set_authorizer(authorize)
            return db
        if self.kind == "PostgreSQL":
            route = {"hostaddr": self.endpoint.host} if self.endpoint else {}
            return driver("psycopg", "psycopg[binary]").connect(
                host=connection.host, port=self.endpoint.port if self.endpoint else port,
                dbname=connection.database, user=connection.username,
                password=connection.password, connect_timeout=8,
                sslmode="require" if connection.ssl else "prefer", options=f"-c statement_timeout={max(1, int(timeout * 1000))}", **route)
        if self.kind == "MySQL":
            route = {"defer_connect": True} if self.endpoint else {}
            db = driver("pymysql", "PyMySQL").connect(
                host=connection.host, port=port, database=connection.database, user=connection.username,
                password=connection.password, connect_timeout=8, read_timeout=timeout, write_timeout=timeout,
                charset="utf8mb4", autocommit=False,
                ssl=ssl.create_default_context() if connection.ssl else None, **route)
            if self.endpoint:
                # Keep PyMySQL's logical host for TLS SNI and hostname checking.
                sock = socket.create_connection((self.endpoint.host, self.endpoint.port), timeout=8)
                try:
                    db.connect(sock=sock)
                except BaseException:
                    sock.close()
                    raise
            return db
        if self.kind == "Oracle":
            module = driver("oracledb")
            host = self.endpoint.host if self.endpoint and not connection.ssl else connection.host
            target_port = self.endpoint.port if self.endpoint and not connection.ssl else port
            dsn = module.makedsn(host, target_port, service_name=connection.database)
            if connection.ssl:
                dsn = dsn.replace("PROTOCOL=TCP", "PROTOCOL=TCPS")
            route = {}
            if self.endpoint and connection.ssl:
                # Thin-mode HTTP CONNECT keeps the original TCPS identity.
                route = {"https_proxy": self.endpoint.host, "https_proxy_port": self.endpoint.proxy_port}
            db = module.connect(user=connection.username, password=connection.password, dsn=dsn,
                                tcp_connect_timeout=8, **route)
            db.call_timeout = max(1, int(timeout * 1000))
            return db
        raise ValueError(f"Unsupported SQL database: {self.kind}")

    def cancel(self):
        with self._active_db_lock:
            db = self._active_db
        if db is None:
            return
        try:
            if self.kind == 'SQLite':
                db.interrupt()
            elif self.kind in {'PostgreSQL', 'Oracle'}:
                db.cancel()
            else:
                # PyMySQL has no portable query-cancel call; closing the
                # worker's dedicated session interrupts its current command.
                db.close()
        except Exception:
            # The worker will surface the original cancellation state. A
            # driver may already have closed the session while racing cancel.
            pass

    @contextmanager
    def session(self, cancel_token=None):
        # Keep the no-argument hook compatible with lightweight adapters and
        # tests that replace _open; cancellation only needs the token-aware
        # call when a token was actually supplied.
        db = self._open(cancel_token) if cancel_token is not None else self._open()
        with self._active_db_lock:
            self._active_db = db
        try:
            if cancel_token is not None:
                cancel_token.raise_if_cancelled()
            if self.connection.read_only and self.kind != "SQLite":
                with db.cursor() as cursor:
                    cursor.execute("SET TRANSACTION READ ONLY" if self.kind != "MySQL" else "START TRANSACTION READ ONLY")
            yield db
            if cancel_token is not None:
                cancel_token.begin_commit()
            db.commit()
        except BaseException:
            # A timed-out MySQL socket may already be closed. Never let a
            # cleanup error such as ``InterfaceError(0, '')`` replace the
            # query/connection error that explains the failed operation.
            try:
                db.rollback()
            except BaseException:
                pass
            raise
        finally:
            with self._active_db_lock:
                if self._active_db is db:
                    self._active_db = None
            db.close()

    def _query(self, db, query: str, values=(), limit=MAX_ROWS):
        cursor = db.cursor()
        try:
            if self.kind == "Oracle":
                module = driver("oracledb")

                def exact_numbers(cursor, metadata):
                    if metadata.type_code == module.DB_TYPE_NUMBER:
                        # Convert Oracle's decimal representation directly,
                        # before a binary float can round significant digits.
                        return cursor.var(str, arraysize=cursor.arraysize, outconverter=Decimal)

                cursor.outputtypehandler = exact_numbers
            values = tuple(json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value for value in values)
            if self.kind == "SQLite":
                values = tuple(str(value) if isinstance(value, Decimal) else
                               value.isoformat() if isinstance(value, date) else value for value in values)
            if self.kind == "Oracle" and any(isinstance(value, datetime) for value in values):
                cursor.setinputsizes(*[
                    (module.DB_TYPE_TIMESTAMP_TZ if value.utcoffset() is not None else module.DB_TYPE_TIMESTAMP)
                    if isinstance(value, datetime) else None for value in values
                ])
            if self.kind == "PostgreSQL":
                # Extended protocol rejects multiple statements even with no
                # parameters, preventing COMMIT from escaping read-only mode.
                cursor.execute(query, values, prepare=True) if values else cursor.execute(query, prepare=True)
            else:
                cursor.execute(query, values) if values else cursor.execute(query)
            result = result_from_cursor(cursor, limit)
            if self.kind == "Oracle":
                lob_type = driver("oracledb").LOB
                for row in result.rows:
                    for name, value in row.items():
                        if isinstance(value, lob_type):
                            row[name] = value.read()
            return result
        finally:
            cursor.close()

    def test(self):
        self.execute("SELECT 1 FROM dual" if self.kind == "Oracle" else "SELECT 1")
        return f"Connected to {self.kind} successfully."

    def execute(self, query: str, cancel_token=None):
        if not query.strip():
            raise ValueError("Enter a SQL statement.")
        if self.connection.read_only and self.kind != "SQLite":
            # Database transactions also enforce this; the whitelist gives a clear early error.
            clean = re.sub(r"/\*.*?\*/|--[^\n]*", " ", query, flags=re.S).strip()
            if not re.match(r"^(SELECT|SHOW|DESCRIBE|DESC)\b", clean, re.I):
                raise PermissionError("Only SELECT, SHOW and DESCRIBE are allowed on a read-only connection.")
        try:
            if cancel_token is not None:
                cancel_token.raise_if_cancelled()
            with self.session(cancel_token) as db:
                result = self._query(db, query)
            if cancel_token is not None:
                cancel_token.raise_if_cancelled()
            return result
        except Exception:
            if cancel_token is not None and cancel_token.cancelled:
                raise QueryCancelled() from None
            raise

    def _select_statement(self, query):
        from .sql_editor import tokens
        query = query.strip()
        lexed = tokens(query)
        # MySQL/MariaDB executable comments are SQL, not inert annotations.
        # The shared editor lexer deliberately treats them as ordinary comments.
        if any(token.kind == "comment" and re.match(r"^/\*(?:!|m!)", token.text, re.I)
               for token in lexed):
            raise ValueError("Executable SQL comments are not allowed here.")
        if self.kind == "MySQL":
            # SQL modes change backslash quoting; MySQL also requires ASCII
            # whitespace after -- and does not support nested block comments,
            # PostgreSQL dollar strings, or bracket-quoted identifiers. Reject
            # these ambiguous forms rather than validate with another dialect.
            ambiguous = "\\" in query or any(
                (token.kind == "comment" and token.text.startswith("--")
                 and len(token.text) > 2 and ord(token.text[2]) > 32)
                or (token.kind == "comment" and token.text.startswith("/*")
                    and token.text.count("/*") > 1)
                or (token.kind == "string" and token.text.startswith("$"))
                or (token.kind == "identifier" and token.text.startswith("["))
                for token in lexed)
            if ambiguous:
                raise ValueError("This SELECT uses quoting or comments that cannot be safely validated for MySQL.")
        significant = [token for token in lexed if token.kind not in {"space", "comment"}]
        if significant and significant[-1].text == ";":
            query = query[:significant[-1].start].rstrip()
            significant.pop()
        if (not significant or significant[0].text.upper() != "SELECT"
                or any(token.kind == "symbol" and token.text == ";" for token in significant)
                or any(token.kind == "word" and token.text.upper() == "INTO" for token in significant)):
            raise ValueError("Choose a single SELECT statement.")
        return query

    def explain(self, query, cancel_token=None):
        if self.kind == "Oracle":
            raise ValueError("Oracle execution plans are not supported without writing a plan table.")
        query = self._select_statement(query)
        prefix = "EXPLAIN QUERY PLAN " if self.kind == "SQLite" else "EXPLAIN "
        original = self.connection
        try:
            self.connection = replace(original, read_only=True)
            with self.session(cancel_token) as db:
                return self._query(db, prefix + query)
        except Exception:
            if cancel_token is not None and cancel_token.cancelled:
                raise QueryCancelled() from None
            raise
        finally:
            self.connection = original

    def list_tables(self):
        with self.session() as db:
            if self.kind == "SQLite":
                rows = self._query(db, "SELECT name, type FROM sqlite_master WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%' ORDER BY name").rows
                result = []
                for row in rows:
                    # User databases may contain very large tables or costly
                    # views. Catalog discovery must not scan their contents.
                    count = (self._query(db, f"SELECT COUNT(*) AS n FROM {self.quote(row['name'])}").rows[0]["n"]
                             if self.connection.is_demo else None)
                    result.append(TableInfo(row["name"], row_count=count, kind=row["type"]))
                return result
            if self.kind == "PostgreSQL":
                rows = self._query(db, "SELECT c.relname AS name, n.nspname AS schema, GREATEST(c.reltuples::bigint,0) AS count, c.relkind FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE c.relkind IN ('r','v','m','p') AND n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname NOT LIKE 'pg_toast%' ORDER BY n.nspname,c.relname").rows
                return [TableInfo(row["name"], row["schema"], row["count"], "view" if row["relkind"] in ("v", "m") else "table") for row in rows]
            if self.kind == "MySQL":
                rows = self._query(db, "SELECT TABLE_NAME AS name, TABLE_ROWS AS count, TABLE_TYPE AS kind FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() ORDER BY TABLE_NAME").rows
                return [TableInfo(row["name"], row_count=row["count"], kind="view" if row["kind"] == "VIEW" else "table") for row in rows]
            rows = self._query(db, "SELECT table_name AS name, num_rows AS row_count, 'table' AS kind FROM user_tables UNION ALL SELECT view_name, NULL, 'view' FROM user_views ORDER BY name").rows
            return [TableInfo(row["NAME"], row_count=row["ROW_COUNT"], kind=row["KIND"]) for row in rows]

    def get_schema(self, table: str):
        with self.session() as db:
            if self.kind == "SQLite":
                rows = self._query(db, f"PRAGMA table_info({self.quote(table)})").rows
                if not rows:
                    raise ValueError(f"Table does not exist: {table}")
                return [ColumnInfo(row["name"], row["type"] or "TEXT", bool(row["pk"]),
                                   not bool(row["notnull"] or row["pk"]), row["dflt_value"]) for row in rows]
            if self.kind == "PostgreSQL":
                schema, name = table.split(".", 1) if "." in table else ("public", table)
                rows = self._query(db, "SELECT c.column_name, c.data_type, c.is_nullable, c.column_default, EXISTS (SELECT 1 FROM information_schema.table_constraints t JOIN information_schema.key_column_usage k ON t.constraint_name=k.constraint_name AND t.constraint_schema=k.constraint_schema AND t.table_name=k.table_name WHERE t.constraint_type='PRIMARY KEY' AND t.table_schema=c.table_schema AND t.table_name=c.table_name AND k.column_name=c.column_name) AS pk FROM information_schema.columns c WHERE c.table_schema=%s AND c.table_name=%s ORDER BY c.ordinal_position", (schema, name)).rows
                return [ColumnInfo(row["column_name"], row["data_type"], bool(row["pk"]), row["is_nullable"] == "YES", row["column_default"]) for row in rows]
            if self.kind == "MySQL":
                rows = self._query(db, "SELECT COLUMN_NAME AS name, COLUMN_TYPE AS type, COLUMN_KEY AS key_type, IS_NULLABLE AS nullable, COLUMN_DEFAULT AS def FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s ORDER BY ORDINAL_POSITION", (table,)).rows
                return [ColumnInfo(row["name"], row["type"], row["key_type"] == "PRI", row["nullable"] == "YES", row["def"]) for row in rows]
            name = table.split(".", 1)[-1]
            rows = self._query(db, "SELECT c.column_name, c.data_type, c.nullable, c.data_default, CASE WHEN p.column_name IS NULL THEN 0 ELSE 1 END AS pk FROM user_tab_columns c LEFT JOIN (SELECT k.column_name, k.table_name FROM user_cons_columns k JOIN user_constraints t ON k.constraint_name=t.constraint_name WHERE t.constraint_type='P') p ON p.table_name=c.table_name AND p.column_name=c.column_name WHERE c.table_name=:1 ORDER BY c.column_id", (name,)).rows
            return [ColumnInfo(row["COLUMN_NAME"], row["DATA_TYPE"], bool(row["PK"]), row["NULLABLE"] == "Y", row["DATA_DEFAULT"]) for row in rows]

    @staticmethod
    def _row_value(row, *names, default=None):
        for name in names:
            if name in row:
                return row[name]
            if name.upper() in row:
                return row[name.upper()]
            if name.lower() in row:
                return row[name.lower()]
        return default

    def list_indexes(self, table: str):
        """Return indexes in a stable, driver-independent shape."""
        with self.session() as db:
            if self.kind == "SQLite":
                indexes = []
                rows = self._query(db, f"PRAGMA index_list({self.quote(table)})").rows
                for row in rows:
                    name = self._row_value(row, "name")
                    if not name:
                        continue
                    columns = self._query(db, f"PRAGMA index_info({self.quote(name)})").rows
                    indexes.append(IndexInfo(
                        str(name),
                        tuple(str(self._row_value(item, "name", default="")) for item in columns
                              if self._row_value(item, "name") is not None),
                        bool(self._row_value(row, "unique", "is_unique", default=0)),
                        str(self._row_value(row, "origin", default="") or ""),
                    ))
                return indexes
            if self.kind == "PostgreSQL":
                schema, name = table.split(".", 1) if "." in table else ("public", table)
                rows = self._query(db, """
                    SELECT idx.relname AS name, i.indisunique AS is_unique,
                           CASE WHEN i.indisprimary THEN 'pk'
                                WHEN EXISTS (SELECT 1 FROM pg_constraint con
                                             WHERE con.conindid = i.indexrelid AND con.contype IN ('u','p'))
                                THEN 'u' ELSE '' END AS origin,
                           COALESCE(array_agg(att.attname ORDER BY ord.n)
                                    FILTER (WHERE att.attname IS NOT NULL), ARRAY[]::text[]) AS columns
                    FROM pg_class tbl
                    JOIN pg_namespace ns ON ns.oid = tbl.relnamespace
                    JOIN pg_index i ON i.indrelid = tbl.oid
                    JOIN pg_class idx ON idx.oid = i.indexrelid
                    LEFT JOIN LATERAL unnest(i.indkey) WITH ORDINALITY ord(attnum, n) ON TRUE
                    LEFT JOIN pg_attribute att ON att.attrelid = tbl.oid AND att.attnum = ord.attnum
                    WHERE ns.nspname = %s AND tbl.relname = %s
                    GROUP BY idx.relname, i.indisunique, i.indisprimary, i.indexrelid
                    ORDER BY idx.relname
                """, (schema, name)).rows
                return [IndexInfo(str(self._row_value(row, "name")),
                                  tuple(str(value) for value in (self._row_value(row, "columns", default=[]) or [])),
                                  bool(self._row_value(row, "is_unique", "unique", default=False)),
                                  str(self._row_value(row, "origin", default="") or ""))
                        for row in rows]
            if self.kind == "MySQL":
                rows = self._query(db, f"SHOW INDEX FROM {self.table_sql(table)}").rows
                grouped = {}
                for row in rows:
                    name = self._row_value(row, "Key_name", "key_name", "name")
                    if not name:
                        continue
                    entry = grouped.setdefault(str(name), {
                        "columns": [], "unique": not bool(self._row_value(row, "Non_unique", "non_unique", default=1))})
                    column = self._row_value(row, "Column_name", "column_name")
                    if column is not None:
                        entry["columns"].append((self._row_value(row, "Seq_in_index", "seq_in_index", default=0), str(column)))
                return [IndexInfo(name, tuple(column for _, column in sorted(entry["columns"])), entry["unique"],
                                  "pk" if name.upper() == "PRIMARY" else "")
                        for name, entry in sorted(grouped.items())]
            name = table.split(".", 1)[-1].upper()
            rows = self._query(db, """
                SELECT i.index_name AS name, i.uniqueness AS uniqueness,
                       (SELECT MAX(CASE WHEN con.constraint_type = 'P' THEN 'pk' ELSE 'u' END)
                        FROM user_constraints con WHERE con.index_name = i.index_name
                        AND con.constraint_type IN ('P', 'U')) AS origin,
                       LISTAGG(c.column_name, ',') WITHIN GROUP (ORDER BY c.column_position) AS columns
                FROM user_indexes i
                LEFT JOIN user_ind_columns c ON c.index_name = i.index_name
                WHERE i.table_name = :1
                GROUP BY i.index_name, i.uniqueness
                ORDER BY i.index_name
            """, (name,)).rows
            return [IndexInfo(str(self._row_value(row, "NAME", "name")),
                              tuple(value for value in str(self._row_value(row, "COLUMNS", "columns", default="") or "").split(",") if value),
                              str(self._row_value(row, "UNIQUENESS", "uniqueness", default="")) == "UNIQUE",
                              str(self._row_value(row, "ORIGIN", "origin", default="") or ""))
                    for row in rows]

    def _ddl(self, query):
        require_writable(self.connection)
        with self.session() as db:
            return self._query(db, query)

    def rename_table(self, table: str, new_name: str):
        if not isinstance(new_name, str) or not new_name.strip():
            raise ValueError("Enter a new table name.")
        new_name = new_name.strip()
        if "\x00" in new_name or "." in new_name:
            raise ValueError("Enter a single table name.")
        query = (f"RENAME TABLE {self.table_sql(table)} TO {self.quote(new_name)}"
                 if self.kind == "MySQL" else
                 f"ALTER TABLE {self.table_sql(table)} RENAME TO {self.quote(new_name)}")
        return self._ddl(query)

    def truncate_table(self, table: str):
        query = f"DELETE FROM {self.table_sql(table)}" if self.kind == "SQLite" else f"TRUNCATE TABLE {self.table_sql(table)}"
        return self._ddl(query)

    def add_column(self, table: str, name: str, data_type: str = "TEXT"):
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Enter a column name.")
        if not isinstance(data_type, str) or not re.match(r"^[A-Za-z][A-Za-z0-9_() ,]*(?: NOT NULL| NULL)?$", data_type.strip(), re.I):
            raise ValueError("Enter a valid column type.")
        return self._ddl(f"ALTER TABLE {self.table_sql(table)} ADD COLUMN {self.quote(name.strip())} {data_type.strip()}")

    def drop_column(self, table: str, name: str):
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Choose a column to delete.")
        return self._ddl(f"ALTER TABLE {self.table_sql(table)} DROP COLUMN {self.quote(name.strip())}")

    def drop_table(self, table: str):
        return self._ddl(f"DROP TABLE {self.table_sql(table)}")

    def create_index(self, table: str, name: str, columns, unique=False):
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Enter an index name.")
        names = [str(column).strip() for column in (columns or ()) if str(column).strip()]
        if not names:
            raise ValueError("Choose at least one index column.")
        if any("\x00" in column for column in names):
            raise ValueError("Invalid index column.")
        query = f"CREATE {'UNIQUE ' if unique else ''}INDEX {self.quote(name.strip())} ON {self.table_sql(table)} ({', '.join(self.quote(column) for column in names)})"
        return self._ddl(query)

    def drop_index(self, table: str, name: str):
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Choose an index to delete.")
        query = (f"DROP INDEX {self.quote(name.strip())} ON {self.table_sql(table)}"
                 if self.kind == "MySQL" else f"DROP INDEX {self.quote(name.strip())}")
        return self._ddl(query)

    def _table_selection(self, table, search="", sort_column="", descending=False,
                         where_clause="", order_by=""):
        columns = self.get_schema(table)
        names = [column.name for column in columns]
        if sort_column and sort_column not in names:
            raise ValueError("Unknown sort column.")
        custom_where = self._custom_fragment(where_clause, "WHERE", "WHERE")
        custom_order = self._custom_fragment(order_by, "ORDER BY", "ORDER BY")
        conditions, values = [], []
        if search:
            escaped = search.replace("!", "!!").replace("%", "!%").replace("_", "!_")
            casts = []
            for column in columns:
                name = column.name
                target = self.quote(name)
                if self.kind == "Oracle":
                    data_type = column.data_type.upper()
                    if data_type in {"BLOB", "BFILE", "RAW", "LONG RAW", "LONG"}:
                        continue
                    if data_type not in {"CLOB", "NCLOB"}:
                        target = f"TO_CHAR({target})"
                else:
                    target = f"CAST({target} AS {'CHAR' if self.kind == 'MySQL' else 'TEXT'})"
                casts.append(f"{target} LIKE {self.binds(1, len(values))[0]} ESCAPE '!' ")
                values.append(f"%{escaped}%")
            conditions.append("(" + " OR ".join(casts) + ")" if casts else "1=0")
        if custom_where:
            conditions.append(f"({custom_where})")
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        selected = sort_column or next((column.name for column in columns if column.primary_key), names[0] if names else "")
        order = f" ORDER BY {custom_order}" if custom_order else (f" ORDER BY {self.quote(selected)} {'DESC' if descending else 'ASC'}" if selected else "")
        return columns, where, order, values

    def fetch_rows(self, table, page=1, page_size=50, search="", sort_column="", descending=False,
                   where_clause="", order_by="", *, count_total=True, after_key=None, cancel_token=None):
        columns, where, order, values = self._table_selection(
            table, search, sort_column, descending, where_clause, order_by)
        page, page_size = max(1, int(page)), max(1, min(MAX_ROWS, int(page_size)))
        primary = [column for column in columns if column.primary_key]
        # Only the unambiguous default single-key order can use an opaque key.
        keyset = len(primary) == 1 and not sort_column and not order_by.strip() and not descending
        cursor_key = primary[0].name if keyset else None
        with (self.session(cancel_token) if cancel_token is not None else self.session()) as db:
            count, exact_count = None, bool(count_total)
            if count_total and count_total != "exact" and self.kind == "MySQL" and not where:
                estimate = self._query(
                    db,
                    "SELECT TABLE_ROWS AS count_rows FROM information_schema.TABLES "
                    "WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s",
                    (self.connection.database or "", table.split(".")[-1]),
                ).rows
                if estimate and estimate[0].get("count_rows") is not None:
                    count = int(estimate[0]["count_rows"])
                    exact_count = False
            if count_total and count is None:
                total = self._query(db, f"SELECT COUNT(*) AS count_rows FROM {self.table_sql(table)}{where}", values).rows[0]
                count = int(next(iter(total.values())))
            use_cursor = keyset and after_key is not None
            if use_cursor:
                where += (" AND " if where else " WHERE ") + f"{self.quote(cursor_key)} > {self.binds(1, len(values))[0]}"
                values = values + [after_key]
            # Fetch a lookahead row even for estimated totals. Never infer next
            # page availability from a catalog estimate.
            limit = page_size + 1
            offset = 0 if use_cursor else (page - 1) * page_size
            binds = self.binds(1 if use_cursor else 2, len(values))
            if self.kind == "Oracle":
                suffix = (f" FETCH FIRST {binds[0]} ROWS ONLY" if use_cursor else
                          f" OFFSET {binds[0]} ROWS FETCH NEXT {binds[1]} ROWS ONLY")
                pagination = [limit] if use_cursor else [offset, limit]
            else:
                suffix = f" LIMIT {binds[0]}" + ("" if use_cursor else f" OFFSET {binds[1]}")
                pagination = [limit] if use_cursor else [limit, offset]
            result = self._query(db, f"SELECT * FROM {self.table_sql(table)}{where}{order}{suffix}",
                                 values + pagination, limit=limit)
            result.has_more = len(result.rows) > page_size
            result.rows = result.rows[:page_size]
            result.affected = len(result.rows)
            result.truncated = False
            result.total = count if count is not None else (page - 1) * page_size + len(result.rows)
            result.total_known = exact_count
            result.next_cursor = result.rows[-1][cursor_key] if keyset and result.rows else None
            return result

    def export_csv(self, destination, *, table=None, query=None, search="", sort_column="",
                   descending=False, where_clause="", order_by="", cancel_token=None,
                   progress=None, batch_size=500):
        """Stream a consistent read-only SQL result to an atomically replaced CSV.

        Named PostgreSQL cursors and PyMySQL SSCursor avoid driver-side result
        buffering; SQLite and Oracle cursors fetch incrementally by default.
        """
        if (table is None) == (query is None):
            raise ValueError("Choose exactly one table or SELECT query to export.")
        values = []
        if table is not None:
            _columns, where, order, values = self._table_selection(
                table, search, sort_column, descending, where_clause, order_by)
            query = f"SELECT * FROM {self.table_sql(table)}{where}{order}"
        else:
            query = self._select_statement(query)
            # Driver single-statement enforcement plus read-only transactions
            # are the security boundary, including SELECT functions/CTEs.
        batch_size = max(1, min(5000, int(batch_size)))
        destination = Path(destination).expanduser().resolve()
        temp_path = None
        count = 0
        start = time.perf_counter()
        callback = self.cancel
        if cancel_token is not None:
            cancel_token.raise_if_cancelled()
            cancel_token.bind(callback)
        original = self.connection
        try:
            self.connection = replace(original, read_only=True)
            with self.session(cancel_token) as db:
                if self.kind == "PostgreSQL":
                    cursor = db.cursor(name=f"baseskiff_export_{uuid.uuid4().hex}")
                elif self.kind == "MySQL":
                    cursor = db.cursor(driver("pymysql", "PyMySQL").cursors.SSCursor)
                else:
                    cursor = db.cursor()
                try:
                    if self.kind == "Oracle":
                        module = driver("oracledb")
                        def exact_numbers(cur, metadata):
                            if metadata.type_code == module.DB_TYPE_NUMBER:
                                return cur.var(str, arraysize=cur.arraysize, outconverter=Decimal)
                        cursor.outputtypehandler = exact_numbers
                    cursor.arraysize = batch_size
                    cursor.execute(query, tuple(values)) if values else cursor.execute(query)
                    if not cursor.description:
                        raise ValueError("The SELECT query did not return rows.")
                    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8-sig", newline="",
                                                     dir=destination.parent, prefix=f".{destination.name}.",
                                                     suffix=".tmp", delete=False) as stream:
                        temp_path = Path(stream.name)
                        writer = csv.writer(stream)
                        writer.writerow([column[0] for column in cursor.description])
                        while True:
                            if cancel_token is not None:
                                cancel_token.raise_if_cancelled()
                            batch = cursor.fetchmany(batch_size)
                            if not batch:
                                break
                            if self.kind == "Oracle":
                                lob_type = module.LOB
                                batch = [[value.read() if isinstance(value, lob_type) else value
                                          for value in row] for row in batch]
                            writer.writerows(batch)
                            count += len(batch)
                            if progress is not None:
                                progress(count)
                        stream.flush()
                        os.fsync(stream.fileno())
                finally:
                    cursor.close()
            if cancel_token is not None:
                cancel_token.raise_if_cancelled()
            os.replace(temp_path, destination)
            temp_path = None
            return QueryResult(affected=count, total=count, elapsed_ms=(time.perf_counter() - start) * 1000)
        except Exception:
            if cancel_token is not None and cancel_token.cancelled:
                raise QueryCancelled() from None
            raise
        finally:
            self.connection = original
            if cancel_token is not None:
                cancel_token.unbind(callback)
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)

    def apply_grid_changes(self, table, *, updates=(), inserts=(), deletes=()):
        """Apply staged SQL grid edits atomically in one database transaction."""
        require_writable(self.connection)
        columns = self.get_schema(table)
        names = {column.name for column in columns}
        primary_key = {column.name for column in columns if column.primary_key}
        if not primary_key:
            raise ValueError("Grid editing requires a primary key.")
        updates, inserts, deletes = list(updates or ()), list(inserts or ()), list(deletes or ())

        def validate_keys(keys):
            if set(keys) != primary_key or any(keys.get(name) is None for name in primary_key):
                raise ValueError("Provide every primary key column, and only primary key columns.")

        for keys, values in updates:
            validate_keys(keys)
            if not values or not set(values) <= names or set(values) & primary_key:
                raise ValueError("Updates must contain non-primary-key columns from the table.")
        for values in inserts:
            if not set(values) <= names:
                raise ValueError("Unknown column in inserted row.")
        for keys in deletes:
            validate_keys(keys)

        affected = 0
        with self.session() as db:
            for keys, values in updates:
                assignments = list(values)
                binds = self.binds(len(assignments) + len(primary_key))
                query = f"UPDATE {self.table_sql(table)} SET " + ", ".join(
                    f"{self.quote(name)}={binds[index]}" for index, name in enumerate(assignments)
                )
                query += " WHERE " + " AND ".join(
                    f"{self.quote(name)}={binds[index + len(assignments)]}" for index, name in enumerate(primary_key)
                )
                result = self._query(db, query, tuple(values[name] for name in assignments) + tuple(keys[name] for name in primary_key))
                if result.affected != 1:
                    raise ValueError("A staged row changed or was deleted. Refresh before committing.")
                affected += result.affected
            for values in inserts:
                names_in_row = list(values)
                if not names_in_row:
                    query, parameters = f"INSERT INTO {self.table_sql(table)} DEFAULT VALUES", ()
                    if self.kind == "MySQL":
                        query = f"INSERT INTO {self.table_sql(table)} () VALUES ()"
                else:
                    query = f"INSERT INTO {self.table_sql(table)} ({', '.join(self.quote(name) for name in names_in_row)}) VALUES ({', '.join(self.binds(len(names_in_row)))})"
                    parameters = tuple(values[name] for name in names_in_row)
                affected += self._query(db, query, parameters).affected
            for keys in deletes:
                binds = self.binds(len(primary_key))
                query = f"DELETE FROM {self.table_sql(table)} WHERE " + " AND ".join(
                    f"{self.quote(name)}={binds[index]}" for index, name in enumerate(primary_key)
                )
                result = self._query(db, query, tuple(keys[name] for name in primary_key))
                if result.affected != 1:
                    raise ValueError("A staged row changed or was deleted. Refresh before committing.")
                affected += result.affected
        return QueryResult(affected=affected)

    def _writable_schema(self, table, values=None, keys=None):
        require_writable(self.connection)
        columns = self.get_schema(table)
        names = {column.name for column in columns}
        pk = {column.name for column in columns if column.primary_key}
        if not pk:
            raise ValueError("Grid editing requires a primary key. Use the query editor for this table.")
        if values is not None and not set(values) <= names:
            raise ValueError("Unknown column in row values.")
        if keys is not None:
            self._validate_row_keys(keys, pk)
        return pk

    @staticmethod
    def _validate_row_keys(keys, primary_key):
        if set(keys) != primary_key or any(value is None for value in keys.values()):
            raise ValueError("Provide every primary key column, and only primary key columns.")

    def insert_row(self, table, values):
        self._writable_schema(table, values=values)
        if values:
            query = f"INSERT INTO {self.table_sql(table)} ({', '.join(self.quote(key) for key in values)}) VALUES ({', '.join(self.binds(len(values)))})"
        else:
            query = f"INSERT INTO {self.table_sql(table)} DEFAULT VALUES"
            if self.kind == "MySQL":
                query = f"INSERT INTO {self.table_sql(table)} () VALUES ()"
            elif self.kind == "Oracle":
                column = self.get_schema(table)[0].name
                query = f"INSERT INTO {self.table_sql(table)} ({self.quote(column)}) VALUES (DEFAULT)"
        with self.session() as db:
            return self._query(db, query, tuple(values.values()))

    def update_row(self, table, keys, values):
        self._writable_schema(table, values=values, keys=keys)
        if not values:
            return QueryResult()
        binds = self.binds(len(values) + len(keys))
        assignments = ", ".join(f"{self.quote(key)}={binds[index]}" for index, key in enumerate(values))
        where = " AND ".join(f"{self.quote(key)}={binds[index + len(values)]}" for index, key in enumerate(keys))
        with self.session() as db:
            return self._query(db, f"UPDATE {self.table_sql(table)} SET {assignments} WHERE {where}", tuple(values.values()) + tuple(keys.values()))

    def delete_rows(self, table, keys_list):
        primary_key = self._writable_schema(table)
        for keys in keys_list:
            self._validate_row_keys(keys, primary_key)
        affected = 0
        with self.session() as db:
            for keys in keys_list:
                where = " AND ".join(f"{self.quote(key)}={bind}" for key, bind in zip(keys, self.binds(len(keys))))
                affected += self._query(db, f"DELETE FROM {self.table_sql(table)} WHERE {where}", tuple(keys.values())).affected
        return QueryResult(affected=affected)


def document_result(documents, *, total=None, truncated=False):
    documents = list(documents)
    columns = list(dict.fromkeys(key for document in documents for key in document))
    return QueryResult(columns=columns, rows=documents, affected=len(documents),
                       total=len(documents) if total is None else total, truncated=truncated)


class MongoAdapter:
    def __init__(self, connection, *, endpoint=None):
        self.connection = connection
        self.endpoint = endpoint

    @contextmanager
    def session(self):
        c = self.connection
        route = {"directConnection": True} if self.endpoint else {}
        if self.endpoint and c.ssl:
            from .routing import mongo_tls_pool
            route["_pool_class"] = mongo_tls_pool(c.host)
        client = driver("pymongo", "pymongo").MongoClient(
            host=self.endpoint.host if self.endpoint else c.host,
            port=self.endpoint.port if self.endpoint else c.port or 27017, username=c.username or None,
            password=c.password or None, tls=c.ssl, serverSelectionTimeoutMS=8000,
            connectTimeoutMS=8000, socketTimeoutMS=max(1, int(c.query_timeout * 1000)), **route)
        try:
            yield client[c.database or "test"]
        finally:
            client.close()

    def test(self):
        with self.session() as db:
            db.command("ping")
        return "Connected to MongoDB successfully."

    def list_tables(self):
        with self.session() as db:
            return [TableInfo(name, row_count=db[name].estimated_document_count(maxTimeMS=max(1, int(self.connection.query_timeout * 1000))), kind="collection")
                    for name in sorted(db.list_collection_names())]

    def get_schema(self, table):
        with self.session() as db:
            documents = list(db[table].find().limit(100).max_time_ms(max(1, int(self.connection.query_timeout * 1000))))
        names = list(dict.fromkeys(["_id"] + [key for document in documents for key in document]))
        return [ColumnInfo(name, "JSON" if any(isinstance(document.get(name), (dict, list)) for document in documents) else
                           "ObjectId" if name == "_id" else type(next((document[name] for document in documents if name in document), "")).__name__,
                           name == "_id", name != "_id") for name in names]

    def fetch_rows(self, table, page=1, page_size=50, search="", sort_column="", descending=False,
                   where_clause="", order_by=""):
        page, page_size = max(1, int(page)), max(1, min(MAX_ROWS, int(page_size)))
        query = {}
        if search:
            columns = self.get_schema(table)
            query = {"$or": [{column.name: {"$regex": re.escape(search), "$options": "i"}}
                               for column in columns if column.name != "_id"]} if len(columns) > 1 else {"_id": search}
        with self.session() as db:
            cursor = db[table].find(query).sort(sort_column or "_id", -1 if descending else 1).skip((page - 1) * page_size).limit(page_size).max_time_ms(max(1, int(self.connection.query_timeout * 1000)))
            return document_result(cursor, total=db[table].count_documents(query, maxTimeMS=max(1, int(self.connection.query_timeout * 1000))))

    def execute(self, query):
        try:
            command = driver("bson.json_util", "pymongo").loads(query)
        except (ValueError, TypeError) as error:
            raise ValueError("MongoDB queries must be JSON command objects, for example {\"find\":\"customers\",\"filter\":{}}.") from error
        if not isinstance(command, dict) or not command:
            raise ValueError("Enter a MongoDB JSON command object.")
        action = next(iter(command))
        if self.connection.read_only:
            if action not in {"find", "aggregate", "count", "distinct", "ping", "listCollections", "listIndexes"}:
                raise PermissionError("This MongoDB command is not allowed on a read-only connection.")
            if action == "aggregate" and any("$out" in stage or "$merge" in stage for stage in command.get("pipeline", [])):
                raise PermissionError("Aggregation writes are blocked on a read-only connection.")
        with self.session() as db:
            if action in {'insertOne', 'updateOne', 'deleteOne'}:
                collection = command[action]
                if not isinstance(collection, str) or not collection:
                    raise ValueError('Specify a MongoDB collection name.')
                if action == 'insertOne':
                    document = command.get('document')
                    if not isinstance(document, dict):
                        raise ValueError('insertOne requires a document object.')
                    response = db[collection].insert_one(document)
                    result = document_result([{'inserted_id': response.inserted_id}])
                    result.affected = 1
                    return result
                selector = command.get('filter')
                if not isinstance(selector, dict):
                    raise ValueError(f'{action} requires a filter object.')
                if action == 'updateOne':
                    update = command.get('update')
                    if not isinstance(update, (dict, list)):
                        raise ValueError('updateOne requires an update object or pipeline.')
                    response = db[collection].update_one(selector, update, upsert=command.get('upsert', False))
                    result = document_result([{'matched_count': 0 if response.did_upsert else response.matched_count,
                                               'modified_count': response.modified_count,
                                               'upserted_id': response.upserted_id}])
                    result.affected = response.modified_count + int(response.did_upsert)
                    return result
                response = db[collection].delete_one(selector)
                result = document_result([{'deleted_count': response.deleted_count}])
                result.affected = response.deleted_count
                return result
            if action == "find":
                cursor = db[command["find"]].find(command.get("filter", {}), command.get("projection"))
                if command.get("sort"):
                    cursor = cursor.sort(list(command["sort"].items()))
                cursor = cursor.skip(max(0, int(command.get("skip", 0)))).limit(min(MAX_ROWS + 1, max(1, int(command.get("limit", MAX_ROWS + 1)))))
                documents = list(cursor.max_time_ms(max(1, int(self.connection.query_timeout * 1000))))
                return document_result(documents[:MAX_ROWS], truncated=len(documents) > MAX_ROWS)
            if action == "aggregate":
                pipeline = list(command.get("pipeline", []))
                if not any("$out" in stage or "$merge" in stage for stage in pipeline):
                    pipeline.append({"$limit": MAX_ROWS + 1})
                documents = list(db[command["aggregate"]].aggregate(pipeline, maxTimeMS=max(1, int(self.connection.query_timeout * 1000))))
                return document_result(documents[:MAX_ROWS], truncated=len(documents) > MAX_ROWS)
            response = db.command(command, maxTimeMS=max(1, int(self.connection.query_timeout * 1000)))
            # Write commands can return ok:1 together with writeErrors. Treat
            # these as failed operations, just as PyMongo's CRUD methods do.
            error = next(iter(response.get('writeErrors', [])), None) or response.get('writeConcernError')
            if error:
                raise driver('pymongo.errors').OperationFailure(
                    error.get('errmsg', 'MongoDB write failed.'), code=error.get('code'), details=response)
            if "cursor" in response:
                # Only metadata commands reach here; explicitly kill any unconsumed server cursor.
                cursor = response["cursor"]
                documents = cursor.get("firstBatch", [])
                if cursor.get("id"):
                    db.command("killCursors", cursor["ns"].split(".", 1)[-1], cursors=[cursor["id"]])
                return document_result(documents[:MAX_ROWS], truncated=bool(cursor.get("id")) or len(documents) > MAX_ROWS)
            result = document_result([response])
            if action in {'insert', 'update', 'delete'}:
                result.affected = int(response.get('nModified', response.get('n', 0))) + len(response.get('upserted', []))
            return result

    def _key(self, keys):
        if set(keys) != {"_id"} or keys["_id"] is None:
            raise ValueError("MongoDB editing requires the _id primary key.")
        value = keys["_id"]
        # Displayed ObjectId values remain real ObjectIds in the grid model.
        return {"_id": value}

    def insert_row(self, table, values):
        require_writable(self.connection)
        with self.session() as db:
            db[table].insert_one(dict(values))
        return QueryResult(affected=1)

    def update_row(self, table, keys, values):
        require_writable(self.connection)
        values = {key: value for key, value in values.items() if key != "_id"}
        if not values:
            return QueryResult()
        with self.session() as db:
            result = db[table].update_one(self._key(keys), {"$set": values})
        return QueryResult(affected=result.modified_count)

    def delete_rows(self, table, keys_list):
        require_writable(self.connection)
        selectors = [self._key(keys) for keys in keys_list]
        if not selectors:
            return QueryResult()
        with self.session() as db:
            result = db[table].delete_many({"$or": selectors})
        return QueryResult(affected=result.deleted_count)


class RedisAdapter:
    READ_COMMANDS = {"GET", "MGET", "EXISTS", "TYPE", "TTL", "PTTL", "STRLEN", "GETRANGE", "HGET", "HGETALL", "HMGET", "HKEYS", "HVALS", "HLEN", "HEXISTS", "LRANGE", "LINDEX", "LLEN", "SMEMBERS", "SCARD", "SISMEMBER", "SRANDMEMBER", "ZRANGE", "ZRANGEBYSCORE", "ZREVRANGE", "ZCARD", "ZSCORE", "ZRANK", "SCAN", "HSCAN", "SSCAN", "ZSCAN", "PING", "DBSIZE", "INFO", "TIME", "XRANGE", "XREVRANGE", "XLEN", "DUMP", "JSON.GET"}

    def __init__(self, connection, *, endpoint=None):
        self.connection = connection
        self.endpoint = endpoint

    @contextmanager
    def session(self, *, database=None):
        c = self.connection
        index = redis_database_index((c.database or 0) if database is None else database)
        module = driver("redis")
        options = dict(host=c.host, port=c.port or 6379, db=index,
                       username=c.username or None, password=c.password or None,
                       # Redis < 6 has no HELLO; RESP2 also supports modern ACL authentication.
                       protocol=2, decode_responses=False, socket_connect_timeout=8, socket_timeout=float(c.query_timeout))
        pool = None
        if self.endpoint:
            from .routing import redis_connection_class
            pool = module.ConnectionPool(connection_class=redis_connection_class(module, self.endpoint, c.ssl),
                                         **options)
            client = module.Redis(connection_pool=pool)
        else:
            client = module.Redis(ssl=c.ssl, **options)
        try:
            yield client
        except module.RedisError as error:
            # Legacy servers/proxies can echo AUTH arguments in their errors.
            # Preserve the driver's exception type for authentication handling.
            if c.password and c.password in str(error):
                error.args = (str(error).replace(c.password, "[redacted]"),)
            raise
        finally:
            client.close()
            if pool is not None:
                pool.disconnect()

    def test(self):
        with self.session() as client:
            client.ping()
        return "Connected to Redis successfully."

    def list_tables(self):
        return self.search_keys().keys

    def list_databases(self):
        module = driver("redis")
        count, keyspace = None, None
        # Discovery must remain possible if a previously selected database is
        # no longer available after the server's configuration changed.
        with self.session(database=0) as client:
            try:
                configuration = _redis_decode(client.config_get("databases"))
                try:
                    configured_count = int(configuration.get("databases", ""))
                    if configured_count > 0:
                        count = configured_count
                except (TypeError, ValueError):
                    pass
            except module.AuthenticationError:
                raise
            except module.ResponseError:
                pass
            try:
                keyspace = _redis_decode(client.info("keyspace"))
            except module.AuthenticationError:
                raise
            except module.ResponseError:
                pass
            try:
                cluster = _redis_decode(client.info("cluster"))
                if str(cluster.get("cluster_enabled", 0)) == "1":
                    # Cluster nodes may still report the standalone databases
                    # setting, but SELECT supports only DB0 in cluster mode.
                    count = 1
            except module.AuthenticationError:
                raise
            except module.ResponseError:
                pass
        counts = {}
        for name, details in (keyspace or {}).items():
            if not re.fullmatch(r"db\d+", name) or not isinstance(details, dict):
                continue
            try:
                value = int(details["keys"])
                if value >= 0:
                    counts[int(name[2:])] = value
            except (KeyError, TypeError, ValueError):
                continue
        if count is not None:
            indices = range(count)
        else:
            indices = set(range(16)) | counts.keys()
            try:
                indices.add(redis_database_index(self.connection.database or 0))
            except ValueError:
                pass
            indices = sorted(indices)
        return RedisDatabaseList(
            [RedisDatabaseInfo(index, counts.get(index, 0) if keyspace is not None else None)
             for index in indices], complete=count is not None)

    @staticmethod
    def _literal_pattern(text):
        return "".join("\\" + char if char in "\\*?[]" else char for char in text)

    def search_keys(self, text="", mode="prefix", limit=MAX_REDIS_KEYS, *, continuation=None):
        database = _redis_key_search_database(self.connection, text, mode, continuation)
        limit = max(1, min(MAX_REDIS_KEYS, int(limit)))
        with self.session() as client:
            if mode == "exact":
                # EXISTS treats metacharacters and even the empty key literally.
                return RedisKeyPage([TableInfo(_redis_decode(text), kind=_redis_kind(client.type(text)))] if client.exists(text) else [])
            pattern = self._literal_pattern(text) + "*"
            seen = set(continuation.seen) if continuation is not None else set()
            pending = list(continuation.pending) if continuation is not None else []
            cursor = continuation.cursor if continuation is not None else 0
            selected, pending = pending[:limit], pending[limit:]
            calls = 0
            # SCAN COUNT is only a hint. Retain every distinct overflow key and
            # the cursor so the next expansion continues without losing keys.
            # Bound SCAN calls as well as results, including sparse searches.
            while len(selected) < limit and calls < MAX_REDIS_SCAN_CALLS and (
                    cursor != 0 or (continuation is None and calls == 0)):
                cursor, found = client.scan(cursor, match=pattern, count=limit)
                calls += 1
                for key in found:
                    key = _redis_decode(key)
                    if key in seen:
                        continue
                    seen.add(key)
                    if len(selected) < limit:
                        selected.append(key)
                    else:
                        pending.append(key)
            next_page = RedisKeyContinuation(
                self.connection.id, database, text, int(cursor), tuple(pending), frozenset(seen)
            ) if cursor != 0 or pending else None
            selected.sort(key=_redis_bytes)
            kinds = []
            if selected:
                with client.pipeline(transaction=False) as pipeline:
                    for key in selected:
                        pipeline.type(key)
                    kinds = pipeline.execute()
            return RedisKeyPage([TableInfo(key, kind=_redis_kind(kind)) for key, kind in zip(selected, kinds)],
                                truncated=next_page is not None, continuation=next_page)

    def get_schema(self, table):
        with self.session() as client:
            kind = _redis_kind(client.type(table))
            editable = kind in {"none", "string", "hash", "list", "set", "zset"} and self._value_size(client, table, kind) <= MAX_ROWS
        return [ColumnInfo("key", "TEXT", primary_key=editable, nullable=False),
                ColumnInfo("type", "TEXT", nullable=False, default="string"),
                ColumnInfo("value", "JSON", nullable=False, default="")]

    @staticmethod
    def _value_size(client, key, kind):
        operation = {"hash": "hlen", "list": "llen", "set": "scard", "zset": "zcard", "stream": "xlen"}.get(kind)
        return int(getattr(client, operation)(key)) if operation else 1

    def _read_value(self, client, key, kind):
        if kind == "string":
            return _redis_decode(client.get(key))
        if kind == "hash":
            return _redis_decode(dict(islice(client.hscan_iter(key, count=100), MAX_ROWS)))
        if kind == "list":
            return _redis_decode(client.lrange(key, 0, MAX_ROWS - 1))
        if kind == "set":
            values = []
            for value in client.sscan_iter(key, count=100):
                values.append(value)
                if len(values) >= MAX_ROWS:
                    break
            return _redis_decode(values)
        if kind == "zset":
            return _redis_decode(dict(client.zrange(key, 0, MAX_ROWS - 1, withscores=True)))
        if kind == "stream":
            return _redis_decode(client.xrange(key, count=MAX_ROWS))
        if kind.lower() in {"rejson-rl", "rejson", "json"}:
            return _redis_decode(client.execute_command("JSON.GET", key))
        if kind != "none":
            # Module-defined types have no common value command. DUMP is a
            # lossless, read-only serialized snapshot, not an editable value.
            return client.dump(key)
        return None

    def read_key(self, key):
        with self.session() as client:
            kind = _redis_kind(client.type(key))
            value = self._read_value(client, key, kind)
            if kind == "none":
                size = 0
            elif kind == "string":
                size = client.strlen(key)
            elif kind in {"hash", "list", "set", "zset", "stream"}:
                size = self._value_size(client, key, kind)
            else:
                size = len(_redis_bytes(value)) if isinstance(value, (str, bytes)) else 0
            return RedisValue(_redis_decode(key), kind, value, size, client.pttl(key),
                              kind in {"hash", "list", "set", "zset", "stream"} and size > MAX_ROWS)

    @staticmethod
    def _entry_text(payload, name):
        value = payload.get(name, _MISSING)
        if not isinstance(value, (str, bytes)):
            raise ValueError(f"Redis {name} must be a string or bytes.")
        return _redis_decode(value)

    @staticmethod
    def _entry_value(payload, name):
        """Return an entry value and whether it must be kept as raw bytes.

        The regular ``value``/``expected`` fields retain the historical UTF-8
        decoding behavior.  Serialized previews use explicit ``*_bytes``
        fields so a valid UTF-8 byte sequence cannot be silently converted to
        text before Redis receives it again.
        """
        raw_name = f"{name}_bytes"
        if raw_name in payload:
            value = payload[raw_name]
            if not isinstance(value, (bytes, bytearray, memoryview)):
                raise ValueError(f"Redis {raw_name} must be bytes.")
            return bytes(value), True
        return RedisAdapter._entry_text(payload, name), False

    @staticmethod
    def _entry_score(value, *, allow_infinity=False):
        try:
            if isinstance(value, bool):
                raise ValueError()
            score = float(value)
        except (TypeError, ValueError):
            raise ValueError("Redis sorted-set scores must be finite numbers.") from None
        if math.isnan(score) or (not allow_infinity and not math.isfinite(score)):
            raise ValueError("Redis sorted-set scores must be finite numbers.")
        return score

    def mutate_entry(self, key, kind, action, payload):
        """Change one entry while watching its key, without replacing collections."""
        require_writable(self.connection)
        if not isinstance(key, (str, bytes)):
            raise ValueError("A Redis key must be a string or bytes.")
        if kind not in {"string", "hash", "list", "set", "zset"}:
            raise ValueError("Entry editing supports Redis string, hash, list, set and zset values.")
        if action not in {"add", "update", "delete"} or not isinstance(payload, dict):
            raise ValueError("Choose add, update or delete and supply entry values.")
        entry, value, expected = None, None, None
        expected_raw = False
        if kind == "set" and action == "add":
            entry_name = "entry" if "entry" in payload or "entry_bytes" in payload else "value"
            entry, _ = self._entry_value(payload, entry_name)
        elif kind in {"hash", "set", "zset"}:
            entry, _ = self._entry_value(payload, "entry")
        elif kind == "list" and action != "add":
            entry = payload.get("entry")
            if isinstance(entry, bool) or not isinstance(entry, int) or entry < 0:
                raise ValueError("A Redis list index must be a nonnegative integer.")
        if action != "delete" and not (kind == "set" and action == "add"):
            if kind == "zset":
                value = self._entry_score(payload.get("value"))
            else:
                value, _ = self._entry_value(payload, "value")
        if action != "add" and kind != "set":
            if "expected" not in payload and "expected_bytes" not in payload:
                raise ValueError("The original entry value is required. Refresh before saving again.")
            if kind == "zset":
                expected = self._entry_score(payload["expected"], allow_infinity=True)
            else:
                expected, expected_raw = self._entry_value(payload, "expected")
        watch_error = driver("redis").WatchError
        try:
            with self.session() as client, client.pipeline(transaction=True) as transaction:
                transaction.watch(key)
                original_kind = _redis_kind(transaction.type(key))
                if original_kind != kind and not (original_kind == "none" and action == "add"):
                    raise ValueError("This Redis key is missing or its type changed. Refresh before saving again.")
                if kind == "string":
                    exists = original_kind != "none"
                    current = transaction.get(key) if expected_raw and exists else (_redis_decode(transaction.get(key)) if exists else None)
                elif kind == "hash":
                    current = transaction.hget(key, entry) if expected_raw else _redis_decode(transaction.hget(key, entry))
                    exists = current is not None
                elif kind == "list":
                    current = (transaction.lindex(key, entry) if expected_raw else _redis_decode(transaction.lindex(key, entry))) if action != "add" else None
                    exists = current is not None
                elif kind == "set":
                    exists = transaction.sismember(key, entry)
                    current = entry
                else:
                    current = transaction.zscore(key, entry)
                    exists = current is not None
                if action == "add" and kind != "list" and exists:
                    raise ValueError("This Redis entry already exists. Refresh before saving again.")
                if expected_raw and current is not None:
                    current = _redis_bytes(current)
                if action != "add" and (not exists or (kind != "set" and current != expected)):
                    raise ValueError("This Redis entry is missing or changed. Refresh before saving again.")
                if kind == "set" and action == "update" and value != entry and transaction.sismember(key, value):
                    raise ValueError("The replacement Redis set member already exists.")
                marker = None
                if kind == "list" and action == "delete":
                    if transaction.llen(key) > MAX_ROWS:
                        raise ValueError(f"This list is truncated to {MAX_ROWS} entries; deletion is disabled.")
                    # LSET + LREM removes precisely the selected position, including
                    # duplicate values, without replacing or deleting the full list.
                    values = _redis_decode(transaction.lrange(key, 0, MAX_ROWS - 1))
                    marker = f"__baseskiff_entry__:{uuid.uuid4().hex}"
                    while marker in values:
                        marker = f"__baseskiff_entry__:{uuid.uuid4().hex}"
                deadline = None
                if original_kind != "none":
                    # Sample both values together on the server: host clocks
                    # may differ, and a relative TTL sampled before EXEC would
                    # extend the lifetime by any network or scheduling delay.
                    with client.pipeline(transaction=False) as timing:
                        timing.time()
                        timing.pttl(key)
                        (seconds, microseconds), ttl = timing.execute()
                    if ttl >= 0:
                        deadline = seconds * 1000 + microseconds // 1000 + ttl
                else:
                    ttl = transaction.pttl(key)
                if original_kind != "none" and ttl == -2:
                    raise ValueError("This Redis key expired while editing. Refresh before saving again.")
                if ttl == 0:
                    raise ValueError("This Redis key expired while editing. Refresh before saving again.")
                transaction.multi()
                if kind == "string":
                    if action == "delete":
                        transaction.delete(key)
                    else:
                        # SET KEEPTTL needs Redis 6; absolute PEXPIREAT also works
                        # on Redis 5 and cannot extend an existing expiration.
                        transaction.set(key, value)
                elif kind == "hash":
                    transaction.hdel(key, entry) if action == "delete" else transaction.hset(key, entry, value)
                elif kind == "list":
                    if action == "add":
                        transaction.rpush(key, value)
                    elif action == "update":
                        transaction.lset(key, entry, value)
                    else:
                        transaction.lset(key, entry, marker)
                        transaction.lrem(key, 1, marker)
                elif kind == "set":
                    if action == "add":
                        transaction.sadd(key, entry)
                    elif action == "delete":
                        transaction.srem(key, entry)
                    elif value != entry:
                        # Adding first preserves the key and its exact expiry
                        # even when the old member was the only member.
                        transaction.sadd(key, value)
                        transaction.srem(key, entry)
                else:
                    transaction.zrem(key, entry) if action == "delete" else transaction.zadd(key, {entry: value})
                if deadline is not None:
                    # Redis < 6.0.9 does not invalidate WATCH on natural expiry.
                    # Even if a write recreates the expired key, the original
                    # absolute deadline removes it before EXEC is observable.
                    transaction.pexpireat(key, deadline)
                transaction.execute()
        except watch_error:
            raise ValueError("This Redis key changed concurrently. Refresh before saving again.") from None
        return QueryResult(affected=1)

    def set_key_ttl(self, key, seconds, *, expected_ttl_ms=None):
        """Set or remove one key's expiry without changing its value."""
        require_writable(self.connection)
        if not isinstance(key, (str, bytes)):
            raise ValueError("A Redis key must be a string or bytes.")
        if seconds is not None and (isinstance(seconds, bool) or not isinstance(seconds, int) or seconds <= 0):
            raise ValueError("TTL must be a positive number of seconds or no expiry.")
        if expected_ttl_ms is not None and (
                isinstance(expected_ttl_ms, bool) or not isinstance(expected_ttl_ms, int)):
            raise ValueError("The original TTL is invalid. Refresh before changing the expiry.")
        watch_error = driver("redis").WatchError
        try:
            with self.session() as client, client.pipeline(transaction=True) as transaction:
                transaction.watch(key)
                kind = _redis_kind(transaction.type(key))
                current_ttl = int(transaction.pttl(key))
                if kind == "none" or current_ttl == -2:
                    raise ValueError("This Redis key is missing or expired. Refresh before changing the expiry.")
                if expected_ttl_ms is not None:
                    current_persistent = current_ttl == -1
                    expected_persistent = expected_ttl_ms == -1
                    if current_persistent != expected_persistent:
                        raise ValueError("This Redis key expiry changed. Refresh before changing the expiry.")
                transaction.multi()
                if seconds is None:
                    transaction.persist(key)
                else:
                    transaction.expire(key, seconds)
                result = transaction.execute()
                if not result or not int(result[0]):
                    raise ValueError("This Redis key expired while changing the expiry. Refresh and try again.")
        except watch_error:
            raise ValueError("This Redis key changed concurrently. Refresh before changing the expiry.") from None
        return QueryResult(affected=1)

    def fetch_rows(self, table, page=1, page_size=50, search="", sort_column="", descending=False,
                   where_clause="", order_by=""):
        with self.session() as client:
            kind = _redis_kind(client.type(table))
            if kind == "none":
                return document_result([])
            row = {"key": table, "type": kind, "value": self._read_value(client, table, kind)}
            truncated = self._value_size(client, table, kind) > MAX_ROWS
            if search and search.lower() not in str(row).lower():
                return document_result([])
            return document_result([row] if int(page) == 1 else [], total=1, truncated=truncated)

    def execute(self, query):
        args = shlex.split(query)
        if not args:
            raise ValueError("Enter a Redis command, for example GET my-key.")
        command = args[0].upper()
        if command in {"SUBSCRIBE", "PSUBSCRIBE", "SSUBSCRIBE", "MONITOR", "BLPOP", "BRPOP", "BRPOPLPUSH", "BZPOPMIN", "BZPOPMAX", "XREAD", "XREADGROUP", "DEBUG", "SHUTDOWN", "CLIENT"}:
            raise ValueError("Interactive and blocking Redis commands are not supported by the query editor.")
        if self.connection.read_only and command not in self.READ_COMMANDS:
            raise PermissionError("This Redis command is not allowed on a read-only connection.")
        with self.session() as client:
            response = client.execute_command(*args)
            truncated = False
            # Sequences retain their order when decoded, so discard invisible
            # top-level rows before converting their possibly large payloads.
            if isinstance(response, (list, tuple)):
                truncated = len(response) > MAX_ROWS
                response = response[:MAX_ROWS]
            response = _redis_decode(response)
        if isinstance(response, dict):
            rows = [{"key": key, "value": value} for key, value in list(response.items())[:MAX_ROWS]]
            return document_result(rows, truncated=len(response) > MAX_ROWS)
        if isinstance(response, (list, tuple, set)):
            values = list(response)
            return document_result([{"value": value} for value in values[:MAX_ROWS]],
                                   truncated=truncated or len(values) > MAX_ROWS)
        return document_result([{"result": response}])

    def insert_row(self, table, values):
        require_writable(self.connection)
        key = values.get("key") or table
        kind = values.get("type") or "string"
        value = values.get("value", "")
        ttl_seconds = values.get("ttl")
        with self.session() as client:
            self._write_value(client, key, kind, value, insert=True, ttl_seconds=ttl_seconds)
        return QueryResult(affected=1)

    @staticmethod
    def _validated_value(kind, value):
        if kind not in {"string", "hash", "list", "set", "zset"}:
            raise ValueError("Grid editing supports Redis string, hash, list, set and zset values.")
        if kind == "string":
            if isinstance(value, bool) or not isinstance(value, (str, bytes, int, float)):
                return json.dumps(value, ensure_ascii=False, allow_nan=False)
            return value
        if kind in {"hash", "zset"} and not isinstance(value, dict):
            raise ValueError("Use a JSON object for Redis hash or zset values.")
        if kind in {"list", "set"} and not isinstance(value, list):
            raise ValueError("Use a JSON array for Redis list or set values.")
        if not value:
            raise ValueError("Redis collections cannot be empty. Delete the key explicitly instead.")
        if len(value) > MAX_ROWS:
            raise ValueError(f"Grid editing supports collections of at most {MAX_ROWS} members.")

        def scalar(item):
            if isinstance(item, bool) or not isinstance(item, (str, bytes, int, float)):
                raise ValueError("Redis members and hash fields must be strings or numbers; nested values are unsupported.")
            if isinstance(item, float) and not math.isfinite(item):
                raise ValueError("Redis numeric values must be finite.")
            return item

        if kind == "zset":
            normalized = {}
            for member, score in value.items():
                scalar(member)
                try:
                    number = float(score)
                except (TypeError, ValueError):
                    raise ValueError("Redis sorted-set scores must be finite numbers.") from None
                if not math.isfinite(number):
                    raise ValueError("Redis sorted-set scores must be finite numbers.")
                normalized[member] = number
            return normalized
        if kind == "hash":
            return {scalar(field): scalar(item) for field, item in value.items()}
        return [scalar(item) for item in value]

    def _write_value(self, client, key, kind, value, *, insert=False, ttl_seconds=None):
        """Stage a valid value, then atomically rename it over an unchanged key.

        Redis MULTI does not roll back earlier commands on runtime errors. The
        original key is therefore never deleted or otherwise modified while
        validating and building its replacement.
        """
        if not isinstance(key, (str, bytes)) or not key:
            raise ValueError("A Redis key must be a nonempty string or bytes.")
        if ttl_seconds is not None and (
                isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, int)
                or ttl_seconds < 1 or ttl_seconds > 2_147_483_647):
            raise ValueError("TTL must be a positive number of seconds up to 2,147,483,647.")
        temporary = f"__baseskiff_tmp__:{uuid.uuid4().hex}"
        watch_error = driver("redis").WatchError
        try:
            with client.pipeline(transaction=True) as transaction:
                transaction.watch(key)
                original_kind = _redis_kind(transaction.type(key))
                if insert and original_kind != "none":
                    raise ValueError("This Redis key already exists; edit its record instead.")
                if not insert and original_kind == "none":
                    raise ValueError("This Redis key no longer exists. Refresh the table.")
                if not insert and original_kind not in {"string", "hash", "list", "set", "zset"}:
                    raise ValueError("This Redis value type is read-only and cannot be replaced in the grid.")
                if not insert and self._value_size(transaction, key, original_kind) > MAX_ROWS:
                    raise ValueError(f"This collection is truncated to {MAX_ROWS} members and cannot be edited in the grid.")
                kind = kind or original_kind
                if value is _MISSING:
                    value = self._read_value(transaction, key, original_kind)
                value = self._validated_value(kind, value)
                ttl = transaction.pttl(key)
                checked_at = time.monotonic()
                # Errors in this transaction affect only the private staging key.
                with client.pipeline(transaction=True) as staging:
                    if kind == "string":
                        staging.set(temporary, value)
                    elif kind == "hash":
                        staging.hset(temporary, mapping=value)
                    elif kind == "list":
                        staging.rpush(temporary, *value)
                    elif kind == "set":
                        staging.sadd(temporary, *value)
                    elif kind == "zset":
                        staging.zadd(temporary, value)
                    staging.pexpire(temporary, 60_000)
                    staging.execute()
                if ttl_seconds is not None:
                    client.expire(temporary, ttl_seconds)
                elif ttl >= 0:
                    remaining = ttl - int((time.monotonic() - checked_at) * 1000)
                    if remaining <= 0:
                        raise ValueError("This Redis key expired while editing. Refresh the table.")
                    client.pexpire(temporary, remaining)
                else:
                    client.persist(temporary)
                transaction.watch(temporary)
                transaction.multi()
                # The only final mutation is RENAME. It transfers the completed
                # value and its expiration together, without a partial replacement.
                transaction.rename(temporary, key)
                transaction.execute()
        except watch_error:
            raise ValueError("This Redis key changed concurrently. Refresh before saving again.") from None
        finally:
            try:
                client.delete(temporary)
            except Exception:
                pass

    def update_row(self, table, keys, values):
        require_writable(self.connection)
        if set(keys) != {"key"}:
            raise ValueError("Redis editing requires the key primary key.")
        with self.session() as client:
            self._write_value(client, keys["key"], values.get("type"), values.get("value", _MISSING))
        return QueryResult(affected=1)

    def delete_rows(self, table, keys_list):
        require_writable(self.connection)
        if any(set(keys) != {"key"} for keys in keys_list):
            raise ValueError("Redis editing requires the key primary key.")
        with self.session() as client:
            affected = client.delete(*(keys["key"] for keys in keys_list)) if keys_list else 0
        return QueryResult(affected=affected)


class SSHAdapter:
    """Own one tunnel per operation, including any nested schema sessions."""

    def __init__(self, connection):
        self.connection = connection

    def _run(self, method, *args, **kwargs):
        from .ssh import ssh_endpoint
        token = kwargs.get("cancel_token")
        if token is not None:
            token.raise_if_cancelled()
        with ssh_endpoint(self.connection) as endpoint:
            adapter = _direct_adapter(self.connection, endpoint=endpoint)
            token = kwargs.get("cancel_token")
            callback = getattr(adapter, "cancel", None)
            if token is not None and callback is not None:
                token.bind(callback)
            try:
                if token is not None:
                    token.raise_if_cancelled()
                return getattr(adapter, method)(*args, **kwargs)
            finally:
                if token is not None and callback is not None:
                    token.unbind(callback)

    def test(self):
        return self._run("test")

    def list_tables(self):
        return self._run("list_tables")

    def list_databases(self):
        return self._run("list_databases")

    def search_keys(self, text="", mode="prefix", limit=MAX_REDIS_KEYS, *, continuation=None):
        _redis_key_search_database(self.connection, text, mode, continuation)
        if continuation is None:
            return self._run("search_keys", text, mode, limit)
        return self._run("search_keys", text, mode, limit, continuation=continuation)

    def read_key(self, key):
        return self._run("read_key", key)

    def mutate_entry(self, key, kind, action, payload):
        return self._run("mutate_entry", key, kind, action, payload)

    def set_key_ttl(self, key, seconds, *, expected_ttl_ms=None):
        return self._run("set_key_ttl", key, seconds, expected_ttl_ms=expected_ttl_ms)

    def get_schema(self, table):
        return self._run("get_schema", table)

    def list_indexes(self, table):
        return self._run("list_indexes", table)

    def rename_table(self, table, new_name):
        return self._run("rename_table", table, new_name)

    def truncate_table(self, table):
        return self._run("truncate_table", table)

    def add_column(self, table, name, data_type="TEXT"):
        return self._run("add_column", table, name, data_type)

    def drop_column(self, table, name):
        return self._run("drop_column", table, name)

    def drop_table(self, table):
        return self._run("drop_table", table)

    def create_index(self, table, name, columns, unique=False):
        return self._run("create_index", table, name, columns, unique)

    def drop_index(self, table, name):
        return self._run("drop_index", table, name)

    def fetch_rows(self, table, page=1, page_size=50, search="", sort_column="", descending=False,
                   where_clause="", order_by="", **kwargs):
        return self._run("fetch_rows", table, page, page_size, search, sort_column, descending,
                         where_clause, order_by, **kwargs)

    def apply_grid_changes(self, table, *, updates=(), inserts=(), deletes=()):
        return self._run("apply_grid_changes", table, updates=updates, inserts=inserts, deletes=deletes)

    def execute(self, query, cancel_token=None):
        if cancel_token is None:
            return self._run("execute", query)
        return self._run("execute", query, cancel_token=cancel_token)

    def export_csv(self, destination, **kwargs):
        return self._run("export_csv", destination, **kwargs)

    def explain(self, query, cancel_token=None):
        return self._run("explain", query, cancel_token=cancel_token)

    def insert_row(self, table, values):
        return self._run("insert_row", table, values)

    def update_row(self, table, keys, values):
        return self._run("update_row", table, keys, values)

    def delete_rows(self, table, keys_list):
        return self._run("delete_rows", table, keys_list)


def _direct_adapter(connection: Connection, *, endpoint=None):
    if connection.kind == "MongoDB":
        return MongoAdapter(connection, endpoint=endpoint)
    if connection.kind == "Redis":
        return RedisAdapter(connection, endpoint=endpoint)
    if connection.kind in {"SQLite", "PostgreSQL", "MySQL", "Oracle"}:
        return SQLAdapter(connection, endpoint=endpoint)
    raise ValueError(f"Unsupported database type: {connection.kind}")


def adapter_for(connection: Connection):
    if connection.ssh_enabled:
        if connection.kind == "SQLite":
            raise ValueError("SSH tunnels are not supported for SQLite connections.")
        if connection.kind not in DEFAULT_PORTS:
            raise ValueError(f"Unsupported database type: {connection.kind}")
        return SSHAdapter(connection)
    return _direct_adapter(connection)
