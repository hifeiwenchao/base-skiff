"""Real SQL regression checks; only uniquely named disposable objects are changed."""
from __future__ import annotations

import csv
import re
import time
import uuid
from dataclasses import replace
from threading import Timer
from types import SimpleNamespace

import pytest

from baseskiff.adapters import SQLAdapter
from baseskiff.cancellation import CancellationToken, QueryCancelled
from baseskiff.data_transfer import CsvOptions
from baseskiff.models import Connection
from baseskiff.sql_structure import StructureDraft

pytestmark = pytest.mark.integration


@pytest.fixture(params=['mysql', 'postgresql', 'oracle'])
def remaining_sql(request, live_config, live_service):
    key = request.param
    config = live_config.get(key)
    if not config:
        pytest.skip(f'Add {key} to the private live config')
    kind = {'mysql':'MySQL', 'postgresql':'PostgreSQL', 'oracle':'Oracle'}[key]
    connection = Connection(name=f'Remaining live {kind}', kind=kind,
        host=config.get('host', live_config.get('host', '127.0.0.1')),
        port=config.get('port', {'mysql':3306,'postgresql':5432,'oracle':1521}[key]),
        database=config.get('database', 'FREEPDB1' if key=='oracle' else ''),
        username=('root' if key=='mysql' and config.get('root_password') else config['username']),
        password=(config['root_password'] if key=='mysql' and config.get('root_password') else config['password']))
    prefix = 'bs_rem_' + uuid.uuid4().hex[:12]
    admin = SQLAdapter(connection)
    if key == 'mysql':
        admin.execute(f'CREATE DATABASE `{prefix}` CHARACTER SET utf8mb4')
        connection = replace(connection,database=prefix)
    elif key == 'postgresql':
        admin.execute(f'CREATE SCHEMA "{prefix}"')
    adapter = SQLAdapter(connection)
    saved = live_service.save_connection(connection)
    tables=[]
    def create(suffix, definition):
        name = prefix + '_' + suffix
        table = prefix+'.'+name if key=='postgresql' else name
        adapter.execute(f'CREATE TABLE {adapter.table_sql(table)} {definition}')
        tables.append(table)
        return table
    state=SimpleNamespace(adapter=adapter, service=live_service, id=saved.id, connection=connection, create=create)
    try:
        yield state
    finally:
        if key=='mysql':
            admin.execute(f'DROP DATABASE `{prefix}`')
        elif key=='postgresql':
            admin.execute(f'DROP SCHEMA "{prefix}" CASCADE')
        else:
            for table in reversed(tables):
                adapter.execute(f'DROP TABLE {adapter.table_sql(table)} PURGE')


def column_definitions(adapter):
    q=adapter.quote
    return f'({q("a")} INTEGER NOT NULL, {q("b")} INTEGER NOT NULL, {q("label")} VARCHAR(80), PRIMARY KEY ({q("a")},{q("b")}))'


def insert_rows(adapter, table, rows):
    with adapter.session() as db:
        for row in rows:
            adapter._query(db, f'INSERT INTO {adapter.table_sql(table)} VALUES ({", ".join(adapter.binds(len(row)))})',row)


def test_live_compound_cursor_null_sort_ties_and_fallback(remaining_sql):
    state=remaining_sql
    adapter=state.adapter
    table=state.create('cursor',column_definitions(adapter))
    insert_rows(adapter,table,[(1,1,None),(1,2,None),(2,1,'a'),(2,2,'a'),(3,1,'b'),(3,2,'b'),(4,1,None)])
    q=adapter.quote
    for options in ({}, {'sort_column':'label'}, {'sort_column':'label','descending':True},
                    {'order_by':f'{q("label")} DESC, {q("a")} ASC'}):
        expected=adapter.fetch_rows(table,page_size=50,count_total=False,**options).rows
        rows=[]
        cursor=None
        for page in range(1,10):
            result=adapter.fetch_rows(table,page=page,page_size=2,count_total=False,after_key=cursor,**options)
            rows.extend(result.rows)
            if not result.has_more:
                break
            assert result.next_cursor is not None
            cursor=result.next_cursor
        assert rows==expected
    result=adapter.fetch_rows(table,page_size=2,count_total=False,order_by=f'LOWER({q("label")})')
    assert result.next_cursor is None


def test_live_csv_stream_export_import_and_import_rollback(remaining_sql,tmp_path):
    state=remaining_sql
    adapter=state.adapter
    source=state.create('source',column_definitions(adapter))
    target=state.create('target',column_definitions(adapter))
    rows=[(i//3,i%3,None if i%7==0 else f'字段 {i}, quoted " text') for i in range(41)]
    insert_rows(adapter,source,rows)
    destination=tmp_path/'stream.csv'
    progress=[]
    # Oracle read-only snapshots can reject tables less than a clock tick old.
    # The independently observed clock condition is a fixture prerequisite.
    if adapter.kind=='Oracle':
        probe=replace(state.connection)
        # Wait for the new table to be visible to a read-only transaction.
        deadline=time.monotonic()+5
        while True:
            try:
                reader=SQLAdapter(replace(probe,read_only=True))
                reader.execute(f'SELECT COUNT(*) FROM {reader.table_sql(source)}')
                break
            except Exception as error:
                if getattr(error.args[0],'code',None)!=1466 or time.monotonic()>deadline:
                    raise
                time.sleep(.1)
    result=state.service.export_csv(state.id,destination,table=source,batch_size=7,progress=progress.append,
        null_value='\\N',delimiter=';')
    assert result.affected==41 and progress[-1]==41 and len(progress)>2
    with destination.open(encoding='utf-8-sig',newline='') as stream:
        exported=list(csv.reader(stream,delimiter=';'))
    assert len(exported)==42 and exported[0]==['a','b','label']
    imported=state.service.import_csv(state.id,destination,table=target,mapping={0:'a',1:'b',2:'label'},options=CsvOptions(delimiter=';'))
    assert imported.affected==41
    assert adapter.fetch_rows(source,page_size=100).rows==adapter.fetch_rows(target,page_size=100).rows
    bad=tmp_path/'invalid.csv'
    bad.write_text('a,b,label\n99,1,new\nnot-integer,2,invalid\n')
    with pytest.raises(ValueError,match='CSV line 3'):
        state.service.import_csv(state.id,bad,table=target,mapping={0:'a',1:'b',2:'label'})
    assert adapter.fetch_rows(target,page_size=100,count_total='exact').total==41


def test_live_cancellation_interrupts_driver(remaining_sql):
    adapter=remaining_sql.adapter
    query={'PostgreSQL':'SELECT pg_sleep(12)', 'MySQL':'SELECT SLEEP(12)',
           'Oracle':'SELECT SUM(a.n*b.n*c.n) FROM (SELECT LEVEL n FROM DUAL CONNECT BY LEVEL <= 1000) a CROSS JOIN (SELECT LEVEL n FROM DUAL CONNECT BY LEVEL <= 1000) b CROSS JOIN (SELECT LEVEL n FROM DUAL CONNECT BY LEVEL <= 1000) c'}[adapter.kind]
    token=CancellationToken()
    timer=Timer(.5,token.cancel)
    start=time.monotonic()
    timer.start()
    try:
        with pytest.raises(QueryCancelled):
            remaining_sql.service.execute(remaining_sql.id,query,cancel_token=token)
    finally:
        timer.cancel()
        timer.join(timeout=3)
    assert time.monotonic()-start<8
    assert adapter.execute('SELECT 1 FROM DUAL' if adapter.kind=='Oracle' else 'SELECT 1').rows


def test_live_foreign_key_catalog_and_ddl_preview(remaining_sql):
    adapter=remaining_sql.adapter
    parent=remaining_sql.create('parent',column_definitions(adapter))
    child=remaining_sql.create('child',column_definitions(adapter))
    draft=StructureDraft(adapter.kind,child,adapter.get_schema(child),adapter.list_indexes(child),adapter.list_constraints(child))
    constraint='fk_' + uuid.uuid4().hex[:12]
    draft.create_constraint(constraint,'FOREIGN KEY',['a','b'],parent,['a','b'],on_delete='CASCADE')
    for sql in draft.statements:
        adapter.execute(sql)
    foreign=next(c for c in adapter.list_constraints(child) if c.name==constraint)
    assert foreign.columns==('a','b') and foreign.reference_columns==('a','b')
    assert foreign.reference_table.split('.')[-1].strip('"')==parent.split('.')[-1]
    draft=StructureDraft(adapter.kind,child,adapter.get_schema(child),adapter.list_indexes(child),adapter.list_constraints(child))
    draft.drop_constraint(constraint)
    adapter.execute(draft.statements[0])
    assert constraint not in {c.name for c in adapter.list_constraints(child)}


def test_live_mysql_type_change_preserves_full_attributes(remaining_sql):
    adapter=remaining_sql.adapter
    if adapter.kind!='MySQL':
        pytest.skip('MySQL catalog attributes')
    table=remaining_sql.create('attrs',"(id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'identity', label VARCHAR(40) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL DEFAULT 'ready' COMMENT 'owner''s note', derived INT GENERATED ALWAYS AS (CHAR_LENGTH(label)) VIRTUAL)")
    before={c.name:c for c in adapter.get_schema(table)}
    draft=StructureDraft('MySQL',table,list(before.values()),adapter.list_indexes(table))
    draft.alter_column('id','id','BIGINT')
    draft.alter_column('label','label','VARCHAR(90)')
    for sql in draft.statements:
        adapter.execute(sql)
    after={c.name:c for c in adapter.get_schema(table)}
    for name in ('id','label','derived'):
        for attribute in ('extra','charset','collation','comment','default','generation_expression','nullable'):
            assert getattr(before[name],attribute)==getattr(after[name],attribute)
    assert after['id'].data_type.lower().startswith('bigint')
    assert after['label'].data_type.lower()=='varchar(90)'
    draft=StructureDraft('MySQL',table,list(after.values()),[])
    with pytest.raises(ValueError,match='safely'):
        draft.alter_column('derived','derived','BIGINT')
    assert draft.statements==[]


def test_live_oracle_plan_rows_are_cleaned(remaining_sql,monkeypatch):
    adapter=remaining_sql.adapter
    if adapter.kind!='Oracle':
        pytest.skip('Oracle PLAN_TABLE')
    calls=[]
    original=adapter._query
    def traced(db,sql,*args,**kwargs):
        calls.append(sql)
        return original(db,sql,*args,**kwargs)
    monkeypatch.setattr(adapter,'_query',traced)
    result=adapter.explain('SELECT 42 AS answer FROM DUAL')
    assert result.rows
    statement=next(sql for sql in calls if sql.startswith('EXPLAIN PLAN'))
    statement_id=re.search("STATEMENT_ID = '([^']+)'",statement)[1]
    with adapter.session() as db:
        result=adapter._query(db,'SELECT COUNT(*) AS n FROM PLAN_TABLE WHERE STATEMENT_ID=:1',(statement_id,))
    assert next(iter(result.rows[0].values()))==0
    assert not any('ANALYZE' in sql for sql in calls)


def test_live_mysql_literal_dotted_table_metadata(remaining_sql):
    adapter=remaining_sql.adapter
    if adapter.kind!='MySQL':
        pytest.skip('MySQL literal identifier regression')
    table=remaining_sql.create(' odd.`table', '(id INT PRIMARY KEY, value VARCHAR(40) DEFAULT \'ready\' COMMENT \'retained\')')
    schema=adapter.get_schema(table)
    assert [column.name for column in schema]==['id','value']
    assert schema[1].default=='ready' and schema[1].comment=='retained'
    assert any(constraint.kind=='PRIMARY KEY' for constraint in adapter.list_constraints(table))
    draft=StructureDraft('MySQL',table,schema,adapter.list_indexes(table))
    draft.alter_column('value','value','VARCHAR(80)')
    adapter.execute(draft.statements[0])
    assert adapter.get_schema(table)[1].data_type.lower()=='varchar(80)'
    adapter.insert_row(table,{'id':1,'value':'中文'})
    assert adapter.fetch_rows(table,sort_column='value',search='中文').rows==[{'id':1,'value':'中文'}]


def test_live_mysql_enum_set_sort_uses_offset(remaining_sql):
    adapter=remaining_sql.adapter
    if adapter.kind!='MySQL':
        pytest.skip('MySQL ordinal ENUM / SET sort semantics')
    table=remaining_sql.create('ordinal', "(id INT PRIMARY KEY, choice ENUM('z','a'), flags SET('z','a'))")
    insert_rows(adapter,table,[(1,'z','z'),(2,'a','a'),(3,'z','z,a'),(4,'a','z')])
    for column in ('choice','flags'):
        for descending in (False,True):
            options=dict(sort_column=column,descending=descending,count_total=False)
            expected=adapter.fetch_rows(table,page_size=100,**options).rows
            actual=[]
            for page in range(1,5):
                result=adapter.fetch_rows(table,page=page,page_size=1,**options)
                assert result.next_cursor is None
                actual.extend(result.rows)
            assert actual==expected


def test_live_oracle_date_csv_preserves_time_of_day(remaining_sql,tmp_path):
    from datetime import datetime

    from tests.integration.test_oracle import wait_for_read_only_snapshot
    state=remaining_sql
    adapter=state.adapter
    if adapter.kind!='Oracle':
        pytest.skip('Oracle DATE includes time of day')
    table=state.create('date_csv','("id" NUMBER(10) PRIMARY KEY, "happened" DATE)')
    expected=datetime(2026,11,26,13,14,15)
    insert_rows(adapter,table,[(1,expected)])
    wait_for_read_only_snapshot(state.connection,table)
    destination=tmp_path/'oracle-date.csv'
    state.service.export_csv(state.id,destination,table=table)
    adapter.execute(f'DELETE FROM {adapter.table_sql(table)}')
    assert state.service.import_csv(state.id,destination,table=table,mapping={0:'id',1:'happened'}).affected==1
    assert adapter.fetch_rows(table).rows[0]['happened']==expected


def test_live_legacy_structure_menu_add_and_index_operations(remaining_sql):
    state=remaining_sql
    adapter=state.adapter
    suffix=' odd.`menu' if adapter.kind=='MySQL' else 'lower_menu'
    table=state.create(suffix,column_definitions(adapter))
    data_type='VARCHAR2(40)' if adapter.kind=='Oracle' else 'VARCHAR(40)'
    state.service.add_column(state.id,table,'extra',data_type)
    assert 'extra' in {column.name for column in adapter.get_schema(table)}
    index='idx_' + uuid.uuid4().hex[:12]
    state.service.create_index(state.id,table,index,['extra'])
    indexes=state.service.list_indexes(state.id,table)
    assert next(item for item in indexes if item.name==index).columns==('extra',)
    state.service.drop_index(state.id,table,index)
    assert index not in {item.name for item in state.service.list_indexes(state.id,table)}
    state.service.drop_column(state.id,table,'extra')
    assert 'extra' not in {column.name for column in adapter.get_schema(table)}
