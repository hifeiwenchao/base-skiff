"""Navigate the public Redis tree hierarchy in native widget tests."""
from PySide6.QtCore import Qt

from baseskiff.models import RedisDatabaseInfo, RedisDatabaseList, RedisKeyPage
from baseskiff.ui.redis_tree import NODE_ID_ROLE, NODE_KIND_ROLE, iter_items


def database_node(window, connection, database=None):
    database = str(database if database is not None else connection.database or '0')
    identity = ('redis_database', connection.id, database)
    return next(item for item in iter_items(window.tree.invisibleRootItem())
                if item.data(0, NODE_ID_ROLE) == identity)


def key_children(parent):
    return [parent.child(index) for index in range(parent.childCount())
            if parent.child(index).data(0, NODE_KIND_ROLE) in {'key', 'redis_folder'}]


def seed_database(window, connection, tables, *, database=None, count=None):
    """Seed an already loaded DB while preserving the normal control-row layout."""
    database = str(database if database is not None else connection.database or '0')
    window._redis_key_pages[(connection.id, database)] = RedisKeyPage(tables)
    catalog = window._redis_database_catalogs.get(connection.id)
    entries = {entry.index: entry.key_count for entry in catalog.databases} if catalog else {}
    entries[int(database)] = len(tables) if count is None else count
    window._redis_database_catalogs[connection.id] = RedisDatabaseList(
        [RedisDatabaseInfo(index, total) for index, total in sorted(entries.items())])
    window._tree_expansion[('connection', connection.id)] = True
    window._tree_expansion[('redis_database', connection.id, database)] = True
    if window._redis_scope(window.current_connection) == (connection.id, database):
        window.tables = tables
        window._connection_tables[connection.id] = tables
    window.rebuild_tree()
    return database_node(window, connection, database)


def node_scope(item):
    return item.data(0, Qt.ItemDataRole.UserRole + 5)
