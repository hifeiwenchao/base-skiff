"""Session-only workspace state and asynchronous request ownership.

A tab object is also its lifetime token: a reopened identity never accepts work
belonging to the object that was closed. No Qt widgets are stored.
"""
from dataclasses import dataclass, field
from typing import Any

from baseskiff.models import Connection, QueryResult


@dataclass(eq=False)
class WorkspaceTab:
    connection: Connection
    table: str | bytes = ''
    page: int = 1
    page_size: int = 50
    total_rows: int = 0
    columns: list = field(default_factory=list)
    current_result: QueryResult = field(default_factory=QueryResult)
    query_result: QueryResult = field(default_factory=QueryResult)
    sort_column: str = ''
    descending: bool = False
    search: str = ''
    query_text: str = ''
    query_cursor: tuple = (0, 0)
    query_running: bool = False
    query_state: str = ''
    query_error: str = ''
    inner_tab: int = 0
    inspector: bool = False
    redis_snapshot: Any = None
    redis_draft: str | None = None
    redis_search: str = ''
    views: dict = field(default_factory=dict)
    loading: bool = False
    dirty: bool = False
    schema_dirty: bool = False
    missing: bool = False
    error: str = ''
    closed: bool = False
    requests: dict = field(default_factory=dict)

    @property
    def identity(self):
        database = self.connection.database or ('0' if self.connection.kind == 'Redis' else '')
        return self.connection.id, database, self.table

    def request(self, operation):
        token = object()
        self.requests[operation] = token
        return token

    def accepts(self, operation, token):
        return not self.closed and self.requests.get(operation) is token

    def close(self):
        self.closed = True
        self.requests.clear()
