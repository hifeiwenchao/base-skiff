"""Driver-provided SQL error coordinates; do not guess from message text."""
from __future__ import annotations


def structured_sql_error(error, query=''):
    result = {'message': str(error), 'position': None, 'line': None, 'column': None}
    diagnostic = getattr(error, 'diag', None)
    position = getattr(diagnostic, 'statement_position', None) or getattr(error, 'statement_position', None)
    detail = error.args[0] if getattr(error, 'args', ()) else error
    # python-oracledb supplies a one-based character offset on its error object.
    if position is None:
        position = getattr(detail, 'offset', None)
    try:
        position = int(position)
    except (ValueError, TypeError):
        position = 0
    if position > 0 and position <= len(query) + 1:
        result['position'] = position
        prefix = query[:position - 1]
        result['line'] = prefix.count('\n') + 1
        result['column'] = len(prefix.rsplit('\n', 1)[-1]) + 1
    else:
        for name in ('line', 'column'):
            value = getattr(detail, name, None) or getattr(error, name, None)
            if isinstance(value, int) and value > 0:
                result[name] = value
    return result
