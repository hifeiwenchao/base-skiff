"""Typed Redis views integrate with worker routing and operation history."""
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QThreadPool

from baseskiff.models import Connection, QueryResult
from baseskiff.service import DatabaseService
from baseskiff.ui.window import MainWindow


@pytest.fixture
def window(qtbot, tmp_path, monkeypatch):
    widget = MainWindow(DatabaseService(tmp_path), language='en')
    monkeypatch.setattr(widget, 'show_error', lambda message: pytest.fail(message))
    qtbot.addWidget(widget)
    widget.show()
    qtbot.waitUntil(lambda: not widget.busy and widget.data_table.rowCount() > 0
                   and QThreadPool.globalInstance().activeThreadCount() == 0, timeout=15000)
    widget.current_connection = Connection('Redis typed values', kind='Redis')
    widget.current_table = 'cart:42'
    widget.columns = []
    widget.update_context()
    yield widget
    qtbot.waitUntil(lambda: not widget.busy, timeout=15000)
    widget.close()


def value(key='cart:42', kind='hash'):
    return SimpleNamespace(key=key, kind=kind, value={'name': 'original', 'count': '2'},
                           size=2, ttl_ms=60000, truncated=False)


def test_hash_load_displays_separate_fields_and_hides_generic_grid(window, qtbot, monkeypatch):
    monkeypatch.setattr(window.service, 'read_redis_key', lambda *_, **kwargs: value(), raising=False)
    window.refresh_data()
    qtbot.waitUntil(lambda: not window.busy)
    assert window.redis_view.isVisible()
    assert window.redis_view.table.rowCount() == 2
    assert not window.data_splitter.isVisible()
    assert not window.data_search.isVisible()
    assert not window.table_criteria_bar.isVisible()
    assert not window.data_pager.isVisible()
    window.current_connection = window.connections[0]
    window.update_context()
    assert not window.redis_view.isVisible()
    assert window.data_splitter.isVisible()


def test_entry_mutation_uses_captured_key_and_refreshes_snapshot(window, qtbot, monkeypatch):
    calls = []
    def mutate(cid, key, kind, action, payload, *, database=None):
        calls.append((cid, key, kind, action, payload))
        return QueryResult(affected=1)
    monkeypatch.setattr(window.service, 'mutate_redis_entry', mutate, raising=False)
    monkeypatch.setattr(window.service, 'read_redis_key', lambda *_, **kwargs: value(), raising=False)
    window._redis_snapshot = value()
    window.redis_view.set_value(value())
    payload = {'entry': 'name', 'value': '00042', 'expected': 'original'}
    window.redis_view.mutation_requested.emit('update', payload)
    qtbot.waitUntil(lambda: bool(calls) and not window.busy)
    assert calls == [(window.current_connection.id, 'cart:42', 'hash', 'update', payload)]
    assert window.redis_view.table.rowCount() == 2


def test_key_switch_ignores_stale_typed_response(window, monkeypatch):
    requests = []
    monkeypatch.setattr(window, 'submit', lambda function, callback=None, error=None:
                        requests.append((function, callback, error)))
    window.refresh_data()
    window.current_table = 'other:key'
    window.refresh_data()
    requests[0][1](value())
    assert window._redis_snapshot is None
    requests[1][1](value('other:key'))
    assert window._redis_snapshot.key == 'other:key'


def test_read_only_rejects_programmatic_mutation_dispatch(window, monkeypatch):
    from dataclasses import replace
    window.current_connection = replace(window.current_connection, read_only=True)
    window._redis_snapshot = value()
    submitted = []
    monkeypatch.setattr(window, 'submit', lambda *args, **kwargs: submitted.append(args))
    window.redis_view.mutation_requested.emit('update', {'entry': 'name', 'value': 'blocked'})
    assert submitted == []


def test_hash_csv_exports_individual_fields(window, qtbot, monkeypatch, tmp_path):
    import csv

    from PySide6.QtWidgets import QFileDialog
    monkeypatch.setattr(window.service, 'read_redis_key', lambda *_, **kwargs: value(), raising=False)
    window.refresh_data()
    qtbot.waitUntil(lambda: not window.busy)
    path = tmp_path / 'hash.csv'
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *args: (str(path), 'CSV (*.csv)'))
    window.export_data()
    with path.open(encoding='utf-8-sig', newline='') as stream:
        assert list(csv.reader(stream)) == [['field', 'value'], ['name', 'original'], ['count', '2']]


def test_read_only_cannot_delete_entire_key(window, monkeypatch):
    from dataclasses import replace
    window.current_connection = replace(window.current_connection, read_only=True)
    window.update_context()
    submitted = []
    monkeypatch.setattr(window, 'submit', lambda *args, **kwargs: submitted.append(args))
    window.delete_redis_key()
    assert not window.delete_key_button.isEnabled()
    assert submitted == []


def test_catalog_refresh_keeps_search_and_open_value(window, monkeypatch):
    calls = []
    monkeypatch.setattr(window, '_refresh_redis_keys', lambda: calls.append('search'))
    monkeypatch.setattr(window, 'refresh_data', lambda: calls.append('read'))
    window.refresh_catalog()
    assert calls == ['search', 'read']
    assert window.current_table == 'cart:42'


def test_returning_to_connection_during_write_reloads_and_unlocks_view(window, monkeypatch):
    requests = []
    monkeypatch.setattr(window, 'submit', lambda function, callback=None, error=None:
                        requests.append((function, callback, error)))
    window._redis_snapshot = value()
    window.redis_view.set_value(value())
    window._mutate_redis_entry('update', {'entry': 'name', 'value': 'edited', 'expected': 'original'})
    done = requests[0][1]
    window._generation += 2
    window.redis_view.set_busy(True)
    done(QueryResult(affected=1))
    assert not window._redis_writes
    assert len(requests) == 3
    requests[1][1](value())
    assert window.redis_view.add_button.isEnabled()


def test_setting_redis_ttl_uses_key_scope_and_refreshes_only_the_value(window, monkeypatch):
    requests = []
    monkeypatch.setattr(window, 'submit', lambda function, callback=None, error=None:
                        requests.append((function, callback, error)))
    refreshed = []
    monkeypatch.setattr(window, 'refresh_data', lambda: refreshed.append('read'))
    window._redis_snapshot = value()
    window.redis_view.set_value(window._redis_snapshot)
    window.redis_view.ttl_requested.emit(90)
    assert len(requests) == 1
    assert requests[0][0].keywords['database'] == '0'
    assert requests[0][0].keywords['expected_ttl_ms'] == 60000
    requests[0][1](QueryResult(affected=1))
    assert refreshed == ['read']
    assert not window._redis_writes


def test_failed_hash_save_reopens_same_draft_with_inline_error(window, qtbot, monkeypatch):
    def rejected(*args, **kwargs):
        raise ValueError('The field changed; refresh before saving.')
    monkeypatch.setattr(window.service, 'mutate_redis_entry', rejected, raising=False)
    window._redis_snapshot = value()
    window.redis_view.set_value(window._redis_snapshot)
    view = window.redis_view
    view.table.selectRow(0)
    view.edit_button.click()
    dialog = view.entry_dialog
    dialog.value_editor.setPlainText('unsaved\n00042')
    dialog.save_button.click()
    qtbot.waitUntil(lambda: not window.busy and dialog.isVisible())
    assert dialog.value_editor.toPlainText() == 'unsaved\n00042'
    assert 'changed' in dialog.error_label.text()
    dialog.reject()
