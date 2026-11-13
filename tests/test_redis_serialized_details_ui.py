"""Serialized Redis details are readable while edits keep their raw identities."""
import gzip
import zlib

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog

from baseskiff.ui.redis_preview import RedisPreviewDialog
from tests.test_redis_value_ui import make_widget, open_edit, value


def test_score_details_decode_member_and_save_only_score(qtbot):
    member = b'\x82\xa4name\xa5Alice\xa5count\x03'
    widget = make_widget(qtbot, value('zset', [(member, 1.5)]))
    dialog = open_edit(qtbot, widget)
    assert dialog.entry_preview_editor.isVisible()
    assert dialog.entry_preview_editor.isReadOnly()
    assert '"name": "Alice"' in dialog.entry_preview_editor.toPlainText()
    assert 'MessagePack' in dialog.entry_preview_label.text()
    assert dialog.entry_edit.isReadOnly()
    dialog.value_editor.setPlainText('2.5')
    with qtbot.waitSignal(widget.mutation_requested) as signal:
        qtbot.mouseClick(dialog.save_button, Qt.MouseButton.LeftButton)
    assert signal.args == ['update', {'entry': member, 'value': '2.5', 'expected': 1.5}]


def test_hash_details_decode_field_without_changing_raw_value_draft(qtbot):
    field = b'\x81\xa2id\x2a'
    widget = make_widget(qtbot, value('hash', {field: '{"amount":1}'}))
    dialog = open_edit(qtbot, widget)
    assert '"id": 42' in dialog.entry_preview_editor.toPlainText()
    assert dialog.value_editor.toPlainText() == '{"amount":1}'
    dialog.value_editor.setPlainText('0007\ntrue')
    qtbot.mouseClick(dialog.entry_view_button, Qt.MouseButton.LeftButton)
    preview = dialog.preview_dialog
    assert preview.isVisible()
    assert preview.raw_value == field
    assert '"id": 42' in preview.editor.toPlainText()
    widget.set_language('zh')
    assert '字段' in dialog.entry_view_button.text()
    assert '只读' in dialog.entry_preview_label.text()
    assert '已识别' in preview.detected_label.text()
    preview.close()
    with qtbot.waitSignal(widget.mutation_requested) as signal:
        qtbot.mouseClick(dialog.save_button, Qt.MouseButton.LeftButton)
    assert signal.args == ['update', {'entry': field, 'value': '0007\ntrue', 'expected': '{"amount":1}'}]


def test_hash_preview_can_inspect_binary_field_and_value_and_export_each(qtbot, monkeypatch, tmp_path):
    field, raw_value = b'\x81\xa2id\x2a', b'\x81\xa4name\xa5Alice'
    widget = make_widget(qtbot, value('hash', {field: raw_value}))
    widget.table.selectRow(0)
    qtbot.mouseClick(widget.view_button, Qt.MouseButton.LeftButton)
    preview = widget.preview_dialog
    assert preview.raw_value == raw_value
    assert '"name": "Alice"' in preview.editor.toPlainText()
    assert preview.value_combo.isVisible()
    destination = tmp_path / 'value.bin'
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *args, **kwargs: (str(destination), ''))
    qtbot.mouseClick(preview.export_button, Qt.MouseButton.LeftButton)
    assert destination.read_bytes() == raw_value
    preview.value_combo.setCurrentIndex(1)
    assert '"id": 42' in preview.editor.toPlainText()
    qtbot.mouseClick(preview.export_button, Qt.MouseButton.LeftButton)
    assert destination.read_bytes() == field
    widget.set_language('zh')
    assert preview.value_combo.currentText() == '字段'
    assert preview.value_combo.currentIndex() == 1


@pytest.mark.parametrize('member,detected,content', [
    ('{"name":"Alice"}', 'JSON', '"name": "Alice"'),
    (b'\xff\x00\xfe', 'Hex', 'ff 00 fe'),
])
def test_score_details_show_json_or_raw_binary_fallback(qtbot, member, detected, content):
    widget = make_widget(qtbot, value('zset', [(member, 1.5)]))
    dialog = open_edit(qtbot, widget)
    assert content in dialog.entry_preview_editor.toPlainText()
    assert detected in dialog.entry_preview_label.text()


@pytest.mark.parametrize('raw,format_name,detected', [
    (b'{"x":1}', 'json', 'JSON'),
    (b'\x81\xa1x\x01', 'msgpack', 'MessagePack'),
    (b'\x0c\x00\x00\x00\x10x\x00\x01\x00\x00\x00\x00', 'bson', 'BSON'),
    (b'\xa1ax\x01', 'cbor', 'CBOR'),
    (gzip.compress(b'{"x":1}', mtime=0), 'gzip', 'gzip → JSON'),
    (zlib.compress(b'\x81\xa1x\x01'), 'zlib', 'zlib → MessagePack'),
])
def test_preview_exposes_each_serialization_and_detects_chain(qtbot, raw, format_name, detected):
    dialog = RedisPreviewDialog(raw)
    qtbot.addWidget(dialog)
    assert dialog.format_combo.findData(format_name) >= 0
    assert dialog.detected_label.text() == 'Detected: ' + detected
    rendered = dialog.editor.toPlainText()
    assert '"x"' in rendered and '1' in rendered
    dialog.format_combo.setCurrentIndex(dialog.format_combo.findData(format_name))
    rendered = dialog.editor.toPlainText()
    assert '"x"' in rendered and '1' in rendered
    assert dialog.editor.isReadOnly()
    assert dialog.raw_value == raw
