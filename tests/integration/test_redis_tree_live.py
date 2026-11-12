"""Real Redis keys remain operable through grouped native sidebar navigation."""
import shlex
from contextlib import nullcontext
from dataclasses import replace

import pytest
from PySide6.QtCore import Qt, QThreadPool
from redis import Redis

from baseskiff.models import Connection
from baseskiff.ui.redis_tree import NODE_KIND_ROLE
from baseskiff.ui.window import MainWindow
from tests.redis_tree_helpers import key_children, seed_database
from tests.ssh_test_server import TestSSHServer

pytestmark = pytest.mark.integration


def descendants(item):
    pending = [item]
    while pending:
        current = pending.pop()
        yield current
        pending.extend(current.child(i) for i in range(current.childCount()))


@pytest.mark.parametrize("separator,route", [(":", "direct"), ("::", "ssh")])
def test_grouped_keys_open_exact_values_search_and_flatten(
        qtbot, live_config, live_service, run_tag, separator, route):
    config = live_config['redis']
    host = live_config['host']
    prefix = run_tag + '_tree'
    keys = [separator.join(parts) for parts in (
        (prefix, 'h', 'match', 'cart', 'stock', '1', '4205457'),
        (prefix, 'h', 'match', 'event', 'message'),
        (prefix, 'h'),
        (prefix, 'h', '', 'trailing', ''),
    )]
    raw = Redis(host=host, port=config['port'], username=config.get('username') or None,
                password=config.get('password') or None, protocol=2, decode_responses=True)
    try:
        raw.mset({key: f'value-{index}' for index, key in enumerate(keys)})
        with TestSSHServer() if route == 'ssh' else nullcontext() as ssh:
            values = dict(name='Redis tree live', kind='Redis', host=host, port=config['port'],
                          username=config.get('username', ''), password=config.get('password', ''),
                          redis_separator=separator)
            candidate = ssh.connection(host, config['port'], **{
                key: value for key, value in values.items() if key not in {'host', 'port'}}) if ssh else Connection(**values)
            saved = live_service.save_connection(candidate)
            window = MainWindow(live_service, language='en')
            qtbot.addWidget(window)
            errors = []
            window.show_error = errors.append
            window.show()

            def idle():
                return not window.busy and QThreadPool.globalInstance().activeThreadCount() == 0

            try:
                qtbot.waitUntil(idle, timeout=30000)
                tables = live_service.list_tables(saved.id)
                window.connections = [saved]
                window.current_connection = saved
                window.current_table = keys[0]
                window.tables = tables
                root = seed_database(window, saved, tables)
                leaves = {item.data(0, Qt.ItemDataRole.UserRole)[1]: item
                          for item in descendants(root) if item.data(0, NODE_KIND_ROLE) == 'key'}
                assert set(keys) <= leaves.keys()
                folder = next(root.child(i) for i in range(root.childCount())
                              if root.child(i).text(0).startswith(prefix + ' ('))
                assert folder.childCount() > 0 and prefix in folder.text(0)
                assert live_service.fetch_rows(saved.id, keys[0]).rows[0]['value'] == 'value-0'
                window.tree_clicked(folder, 0)
                assert window.current_table == keys[0] and idle()
                window.side_search.setText('4205457')
                assert not leaves[keys[0]].isHidden()
                assert leaves[keys[1]].isHidden()
                window.side_search.clear()
                opened = []
                window.open_table = opened.append
                window.tree_clicked(leaves[keys[1]], 0)
                assert opened == [keys[1]]
                assert live_service.fetch_rows(saved.id, keys[1]).rows[0]['value'] == 'value-1'
                query = 'GET ' + shlex.quote(keys[1])
                assert live_service.execute(saved.id, query).rows == [{'result': 'value-1'}]
                assert live_service.list_history()[0].query == query
                flat = live_service.save_connection(replace(saved, redis_separator=''))
                flat_tables = live_service.list_tables(flat.id)
                window.connections = [flat]
                window.current_connection = flat
                window.current_table = keys[1]
                window.tables = flat_tables
                root = seed_database(window, flat, flat_tables)
                assert all(child.childCount() == 0 for child in key_children(root))
                assert window.current_table == keys[1] and not errors, errors
                if ssh:
                    assert ssh.destinations and all(item == (host, config['port']) for item in ssh.destinations)
            finally:
                qtbot.waitUntil(idle, timeout=30000)
                window.close()
    finally:
        raw.delete(*keys)
        raw.close()
