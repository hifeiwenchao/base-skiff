"""Small versioned workspace descriptors; never persist results or credentials."""
import base64
from dataclasses import replace

from .workspace_tabs import WorkspaceTab


def encode_workspace(state):
    record = {'connection_id': state.connection.id, 'database': state.connection.database,
              'page_size': state.page_size, 'search': state.search,
              'sort_column': state.sort_column, 'descending': state.descending,
              'where_clause': state.where_clause, 'order_by': state.order_by,
              'where_draft': state.where_draft, 'order_by_draft': state.order_by_draft,
              'inner_tab': state.inner_tab}
    record['count_total'] = state.count_total
    if isinstance(state.table, bytes):
        record['key_base64'] = base64.b64encode(state.table).decode('ascii')
    else:
        record['table'] = state.table
    return record


def decode_workspace(record, connections):
    if not isinstance(record, dict):
        return None
    connection = next((c for c in connections if c.id == record.get('connection_id')), None)
    if connection is None:
        return None
    try:
        table = (base64.b64decode(record['key_base64'], validate=True) if 'key_base64' in record
                 else record.get('table', ''))
        if not isinstance(table, (str, bytes)) or not table:
            return None
        # Redis tab identity includes its DB; other connections use their current config.
        if connection.kind == 'Redis':
            database = str(record.get('database', '0'))
            if not database.isdigit():
                return None
            connection = replace(connection, database=database)
        state = WorkspaceTab(connection, table, dirty=True, schema_dirty=True)
        state.page_size = min(1000, max(1, int(record.get('page_size', 50))))
        state.inner_tab = 2 if record.get('inner_tab') == 2 else 0
        state.descending = bool(record.get('descending'))
        state.count_total = bool(record.get('count_total', connection.is_demo))
        for name in ('search', 'sort_column', 'where_clause', 'order_by', 'where_draft', 'order_by_draft'):
            value = record.get(name, '')
            if isinstance(value, str):
                setattr(state, name, value)
        return state
    except (ValueError, TypeError, OverflowError):
        return None
