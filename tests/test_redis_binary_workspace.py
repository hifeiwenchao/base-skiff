"""Binary Redis identities survive native navigation, history and exports."""
import json
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt, QThreadPool
from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem

from baseskiff.models import Connection, QueryResult, TableInfo
from baseskiff.service import DatabaseService
from baseskiff.ui.redis_tree import NODE_KIND_ROLE, append_redis_keys, iter_items
from baseskiff.ui.window import MainWindow, display_value
from tests.redis_tree_helpers import seed_database


def test_mixed_binary_keys_share_text_prefix_and_preserve_raw_identity(qtbot):
    tree = QTreeWidget()
    qtbot.addWidget(tree)
    root = QTreeWidgetItem(['Redis'])
    tree.addTopLevelItem(root)
    connection = Connection('Binary keys', kind='Redis')
    keys = ['bucket:plain', b'bucket:\xff\x00', 'bucket:literal\\xff']

    leaves = append_redis_keys(root, connection, [TableInfo(key) for key in keys], '(empty)', 'keys')

    folders = [item for item in iter_items(root) if item.data(0, NODE_KIND_ROLE) == 'redis_folder']
    assert len(folders) == 1 and folders[0].text(0) == 'bucket (3)'
    assert leaves[keys[1]].data(0, Qt.ItemDataRole.UserRole) == (connection.id, keys[1])
    assert leaves[keys[1]].text(0) != leaves[keys[2]].text(0)
    assert '\x00' not in leaves[keys[1]].text(0)
    assert len({item.text(0) for item in leaves.values()}) == 3


def test_nested_binary_query_values_can_render_without_losing_type():
    rendered = display_value({b'\xff': [b'\x00\xde', 'normal']})
    structured = json.loads(rendered)
    assert structured['$map'][0][0] == {'$binary': '/w=='}
    assert structured['$map'][0][1] == [{'$binary': 'AN4='}, 'normal']


def test_nested_database_scalars_keep_existing_text_display():
    data = {'date': datetime(2026, 11, 11, 12, 0), 'amount': Decimal('12.50')}
    assert json.loads(display_value(data)) == {key: str(value) for key, value in data.items()}


def test_binary_key_and_payload_can_be_written_to_local_history(tmp_path, monkeypatch):
    service = DatabaseService(tmp_path)
    connection = service.save_connection(Connection('Binary history', kind='Redis'))
    key, field = b'raw:\xff', b'field:\xfe'
    payload = {'entry': field, 'value': 'updated', 'expected': b'\xde\x00'}
    calls = []
    adapter = SimpleNamespace(connection=connection,
                              mutate_entry=lambda *args: calls.append(args) or QueryResult(affected=1))
    monkeypatch.setattr('baseskiff.service.adapter_for', lambda _: adapter)

    assert service.mutate_redis_entry(connection.id, key, 'hash', 'update', payload).affected == 1

    assert calls == [(key, 'hash', 'update', payload)]
    entry = service.list_history()[0]
    assert entry.success and entry.database == '0'
    assert '$binary' in entry.query and 'cmF3Ov8=' in entry.query


@pytest.fixture
def binary_window(qtbot, tmp_path):
    window = MainWindow(DatabaseService(tmp_path), language='en')
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: not window.busy and window.data_table.rowCount() > 0
                   and QThreadPool.globalInstance().activeThreadCount() == 0, timeout=15000)
    window.current_connection = Connection('Binary workspace', kind='Redis')
    window.connections = [window.current_connection]
    yield window
    window.close()


def test_binary_key_window_context_and_click_keep_identity(binary_window, monkeypatch):
    window = binary_window
    key = b'bucket:raw.\xff\x00'
    window.current_table = key
    seed_database(window, window.current_connection, [TableInfo(key, kind='hash')])

    window.update_context()

    assert window.table_heading.text() and '\x00' not in window.table_heading.text()
    item = next(item for item in iter_items(window.tree.topLevelItem(0))
                if item.data(0, NODE_KIND_ROLE) == 'key')
    calls = []
    monkeypatch.setattr(window, 'open_table', calls.append)
    window.tree_clicked(item, 0)
    assert calls == [key]
