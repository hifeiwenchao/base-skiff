import re

from PySide6.QtCore import QRegularExpression, QStringListModel, Qt, Signal
from PySide6.QtGui import QColor, QFont, QSyntaxHighlighter, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import QCompleter, QPlainTextEdit, QTextEdit

from baseskiff.sql_editor import (
    KEYWORDS,
    completion_context,
    completion_fragment,
    completion_scope,
    current_statement_span,
    format_sql,
    tokens,
)


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
    metadata_requested = Signal(str)
    MAX_COMPLETIONS = 100
    MAX_COMPLETION_TEXT = 100_000

    def __init__(self, parent=None):
        super().__init__(parent)
        self._sql_enabled = True
        self._tables = ()
        self._columns = {}
        self._metadata_requests = set()
        self.textChanged.connect(self.clear_error_location)
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

    def reset_metadata_requests(self):
        """Allow new requests after a connection switch or explicit refresh."""
        self._metadata_requests.clear()

    def set_metadata(self, tables, columns_by_table=None):
        new_tables = tuple(str(table) for table in tables)
        if new_tables != self._tables:
            self._metadata_requests.clear()
        self._tables = new_tables
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

    def _completion_details(self):
        code, _ = self._context()
        if code is None:
            return [], '', set()
        text = self.toPlainText()
        position = len(text.encode('utf-16-le')[:self.textCursor().position() * 2].decode('utf-16-le', errors='ignore'))
        qualifier, prefix = completion_fragment(text, position)
        relations, missing, ctes = completion_scope(text, position, self._columns)
        before = text[:position - len(prefix)]
        table_context = bool(re.search(r'\b(?:FROM|JOIN|UPDATE|INTO)\s+(?:[\w$]+\.)?$', before, re.I))
        if qualifier and table_context:
            choices = [name[len(qualifier) + 1:] for name in self._tables
                       if name.casefold().startswith(qualifier.casefold() + '.')]
            missing = set()
        elif qualifier:
            choices = relations.get(qualifier.casefold(), ())
            if not choices:
                choices = next((values for name, values in self._columns.items()
                                if name.casefold() == qualifier.casefold()), ()) if not relations else ()
        else:
            choices = list(KEYWORDS) + list(self._tables) + list(ctes)
            choices += [column for values in relations.values() for column in values]
        values = sorted({value for value in choices if value.casefold().startswith(prefix.casefold())},
                        key=str.casefold)[:self.MAX_COMPLETIONS]
        return values, prefix, missing

    def completion_candidates(self):
        return self._completion_details()[0]

    def show_completions(self, automatic=False):
        choices, prefix, missing = self._completion_details()
        if not automatic:
            for table in sorted(missing - self._metadata_requests):
                self._metadata_requests.add(table)
                self.metadata_requested.emit(table)
        text = self.toPlainText()
        at_dot = text.encode('utf-16-le')[:self.textCursor().position() * 2].decode('utf-16-le', errors='ignore').endswith('.')
        if automatic and len(prefix) < 2 and not at_dot:
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
        _, prefix, _ = self._completion_details()
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.Left, QTextCursor.MoveMode.KeepAnchor, len(prefix.encode('utf-16-le')) // 2)
        cursor.insertText(completion)
        self.setTextCursor(cursor)
        self.completer.popup().hide()

    def execution_range(self, current_only=False):
        """Return execution SQL and its exact Python-character editor offset.

        A selection always takes priority. Selection and whole-document text
        are returned verbatim; current-statement whitespace is trimmed by the
        lexer together with its source bounds, so duplicate SQL is unambiguous.
        """
        text = self.toPlainText()
        cursor = self.textCursor()

        def character_offset(qt_position):
            return len(text.encode('utf-16-le')[:qt_position * 2].decode('utf-16-le', errors='ignore'))

        if cursor.hasSelection():
            start = character_offset(cursor.selectionStart())
            end = character_offset(cursor.selectionEnd())
            return text[start:end], start
        if current_only:
            start, end = current_statement_span(text, character_offset(cursor.position()))
            return text[start:end], start
        return text, 0

    def current_statement(self):
        return self.execution_range(current_only=True)[0]

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

    def clear_error_location(self):
        self.setExtraSelections([])

    def show_error_location(self, position=None, line=None, column=None):
        """Underline a driver-reported 1-based character position or line/column.

        SQL syntax isn't guessed locally. Missing or invalid locations clear
        the marker. Character offsets are converted to Qt UTF-16 positions.
        """
        self.clear_error_location()
        text = self.toPlainText()
        if position is not None:
            try:
                offset = int(position) - 1
            except (ValueError, TypeError):
                return
        elif line is not None:
            try:
                row, col = int(line), int(column or 1)
            except (ValueError, TypeError):
                return
            lines = text.splitlines(keepends=True)
            if row < 1 or row > len(lines) or col < 1 or col > len(lines[row - 1].rstrip('\r\n')) + 1:
                return
            offset = sum(len(value) for value in lines[:row - 1]) + col - 1
        else:
            return
        if offset < 0 or offset > len(text) or not text:
            return
        offset = min(offset, len(text) - 1)
        cursor = QTextCursor(self.document())
        cursor.setPosition(len(text[:offset].encode('utf-16-le')) // 2)
        cursor.movePosition(QTextCursor.MoveOperation.NextCharacter, QTextCursor.MoveMode.KeepAnchor)
        selection = QTextEdit.ExtraSelection()
        selection.cursor = cursor
        selection.format.setUnderlineStyle(QTextCharFormat.UnderlineStyle.WaveUnderline)
        selection.format.setUnderlineColor(QColor('#c45656'))
        self.setExtraSelections([selection])

    def keyPressEvent(self, event):
        if (self.completer.popup().isVisible() and not event.modifiers() & Qt.KeyboardModifier.ShiftModifier
                and event.key() in (
                Qt.Key.Key_Enter, Qt.Key.Key_Return, Qt.Key.Key_Escape, Qt.Key.Key_Tab, Qt.Key.Key_Backtab)):
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
