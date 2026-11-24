"""Table structure editor with local drafts and a read-only DDL preview."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from baseskiff.sql_structure import StructureDraft
from baseskiff.ui.theme import STYLE


class SqlStructureDialog(QDialog):
    """The owner loads metadata and executes apply_requested SQL asynchronously.

    Call mark_applied with refreshed catalogs after success, or show_apply_error
    after failure. DDL may partially commit; the owner must report completed steps
    and reload metadata before permitting another attempt.
    """
    apply_requested = Signal(list)

    def __init__(self, kind, table, columns, indexes, parent=None, language='zh', read_only=False):
        super().__init__(parent)
        self.language = language
        self.read_only = read_only
        self.kind, self.table = kind, table
        self._busy = False
        self._reload_required = False
        self._mode = None
        self._original = None
        self._baseline_columns, self._baseline_indexes = list(columns), list(indexes)
        self.draft = StructureDraft(kind, table, columns, indexes)
        self.setObjectName('sqlStructureDialog')
        self.setWindowTitle(self._t('修改表结构', 'Modify table structure') + ' — ' + table)
        self.setStyleSheet(STYLE)
        self.resize(960, 740)
        self.setMinimumSize(740, 580)
        outer = QVBoxLayout(self)
        title = QLabel(self.windowTitle())
        title.setObjectName('dialogTitle')
        outer.addWidget(title)
        self.notice = QLabel(self._t('修改先暂存在本窗口；检查 SQL 后点击「应用」。DDL 可能逐条提交，失败时此前操作可能已生效。',
            'Changes stay local until Apply. Review the SQL below. DDL may commit each statement; earlier steps can remain applied after a failure.'))
        self.notice.setWordWrap(True)
        outer.addWidget(self.notice)
        self.body = QWidget()
        body_layout = QVBoxLayout(self.body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels([self._t('对象 / 类型', 'Object / type')])
        self.tree.currentItemChanged.connect(self._selected)
        left_layout.addWidget(self.tree)
        tools = QHBoxLayout()
        self.add_column_button = QPushButton(self._t('+ 字段', '+ Column'))
        self.add_index_button = QPushButton(self._t('+ 索引', '+ Index'))
        self.drop_button = QPushButton(self._t('删除', 'Drop'))
        for button in (self.add_column_button, self.add_index_button, self.drop_button):
            tools.addWidget(button)
            button.setAutoDefault(False)
        self.add_column_button.clicked.connect(self._new_column)
        self.add_index_button.clicked.connect(self._new_index)
        self.drop_button.clicked.connect(self._drop)
        left_layout.addLayout(tools)
        splitter.addWidget(left)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        self.properties = QStackedWidget()
        empty = QLabel(self._t('选择字段或索引以查看属性，或点击 + 创建。', 'Select a column or index, or use + to create one.'))
        empty.setWordWrap(True)
        self.properties.addWidget(empty)
        column_page = QWidget()
        form = QFormLayout(column_page)
        self.name_edit = QLineEdit()
        self.type_edit = QLineEdit('TEXT')
        self.nullable_check = QCheckBox(self._t('允许 NULL', 'Allow NULL'))
        self.nullable_check.setChecked(True)
        self.default_kind = QComboBox()
        for zh, en, key in [('无默认值', 'No default', 'none'), ('文本', 'Text literal', 'literal'), ('数字', 'Number', 'number'), ('NULL', 'NULL', 'null')]:
            self.default_kind.addItem(self._t(zh, en), key)
        self.default_edit = QLineEdit()
        form.addRow(self._t('名称', 'Name'), self.name_edit)
        form.addRow(self._t('类型', 'Type'), self.type_edit)
        form.addRow('', self.nullable_check)
        form.addRow(self._t('默认值类型', 'Default kind'), self.default_kind)
        form.addRow(self._t('默认值', 'Default value'), self.default_edit)
        self.properties.addWidget(column_page)
        index_page = QWidget()
        form = QFormLayout(index_page)
        self.index_name_edit = QLineEdit()
        self.unique_check = QCheckBox(self._t('唯一索引', 'Unique index'))
        self.index_columns = QListWidget()
        self.index_columns.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        form.addRow(self._t('名称', 'Name'), self.index_name_edit)
        form.addRow('', self.unique_check)
        form.addRow(self._t('字段（勾选并拖动排序）', 'Columns (check; drag to order)'), self.index_columns)
        self.properties.addWidget(index_page)
        right_layout.addWidget(self.properties)
        self.property_hint = QLabel()
        self.property_hint.setWordWrap(True)
        right_layout.addWidget(self.property_hint)
        self.stage_button = QPushButton(self._t('暂存属性修改', 'Stage changes'))
        self.stage_button.setAutoDefault(False)
        self.stage_button.clicked.connect(self._stage)
        right_layout.addWidget(self.stage_button)
        splitter.addWidget(right)
        splitter.setSizes([340, 500])
        body_layout.addWidget(splitter, 3)
        body_layout.addWidget(QLabel(self._t('待应用 SQL（只读预览）', 'Pending SQL (read-only preview)')))
        self.sql_preview = QPlainTextEdit()
        self.sql_preview.setReadOnly(True)
        self.sql_preview.setMinimumHeight(150)
        body_layout.addWidget(self.sql_preview, 2)
        outer.addWidget(self.body, 1)
        self.error_label = QLabel()
        self.error_label.setWordWrap(True)
        self.error_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        outer.addWidget(self.error_label)
        footer = QHBoxLayout()
        self.reset_button = QPushButton(self._t('撤销全部暂存', 'Revert draft'))
        self.reset_button.clicked.connect(self._reset)
        self.apply_button = QPushButton(self._t('应用', 'Apply'))
        self.apply_button.clicked.connect(self._apply)
        self.close_button = QPushButton(self._t('关闭', 'Close'))
        self.close_button.clicked.connect(self.reject)
        footer.addWidget(self.reset_button)
        footer.addStretch()
        footer.addWidget(self.close_button)
        footer.addWidget(self.apply_button)
        outer.addLayout(footer)
        self._refresh()

    def _t(self, zh, en):
        return en if self.language == 'en' else zh

    @property
    def statements(self):
        return list(self.draft.statements)

    def _refresh(self):
        self.tree.clear()
        columns = QTreeWidgetItem(self.tree, [self._t('字段', 'Columns')])
        indexes = QTreeWidgetItem(self.tree, [self._t('索引', 'Indexes')])
        for column in self.draft.columns:
            item = QTreeWidgetItem(columns, [f'{column.name} : {column.data_type}' + ('  [PK]' if column.primary_key else '')])
            item.setData(0, Qt.ItemDataRole.UserRole, ('column', column.name))
        for index in self.draft.indexes:
            item = QTreeWidgetItem(indexes, [index.name + ('  [UNIQUE]' if index.unique else '')])
            item.setData(0, Qt.ItemDataRole.UserRole, ('index', index.name))
        self.tree.expandAll()
        self.sql_preview.setPlainText(';\n\n'.join(self.statements) + (';' if self.statements else ''))
        self.apply_button.setEnabled(bool(self.statements) and not self.read_only and not self._busy and not self._reload_required)
        self.reset_button.setEnabled(bool(self.statements) and not self._busy and not self._reload_required)
        self.add_column_button.setEnabled(not self.read_only)
        self.add_index_button.setEnabled(not self.read_only)
        self._selected(None)

    def _selected(self, item, previous=None):
        self._mode, self._original = None, None
        self.drop_button.setEnabled(False)
        self.stage_button.setEnabled(False)
        self.properties.setCurrentIndex(0)
        self.property_hint.clear()
        if item is None:
            return
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data:
            return
        kind, name = data
        self._mode, self._original = kind, name
        if kind == 'column':
            column = self.draft._column(name)
            self._column_form(column.name, column.data_type, column.nullable)
            self.default_edit.setText('' if column.default is None else str(column.default))
            self.default_kind.setEnabled(False)
            self.default_edit.setEnabled(False)
            self.nullable_check.setEnabled(False)
            self.type_edit.setEnabled(self.kind != 'SQLite' and not self.read_only)
            self.property_hint.setText(self._t('已有字段支持重命名和类型修改；NULL 与默认值只在新增时设置。SQLite 类型修改需重建表，已禁用。MySQL 类型修改需完整字段定义，请使用 Console。',
                'Existing columns support rename/type changes. NULL/default are editable for new columns. SQLite type changes require a table rebuild. MySQL type changes require a full definition; use Console.'))
            if self.kind == 'MySQL':
                self.type_edit.setEnabled(False)
            self.drop_button.setEnabled(not self.read_only and not column.primary_key and len(self.draft.columns) > 1)
            self.stage_button.setEnabled(not self.read_only)
        else:
            index = next(i for i in self.draft.indexes if i.name == name)
            self._index_form(index.name, index.columns, index.unique, editable=False)
            self.drop_button.setEnabled(not self.read_only and not self.draft.index_protected(index))
            self.property_hint.setText(self._t('已有索引只读。修改定义请先删除，再创建。约束索引不能在这里删除。',
                'Existing indexes are read-only. Drop and recreate to change their definition. Constraint indexes are protected.'))

    def _column_form(self, name='', data_type='TEXT', nullable=True):
        self.properties.setCurrentIndex(1)
        self.name_edit.setText(name)
        self.type_edit.setText(data_type)
        self.nullable_check.setChecked(nullable)
        self.default_kind.setCurrentIndex(0)
        self.default_edit.clear()
        for widget in (self.name_edit, self.type_edit, self.nullable_check, self.default_kind, self.default_edit):
            widget.setEnabled(not self.read_only)

    def _index_form(self, name='', columns=(), unique=False, editable=True):
        self.properties.setCurrentIndex(2)
        self.index_name_edit.setText(name)
        self.unique_check.setChecked(unique)
        self.index_columns.clear()
        names = list(columns) + [c.name for c in self.draft.columns if c.name not in columns]
        for name in names:
            item = QListWidgetItem(name, self.index_columns)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if name in columns else Qt.CheckState.Unchecked)
        for widget in (self.index_name_edit, self.unique_check, self.index_columns):
            widget.setEnabled(editable and not self.read_only)

    def _new_column(self):
        self.tree.setCurrentItem(None)
        self._mode, self._original = 'new_column', None
        self._column_form()
        self.property_hint.setText(self._t('类型填写名称和可选长度 / 精度。默认文本按字面值保存，无需引号。',
            'Enter a type name and optional size/precision. Text defaults are literal values; do not add SQL quotes.'))
        self.stage_button.setEnabled(not self.read_only)
        self.name_edit.setFocus()

    def _new_index(self):
        self.tree.setCurrentItem(None)
        self._mode, self._original = 'new_index', None
        self._index_form()
        self.property_hint.clear()
        self.stage_button.setEnabled(not self.read_only)
        self.index_name_edit.setFocus()

    def _stage(self):
        if self.read_only or self._busy or self._reload_required:
            return
        try:
            if self._mode == 'new_column':
                kind = self.default_kind.currentData()
                value = None if kind == 'none' else self.default_edit.text()
                self.draft.add_column(self.name_edit.text(), self.type_edit.text(), self.nullable_check.isChecked(), value,
                                      'literal' if kind == 'none' else kind)
            elif self._mode == 'column':
                self.draft.alter_column(self._original, self.name_edit.text(), self.type_edit.text())
            elif self._mode == 'new_index':
                columns = [self.index_columns.item(i).text() for i in range(self.index_columns.count())
                           if self.index_columns.item(i).checkState() == Qt.CheckState.Checked]
                self.draft.create_index(self.index_name_edit.text(), columns, self.unique_check.isChecked())
            self.error_label.clear()
            self._refresh()
        except ValueError as exc:
            self.error_label.setText(str(exc))

    def _drop(self):
        if self.read_only or self._busy or self._reload_required or not self._original:
            return
        if QMessageBox.question(self, self._t('确认删除', 'Confirm drop'),
                self._t(f'暂存删除「{self._original}」？点击应用后才生效。', f'Stage dropping “{self._original}”? This takes effect only after Apply.'),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        try:
            if self._mode == 'column':
                self.draft.drop_column(self._original)
            else:
                self.draft.drop_index(self._original)
            self.error_label.clear()
            self._refresh()
        except ValueError as exc:
            self.error_label.setText(str(exc))

    def _reset(self):
        if self._reload_required:
            return
        self.draft = StructureDraft(self.kind, self.table, self._baseline_columns, self._baseline_indexes)
        self.error_label.clear()
        self._refresh()

    def _apply(self):
        if self.statements and not self.read_only and not self._busy and not self._reload_required:
            statements = self.statements
            self.set_busy(True)
            self.apply_requested.emit(statements)

    def set_busy(self, busy):
        self._busy = busy
        self.body.setEnabled(not busy and not self._reload_required)
        self.close_button.setEnabled(not busy)
        self.apply_button.setEnabled(not busy and bool(self.statements) and not self.read_only and not self._reload_required)
        self.reset_button.setEnabled(not busy and bool(self.statements) and not self._reload_required)

    def mark_applied(self, columns, indexes):
        self._baseline_columns, self._baseline_indexes = list(columns), list(indexes)
        self._reload_required = False
        self.set_busy(False)
        self._reset()

    def show_apply_error(self, message):
        self._reload_required = True
        self.set_busy(False)
        self.error_label.setText(message)
        # Never retry an ambiguous partially applied draft. Owner reloads catalogs.
        self.apply_button.setEnabled(False)

    def reject(self):
        if self._busy:
            return
        if self.statements and QMessageBox.question(self, self._t('丢弃暂存修改', 'Discard draft'),
                self._t('关闭并丢弃本地暂存修改？', 'Close and discard local staged changes?'),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        super().reject()
