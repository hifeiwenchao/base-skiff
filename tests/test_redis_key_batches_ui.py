"""The 500-key bound applies to each load; DB trees keep every loaded key."""
from types import SimpleNamespace

from PySide6.QtCore import QPoint, Qt

from baseskiff.models import TableInfo
from baseskiff.ui.redis_tree import NODE_KIND_ROLE, iter_items
from tests.test_redis_database_tree import db_node, run_pending
from tests.test_redis_database_tree import window as window


def page(start, stop, continuation=None):
    return SimpleNamespace(keys=[TableInfo(f'item:{index:04}') for index in range(start, stop)],
                           truncated=continuation is not None, continuation=continuation)


def keys(window, database=0):
    return {item.data(0, Qt.ItemDataRole.UserRole)[1] for item in iter_items(db_node(window, database))
            if item.data(0, NODE_KIND_ROLE) == 'key'}


def more(window, database=0):
    return next(item for item in iter_items(db_node(window, database))
                if item.data(0, NODE_KIND_ROLE) == 'redis_load_more')


def initial_page(window, result):
    db_node(window, 0).setExpanded(True)
    request = window.requests.pop()
    request[1](result)


def next_expansion(window, database=0):
    db_node(window, database).setExpanded(False)
    db_node(window, database).setExpanded(True)


def test_each_db_expansion_loads_another_batch_and_keeps_all_loaded_keys(window, monkeypatch):
    cursor = object()
    initial_page(window, page(0, 500, cursor))
    assert len(keys(window)) == 500
    assert not window.requests
    next_expansion(window)
    assert len(window.requests) == 1, 'Reopening an unfinished DB should request its next batch'
    request = window.requests.pop()
    calls = []
    monkeypatch.setattr(window.service, 'search_redis_keys',
                        lambda cid, text='', mode='prefix', **kwargs: calls.append((text, mode, kwargs)))
    # The queued function already captured the original bound method. Inspect
    # the application boundary when a subsequent continuation is queued below.
    request[1](page(500, 1000, cursor))
    assert len(keys(window)) == 1000
    assert len(window._redis_key_pages[(window.redis_connection.id, '0')].keys) == 1000
    next_expansion(window)
    window.requests.pop()[0]()
    assert calls == [('', 'prefix', {'database': '0', 'continuation': cursor})]


def test_load_more_row_appends_without_collapsing_db_and_stops_at_scan_end(window):
    initial_page(window, page(0, 500, object()))
    window.tree_clicked(more(window), 0)
    assert len(window.requests) == 1
    window.requests.pop()[1](page(500, 1000, object()))
    assert len(keys(window)) == 1000
    window.tree_clicked(more(window), 0)
    window.requests.pop()[1](page(1000, 1001))
    assert db_node(window, 0).isExpanded()
    assert len(keys(window)) == 1001
    assert not any(item.data(0, NODE_KIND_ROLE) == 'redis_load_more'
                   for item in iter_items(db_node(window, 0)))
    next_expansion(window)
    assert not window.requests, 'A completed scan should reopen from its cumulative cache'


def test_continuation_deduplicates_and_preserves_binary_key_identity(window):
    binary = b'raw:\xff'
    first = page(0, 499, object())
    first.keys.append(TableInfo(binary))
    initial_page(window, first)
    window.tree_clicked(more(window), 0)
    last = page(498, 501)
    last.keys.append(TableInfo(binary))
    window.requests.pop()[1](last)
    assert len(keys(window)) == 502
    assert binary in keys(window)
    assert len(window._redis_key_pages[(window.redis_connection.id, '0')].keys) == 502


def test_new_search_replaces_cumulative_keys_and_ignores_an_old_append(window):
    initial_page(window, page(0, 500, object()))
    window.tree_clicked(more(window), 0)
    obsolete = window.requests.pop()
    window.redis_key_search.setText('new:')
    window._search_redis_keys()
    window.requests.pop()[1](SimpleNamespace(keys=[TableInfo('new:one')], truncated=False,
                                           continuation=None))
    obsolete[1](page(500, 1000))
    assert keys(window) == {'new:one'}
    assert window.redis_key_search.text() == 'new:'


def test_continuation_failure_keeps_loaded_keys_and_retry_uses_the_same_cursor(window, monkeypatch):
    initial_page(window, page(0, 500, cursor := object()))
    window.tree_clicked(more(window), 0)
    window.requests.pop()[2]('temporary network error')
    assert len(keys(window)) == 500
    calls = []
    monkeypatch.setattr(window.service, 'search_redis_keys',
                        lambda cid, text='', mode='prefix', **kwargs: calls.append(kwargs))
    window.tree_clicked(more(window), 0)
    window.requests.pop()[0]()
    assert calls == [{'database': '0', 'continuation': cursor}]


def test_late_batch_only_appends_to_its_original_db(window):
    initial_page(window, page(0, 500, object()))
    window.tree_clicked(more(window), 0)
    old = window.requests.pop()
    db_node(window, 1).setExpanded(True)
    run_pending(window)
    assert window.current_connection.database == '1'
    window.redis_key_search.setText('db-one:')
    window.redis_key_timer.stop()
    old[1](page(500, 1000))
    assert len(keys(window, 0)) == 1000
    assert keys(window, 1) == {'team:one'}
    assert window.current_connection.database == '1'
    assert window.redis_key_search.text() == 'db-one:'


def test_pending_changed_filter_cannot_resume_a_cursor_for_the_previous_prefix(window, monkeypatch):
    initial_page(window, page(0, 500, object()))
    old_more = more(window)
    window.redis_key_search.setText('different:')
    window.redis_key_timer.stop()
    calls = []
    monkeypatch.setattr(window.service, 'search_redis_keys',
                        lambda cid, text='', mode='prefix', **kwargs: calls.append(kwargs))
    window.tree_clicked(old_more, 0)
    for function, _callback, _error in window.requests:
        function()
    assert all(call.get('continuation') is None for call in calls)


def test_more_row_has_no_connection_context_menu(window, monkeypatch):
    initial_page(window, page(0, 500, object()))
    item = more(window)
    monkeypatch.setattr(window.tree, 'itemAt', lambda _point: item)
    menus = []
    monkeypatch.setattr('baseskiff.ui.window.QMenu', lambda *_args: menus.append('unexpected'))
    window.tree_menu(QPoint())
    assert menus == []


def test_empty_scan_batch_can_continue_and_loaded_status_does_not_claim_a_500_total_cap(window):
    initial_page(window, page(0, 0, object()))
    assert keys(window) == set()
    window.tree_clicked(more(window), 0)
    window.requests.pop()[1](page(0, 500, object()))
    window.tree_clicked(more(window), 0)
    window.requests.pop()[1](page(500, 1000, object()))
    assert len(keys(window)) == 1000
    assert '1,000' in window.redis_key_status.text()
    assert '500' in window.redis_key_status.text()
    assert 'narrow the search' not in window.redis_key_status.text()


def test_refresh_restarts_scan_and_replaces_previously_accumulated_keys(window, monkeypatch):
    initial_page(window, page(0, 500, object()))
    window.tree_clicked(more(window), 0)
    window.requests.pop()[1](page(500, 1000, object()))
    calls = []
    monkeypatch.setattr(window.service, 'search_redis_keys',
                        lambda cid, text='', mode='prefix', **kwargs: calls.append(kwargs))
    window._search_redis_keys()
    function, callback, _error = window.requests.pop()
    function()
    assert calls == [{'database': '0'}]
    callback(page(1100, 1102))
    assert keys(window) == {'item:1100', 'item:1101'}


def test_inflight_append_is_not_duplicated_by_repeated_load_clicks(window):
    initial_page(window, page(0, 500, object()))
    window.tree_clicked(more(window), 0)
    next_expansion(window)
    # The pending row may stay visible or be replaced by a loading hint.
    control = next((item for item in iter_items(db_node(window, 0))
                    if item.data(0, NODE_KIND_ROLE) == 'redis_load_more'), None)
    if control is not None:
        window.tree_clicked(control, 0)
    assert len(window.requests) == 1


def test_switching_existing_key_tabs_does_not_resume_the_db_scan(window):
    initial_page(window, page(0, 500, object()))
    window.open_table('team:one')
    run_pending(window)
    window.open_table(b'raw:\xff')
    run_pending(window)
    cid = window.redis_connection.id
    window.table_tabs.setCurrentIndex(window._tab_index((cid, '0', 'team:one')))
    window.table_tabs.setCurrentIndex(window._tab_index((cid, '0', b'raw:\xff')))
    assert not window.requests
    assert len(keys(window)) == 500


def test_native_db_tree_eventually_displays_every_key_from_real_adapter_batches(window):
    cid = window.redis_connection.id
    expected = {f'item:{index:04}' for index in range(1201)} | {'team:one', b'raw:\xff'}
    with window.service._redis_adapter(cid, database='0').session() as client:
        client.mset({f'item:{index:04}': 'value' for index in range(1201)})
    db_node(window, 0).setExpanded(True)
    run_pending(window)
    assert len(keys(window)) <= 500
    for _ in range(8):
        continuation = window._redis_key_pages[(cid, '0')].continuation
        if continuation is None:
            break
        before = set(keys(window))
        window.tree_clicked(more(window), 0)
        run_pending(window)
        assert before <= keys(window)
        assert len(keys(window) - before) <= 500
    assert window._redis_key_pages[(cid, '0')].continuation is None
    assert keys(window) == expected
    assert len(window.tables) == 1203
