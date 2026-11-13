"""Redis TTL editing is bounded, explicit and scoped to the displayed key."""
from types import SimpleNamespace

from PySide6.QtCore import Qt

from baseskiff.ui.redis_value import RedisValueWidget


def snapshot(kind='string', *, key='key', ttl_ms=2229870000, value='value'):
    return SimpleNamespace(key=key, kind=kind, value=value, size=1,
                           ttl_ms=ttl_ms, truncated=False)


def make_widget(qtbot, snap, *, read_only=False, language='en'):
    widget = RedisValueWidget(language=language)
    qtbot.addWidget(widget)
    widget.set_value(snap, read_only=read_only)
    widget.show()
    return widget


def test_ttl_display_uses_fixed_point_and_emits_positive_seconds(qtbot):
    widget = make_widget(qtbot, snapshot())
    assert widget.ttl_label.text() == 'TTL: 2,229,870 seconds'
    qtbot.mouseClick(widget.ttl_button, Qt.MouseButton.LeftButton)
    dialog = widget.ttl_dialog
    assert dialog.isVisible()
    assert dialog.seconds_edit.text() == '2229870'
    emitted = []
    widget.ttl_requested.connect(emitted.append)
    dialog.seconds_edit.setText('90')
    qtbot.mouseClick(dialog.save_button, Qt.MouseButton.LeftButton)
    assert emitted == [90]


def test_ttl_dialog_supports_no_expiry_and_rejects_zero(qtbot):
    widget = make_widget(qtbot, snapshot(ttl_ms=1500))
    qtbot.mouseClick(widget.ttl_button, Qt.MouseButton.LeftButton)
    dialog = widget.ttl_dialog
    dialog.seconds_edit.setText('0')
    qtbot.mouseClick(dialog.save_button, Qt.MouseButton.LeftButton)
    assert dialog.isVisible()
    assert dialog.error_label.isVisible()
    dialog.no_expiry.setChecked(True)
    emitted = []
    widget.ttl_requested.connect(emitted.append)
    qtbot.mouseClick(dialog.save_button, Qt.MouseButton.LeftButton)
    assert emitted == [None]


def test_ttl_disabled_for_read_only_missing_and_busy(qtbot):
    widget = make_widget(qtbot, snapshot(), read_only=True)
    assert not widget.ttl_button.isEnabled()
    widget.set_value(snapshot(kind='none', value=None, ttl_ms=-2))
    assert not widget.ttl_button.isEnabled()
    widget.set_value(snapshot())
    widget.set_busy(True)
    assert not widget.ttl_button.isEnabled()


def test_ttl_result_from_old_key_is_discarded(qtbot):
    widget = make_widget(qtbot, snapshot(key='first'))
    qtbot.mouseClick(widget.ttl_button, Qt.MouseButton.LeftButton)
    dialog = widget.ttl_dialog
    widget.set_value(snapshot(key='second'))
    emitted = []
    widget.ttl_requested.connect(emitted.append)
    dialog.seconds_edit.setText('9')
    qtbot.mouseClick(dialog.save_button, Qt.MouseButton.LeftButton)
    assert emitted == []


def test_ttl_preserves_string_draft_and_localization(qtbot):
    widget = make_widget(qtbot, snapshot(value='original'), language='zh')
    widget.string_editor.setPlainText('unsaved draft')
    qtbot.mouseClick(widget.ttl_button, Qt.MouseButton.LeftButton)
    dialog = widget.ttl_dialog
    assert '设置 TTL' in widget.ttl_button.text()
    assert '秒' in dialog.seconds_edit.placeholderText()
    dialog.no_expiry.setChecked(True)
    dialog.save_button.click()
    assert widget.string_editor.toPlainText() == 'unsaved draft'
