"""CSV encoding, import preview and explicit target-column mapping."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from baseskiff.data_transfer import CsvOptions
from baseskiff.ui.dialog_layout import configure_buttons, configure_form, fit_dialog
from baseskiff.ui.theme import STYLE


class CsvOptionsDialog(QDialog):
    def __init__(self, parent=None, language='zh', options=None):
        super().__init__(parent)
        self.setStyleSheet(STYLE)
        self.language = language
        options = options or CsvOptions()
        self.setWindowTitle(self.t('CSV 格式', 'CSV format'))
        outer = QVBoxLayout(self)
        form = QFormLayout()
        self.encoding = QComboBox()
        self.encoding.addItems(['utf-8-sig', 'utf-8', 'gb18030', 'utf-16', 'latin-1'])
        self.encoding.setCurrentText(options.encoding)
        self.delimiter = QComboBox()
        for title, value in [(self.t('逗号', 'Comma'), ','), (self.t('制表符', 'Tab'), '\t'), (self.t('分号', 'Semicolon'), ';')]:
            self.delimiter.addItem(title, value)
        self.delimiter.setCurrentIndex(max(0, self.delimiter.findData(options.delimiter)))
        self.null_value = QLineEdit(options.null_value)
        self.null_value.setToolTip(self.t('与此文本完全相同的字段将转换为 NULL；空字符串可设为 NULL 标记。',
                                          'A field matching this text exactly is NULL. An empty marker treats empty fields as NULL.'))
        form.addRow(self.t('编码', 'Encoding'), self.encoding)
        form.addRow(self.t('分隔符', 'Delimiter'), self.delimiter)
        form.addRow(self.t('NULL 标记', 'NULL marker'), self.null_value)
        configure_form(form)
        outer.addLayout(form)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText(self.t('确定', 'OK'))
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(self.t('取消', 'Cancel'))
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        configure_buttons(self.buttons)
        outer.addWidget(self.buttons)
        fit_dialog(self, 460, 270)

    def t(self, zh, en):
        return zh if self.language == 'zh' else en

    def options(self):
        return CsvOptions(self.encoding.currentText(), self.delimiter.currentData(), self.null_value.text())


class CsvImportDialog(QDialog):
    def __init__(self, headers, rows, columns, parent=None, language='zh'):
        super().__init__(parent)
        self.setStyleSheet(STYLE)
        self.language = language
        self.setWindowTitle(self.t('CSV 导入预览与字段映射', 'CSV import preview and column mapping'))
        fit_dialog(self, 850, 540)
        outer = QVBoxLayout(self)
        note = QLabel(self.t('首行为标题。下方选择目标字段；确认后在一个事务中导入全部行，失败或取消会回滚。',
                            'The first row is the header. Map target columns below. All rows import in one transaction; errors or cancellation roll back.'))
        note.setWordWrap(True)
        outer.addWidget(note)
        self.preview = QTableWidget(len(rows), len(headers))
        self.preview.setHorizontalHeaderLabels(headers)
        self.preview.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        for i, row in enumerate(rows):
            for j, text in enumerate(row[:len(headers)]):
                item = QTableWidgetItem(text[:1000])
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.preview.setItem(i,j,item)
        outer.addWidget(self.preview)
        self.mapping_widgets = []
        form = QFormLayout()
        for index, title in enumerate(headers):
            combo = QComboBox()
            combo.addItem(self.t('跳过', 'Skip'), None)
            for column in columns:
                combo.addItem(f'{column.name} · {column.data_type}', column.name)
            match = next((c.name for c in columns if c.name.casefold() == title.casefold()), None)
            if match is not None:
                combo.setCurrentIndex(combo.findData(match))
            self.mapping_widgets.append(combo)
            form.addRow(f'{index + 1}. {title}', combo)
        from PySide6.QtWidgets import QScrollArea, QWidget
        host = QWidget()
        configure_form(form)
        host.setLayout(form)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(host)
        outer.addWidget(scroll)
        self.error = QLabel()
        self.error.setObjectName("error")
        self.error.setWordWrap(True)
        self.error.hide()
        outer.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(self.t('导入全部行', 'Import all rows'))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(self.t('取消', 'Cancel'))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        configure_buttons(buttons)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(buttons)
        outer.addLayout(row)

    def t(self, zh, en):
        return zh if self.language == 'zh' else en

    def mapping(self):
        return {i: combo.currentData() for i, combo in enumerate(self.mapping_widgets) if combo.currentData() is not None}

    def accept(self):
        mapping = self.mapping()
        if not mapping or len(set(mapping.values())) != len(mapping):
            self.error.show()
            self.error.setText(self.t('请至少映射一个字段，且目标字段不能重复。', 'Map at least one column, without duplicate targets.'))
            return
        super().accept()
