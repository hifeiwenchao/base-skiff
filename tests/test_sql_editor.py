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


def _completion(editor, text):
    position = text.index('|')
    editor.setPlainText(text.replace('|', ''))
    cursor = editor.textCursor()
    cursor.setPosition(position)
    editor.setTextCursor(cursor)
    return editor.completion_candidates()


def test_scope_join_quoted_schema_and_statement_boundaries(qtbot):
    from baseskiff.ui.editor import QueryEditor
    editor = QueryEditor()
    qtbot.addWidget(editor)
    editor.set_metadata(['public.customers', 'orders'], {'public.customers': ['name'], 'orders': ['total']})
    assert _completion(editor, 'SELECT c.na| FROM "public"."customers" c LEFT JOIN orders o ON c.id=o.id') == ['name']
    assert _completion(editor, 'SELECT "c".na| FROM "public"."customers" AS "c"') == ['name']
    assert _completion(editor, 'SELECT o.to| FROM orders o, "public"."customers" c') == ['total']
    assert _completion(editor, 'SELECT 1 FROM orders o; SELECT o.to| FROM "public"."customers" c') == []
    assert _completion(editor, 'SELECT o.to| FROM "public"."customers" c WHERE EXISTS (SELECT 1 FROM orders o)') == []


def test_cte_and_derived_projection_completion(qtbot):
    from baseskiff.ui.editor import QueryEditor
    editor = QueryEditor()
    qtbot.addWidget(editor)
    editor.set_metadata(['customers'], {'customers': ['id', 'name']})
    assert _completion(editor, 'WITH people AS (SELECT name AS label, id FROM customers) SELECT p.la| FROM people p') == ['label']
    assert _completion(editor, 'SELECT d.na| FROM (SELECT name FROM customers) d') == ['name']
    assert _completion(editor, 'WITH people(label) AS (SELECT name FROM customers) SELECT p.la| FROM people p') == ['label']


def test_metadata_requests_only_explicit_once_per_missing_table(qtbot):
    from baseskiff.ui.editor import QueryEditor
    editor = QueryEditor()
    qtbot.addWidget(editor)
    editor.set_metadata(['customers'], {})
    requests = []
    editor.metadata_requested.connect(requests.append)
    _completion(editor, 'SELECT c.na| FROM customers c')
    editor.show_completions(automatic=True)
    assert requests == []
    editor.show_completions()
    editor.show_completions()
    assert requests == ['customers']
    editor.set_metadata(['customers'], {'customers': ['name']})
    assert editor.completion_candidates() == ['name']


def test_unclosed_doubled_quotes_and_comments_suppress(qtbot):
    from baseskiff.ui.editor import QueryEditor
    editor = QueryEditor()
    qtbot.addWidget(editor)
    for text in ("SELECT 'cust''", 'SELECT "cust""', 'SELECT `cust``', 'SELECT [cust]]', 'SELECT /* outer /* inner */'):
        assert _completion(editor, text + '|') == []


def test_driver_error_location_unicode_and_edit_clears(qtbot):
    from baseskiff.ui.editor import QueryEditor
    editor = QueryEditor()
    qtbot.addWidget(editor)
    editor.setPlainText("SELECT '😀';\nBAD")
    editor.show_error_location(line=2, column=2)
    selections = editor.extraSelections()
    assert len(selections) == 1
    assert selections[0].cursor.selectedText() == 'A'
    editor.insertPlainText('x')
    assert editor.extraSelections() == []


def test_cte_body_and_union_keep_aliases_in_their_query_block(qtbot):
    from baseskiff.ui.editor import QueryEditor
    editor = QueryEditor()
    qtbot.addWidget(editor)
    editor.set_metadata(['customers', 'orders'], {'customers': ['name'], 'orders': ['total']})
    assert _completion(editor, 'WITH people AS (SELECT o.to| FROM customers c) SELECT 1 FROM orders o') == []
    assert _completion(editor, 'SELECT o.to| FROM customers c UNION SELECT 1 FROM orders o') == []
    assert _completion(editor, 'SELECT c.na| FROM customers c WHERE EXISTS (SELECT c.name FROM orders o)') == ['name']


def test_oracle_alternative_quote_keeps_semicolon_and_completion_protected(qtbot):
    from baseskiff.sql_editor import current_statement
    from baseskiff.ui.editor import QueryEditor
    editor = QueryEditor()
    qtbot.addWidget(editor)
    text = "SELECT q'[it's; SELECT]' FROM dual; SELECT 2"
    assert current_statement(text, 5) == text[:text.index('; SELECT 2') + 1]
    assert _completion(editor, "SELECT q'[it's; cust|") == []


def test_schema_table_completion_and_comma_after_join(qtbot):
    from baseskiff.ui.editor import QueryEditor
    editor = QueryEditor()
    qtbot.addWidget(editor)
    editor.set_metadata(['public.customers', 'orders'], {'public.customers': ['name'], 'orders': ['total']})
    assert _completion(editor, 'SELECT * FROM public.cust|') == ['customers']
    assert _completion(editor, 'SELECT o.to| FROM "public"."customers" c JOIN "public"."customers" d ON c.id=d.id, orders o') == ['total']


def test_quoted_keyword_alias_and_metadata_retry_reset(qtbot):
    from baseskiff.ui.editor import QueryEditor
    editor = QueryEditor()
    qtbot.addWidget(editor)
    editor.set_metadata(['customers'], {'customers': ['name']})
    assert _completion(editor, 'SELECT "where".na| FROM customers AS "where"') == ['name']
    editor.reset_metadata_requests()


def test_derived_query_does_not_see_its_own_outer_alias(qtbot):
    from baseskiff.ui.editor import QueryEditor
    editor = QueryEditor()
    qtbot.addWidget(editor)
    editor.set_metadata(['orders'], {'orders': ['total']})
    assert _completion(editor, 'SELECT d.total FROM (SELECT d.to| AS total FROM orders o) d') == []


def test_current_statement_span_retains_exact_duplicate_and_trim_offsets():
    from baseskiff.sql_editor import current_statement, current_statement_span
    text = '  SELECT missing;  SELECT missing;  '
    first = current_statement_span(text, text.index(';') + 1)
    second = current_statement_span(text, text.rindex(';'))
    assert first == (2, 17)
    assert second == (19, 34)
    assert text[slice(*first)] == text[slice(*second)] == 'SELECT missing;'
    assert current_statement(text, 17) == text[slice(*first)]
    assert current_statement_span('   ', 2) == (3, 3)


def test_execution_range_uses_python_offset_for_unicode_selection_and_statement(qtbot):
    from baseskiff.ui.editor import QueryEditor
    editor = QueryEditor()
    qtbot.addWidget(editor)
    text = "SELECT '😀';  SELECT missing; SELECT missing;"
    editor.setPlainText(text)
    cursor = editor.textCursor()
    cursor.setPosition(len(text[:text.index('missing')].encode('utf-16-le')) // 2)
    editor.setTextCursor(cursor)
    assert editor.execution_range(current_only=True) == ('SELECT missing;', text.index('SELECT missing'))
    assert editor.execution_range() == (text, 0)
    start = text.rindex(' SELECT')
    cursor.setPosition(len(text[:start].encode('utf-16-le')) // 2)
    cursor.setPosition(len(text.encode('utf-16-le')) // 2, QTextCursor.MoveMode.KeepAnchor)
    editor.setTextCursor(cursor)
    assert editor.execution_range(current_only=True) == (' SELECT missing;', start)
    assert editor.execution_range() == (' SELECT missing;', start)
