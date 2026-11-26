"""Bounded, local SQL editing helpers; these never execute database work.

This lexer is deliberately conservative: formatting only changes keyword case
and clause whitespace, leaving quoted tokens, comments and operators verbatim.
"""

import re
from dataclasses import dataclass

KEYWORDS = frozenset(
    "SELECT FROM WHERE AND OR NOT NULL IS IN LIKE LIMIT OFFSET ORDER BY ASC DESC GROUP HAVING JOIN LEFT RIGHT INNER OUTER CROSS ON AS INSERT INTO VALUES UPDATE SET DELETE CREATE TABLE DROP ALTER DISTINCT WITH UNION ALL RETURNING TRUE FALSE PRAGMA EXPLAIN ANALYZE CASE WHEN THEN ELSE END EXISTS BETWEEN FETCH FIRST ROWS ONLY".split()
)
_CLAUSES = frozenset("FROM WHERE HAVING LIMIT OFFSET RETURNING UNION".split())


@dataclass(frozen=True)
class Token:
    kind: str
    text: str
    start: int
    end: int
    closed: bool = True


def tokens(sql):
    """Lex SQL, including nested comments and SQL doubled-quote escapes."""
    result = []
    i = 0
    while i < len(sql):
        start = i
        char = sql[i]
        kind = "symbol"
        closed = True
        if char.isspace():
            kind = "space"
            while i < len(sql) and sql[i].isspace():
                i += 1
        elif sql.startswith("--", i) or char == "#":
            kind = "comment"
            end = sql.find("\n", i)
            i = len(sql) if end < 0 else end
        elif sql.startswith("/*", i):
            kind = "comment"
            i += 2
            depth = 1
            while i < len(sql) and depth:
                if sql.startswith("/*", i):
                    depth += 1
                    i += 2
                elif sql.startswith("*/", i):
                    depth -= 1
                    i += 2
                else:
                    i += 1
            closed = depth == 0
        elif char.lower() == "q" and sql[i + 1 : i + 2] == "'" and i + 2 < len(sql):
            kind = "string"
            opening = sql[i + 2]
            closing = {"[": "]", "{": "}", "(": ")", "<": ">"}.get(opening, opening)
            end = sql.find(closing + "'", i + 3)
            closed = end >= 0
            i = len(sql) if end < 0 else end + 2
        elif char in "'\"`[":
            kind = "string" if char == "'" else "identifier"
            close = "]" if char == "[" else char
            i += 1
            closed = False
            while i < len(sql):
                if sql[i] == close:
                    i += 1
                    if i < len(sql) and sql[i] == close:
                        i += 1
                    else:
                        closed = True
                        break
                elif sql[i] == "\\" and i + 1 < len(sql):
                    i += 2
                else:
                    i += 1
        elif char == "$" and (match := re.match(r"\$(?:[A-Za-z_][A-Za-z_0-9]*)?\$", sql[i:])):
            kind = "string"
            delimiter = match.group()
            end = sql.find(delimiter, i + len(delimiter))
            closed = end >= 0
            i = len(sql) if end < 0 else end + len(delimiter)
        elif char.isalnum() or char == "_":
            kind = "word"
            i += 1
            while i < len(sql) and (sql[i].isalnum() or sql[i] in "_$"):
                i += 1
        else:
            i += 1
        result.append(Token(kind, sql[start:i], start, i, closed))
    return result


def current_statement_span(sql, position):
    """Return exact Python-character bounds of the trimmed current statement.

    A caret immediately after a semicolon belongs to the preceding statement,
    matching current_statement's historical execution behavior. Protected
    semicolons in strings, quoted identifiers and comments aren't delimiters.
    """
    position = max(0, min(position, len(sql)))
    start, end = 0, len(sql)
    for token in tokens(sql):
        if token.kind == "symbol" and token.text == ";":
            if token.end >= position and sql[start:token.end].strip():
                end = token.end
                break
            start = token.end
    while start < end and sql[start].isspace():
        start += 1
    while end > start and sql[end - 1].isspace():
        end -= 1
    return start, end


def current_statement(sql, position):
    """Return the semicolon-delimited statement containing the caret."""
    start, end = current_statement_span(sql, position)
    return sql[start:end]


def format_sql(sql):
    """Format clause boundaries without rewriting SQL expressions or comments."""
    parts = tokens(sql)
    output = ""
    meaningful = [i for i, token in enumerate(parts) if token.kind != "space"]
    neighbors = {
        index: (meaningful[n - 1] if n else None, meaningful[n + 1] if n + 1 < len(meaningful) else None)
        for n, index in enumerate(meaningful)
    }
    for index, token in enumerate(parts):
        value = token.text
        if token.kind == "word" and value.upper() in KEYWORDS:
            before, after = neighbors[index]
            qualified = (before is not None and parts[before].text == ".") or (
                after is not None and parts[after].text == "."
            )
            if not qualified:
                value = value.upper()
                if value in _CLAUSES and output.strip():
                    # A newline ending a line comment must never be removed.
                    output = output.rstrip(" \t")
                    if not output.endswith("\n"):
                        output += "\n"
        output += value
    return output


def completion_context(sql, position):
    """Return unquoted code with protected spans masked, or None inside one."""
    masked = list(sql)
    for token in tokens(sql):
        if token.kind in ("string", "comment", "identifier"):
            if token.start < position < token.end:
                return None
            if position == token.end and (
                not token.closed or token.kind == "comment" and not token.text.startswith("/*")
            ):
                return None
            masked[token.start : token.end] = " " * (token.end - token.start)
    return "".join(masked)


def identifier_name(token):
    """Unescape a lexer identifier without changing its spelling or case."""
    text = token.text
    if token.kind == "identifier":
        closing = "]" if text[0] == "[" else text[0]
        return text[1:-1].replace(closing * 2, closing)
    return text


def completion_scope(sql, position, columns):
    """Resolve visible relation columns and missing metadata locally.

    Parenthesized query blocks have separate namespaces. Derived tables and
    CTEs expose their projected names; ordinary expressions aren't SQL parsed.
    This intentionally returns no invented columns for unknown expressions.
    """
    parts = [t for t in tokens(sql) if t.kind not in ("space", "comment", "string")]
    # Restrict every lookup (including CTEs) to the active statement.
    start = max((i + 1 for i, t in enumerate(parts) if t.text == ";" and t.end < position), default=0)
    end = next(
        (i for i in range(start, len(parts)) if parts[i].text == ";" and parts[i].start >= position),
        len(parts),
    )
    parts = parts[start:end]
    pairs, stack = {}, []
    for i, token in enumerate(parts):
        if token.text == "(":
            stack.append(i)
        elif token.text == ")" and stack:
            opening = stack.pop()
            pairs[opening] = i
    for opening in stack:
        pairs[opening] = len(parts)
    folded = {name.casefold(): tuple(values) for name, values in columns.items()}
    missing = set()
    stop = KEYWORDS | {"USING", "NATURAL", "FULL", "RECURSIVE", "WINDOW", "QUALIFY", "EXCEPT", "INTERSECT"}

    def name_at(i, limit):
        names = []
        if i >= limit or parts[i].kind not in ("word", "identifier"):
            return "", i
        names.append(identifier_name(parts[i]))
        i += 1
        while i + 1 < limit and parts[i].text == "." and parts[i + 1].kind in ("word", "identifier"):
            names.append(identifier_name(parts[i + 1]))
            i += 2
        return ".".join(names), i

    def metadata(name, ctes):
        key = name.casefold()
        if key in ctes:
            return ctes[key]
        if key in folded:
            return folded[key]
        # Qualified source may have been cached under the unqualified table.
        if "." in name and name.rsplit(".", 1)[-1].casefold() in folded:
            return folded[name.rsplit(".", 1)[-1].casefold()]
        missing.add(name)
        return ()

    def block(lo, hi, inherited, depth=0):
        if depth > 32:
            return {}, (), {}
        ctes = dict(inherited)
        body = lo
        if lo < hi and parts[lo].text.upper() == "WITH":
            i = lo + 1
            if i < hi and parts[i].text.upper() == "RECURSIVE":
                i += 1
            while i < hi:
                name, i = name_at(i, hi)
                declared = []
                if i in pairs:
                    close = pairs[i]
                    declared = [
                        identifier_name(t) for t in parts[i + 1 : close] if t.kind in ("word", "identifier")
                    ]
                    i = close + 1
                if not name or i >= hi or parts[i].text.upper() != "AS":
                    break
                i += 1
                if i not in pairs:
                    break
                close = pairs[i]
                inner_relations, projection, inner_ctes = block(i + 1, close, ctes, depth + 1)
                finish = parts[close].start if close < len(parts) else len(sql)
                if parts[i].end <= position <= finish:
                    return inner_relations, projection, inner_ctes
                ctes[name.casefold()] = tuple(declared or projection)
                i = close + 1
                if i < hi and parts[i].text == ",":
                    i += 1
                    continue
                body = i
                break
        flat = []
        i = body
        while i < hi:
            flat.append(i)
            i = pairs[i] + 1 if i in pairs else i + 1
        # UNION/INTERSECT/EXCEPT siblings cannot see each other's aliases.
        boundaries = [i for i in flat if parts[i].text.upper() in ("UNION", "INTERSECT", "EXCEPT")]
        if boundaries:
            active_lo, active_hi = body, hi
            for boundary in boundaries:
                if parts[boundary].start < position:
                    active_lo = boundary + 1
                    if active_lo < hi and parts[active_lo].text.upper() in ("ALL", "DISTINCT"):
                        active_lo += 1
                else:
                    active_hi = boundary
                    break
            return block(active_lo, active_hi, ctes, depth + 1)
        relations = {}
        derived_openings = set()
        in_from = False
        expect = False
        for i in flat:
            word = parts[i].text.upper()
            if word in ("FROM", "JOIN", "UPDATE", "INTO"):
                in_from = True
                expect = True
                continue
            if word in ("WHERE", "GROUP", "ORDER", "HAVING", "LIMIT", "UNION", "RETURNING", "SET"):
                in_from = False
                expect = False
            if word in ("ON", "USING"):
                expect = False
            if parts[i].text == "," and in_from:
                expect = True
                continue
            if not expect:
                continue
            expect = False
            if i in pairs:
                derived_openings.add(i)
                close = pairs[i]
                _, values, _ = block(i + 1, close, ctes, depth + 1)
                after = close + 1
                name = ""
            else:
                name, after = name_at(i, hi)
                if not name:
                    continue
                values = metadata(name, ctes)
                relations[name.casefold()] = values
                relations[name.rsplit(".", 1)[-1].casefold()] = values
            if after < hi and parts[after].text.upper() == "AS":
                after += 1
            if (
                after < hi
                and parts[after].kind in ("word", "identifier")
                and parts[after].text.upper() not in stop
            ):
                relations[identifier_name(parts[after]).casefold()] = values
        projection = []
        select = next((n for n, i in enumerate(flat) if parts[i].text.upper() == "SELECT"), None)
        if select is not None:
            groups, group = [], []
            for i in flat[select + 1 :]:
                if parts[i].text.upper() == "FROM":
                    break
                if parts[i].text == ",":
                    groups.append(group)
                    group = []
                else:
                    group.append(i)
            groups.append(group)
            for group in groups:
                if not group:
                    continue
                last = parts[group[-1]]
                if last.text == "*":
                    if len(group) >= 3 and parts[group[-2]].text == ".":
                        projection.extend(relations.get(identifier_name(parts[group[-3]]).casefold(), ()))
                    else:
                        projection.extend(c for values in relations.values() for c in values)
                elif last.kind in ("word", "identifier") and (
                    len(group) == 1
                    or parts[group[-2]].text.upper() == "AS"
                    or parts[group[-2]].text == "."
                    or last.start > parts[group[-2]].end
                ):
                    projection.append(identifier_name(last))
        # Recurse only into the query block containing the caret. Its aliases
        # shadow outer aliases, while correlated outer names remain visible.
        for opening in flat:
            if opening not in pairs:
                continue
            close = pairs[opening]
            finish = parts[close].start if close < len(parts) else len(sql)
            if parts[opening].end <= position <= finish and any(
                t.text.upper() in ("SELECT", "WITH") for t in parts[opening + 1 : close]
            ):
                inner, _, inner_ctes = block(opening + 1, close, ctes, depth + 1)
                if opening in derived_openings:
                    return inner, tuple(dict.fromkeys(projection)), inner_ctes
                relations.update(inner)
                ctes.update(inner_ctes)
                break
        return relations, tuple(dict.fromkeys(projection)), ctes

    relations, _, ctes = block(0, len(parts), {})
    return relations, missing, tuple(ctes)


def completion_fragment(sql, position):
    """Read identifier chains, retaining quoted qualifiers for SQL completion."""
    parts = [t for t in tokens(sql[:position]) if t.kind not in ("space", "comment")]
    if not parts or parts[-1].end != position:
        return "", ""
    index = len(parts) - 1
    prefix = ""
    if parts[index].kind == "word":
        prefix = parts[index].text
        index -= 1
    names = []
    while index >= 1 and parts[index].text == "." and parts[index - 1].kind in ("word", "identifier"):
        names.insert(0, identifier_name(parts[index - 1]))
        index -= 2
    return ".".join(names), prefix
