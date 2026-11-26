"""Shared, Qt-independent contracts for database adapters and the interface."""
import uuid
from dataclasses import dataclass, field
from typing import Any

DATABASE_TYPES = ("PostgreSQL", "MySQL", "SQLite", "MongoDB", "Redis", "Oracle")
DEFAULT_PORTS = {"PostgreSQL": 5432, "MySQL": 3306, "MongoDB": 27017, "Redis": 6379, "Oracle": 1521}
DATABASE_COLORS = {"PostgreSQL": "#477bc4", "MySQL": "#d69a44", "SQLite": "#627dce", "MongoDB": "#4c9c78", "Redis": "#d16b68", "Oracle": "#b272cb"}

@dataclass
class Connection:
    name: str
    kind: str = "SQLite"
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    host: str = "localhost"
    port: int = 0
    database: str = ""
    username: str = ""
    password: str = ""
    path: str = ""
    group: str = "Local"
    ssl: bool = False
    read_only: bool = False
    is_demo: bool = False
    query_timeout: float = 10.0
    redis_separator: str = ":"
    ssh_enabled: bool = False
    ssh_host: str = ""
    ssh_port: int = 22
    ssh_username: str = ""
    ssh_auth_method: str = "password"
    ssh_password: str = field(default="", repr=False)
    ssh_private_key: str = ""
    ssh_passphrase: str = field(default="", repr=False)
    ssh_host_key: str = ""
    ssh_known_hosts: str = ""

@dataclass
class TableInfo:
    name: str | bytes
    schema: str = ""
    row_count: int | None = None
    kind: str = "table"

    @property
    def qualified_name(self) -> str | bytes:
        return f"{self.schema}.{self.name}" if self.schema else self.name

@dataclass(frozen=True)
class RedisKeyContinuation:
    connection_id: str
    database: str
    prefix: str
    cursor: int
    pending: tuple[str | bytes, ...] = ()
    seen: frozenset[str | bytes] = frozenset()

@dataclass
class RedisKeyPage:
    keys: list[TableInfo]
    truncated: bool = False
    continuation: RedisKeyContinuation | None = None

@dataclass
class RedisDatabaseInfo:
    index: int
    key_count: int | None = None

@dataclass
class RedisDatabaseList:
    databases: list[RedisDatabaseInfo]
    complete: bool = True

@dataclass(frozen=True)
class RedisValueContinuation:
    connection_id: str
    database: str
    key: bytes
    kind: str
    cursor: int = 0
    pending: tuple = ()
    seen: frozenset[bytes] = frozenset()

@dataclass
class RedisValue:
    key: str | bytes
    kind: str
    value: Any
    size: int = 0
    ttl_ms: int = -1
    truncated: bool = False
    continuation: RedisValueContinuation | None = None

@dataclass
class ColumnInfo:
    name: str
    data_type: str = "TEXT"
    primary_key: bool = False
    nullable: bool = True
    default: Any = None
    extra: str = ""
    charset: str | None = None
    collation: str | None = None
    comment: str = ""
    generation_expression: str = ""
    metadata_complete: bool = False

@dataclass
class IndexInfo:
    """An index exposed by a SQL table catalog."""
    name: str
    columns: tuple[str, ...] = ()
    unique: bool = False
    origin: str = ""

@dataclass
class QueryResult:
    columns: list[str] = field(default_factory=list)
    rows: list[dict[str, Any]] = field(default_factory=list)
    affected: int = 0
    elapsed_ms: float = 0
    total: int = 0
    truncated: bool = False
    total_known: bool = True
    has_more: bool = False
    next_cursor: Any = None

@dataclass
class HistoryEntry:
    id: str
    connection_id: str
    connection_name: str
    query: str
    timestamp: str
    elapsed_ms: float
    affected: int
    success: bool
    error: str = ""
    database: str = ""
