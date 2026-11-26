"""Redis layout preferences and draft previews preserve explicit writes."""
import json

import msgpack
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from tests.test_redis_value_ui import make_widget, open_edit, value


def test_long_key_header_keeps_full_copy_without_forcing_window_width(qtbot):
    key = 'customer:' + 'x' * 5000
    widget = make_widget(qtbot, value('string', 'text', key=key))
    widget.resize(680, 480)
    qtbot.waitUntil(lambda: widget.width() == 680)
    assert widget.key_label.toolTip() == key
    assert widget.key_label.text() == key
    qtbot.mouseClick(widget.key_copy_button, Qt.MouseButton.LeftButton)
    assert QApplication.clipboard().text() == key
    QApplication.clipboard().clear()


def test_copy_binary_key_preserves_original_bytes(qtbot):
    key = b'\xff\x00' * 2500
    widget = make_widget(qtbot, value('string', 'text', key=key))
    qtbot.mouseClick(widget.key_copy_button, Qt.MouseButton.LeftButton)
    assert bytes(QApplication.clipboard().mimeData().data('application/octet-stream')) == key
    assert key.hex(' ') in widget.key_label.toolTip()
    QApplication.clipboard().clear()


def test_partial_collection_header_shows_loaded_and_total(qtbot):
    widget = make_widget(qtbot, value('list', ['one', 'two'], size=1200, truncated=True))
    assert widget.count_label.text() == '2 / 1,200 items'


def test_string_draft_preview_is_local_and_save_retains_msgpack(qtbot):
    raw = msgpack.packb({'enabled': True}, use_bin_type=True)
    widget = make_widget(qtbot, value('string', raw))
    emitted = []
    widget.mutation_requested.connect(lambda *args: emitted.append(args))
    qtbot.mouseClick(widget.preview_toggle, Qt.MouseButton.LeftButton)
    widget.string_editor.setPlainText('{"enabled":false,"count":2}')
    qtbot.waitUntil(lambda: '"count": 2' in widget.string_preview.toPlainText())
    assert widget.string_preview.isReadOnly()
    assert not emitted
    qtbot.mouseClick(widget.save_button, Qt.MouseButton.LeftButton)
    assert msgpack.unpackb(emitted[0][1]['value_bytes'], raw=False) == {'enabled': False, 'count': 2}


def test_entry_draft_preview_does_not_rewrite_raw_json_text(qtbot):
    widget = make_widget(qtbot, value('hash', {'field': '{"count":1}'}))
    dialog = open_edit(qtbot, widget)
    qtbot.mouseClick(dialog.preview_toggle, Qt.MouseButton.LeftButton)
    draft = '{"count":2}'
    dialog.value_editor.setPlainText(draft)
    qtbot.waitUntil(lambda: '"count": 2' in dialog.value_preview.toPlainText())
    assert dialog.value_preview.isReadOnly()
    assert json.loads(dialog.value_preview.toPlainText()) == {'count': 2}
    assert dialog.value_editor.toPlainText() == draft
    with qtbot.waitSignal(widget.mutation_requested) as signal:
        qtbot.mouseClick(dialog.save_button, Qt.MouseButton.LeftButton)
    assert signal.args[1]['value'] == draft


def test_redis_display_preferences_update_rows_and_open_dialog(qtbot):
    widget = make_widget(qtbot, value('hash', {'field': 'value'}))
    widget.setStyleSheet('QPlainTextEdit { font-size: 12px; }')
    widget.set_display_preferences('compact', 12)
    compact_height = widget.table.verticalHeader().defaultSectionSize()
    dialog = open_edit(qtbot, widget)
    widget.set_display_preferences('comfortable', 16)
    assert widget.table.verticalHeader().defaultSectionSize() > compact_height
    assert widget.string_editor.font().pointSize() == 16
    assert dialog.value_editor.font().pointSize() == 16
