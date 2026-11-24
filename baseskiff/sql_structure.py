"""Structured, local SQL DDL drafts. No database operations happen here."""
from __future__ import annotations

import re
from copy import deepcopy

from baseskiff.models import ColumnInfo, IndexInfo

_TYPES = re.compile(r'^[A-Za-z][A-Za-z0-9_]*(?:\s+(?:PRECISION|VARYING|WITH|WITHOUT|TIME|ZONE|UNSIGNED|DOUBLE|INT|INTEGER))*\s*(?:\(\s*\d+\s*(?:,\s*\d+\s*)?\))?(?:\[\])*$', re.I)


class StructureDraft:
    def __init__(self, kind, table, columns, indexes):
        if kind not in {'SQLite', 'PostgreSQL', 'MySQL', 'Oracle'}:
            raise ValueError('SQL tables only.')
        self.kind, self.table = kind, table
        self.columns = deepcopy(list(columns))
        self.indexes = deepcopy(list(indexes))
        self.statements: list[str] = []

    def quote(self, name):
        if not isinstance(name, str) or not name.strip() or '\x00' in name:
            raise ValueError('Enter a nonempty name without NUL characters.')
        mark = '`' if self.kind == 'MySQL' else '"'
        return mark + name.replace(mark, mark * 2) + mark

    @property
    def table_sql(self):
        return '.'.join(self.quote(part) for part in self.table.split('.'))

    @staticmethod
    def validated_type(value):
        value = value.strip()
        if not _TYPES.fullmatch(value):
            raise ValueError('Use a type name with optional numeric size/precision; SQL clauses are not allowed.')
        return value

    def _column(self, name):
        column = next((c for c in self.columns if c.name == name), None)
        if column is None:
            raise ValueError('Choose an existing column.')
        return column

    def _new_name(self, name, existing):
        self.quote(name)
        if any(value.casefold() == name.casefold() for value in existing):
            raise ValueError('This name already exists.')

    def _default(self, value, default_kind):
        if value is None:
            return ''
        if default_kind == 'number':
            if not re.fullmatch(r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?', str(value)):
                raise ValueError('Enter a numeric default.')
            return ' DEFAULT ' + str(value)
        if default_kind == 'null':
            return ' DEFAULT NULL'
        if default_kind != 'literal':
            raise ValueError('Choose a literal, numeric or NULL default.')
        if '\x00' in str(value):
            raise ValueError('NUL is not supported in defaults.')
        text = str(value).replace("'", "''")
        if self.kind == 'MySQL' and '\\' in text:
            # SQL modes differ in their treatment of backslash literals.
            raise ValueError('Backslash defaults require the SQL Console because MySQL SQL modes vary.')
        if self.kind == 'PostgreSQL' and '\\' in text:
            return " DEFAULT E'" + text.replace('\\', '\\\\') + "'"
        return " DEFAULT '" + text + "'"

    def add_column(self, name, data_type, nullable=True, default=None, default_kind='literal'):
        self._new_name(name, [c.name for c in self.columns])
        data_type = self.validated_type(data_type)
        definition = self.quote(name) + ' ' + data_type + self._default(default, default_kind) + ('' if nullable else ' NOT NULL')
        if self.kind == 'Oracle':
            sql = f'ALTER TABLE {self.table_sql} ADD ({definition})'
        else:
            sql = f'ALTER TABLE {self.table_sql} ADD COLUMN {definition}'
        self.statements.append(sql)
        self.columns.append(ColumnInfo(name, data_type, False, nullable, default))

    def alter_column(self, original, name, data_type):
        column = self._column(original)
        self.quote(name)
        if original != name:
            self._new_name(name, [c.name for c in self.columns if c is not column])
        data_type = data_type.strip()
        changed_type = data_type.casefold() != column.data_type.casefold()
        if changed_type:
            data_type = self.validated_type(data_type)
        if changed_type and self.kind == 'SQLite':
            raise ValueError('SQLite cannot change a column type without rebuilding the table. Use the SQL Console.')
        if changed_type and self.kind == 'MySQL':
            raise ValueError('Changing this MySQL column requires its full definition (default/extra attributes). Use the SQL Console.')
        sql = []
        if original != name:
            sql.append(f'ALTER TABLE {self.table_sql} RENAME COLUMN {self.quote(original)} TO {self.quote(name)}')
        if changed_type:
            if self.kind == 'PostgreSQL':
                suffix = f'ALTER COLUMN {self.quote(name)} TYPE {data_type}'
            elif self.kind == 'MySQL':
                suffix = f'MODIFY COLUMN {self.quote(name)} {data_type}' + (' NULL' if column.nullable else ' NOT NULL')
            else:
                suffix = f'MODIFY ({self.quote(name)} {data_type})'
            sql.append(f'ALTER TABLE {self.table_sql} {suffix}')
        self.statements.extend(sql)
        column.name, column.data_type = name, data_type
        for index in self.indexes:
            index.columns = tuple(name if c == original else c for c in index.columns)

    def drop_column(self, name):
        column = self._column(name)
        if column.primary_key or len(self.columns) <= 1:
            raise ValueError('Primary keys and the last column cannot be removed here.')
        if any(name in index.columns for index in self.indexes):
            raise ValueError('Remove dependent indexes before removing this column.')
        self.statements.append(f'ALTER TABLE {self.table_sql} DROP COLUMN {self.quote(name)}')
        self.columns.remove(column)

    def create_index(self, name, columns, unique=False):
        self._new_name(name, [i.name for i in self.indexes])
        columns = tuple(columns)
        if not columns or len(set(columns)) != len(columns):
            raise ValueError('Choose one or more distinct columns.')
        for column in columns:
            self._column(column)
        index_sql = self.quote(name)
        table_sql = self.table_sql
        if '.' in self.table and self.kind in {'SQLite', 'Oracle'}:
            schema, table = self.table.rsplit('.', 1)
            index_sql = '.'.join(self.quote(p) for p in schema.split('.')) + '.' + index_sql
            if self.kind == 'SQLite':
                table_sql = self.quote(table)
        self.statements.append(f'CREATE {"UNIQUE " if unique else ""}INDEX {index_sql} ON {table_sql} ({", ".join(self.quote(c) for c in columns)})')
        self.indexes.append(IndexInfo(name, columns, unique))

    def index_protected(self, index):
        return (index.origin.lower() in {'pk', 'u', 'p', 'primary', 'unique', 'constraint'}
                or index.name.upper() == 'PRIMARY'
                or index.name.startswith('sqlite_autoindex_'))

    def drop_index(self, name):
        index = next((i for i in self.indexes if i.name == name), None)
        if index is None or self.index_protected(index):
            raise ValueError('Constraint indexes must be managed through their constraints in the SQL Console.')
        index_sql = self.quote(name)
        if '.' in self.table and self.kind != 'MySQL':
            index_sql = '.'.join(self.quote(p) for p in self.table.rsplit('.', 1)[0].split('.')) + '.' + index_sql
        suffix = f' ON {self.table_sql}' if self.kind == 'MySQL' else ''
        self.statements.append(f'DROP INDEX {index_sql}{suffix}')
        self.indexes.remove(index)
