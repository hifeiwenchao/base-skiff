import re

from PySide6.QtCore import QRegularExpression, QStringListModel, Qt, Signal
from PySide6.QtGui import QColor, QFont, QSyntaxHighlighter, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import QCompleter, QPlainTextEdit

from baseskiff.sql_editor import KEYWORDS, completion_context, current_statement, format_sql, tokens


class QueryHighlighter(QSyntaxHighlighter):
    def __init__(self, document):
        super().__init__(document)
        self.rules = []
        for expression, color, bold in [
            (r'\b(SELECT|FROM|WHERE|AND|OR|NOT|NULL|IS|IN|LIKE|LIMIT|OFFSET|ORDER|BY|ASC|DESC|GROUP|HAVING|JOIN|LEFT|RIGHT|INNER|ON|AS|INSERT|INTO|VALUES|UPDATE|SET|DELETE|CREATE|TABLE|DROP|ALTER|DISTINCT|WITH|UNION|ALL|RETURNING|TRUE|FALSE|PRAGMA)\b', '#477eaa', True),
            (r'\b(COUNT|SUM|AVG|MAX|MIN|COALESCE|CAST|ROUND|LENGTH|UPPER|LOWER)\b', '#a6804d', False),
            (r"'(?:[^']|'')*'", '#60885b', False),
            (r'"(?:[^"\\]|\\.)*"', '#60885b', False),
            (r'\b\d+(?:\.\d+)?\b', '#ac7d50', False),
            (r'--[^\n]*', '#9aa38f', False),
        ]:
            form = QTextCharFormat()
            form.setForeground(QColor(color))
            if bold:
                form.setFontWeight(QFont.Weight.DemiBold)
            regex = QRegularExpression(expression, QRegularExpression.PatternOption.CaseInsensitiveOption)
            self.rules.append((regex, form))

    def highlightBlock(self, text):
        for regex, form in self.rules:
            iterator = regex.globalMatch(text)
            while iterator.hasNext():
                match = iterator.next()
                self.setFormat(match.capturedStart(), match.capturedLength(), form)


# Completion uses only a metadata snapshot supplied by the window. In particular,
# neither key handling nor popup filtering opens a connection.


class QueryEditor(QPlainTextEdit):
    execute_requested = Signal()
    MAX_COMPLETIONS = 100
    MAX_COMPLETION_TEXT = 100_000

    def __init__(self, parent=None):
        super().__init__(parent)
        self._sql_enabled = True
        self._tables = ()
        self._columns = {}
        self._completion_model = QStringListModel(self)
        self.completer = QCompleter(self._completion_model, self)
        self.completer.setWidget(self)
        self.completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self.completer.activated[str].connect(self.insert_completion)

    def set_sql_enabled(self, enabled):
        self._sql_enabled = bool(enabled)
        if not enabled:
            self.completer.popup().hide()

    def set_metadata(self, tables, columns_by_table=None):
        self._tables = tuple(str(table) for table in tables)
        self._columns = {str(table): tuple(str(column) for column in columns)
                         for table, columns in (columns_by_table or {}).items()}
        self.completer.popup().hide()

    def _context(self):
        text = self.toPlainText()
        if not self._sql_enabled or len(text) > self.MAX_COMPLETION_TEXT:
            return None, ''
        position = len(text.encode('utf-16-le')[:self.textCursor().position() * 2].decode('utf-16-le', errors='ignore'))
        code = completion_context(text, position)
        match = re.search(r'([\w$]*)(?:\.([\w$]*))?$', code[:position]) if code is not None else None
        return code, match.group(0) if match else ''

    def completion_candidates(self):
        code, fragment = self._context()
        if code is None:
            return []
        prefix = fragment.rsplit('.', 1)[-1]
        if '.' in fragment:
            qualifier = fragment.rsplit('.', 1)[0]
            table = qualifier
            aliases = re.finditer(r'\b(?:FROM|JOIN|UPDATE|INTO)\s+([\w$]+)(?:\s+(?:AS\s+)?([\w$]+))?', code, re.I)
            for match in aliases:
                if match.group(2) and match.group(2).casefold() == qualifier.casefold():
                    table = match.group(1)
            choices = next((columns for name, columns in self._columns.items()
                            if name.casefold() == table.casefold()), ())
        else:
            choices = list(KEYWORDS) + list(self._tables)
            choices += [column for columns in self._columns.values() for column in columns]
        return sorted({value for value in choices if value.casefold().startswith(prefix.casefold())},
                      key=str.casefold)[:self.MAX_COMPLETIONS]

    def show_completions(self, automatic=False):
        code, fragment = self._context()
        prefix = fragment.rsplit('.', 1)[-1]
        choices = self.completion_candidates() if code is not None else []
        if automatic and len(prefix) < 2 and not fragment.endswith('.'):
            choices = []
        if not choices:
            self.completer.popup().hide()
            return
        self._completion_model.setStringList(choices)
        self.completer.setCompletionPrefix(prefix)
        self.completer.popup().setCurrentIndex(self.completer.completionModel().index(0, 0))
        self.completer.complete(self.cursorRect())

    def insert_completion(self, completion):
        code, fragment = self._context()
        if code is None:
            return
        prefix = fragment.rsplit('.', 1)[-1]
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.Left, QTextCursor.MoveMode.KeepAnchor, len(prefix))
        cursor.insertText(completion)
        self.setTextCursor(cursor)
        self.completer.popup().hide()

    def current_statement(self):
        cursor = self.textCursor()
        return cursor.selectedText().replace('\u2029', '\n') if cursor.hasSelection() else current_statement(self.toPlainText(), len(self.toPlainText().encode('utf-16-le')[:cursor.position() * 2].decode('utf-16-le', errors='ignore')))

    def format_sql(self):
        if not self._sql_enabled:
            return
        cursor = self.textCursor()
        if cursor.hasSelection():
            text = self.toPlainText()
            start = len(text.encode('utf-16-le')[:cursor.selectionStart() * 2].decode('utf-16-le', errors='ignore'))
            end = len(text.encode('utf-16-le')[:cursor.selectionEnd() * 2].decode('utf-16-le', errors='ignore'))
            if any(token.kind in ('string', 'comment', 'identifier') and (
                    token.start < start < token.end or token.start < end < token.end)
                   for token in tokens(text)):
                return
        else:
            cursor.select(QTextCursor.SelectionType.Document)
        source = cursor.selectedText().replace('\u2029', '\n')
        cursor.beginEditBlock()
        cursor.insertText(format_sql(source))
        cursor.endEditBlock()
        self.setTextCursor(cursor)

    def keyPressEvent(self, event):
        if self.completer.popup().isVisible() and event.key() in (
                Qt.Key.Key_Enter, Qt.Key.Key_Return, Qt.Key.Key_Escape, Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
            event.ignore()
            return
        if (event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
                and event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
            event.accept()
            self.execute_requested.emit()
            return
        if event.key() == Qt.Key.Key_Space and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.show_completions()
            event.accept()
            return
        super().keyPressEvent(event)
        if event.text() and not event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier):
            self.show_completions(automatic=True)
        else:
            self.completer.popup().hide()
