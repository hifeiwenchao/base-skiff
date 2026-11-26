"""Dialogs retain reachable actions and scrollable forms on small logical screens."""
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QRect
from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QPushButton, QScrollArea

from baseskiff.models import ColumnInfo, QueryResult
from baseskiff.ui.data_transfer import CsvImportDialog, CsvOptionsDialog
from baseskiff.ui.dialogs import ConnectionDialog, RedisKeyDialog, RowDialog
from baseskiff.ui.query_plan import QueryPlanDialog
from baseskiff.ui.sql_structure import SqlStructureDialog
from baseskiff.ui.theme import STYLE


@pytest.mark.parametrize('language', ['zh', 'en'])
@pytest.mark.parametrize('screen_size', [(1024, 600), (1440, 900)])
def test_dialog_actions_fit_available_logical_screen(qtbot, monkeypatch, language, screen_size):
    width, height = screen_size
    screen = SimpleNamespace(availableGeometry=lambda: QRect(0, 0, width, height))
    monkeypatch.setattr(QDialog, 'screen', lambda self: screen)
    columns = [ColumnInfo('descriptive_column_name_' + str(i), 'TEXT') for i in range(30)]
    dialogs = [ConnectionDialog(kind='PostgreSQL', language=language),
               RowDialog(columns, language=language), RedisKeyDialog(language=language),
               CsvOptionsDialog(language=language),
               CsvImportDialog([c.name for c in columns], [], columns, language=language),
               SqlStructureDialog('SQLite', 'long_descriptive_table_name', columns, [], language=language),
               QueryPlanDialog(QueryResult(), language=language)]
    for dialog in dialogs:
        qtbot.addWidget(dialog)
        dialog.setStyleSheet(STYLE)
        dialog.show()
        QApplication.processEvents()
        assert dialog.width() <= width - 48, type(dialog).__name__
        assert dialog.height() <= height - 80, type(dialog).__name__
        for button in dialog.findChildren(QPushButton):
            if button.isVisible() and not any(isinstance(p, QScrollArea) for p in _parents(button)):
                rect = QRect(button.mapTo(dialog, button.rect().topLeft()), button.size())
                assert dialog.rect().contains(rect), (type(dialog).__name__, button.text())
        dialog.close()


def _parents(widget):
    while widget.parentWidget():
        widget = widget.parentWidget()
        yield widget


@pytest.mark.parametrize('language', ['zh', 'en'])
def test_csv_cancel_precedes_primary_action(qtbot, language):
    dialog = CsvOptionsDialog(language=language)
    qtbot.addWidget(dialog)
    dialog.show()
    QApplication.processEvents()
    cancel = dialog.buttons.button(QDialogButtonBox.StandardButton.Cancel)
    accept = dialog.buttons.button(QDialogButtonBox.StandardButton.Ok)
    assert cancel.x() < accept.x()
    assert accept.objectName() == 'primaryButton'


def test_redis_many_value_rows_remain_scrollable(qtbot):
    dialog = RedisKeyDialog(language='en')
    qtbot.addWidget(dialog)
    dialog.setStyleSheet(STYLE)
    dialog.type_combo.setCurrentIndex(dialog.type_combo.findData('hash'))
    for _ in range(30):
        dialog._add_row('hash')
    dialog.show()
    QApplication.processEvents()
    scroll = dialog.findChild(QScrollArea)
    assert scroll.verticalScrollBar().maximum() > 0
    scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
    assert dialog.save_button.isVisible()
    assert dialog.save_button.y() + dialog.save_button.height() <= dialog.height()
