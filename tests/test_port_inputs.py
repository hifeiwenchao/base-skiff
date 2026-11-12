"""Port fields accept deliberate numeric input without wheel adjustments."""
import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QAbstractSpinBox

from baseskiff.models import Connection
from baseskiff.service import DatabaseService
from baseskiff.ui.dialogs import ConnectionDialog
from baseskiff.ui.theme import STYLE


@pytest.fixture
def port_dialog(qtbot):
    connection = Connection('Redis test', kind='Redis', port=6379, ssh_enabled=True,
                            ssh_host='bastion.example', ssh_username='developer')
    dialog = ConnectionDialog(connection=connection, language='en')
    dialog.setStyleSheet(STYLE)
    qtbot.addWidget(dialog)
    dialog.show()
    return dialog


def wheel(widget, delta, pixels=False):
    point = widget.rect().center()
    window = widget.window()
    QTest.wheelEvent(window.windowHandle(), widget.mapTo(window, point),
                     QPoint() if pixels else QPoint(0, delta),
                     pixelDelta=QPoint(0, delta) if pixels else QPoint())


@pytest.mark.parametrize('field,tab', [('port_spin', 0), ('ssh_port_spin', 1)])
@pytest.mark.parametrize('focused', [True, False])
@pytest.mark.parametrize('pixels', [True, False])
@pytest.mark.parametrize('target', ['spin', 'editor'])
def test_port_wheel_never_changes_value_even_when_focused(port_dialog, qtbot,
                                                        field, tab, focused, pixels, target):
    dialog = port_dialog
    dialog.tabs.setCurrentIndex(tab)
    spin = getattr(dialog, field)
    if focused:
        spin.setFocus()
        qtbot.waitUntil(spin.hasFocus)
    else:
        dialog.cancel_button.setFocus()
        qtbot.waitUntil(dialog.cancel_button.hasFocus)
    original = spin.value()
    for delta in (120, -120):
        dialog.tabs.widget(tab).ensureWidgetVisible(spin)
        wheel(spin.lineEdit() if target == 'editor' else spin, delta, pixels)
        assert spin.value() == original


@pytest.mark.parametrize('field,tab', [('port_spin', 0), ('ssh_port_spin', 1)])
@pytest.mark.parametrize('target', ['spin', 'editor'])
def test_wheel_over_port_scrolls_the_form(port_dialog, qtbot, field, tab, target):
    dialog = port_dialog
    dialog.resize(650, 440)
    dialog.tabs.setCurrentIndex(tab)
    spin = getattr(dialog, field)
    scroll = dialog.tabs.widget(tab)
    qtbot.waitUntil(lambda: scroll.verticalScrollBar().maximum() > 0)
    scroll.ensureWidgetVisible(spin)
    original_scroll = scroll.verticalScrollBar().value()
    assert original_scroll < scroll.verticalScrollBar().maximum()
    original_port = spin.value()

    wheel(spin.lineEdit() if target == 'editor' else spin, -120)

    assert spin.value() == original_port
    assert scroll.verticalScrollBar().value() > original_scroll


@pytest.mark.parametrize('field,tab', [('port_spin', 0), ('ssh_port_spin', 1)])
def test_port_input_has_no_arrow_buttons(port_dialog, field, tab):
    port_dialog.tabs.setCurrentIndex(tab)
    spin = getattr(port_dialog, field)
    assert spin.buttonSymbols() == QAbstractSpinBox.ButtonSymbols.NoButtons


@pytest.mark.parametrize('field,tab', [('port_spin', 0), ('ssh_port_spin', 1)])
def test_port_text_has_room_to_render_with_application_theme(port_dialog, qtbot, field, tab):
    port_dialog.tabs.setCurrentIndex(tab)
    spin = getattr(port_dialog, field)
    spin.setValue(65535)
    editor = spin.lineEdit()
    qtbot.waitUntil(editor.isVisible)
    metrics = editor.fontMetrics()
    margins = editor.textMargins()
    assert editor.width() - margins.left() - margins.right() >= metrics.horizontalAdvance('65535')
    assert editor.height() - margins.top() - margins.bottom() >= metrics.height()


@pytest.mark.parametrize('field,tab,key', [('port_spin', 0, 'port'),
                                        ('ssh_port_spin', 1, 'ssh_port')])
@pytest.mark.parametrize('value', ['1', '65535'])
def test_manual_port_input_and_tab_preserve_the_value_on_disk(port_dialog, qtbot,
                                                            tmp_path, field, tab, key, value):
    dialog = port_dialog
    dialog.tabs.setCurrentIndex(tab)
    spin = getattr(dialog, field)
    spin.setFocus()
    spin.selectAll()
    qtbot.keyClicks(spin, value)
    qtbot.keyClick(spin, Qt.Key.Key_Tab)
    assert spin.hasAcceptableInput()
    assert getattr(dialog.current_connection(), key) == int(value)
    service = DatabaseService(tmp_path)
    saved = service.save_connection(dialog.current_connection())
    assert getattr(DatabaseService(tmp_path)._connection(saved.id), key) == int(value)
