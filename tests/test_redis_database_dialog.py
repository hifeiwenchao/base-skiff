"""Redis database choice belongs to navigation rather than connection forms."""
from dataclasses import replace

import pytest
from PySide6.QtWidgets import QDialog

from baseskiff.models import Connection
from baseskiff.ui.dialogs import ConnectionDialog


@pytest.mark.parametrize("language", ["zh", "en"])
@pytest.mark.parametrize("editing", [False, True])
def test_redis_connection_form_hides_database_index_and_label(qtbot, language, editing):
    original = Connection("Stored Redis", kind="Redis", database="2") if editing else None
    dialog = ConnectionDialog(connection=original, kind="Redis", language=language)
    qtbot.addWidget(dialog)
    dialog.show()

    assert not dialog.database_edit.isVisible()
    assert not dialog.form.labelForField(dialog.database_edit).isVisible()
    for field in (dialog.host_edit, dialog.port_spin, dialog.username_edit,
                  dialog.password_edit, dialog.redis_separator_row):
        assert field.isVisible()


@pytest.mark.parametrize("language", ["zh", "en"])
def test_new_redis_connection_tests_and_saves_database_zero(qtbot, language):
    dialog = ConnectionDialog(kind="Redis", language=language)
    qtbot.addWidget(dialog)
    dialog.name_edit.setText("New Redis")

    assert dialog.current_connection().database == "0"
    dialog.database_edit.setText("7")
    with qtbot.waitSignal(dialog.test_requested) as emitted:
        dialog.test_button.click()
    assert emitted.args[0].database == "0"
    dialog.accept()
    assert dialog.result() == QDialog.Accepted
    assert dialog.result_connection().database == "0"


@pytest.mark.parametrize("language", ["zh", "en"])
@pytest.mark.parametrize("database", ["2", ""])
def test_edit_redis_connection_retains_selected_database_for_test_and_save(qtbot, language, database):
    original = Connection("Stored Redis", kind="Redis", host="cache.internal", port=6380,
                          database=database, username="operator", password="example-password",
                          ssl=True, read_only=True, redis_separator="/",
                          ssh_enabled=True, ssh_host="bastion.example", ssh_port=2222,
                          ssh_username="ssh-user", ssh_auth_method="agent")
    dialog = ConnectionDialog(connection=original, language=language)
    qtbot.addWidget(dialog)
    dialog.name_edit.setText("Renamed Redis")
    dialog.redis_separator_edit.setText("::")
    dialog.database_edit.setText("-1")
    expected = replace(original, name="Renamed Redis", redis_separator="::")

    assert dialog.current_connection() == expected
    with qtbot.waitSignal(dialog.test_requested) as emitted:
        dialog.test_button.click()
    assert emitted.args[0] == expected
    dialog.accept()
    assert dialog.result() == QDialog.Accepted
    assert dialog.result_connection() == expected
    assert original.database == database


@pytest.mark.parametrize("kind", ["PostgreSQL", "MySQL", "MongoDB", "Oracle"])
def test_other_network_database_forms_keep_editable_database_or_service_name(qtbot, kind):
    dialog = ConnectionDialog(kind=kind, language="en")
    qtbot.addWidget(dialog)
    dialog.show()

    assert dialog.database_edit.isVisible()
    assert dialog.form.labelForField(dialog.database_edit).isVisible()
    dialog.database_edit.setText("application")
    assert dialog.current_connection().database == "application"
    assert not dialog.path_row.isVisible()


def test_sqlite_form_keeps_local_path_instead_of_database_index(qtbot):
    dialog = ConnectionDialog(kind="SQLite", language="en")
    qtbot.addWidget(dialog)
    dialog.show()

    assert dialog.path_row.isVisible()
    assert not dialog.database_edit.isVisible()
    dialog.path_edit.setText(":memory:")
    assert dialog.current_connection().path == ":memory:"
