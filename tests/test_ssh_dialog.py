"""SSH settings remain explicit and safe across native dialog edits."""
from dataclasses import replace

import pytest
from PySide6.QtWidgets import QDialog, QLineEdit

from baseskiff.models import Connection
from baseskiff.ui.dialogs import ConnectionDialog

PIN = "SHA256:" + "A" * 43


def ssh_connection(**changes):
    return replace(Connection("Remote", kind="PostgreSQL", host="db.internal", port=5439,
                              database="app", username="db-user", password="db-secret",
                              group="Production", ssl=True, read_only=True,
                              ssh_enabled=True, ssh_host="bastion.example", ssh_port=2222,
                              ssh_username="ssh-user", ssh_password="ssh-secret",
                              ssh_private_key="/keep/key", ssh_passphrase="key-secret",
                              ssh_host_key=PIN, ssh_known_hosts="/keep/known_hosts"), **changes)


def test_ssh_dialog_round_trip_preserves_database_and_hidden_auth_fields(qtbot):
    original = ssh_connection()
    dialog = ConnectionDialog(connection=original, language="en")
    qtbot.addWidget(dialog)
    dialog.name_edit.setText("Renamed")
    dialog.ssh_auth_combo.setCurrentIndex(dialog.ssh_auth_combo.findData("agent"))
    updated = dialog.current_connection()
    assert updated == replace(original, name="Renamed", ssh_auth_method="agent")
    assert original.name == "Remote"
    for field in (dialog.password_edit, dialog.ssh_password_edit, dialog.ssh_passphrase_edit):
        assert field.echoMode() == QLineEdit.Password


def test_ssh_auth_changes_show_only_relevant_credentials_without_clearing(qtbot):
    dialog = ConnectionDialog(connection=ssh_connection(), language="en")
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.tabs.setCurrentIndex(1)
    assert dialog.ssh_password_edit.isVisible()
    assert not dialog.ssh_private_key_edit.isVisible()
    dialog.ssh_auth_combo.setCurrentIndex(dialog.ssh_auth_combo.findData("key"))
    assert not dialog.ssh_password_edit.isVisible()
    assert dialog.ssh_private_key_edit.isVisible()
    assert dialog.ssh_passphrase_edit.isVisible()
    dialog.ssh_auth_combo.setCurrentIndex(dialog.ssh_auth_combo.findData("agent"))
    assert not dialog.ssh_password_edit.isVisible()
    assert not dialog.ssh_private_key_edit.isVisible()
    assert not dialog.ssh_passphrase_edit.isVisible()
    assert dialog.current_connection().ssh_password == "ssh-secret"
    assert dialog.current_connection().ssh_passphrase == "key-secret"


def test_disabling_ssh_preserves_config_without_validating_unused_fields(qtbot):
    original = ssh_connection(ssh_host="", ssh_private_key="/missing/key", ssh_host_key="bad")
    dialog = ConnectionDialog(connection=original, language="en")
    qtbot.addWidget(dialog)
    dialog.ssh_enabled_check.setChecked(False)
    dialog.accept()
    assert dialog.result() == QDialog.Accepted
    assert dialog.result_connection() == replace(original, ssh_enabled=False)


@pytest.mark.parametrize("kind", ["SQLite", "PostgreSQL"])
def test_selected_type_only_allows_network_database_tunnels(qtbot, kind):
    original = ssh_connection(kind=kind)
    dialog = ConnectionDialog(connection=original, language="en")
    qtbot.addWidget(dialog)
    assert dialog.ssh_enabled_check.isEnabled() is (kind != "SQLite")
    assert dialog.ssh_enabled_check.isChecked() is (kind != "SQLite")
    assert dialog.current_connection().ssh_enabled is (kind != "SQLite")
    assert dialog.current_connection().ssh_host == "bastion.example"


@pytest.mark.parametrize("language,field,text,fragment", [
    ("en", "ssh_host_edit", "", "SSH host"),
    ("en", "ssh_username_edit", "", "SSH username"),
    ("en", "ssh_host_key_edit", "SHA256:invalid", "SHA256"),
    ("en", "ssh_known_hosts_edit", "/missing/known_hosts", "known_hosts"),
    ("zh", "ssh_host_edit", "", "SSH 主机"),
    ("zh", "ssh_username_edit", "", "SSH 用户名"),
])
def test_enabled_ssh_validates_fields_before_emitting_test(qtbot, language, field, text, fragment):
    dialog = ConnectionDialog(connection=ssh_connection(ssh_known_hosts=""), language=language)
    qtbot.addWidget(dialog)
    getattr(dialog, field).setText(text)
    emitted = []
    dialog.test_requested.connect(emitted.append)
    dialog.test_button.click()
    assert emitted == []
    assert fragment in dialog.error_label.text()
    assert dialog.result() == QDialog.Rejected


def test_ssh_private_key_must_exist_when_key_auth_is_selected(qtbot):
    dialog = ConnectionDialog(connection=ssh_connection(ssh_known_hosts="", ssh_auth_method="key"),
                              language="en")
    qtbot.addWidget(dialog)
    dialog.accept()
    assert dialog.result() == QDialog.Rejected
    assert "private key" in dialog.error_label.text()


@pytest.mark.parametrize("port", ["0", "65536", ""])
def test_ssh_invalid_port_is_not_silently_replaced(qtbot, port):
    dialog = ConnectionDialog(connection=ssh_connection(ssh_known_hosts=""), language="en")
    qtbot.addWidget(dialog)
    dialog.ssh_port_spin.lineEdit().setText(port)
    dialog.accept()
    assert dialog.result() == QDialog.Rejected
    assert "SSH port" in dialog.error_label.text()


def test_test_signal_includes_full_tunnel_and_allows_unknown_host_discovery(qtbot):
    original = ssh_connection(ssh_host_key="", ssh_known_hosts="")
    dialog = ConnectionDialog(connection=original, language="en")
    qtbot.addWidget(dialog)
    with qtbot.waitSignal(dialog.test_requested) as emitted:
        dialog.test_button.click()
    assert emitted.args[0] == original
    assert "SHA256" in dialog.ssh_trust_hint.text()
    assert "SSH server" in dialog.ssh_hint.text()


def test_key_auth_and_known_hosts_can_be_saved_with_blank_edit_credentials(qtbot, tmp_path):
    key = tmp_path / "identity"
    known_hosts = tmp_path / "known_hosts"
    key.write_text("identity file content is validated when connecting")
    known_hosts.write_text("known hosts parsed when connecting")
    original = ssh_connection(ssh_auth_method="key", ssh_private_key=str(key),
                              ssh_known_hosts=str(known_hosts), ssh_host_key="")
    dialog = ConnectionDialog(connection=original, language="en")
    qtbot.addWidget(dialog)
    dialog.password_edit.clear()
    dialog.ssh_password_edit.clear()
    dialog.ssh_passphrase_edit.clear()
    dialog.accept()
    assert dialog.result() == QDialog.Accepted
    assert dialog.result_connection() == replace(original, password="", ssh_password="", ssh_passphrase="")


def test_database_validation_still_applies_with_ssh_enabled(qtbot):
    dialog = ConnectionDialog(connection=ssh_connection(ssh_known_hosts=""), language="en")
    qtbot.addWidget(dialog)
    dialog.host_edit.clear()
    dialog.accept()
    assert dialog.result() == QDialog.Rejected
    assert "database host" in dialog.error_label.text()
