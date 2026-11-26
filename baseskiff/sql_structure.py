"""Structured, local SQL DDL drafts. No database operations happen here."""
from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass

from baseskiff.models import ColumnInfo, IndexInfo

_TYPES = re.compile(r'^[A-Za-z][A-Za-z0-9_]*(?:\s+(?:PRECISION|VARYING|WITH|WITHOUT|TIME|ZONE|UNSIGNED|DOUBLE|INT|INTEGER))*\s*(?:\(\s*\d+\s*(?:,\s*\d+\s*)?\))?(?:\[\])*$', re.I)


@dataclass
class ConstraintInfo:
    name: str
    kind: str
    columns: tuple[str, ...] = ()
    reference_table: str = ''
    reference_columns: tuple[str, ...] = ()
    definition: str = ''
    on_update: str = ''
    on_delete: str = ''


class StructureDraft:
    def __init__(self, kind, table, columns, indexes, constraints=()):
        if kind not in {'SQLite', 'PostgreSQL', 'MySQL', 'Oracle'}:
            raise ValueError('SQL tables only.')
        self.kind, self.table = kind, table
        self.columns = deepcopy(list(columns))
        self.indexes = deepcopy(list(indexes))
        self.constraints = deepcopy(list(constraints))
        self.statements: list[str] = []

    def quote(self, name):
        if not isinstance(name, str) or not name.strip() or '\x00' in name:
            raise ValueError('Enter a nonempty name without NUL characters.')
        mark = '`' if self.kind == 'MySQL' else '"'
        return mark + name.replace(mark, mark * 2) + mark

    @property
    def table_sql(self):
        if self.kind in {'SQLite', 'MySQL'}:
            return self.quote(self.table)
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
        if changed_type and self.kind == 'MySQL' and not column.metadata_complete:
            raise ValueError('Changing this MySQL column requires its full definition (default/extra attributes). Use the SQL Console.')
        mysql_definition = self._mysql_definition(column, data_type) if changed_type and self.kind == 'MySQL' else ''
        sql = []
        if original != name:
            sql.append(f'ALTER TABLE {self.table_sql} RENAME COLUMN {self.quote(original)} TO {self.quote(name)}')
        if changed_type:
            if self.kind == 'PostgreSQL':
                suffix = f'ALTER COLUMN {self.quote(name)} TYPE {data_type}'
            elif self.kind == 'MySQL':
                suffix = f'MODIFY COLUMN {self.quote(name)} {mysql_definition}'
            else:
                suffix = f'MODIFY ({self.quote(name)} {data_type})'
            sql.append(f'ALTER TABLE {self.table_sql} {suffix}')
        self.statements.extend(sql)
        column.name, column.data_type = name, data_type
        for index in self.indexes:
            index.columns = tuple(name if c == original else c for c in index.columns)
        for constraint in self.constraints:
            constraint.columns = tuple(name if c == original else c for c in constraint.columns)

    def _mysql_definition(self, column, data_type):
        extra = column.extra or ''
        # Unknown attributes and expression defaults cannot be reconstructed
        # reliably across server versions and SQL modes.
        if column.generation_expression or not re.fullmatch(r'(?:auto_increment)?', extra, re.I):
            raise ValueError('This MySQL generated/expression/extra definition cannot be reconstructed safely. Use Console.')
        result = data_type
        for label, value in (('CHARACTER SET', column.charset), ('COLLATE', column.collation)):
            if value:
                if not re.fullmatch(r'[A-Za-z0-9_]+', value):
                    raise ValueError('Unsupported MySQL character metadata.')
                result += f' {label} {value}'
        result += ' NULL' if column.nullable else ' NOT NULL'
        if column.default is not None:
            result += self._default(column.default, 'literal')
        elif column.nullable and not extra:
            result += ' DEFAULT NULL'
        if extra:
            result += ' ' + extra
        if column.comment:
            result += ' COMMENT' + self._default(column.comment, 'literal').removeprefix(' DEFAULT')
        return result

    def drop_column(self, name):
        column = self._column(name)
        if column.primary_key or len(self.columns) <= 1:
            raise ValueError('Primary keys and the last column cannot be removed here.')
        if any(name in index.columns for index in self.indexes):
            raise ValueError('Remove dependent indexes before removing this column.')
        if any(name in constraint.columns for constraint in self.constraints):
            raise ValueError('Remove dependent constraints before removing this column.')
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
        if '.' in self.table and self.kind == 'Oracle':
            schema, table = self.table.rsplit('.', 1)
            index_sql = '.'.join(self.quote(p) for p in schema.split('.')) + '.' + index_sql
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
        if '.' in self.table and self.kind in {'PostgreSQL', 'Oracle'}:
            index_sql = '.'.join(self.quote(p) for p in self.table.rsplit('.', 1)[0].split('.')) + '.' + index_sql
        suffix = f' ON {self.table_sql}' if self.kind == 'MySQL' else ''
        self.statements.append(f'DROP INDEX {index_sql}{suffix}')
        self.indexes.remove(index)

    def create_constraint(self, name, kind, columns=(), reference_table='', reference_columns=(), on_update='', on_delete='', definition=''):
        if self.kind == 'SQLite':
            raise ValueError('SQLite constraint changes require a table rebuild. Use Console.')
        self._new_name(name, [c.name for c in self.constraints])
        kind = kind.upper()
        if kind not in {'FOREIGN KEY', 'UNIQUE', 'PRIMARY KEY', 'CHECK'}:
            raise ValueError('Choose FOREIGN KEY, UNIQUE, PRIMARY KEY or CHECK.')
        columns = tuple(columns)
        if (not columns and kind != 'CHECK') or len(set(columns)) != len(columns):
            raise ValueError('Choose distinct columns.')
        for column in columns:
            self._column(column)
        if kind == 'PRIMARY KEY' and (any(c.primary_key for c in self.columns) or any(c.kind == kind for c in self.constraints)):
            raise ValueError('A primary key already exists.')
        expression = ', '.join(self.quote(c) for c in columns)
        if kind == 'CHECK':
            from .sql_editor import tokens
            depth = 0
            lexed = tokens(definition)
            if not definition.strip() or any(t.kind == 'comment' or (t.kind == 'symbol' and t.text == ';') for t in lexed):
                raise ValueError('Enter one CHECK expression without comments or semicolons.')
            for token in lexed:
                if token.kind == 'symbol' and token.text in {'(', ')'}:
                    depth += 1 if token.text == '(' else -1
                    if depth < 0:
                        raise ValueError('Unbalanced CHECK expression.')
            if depth:
                raise ValueError('Unbalanced CHECK expression.')
            expression = definition
        sql = f'ALTER TABLE {self.table_sql} ADD CONSTRAINT {self.quote(name)} {kind} ({expression})'
        reference_columns = tuple(reference_columns)
        if kind == 'FOREIGN KEY':
            if not reference_table or len(reference_columns) != len(columns):
                raise ValueError('Choose a referenced table and matching referenced columns.')
            reference_sql = (self.quote(reference_table) if self.kind in {'SQLite', 'MySQL'} else
                             '.'.join(self.quote(p) for p in reference_table.split('.')))
            sql += ' REFERENCES ' + reference_sql
            sql += ' (' + ', '.join(self.quote(c) for c in reference_columns) + ')'
            for clause, action in (('UPDATE', on_update), ('DELETE', on_delete)):
                if action:
                    action = action.upper()
                    if action not in {'NO ACTION', 'RESTRICT', 'CASCADE', 'SET NULL', 'SET DEFAULT'}:
                        raise ValueError('Unsupported referential action.')
                    if self.kind == 'Oracle' and (clause == 'UPDATE' or action not in {'CASCADE', 'SET NULL'}):
                        raise ValueError('Oracle supports only ON DELETE CASCADE / SET NULL.')
                    if self.kind == 'MySQL' and action == 'SET DEFAULT':
                        raise ValueError('MySQL does not support SET DEFAULT referential actions.')
                    sql += f' ON {clause} {action}'
        self.statements.append(sql)
        self.constraints.append(ConstraintInfo(name, kind, columns, reference_table, reference_columns, definition, on_update, on_delete))
        if kind == 'PRIMARY KEY':
            for column in self.columns:
                column.primary_key = column.name in columns

    def drop_constraint(self, name):
        constraint = next((c for c in self.constraints if c.name == name), None)
        if constraint is None:
            raise ValueError('Choose an existing constraint.')
        if self.kind == 'SQLite':
            raise ValueError('SQLite constraint changes require a table rebuild. Use Console.')
        if self.kind == 'MySQL':
            suffix = ('DROP FOREIGN KEY ' + self.quote(name) if constraint.kind == 'FOREIGN KEY' else
                      'DROP PRIMARY KEY' if constraint.kind == 'PRIMARY KEY' else 'DROP INDEX ' + self.quote(name) if constraint.kind == 'UNIQUE' else 'DROP CHECK ' + self.quote(name))
        else:
            suffix = 'DROP CONSTRAINT ' + self.quote(name)
        self.statements.append(f'ALTER TABLE {self.table_sql} {suffix}')
        self.constraints.remove(constraint)
        if constraint.kind == 'PRIMARY KEY':
            for column in self.columns:
                column.primary_key = False
        if constraint.kind in {'PRIMARY KEY', 'UNIQUE'}:
            self.indexes = [index for index in self.indexes if not
                (self.index_protected(index) and index.name == constraint.name and index.columns == constraint.columns)]
