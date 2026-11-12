"""Read-only Redis payload previews; rendered data is never a mutation draft."""
from __future__ import annotations

import json
import unicodedata
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from baseskiff.redis_codec import json_safe, preview_value, raw_bytes, safe_text

_TEXT = {
    'zh': {
        'title': 'Redis 值预览 · 只读', 'format': '格式', 'auto': '自动识别',
        'text': '文本', 'json': 'JSON', 'msgpack': 'MessagePack', 'hex': 'Hex',
        'base64': 'Base64', 'export': '导出原始字节…', 'close': '关闭',
        'readonly': '预览不会修改或保存原始数据。',
        'nested': '结构预览中的二进制字段使用 Base64 标注；选择独立值可导出原始字节。',
        'export_title': '导出 Redis 原始字节', 'export_error': '无法导出文件：',
        'notice': '格式提示', 'value': '字段值', 'structure': '完整结构',
        'file_types': '二进制文件 (*.bin);;所有文件 (*)',
        'detected': '已识别：',
    },
    'en': {
        'title': 'Redis value preview · read-only', 'format': 'Format', 'auto': 'Auto',
        'text': 'Text', 'json': 'JSON', 'msgpack': 'MessagePack', 'hex': 'Hex',
        'base64': 'Base64', 'export': 'Export original bytes…', 'close': 'Close',
        'readonly': 'Previewing does not change or save the original data.',
        'nested': 'Binary fields use Base64 annotations in this structure preview. Select an individual value to export its original bytes.',
        'export_title': 'Export original Redis bytes', 'export_error': 'Could not export file: ',
        'notice': 'Format notice', 'value': 'Field value', 'structure': 'Whole structure',
        'file_types': 'Binary files (*.bin);;All files (*)',
        'detected': 'Detected: ',
    },
}

_NOTICE_ZH = {
    'Unknown preview format; showing hexadecimal bytes.': '未知预览格式，显示 Hex 字节。',
    'Preview truncated; export retains all bytes.': '预览已截断，导出仍保留全部字节。',
    'Value exceeds decode limit (1 MiB).': '值超过解码上限（1 MiB）。',
    'Invalid UTF-8 or control characters are shown as escapes.': '无效 UTF-8 或控制字符以转义形式显示。',
    'Unable to decode as JSON or structure exceeds preview limits; showing Hex.': '无法解码为 JSON 或结构超过预览上限，显示 Hex。',
    'Unable to decode as JSON; showing hexadecimal bytes.': '无法解码为 JSON，显示 Hex 字节。',
    'Unable to decode a complete MessagePack object or structure exceeds preview limits.': '无法解码完整的 MessagePack 对象，或结构超过预览上限。',
}


def is_editable_text(value: Any) -> bool:
    """Only expose text which QPlainTextEdit can return without normalization.

    Qt normalizes carriage returns, paragraph separators, frame markers and
    nonbreaking spaces, and drops byte-order markers. Embedded controls and
    lone surrogates also stay in the read-only byte view.
    """
    return isinstance(value, str) and all(
        character not in {'\r', '\u00a0', '\u2028', '\u2029', '\ufdd0', '\ufdd1', '\ufeff', '\ufffe'}
        and (character in {'\t', '\n'} or unicodedata.category(character) not in {'Cc', 'Cs'})
        for character in value
    )


class RedisPreviewDialog(QDialog):
    """Keep the source object and export its bytes, regardless of display format."""

    def __init__(self, value: Any, language: str = 'en', *, format: str = 'auto',
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.raw_value = value
        self._scalar = isinstance(value, (str, bytes))
        self.setObjectName('redisPreviewDialog')
        self.resize(720, 500)
        layout = QVBoxLayout(self)
        self._field_values = list(value.items())[:1000] if isinstance(value, dict) else []
        value_toolbar = QHBoxLayout()
        self.value_label = QLabel()
        value_toolbar.addWidget(self.value_label)
        self.value_combo = QComboBox()
        self.value_combo.setObjectName('redisPreviewField')
        self.value_combo.addItem('')
        for key, _value in self._field_values:
            self.value_combo.addItem(safe_text(key) if isinstance(key, (str, bytes)) else str(key))
        value_toolbar.addWidget(self.value_combo, 1)
        self.value_label.setVisible(bool(self._field_values))
        self.value_combo.setVisible(bool(self._field_values))
        layout.addLayout(value_toolbar)
        toolbar = QHBoxLayout()
        self.format_label = QLabel()
        toolbar.addWidget(self.format_label)
        self.format_combo = QComboBox()
        self.format_combo.setObjectName('redisPreviewFormat')
        for name in ('auto', 'text', 'json', 'msgpack', 'hex', 'base64'):
            self.format_combo.addItem(name, name)
        self.format_combo.setCurrentIndex(max(0, self.format_combo.findData(format)))
        self.format_combo.setEnabled(self._scalar)
        toolbar.addWidget(self.format_combo)
        self.detected_label = QLabel()
        self.detected_label.setObjectName('redisPreviewDetected')
        toolbar.addWidget(self.detected_label)
        toolbar.addStretch()
        layout.addLayout(toolbar)
        self.editor = QPlainTextEdit()
        self.editor.setObjectName('redisPreviewValue')
        self.editor.setReadOnly(True)
        layout.addWidget(self.editor, 1)
        self.notice_label = QLabel()
        self.notice_label.setTextFormat(Qt.TextFormat.PlainText)
        self.notice_label.setWordWrap(True)
        layout.addWidget(self.notice_label)
        buttons = QHBoxLayout()
        self.hint_label = QLabel()
        self.hint_label.setWordWrap(True)
        buttons.addWidget(self.hint_label, 1)
        self.export_button = QPushButton()
        self.export_button.setObjectName('redisPreviewExport')
        self.export_button.setEnabled(self._scalar)
        buttons.addWidget(self.export_button)
        self.close_button = QPushButton()
        buttons.addWidget(self.close_button)
        layout.addLayout(buttons)
        self.format_combo.currentIndexChanged.connect(self._render)
        self.value_combo.currentIndexChanged.connect(self._render)
        self.export_button.clicked.connect(self._export)
        self.close_button.clicked.connect(self.close)
        self.set_language(language)

    def set_language(self, language: str) -> None:
        self.language = language if language in _TEXT else 'en'
        tr = _TEXT[self.language].__getitem__
        self.setWindowTitle(tr('title'))
        self.value_label.setText(tr('value'))
        self.value_combo.setItemText(0, tr('structure'))
        self.format_label.setText(tr('format'))
        for index in range(self.format_combo.count()):
            self.format_combo.setItemText(index, tr(self.format_combo.itemData(index)))
        self.export_button.setText(tr('export'))
        self.close_button.setText(tr('close'))
        self._render()

    def _render(self) -> None:
        value = self._current_value()
        self._scalar = isinstance(value, (str, bytes))
        self.format_combo.setEnabled(self._scalar)
        self.export_button.setEnabled(self._scalar)
        self.hint_label.setText(_TEXT[self.language]['readonly' if self._scalar else 'nested'])
        if self._scalar:
            preview = preview_value(value, self.format_combo.currentData())
            text, notice = preview.text, preview.notice
        else:
            rendered = json.dumps(json_safe(value), ensure_ascii=False, indent=2)
            rendered = rendered.encode('utf-8', errors='backslashreplace').decode('utf-8')
            preview = preview_value(rendered, 'text')
            text, notice = preview.text, preview.notice
        if self.language == 'zh':
            for source, translated in _NOTICE_ZH.items():
                notice = notice.replace(source, translated)
        detected = preview.format if self._scalar else 'json'
        self.detected_label.setText(_TEXT[self.language]['detected'] + _TEXT[self.language][detected])
        self.editor.setPlainText(text)
        self.notice_label.setText(f'{_TEXT[self.language]["notice"]}: {notice}' if notice else '')
        self.notice_label.setVisible(bool(notice))

    def _current_value(self) -> Any:
        index = self.value_combo.currentIndex()
        return self._field_values[index - 1][1] if index > 0 else self.raw_value

    def _export(self) -> None:
        if not self._scalar:
            return
        destination, _selected_filter = QFileDialog.getSaveFileName(
            self, _TEXT[self.language]['export_title'], 'redis-value.bin',
            _TEXT[self.language]['file_types'],
        )
        if not destination:
            return
        try:
            Path(destination).write_bytes(raw_bytes(self._current_value()))
        except (OSError, UnicodeError) as error:
            self.notice_label.setText(_TEXT[self.language]['export_error'] + str(error))
            self.notice_label.show()
