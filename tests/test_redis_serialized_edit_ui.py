"""Editable Redis serialization drafts keep their original bytes and codec."""
import gzip
import json

import msgpack
import pytest
from PySide6.QtCore import Qt

from tests.test_redis_value_ui import make_widget, open_edit, value


@pytest.mark.parametrize(
    "raw,expected_name,updated",
    [
        (b'{"name":"Alice","count":1}', "JSON", {"name": "Bob", "count": 2}),
        (msgpack.packb({"name": "Alice", "count": 1}, use_bin_type=True), "MessagePack", {"name": "Bob", "count": 2}),
        (gzip.compress(b'{"name":"Alice","count":1}', mtime=0), "gzip", {"name": "Bob", "count": 2}),
    ],
)
def test_serialized_list_value_can_be_edited_and_emits_bytes(qtbot, raw, expected_name, updated):
    widget = make_widget(qtbot, value("list", [raw]))
    dialog = open_edit(qtbot, widget)
    assert not dialog.value_editor.isReadOnly()
    assert expected_name in dialog.hint_label.text() or expected_name in dialog.entry_preview_label.text()
    dialog.value_editor.setPlainText(json.dumps(updated, ensure_ascii=False, indent=2))
    with qtbot.waitSignal(widget.mutation_requested) as signal:
        qtbot.mouseClick(dialog.save_button, Qt.MouseButton.LeftButton)
    payload = signal.args[1]
    assert payload["expected_bytes"] == raw
    assert payload["value_bytes"] != raw
    assert payload.get("value") is None
    assert msgpack.unpackb(payload["value_bytes"], raw=False) == updated if expected_name == "MessagePack" else json.loads(
        gzip.decompress(payload["value_bytes"])
        if expected_name == "gzip" else payload["value_bytes"]
    ) == updated


def test_serialized_editor_rejects_invalid_json_without_emitting(qtbot):
    raw = b'{"name":"Alice"}'
    widget = make_widget(qtbot, value("list", [raw]))
    dialog = open_edit(qtbot, widget)
    dialog.value_editor.setPlainText("{ invalid")
    emitted = []
    widget.mutation_requested.connect(lambda *args: emitted.append(args))
    qtbot.mouseClick(dialog.save_button, Qt.MouseButton.LeftButton)
    assert dialog.isVisible()
    assert dialog.error_label.isVisible()
    assert not emitted


def test_unknown_binary_remains_read_only(qtbot):
    widget = make_widget(qtbot, value("list", [b"\xff\x00\xfe"]))
    widget._edit(0)
    assert widget.entry_dialog is None
    assert widget.preview_dialog.isVisible()
    assert widget.preview_editor.isReadOnly()


def test_serialized_string_value_can_be_saved_as_bytes(qtbot):
    raw = msgpack.packb({"enabled": True}, use_bin_type=True)
    widget = make_widget(qtbot, value("string", raw))
    assert not widget.string_editor.isReadOnly()
    widget.string_editor.setPlainText('{"enabled": false}')
    with qtbot.waitSignal(widget.mutation_requested) as signal:
        qtbot.mouseClick(widget.save_button, Qt.MouseButton.LeftButton)
    payload = signal.args[1]
    assert payload["expected_bytes"] == raw
    assert msgpack.unpackb(payload["value_bytes"], raw=False) == {"enabled": False}


def test_serialized_set_member_keeps_binary_identity_for_writeback(qtbot):
    raw = msgpack.packb({"member": "old"}, use_bin_type=True)
    widget = make_widget(qtbot, value("set", [raw]))
    dialog = open_edit(qtbot, widget)
    dialog.value_editor.setPlainText('{"member":"new"}')
    with qtbot.waitSignal(widget.mutation_requested) as signal:
        qtbot.mouseClick(dialog.save_button, Qt.MouseButton.LeftButton)
    payload = signal.args[1]
    assert payload["entry_bytes"] == raw
    assert payload["expected_bytes"] == raw
    assert msgpack.unpackb(payload["value_bytes"], raw=False) == {"member": "new"}
