"""Bound rendering work without changing retained values or workspace state."""
import base64
import csv
import json

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication, QFileDialog, QStyleOptionViewItem

from baseskiff.models import ColumnInfo, Connection, QueryResult, RedisValue
from baseskiff.ui.redis_preview import is_editable_text
from baseskiff.ui.window import MainWindow
from baseskiff.ui.workspace_tabs import WorkspaceTab


class _EmptyService:
    def list_connections(self):
        return []


class _Window(MainWindow):
    def _open_initial(self):
        pass


@pytest.fixture
def window(qtbot):
    widget = _Window(_EmptyService(), language='en')
    qtbot.addWidget(widget)
    widget.show()
    return widget


def test_tab_restores_disjoint_rows_and_current_cell_with_one_inspector_update(window, monkeypatch):
    connection = Connection(name='Synthetic')
    columns = [f'column_{index}' for index in range(8)]
    result = QueryResult(columns=columns, rows=[dict.fromkeys(columns, row) for row in range(40)])
    state = WorkspaceTab(connection, 'selected', current_result=result, total_rows=40,
                         columns=[ColumnInfo(name, primary_key=index == 0)
                                  for index, name in enumerate(columns)], inspector=True)
    state.views['data'] = {'selected': [0, 2, 3, 7], 'current': (5, 4),
                           'widths': [230] * 8, 'scroll': (2, 8)}
    window.connections = [connection]
    # A prior selection also has to be cleared without rebuilding the inspector.
    window.current_connection = connection
    window.current_result = result
    window.populate_table(window.data_table, result)
    window.data_table.selectRow(1)
    window.inspector.show()
    rendered = []
    render = window.render_inspector

    def observed():
        rendered.append(True)
        render()

    monkeypatch.setattr(window, 'render_inspector', observed)
    changes = QSignalSpy(window.data_table.itemSelectionChanged)
    window._activate_workspace(state, capture=False)

    assert window._selected_rows() == [0, 2, 3, 7]
    assert (window.data_table.currentRow(), window.data_table.currentColumn()) == (5, 4)
    assert window.data_table.columnWidth(0) == 230
    assert window.data_table.horizontalScrollBar().value() == 2
    assert window.data_table.verticalScrollBar().value() == 8
    assert window.delete_row_button.isEnabled()
    assert not window.edit_row_button.isEnabled()
    assert rendered == [True]
    assert changes.count() == 0


def test_large_cell_paint_is_bounded_but_copy_and_csv_remain_complete(window, tmp_path, monkeypatch):
    text = 'prefix ' + '长文本' * 4000 + ' complete suffix'
    raw = b'\x00\xff' * 4000
    result = QueryResult(columns=['text', 'binary'], rows=[{'text': text, 'binary': raw}])
    window.current_result = result
    window.populate_table(window.data_table, result)
    item = window.data_table.item(0, 0)
    option = QStyleOptionViewItem()
    window.data_table.itemDelegate().initStyleOption(option, window.data_table.model().index(0, 0))

    assert len(option.text) < 5000
    assert option.text.startswith('prefix ')
    assert option.text.endswith('…')
    assert item.text() == item.toolTip() == text
    assert window.data_table.item(0, 1).data(Qt.ItemDataRole.UserRole) == raw
    window.copy_text(item.text())
    assert QApplication.clipboard().text() == text
    destination = tmp_path / 'complete.csv'
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *args: (str(destination), 'CSV'))
    window.export_data()
    with destination.open(encoding='utf-8-sig', newline='') as stream:
        exported = list(csv.reader(stream))
    assert exported[1][0] == text
    assert base64.b64decode(json.loads(exported[1][1])['$binary']) == raw


def test_grid_formats_each_value_once_for_matching_text_and_tooltip(window):
    class Scalar:
        calls = 0

        def __str__(self):
            self.calls += 1
            return 'formatted scalar'

    value = Scalar()
    window.populate_table(window.data_table, QueryResult(columns=['value'], rows=[{'value': value}]))
    item = window.data_table.item(0, 0)
    assert item.text() == item.toolTip() == 'formatted scalar'
    assert value.calls == 1


@pytest.mark.parametrize('snapshot', [
    RedisValue('hash', 'hash', {'field': 'value'}, size=1),
    RedisValue('binary', 'string', b'\xff', size=1),
])
def test_noneditable_redis_tabs_discard_hidden_string_drafts(window, snapshot):
    connection = Connection(name='Synthetic Redis', kind='Redis', database='0')
    window.connections = [connection]
    string = WorkspaceTab(connection, 'string', redis_snapshot=RedisValue('string', 'string', 'original'))
    other = WorkspaceTab(connection, snapshot.key, redis_snapshot=snapshot)
    window._activate_workspace(string, capture=False)
    window.redis_view.string_editor.setPlainText('unsaved\n中文 draft')
    window._activate_workspace(other)
    window._capture_workspace()

    assert other.redis_draft is None
    assert string.redis_draft == 'unsaved\n中文 draft'
    if snapshot.kind == 'hash':
        assert window.redis_view.string_editor.toPlainText() == ''
    window._activate_workspace(string)
    assert window.redis_view.string_editor.toPlainText() == 'unsaved\n中文 draft'


def test_restoring_redis_selection_updates_entry_actions_once(window, monkeypatch):
    connection = Connection(name='Redis collection', kind='Redis', database='0')
    state = WorkspaceTab(connection, 'hash', redis_snapshot=RedisValue('hash', 'hash', {'field': 'value'}, size=1))
    state.views['redis'] = {'selected': [0], 'current': (0, 0)}
    window.connections = [connection]
    selected_updates = []
    update = window.redis_view._update_controls

    def observed():
        if window.redis_view.table.selectionModel().selectedRows():
            selected_updates.append(True)
        update()

    monkeypatch.setattr(window.redis_view, '_update_controls', observed)
    window._activate_workspace(state, capture=False)
    assert window.redis_view.table.selectionModel().selectedRows()[0].row() == 0
    assert window.redis_view.edit_button.isEnabled()
    assert window.redis_view.delete_button.isEnabled()
    assert window.redis_view.view_button.isEnabled()
    assert selected_updates == [True]


@pytest.mark.parametrize('text,editable', [
    ('ordinary\ttext\n中文😀', True), ('', True), ('\u200b\u061c\ue000\ufdd2\uffff', True),
    ('\r', False), ('\x00', False), ('\x08', False), ('\x0b', False), ('\x7f', False),
    ('\x85', False), ('\x9f', False), ('\u00a0', False), ('\u2028', False), ('\u2029', False),
    ('\ufdd0', False), ('\ufdd1', False), ('\ufeff', False), ('\ufffe', False),
    ('\ud800', False), ('\udfff', False), (b'text', False), (None, False),
])
def test_redis_editability_retains_unicode_and_normalization_rules(text, editable):
    assert is_editable_text(text) is editable
