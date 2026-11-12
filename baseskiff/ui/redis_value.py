"""Native Redis value inspection and entry editing, without database access."""
from __future__ import annotations

import json
import math
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from baseskiff.redis_codec import json_safe, preview_value, safe_text
from baseskiff.ui.icons import icon
from baseskiff.ui.redis_preview import RedisPreviewDialog, is_editable_text

_TEXT = {
    'zh': {
        'refresh': '刷新', 'copy': '复制', 'edit': '编辑', 'delete': '删除', 'view': '查看',
        'add_hash': '添加字段', 'add_list': '追加元素', 'add_set': '添加成员',
        'add_zset': '添加成员', 'add': '添加', 'save': '保存', 'cancel': '取消',
        'save_value': '保存值', 'json': 'JSON 预览', 'field': '字段', 'value': '值',
        'index': '索引', 'member': '成员', 'score': '分数', 'actions': '操作',
        'stream_id': '消息 ID', 'fields': '字段 (JSON)', 'ttl': 'TTL',
        'no_expiry': '永久', 'seconds': '秒', 'expired': '已过期', 'bytes': '字节',
        'entries': '项', 'select': '选择一个键以查看值',
        'missing': '键不存在或已过期，请刷新键列表。',
        'preview': '仅预览前 1,000 项；操作只影响选中的项。',
        'list_preview': '仅预览前 1,000 项；可追加或编辑，删除需先载入完整列表。',
        'readonly': '只读连接', 'stream_readonly': 'Stream 预览 · 只读',
        'edit_title': '编辑 Redis 值', 'add_title': '添加 Redis 值',
        'score_error': '分数必须是有限数字。',
        'delete_title': '删除选中项', 'delete_question': '确定删除选中项？此操作将写入数据库。',
        'json_error': '值不是有效 JSON；下方显示原始文本。',
        'raw_hint': '按原始文本保存，数字和 JSON 文本不会自动转换。',
        'binary_hint': '二进制或特殊文本 · 只读。查看预览可切换格式或导出原始字节。',
        'dump_hint': 'Redis DUMP 序列化值 · 只读，包含类型与校验数据，并非原始业务内容。',
        'scalar_hint': '模块值 · 只读。查看预览可切换显示格式。',
        'unsafe_text': '文本包含无法无损编辑的特殊字符；请使用只读预览。',
        'search': '搜索已载入的预览…',
        'search_hint': '仅搜索当前已载入预览中的字段、值、成员或分数。',
    },
    'en': {
        'refresh': 'Refresh', 'copy': 'Copy', 'edit': 'Edit', 'delete': 'Delete', 'view': 'View',
        'add_hash': 'Add field', 'add_list': 'Append item', 'add_set': 'Add member',
        'add_zset': 'Add member', 'add': 'Add', 'save': 'Save', 'cancel': 'Cancel',
        'save_value': 'Save value', 'json': 'JSON preview', 'field': 'Field', 'value': 'Value',
        'index': 'Index', 'member': 'Member', 'score': 'Score', 'actions': 'Actions',
        'stream_id': 'Entry ID', 'fields': 'Fields (JSON)', 'ttl': 'TTL',
        'no_expiry': 'No expiry', 'seconds': 'seconds', 'expired': 'Expired', 'bytes': 'bytes',
        'entries': 'items', 'select': 'Select a key to view its value',
        'missing': 'The key is missing or expired. Refresh the key list.',
        'preview': 'Preview limited to 1,000 items. Actions affect the selected item only.',
        'list_preview': 'Preview limited to 1,000 items. Append and edit are available; deleting needs the complete list.',
        'readonly': 'Read-only connection', 'stream_readonly': 'Stream preview · read-only',
        'edit_title': 'Edit Redis value', 'add_title': 'Add Redis value',
        'score_error': 'The score must be a finite number.',
        'delete_title': 'Delete selected item', 'delete_question': 'Delete the selected item? This writes to the database.',
        'json_error': 'The value is not valid JSON. The original text is shown below.',
        'raw_hint': 'Saved as raw text. Numbers and JSON text are never converted automatically.',
        'binary_hint': 'Binary or special text · read-only. View previews to switch formats or export original bytes.',
        'dump_hint': 'Redis DUMP serialization · read-only. This includes type and checksum data, not the original application payload.',
        'scalar_hint': 'Module value · read-only. View previews to switch display formats.',
        'unsafe_text': 'This text contains special characters that cannot be edited losslessly. Use the read-only preview.',
        'search': 'Search loaded preview…',
        'search_hint': 'Search fields, values, members or scores within the loaded preview only.',
    },
}


def _label(name: str = 'muted') -> QLabel:
    label = QLabel()
    label.setObjectName(name)
    label.setTextFormat(Qt.TextFormat.PlainText)
    return label


def _button(name: str, glyph: str | None = None) -> QPushButton:
    button = QPushButton()
    button.setObjectName(name)
    if glyph:
        button.setIcon(icon(glyph, '#588068', 16))
    return button


def _raw(value: Any) -> str:
    if is_editable_text(value):
        return value
    return safe_text(value) if isinstance(value, (str, bytes)) else str(value)


class _RedisEntryDialog(QDialog):
    """A focused raw-text editor. Entry identity cannot change during hash/zset edits."""

    def __init__(self, kind: str, language: str, *, editing: bool,
                 entry: Any = '', value: Any = '', parent: QWidget) -> None:
        super().__init__(parent)
        self.kind, self.editing = kind, editing
        self.setObjectName('redisEntryDialog')
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.resize(600, 370)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(13)
        self.title_label = _label('dialogTitle')
        layout.addWidget(self.title_label)
        form = QFormLayout()
        self.entry_label = _label('fieldLabel')
        self.entry_edit = QLineEdit(_raw(entry))
        self.entry_edit.setObjectName('redisEntryIdentity')
        self.entry_edit.setReadOnly(editing)
        self.entry_edit.setVisible(kind in {'hash', 'zset', 'list'})
        self.entry_label.setVisible(kind in {'hash', 'zset', 'list'})
        form.addRow(self.entry_label, self.entry_edit)
        layout.addLayout(form)
        self.value_label = _label('fieldLabel')
        layout.addWidget(self.value_label)
        self.value_editor = QPlainTextEdit(value if is_editable_text(value) else _raw(value))
        self.value_editor.setObjectName('redisEntryValue')
        layout.addWidget(self.value_editor, 1)
        self.hint_label = _label()
        self.hint_label.setWordWrap(True)
        layout.addWidget(self.hint_label)
        self.error_label = _label('error')
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        layout.addWidget(self.error_label)
        buttons = QHBoxLayout()
        buttons.addStretch()
        self.cancel_button = _button('secondaryButton')
        self.save_button = _button('primaryButton')
        self.save_button.setDefault(True)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.save_button)
        layout.addLayout(buttons)
        self.cancel_button.clicked.connect(self.reject)
        self.save_button.clicked.connect(self._submit)
        self.set_language(language)

    def set_language(self, language: str) -> None:
        self.language = language if language in _TEXT else 'en'
        tr = _TEXT[self.language].__getitem__
        title = tr('edit_title' if self.editing else 'add_title')
        self.setWindowTitle(title)
        self.title_label.setText(title)
        self.entry_label.setText(tr('field' if self.kind == 'hash' else
                                    'index' if self.kind == 'list' else 'member'))
        self.value_label.setText(tr('score' if self.kind == 'zset' else
                                    'member' if self.kind == 'set' else 'value'))
        self.save_button.setText(tr('save' if self.editing else 'add'))
        self.cancel_button.setText(tr('cancel'))
        self.hint_label.setText(tr('raw_hint'))
        self.hint_label.setVisible(self.kind != 'zset')

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self.editing and self.kind in {'hash', 'zset'}:
            self.entry_edit.setFocus()
        else:
            self.value_editor.setFocus()

    def _submit(self) -> None:
        self.error_label.hide()
        if (not is_editable_text(self.value_editor.toPlainText())
                or not self.editing and self.kind in {'hash', 'zset'}
                and not is_editable_text(self.entry_edit.text())):
            self.error_label.setText(_TEXT[self.language]['unsafe_text'])
            self.error_label.show()
            return
        if self.kind == 'zset':
            try:
                valid = math.isfinite(float(self.value_editor.toPlainText()))
            except ValueError:
                valid = False
            if not valid:
                self.error_label.setText(_TEXT[self.language]['score_error'])
                self.error_label.show()
                self.value_editor.setFocus()
                return
        self.accept()


class RedisValueWidget(QWidget):
    """Present one RedisValue snapshot and emit optimistic entry-level mutations.

    The caller owns I/O, errors and history. A mutation leaves the snapshot intact
    and locks writes until ``set_value`` or ``set_busy(False)`` is called.
    """

    mutation_requested = Signal(str, object)
    refresh_requested = Signal()

    def __init__(self, parent: QWidget | None = None, language: str = 'zh') -> None:
        super().__init__(parent)
        self.setObjectName('redisValueWidget')
        self.snapshot = None
        self.read_only = False
        self._busy = False
        self._rows: list[tuple[Any, Any]] = []
        self._row_buttons: list[tuple[QPushButton, QPushButton]] = []
        self.entry_dialog: _RedisEntryDialog | None = None
        self._submitted_dialog: _RedisEntryDialog | None = None
        self._submitted_snapshot = None
        self.preview_dialog: QDialog | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(13)
        header = QHBoxLayout()
        self.type_label = _label('badge')
        self.key_label = _label('redisKey')
        self.key_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.key_label.setWordWrap(True)
        self.key_label.setStyleSheet('font-size: 17px; font-weight: 600; color: #305742;')
        header.addWidget(self.type_label)
        header.addWidget(self.key_label, 1)
        self.refresh_button = _button('redisRefresh', 'refresh')
        header.addWidget(self.refresh_button)
        layout.addLayout(header)
        metadata = QHBoxLayout()
        self.count_label = _label()
        self.ttl_label = _label()
        self.mode_label = _label('statusBadge')
        metadata.addWidget(self.count_label)
        metadata.addWidget(self.ttl_label)
        metadata.addStretch()
        metadata.addWidget(self.mode_label)
        layout.addLayout(metadata)
        toolbar = QHBoxLayout()
        self.add_button = _button('redisAdd', 'plus')
        self.edit_button = _button('redisEdit', 'edit')
        self.delete_button = _button('redisDelete', 'trash')
        self.copy_button = _button('redisCopy', 'copy')
        self.view_button = _button('redisView')
        for button in (self.add_button, self.edit_button, self.delete_button):
            toolbar.addWidget(button)
        toolbar.addStretch()
        toolbar.addWidget(self.view_button)
        toolbar.addWidget(self.copy_button)
        layout.addLayout(toolbar)
        self.entry_search = QLineEdit()
        self.entry_search.setObjectName('redisEntrySearch')
        self.entry_search.setClearButtonEnabled(True)
        self.entry_search.addAction(icon('search', '#7d9485', 16), QLineEdit.ActionPosition.LeadingPosition)
        layout.addWidget(self.entry_search)
        self.stack = QStackedWidget()
        self.empty_label = _label()
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setWordWrap(True)
        self.stack.addWidget(self.empty_label)
        self.table = QTableWidget()
        self.table.setObjectName('redisValueTable')
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setWordWrap(False)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(43)
        self.stack.addWidget(self.table)
        self.string_page = QWidget()
        string_layout = QVBoxLayout(self.string_page)
        string_layout.setContentsMargins(0, 0, 0, 0)
        self.string_editor = QPlainTextEdit()
        self.string_editor.setObjectName('redisStringValue')
        string_layout.addWidget(self.string_editor, 1)
        string_toolbar = QHBoxLayout()
        self.raw_hint_label = _label()
        self.raw_hint_label.setWordWrap(True)
        string_toolbar.addWidget(self.raw_hint_label, 1)
        self.json_button = _button('redisJson')
        self.save_button = _button('primaryButton', 'check')
        string_toolbar.addWidget(self.json_button)
        string_toolbar.addWidget(self.save_button)
        string_layout.addLayout(string_toolbar)
        self.stack.addWidget(self.string_page)
        layout.addWidget(self.stack, 1)
        self.notice_label = _label()
        self.notice_label.setWordWrap(True)
        layout.addWidget(self.notice_label)
        self.refresh_button.clicked.connect(self.refresh_requested)
        self.copy_button.clicked.connect(self._copy)
        self.view_button.clicked.connect(self._show_selected_preview)
        self.add_button.clicked.connect(self._add)
        self.edit_button.clicked.connect(self._edit_selected)
        self.delete_button.clicked.connect(self._delete_selected)
        self.save_button.clicked.connect(self._save_string)
        self.json_button.clicked.connect(self._show_json)
        self.table.itemSelectionChanged.connect(self._update_controls)
        self.entry_search.textChanged.connect(self._apply_filter)
        self.string_editor.textChanged.connect(self._update_controls)
        self.set_language(language)
        self.clear()

    def _tr(self, key: str) -> str:
        return _TEXT[self.language][key]

    @property
    def _kind(self) -> str:
        return str(self.snapshot.kind).lower() if self.snapshot is not None else 'none'

    @property
    def _scalar(self) -> bool:
        return self.snapshot is not None and self._kind not in {
            'none', 'missing', 'hash', 'list', 'set', 'zset', 'stream',
        }

    def _editable_string(self) -> bool:
        return self._kind == 'string' and is_editable_text(self.snapshot.value)

    def set_value(self, snapshot, read_only: bool = False) -> None:
        self._close_dialogs()
        self._submitted_dialog = None
        self._submitted_snapshot = None
        previous_key = self.snapshot.key if self.snapshot is not None else None
        self.snapshot, self.read_only, self._busy = snapshot, read_only, False
        if previous_key != (snapshot.key if snapshot is not None else None):
            self.entry_search.clear()
        self._rows = []
        self._row_buttons = []
        self.table.clearContents()
        self.table.setRowCount(0)
        if snapshot is None or self._kind in {'none', 'missing'}:
            self.string_editor.clear()
            self.stack.setCurrentWidget(self.empty_label)
        elif self._scalar:
            self.string_editor.setPlainText(snapshot.value if self._editable_string()
                                            else preview_value(snapshot.value).text)
            self.stack.setCurrentWidget(self.string_page)
        else:
            self.string_editor.clear()
            data = snapshot.value
            if self._kind == 'hash':
                self._rows = list(data.items())
            elif self._kind == 'list':
                self._rows = list(enumerate(data))
            elif self._kind == 'set':
                self._rows = [(member, member) for member in data]
            elif self._kind == 'zset':
                self._rows = list(data.items() if isinstance(data, dict) else data)
            elif self._kind == 'stream':
                self._rows = list(data)
            self._rows = self._rows[:1000]
            self._populate_table()
            self.stack.setCurrentWidget(self.table)
        self._retranslate()
        self._apply_filter()

    def clear(self) -> None:
        self.set_value(None)

    def _close_dialogs(self) -> None:
        for dialog in (self.entry_dialog, self.preview_dialog):
            if dialog is not None:
                dialog.close()

    def set_language(self, language: str) -> None:
        self.language = language if language in _TEXT else 'en'
        self._retranslate()
        if self.entry_dialog is not None:
            self.entry_dialog.set_language(self.language)
        if self.preview_dialog is not None:
            self.preview_dialog.set_language(self.language)

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._update_controls()

    def restore_failed_edit(self, message: str) -> bool:
        """Reopen a submitted entry draft only while its original snapshot is current."""
        dialog = self._submitted_dialog
        if dialog is None or self.snapshot is not self._submitted_snapshot or not self._can_write():
            return False
        self._submitted_dialog = None
        self._submitted_snapshot = None
        dialog.error_label.setText(message)
        dialog.error_label.show()
        dialog.open()
        return True

    def _retranslate(self) -> None:
        for button, key in ((self.refresh_button, 'refresh'), (self.copy_button, 'copy'),
                            (self.view_button, 'view'),
                            (self.edit_button, 'edit'), (self.delete_button, 'delete'),
                            (self.save_button, 'save_value'), (self.json_button, 'json')):
            button.setText(self._tr(key))
        self.add_button.setText(self._tr('add_' + self._kind if self._kind in
                                       {'hash', 'list', 'set', 'zset'} else 'add'))
        hint = ('raw_hint' if self._editable_string() else 'binary_hint') if self._kind == 'string' else (
            'dump_hint' if self._scalar and isinstance(self.snapshot.value, bytes) else 'scalar_hint')
        self.raw_hint_label.setText(self._tr(hint))
        self.entry_search.setPlaceholderText(self._tr('search'))
        self.entry_search.setToolTip(self._tr('search_hint'))
        self.entry_search.setVisible(self._kind in {'hash', 'list', 'set', 'zset', 'stream'})
        self.empty_label.setText(self._tr('select' if self.snapshot is None else 'missing'))
        self.key_label.setText(safe_text(self.snapshot.key) if self.snapshot is not None else '')
        self.type_label.setText(self._kind.upper() if self.snapshot is not None else 'REDIS')
        size = self.snapshot.size if self.snapshot is not None else 0
        unit = 'bytes' if self._scalar else 'entries'
        self.count_label.setText(f'{size:,} {self._tr(unit)}' if self.snapshot is not None else '')
        ttl = self.snapshot.ttl_ms if self.snapshot is not None else -1
        ttl_text = self._tr('no_expiry') if ttl == -1 else self._tr('expired') if ttl == -2 else f'{ttl / 1000:g} {self._tr("seconds")}'
        self.ttl_label.setText(f'{self._tr("ttl")}: {ttl_text}' if self.snapshot is not None else '')
        self.mode_label.setText(self._tr('stream_readonly' if self._kind == 'stream' else 'readonly'))
        self.mode_label.setVisible(self.read_only or self._kind == 'stream')
        truncated = self.snapshot is not None and (self.snapshot.truncated or size > len(self._rows)) and not self._scalar and self._kind not in {'none', 'missing'}
        self.notice_label.setText(self._tr('list_preview' if self._kind == 'list' else 'preview'))
        self.notice_label.setVisible(truncated)
        columns = self._column_labels()
        self.table.setColumnCount(len(columns))
        self.table.setHorizontalHeaderLabels(columns)
        for edit, delete in self._row_buttons:
            edit.setToolTip(self._tr('edit'))
            edit.setAccessibleName(self._tr('edit'))
            delete.setToolTip(self._tr('delete'))
            delete.setAccessibleName(self._tr('delete'))
        self._update_controls()

    def _column_labels(self) -> list[str]:
        keys = {'hash': ['field', 'value'], 'list': ['index', 'value'],
                'set': ['member'], 'zset': ['member', 'score'],
                'stream': ['stream_id', 'fields']}.get(self._kind, ['value'])
        return [self._tr(key) for key in keys + ([] if self._kind == 'stream' else ['actions'])]

    def _populate_table(self) -> None:
        self.table.setColumnCount(len(self._column_labels()))
        self.table.setRowCount(len(self._rows))
        for row, (entry, raw_value) in enumerate(self._rows):
            cells = [entry] if self._kind == 'set' else [entry, raw_value]
            if self._kind == 'stream':
                cells[1] = json.dumps(json_safe(raw_value), ensure_ascii=False)
            for column, cell in enumerate(cells):
                text = _raw(cell)
                item = QTableWidgetItem(text)
                item.setToolTip(text)
                self.table.setItem(row, column, item)
            if self._kind == 'stream':
                continue
            actions = QWidget()
            buttons = QHBoxLayout(actions)
            buttons.setContentsMargins(7, 1, 7, 1)
            buttons.setSpacing(3)
            edit = _button('redisRowEdit', 'edit')
            delete = _button('redisRowDelete', 'trash')
            for button in (edit, delete):
                button.setFixedSize(30, 30)
                button.setStyleSheet('padding: 3px; border: none; background: transparent;')
                buttons.addWidget(button)
            buttons.addStretch()
            edit.clicked.connect(lambda _checked=False, index=row: self._edit(index))
            delete.clicked.connect(lambda _checked=False, index=row: self._delete(index))
            self.table.setCellWidget(row, len(cells), actions)
            self._row_buttons.append((edit, delete))
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch if self._kind == 'set' else QHeaderView.ResizeMode.Interactive)
        if self._kind != 'set':
            self.table.setColumnWidth(0, 210 if self._kind != 'list' else 90)
            header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        if self._kind != 'stream':
            actions_column = self.table.columnCount() - 1
            header.setSectionResizeMode(actions_column, QHeaderView.ResizeMode.Fixed)
            self.table.setColumnWidth(actions_column, 92)

    def _can_write(self) -> bool:
        return (not self.read_only and not self._busy
                and self._kind in {'string', 'hash', 'list', 'set', 'zset'}
                and (self._kind != 'string' or self._editable_string()))

    def _can_delete(self) -> bool:
        return self._can_write() and not (self._kind == 'list' and self.snapshot.truncated)

    def _can_edit_row(self, row: int) -> bool:
        return self._can_write() and 0 <= row < len(self._rows) and (
            self._kind == 'zset' or is_editable_text(self._rows[row][1]))

    def _apply_filter(self) -> None:
        needle = self.entry_search.text().casefold()
        for row in range(self.table.rowCount()):
            matches = not needle or any(
                needle in self.table.item(row, column).text().casefold()
                for column in range(self.table.columnCount())
                if self.table.item(row, column) is not None)
            self.table.setRowHidden(row, not matches)
        selected = self.table.currentRow()
        if selected >= 0 and self.table.isRowHidden(selected):
            self.table.clearSelection()
            self.table.setCurrentItem(None)
        self._update_controls()

    def _update_controls(self) -> None:
        mutable = self._can_write()
        collection = self._kind in {'hash', 'list', 'set', 'zset'}
        selected = self.table.currentRow() >= 0 and not self.table.isRowHidden(self.table.currentRow())
        for button in (self.add_button, self.edit_button, self.delete_button):
            button.setVisible(not self._scalar)
        self.add_button.setEnabled(mutable and collection)
        self.edit_button.setEnabled(collection and selected and self._can_edit_row(self.table.currentRow()))
        self.delete_button.setEnabled(self._can_delete() and collection and selected)
        self.save_button.setEnabled(mutable and self._editable_string()
                                    and is_editable_text(self.string_editor.toPlainText()))
        self.string_editor.setReadOnly(not (mutable and self._editable_string()))
        self.copy_button.setEnabled(self._scalar or bool(self._rows) and selected)
        self.view_button.setEnabled(self._scalar or bool(self._rows) and selected)
        self.json_button.setEnabled(self._scalar)
        self.refresh_button.setEnabled(self.snapshot is not None and not self._busy)
        for row, (edit, delete) in enumerate(self._row_buttons):
            edit.setEnabled(self._can_edit_row(row))
            delete.setEnabled(self._can_delete())

    def _emit_mutation(self, action: str, payload: dict) -> None:
        if not self._can_write():
            return
        self.set_busy(True)
        self.mutation_requested.emit(action, payload)

    def _add(self) -> None:
        if self._can_write():
            self._open_entry_dialog()

    def _edit_selected(self) -> None:
        self._edit(self.table.currentRow())

    def _edit(self, row: int) -> None:
        if self._can_edit_row(row):
            self._open_entry_dialog(row)
        elif 0 <= row < len(self._rows):
            self._show_row_preview(row)

    def _open_entry_dialog(self, row: int | None = None) -> None:
        if self.entry_dialog is not None:
            self.entry_dialog.close()
        editing = row is not None
        entry, old_value = self._rows[row] if editing else ('', '')
        dialog = _RedisEntryDialog(self._kind, self.language, editing=editing,
                                   entry=entry, value=old_value, parent=self)
        if self._kind == 'list' and not editing:
            dialog.entry_edit.hide()
            dialog.entry_label.hide()
        self.entry_dialog = dialog

        def accepted() -> None:
            if not self._can_write():
                return
            payload = {'value': dialog.value_editor.toPlainText()}
            if editing or self._kind in {'hash', 'zset'}:
                payload['entry'] = entry if editing else dialog.entry_edit.text()
            if editing and self._kind in {'hash', 'list', 'zset'}:
                payload['expected'] = old_value
            self._submitted_dialog = dialog
            self._submitted_snapshot = self.snapshot
            self._emit_mutation('update' if editing else 'add', payload)

        dialog.accepted.connect(accepted)
        dialog.open()

    def _delete_selected(self) -> None:
        self._delete(self.table.currentRow())

    def _delete(self, row: int) -> None:
        if not self._can_delete() or not 0 <= row < len(self._rows):
            return
        snapshot = self.snapshot
        entry, old_value = self._rows[row]
        answer = QMessageBox.question(self, self._tr('delete_title'), self._tr('delete_question'),
                                      QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                      QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes or self.snapshot is not snapshot:
            return
        payload = {'entry': entry}
        if self._kind in {'hash', 'list', 'zset'}:
            payload['expected'] = old_value
        self._emit_mutation('delete', payload)

    def _save_string(self) -> None:
        if self._editable_string() and is_editable_text(self.string_editor.toPlainText()):
            self._emit_mutation('update', {'value': self.string_editor.toPlainText(),
                                           'expected': self.snapshot.value})

    def _copy(self) -> None:
        if self._scalar:
            QApplication.clipboard().setText(self.string_editor.toPlainText())
        elif 0 <= self.table.currentRow() < len(self._rows):
            row = self.table.currentRow()
            cells = [self.table.item(row, column).text() for column in range(self.table.columnCount())
                     if self.table.item(row, column) is not None]
            QApplication.clipboard().setText('\t'.join(cells))

    def _show_json(self) -> None:
        if self._scalar:
            self._open_preview(self.string_editor.toPlainText() if self._editable_string()
                               else self.snapshot.value, format='json' if self._editable_string() else 'auto')

    def _show_selected_preview(self) -> None:
        if self._scalar:
            self._open_preview(self.string_editor.toPlainText() if self._editable_string() else self.snapshot.value)
        else:
            self._show_row_preview(self.table.currentRow())

    def _show_row_preview(self, row: int) -> None:
        if 0 <= row < len(self._rows):
            entry, value = self._rows[row]
            self._open_preview(entry if self._kind in {'set', 'zset'} else value)

    def _open_preview(self, value: Any, *, format: str = 'auto') -> None:
        if self.preview_dialog is not None:
            self.preview_dialog.close()
        dialog = RedisPreviewDialog(value, self.language, format=format, parent=self)
        self.preview_editor = dialog.editor
        self.preview_dialog = dialog
        dialog.open()
