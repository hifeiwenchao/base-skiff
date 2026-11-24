from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor


def test_statement_ignores_semicolons_in_quoted_values_and_comments():
    from baseskiff.sql_editor import current_statement
    text = "select ';', $$a;b$$, \"x;y\" /* ; /* nested */ */;\n-- ;\nselect 2;"
    assert current_statement(text, text.index('select 2') + 4) == '-- ;\nselect 2;'
    assert current_statement(text, 4) == text[:text.index(';\n') + 1]


def test_formatter_preserves_literals_identifiers_and_comment_newlines():
    from baseskiff.sql_editor import format_sql
    source = "select 'select  X; from', \"where\" from customers -- preserve   words\nwhere name='a  b' and id > 1;"
    formatted = format_sql(source)
    assert "'select  X; from'" in formatted
    assert '"where"' in formatted
    assert '-- preserve   words\n' in formatted
    assert '\nFROM customers' in formatted
    assert '\nWHERE name' in formatted


def test_cached_alias_completions_and_suppression(qtbot):
    from baseskiff.ui.editor import QueryEditor
    editor = QueryEditor()
    qtbot.addWidget(editor)
    editor.set_metadata(['customers'], {'customers': ['id', 'name']})
    editor.setPlainText('SELECT c.na FROM customers AS c')
    cursor = editor.textCursor()
    cursor.setPosition(len('SELECT c.na'))
    editor.setTextCursor(cursor)
    assert editor.completion_candidates() == ['name']
    editor.setPlainText("SELECT 'cust")
    editor.moveCursor(QTextCursor.MoveOperation.End)
    assert editor.completion_candidates() == []
    editor.setPlainText('SELECT 1 -- cust')
    editor.moveCursor(QTextCursor.MoveOperation.End)
    assert editor.completion_candidates() == []


def test_completion_inserts_suffix_and_selection_formats_in_one_undo(qtbot):
    from baseskiff.ui.editor import QueryEditor
    editor = QueryEditor()
    qtbot.addWidget(editor)
    editor.setPlainText('SELECT c.na FROM customers c')
    cursor = editor.textCursor()
    cursor.setPosition(11)
    editor.setTextCursor(cursor)
    editor.insert_completion('name')
    assert editor.toPlainText() == 'SELECT c.name FROM customers c'
    editor.setPlainText("select 1; select 'a  b' from customers")
    cursor = editor.textCursor()
    cursor.setPosition(10)
    cursor.movePosition(QTextCursor.MoveOperation.End, QTextCursor.MoveMode.KeepAnchor)
    editor.setTextCursor(cursor)
    editor.format_sql()
    assert editor.toPlainText().startswith('select 1; SELECT')
    editor.undo()
    assert editor.toPlainText() == "select 1; select 'a  b' from customers"


def test_shift_enter_signal_and_sql_disable(qtbot):
    from baseskiff.ui.editor import QueryEditor
    editor = QueryEditor()
    qtbot.addWidget(editor)
    with qtbot.waitSignal(editor.execute_requested):
        qtbot.keyClick(editor, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
    editor.setPlainText('sel')
    editor.moveCursor(QTextCursor.MoveOperation.End)
    editor.set_sql_enabled(False)
    assert editor.completion_candidates() == []


def test_statement_and_completion_use_qt_unicode_cursor_positions(qtbot):
    from baseskiff.ui.editor import QueryEditor
    editor = QueryEditor()
    qtbot.addWidget(editor)
    editor.set_metadata(['customers'], {})
    editor.setPlainText("SELECT '😀'; cust")
    editor.moveCursor(QTextCursor.MoveOperation.End)
    assert editor.current_statement() == 'cust'
    assert editor.completion_candidates() == ['customers']


def test_shift_enter_executes_even_when_completion_popup_is_open(qtbot):
    from baseskiff.ui.editor import QueryEditor
    editor = QueryEditor()
    qtbot.addWidget(editor)
    editor.show()
    editor.setPlainText('sel')
    editor.moveCursor(QTextCursor.MoveOperation.End)
    editor.show_completions()
    assert editor.completer.popup().isVisible()
    with qtbot.waitSignal(editor.execute_requested):
        qtbot.keyClick(editor, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)


def test_comments_and_quoted_identifiers_suppress_completion(qtbot):
    from baseskiff.ui.editor import QueryEditor
    editor = QueryEditor()
    qtbot.addWidget(editor)
    for text in ('SELECT /* cust', 'SELECT "cust', 'SELECT `cust', 'SELECT [cust', 'SELECT $tag$cust'):
        editor.setPlainText(text)
        editor.moveCursor(QTextCursor.MoveOperation.End)
        assert editor.completion_candidates() == [], text


def test_completion_at_unicode_cursor_inside_document(qtbot):
    from baseskiff.ui.editor import QueryEditor
    editor = QueryEditor()
    qtbot.addWidget(editor)
    editor.set_metadata(['customers'], {})
    editor.setPlainText("SELECT '😀', cust FROM other")
    cursor = editor.textCursor()
    cursor.setPosition(len("SELECT '😀', cust") + 1)  # Qt counts UTF-16 units.
    editor.setTextCursor(cursor)
    assert editor.completion_candidates() == ['customers']


def test_format_selection_preserves_comment_terminating_newline(qtbot):
    from baseskiff.ui.editor import QueryEditor
    editor = QueryEditor()
    qtbot.addWidget(editor)
    editor.setPlainText('select 1 -- keep\nselect 2')
    cursor = editor.textCursor()
    cursor.setPosition(len('select 1 -- keep\n'), QTextCursor.MoveMode.KeepAnchor)
    editor.setTextCursor(cursor)
    editor.format_sql()
    assert editor.toPlainText() == 'SELECT 1 -- keep\nselect 2'


def test_formatter_does_not_rewrite_partial_literal_selection(qtbot):
    from baseskiff.ui.editor import QueryEditor
    editor = QueryEditor()
    qtbot.addWidget(editor)
    editor.setPlainText("SELECT 'select from' FROM customers")
    cursor = editor.textCursor()
    cursor.setPosition(8)
    cursor.setPosition(19, QTextCursor.MoveMode.KeepAnchor)
    editor.setTextCursor(cursor)
    editor.format_sql()
    assert editor.toPlainText() == "SELECT 'select from' FROM customers"
