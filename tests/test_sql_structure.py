import sqlite3

import pytest

from baseskiff.models import ColumnInfo, IndexInfo
from baseskiff.sql_structure import StructureDraft


def test_staged_sqlite_changes_execute_only_after_apply():
    db = sqlite3.connect(':memory:')
    db.execute('CREATE TABLE "order" (id INTEGER PRIMARY KEY, title TEXT)')
    draft = StructureDraft('SQLite', 'order', [ColumnInfo('id', 'INTEGER', True), ColumnInfo('title')], [])
    draft.add_column('new"field', 'VARCHAR(80)', nullable=False, default="O'Reilly")
    draft.alter_column('title', 'label', 'TEXT')
    draft.create_index('idx', ['label'])
    assert len(db.execute('PRAGMA table_info("order")').fetchall()) == 2
    for sql in draft.statements:
        db.execute(sql)
    names = [row[1] for row in db.execute('PRAGMA table_info("order")')]
    assert names == ['id', 'label', 'new"field']
    assert db.execute('PRAGMA index_list("order")').fetchone()[1] == 'idx'


@pytest.mark.parametrize('kind, expected', [
    ('PostgreSQL', 'ALTER TABLE "public"."items" ALTER COLUMN "value" TYPE VARCHAR(60)'),
    ('Oracle', 'ALTER TABLE "public"."items" MODIFY ("value" VARCHAR(60))'),
])
def test_type_change_dialect(kind, expected):
    draft = StructureDraft(kind, 'public.items', [ColumnInfo('value', 'TEXT')], [])
    draft.alter_column('value', 'value', 'VARCHAR(60)')
    assert draft.statements == [expected]


def test_sqlite_type_change_and_constraint_index_are_rejected():
    draft = StructureDraft('SQLite', 'items', [ColumnInfo('id', 'INTEGER', True)], [IndexInfo('pk', ('id',), True, 'pk')])
    with pytest.raises(ValueError, match='SQLite'):
        draft.alter_column('id', 'id', 'TEXT')
    with pytest.raises(ValueError):
        draft.drop_index('pk')
    with pytest.raises(ValueError):
        draft.drop_column('id')
    assert draft.statements == []


@pytest.mark.parametrize('data_type', ['TEXT; DROP TABLE x', 'TEXT DEFAULT 1', 'TEXT, evil INTEGER', 'VARCHAR(10) --', 'TEXT CHECK(1)'])
def test_types_reject_sql_fragments(data_type):
    draft = StructureDraft('SQLite', 'items', [], [])
    with pytest.raises(ValueError):
        draft.add_column('x', data_type)
    assert not draft.statements


def test_defaults_are_literals_and_index_drop_is_schema_qualified():
    draft = StructureDraft('PostgreSQL', 'tenant.items', [], [IndexInfo('index', ('a',))])
    draft.add_column('x', 'TEXT', default="'; DROP TABLE items; --")
    assert "DEFAULT '''; DROP TABLE items; --'" in draft.statements[0]
    draft.drop_index('index')
    assert draft.statements[-1] == 'DROP INDEX "tenant"."index"'


def test_postgresql_create_index_uses_table_schema_not_index_qualification():
    draft = StructureDraft('PostgreSQL', 'tenant.items', [ColumnInfo('a')], [])
    draft.create_index('ix', ['a'])
    assert draft.statements == ['CREATE INDEX "ix" ON "tenant"."items" ("a")']


def test_oracle_default_precedes_nullability():
    draft = StructureDraft('Oracle', 'ITEMS', [], [])
    draft.add_column('VALUE', 'VARCHAR2(80)', False, 'example')
    assert draft.statements == ['ALTER TABLE "ITEMS" ADD ("VALUE" VARCHAR2(80) DEFAULT \'example\' NOT NULL)']


def test_postgresql_backslash_default_uses_explicit_escape_literal():
    draft = StructureDraft('PostgreSQL', 'items', [], [])
    draft.add_column('value', 'TEXT', default="\\'; DROP TABLE items; --")
    assert "DEFAULT E'\\\\''; DROP TABLE items; --'" in draft.statements[0]


def test_mysql_type_changes_need_full_server_definition():
    # ColumnInfo omits auto_increment, generated expressions, collation and comments.
    draft = StructureDraft('MySQL', 'items', [ColumnInfo('value', 'TEXT')], [])
    with pytest.raises(ValueError, match='full definition'):
        draft.alter_column('value', 'renamed', 'VARCHAR(80)')
    assert draft.statements == []
    assert draft.columns[0].name == 'value'


def test_renaming_preserves_catalog_type_without_parsing_it():
    draft = StructureDraft('PostgreSQL', 'items', [ColumnInfo('state', 'USER-DEFINED')], [])
    draft.alter_column('state', 'status', 'USER-DEFINED')
    assert draft.statements == ['ALTER TABLE "items" RENAME COLUMN "state" TO "status"']
