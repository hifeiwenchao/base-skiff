"""Collection tabs keep independent state and BSON edits on real MongoDB."""
import json
import uuid
from datetime import datetime
from threading import Event
from types import SimpleNamespace

import pytest
from bson import Binary, Decimal128, Int64, ObjectId
from pymongo import MongoClient
from PySide6.QtCore import Qt, QThreadPool, QTimer
from PySide6.QtWidgets import QApplication, QPlainTextEdit

from baseskiff.models import Connection
from baseskiff.ui.dialogs import RowDialog
from baseskiff.ui.window import MainWindow

pytestmark = pytest.mark.integration


def _drain(qtbot, window):
    qtbot.waitUntil(
        lambda: not window.busy and not window.search_timer.isActive()
        and QThreadPool.globalInstance().activeThreadCount() == 0,
        timeout=30000,
    )


def _click_tab(qtbot, workspace, collection):
    window = workspace.window
    identity = workspace.saved.id, workspace.name, collection
    matches = [index for index in range(window.table_tabs.count())
               if window.table_tabs.tabData(index) == identity]
    assert len(matches) == 1
    qtbot.mouseClick(window.table_tabs, Qt.MouseButton.LeftButton,
                     pos=window.table_tabs.tabRect(matches[0]).center())
    assert window.current_table == collection


@pytest.fixture
def mongo_workspace(qtbot, monkeypatch, live_config, live_service, run_tag):
    config = live_config['mongo']
    name = f'{run_tag}_tabs_{uuid.uuid4().hex[:8]}'
    raw = MongoClient(
        host=live_config['host'], port=config['port'], username=config['username'],
        password=config['password'], authSource='admin',
        serverSelectionTimeoutMS=8000, socketTimeoutMS=10000,
    )
    window = None
    shared_id = ObjectId()
    originals = {}
    try:
        for side in ('left', 'right'):
            collection = side + '_records'
            original = {
                '_id': shared_id,
                'note': side + ' visible [literal].*',
                'amount': Decimal128('1234567890.1234'),
                'counter': Int64(2**40),
                'binary_value': Binary(b'\x00\xfftabs', subtype=128),
                'nested': {
                    'identifier': ObjectId(), 'amount': Decimal128('12.5000'),
                    'when': datetime(2026, 2, 3, 4, 5, 6, 123000),
                    'label': side + ' original',
                },
            }
            raw[name][collection].insert_many([
                original,
                {'_id': ObjectId(), 'note': side + ' hidden', 'amount': Decimal128('2.50')},
            ])
            originals[collection] = original
        saved = live_service.save_connection(Connection(
            name=name, kind='MongoDB', host=live_config['host'], port=config['port'],
            username=config['username'], password=config['password'], database=name,
        ))
        window = MainWindow(live_service, language='en')
        qtbot.addWidget(window)
        errors = []
        monkeypatch.setattr(window, 'show_error', errors.append)
        window.show()
        qtbot.waitUntil(lambda: window.current_connection is not None, timeout=15000)
        _drain(qtbot, window)
        window.activate_connection(saved, 'left_records')
        _drain(qtbot, window)
        assert window.current_table == 'left_records'
        assert window.total_rows == 2
        assert not errors, errors
        yield SimpleNamespace(window=window, saved=saved, errors=errors, raw=raw,
                              name=name, originals=originals, shared_id=shared_id)
    finally:
        try:
            if window is not None:
                window.search_timer.stop()
                _drain(qtbot, window)
                window.close()
                _drain(qtbot, window)
        finally:
            try:
                # Every test owns one randomly named database, never a shared DB.
                raw.drop_database(name)
            finally:
                raw.close()


def test_collection_tabs_retain_independent_filters_drafts_and_bson_results(qtbot, mongo_workspace):
    workspace = mongo_workspace
    window = workspace.window
    left_query = json.dumps({'find': 'left_records', 'filter': {'note': 'left visible [literal].*'}})
    left_draft = '{"find":"left_records","filter":'
    window.data_search.setText('left visible [literal].*')
    _drain(qtbot, window)
    assert window.total_rows == 1
    assert window.current_result.rows == [workspace.originals['left_records']]
    window.query_editor.setPlainText(left_query)
    window.run_button.click()
    _drain(qtbot, window)
    assert window._query_state == 'success'
    window.query_editor.setPlainText(left_draft)

    window.open_table('right_records')
    _drain(qtbot, window)
    assert window.data_search.text() == ''
    assert window.total_rows == 2
    right_query = json.dumps({'find': 'right_records', 'filter': {'note': 'right visible [literal].*'}})
    right_draft = '{"aggregate":"right_records","pipeline":['
    window.data_search.setText('right hidden')
    _drain(qtbot, window)
    assert window.total_rows == 1
    assert window.current_result.rows[0]['note'] == 'right hidden'
    window.query_editor.setPlainText(right_query)
    window.run_button.click()
    _drain(qtbot, window)
    assert window._query_state == 'success'
    window.query_editor.setPlainText(right_draft)
    window.tabs.setCurrentIndex(2)

    _click_tab(qtbot, workspace, 'left_records')
    _drain(qtbot, window)
    assert window.data_search.text() == 'left visible [literal].*'
    assert window.current_result.rows == [workspace.originals['left_records']]
    assert window.query_editor.toPlainText() == left_draft
    assert window.query_result.rows == [workspace.originals['left_records']]
    assert window.query_result_table.rowCount() == 1
    assert window.tabs.currentIndex() == 1
    assert window._query_state == 'success'

    _click_tab(qtbot, workspace, 'right_records')
    _drain(qtbot, window)
    assert window.data_search.text() == 'right hidden'
    assert window.current_result.rows[0]['note'] == 'right hidden'
    assert window.query_editor.toPlainText() == right_draft
    assert window.query_result.rows == [workspace.originals['right_records']]
    assert window.tabs.currentIndex() == 2
    history = window.service.list_history()
    assert {entry.query for entry in history} == {left_query, right_query}
    assert all(entry.success for entry in history)
    assert not workspace.errors, workspace.errors


def test_background_collection_dialog_edit_keeps_origin_and_nested_bson(qtbot, monkeypatch, mongo_workspace):
    workspace = mongo_workspace
    window = workspace.window
    window.open_table('right_records')
    _drain(qtbot, window)
    right_draft = '{"find":"right_records","filter":{"unfinished":'
    window.query_editor.setPlainText(right_draft)
    _click_tab(qtbot, workspace, 'left_records')
    _drain(qtbot, window)
    started, release = Event(), Event()
    update = window.service.update_row

    def delayed_update(*args, **kwargs):
        # Control completion order only; the actual driver still writes MongoDB.
        started.set()
        if not release.wait(10):
            raise RuntimeError('Timed out waiting to finish the collection-tab edit')
        return update(*args, **kwargs)

    monkeypatch.setattr(window.service, 'update_row', delayed_update)
    failures, parsed = [], []

    def accept_edit():
        dialog = QApplication.activeModalWidget()
        try:
            assert isinstance(dialog, RowDialog)
            dialog.fields['note'].setText('left edited while inactive')
            dialog.fields['amount'].setText('9876543210.4321')
            field = dialog.fields['nested']
            assert isinstance(field, QPlainTextEdit)
            nested = json.loads(field.toPlainText())
            nested['label'] = 'edited nested sibling'
            field.setPlainText(json.dumps(nested))
            parsed.append(dialog.values())
            dialog.accept()
        except Exception as error:
            failures.append(str(error))
            if dialog is not None:
                dialog.reject()

    try:
        row_index = next(index for index, row in enumerate(window.current_result.rows)
                         if row['_id'] == workspace.shared_id)
        window.tabs.setCurrentIndex(0)
        window.data_table.selectRow(row_index)
        assert window.edit_row_button.isEnabled()
        QTimer.singleShot(0, accept_edit)
        window.edit_row_button.click()
        assert not failures, failures
        assert parsed[0]['amount'] == Decimal128('9876543210.4321')
        assert isinstance(parsed[0]['nested']['identifier'], ObjectId)
        assert isinstance(parsed[0]['nested']['amount'], Decimal128)
        qtbot.waitUntil(started.is_set, timeout=5000)
        _click_tab(qtbot, workspace, 'right_records')
        assert window.query_editor.toPlainText() == right_draft
    finally:
        release.set()
    _drain(qtbot, window)
    assert window.current_table == 'right_records'
    assert window.query_editor.toPlainText() == right_draft
    right_row = next(row for row in window.current_result.rows if row['_id'] == workspace.shared_id)
    assert right_row == workspace.originals['right_records']
    assert workspace.raw[workspace.name].right_records.find_one({'_id': workspace.shared_id}) == right_row

    _click_tab(qtbot, workspace, 'left_records')
    _drain(qtbot, window)
    edited = next(row for row in window.current_result.rows if row['_id'] == workspace.shared_id)
    original = workspace.originals['left_records']
    expected = dict(original, note='left edited while inactive',
                    amount=Decimal128('9876543210.4321'),
                    nested=dict(original['nested'], label='edited nested sibling'))
    assert edited == expected
    stored = workspace.raw[workspace.name].left_records.find_one({'_id': workspace.shared_id})
    assert stored == expected
    assert isinstance(stored['_id'], ObjectId)
    assert isinstance(stored['amount'], Decimal128)
    assert isinstance(stored['counter'], Int64)
    assert isinstance(stored['binary_value'], Binary) and stored['binary_value'].subtype == 128
    assert isinstance(stored['nested']['identifier'], ObjectId)
    assert isinstance(stored['nested']['amount'], Decimal128)
    assert isinstance(stored['nested']['when'], datetime)
    assert not workspace.errors, workspace.errors
