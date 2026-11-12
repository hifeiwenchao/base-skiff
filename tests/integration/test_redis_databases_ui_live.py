"""Exercise logical database selection and Hash edits through native Qt."""
import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QAbstractItemView
from redis import Redis

from baseskiff.models import Connection
from baseskiff.ui.window import MainWindow
from tests.redis_tree_helpers import database_node, node_scope

pytestmark = pytest.mark.integration


def test_native_database_tree_browses_and_edits_only_selected_database(qtbot, live_service, live_config, run_tag, monkeypatch):
    configuration = live_config['redis']
    key = run_tag + ':database-ui:hash'
    clients = [Redis(host=live_config['host'], port=configuration['port'], db=index,
                     username=configuration.get('username') or None,
                     password=configuration.get('password') or None,
                     protocol=2, decode_responses=True) for index in (0, 1)]
    widget = None
    try:
        for index, client in enumerate(clients):
            client.hset(key, mapping={'name': f'database-{index}', 'other': 'keep'})
        saved = live_service.save_connection(Connection(
            'Redis logical databases', kind='Redis', database='0', host=live_config['host'],
            port=configuration['port'], username=configuration.get('username', ''),
            password=configuration.get('password', '')))
        widget = MainWindow(live_service, language='en')
        qtbot.addWidget(widget)
        errors = []
        monkeypatch.setattr(widget, 'show_error', errors.append)
        widget.show()
        qtbot.waitUntil(lambda: not widget.busy and widget.current_table != '', timeout=15000)
        widget.activate_connection(saved)
        qtbot.waitUntil(lambda: not widget.busy, timeout=15000)
        database = database_node(widget, saved, '0')
        widget.tree.scrollToItem(database, QAbstractItemView.ScrollHint.PositionAtCenter)
        rectangle = widget.tree.visualItemRect(database)
        qtbot.mouseClick(widget.tree.viewport(), Qt.MouseButton.LeftButton,
                         pos=QPoint(rectangle.left() + 30, rectangle.center().y()))
        qtbot.waitUntil(lambda: widget.redis_search_group.isVisible() and not widget.busy, timeout=15000)
        widget.redis_key_mode.setCurrentIndex(widget.redis_key_mode.findData('exact'))
        widget.redis_key_search.setText(key)
        widget._search_redis_keys()
        qtbot.waitUntil(lambda: not widget.busy, timeout=15000)
        widget.open_table(key)
        qtbot.waitUntil(lambda: not widget.busy, timeout=15000)
        assert widget._redis_snapshot.value['name'] == 'database-0'
        database = database_node(widget, saved, '1')
        widget.tree.scrollToItem(database, QAbstractItemView.ScrollHint.PositionAtCenter)
        rectangle = widget.tree.visualItemRect(database)
        qtbot.mouseClick(widget.tree.viewport(), Qt.MouseButton.LeftButton,
                         pos=QPoint(rectangle.left() + 30, rectangle.center().y()))
        assert database.isExpanded()
        qtbot.waitUntil(lambda: not widget.busy and widget.current_connection.database == '1', timeout=15000)
        assert node_scope(database_node(widget, saved, '1')) == (saved.id, '1')
        widget.open_table(key)
        qtbot.waitUntil(lambda: not widget.busy, timeout=15000)
        assert widget._redis_snapshot.value['name'] == 'database-1'
        widget.redis_view.mutation_requested.emit('update',
            {'entry': 'name', 'expected': 'database-1', 'value': 'edited DB1'})
        qtbot.waitUntil(lambda: not widget.busy, timeout=15000)
        assert clients[1].hgetall(key) == {'name': 'edited DB1', 'other': 'keep'}
        assert clients[0].hgetall(key) == {'name': 'database-0', 'other': 'keep'}
        assert live_service.list_history()[0].database == '1'
        assert errors == []
    finally:
        if widget is not None:
            widget.redis_key_timer.stop()
            qtbot.waitUntil(lambda: not widget.busy, timeout=15000)
            widget.close()
        for client in clients:
            client.delete(key)
            client.close()
