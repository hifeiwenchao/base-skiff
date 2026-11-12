"""Database type selection through native pointer and keyboard interaction."""
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog

from baseskiff.models import DATABASE_TYPES
from baseskiff.ui.connection_type import ConnectionTypeDialog


@pytest.mark.parametrize('kind', DATABASE_TYPES)
def test_type_choice_accepts_exact_database_kind(qtbot, kind):
    dialog = ConnectionTypeDialog(language='en')
    qtbot.addWidget(dialog)
    dialog.show()
    assert dialog.selected_kind is None
    assert tuple(dialog.kind_buttons) == DATABASE_TYPES

    qtbot.mouseClick(dialog.kind_buttons[kind], Qt.MouseButton.LeftButton)

    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.selected_kind == kind
    assert not dialog.isVisible()


@pytest.mark.parametrize('cancel_method', ['button', 'escape'])
def test_cancel_leaves_no_database_type_selected(qtbot, cancel_method):
    dialog = ConnectionTypeDialog(language='en')
    qtbot.addWidget(dialog)
    dialog.show()

    if cancel_method == 'button':
        qtbot.mouseClick(dialog.cancel_button, Qt.MouseButton.LeftButton)
    else:
        qtbot.keyClick(dialog, Qt.Key.Key_Escape)

    assert dialog.result() == QDialog.DialogCode.Rejected
    assert dialog.selected_kind is None
    assert not dialog.isVisible()


@pytest.mark.parametrize('language,title,cancel', [
    ('zh', '选择数据库类型', '取消'),
    ('en', 'Choose database type', 'Cancel'),
])
def test_picker_translates_controls_and_has_named_choices(qtbot, language, title, cancel):
    dialog = ConnectionTypeDialog(language=language)
    qtbot.addWidget(dialog)
    dialog.show()

    assert dialog.windowTitle() == title
    assert dialog.cancel_button.text() == cancel
    for kind, button in dialog.kind_buttons.items():
        assert button.accessibleName() == kind
        assert button.accessibleDescription()
        assert not button.icon().isNull()


def test_tab_then_enter_selects_focused_database_type(qtbot):
    dialog = ConnectionTypeDialog(language='en')
    qtbot.addWidget(dialog)
    dialog.show()
    first, second = [dialog.kind_buttons[kind] for kind in DATABASE_TYPES[:2]]
    first.setFocus()
    qtbot.waitUntil(first.hasFocus)

    qtbot.keyClick(first, Qt.Key.Key_Tab)
    assert second.hasFocus()
    qtbot.keyClick(second, Qt.Key.Key_Return)

    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.selected_kind == DATABASE_TYPES[1]


def test_enter_on_focused_cancel_rejects_without_selecting_a_database(qtbot):
    dialog = ConnectionTypeDialog(language='en')
    qtbot.addWidget(dialog)
    dialog.show()
    database_button = dialog.kind_buttons['MySQL']
    database_button.setFocus()
    qtbot.waitUntil(database_button.hasFocus)
    dialog.cancel_button.setFocus()
    qtbot.waitUntil(dialog.cancel_button.hasFocus)

    qtbot.keyClick(dialog.cancel_button, Qt.Key.Key_Return)

    assert dialog.result() == QDialog.DialogCode.Rejected
    assert dialog.selected_kind is None
    assert not dialog.isVisible()
