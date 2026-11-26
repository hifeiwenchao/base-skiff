"""Normalize driver EXPLAIN results into a local, display-only plan forest.

No query is executed here. Unsupported formats retain their original text;
flat MySQL EXPLAIN rows deliberately remain flat because select_id is not a
parent pointer. Parser depth and node counts are bounded for imported JSON.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from .models import QueryResult


@dataclass(frozen=True)
class PlanNode:
    id: str
    parent: str | None
    label: str
    estimates: dict[str, Any] = field(default_factory=dict)


def normalize_query_plan(result: QueryResult, *, max_nodes=5000) -> list[PlanNode]:
    nodes = []
    max_nodes = max(0, min(int(max_nodes), 5000))

    def add(label, parent=None, estimates=None, identity=None):
        if len(nodes) >= max_nodes:
            return None
        identity = str(identity) if identity is not None else f'plan-{len(nodes)}'
        node = PlanNode(identity, parent, str(label), estimates or {})
        nodes.append(node)
        return identity

    def pg(value, parent=None, depth=0):
        if depth > 64 or len(nodes) >= max_nodes:
            return
        if isinstance(value, list):
            for item in value:
                pg(item, parent, depth)
        elif isinstance(value, dict):
            if 'Plan' in value:
                pg(value['Plan'], parent, depth)
            elif 'Node Type' in value:
                label = value['Node Type']
                if value.get('Relation Name'):
                    label += ' on ' + str(value['Relation Name'])
                estimates = {key: val for key, val in value.items()
                             if key != 'Plans' and not isinstance(val, (dict, list))}
                identity = add(label, parent, estimates)
                for child in value.get('Plans', ()):
                    pg(child, identity, depth + 1)

    def mysql(value, parent=None, depth=0, name='Plan'):
        if depth > 64 or len(nodes) >= max_nodes:
            return
        if isinstance(value, list):
            for child in value:
                mysql(child, parent, depth + 1, name)
        elif isinstance(value, dict):
            scalar = {key: val for key, val in value.items() if not isinstance(val, (dict, list))}
            children = [(key, val) for key, val in value.items() if isinstance(val, (dict, list))]
            label = str(value.get('table_name', name.replace('_', ' ')))
            if value.get('access_type'):
                label += ' · ' + str(value['access_type'])
            identity = add(label, parent, scalar)
            for key, child in children:
                # cost_info is metadata on its operator rather than a child.
                if key == 'cost_info' and isinstance(child, dict):
                    if identity is not None:
                        next(node for node in nodes if node.id == identity).estimates.update(child)
                    continue
                mysql(child, identity, depth + 1, key)

    rows = result.rows[:max_nodes]
    if not rows or not max_nodes:
        return []
    first = {str(key).casefold(): value for key, value in rows[0].items()}
    if {'id', 'parent', 'detail'} <= first.keys():
        identities = {str(next((value for key, value in row.items() if str(key).casefold() == 'id'), '')) for row in rows}
        for row in rows:
            row = {str(key).casefold(): value for key, value in row.items()}
            identity, parent = str(row['id']), str(row['parent'])
            add(row['detail'], parent if parent in identities and parent != identity else None, identity=identity)
        return nodes
    if {'select_type', 'table'} <= first.keys():
        for row in rows:
            row = {str(key).casefold(): value for key, value in row.items()}
            label = ' · '.join(str(row[key]) for key in ('select_type', 'table', 'type') if row.get(key) is not None)
            add(label, estimates={key: val for key, val in row.items() if val is not None})
        return nodes
    # PostgreSQL FORMAT JSON may be decoded by its driver, or returned as text.
    if len(rows) == 1 and len(rows[0]) == 1:
        value = next(iter(rows[0].values()))
        if isinstance(value, str) and value.lstrip().startswith(('{', '[')):
            try:
                value = json.loads(value)
            except (ValueError, RecursionError):
                pass
        if isinstance(value, (dict, list)):
            candidate = value[0] if isinstance(value, list) and value else value
            if isinstance(candidate, dict) and ('Plan' in candidate or 'Node Type' in candidate):
                pg(value)
            elif isinstance(candidate, dict) and 'query_block' in candidate:
                mysql(candidate['query_block'], name='query block')
            if nodes:
                return nodes
    lines = []
    for row in rows:
        lines.extend(str(next(iter(row.values()), '')).splitlines())
    # Oracle DBMS_XPLAN is an ASCII table: operation whitespace encodes depth.
    oracle_header = next((i for i, line in enumerate(lines)
                          if re.match(r'\s*\|\s*Id\s*\|\s*Operation\s*\|', line, re.I)), None)
    if oracle_header is not None:
        headers = [part.strip() for part in lines[oracle_header].strip().strip('|').split('|')]
        stack = []
        for line in lines[oracle_header + 1:]:
            cells = line.strip().strip('|').split('|')
            if len(cells) < 2 or not re.fullmatch(r'\s*\*?\s*\d+\s*', cells[0]):
                continue
            operation = cells[1]
            indent = len(operation) - len(operation.lstrip())
            while stack and stack[-1][0] >= indent:
                stack.pop()
            detail = dict(zip(headers, (cell.strip() for cell in cells)))
            label = operation.strip()
            if detail.get('Name'):
                label += ' · ' + detail['Name']
            identity = add(label, stack[-1][1] if stack else None, detail)
            stack.append((indent, identity))
        if nodes:
            return nodes
    stack = []
    for line in lines:
        if len(nodes) >= max_nodes:
            break
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        text = line.strip()
        arrow = text.startswith('->')
        if arrow:
            text = text[2:].strip()
        # PostgreSQL annotations belong to the closest less-indented operator.
        annotation = re.match(r'^([A-Za-z][A-Za-z ]+):\s*(.*)$', text)
        if annotation and nodes and not arrow:
            target = next((identity for level, identity in reversed(stack) if level < indent), nodes[0].id)
            node = next((node for node in reversed(nodes) if node.id == target), nodes[0])
            node.estimates[annotation.group(1)] = annotation.group(2)
            continue
        estimates = {}
        metrics = re.search(r'\s+\((?:cost|actual time)=.*', text)
        if metrics:
            for key, value in re.findall(r'([A-Za-z][A-Za-z ]*)=([^\s)]+)', metrics.group()):
                estimates[key.strip()] = value
            text = text[:metrics.start()].rstrip()
        while stack and stack[-1][0] >= indent:
            stack.pop()
        identity = add(text, stack[-1][1] if stack else None, estimates)
        stack.append((indent, identity))
    return nodes
