"""Redis sidebar searches keep their logical database across table tabs."""
from dataclasses import replace

import pytest
from PySide6.QtCore import QThreadPool

from baseskiff.models import Connection, RedisKeyPage, TableInfo
from baseskiff.service import DatabaseService
from baseskiff.ui.window import MainWindow


@pytest.fixture
def redis_tabs(qtbot, tmp_path, monkeypatch):
    window = MainWindow(DatabaseService(tmp_path), language='en')
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: not window.busy and window.data_table.rowCount() > 0
                   and QThreadPool.globalInstance().activeThreadCount() == 0, timeout=15000)
    requests = []
    monkeypatch.setattr(window, 'submit', lambda function, callback=None, error=None, **kwargs:
                        requests.append((function, callback, error)))
    connection = Connection('Redis tabs', kind='Redis', database='0')
    window.connections.append(connection)
    window.activate_connection(connection, 'A')
    search_request(requests)[1](RedisKeyPage([TableInfo('A'), TableInfo('B')]))
    window.open_table('B')
    yield window, requests, connection
    window.search_timer.stop()
    window.close()


def search_request(requests):
    return next(request for request in reversed(requests)
                if getattr(request[0].func, '__name__', '') == 'search_redis_keys')


def select(window, connection, key):
    identity = (connection.id, connection.database, key)
    index = next(index for index in range(window.table_tabs.count())
                 if window.table_tabs.tabData(index) == identity)
    window.table_tabs.setCurrentIndex(index)


def test_inflight_search_updates_same_db_after_switching_table_tabs(redis_tabs):
    window, requests, connection = redis_tabs
    window.redis_key_search.setText('wanted:')
    window._search_redis_keys()
    search = search_request(requests)
    select(window, connection, 'A')
    assert window.redis_key_search.text() == 'wanted:'
    page = RedisKeyPage([TableInfo('wanted:fresh')])
    search[1](page)
    assert window.tables == page.keys
    assert window._redis_key_pages[(connection.id, '0')] == page
    assert window._connection_tables[connection.id] == page.keys
    assert '1 keys loaded' in window.redis_key_status.text()


def test_inflight_old_db_search_is_cached_without_replacing_current_db(redis_tabs):
    window, requests, connection = redis_tabs
    window.redis_key_search.setText('wanted:')
    window._search_redis_keys()
    old_search = search_request(requests)
    other_db = replace(connection, database='1')
    window.activate_connection(other_db, 'A')
    current_page = RedisKeyPage([TableInfo('A')])
    search_request(requests)[1](current_page)
    old_page = RedisKeyPage([TableInfo('wanted:old-db')])
    old_search[1](old_page)
    assert window.current_connection.database == '1'
    assert window.tables == current_page.keys
    assert window.redis_key_search.text() == ''
    assert window._redis_key_pages[(connection.id, '0')] == old_page
    select(window, connection, 'A')
    assert window.current_connection.database == '0'
    assert window.tables == old_page.keys
    assert window.redis_key_search.text() == 'wanted:'


def test_background_catalog_refresh_searches_origin_scope_on_return(redis_tabs):
    window, requests, connection = redis_tabs
    window.activate_connection(replace(connection, database='1'), 'A')
    search_request(requests)[1](RedisKeyPage([TableInfo('A')]))
    previous_search = search_request(requests)
    window.refresh_catalog(connection)
    assert search_request(requests) is previous_search
    select(window, connection, 'A')
    resumed = search_request(requests)
    assert resumed is not previous_search
    assert resumed[0].keywords['database'] == '0'
    page = RedisKeyPage([TableInfo('A'), TableInfo('new:background')])
    resumed[1](page)
    assert window.tables == page.keys


def test_background_new_key_refreshes_origin_catalog_without_opening_key(redis_tabs, monkeypatch):
    window, requests, connection = redis_tabs

    class NewKeyDialog:
        def __init__(self, *args, **kwargs):
            pass

        def setWindowTitle(self, title):
            pass

        def exec(self):
            return True

        def values(self):
            return {'key': 'created:background', 'type': 'string', 'value': 'new'}

    monkeypatch.setattr('baseskiff.ui.window.RedisKeyDialog', NewKeyDialog)
    window.add_row()
    insertion = next(request for request in reversed(requests)
                     if getattr(request[0].func, '__name__', '') == 'insert_row')
    window.activate_connection(replace(connection, database='1'), 'A')
    search_request(requests)[1](RedisKeyPage([TableInfo('A')]))
    previous_search = search_request(requests)
    insertion[1](None)
    assert window.current_connection.database == '1'
    assert window.current_table == 'A'
    assert search_request(requests) is previous_search
    select(window, connection, 'A')
    resumed = search_request(requests)
    assert resumed is not previous_search
    assert resumed[0].keywords['database'] == '0'


def test_background_query_catalog_error_belongs_to_origin_tab(redis_tabs, monkeypatch):
    from baseskiff.models import QueryResult

    window, requests, _ = redis_tabs
    connection = window.connections[0]
    select(window, connection, 'customers')
    origin = window._active_workspace
    window.query_editor.setPlainText("UPDATE customers SET status='Active'")
    window.run_query()
    execution = next(request for request in reversed(requests)
                     if getattr(request[0].func, '__name__', '') == 'execute')
    window.open_table('orders')
    errors = []
    monkeypatch.setattr(window, 'show_error', errors.append)
    execution[1](QueryResult(affected=1))
    catalog = next(request for request in reversed(requests)
                   if getattr(request[0].func, '__name__', '') == 'list_tables')
    assert catalog[2] is not None
    catalog[2]('origin catalog unavailable')
    assert not errors
    assert origin.error == 'origin catalog unavailable'
    assert window.current_table == 'orders'
    select(window, connection, 'customers')
    assert 'origin catalog unavailable' in window.table_subtitle.text()


def test_background_new_key_error_belongs_to_origin_tab(redis_tabs, monkeypatch):
    from types import SimpleNamespace

    window, requests, connection = redis_tabs
    origin = window._active_workspace
    dialog = SimpleNamespace(setWindowTitle=lambda title: None, exec=lambda: True,
                             values=lambda: {'key': 'new', 'type': 'string', 'value': 'value'})
    monkeypatch.setattr('baseskiff.ui.window.RedisKeyDialog', lambda *args, **kwargs: dialog)
    window.add_row()
    insertion = next(request for request in reversed(requests)
                     if getattr(request[0].func, '__name__', '') == 'insert_row')
    select(window, connection, 'A')
    errors = []
    monkeypatch.setattr(window, 'show_error', errors.append)
    assert insertion[2] is not None
    insertion[2]('origin insert denied')
    assert not errors
    assert origin.error == 'origin insert denied'
    assert window.current_table == 'A'
