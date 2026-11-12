"""Redis key separators remain literal connection preferences in native dialogs."""
from dataclasses import replace

import pytest
from PySide6.QtWidgets import QDialog

from baseskiff.models import Connection
from baseskiff.ui.dialogs import ConnectionDialog


@pytest.mark.parametrize("language,label,hint", [
    ("zh", "键分隔符", "留空"),
    ("en", "Key separator", "Leave empty"),
])
def test_redis_separator_defaults_and_localization(qtbot, language, label, hint):
    dialog = ConnectionDialog(kind="Redis", language=language)
    qtbot.addWidget(dialog)
    dialog.show()
    assert dialog.redis_separator_edit.isVisible()
    assert dialog.redis_separator_edit.text() == ":"
    assert dialog.redis_separator_edit.objectName() == "connection_redis_separator"
    assert dialog.form.labelForField(dialog.redis_separator_row).text() == label
    assert hint in dialog.redis_separator_hint.text()
    assert ":" in dialog.redis_separator_hint.text()


@pytest.mark.parametrize("separator", ["", "::", "/", " 分组 "])
def test_redis_separator_round_trips_through_edit_save_and_test(qtbot, separator):
    original = Connection("Redis", kind="Redis", port=6379)
    dialog = ConnectionDialog(connection=original, language="en")
    qtbot.addWidget(dialog)
    dialog.redis_separator_edit.setText(separator)
    with qtbot.waitSignal(dialog.test_requested) as emitted:
        dialog.test_button.click()
    assert emitted.args[0] == replace(original, redis_separator=separator)
    dialog.accept()
    assert dialog.result() == QDialog.Accepted
    assert dialog.result_connection().redis_separator == separator
    assert original.redis_separator == ":"


@pytest.mark.parametrize("kind", ["MySQL", "PostgreSQL", "MongoDB", "Oracle", "SQLite", "Redis"])
def test_custom_separator_is_preserved_and_only_shown_for_redis(qtbot, kind):
    original = Connection("Stored preference", kind=kind, redis_separator="||")
    dialog = ConnectionDialog(connection=original, language="en")
    qtbot.addWidget(dialog)
    dialog.show()
    assert dialog.redis_separator_edit.text() == "||"
    assert dialog.redis_separator_row.isVisible() is (kind == "Redis")
    assert dialog.redis_separator_hint.isVisible() is (kind == "Redis")
    assert dialog.current_connection().redis_separator == "||"


def test_unrelated_connection_fields_are_preserved(qtbot):
    original = Connection("Redis", kind="Redis", host="cache.internal", port=6380,
                          database="2", username="operator", password="db-secret", ssl=True,
                          group="Development", read_only=True, redis_separator="/",
                          ssh_enabled=True, ssh_host="bastion.example", ssh_username="ssh-user",
                          ssh_password="ssh-secret")
    dialog = ConnectionDialog(connection=original, language="en")
    qtbot.addWidget(dialog)
    dialog.redis_separator_edit.setText(" → ")
    assert dialog.current_connection() == replace(original, redis_separator=" → ")
