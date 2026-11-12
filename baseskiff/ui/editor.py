from PySide6.QtCore import QRegularExpression
from PySide6.QtGui import QColor, QFont, QSyntaxHighlighter, QTextCharFormat


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
