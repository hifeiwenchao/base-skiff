"""Native previews keep Redis bytes intact and cannot save rendered bytes as text."""
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog, QMessageBox

from tests.test_redis_value_ui import make_widget, open_edit, value


@pytest.mark.parametrize('raw', [b'\xff\x00\xfe', 'hello\x00world', 'carriage\rreturn', 'non\u00a0breaking'])
def test_non_roundtrip_string_is_read_only_and_cannot_emit_write(qtbot, raw):
    widget = make_widget(qtbot, value('string', raw))
    assert widget.snapshot.value == raw
    assert widget.string_editor.isReadOnly()
    assert not widget.save_button.isEnabled()
    mutations = []
    widget.mutation_requested.connect(lambda *args: mutations.append(args))
    widget._save_string()
    assert mutations == []


def test_binary_key_is_safe_label_and_keeps_original_identity(qtbot):
    widget = make_widget(qtbot, value('string', 'text', key=b'key:\xff\x00'))
    assert widget.snapshot.key == b'key:\xff\x00'
    assert '\ufffd' not in widget.key_label.text()
    assert '\x00' not in widget.key_label.text()
    assert not widget.key_label.text().startswith("b'")


@pytest.mark.parametrize('kind,data', [('hash', {'blob': b'\xff\x00'}), ('list', [b'\xff\x00']), ('set', [b'\xff\x00'])])
def test_binary_entry_can_be_previewed_and_deleted_without_text_edit(qtbot, monkeypatch, kind, data):
    widget = make_widget(qtbot, value(kind, data))
    widget.table.selectRow(0)
    assert not widget.edit_button.isEnabled()
    assert widget.view_button.isEnabled()
    qtbot.mouseClick(widget.view_button, Qt.MouseButton.LeftButton)
    assert widget.preview_dialog.isVisible()
    assert widget.preview_editor.isReadOnly()
    assert widget.preview_dialog.raw_value == b'\xff\x00'
    widget.preview_dialog.close()
    monkeypatch.setattr(QMessageBox, 'question', lambda *args, **kwargs: QMessageBox.StandardButton.Yes)
    with qtbot.waitSignal(widget.mutation_requested) as signal:
        qtbot.mouseClick(widget.delete_button, Qt.MouseButton.LeftButton)
    expected = {'entry': 'blob', 'expected': b'\xff\x00'} if kind == 'hash' else \
               {'entry': 0, 'expected': b'\xff\x00'} if kind == 'list' else {'entry': b'\xff\x00'}
    assert signal.args == ['delete', expected]


def test_binary_hash_field_edit_keeps_exact_identity(qtbot):
    field = b'field:\xff\x00'
    widget = make_widget(qtbot, value('hash', {field: 'old'}))
    assert widget._rows == [(field, 'old')]
    dialog = open_edit(qtbot, widget)
    assert '\x00' not in dialog.entry_edit.text()
    dialog.value_editor.setPlainText('new')
    with qtbot.waitSignal(widget.mutation_requested) as signal:
        qtbot.mouseClick(dialog.save_button, Qt.MouseButton.LeftButton)
    assert signal.args == ['update', {'entry': field, 'value': 'new', 'expected': 'old'}]


def test_binary_zset_member_can_edit_score_and_preview_member(qtbot):
    member = b'member:\xff\x00'
    widget = make_widget(qtbot, value('zset', [(member, 1.5)]))
    widget.table.selectRow(0)
    qtbot.mouseClick(widget.view_button, Qt.MouseButton.LeftButton)
    assert widget.preview_dialog.raw_value == member
    widget.preview_dialog.close()
    dialog = open_edit(qtbot, widget)
    dialog.value_editor.setPlainText('2.5')
    with qtbot.waitSignal(widget.mutation_requested) as signal:
        qtbot.mouseClick(dialog.save_button, Qt.MouseButton.LeftButton)
    assert signal.args == ['update', {'entry': member, 'value': '2.5', 'expected': 1.5}]


def test_stream_nested_bytes_display_and_preview_without_coercion(qtbot):
    fields = {b'field:\xff': b'\x81\xa1a\x01', 'text': '普通值'}
    widget = make_widget(qtbot, value('stream', [('1-0', fields)]))
    assert widget._rows == [('1-0', fields)]
    assert '\ufffd' not in widget.table.item(0, 1).text()
    widget.table.selectRow(0)
    qtbot.mouseClick(widget.view_button, Qt.MouseButton.LeftButton)
    assert widget.preview_editor.isReadOnly()
    assert '普通值' in widget.preview_editor.toPlainText()


@pytest.mark.parametrize('kind,raw', [('ReJSON-RL', '{"x": [1, 2]}'), ('module-value', b'\xff\x00\x01')])
def test_unknown_type_has_scalar_preview_and_no_write(qtbot, kind, raw):
    widget = make_widget(qtbot, value(kind, raw))
    assert widget.stack.currentWidget() is widget.string_page
    assert widget.string_editor.toPlainText()
    assert widget.string_editor.isReadOnly()
    assert not widget.save_button.isEnabled()
    qtbot.mouseClick(widget.view_button, Qt.MouseButton.LeftButton)
    assert widget.preview_dialog.raw_value == raw
    if kind == 'module-value':
        assert 'DUMP' in widget.raw_hint_label.text()


def test_preview_switches_format_and_exports_exact_bytes(qtbot, monkeypatch, tmp_path):
    raw = b'\x81\xa1x\x01'
    widget = make_widget(qtbot, value('string', raw))
    qtbot.mouseClick(widget.json_button, Qt.MouseButton.LeftButton)
    dialog = widget.preview_dialog
    assert dialog.raw_value == raw
    combo = dialog.format_combo
    combo.setCurrentIndex(combo.findData('msgpack'))
    assert '"x"' in widget.preview_editor.toPlainText()
    combo.setCurrentIndex(combo.findData('base64'))
    assert widget.preview_editor.toPlainText() == 'gaF4AQ=='
    destination = tmp_path / 'original.bin'
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *args, **kwargs: (str(destination), ''))
    qtbot.mouseClick(dialog.export_button, Qt.MouseButton.LeftButton)
    assert destination.read_bytes() == raw
    widget.set_language('zh')
    assert '导出' in dialog.export_button.text()
    assert combo.currentData() == 'base64'


def test_readonly_json_preview_does_not_write_format_back_to_raw_editor(qtbot):
    raw = '{"x":1}'
    widget = make_widget(qtbot, value('string', raw), read_only=True)
    qtbot.mouseClick(widget.json_button, Qt.MouseButton.LeftButton)
    assert widget.preview_editor.toPlainText() == '{\n  "x": 1\n}'
    assert widget.string_editor.toPlainText() == raw
    assert widget.preview_editor.isReadOnly()
    assert not widget.save_button.isEnabled()


def test_entry_dialog_refuses_unsafe_new_text(qtbot):
    widget = make_widget(qtbot, value('hash', {'old': 'value'}))
    qtbot.mouseClick(widget.add_button, Qt.MouseButton.LeftButton)
    dialog = widget.entry_dialog
    dialog.entry_edit.setText('new\x00field')
    dialog.value_editor.setPlainText('value')
    mutations = []
    widget.mutation_requested.connect(lambda *args: mutations.append(args))
    qtbot.mouseClick(dialog.save_button, Qt.MouseButton.LeftButton)
    assert dialog.isVisible()
    assert dialog.error_label.isVisible()
    assert mutations == []


@pytest.mark.parametrize('raw', [
    'lone\udcffsurrogate', 'paragraph\u2029end', 'line\u2028end', 'delete\x7f',
    'qt\ufdd0marker', 'qt\ufdd1marker', 'byte\ufefforder', 'byte\ufffeorder',
])
def test_special_text_uses_safe_readonly_preview(qtbot, raw):
    widget = make_widget(qtbot, value('string', raw))
    assert widget.string_editor.isReadOnly()
    assert not widget.save_button.isEnabled()
    assert widget.snapshot.value == raw
    mutations = []
    widget.mutation_requested.connect(lambda *args: mutations.append(args))
    widget._save_string()
    assert mutations == []
    qtbot.mouseClick(widget.view_button, Qt.MouseButton.LeftButton)
    assert widget.preview_editor.isReadOnly()
    assert widget.preview_dialog.raw_value == raw


def test_binary_row_double_click_opens_readonly_preview_even_on_readonly_connection(qtbot):
    widget = make_widget(qtbot, value('list', [b'\xff\x00']), read_only=True)
    widget._edit(0)
    assert widget.preview_dialog.isVisible()
    assert widget.preview_dialog.raw_value == b'\xff\x00'
    assert widget.entry_dialog is None


def test_stream_nested_surrogate_is_escaped_in_preview(qtbot):
    widget = make_widget(qtbot, value('stream', [('1-0', {'field': '\udcff'})]))
    widget.table.selectRow(0)
    qtbot.mouseClick(widget.view_button, Qt.MouseButton.LeftButton)
    assert '\\udcff' in widget.preview_editor.toPlainText()


def test_stream_field_selector_previews_and_exports_individual_binary_value(qtbot, monkeypatch, tmp_path):
    raw = b'\x81\xa1x\x01'
    widget = make_widget(qtbot, value('stream', [('1-0', {b'field:\xff': raw})]))
    widget.table.selectRow(0)
    qtbot.mouseClick(widget.view_button, Qt.MouseButton.LeftButton)
    dialog = widget.preview_dialog
    assert not dialog.export_button.isEnabled()
    dialog.value_combo.setCurrentIndex(1)
    dialog.format_combo.setCurrentIndex(dialog.format_combo.findData('msgpack'))
    assert '"x"' in widget.preview_editor.toPlainText()
    destination = tmp_path / 'stream-field.bin'
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *args, **kwargs: (str(destination), ''))
    qtbot.mouseClick(dialog.export_button, Qt.MouseButton.LeftButton)
    assert destination.read_bytes() == raw


def test_preview_notice_is_translated_when_language_changes(qtbot):
    widget = make_widget(qtbot, value('string', b'\xff\x00'))
    qtbot.mouseClick(widget.view_button, Qt.MouseButton.LeftButton)
    dialog = widget.preview_dialog
    dialog.format_combo.setCurrentIndex(dialog.format_combo.findData('json'))
    assert 'Unable to decode' in dialog.notice_label.text()
    widget.set_language('zh')
    assert '无法' in dialog.notice_label.text()
    assert 'Unable to decode' not in dialog.notice_label.text()


def test_auto_preview_shows_detected_format_and_translates_it(qtbot):
    widget = make_widget(qtbot, value('string', b'\x81\xa1x\x01'))
    qtbot.mouseClick(widget.view_button, Qt.MouseButton.LeftButton)
    dialog = widget.preview_dialog
    assert dialog.format_combo.currentData() == 'auto'
    assert dialog.detected_label.text() == 'Detected: MessagePack'
    widget.set_language('zh')
    assert dialog.detected_label.text() == '已识别：MessagePack'
