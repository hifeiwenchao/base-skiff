"""Connection and typed record editors. Network work belongs to their caller."""
from __future__ import annotations

import json
import math
import os
import re
import sqlite3
from contextlib import closing
from dataclasses import replace
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from baseskiff.i18n import Translator
from baseskiff.models import DEFAULT_PORTS, ColumnInfo, Connection
from baseskiff.ui.dialog_layout import configure_form, fit_dialog


def _label(text: str, name: str = "fieldLabel", parent: QWidget | None = None) -> QLabel:
    widget = QLabel(text, parent)
    widget.setObjectName(name)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setWordWrap(True)
    return widget


def _button(text: str, primary: bool = False) -> QPushButton:
    widget = QPushButton(text)
    widget.setObjectName("primaryButton" if primary else "secondaryButton")
    widget.setMinimumHeight(34)
    return widget


class PortSpinBox(QSpinBox):
    """A port is edited deliberately; wheel gestures belong to its form."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.lineEdit().setTextMargins(11, 0, 11, 0)

    def wheelEvent(self, event) -> None:
        event.ignore()


class ConnectionDialog(QDialog):
    """Gather a connection. A test request emits a detached Connection value."""

    test_requested = Signal(object)

    def __init__(self, parent: QWidget | None = None, connection: Connection | None = None,
                 language: str = "zh", kind: str | None = None) -> None:
        super().__init__(parent)
        self.translator = Translator(language)
        self.tr = self.translator.tr
        self._original = replace(connection) if connection else Connection("", kind=kind or "SQLite")
        if connection is None and self._original.kind == "Redis":
            self._original.database = "0"
        self._result_connection: Connection | None = None
        self.setObjectName("connectionDialog")
        self.setWindowTitle(self.tr("edit_connection" if connection else "new_connection"))
        fit_dialog(self, 650, 650, 540)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 25, 28, 24)
        layout.setSpacing(15)
        layout.addWidget(_label(self.windowTitle(), "dialogTitle"))
        subtitle = _label(self.tr("connection_hint"), "muted")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        self.tabs = QTabWidget()
        self.tabs.setObjectName("connectionTabs")
        database_scroll = QScrollArea()
        database_scroll.setWidgetResizable(True)
        database_scroll.setFrameShape(QFrame.NoFrame)
        database_page = QWidget()
        database_page.setObjectName("page")
        self.form = QFormLayout(database_page)
        self.form.setContentsMargins(0, 12, 6, 8)
        self.form.setHorizontalSpacing(22)
        self.form.setVerticalSpacing(10)
        self.form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self.name_edit = QLineEdit(self._original.name)
        self.name_edit.setPlaceholderText(self.tr("connection_name_placeholder"))
        self.kind_label = _label(self._original.kind)
        self.kind_label.setMinimumHeight(33)
        self.kind_label.setTextFormat(Qt.TextFormat.PlainText)
        self.host_edit = QLineEdit(self._original.host)
        self.port_spin = PortSpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(self._original.port or DEFAULT_PORTS.get(self._original.kind, 5432))
        self.database_edit = QLineEdit(self._original.database)
        self.redis_separator_edit = QLineEdit(self._original.redis_separator)
        self.redis_separator_row = QWidget()
        separator_layout = QVBoxLayout(self.redis_separator_row)
        separator_layout.setContentsMargins(0, 0, 0, 0)
        separator_layout.setSpacing(4)
        separator_layout.addWidget(self.redis_separator_edit)
        self.redis_separator_hint = _label(self.tr("redis_separator_hint"), "muted")
        self.redis_separator_hint.setWordWrap(True)
        separator_layout.addWidget(self.redis_separator_hint)
        self.username_edit = QLineEdit(self._original.username)
        self.password_edit = QLineEdit(self._original.password)
        self.password_edit.setEchoMode(QLineEdit.Password)
        self.password_edit.setClearButtonEnabled(True)
        self.path_edit = QLineEdit(self._original.path)
        self.path_edit.setPlaceholderText("/path/to/database.sqlite3")
        self.browse_button = _button(self.tr("browse"))
        self.create_file_button = _button(self.tr("create_file"))
        self.path_row = QWidget()
        path_layout = QHBoxLayout(self.path_row)
        path_layout.setContentsMargins(0, 0, 0, 0)
        path_layout.setSpacing(6)
        path_layout.addWidget(self.path_edit, 1)
        path_layout.addWidget(self.browse_button)
        path_layout.addWidget(self.create_file_button)
        self.group_edit = QLineEdit(self._original.group)
        self.group_edit.setPlaceholderText("Local")
        self.timeout_spin = PortSpinBox()
        self.timeout_spin.setRange(1, 86400)
        self.timeout_spin.setValue(max(1, round(self._original.query_timeout)))
        self.timeout_spin.setSuffix(" 秒" if language == 'zh' else " s")
        self.ssl_check = QCheckBox(self.tr("ssl"))
        self.ssl_check.setChecked(self._original.ssl)
        self.read_only_check = QCheckBox(self.tr("read_only"))
        self.read_only_check.setChecked(self._original.read_only)
        self.read_only_check.setToolTip(self.tr("read_only_hint"))
        self.fields: dict[str, QWidget] = {
            "name": self.name_edit, "kind": self.kind_label, "host": self.host_edit,
            "port": self.port_spin, "database": self.database_edit,
            "redis_separator": self.redis_separator_edit,
            "username": self.username_edit, "password": self.password_edit,
            "path": self.path_edit, "group": self.group_edit,
            "ssl": self.ssl_check, "read_only": self.read_only_check,
        }
        for key, widget in self.fields.items():
            widget.setObjectName(f"connection_{key}")
            if isinstance(widget, QLineEdit):
                widget.setMinimumHeight(33)
        for key, widget in (("name", self.name_edit), ("database_type", self.kind_label),
                            ("path", self.path_row), ("host", self.host_edit),
                            ("port", self.port_spin), ("database", self.database_edit),
                            ("redis_separator", self.redis_separator_row),
                            ("username", self.username_edit), ("password", self.password_edit),
                            ("group", self.group_edit)):
            self.form.addRow(_label(self.tr(key)), widget)
        self.form.addRow("", self.ssl_check)
        self.form.addRow(_label("查询超时" if language == 'zh' else "Query timeout"), self.timeout_spin)
        self.form.addRow("", self.read_only_check)
        self.database_ssh_hint = _label(self.tr("ssh_database_hint"), "muted")
        self.database_ssh_hint.setWordWrap(True)
        self.form.addRow(self.database_ssh_hint)
        configure_form(self.form)
        database_scroll.setWidget(database_page)
        self.tabs.addTab(database_scroll, self.tr("database"))
        self._build_ssh_tab()
        layout.addWidget(self.tabs, 1)
        security = _label(self.tr("connection_security"), "muted")
        security.setWordWrap(True)
        layout.addWidget(security)
        self.error_label = _label("", "error")
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        layout.addWidget(self.error_label)
        self.test_status = _label("", "muted")
        self.test_status.setWordWrap(True)
        layout.addWidget(self.test_status)

        footer = QHBoxLayout()
        self.test_button = _button(self.tr("test_connection"))
        self.cancel_button = _button(self.tr("cancel"))
        self.save_button = _button(self.tr("save_connection"), True)
        self.save_button.setDefault(True)
        footer.addWidget(self.test_button)
        footer.addStretch()
        footer.addWidget(self.cancel_button)
        footer.addWidget(self.save_button)
        layout.addLayout(footer)

        self.cancel_button.clicked.connect(self.reject)
        self.save_button.clicked.connect(self.accept)
        self.test_button.clicked.connect(self._request_test)
        self.browse_button.clicked.connect(self._browse_file)
        self.create_file_button.clicked.connect(self._create_file)
        self._update_fields(self._original.kind)

    def _build_ssh_tab(self) -> None:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        page = QWidget()
        page.setObjectName("page")
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, 12, 6, 8)
        page_layout.setSpacing(10)
        self.ssh_enabled_check = QCheckBox(self.tr("ssh_enabled"))
        self.ssh_enabled_check.setChecked(self._original.ssh_enabled)
        page_layout.addWidget(self.ssh_enabled_check)
        self.ssh_sqlite_hint = _label(self.tr("ssh_sqlite_unavailable"), "muted")
        self.ssh_sqlite_hint.setWordWrap(True)
        page_layout.addWidget(self.ssh_sqlite_hint)
        self.ssh_hint = _label(self.tr("ssh_hint"), "muted")
        self.ssh_hint.setWordWrap(True)
        page_layout.addWidget(self.ssh_hint)

        self.ssh_fields_container = QWidget()
        self.ssh_form = QFormLayout(self.ssh_fields_container)
        self.ssh_form.setContentsMargins(0, 0, 0, 0)
        self.ssh_form.setHorizontalSpacing(18)
        self.ssh_form.setVerticalSpacing(9)
        self.ssh_form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self.ssh_host_edit = QLineEdit(self._original.ssh_host)
        self.ssh_host_edit.setPlaceholderText("bastion.example.com")
        self.ssh_port_spin = PortSpinBox()
        self.ssh_port_spin.setRange(1, 65535)
        self.ssh_port_spin.setValue(self._original.ssh_port)
        self.ssh_username_edit = QLineEdit(self._original.ssh_username)
        self.ssh_auth_combo = QComboBox()
        for key, value in (("ssh_auth_password", "password"), ("ssh_auth_key", "key"),
                           ("ssh_auth_agent", "agent")):
            self.ssh_auth_combo.addItem(self.tr(key), value)
        self.ssh_auth_combo.setCurrentIndex(
            max(0, self.ssh_auth_combo.findData(self._original.ssh_auth_method)))
        self.ssh_password_edit = QLineEdit(self._original.ssh_password)
        self.ssh_passphrase_edit = QLineEdit(self._original.ssh_passphrase)
        for field in (self.ssh_password_edit, self.ssh_passphrase_edit):
            field.setEchoMode(QLineEdit.Password)
            field.setClearButtonEnabled(True)
            if self._original.id and self._original.name:
                field.setPlaceholderText(self.tr("ssh_keep_secret"))
        self.ssh_private_key_edit = QLineEdit(self._original.ssh_private_key)
        self.ssh_key_browse_button = _button(self.tr("browse"))
        self.ssh_private_key_row = QWidget()
        key_layout = QHBoxLayout(self.ssh_private_key_row)
        key_layout.setContentsMargins(0, 0, 0, 0)
        key_layout.setSpacing(6)
        key_layout.addWidget(self.ssh_private_key_edit, 1)
        key_layout.addWidget(self.ssh_key_browse_button)
        self.ssh_host_key_edit = QLineEdit(self._original.ssh_host_key)
        self.ssh_host_key_edit.setPlaceholderText("SHA256:…")
        self.ssh_known_hosts_edit = QLineEdit(self._original.ssh_known_hosts)
        self.ssh_known_hosts_edit.setPlaceholderText(self.tr("ssh_known_hosts_default"))
        self.ssh_known_hosts_browse_button = _button(self.tr("browse"))
        self.ssh_known_hosts_row = QWidget()
        known_hosts_layout = QHBoxLayout(self.ssh_known_hosts_row)
        known_hosts_layout.setContentsMargins(0, 0, 0, 0)
        known_hosts_layout.setSpacing(6)
        known_hosts_layout.addWidget(self.ssh_known_hosts_edit, 1)
        known_hosts_layout.addWidget(self.ssh_known_hosts_browse_button)
        ssh_fields = {
            "ssh_enabled": self.ssh_enabled_check, "ssh_host": self.ssh_host_edit,
            "ssh_port": self.ssh_port_spin, "ssh_username": self.ssh_username_edit,
            "ssh_auth_method": self.ssh_auth_combo, "ssh_password": self.ssh_password_edit,
            "ssh_private_key": self.ssh_private_key_edit, "ssh_passphrase": self.ssh_passphrase_edit,
            "ssh_host_key": self.ssh_host_key_edit, "ssh_known_hosts": self.ssh_known_hosts_edit,
        }
        for key, field in ssh_fields.items():
            self.fields[key] = field
            field.setObjectName(f"connection_{key}")
            if isinstance(field, QLineEdit):
                field.setMinimumHeight(33)
        for key, field in (("ssh_host", self.ssh_host_edit), ("ssh_port", self.ssh_port_spin),
                           ("ssh_username", self.ssh_username_edit),
                           ("ssh_auth_method", self.ssh_auth_combo),
                           ("ssh_password", self.ssh_password_edit),
                           ("ssh_private_key", self.ssh_private_key_row),
                           ("ssh_passphrase", self.ssh_passphrase_edit),
                           ("ssh_host_key", self.ssh_host_key_edit),
                           ("ssh_known_hosts", self.ssh_known_hosts_row)):
            self.ssh_form.addRow(_label(self.tr(key)), field)
        page_layout.addWidget(self.ssh_fields_container)
        self.ssh_trust_hint = _label(self.tr("ssh_trust_hint"), "muted")
        self.ssh_trust_hint.setWordWrap(True)
        page_layout.addWidget(self.ssh_trust_hint)
        page_layout.addStretch(1)
        configure_form(self.ssh_form)
        scroll.setWidget(page)
        self.tabs.addTab(scroll, self.tr("ssh_tunnel"))
        self.ssh_enabled_check.toggled.connect(self._update_ssh_fields)
        self.ssh_auth_combo.currentIndexChanged.connect(self._update_ssh_fields)
        self.ssh_key_browse_button.clicked.connect(
            lambda: self._browse_ssh_file(self.ssh_private_key_edit, "ssh_choose_private_key"))
        self.ssh_known_hosts_browse_button.clicked.connect(
            lambda: self._browse_ssh_file(self.ssh_known_hosts_edit, "ssh_choose_known_hosts"))

    def _update_ssh_fields(self) -> None:
        sqlite = self._original.kind == "SQLite"
        if sqlite:
            self.ssh_enabled_check.setChecked(False)
        self.ssh_enabled_check.setEnabled(not sqlite)
        self.ssh_sqlite_hint.setVisible(sqlite)
        enabled = self.ssh_enabled_check.isChecked() and not sqlite
        self.ssh_fields_container.setEnabled(enabled)
        self.database_ssh_hint.setVisible(enabled)
        method = self.ssh_auth_combo.currentData()
        self.ssh_form.setRowVisible(self.ssh_password_edit, method == "password")
        self.ssh_form.setRowVisible(self.ssh_private_key_row, method == "key")
        self.ssh_form.setRowVisible(self.ssh_passphrase_edit, method == "key")

    def _browse_ssh_file(self, field: QLineEdit, title_key: str) -> None:
        path, _ = QFileDialog.getOpenFileName(self, self.tr(title_key), field.text())
        if path:
            field.setText(path)

    def _update_fields(self, kind: str) -> None:
        sqlite = kind == "SQLite"
        self.form.setRowVisible(self.path_row, sqlite)
        self.form.setRowVisible(self.redis_separator_row, kind == "Redis")
        for field in (self.host_edit, self.port_spin,
                      self.username_edit, self.password_edit, self.ssl_check):
            self.form.setRowVisible(field, not sqlite)
        self.form.setRowVisible(self.database_edit, kind not in {"SQLite", "Redis"})
        database_label = self.form.labelForField(self.database_edit)
        database_label.setText(self.tr("database_index" if kind == "Redis" else
                                       "service_name" if kind == "Oracle" else "database"))
        self.database_edit.setPlaceholderText("0" if kind == "Redis" else
                                             "FREEPDB1" if kind == "Oracle" else "")
        self._update_ssh_fields()

    def _browse_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, self.tr("open_database_file"),
                                            self.path_edit.text(), self.tr("sqlite_files"))
        if path:
            self.path_edit.setText(path)

    def _create_file(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, self.tr("create_database_file"),
                                            self.path_edit.text() or "database.sqlite3",
                                            self.tr("sqlite_files"))
        if not path:
            return
        destination = Path(path).expanduser()
        created = False
        try:
            try:
                descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                # The save chooser can return an existing file. Never overwrite
                # or initialize it, even after its overwrite confirmation.
                if not destination.is_file():
                    raise IsADirectoryError(str(destination)) from None
            else:
                created = True
                os.close(descriptor)
                with closing(sqlite3.connect(destination)) as database:
                    database.execute("PRAGMA user_version = 0")
                    database.commit()
        except (OSError, sqlite3.Error) as error:
            if created:
                try:
                    destination.unlink(missing_ok=True)
                except OSError:
                    pass
            self._show_error(ValueError(self.tr("create_database_failed", error=str(error))))
            return
        self.error_label.hide()
        self.path_edit.setText(str(destination))

    def current_connection(self) -> Connection:
        kind = self._original.kind
        return replace(self._original, name=self.name_edit.text().strip(), kind=kind,
                       host=self.host_edit.text().strip(),
                       port=0 if kind == "SQLite" else self.port_spin.value(),
                       database=(self._original.database if kind == "Redis"
                                 else self.database_edit.text().strip()),
                       redis_separator=self.redis_separator_edit.text(),
                       username=self.username_edit.text().strip(), password=self.password_edit.text(),
                       path=self.path_edit.text().strip(), group=self.group_edit.text().strip() or "Local",
                       ssl=self.ssl_check.isChecked() if kind != "SQLite" else False,
                       read_only=self.read_only_check.isChecked(),
                       query_timeout=self.timeout_spin.value(),
                       ssh_enabled=self.ssh_enabled_check.isChecked() and kind != "SQLite",
                       ssh_host=self.ssh_host_edit.text().strip(), ssh_port=self.ssh_port_spin.value(),
                       ssh_username=self.ssh_username_edit.text().strip(),
                       ssh_auth_method=self.ssh_auth_combo.currentData(),
                       ssh_password=self.ssh_password_edit.text(),
                       ssh_private_key=self.ssh_private_key_edit.text().strip(),
                       ssh_passphrase=self.ssh_passphrase_edit.text(),
                       ssh_host_key=self.ssh_host_key_edit.text().strip(),
                       ssh_known_hosts=self.ssh_known_hosts_edit.text().strip())

    def _validated_connection(self) -> Connection:
        connection = self.current_connection()
        if not connection.name:
            raise ValueError(self.tr("connection_name_required"))
        if connection.kind == "SQLite":
            if not connection.path:
                raise ValueError(self.tr("connection_path_required"))
            if connection.path != ":memory:":
                path = Path(connection.path).expanduser()
                if path.is_dir() or not path.parent.is_dir():
                    raise ValueError(self.tr("connection_path_invalid"))
                connection.path = str(path)
        elif not connection.host:
            raise ValueError(self.tr("connection_host_required"))
        if connection.kind == "Oracle" and not connection.database:
            raise ValueError(self.tr("connection_service_required"))
        if connection.kind == "Redis":
            try:
                if int(connection.database or "0") < 0:
                    raise ValueError
            except ValueError:
                raise ValueError(self.tr("connection_redis_database_invalid")) from None
        if connection.ssh_enabled:
            if not connection.ssh_host:
                raise ValueError(self.tr("ssh_host_required"))
            if not self.ssh_port_spin.hasAcceptableInput():
                raise ValueError(self.tr("ssh_port_invalid"))
            if not connection.ssh_username:
                raise ValueError(self.tr("ssh_username_required"))
            if connection.ssh_auth_method == "key":
                if not connection.ssh_private_key or not Path(connection.ssh_private_key).expanduser().is_file():
                    raise ValueError(self.tr("ssh_private_key_invalid"))
            if connection.ssh_host_key and not re.fullmatch(r"SHA256:[A-Za-z0-9+/]{43}=?", connection.ssh_host_key):
                raise ValueError(self.tr("ssh_host_key_invalid"))
            if connection.ssh_known_hosts and not Path(connection.ssh_known_hosts).expanduser().is_file():
                raise ValueError(self.tr("ssh_known_hosts_invalid"))
        return connection

    def _show_error(self, error: Exception) -> None:
        self.error_label.setText(str(error))
        self.error_label.show()

    def _request_test(self) -> None:
        try:
            connection = self._validated_connection()
        except ValueError as error:
            self._show_error(error)
            return
        self.error_label.hide()
        self.test_status.setText(self.tr("testing"))
        self.test_requested.emit(connection)

    def set_test_result(self, success: bool, message: str = "") -> None:
        self.test_button.setEnabled(True)
        self.test_status.setText(message or self.tr("test_success" if success else "connection_failed"))

    def result_connection(self) -> Connection:
        return replace(self._result_connection) if self._result_connection else self.current_connection()

    def accept(self) -> None:
        try:
            self._result_connection = self._validated_connection()
        except ValueError as error:
            self._show_error(error)
            return
        self.error_label.hide()
        super().accept()


class RowDialog(QDialog):
    """Edit a row without conflating an empty string and a SQL NULL."""

    def __init__(self, columns: list[ColumnInfo], row: dict[str, Any] | None = None,
                 parent: QWidget | None = None, language: str = "zh", bson_mode: bool = False) -> None:
        super().__init__(parent)
        self.translator = Translator(language)
        self.tr = self.translator.tr
        self.columns = list(columns)
        self._bson_mode = bson_mode
        self.row = dict(row) if row is not None else None
        self._redis_record = ({column.name for column in columns} == {"key", "type", "value"}
                              and any(column.name == "key" and column.primary_key for column in columns))
        self.fields: dict[str, QLineEdit | QPlainTextEdit] = {}
        self.null_checks: dict[str, QCheckBox] = {}
        self.setObjectName("rowDialog")
        self.setWindowTitle(self.tr("edit_row" if row is not None else "add_row"))
        fit_dialog(self, 620, min(720, max(340, 200 + len(columns) * 72)), 480)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 24, 26, 22)
        layout.setSpacing(14)
        layout.addWidget(_label(self.windowTitle(), "dialogTitle"))
        hint = _label(self.tr("row_editor_hint"), "muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        container = QWidget()
        container.setObjectName("page")
        form = QFormLayout(container)
        form.setContentsMargins(0, 2, 6, 6)
        form.setVerticalSpacing(15)
        form.setHorizontalSpacing(22)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        for column in columns:
            value = self.row.get(column.name) if self.row is not None else None
            json_field = self._is_json(column, value)
            field = QPlainTextEdit() if json_field else QLineEdit()
            field.setObjectName(f"row_{column.name}")
            if json_field:
                field.setMinimumHeight(120)
                field.setMaximumHeight(190)
                field.setPlainText(self._format_value(value, json_field=True))
            else:
                field.setMinimumHeight(34)
                field.setText(self._format_value(value))
            field.setAccessibleName(column.name)
            if self.row is None:
                if column.default is not None:
                    field.setPlaceholderText(self.tr("default_value_hint"))
                elif self._generated_primary_key(column):
                    field.setPlaceholderText(self.tr("generated_value_hint"))
            locked = self.row is not None and column.primary_key
            field.setEnabled(not locked)
            if locked:
                field.setToolTip(self.tr("primary_key_locked"))
            null_check = QCheckBox(self.tr("null"))
            null_check.setObjectName(f"null_{column.name}")
            null_check.setAccessibleName(f"{column.name} NULL")
            initial_null = column.nullable and value is None and (
                self.row is not None or (column.default is None and not self._generated_primary_key(column)))
            null_check.setChecked(initial_null)
            null_check.setEnabled(column.nullable and not locked)
            if initial_null:
                field.setEnabled(False)
            null_check.toggled.connect(lambda checked, field=field, locked=locked: field.setEnabled(not checked and not locked))
            row_widget = QWidget()
            row_layout = QHBoxLayout(row_widget)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(10)
            row_layout.addWidget(field, 1)
            row_layout.addWidget(null_check, 0, Qt.AlignTop)
            label = _label(column.name + ("  ·  PK" if column.primary_key else ""))
            label.setToolTip(column.data_type)
            form.addRow(label, row_widget)
            self.fields[column.name] = field
            self.null_checks[column.name] = null_check
        configure_form(form)
        scroll.setWidget(container)
        layout.addWidget(scroll, 1)
        self.error_label = _label("", "error")
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        layout.addWidget(self.error_label)
        footer = QHBoxLayout()
        footer.addStretch()
        self.cancel_button = _button(self.tr("cancel"))
        self.save_button = _button(self.tr("save"), True)
        self.save_button.setDefault(True)
        footer.addWidget(self.cancel_button)
        footer.addWidget(self.save_button)
        layout.addLayout(footer)
        self.cancel_button.clicked.connect(self.reject)
        self.save_button.clicked.connect(self.accept)

    @staticmethod
    def _is_json(column: ColumnInfo, value: Any) -> bool:
        return "JSON" in column.data_type.upper() or column.data_type.upper() in {"OBJECT", "DOCUMENT", "ARRAY"} or isinstance(value, (dict, list))

    @staticmethod
    def _generated_primary_key(column: ColumnInfo) -> bool:
        data_type = column.data_type.upper()
        return column.primary_key and ("INT" in data_type or "SERIAL" in data_type
                                       or "IDENTITY" in data_type or data_type == "OBJECTID")

    def _format_value(self, value: Any, json_field: bool = False) -> str:
        if value is None:
            return ""
        if json_field:
            if self._bson_mode and not isinstance(value, str):
                from bson import json_util
                return json_util.dumps(value, ensure_ascii=False, indent=2,
                                       json_options=json_util.CANONICAL_JSON_OPTIONS)
            return json.dumps(value, ensure_ascii=False, indent=2, default=str) if not isinstance(value, str) else value
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, bytes):
            return value.hex()
        if isinstance(value, (datetime, date, time)):
            return value.isoformat()
        return str(value)

    def _parse_value(self, column: ColumnInfo, text: str, original: Any) -> Any:
        kind = column.data_type.upper()
        same_redis_type = True
        if self._redis_record and column.name == "value":
            redis_kind = self.fields["type"].text().strip().lower() or "string"
            if redis_kind == "string":
                return text
            same_redis_type = self.row is not None and redis_kind == self.row.get("type")
        if original is not None and same_redis_type and text == self._format_value(
                original, json_field=self._is_json(column, original)):
            # Drivers may return richer types (BSON Binary/Decimal128/Int64,
            # timezone-aware dates). Saving an unrelated field must retain them.
            return original
        if kind == "OBJECTID":
            from bson import ObjectId
            from bson.errors import InvalidId
            try:
                return ObjectId(text.strip())
            except (InvalidId, TypeError) as error:
                raise ValueError(str(error)) from None
        if kind == "DECIMAL128":
            from bson.decimal128 import Decimal128
            try:
                return Decimal128(text.strip())
            except (InvalidOperation, ValueError) as error:
                raise ValueError(str(error)) from None
        if self._is_json(column, original):
            try:
                if self._bson_mode:
                    from bson import json_util
                    return json_util.loads(text)
                return json.loads(text)
            except (ValueError, TypeError, KeyError, OverflowError, InvalidOperation):
                raise ValueError(self.tr("invalid_json")) from None
        if isinstance(original, bool) or "BOOL" in kind:
            normalized = text.strip().lower()
            if normalized in {"true", "1", "yes", "on"}:
                return True
            if normalized in {"false", "0", "no", "off"}:
                return False
            raise ValueError(self.tr("invalid_boolean"))
        if isinstance(original, int) or "INT" in kind or "SERIAL" in kind:
            try:
                return int(text.strip())
            except ValueError:
                raise ValueError(self.tr("invalid_integer")) from None
        if isinstance(original, Decimal) or any(token in kind for token in ("NUMERIC", "DECIMAL", "NUMBER")):
            try:
                value = Decimal(text.strip())
                if not value.is_finite():
                    raise InvalidOperation
                return value
            except InvalidOperation:
                raise ValueError(self.tr("invalid_number")) from None
        if isinstance(original, float) or any(token in kind for token in ("REAL", "FLOA", "DOUB")):
            try:
                value = float(text.strip())
                if not math.isfinite(value):
                    raise ValueError
                return value
            except ValueError:
                raise ValueError(self.tr("invalid_number")) from None
        if isinstance(original, bytes):
            return bytes.fromhex(text.strip())
        if isinstance(original, datetime) or kind.startswith(("TIMESTAMP", "DATETIME")):
            return datetime.fromisoformat(text.strip())
        if isinstance(original, date) or kind == "DATE":
            return date.fromisoformat(text.strip())
        if isinstance(original, time):
            return time.fromisoformat(text.strip())
        return text

    def values(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for column in self.columns:
            original = self.row.get(column.name) if self.row is not None else None
            if self.row is not None and column.primary_key:
                result[column.name] = original
                continue
            if self.null_checks[column.name].isChecked():
                if not column.nullable:
                    raise ValueError(self.tr("invalid_field", field=column.name, error=self.tr("null_not_allowed")))
                result[column.name] = None
                continue
            field = self.fields[column.name]
            text = field.toPlainText() if isinstance(field, QPlainTextEdit) else field.text()
            if self.row is None and not text and (column.default is not None or self._generated_primary_key(column)):
                continue
            try:
                result[column.name] = self._parse_value(column, text, original)
            except (ValueError, TypeError, OverflowError) as error:
                raise ValueError(self.tr("invalid_field", field=column.name, error=str(error))) from None
        return result

    def accept(self) -> None:
        try:
            self.values()
        except ValueError as error:
            self.error_label.setText(str(error))
            self.error_label.show()
            return
        self.error_label.hide()
        super().accept()


class RedisKeyDialog(QDialog):
    """Create a Redis key with a type-aware value editor and optional TTL."""

    _TYPES = (('字符串', 'String', 'string'), ('哈希', 'Hash', 'hash'),
              ('列表', 'List', 'list'), ('集合', 'Set', 'set'),
              ('有序集合', 'Sorted set', 'zset'))

    def __init__(self, parent: QWidget | None = None, language: str = 'zh') -> None:
        super().__init__(parent)
        self.language = language if language in {'zh', 'en'} else 'en'
        self.setObjectName('redisKeyDialog')
        self.setWindowTitle(self._text('新建 key', 'New key'))
        fit_dialog(self, 620, 560, 480)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 24, 26, 22)
        layout.setSpacing(14)
        layout.addWidget(_label(self.windowTitle(), 'dialogTitle'))
        hint = _label(self._text('选择类型后按对应结构填写值，可选设置 TTL。',
                                 'Choose a type, fill its structured value, and optionally set a TTL.'), 'muted')
        hint.setWordWrap(True)
        layout.addWidget(hint)

        form = QFormLayout()
        form.setVerticalSpacing(13)
        form.setHorizontalSpacing(22)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self.key_edit = QLineEdit()
        self.key_edit.setObjectName('redisKey')
        self.key_edit.setMinimumHeight(34)
        self.key_edit.setPlaceholderText(self._text('输入 key', 'Key'))
        form.addRow(_label('key'), self.key_edit)

        self.type_combo = QComboBox()
        self.type_combo.setObjectName('redisType')
        self.type_combo.setMinimumHeight(34)
        for chinese, english, value in self._TYPES:
            self.type_combo.addItem(self._text(chinese, english), value)
        form.addRow(_label(self._text('类型', 'Type')), self.type_combo)

        self.ttl_edit = QLineEdit()
        self.ttl_edit.setObjectName('redisTTL')
        self.ttl_edit.setMinimumHeight(34)
        self.ttl_edit.setPlaceholderText(self._text('留空表示不过期（秒）', 'Leave empty for no expiry (seconds)'))
        form.addRow(_label('TTL'), self.ttl_edit)

        self.value_stack = QStackedWidget()
        self.value_stack.setObjectName('redisValueStack')
        self._pages: dict[str, QWidget] = {}
        self._row_containers: dict[str, QVBoxLayout] = {}
        self._rows: dict[str, list[tuple[QLineEdit, ...]]] = {}
        self._add_buttons: dict[str, QPushButton] = {}
        self._rows['string'] = []
        self.hash_rows = self._rows.setdefault('hash', [])
        self.list_rows = self._rows.setdefault('list', [])
        self.set_rows = self._rows.setdefault('set', [])
        self.zset_rows = self._rows.setdefault('zset', [])
        self.add_value_row_button: QPushButton | None = None
        self._string_value: QPlainTextEdit | None = None
        for kind in ('string', 'hash', 'list', 'set', 'zset'):
            page = QWidget()
            page_layout = QVBoxLayout(page)
            page_layout.setContentsMargins(0, 0, 0, 0)
            page_layout.setSpacing(8)
            rows = QVBoxLayout()
            rows.setContentsMargins(0, 0, 0, 0)
            rows.setSpacing(7)
            page_layout.addLayout(rows)
            add_button = _button(self._text('添加一行', 'Add row'))
            add_button.setObjectName('addRedisValueRow')
            add_button.clicked.connect(lambda _checked=False, selected=kind: self._add_row(selected))
            page_layout.addWidget(add_button, 0, Qt.AlignmentFlag.AlignLeft)
            self._add_buttons[kind] = add_button
            self._pages[kind] = page
            self._row_containers[kind] = rows
            self.value_stack.addWidget(page)
        form.addRow(_label(self._text('值', 'Value')), self.value_stack)
        configure_form(form)
        form_host = QWidget()
        form_host.setLayout(form)
        form_scroll = QScrollArea()
        form_scroll.setWidgetResizable(True)
        form_scroll.setWidget(form_host)
        layout.addWidget(form_scroll, 1)

        self.error_label = _label('', 'error')
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        layout.addWidget(self.error_label)
        footer = QHBoxLayout()
        footer.addStretch()
        self.cancel_button = _button(self._text('取消', 'Cancel'))
        self.save_button = _button(self._text('保存', 'Save'), True)
        self.save_button.setDefault(True)
        footer.addWidget(self.cancel_button)
        footer.addWidget(self.save_button)
        layout.addLayout(footer)
        self.cancel_button.clicked.connect(self.reject)
        self.save_button.clicked.connect(self.accept)
        self.type_combo.currentIndexChanged.connect(self._type_changed)
        self._type_changed()

    def _text(self, chinese: str, english: str) -> str:
        return chinese if self.language == 'zh' else english

    def _type_changed(self) -> None:
        kind = self.type_combo.currentData()
        self.value_stack.setCurrentWidget(self._pages[kind])
        self.add_value_row_button = self._add_buttons[kind]
        self._clear_rows(kind)
        if kind == 'string':
            editor = QPlainTextEdit()
            editor.setObjectName('redisStringValue')
            editor.setMinimumHeight(110)
            self._string_value = editor
            self._row_containers[kind].addWidget(editor)
        else:
            self._string_value = None
            self._add_row(kind)

    def _clear_rows(self, kind: str) -> None:
        container = self._row_containers[kind]
        while container.count():
            item = container.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._rows[kind].clear()

    def _add_row(self, kind: str) -> None:
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(8)
        fields: tuple[QLineEdit, ...]
        if kind == 'hash':
            field, value = QLineEdit(), QLineEdit()
            field.setPlaceholderText(self._text('字段', 'Field'))
            value.setPlaceholderText(self._text('值', 'Value'))
            fields = (field, value)
        elif kind == 'zset':
            member, score = QLineEdit(), QLineEdit()
            member.setPlaceholderText(self._text('成员', 'Member'))
            score.setPlaceholderText(self._text('分数', 'Score'))
            fields = (member, score)
        else:
            value = QLineEdit()
            value.setPlaceholderText(self._text('值', 'Value'))
            fields = (value,)
        for field in fields:
            field.setMinimumHeight(32)
            row_layout.addWidget(field, 1)
        self._row_containers[kind].addWidget(row)
        self._rows[kind].append(fields)

    def _parse_ttl(self) -> int | None:
        text = self.ttl_edit.text().strip()
        if not text:
            return None
        try:
            ttl = int(text)
        except ValueError:
            raise ValueError(self._text('TTL 必须是 1 到 2,147,483,647 之间的整数秒。',
                                        'TTL must be an integer from 1 to 2,147,483,647 seconds.')) from None
        if ttl < 1 or ttl > 2_147_483_647 or str(ttl) != text:
            raise ValueError(self._text('TTL 必须是 1 到 2,147,483,647 之间的整数秒。',
                                        'TTL must be an integer from 1 to 2,147,483,647 seconds.'))
        return ttl

    def _collection_value(self, kind: str) -> Any:
        rows = self._rows[kind]
        if kind == 'hash':
            result = {}
            for field_edit, value_edit in rows:
                field, value = field_edit.text(), value_edit.text()
                if not field and not value:
                    continue
                if not field:
                    raise ValueError(self._text('Hash 字段不能为空。', 'Hash fields cannot be empty.'))
                if field in result:
                    raise ValueError(self._text(f'Hash 字段“{field}”重复。', f'Duplicate Hash field: {field}.'))
                result[field] = value
            if not result:
                raise ValueError(self._text('请至少填写一组 Hash 字段和值。', 'Enter at least one Hash field and value.'))
            return result
        if kind == 'zset':
            result = {}
            for member_edit, score_edit in rows:
                member, text = member_edit.text(), score_edit.text().strip()
                if not member and not text:
                    continue
                if not member:
                    raise ValueError(self._text('ZSet 成员不能为空。', 'ZSet members cannot be empty.'))
                try:
                    score = float(text)
                    if not math.isfinite(score):
                        raise ValueError
                except ValueError:
                    raise ValueError(self._text('ZSet 分数必须是有限数字。', 'ZSet scores must be finite numbers.')) from None
                if member in result:
                    raise ValueError(self._text(f'ZSet 成员“{member}”重复。', f'Duplicate ZSet member: {member}.'))
                result[member] = score
            if not result:
                raise ValueError(self._text('请至少填写一组 ZSet 成员和分数。', 'Enter at least one ZSet member and score.'))
            return result
        values = [fields[0].text() for fields in rows if fields[0].text()]
        if not values:
            raise ValueError(self._text('请至少填写一个值。', 'Enter at least one value.'))
        if kind == 'set' and len(values) != len(set(values)):
            raise ValueError(self._text('Set 成员不能重复。', 'Set members must be unique.'))
        return values

    def values(self) -> dict[str, Any]:
        key = self.key_edit.text()
        if not key:
            raise ValueError(self._text('请输入 Redis key。', 'Enter a Redis key.'))
        kind = self.type_combo.currentData()
        value = self._string_value.toPlainText() if kind == 'string' else self._collection_value(kind)
        return {'key': key, 'type': kind, 'value': value, 'ttl': self._parse_ttl()}

    def accept(self) -> None:
        try:
            self.values()
        except ValueError as error:
            self.error_label.setText(str(error))
            self.error_label.show()
            return
        self.error_label.hide()
        super().accept()
