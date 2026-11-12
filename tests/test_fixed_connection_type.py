"""A connection editor always keeps the database type selected before opening."""
from dataclasses import replace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel

from baseskiff.models import DATABASE_TYPES, DEFAULT_PORTS, Connection
from baseskiff.ui.dialogs import ConnectionDialog


@pytest.mark.parametrize('kind', DATABASE_TYPES)
def test_new_editor_uses_selected_type_and_default_port(qtbot, kind):
    dialog = ConnectionDialog(kind=kind, language='en')
    qtbot.addWidget(dialog)
    dialog.show()
    assert isinstance(dialog.kind_label, QLabel)
    assert dialog.kind_label.text() == kind
    assert dialog.fields['kind'] is dialog.kind_label
    assert dialog.kind_label.focusPolicy() == Qt.FocusPolicy.NoFocus
    assert not hasattr(dialog, 'kind_combo')
    connection = dialog.current_connection()
    assert connection.kind == kind
    assert connection.port == DEFAULT_PORTS.get(kind, 0)
    assert dialog.path_edit.isVisible() is (kind == 'SQLite')
    assert dialog.host_edit.isVisible() is (kind != 'SQLite')
    assert dialog.redis_separator_row.isVisible() is (kind == 'Redis')
    assert dialog.ssh_enabled_check.isEnabled() is (kind != 'SQLite')


@pytest.mark.parametrize('kind', DATABASE_TYPES)
def test_existing_editor_preserves_type_identity_and_custom_port(qtbot, kind):
    original = Connection('Existing', kind=kind, port=0 if kind == 'SQLite' else 15432,
                          password='db-secret', group='Production', redis_separator='::')
    conflicting_kind = 'Redis' if kind != 'Redis' else 'Oracle'
    dialog = ConnectionDialog(connection=original, kind=conflicting_kind, language='en')
    qtbot.addWidget(dialog)
    dialog.name_edit.setText('Renamed')
    # The label is presentation only; configuration never reads a display value.
    dialog.kind_label.setText(conflicting_kind)
    assert dialog.current_connection() == replace(original, name='Renamed')
    assert original.name == 'Existing'


@pytest.mark.parametrize('language,label', [('zh', '服务名'), ('en', 'Service name')])
def test_selected_oracle_editor_uses_service_name_field(qtbot, language, label):
    dialog = ConnectionDialog(kind='Oracle', language=language)
    qtbot.addWidget(dialog)
    assert dialog.form.labelForField(dialog.database_edit).text() == label
    assert dialog.database_edit.placeholderText() == 'FREEPDB1'


def test_connection_editor_without_selection_keeps_legacy_sqlite_default(qtbot):
    dialog = ConnectionDialog(language='en')
    qtbot.addWidget(dialog)
    assert dialog.current_connection().kind == 'SQLite'
