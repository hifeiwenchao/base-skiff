"""Redis's native editor dispatches precise entry mutations without coercing text."""
from importlib import import_module, util
from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox


def value(kind, data, *, key='customer:<alice>', size=None, ttl=-1, truncated=False):
    return SimpleNamespace(key=key, kind=kind, value=data,
                           size=len(data) if size is None else size,
                           ttl_ms=ttl, truncated=truncated)


def make_widget(qtbot, snapshot, *, read_only=False, language='en'):
    assert util.find_spec('baseskiff.ui.redis_value') is not None, 'Redis value editor is missing'
    widget = import_module('baseskiff.ui.redis_value').RedisValueWidget(language=language)
    qtbot.addWidget(widget)
    widget.resize(900, 600)
    widget.set_value(snapshot, read_only=read_only)
    widget.show()
    return widget


def open_edit(qtbot, widget, row=0):
    widget.table.selectRow(row)
    qtbot.mouseClick(widget.edit_button, Qt.MouseButton.LeftButton)
    assert widget.entry_dialog.isVisible()
    return widget.entry_dialog


def test_hash_edit_locks_field_and_dispatches_literal_value_with_expected(qtbot):
    widget = make_widget(qtbot, value('hash', {'001': '{"amount": 1}', 'name': '<b>Alice</b>'}))
    assert widget.key_label.text() == 'customer:<alice>'
    assert widget.key_label.textFormat() == Qt.TextFormat.PlainText
    assert widget.table.rowCount() == 2
    assert widget.table.item(1, 1).text() == '<b>Alice</b>'
    dialog = open_edit(qtbot, widget)
    assert dialog.entry_edit.text() == '001'
    assert dialog.entry_edit.isReadOnly()
    dialog.value_editor.setPlainText('0007\ntrue')
    with qtbot.waitSignal(widget.mutation_requested) as signal:
        qtbot.mouseClick(dialog.save_button, Qt.MouseButton.LeftButton)
    assert signal.args == ['update', {'entry': '001', 'value': '0007\ntrue', 'expected': '{"amount": 1}'}]
    assert not widget.edit_button.isEnabled()
    assert widget.table.item(0, 1).text() == '{"amount": 1}'


def test_hash_add_and_per_row_delete_dispatch_exact_field(qtbot, monkeypatch):
    widget = make_widget(qtbot, value('hash', {'old': 'false'}))
    qtbot.mouseClick(widget.add_button, Qt.MouseButton.LeftButton)
    dialog = widget.entry_dialog
    dialog.entry_edit.setText('new:field')
    dialog.value_editor.setPlainText('001')
    with qtbot.waitSignal(widget.mutation_requested) as signal:
        qtbot.mouseClick(dialog.save_button, Qt.MouseButton.LeftButton)
    assert signal.args == ['add', {'entry': 'new:field', 'value': '001'}]
    widget.set_busy(False)
    monkeypatch.setattr(QMessageBox, 'question', lambda *args, **kwargs: QMessageBox.StandardButton.Yes)
    with qtbot.waitSignal(widget.mutation_requested) as signal:
        qtbot.mouseClick(widget.table.cellWidget(0, 2).findChild(
            import_module('PySide6.QtWidgets').QPushButton, 'redisRowDelete'), Qt.MouseButton.LeftButton)
    assert signal.args == ['delete', {'entry': 'old', 'expected': 'false'}]


@pytest.mark.parametrize('kind,data,entry,new,expected', [
    ('list', ['001', 'true'], 1, '{"unchanged": false}', 'true'),
    ('set', ['alice', 'bob'], 'bob', 'bob:renamed', None),
    ('zset', [('alice', 1.5), ('bob', 2.0)], 'bob', '3.25', 2.0),
])
def test_collection_updates_use_entry_identity(qtbot, kind, data, entry, new, expected):
    widget = make_widget(qtbot, value(kind, data))
    dialog = open_edit(qtbot, widget, 1)
    dialog.value_editor.setPlainText(new)
    with qtbot.waitSignal(widget.mutation_requested) as signal:
        qtbot.mouseClick(dialog.save_button, Qt.MouseButton.LeftButton)
    payload = {'entry': entry, 'value': new}
    if expected is not None:
        payload['expected'] = expected
    assert signal.args == ['update', payload]


@pytest.mark.parametrize('kind,entry,new,payload', [
    ('list', '', '001', {'value': '001'}),
    ('set', '', '<member>', {'value': '<member>'}),
    ('zset', 'alice', '2.5', {'entry': 'alice', 'value': '2.5'}),
])
def test_collection_adds_use_raw_text(qtbot, kind, entry, new, payload):
    widget = make_widget(qtbot, value(kind, []))
    qtbot.mouseClick(widget.add_button, Qt.MouseButton.LeftButton)
    dialog = widget.entry_dialog
    dialog.entry_edit.setText(entry)
    dialog.value_editor.setPlainText(new)
    with qtbot.waitSignal(widget.mutation_requested) as signal:
        qtbot.mouseClick(dialog.save_button, Qt.MouseButton.LeftButton)
    assert signal.args == ['add', payload]


@pytest.mark.parametrize('score', ['NaN', 'Infinity', '-inf', 'not a number', ''])
def test_zset_dialog_rejects_nonfinite_score_without_emitting(qtbot, score):
    widget = make_widget(qtbot, value('zset', [('alice', 1)]))
    dialog = open_edit(qtbot, widget)
    dialog.value_editor.setPlainText(score)
    emitted = []
    widget.mutation_requested.connect(lambda *args: emitted.append(args))
    qtbot.mouseClick(dialog.save_button, Qt.MouseButton.LeftButton)
    assert dialog.isVisible()
    assert dialog.error_label.isVisible()
    assert not emitted


def test_string_save_and_json_preview_preserve_original_literal(qtbot):
    raw = '{"value":001}'
    widget = make_widget(qtbot, value('string', raw, ttl=1500))
    assert widget.string_editor.toPlainText() == raw
    widget.string_editor.setPlainText('{"value": 1, "text": "中文"}')
    qtbot.mouseClick(widget.json_button, Qt.MouseButton.LeftButton)
    assert widget.preview_dialog.isVisible()
    assert '中文' in widget.preview_editor.toPlainText()
    assert '\n' in widget.preview_editor.toPlainText()
    assert widget.string_editor.toPlainText() == '{"value": 1, "text": "中文"}'
    widget.preview_dialog.close()
    with qtbot.waitSignal(widget.mutation_requested) as signal:
        qtbot.mouseClick(widget.save_button, Qt.MouseButton.LeftButton)
    assert signal.args == ['update', {'value': '{"value": 1, "text": "中文"}', 'expected': raw}]
    assert '1.5' in widget.ttl_label.text()


def test_read_only_preserves_copy_and_refresh_but_blocks_mutations(qtbot):
    widget = make_widget(qtbot, value('hash', {'secret': '001'}), read_only=True)
    widget.table.selectRow(0)
    assert not widget.add_button.isEnabled()
    assert not widget.edit_button.isEnabled()
    assert not widget.delete_button.isEnabled()
    qtbot.mouseClick(widget.copy_button, Qt.MouseButton.LeftButton)
    assert QApplication.clipboard().text() == 'secret\t001'
    with qtbot.waitSignal(widget.refresh_requested):
        qtbot.mouseClick(widget.refresh_button, Qt.MouseButton.LeftButton)
    widget.set_value(value('string', 'original'), read_only=True)
    assert widget.string_editor.isReadOnly()
    assert not widget.save_button.isEnabled()


def test_language_switch_keeps_string_draft_and_relabels_ttl(qtbot):
    widget = make_widget(qtbot, value('string', 'old'), language='zh')
    widget.string_editor.setPlainText('unsaved\n001')
    assert '永久' in widget.ttl_label.text()
    widget.set_language('en')
    assert widget.save_button.text() == 'Save value'
    assert 'No expiry' in widget.ttl_label.text()
    assert widget.string_editor.toPlainText() == 'unsaved\n001'
    widget.set_busy(True)
    assert widget.string_editor.isReadOnly()
    assert not widget.save_button.isEnabled()
    widget.set_busy(False)
    assert not widget.string_editor.isReadOnly()


def test_truncated_lists_allow_append_and_edit_but_block_delete(qtbot):
    widget = make_widget(qtbot, value('list', ['one', 'two'], size=2000, truncated=True))
    widget.table.selectRow(0)
    assert widget.add_button.isEnabled()
    assert widget.edit_button.isEnabled()
    assert not widget.delete_button.isEnabled()
    assert '2,000' in widget.count_label.text()
    assert widget.notice_label.isVisible()
    assert '1,000' in widget.notice_label.text()
    widget.set_value(value('hash', {'one': 'two'}, size=2000, truncated=True))
    widget.table.selectRow(0)
    assert widget.delete_button.isEnabled()


def test_stream_preview_and_expired_key_have_no_mutations(qtbot):
    widget = make_widget(qtbot, value('stream', [('1-0', {'field': '001'})]))
    assert widget.table.item(0, 0).text() == '1-0'
    assert '"field": "001"' in widget.table.item(0, 1).text()
    assert not widget.add_button.isEnabled()
    assert not widget.edit_button.isEnabled()
    widget.set_value(value('none', None, size=0, ttl=-2))
    assert widget.empty_label.isVisible()
    assert widget.table.rowCount() == 0
    assert not widget.add_button.isEnabled()
    assert 'expired' in widget.empty_label.text()
    widget.clear()
    assert widget.key_label.text() == ''


def test_delete_cancel_never_emits_and_full_multiline_cell_copies(qtbot, monkeypatch):
    widget = make_widget(qtbot, value('hash', {'one': 'x' * 10000 + '\nline'}))
    widget.table.selectRow(0)
    qtbot.mouseClick(widget.copy_button, Qt.MouseButton.LeftButton)
    assert QApplication.clipboard().text() == 'one\t' + 'x' * 10000 + '\nline'
    monkeypatch.setattr(QMessageBox, 'question', lambda *args, **kwargs: QMessageBox.StandardButton.No)
    emitted = []
    widget.mutation_requested.connect(lambda *args: emitted.append(args))
    qtbot.mouseClick(widget.delete_button, Qt.MouseButton.LeftButton)
    assert not emitted


def test_native_edit_button_focuses_full_value_editor(qtbot):
    widget = make_widget(qtbot, value('hash', {'field': 'line one\nline two'}))
    open_edit(qtbot, widget)
    assert widget.entry_dialog.isVisible()
    assert widget.entry_dialog.value_editor.toPlainText() == 'line one\nline two'
    qtbot.waitUntil(lambda: widget.entry_dialog.value_editor.hasFocus())


def test_deletion_confirmation_does_not_apply_to_new_snapshot(qtbot, monkeypatch):
    widget = make_widget(qtbot, value('hash', {'old-field': 'old-value'}))
    widget.table.selectRow(0)

    def replaced_during_confirmation(*_args, **_kwargs):
        widget.set_value(value('hash', {'new-field': 'new-value'}, key='other:key'))
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, 'question', replaced_during_confirmation)
    emitted = []
    widget.mutation_requested.connect(lambda *args: emitted.append(args))
    qtbot.mouseClick(widget.delete_button, Qt.MouseButton.LeftButton)
    assert not emitted


def test_empty_string_can_be_saved_and_persistent_ttl_remains_visible(qtbot):
    widget = make_widget(qtbot, value('string', 'old', ttl=0))
    widget.string_editor.clear()
    with qtbot.waitSignal(widget.mutation_requested) as signal:
        qtbot.mouseClick(widget.save_button, Qt.MouseButton.LeftButton)
    assert signal.args == ['update', {'value': '', 'expected': 'old'}]
    assert widget.ttl_label.text() == 'TTL: 0 seconds'


def test_zset_dictionary_displays_scores_without_losing_member_identity(qtbot):
    widget = make_widget(qtbot, value('zset', {'001': 1.25}))
    assert widget.table.item(0, 0).text() == '001'
    assert widget.table.item(0, 1).text() == '1.25'
    dialog = open_edit(qtbot, widget)
    assert dialog.entry_edit.isReadOnly()
    assert dialog.entry_edit.text() == '001'


def test_search_matches_literal_field_and_value_and_keeps_original_row_identity(qtbot):
    widget = make_widget(qtbot, value('hash', {'alice': 'one', '001[field]': 'VALUE.*', 'last': 'three'}))
    widget.entry_search.setText('[field]')
    assert widget.table.isRowHidden(0)
    assert not widget.table.isRowHidden(1)
    assert widget.table.isRowHidden(2)
    widget.entry_search.setText('value.*')
    assert not widget.table.isRowHidden(1)
    widget.table.selectRow(1)
    dialog = open_edit(qtbot, widget, 1)
    assert dialog.entry_edit.text() == '001[field]'
    assert dialog.value_editor.toPlainText() == 'VALUE.*'
    dialog.value_editor.setPlainText('updated 001')
    with qtbot.waitSignal(widget.mutation_requested) as signal:
        qtbot.mouseClick(dialog.save_button, Qt.MouseButton.LeftButton)
    assert signal.args == ['update', {'entry': '001[field]', 'value': 'updated 001', 'expected': 'VALUE.*'}]
    widget.set_busy(False)
    widget.entry_search.setText('last')
    assert widget.table.currentRow() == -1
    assert not widget.edit_button.isEnabled()
    assert not widget.delete_button.isEnabled()
    widget.entry_search.clear()
    assert not any(widget.table.isRowHidden(row) for row in range(3))


def test_search_draft_survives_language_and_same_key_refresh_but_resets_on_new_key(qtbot):
    widget = make_widget(qtbot, value('set', ['first', 'second']), language='zh')
    widget.entry_search.setText('SECOND')
    assert '预览' in widget.entry_search.placeholderText()
    widget.set_language('en')
    assert widget.entry_search.text() == 'SECOND'
    assert 'loaded preview' in widget.entry_search.placeholderText()
    assert widget.table.isRowHidden(0)
    widget.set_value(value('set', ['second', 'third']))
    assert widget.entry_search.text() == 'SECOND'
    assert not widget.table.isRowHidden(0)
    assert widget.table.isRowHidden(1)
    widget.set_value(value('set', ['first'], key='other:key'))
    assert widget.entry_search.text() == ''
    assert not widget.table.isRowHidden(0)
    widget.set_value(value('string', 'text'))
    assert widget.entry_search.isHidden()
    widget.clear()
    assert widget.entry_search.isHidden()


@pytest.mark.parametrize('kind,data,query', [
    ('list', ['one', '002'], '002'),
    ('set', ['one', '中文成员'], '中文'),
    ('zset', {'one': 1.0, 'two': 32.5}, '32.5'),
    ('stream', [('1-0', {'field': 'one'}), ('2-0', {'field': 'two'})], '"two"'),
])
def test_search_includes_typed_collection_preview_contents(qtbot, kind, data, query):
    widget = make_widget(qtbot, value(kind, data))
    widget.entry_search.setText(query)
    assert widget.table.isRowHidden(0)
    assert not widget.table.isRowHidden(1)


@pytest.mark.parametrize('action', ['add', 'update'])
def test_failed_entry_write_restores_exact_draft_and_expected_for_retry(qtbot, action):
    widget = make_widget(qtbot, value('hash', {'001': 'old\nvalue'}))
    if action == 'update':
        dialog = open_edit(qtbot, widget)
    else:
        qtbot.mouseClick(widget.add_button, Qt.MouseButton.LeftButton)
        dialog = widget.entry_dialog
        dialog.entry_edit.setText('new:<field>')
    draft = '001\n{"raw": true}\n<literal>'
    dialog.value_editor.setPlainText(draft)
    with qtbot.waitSignal(widget.mutation_requested) as first:
        qtbot.mouseClick(dialog.save_button, Qt.MouseButton.LeftButton)
    assert not dialog.isVisible()
    widget.set_busy(False)
    assert widget.restore_failed_edit('Conflict: <b>refresh required</b>')
    assert widget.entry_dialog is dialog
    assert dialog.isVisible()
    assert dialog.value_editor.toPlainText() == draft
    assert dialog.entry_edit.text() == ('001' if action == 'update' else 'new:<field>')
    assert dialog.error_label.text() == 'Conflict: <b>refresh required</b>'
    assert dialog.error_label.textFormat() == Qt.TextFormat.PlainText
    assert dialog.error_label.isVisible()
    with qtbot.waitSignal(widget.mutation_requested) as retry:
        qtbot.mouseClick(dialog.save_button, Qt.MouseButton.LeftButton)
    assert retry.args == first.args
    widget.set_busy(False)
    dialog.open()
    assert dialog.error_label.isHidden()
    dialog.reject()


def test_failed_dialog_is_not_restored_after_snapshot_refresh_or_key_switch(qtbot):
    widget = make_widget(qtbot, value('hash', {'field': 'old'}))
    dialog = open_edit(qtbot, widget)
    dialog.value_editor.setPlainText('draft')
    qtbot.mouseClick(dialog.save_button, Qt.MouseButton.LeftButton)
    widget.set_value(value('hash', {'field': 'new'}))
    assert not widget.restore_failed_edit('stale error')
    assert not dialog.isVisible()
    dialog = open_edit(qtbot, widget)
    qtbot.mouseClick(dialog.save_button, Qt.MouseButton.LeftButton)
    widget.set_value(value('hash', {'other': 'new'}, key='other:key'))
    assert not widget.restore_failed_edit('stale error')
    assert not dialog.isVisible()


def test_failed_zset_retry_replaces_backend_error_with_score_validation(qtbot):
    widget = make_widget(qtbot, value('zset', {'member': 1.0}))
    dialog = open_edit(qtbot, widget)
    dialog.value_editor.setPlainText('2.0')
    qtbot.mouseClick(dialog.save_button, Qt.MouseButton.LeftButton)
    widget.set_busy(False)
    assert widget.restore_failed_edit('backend failure')
    dialog.value_editor.setPlainText('NaN')
    qtbot.mouseClick(dialog.save_button, Qt.MouseButton.LeftButton)
    assert dialog.isVisible()
    assert dialog.error_label.text() == 'The score must be a finite number.'


def test_canceled_restored_draft_does_not_replace_later_delete_failure(qtbot, monkeypatch):
    widget = make_widget(qtbot, value('hash', {'first': 'one', 'second': 'two'}))
    dialog = open_edit(qtbot, widget)
    dialog.value_editor.setPlainText('canceled draft')
    qtbot.mouseClick(dialog.save_button, Qt.MouseButton.LeftButton)
    widget.set_busy(False)
    assert widget.restore_failed_edit('edit failed')
    dialog.reject()
    widget.table.selectRow(1)
    monkeypatch.setattr(QMessageBox, 'question', lambda *args, **kwargs: QMessageBox.StandardButton.Yes)
    with qtbot.waitSignal(widget.mutation_requested) as deletion:
        qtbot.mouseClick(widget.delete_button, Qt.MouseButton.LeftButton)
    assert deletion.args == ['delete', {'entry': 'second', 'expected': 'two'}]
    widget.set_busy(False)
    assert not widget.restore_failed_edit('delete failed')
    assert not dialog.isVisible()
