from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCharFormat, QTextCursor, QTextFormat

from baseskiff.ui.editor import QueryEditor


def test_gutter_grows_with_line_digits_and_font_size(qtbot):
    editor = QueryEditor()
    qtbot.addWidget(editor)
    editor.show()
    editor.setPlainText('\n'.join('select 1;' for _ in range(9)))
    narrow = editor.line_number_area_width()
    editor.appendPlainText('select 2;')
    wider = editor.line_number_area_width()
    assert wider > narrow
    assert editor.viewportMargins().left() == wider
    assert editor.line_number_area.width() == wider
    editor.set_font_size(20)
    assert editor.font().pointSize() == 20
    assert editor.line_number_area_width() > wider
    assert editor.viewportMargins().left() == editor.line_number_area_width()


def test_gutter_tracks_scroll_resize_and_keeps_keyboard_focus(qtbot):
    editor = QueryEditor()
    qtbot.addWidget(editor)
    editor.resize(420, 160)
    editor.setPlainText('\n'.join(f'SELECT {i};' for i in range(200)))
    editor.show()
    editor.setFocus()
    editor.verticalScrollBar().setValue(100)
    assert editor.firstVisibleBlock().blockNumber() > 0
    assert editor.line_number_area.geometry().bottom() == editor.contentsRect().bottom()
    assert editor.line_number_area.focusPolicy() == Qt.FocusPolicy.NoFocus
    editor.resize(500, 220)
    qtbot.waitUntil(lambda: editor.line_number_area.height() == editor.contentsRect().height())
    editor.line_number_area.grab()  # Paint visible, scrolled line numbers.
    qtbot.keyClick(editor, Qt.Key.Key_End, Qt.KeyboardModifier.ControlModifier)
    assert editor.textCursor().atEnd()


def test_current_line_follows_cursor_without_removing_error_marker(qtbot):
    editor = QueryEditor()
    qtbot.addWidget(editor)
    editor.setPlainText('SELECT 1;\nBAD')
    editor.show_error_location(line=2, column=2)
    editor.moveCursor(QTextCursor.MoveOperation.End)
    highlights = [selection for selection in editor.extraSelections()
                  if selection.format.boolProperty(QTextFormat.Property.FullWidthSelection)]
    errors = [selection for selection in editor.extraSelections()
              if selection.format.underlineStyle() == QTextCharFormat.UnderlineStyle.WaveUnderline]
    assert len(highlights) == len(errors) == 1
    assert highlights[0].cursor.blockNumber() == 1
    assert errors[0].cursor.selectedText() == 'A'
    editor.clear_error_location()
    assert len(editor.extraSelections()) == 1
    assert editor.extraSelections()[0].format.boolProperty(QTextFormat.Property.FullWidthSelection)
