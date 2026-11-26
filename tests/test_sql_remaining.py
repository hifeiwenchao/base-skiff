import sqlite3
from contextlib import closing

import pytest

from baseskiff.adapters import SQLAdapter
from baseskiff.models import ColumnInfo, Connection
from baseskiff.sql_structure import StructureDraft


def test_composite_and_nullable_sort_cursor(tmp_path):
    path = tmp_path / 'cursor.sqlite'
    with closing(sqlite3.connect(path)) as db, db:
        db.execute('CREATE TABLE t(a INT NOT NULL, b INT NOT NULL, label TEXT, PRIMARY KEY(a,b))')
        db.executemany('INSERT INTO t VALUES (?,?,?)', [(1,1,None),(1,2,None),(2,1,'a'),(2,2,'a'),(3,1,'b')])
    adapter = SQLAdapter(Connection(name='test',path=str(path)))
    for options in ({}, {'sort_column':'label'}, {'sort_column':'label','descending':True}, {'order_by':'label DESC, a ASC'}):
        expected = adapter.fetch_rows('t', page_size=50, **options).rows
        rows, cursor = [], None
        for page in range(1,5):
            result = adapter.fetch_rows('t', page=page,page_size=2,count_total=False,after_key=cursor,**options)
            rows.extend(result.rows)
            cursor = result.next_cursor
            if not result.has_more:
                break
            assert cursor is not None
        assert rows == expected


def test_mysql_definition_preserved():
    column = ColumnInfo('id','INT',True,False,None,extra='auto_increment',comment="owner's",metadata_complete=True)
    draft=StructureDraft('MySQL','t',[column],[])
    draft.alter_column('id','id','BIGINT')
    assert 'auto_increment' in draft.statements[0]
    assert "COMMENT 'owner''s'" in draft.statements[0]
    assert 'NOT NULL' in draft.statements[0]


def test_constraints_preview_and_safe_reference_identifiers():
    from baseskiff.sql_structure import ConstraintInfo
    draft = StructureDraft('PostgreSQL','tenant.child',[ColumnInfo('parent_id')],[])
    draft.create_constraint('fk', 'FOREIGN KEY', ['parent_id'], 'tenant.parent', ['id'], on_delete='CASCADE')
    assert draft.statements == ['ALTER TABLE "tenant"."child" ADD CONSTRAINT "fk" FOREIGN KEY ("parent_id") REFERENCES "tenant"."parent" ("id") ON DELETE CASCADE']
    with pytest.raises(ValueError, match='dependent constraints'):
        draft.columns.append(ColumnInfo('another'))
        draft.drop_column('parent_id')
    draft.drop_constraint('fk')
    assert draft.statements[-1] == 'ALTER TABLE "tenant"."child" DROP CONSTRAINT "fk"'
    sqlite = StructureDraft('SQLite','t',[ColumnInfo('id')],[], [ConstraintInfo('fk','FOREIGN KEY',('id',))])
    with pytest.raises(ValueError, match='rebuild'):
        sqlite.drop_constraint('fk')


def test_mysql_unsafe_metadata_does_not_stage_partial_rename():
    column = ColumnInfo('x','INT',extra='DEFAULT_GENERATED on update CURRENT_TIMESTAMP',metadata_complete=True)
    draft = StructureDraft('MySQL','t',[column],[])
    with pytest.raises(ValueError, match='safely'):
        draft.alter_column('x','renamed','BIGINT')
    assert draft.statements == [] and draft.columns[0].name == 'x'


def test_structured_driver_error_positions():
    from types import SimpleNamespace

    from baseskiff.sql_diagnostics import structured_sql_error
    error = Exception('syntax error')
    error.diag = SimpleNamespace(statement_position='10')
    assert structured_sql_error(error, 'SELECT 1\nFROM') == {'message':'syntax error','position':10,'line':2,'column':1}
    oracle = Exception(SimpleNamespace(offset=8))
    assert structured_sql_error(oracle,'SELECT error')['column'] == 8
    assert structured_sql_error(Exception('near x at line 50'),'SELECT 1')['line'] is None


def test_oracle_plan_is_not_analyze_and_cleans_only_own_identifier(monkeypatch):
    from contextlib import contextmanager

    from baseskiff.models import QueryResult
    adapter = SQLAdapter(Connection(name='oracle',kind='Oracle'))
    calls = []
    @contextmanager
    def session(token=None):
        yield object()
    def query(db, sql, params=(), **kw):
        calls.append((sql, params))
        return QueryResult(rows=[{'PLAN_TABLE_OUTPUT':'plan'}])
    monkeypatch.setattr(adapter,'session',session)
    monkeypatch.setattr(adapter,'_query',query)
    assert adapter.explain('SELECT 1 FROM DUAL').rows
    assert len(calls) == 3
    assert calls[0][0].startswith('EXPLAIN PLAN SET STATEMENT_ID')
    assert 'ANALYZE' not in calls[0][0]
    assert calls[1][1] == calls[2][1]
    assert calls[2][0] == 'DELETE FROM PLAN_TABLE WHERE STATEMENT_ID=:1'
    adapter.connection.read_only = True
    with pytest.raises(PermissionError, match='PLAN_TABLE'):
        adapter.explain('SELECT 1 FROM DUAL')


def test_foreign_key_catalog_sqlite(tmp_path):
    path = tmp_path/'fk.sqlite'
    with closing(sqlite3.connect(path)) as db,db:
        db.execute('CREATE TABLE parent(a INT,b INT,PRIMARY KEY(a,b))')
        db.execute('CREATE TABLE child(a INT,b INT,FOREIGN KEY(a,b) REFERENCES parent(a,b) ON DELETE CASCADE)')
    adapter = SQLAdapter(Connection(name='test',path=str(path)))
    foreign = adapter.list_constraints('child')[0]
    assert foreign.columns == ('a','b') and foreign.reference_columns == ('a','b')
    assert foreign.reference_table == 'parent' and foreign.on_delete == 'CASCADE'


def test_mysql_catalog_attributes_and_literal_dotted_name(monkeypatch):
    from contextlib import contextmanager

    from baseskiff.models import QueryResult
    adapter = SQLAdapter(Connection(name='mysql',kind='MySQL',database='default'))
    calls=[]
    @contextmanager
    def session():
        yield object()
    def query(db,sql,params):
        calls.append((sql,params))
        return QueryResult(rows=[{'name':'title','type':'varchar(80)','key_type':'','nullable':'YES','def':'hello',
            'extra':'','charset':'utf8mb4','collation':'utf8mb4_bin','comment':'description','generation_expression':''}])
    monkeypatch.setattr(adapter,'session',session)
    monkeypatch.setattr(adapter,'_query',query)
    column=adapter.get_schema('tenant.items')[0]
    assert calls[0][1] == ('default','tenant.items')
    assert column.metadata_complete and column.charset=='utf8mb4' and column.comment=='description'
    draft=StructureDraft('MySQL','items',[column],[])
    draft.alter_column('title','title','VARCHAR(160)')
    assert "CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NULL DEFAULT 'hello' COMMENT 'description'" in draft.statements[0]


def test_constraint_dialog_stages_foreign_reference(qtbot):
    from baseskiff.ui.sql_structure import SqlStructureDialog
    dialog=SqlStructureDialog('PostgreSQL','child',[ColumnInfo('parent_id')],[],language='en')
    qtbot.addWidget(dialog)
    dialog._new_constraint()
    dialog.constraint_name_edit.setText('fk_parent')
    dialog.constraint_columns_edit.setText('parent_id')
    dialog.reference_table_edit.setText('parent')
    dialog.reference_columns_edit.setText('id')
    dialog._stage()
    assert 'REFERENCES "parent" ("id")' in dialog.sql_preview.toPlainText()
    assert dialog.draft.constraints[0].reference_table == 'parent'
    assert dialog.apply_button.isEnabled()


def test_check_constraints_reject_unbalanced_and_multistatement():
    draft=StructureDraft('PostgreSQL','t',[ColumnInfo('n')],[])
    for expression in ('n > 0); DROP TABLE t; --', 'n > 0)', '(n > 0', 'n > 0 /* x */'):
        with pytest.raises(ValueError):
            draft.create_constraint('positive','CHECK',definition=expression)
        assert not draft.statements
    draft.create_constraint('positive','CHECK',definition='n > 0 AND n < 100')
    assert draft.statements == ['ALTER TABLE "t" ADD CONSTRAINT "positive" CHECK (n > 0 AND n < 100)']


def test_primary_key_can_be_staged_for_drop_then_recreated():
    from baseskiff.models import IndexInfo
    from baseskiff.sql_structure import ConstraintInfo
    draft=StructureDraft('PostgreSQL','t',[ColumnInfo('a','INT',True),ColumnInfo('b','INT')],
        [IndexInfo('t_pkey',('a',),True,'pk')],[ConstraintInfo('t_pkey','PRIMARY KEY',('a',))])
    draft.drop_constraint('t_pkey')
    draft.create_constraint('new_pkey','PRIMARY KEY',['b'])
    assert not draft._column('a').primary_key
    assert draft._column('b').primary_key
    assert not draft.indexes


def test_mysql_cancel_wakes_buffered_socket_before_closing_session():
    import socket
    calls=[]
    class Stream:
        def shutdown(self,mode):
            assert mode==socket.SHUT_RDWR
            calls.append('shutdown')
    class Database:
        _sock=Stream()
        def close(self):
            calls.append('close')
    adapter=SQLAdapter(Connection(name='mysql',kind='MySQL'))
    adapter._active_db=Database()
    adapter.cancel()
    assert calls==['shutdown','close']


@pytest.mark.parametrize('kind,quoted', [('MySQL','`odd.``table`'),('SQLite','"odd.`table"')])
def test_structure_ddl_matches_adapter_literal_table_contract(kind,quoted):
    draft=StructureDraft(kind,'odd.`table',[ColumnInfo('value')],[])
    draft.add_column('next','TEXT')
    assert draft.statements[0].startswith(f'ALTER TABLE {quoted} ADD COLUMN')
    if kind=='MySQL':
        draft.create_constraint('fk','FOREIGN KEY',['value'],'parent.with.dot',['id'])
        assert 'REFERENCES `parent.with.dot` (`id`)' in draft.statements[-1]


@pytest.mark.parametrize('data_type', ["enum('z','a')", "set('z','a')"])
def test_mysql_cursor_rejects_ordinal_enum_and_set_order(data_type):
    adapter=SQLAdapter(Connection(name='mysql',kind='MySQL'))
    columns=[ColumnInfo('id','INT',True),ColumnInfo('choice',data_type)]
    assert adapter._cursor_order(columns,'choice') == []
    assert adapter._cursor_order(columns,order_by='`choice` DESC') == []
    assert adapter._cursor_order([ColumnInfo('choice',data_type,True)]) == []
    assert adapter._cursor_order(columns,'id') == [('id',False)]


@pytest.mark.parametrize('kind,operation,expected',[
    ('Oracle','add','ALTER TABLE "mixedCase" ADD ("extra" VARCHAR2(40))'),
    ('PostgreSQL','drop','DROP INDEX "tenant"."idx"')])
def test_legacy_structure_menu_dialect_sql(monkeypatch,kind,operation,expected):
    adapter=SQLAdapter(Connection(name='ddl',kind=kind))
    calls=[]
    monkeypatch.setattr(adapter,'_ddl',calls.append)
    if operation=='add':
        adapter.add_column('mixedCase','extra','VARCHAR2(40)')
    else:
        adapter.drop_index('tenant.items','idx')
    assert calls==[expected]


def test_oracle_index_catalog_keeps_quoted_case(monkeypatch):
    from contextlib import contextmanager

    from baseskiff.models import QueryResult
    adapter=SQLAdapter(Connection(name='oracle',kind='Oracle'))
    calls=[]
    @contextmanager
    def session():
        yield object()
    def query(db,sql,params):
        calls.append(params)
        return QueryResult()
    monkeypatch.setattr(adapter,'session',session)
    monkeypatch.setattr(adapter,'_query',query)
    assert adapter.list_indexes('quotedLower')==[]
    assert calls==[('quotedLower',)]
