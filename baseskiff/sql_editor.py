"""Bounded, local SQL editing helpers; these never execute database work.

This lexer is deliberately conservative: formatting only changes keyword case
and clause whitespace, leaving quoted tokens, comments and operators verbatim.
"""
import re
from dataclasses import dataclass

KEYWORDS = frozenset('SELECT FROM WHERE AND OR NOT NULL IS IN LIKE LIMIT OFFSET ORDER BY ASC DESC GROUP HAVING JOIN LEFT RIGHT INNER OUTER CROSS ON AS INSERT INTO VALUES UPDATE SET DELETE CREATE TABLE DROP ALTER DISTINCT WITH UNION ALL RETURNING TRUE FALSE PRAGMA EXPLAIN ANALYZE CASE WHEN THEN ELSE END EXISTS BETWEEN FETCH FIRST ROWS ONLY'.split())
_CLAUSES = frozenset('FROM WHERE HAVING LIMIT OFFSET RETURNING UNION'.split())


@dataclass(frozen=True)
class Token:
    kind: str
    text: str
    start: int
    end: int


def tokens(sql):
    """Lex SQL, including nested comments and SQL doubled-quote escapes."""
    result = []
    i = 0
    while i < len(sql):
        start = i
        char = sql[i]
        kind = 'symbol'
        if char.isspace():
            kind = 'space'
            while i < len(sql) and sql[i].isspace():
                i += 1
        elif sql.startswith('--', i) or char == '#':
            kind = 'comment'
            end = sql.find('\n', i)
            i = len(sql) if end < 0 else end
        elif sql.startswith('/*', i):
            kind = 'comment'
            i += 2
            depth = 1
            while i < len(sql) and depth:
                if sql.startswith('/*', i):
                    depth += 1
                    i += 2
                elif sql.startswith('*/', i):
                    depth -= 1
                    i += 2
                else:
                    i += 1
        elif char in "'\"`[":
            kind = 'string' if char == "'" else 'identifier'
            close = ']' if char == '[' else char
            i += 1
            while i < len(sql):
                if sql[i] == close:
                    i += 1
                    if i < len(sql) and sql[i] == close:
                        i += 1
                    else:
                        break
                elif sql[i] == '\\' and i + 1 < len(sql):
                    i += 2
                else:
                    i += 1
        elif char == '$' and (match := re.match(r'\$(?:[A-Za-z_][A-Za-z_0-9]*)?\$', sql[i:])):
            kind = 'string'
            delimiter = match.group()
            end = sql.find(delimiter, i + len(delimiter))
            i = len(sql) if end < 0 else end + len(delimiter)
        elif char.isalnum() or char == '_':
            kind = 'word'
            i += 1
            while i < len(sql) and (sql[i].isalnum() or sql[i] in '_$'):
                i += 1
        else:
            i += 1
        result.append(Token(kind, sql[start:i], start, i))
    return result


def current_statement(sql, position):
    """Return the semicolon-delimited statement containing the caret."""
    position = max(0, min(position, len(sql)))
    start = 0
    for token in tokens(sql):
        if token.kind == 'symbol' and token.text == ';':
            if token.end >= position and sql[start:token.end].strip():
                return sql[start:token.end].strip()
            start = token.end
    return sql[start:].strip()


def format_sql(sql):
    """Format clause boundaries without rewriting SQL expressions or comments."""
    parts = tokens(sql)
    output = ''
    meaningful = [i for i, token in enumerate(parts) if token.kind != 'space']
    neighbors = {index: (meaningful[n - 1] if n else None,
                          meaningful[n + 1] if n + 1 < len(meaningful) else None)
                 for n, index in enumerate(meaningful)}
    for index, token in enumerate(parts):
        value = token.text
        if token.kind == 'word' and value.upper() in KEYWORDS:
            before, after = neighbors[index]
            qualified = ((before is not None and parts[before].text == '.') or
                         (after is not None and parts[after].text == '.'))
            if not qualified:
                value = value.upper()
                if value in _CLAUSES and output.strip():
                    # A newline ending a line comment must never be removed.
                    output = output.rstrip(' \t')
                    if not output.endswith('\n'):
                        output += '\n'
        output += value
    return output


def completion_context(sql, position):
    """Return unquoted code with protected spans masked, or None inside one."""
    masked = list(sql)
    for token in tokens(sql):
        if token.kind in ('string', 'comment', 'identifier'):
            if token.start < position < token.end:
                return None
            if position == token.end:
                closed = (token.kind == 'identifier' and token.text[-1:] in ('"', '`', ']')) or (
                    token.kind == 'string' and (token.text.endswith("'") and len(token.text) > 1 or
                                               token.text.startswith('$') and token.text.count('$') >= 4))
                if token.kind == 'comment' and token.text.startswith('/*') and token.text.endswith('*/'):
                    closed = True
                if not closed:
                    return None
            masked[token.start:token.end] = ' ' * (token.end - token.start)
    return ''.join(masked)
