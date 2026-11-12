"""Opt-in MySQL server tests using the application's real service and driver.

Credentials are read only by the shared private-config fixture. Setup creates a
uniquely named schema; teardown removes only that schema. No grants are changed.
"""
import json
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from baseskiff.adapters import SQLAdapter
from baseskiff.models import Connection

pytestmark = pytest.mark.integration


def quote(identifier):
    return '`' + identifier.replace('`', '``') + '`'


def credentials(config, *, developer=False, database=''):
    mysql = config['mysql']
    return Connection(
        name='Live MySQL developer' if developer else 'Live MySQL disposable',
        kind='MySQL', host=config['host'], port=mysql['port'], database=database,
        username=mysql['username'] if developer else 'root',
        password=mysql['password'] if developer else mysql['root_password'],
    )


@pytest.fixture(scope='module')
def mysql_schema(live_config, run_tag):
    schema = run_tag + '_mysql'
    adapter = SQLAdapter(credentials(live_config))
    adapter.execute(f'CREATE DATABASE {quote(schema)} CHARACTER SET utf8mb4')
    try:
        yield schema
    finally:
        adapter.execute(f'DROP DATABASE {quote(schema)}')


@pytest.fixture
def mysql(live_service, live_config, mysql_schema, request):
    connection = credentials(live_config, database=mysql_schema)
    saved = live_service.save_connection(connection)
    prefix = request.node.name.removeprefix('test_')[:40]
    state = SimpleNamespace(service=live_service, id=saved.id,
                            connection=connection, prefix=prefix)
    state.execute = lambda query: live_service.execute(saved.id, query)
    state.table = lambda suffix='rows': prefix + '_' + suffix
    return state


def simple_table(mysql, name=None):
    table = name or mysql.table()
    mysql.execute(f'CREATE TABLE {quote(table)} '
                  '(id INT PRIMARY KEY, name VARCHAR(200) NOT NULL) ENGINE=InnoDB')
    return table


def test_root_and_developer_authentication(mysql, live_config, mysql_schema):
    assert mysql.service.test_connection(mysql.connection) == 'Connected to MySQL successfully.'
    version = mysql.execute('SELECT VERSION() AS version').rows[0]['version']
    assert version and version[0].isdigit()

    developer = credentials(live_config, developer=True)
    assert mysql.service.test_connection(developer) == 'Connected to MySQL successfully.'
    saved = mysql.service.save_connection(developer)
    assert mysql.service.execute(saved.id, 'SELECT 42 AS answer').rows == [{'answer': 42}]
    schemas = mysql.service.execute(saved.id, 'SHOW DATABASES').rows
    visible = {next(iter(row.values())) for row in schemas}
    # The supplied developer account has no privilege to the root-owned test
    # schema. Validate that server denial is surfaced without changing grants.
    if mysql_schema not in visible:
        import pymysql
        with pytest.raises(pymysql.err.OperationalError) as error:
            mysql.service.test_connection(replace(developer, database=mysql_schema))
        assert error.value.args[0] == 1044


def test_grid_crud_preserves_mysql_types_and_unicode(mysql):
    table = mysql.table()
    mysql.execute(f'''CREATE TABLE {quote(table)} (
        id BIGINT PRIMARY KEY AUTO_INCREMENT,
        name VARCHAR(200) NOT NULL,
        amount DECIMAL(18,4) NOT NULL,
        born DATE NOT NULL,
        happened DATETIME NOT NULL,
        details JSON NOT NULL,
        optional_value VARCHAR(100) NULL,
        binary_value VARBINARY(20)
    ) ENGINE=InnoDB''')
    expected = {'name': "中文 🛶 O'Reilly", 'amount': Decimal('123456789.1200'),
                'born': date(2024, 2, 29), 'happened': datetime(2024, 2, 29, 12, 34, 56),
                'details': {'text': '绿舟', 'nested': [1, True, None]},
                'optional_value': None, 'binary_value': b'\x00\xfftest'}
    assert mysql.service.insert_row(mysql.id, table, expected).affected == 1
    row = mysql.service.fetch_rows(mysql.id, table).rows[0]
    key = {'id': row['id']}
    for column in ('name', 'amount', 'born', 'happened', 'optional_value', 'binary_value'):
        assert row[column] == expected[column]
    assert isinstance(row['amount'], Decimal)
    assert json.loads(row['details']) == expected['details']
    changed = {'name': "更新 '; DROP TABLE ignored; --", 'amount': Decimal('-0.1250'),
               'details': {'changed': ['a', '中文']}}
    assert mysql.service.update_row(mysql.id, table, key, changed).affected == 1
    row = mysql.service.fetch_rows(mysql.id, table).rows[0]
    assert row['name'] == changed['name']
    assert row['amount'] == changed['amount']
    assert json.loads(row['details']) == changed['details']
    assert mysql.service.delete_rows(mysql.id, table, [key]).affected == 1
    assert mysql.service.fetch_rows(mysql.id, table).total == 0


def test_introspection_reports_tables_views_columns_and_composite_keys(mysql):
    table, view = mysql.table(), mysql.table('view')
    mysql.execute(f'''CREATE TABLE {quote(table)} (
        tenant_id INT NOT NULL,
        row_id INT NOT NULL,
        label VARCHAR(50) NOT NULL DEFAULT 'ready',
        optional_value VARCHAR(30) NULL,
        PRIMARY KEY(tenant_id, row_id)
    ) ENGINE=InnoDB''')
    mysql.execute(f'CREATE VIEW {quote(view)} AS SELECT * FROM {quote(table)}')
    tables = {item.name: item for item in mysql.service.list_tables(mysql.id)}
    assert tables[table].kind == 'table'
    assert tables[view].kind == 'view'
    schema = mysql.service.get_schema(mysql.id, table)
    assert [column.name for column in schema] == ['tenant_id', 'row_id', 'label', 'optional_value']
    columns = {column.name: column for column in schema}
    assert {column.name for column in schema if column.primary_key} == {'tenant_id', 'row_id'}
    assert not columns['tenant_id'].nullable
    assert not columns['label'].nullable
    assert columns['optional_value'].nullable
    assert columns['label'].default == 'ready'
    assert 'varchar' in columns['label'].data_type.lower()


def test_pagination_sort_and_literal_search(mysql):
    table = simple_table(mysql)
    names = ['normal', '100% exact', 'under_score', 'bang!value', "O'Reilly", '中文 🛶', 'slash\\value']
    for index, name in enumerate(names, 1):
        mysql.service.insert_row(mysql.id, table, {'id': index, 'name': name})
    first = mysql.service.fetch_rows(mysql.id, table, page=1, page_size=3)
    second = mysql.service.fetch_rows(mysql.id, table, page=2, page_size=3)
    assert first.total == second.total == len(names)
    assert [row['id'] for row in first.rows] == [1, 2, 3]
    assert [row['id'] for row in second.rows] == [4, 5, 6]
    descending = mysql.service.fetch_rows(mysql.id, table, page_size=2, sort_column='id', descending=True)
    assert [row['id'] for row in descending.rows] == [7, 6]
    for needle, expected in [('%', names[1]), ('_', names[2]), ('!', names[3]),
                             ("O'Reilly", names[4]), ('中文', names[5]), ('\\', names[6])]:
        result = mysql.service.fetch_rows(mysql.id, table, search=needle)
        assert result.total == 1
        assert result.rows[0]['name'] == expected
    assert mysql.service.fetch_rows(mysql.id, table, search="' OR 1=1 --").total == 0
    assert mysql.service.fetch_rows(mysql.id, table, page=99).rows == []
    with pytest.raises(ValueError, match='Unknown sort column'):
        mysql.service.fetch_rows(mysql.id, table, sort_column='id DESC; DROP TABLE ignored')


def test_quoted_identifiers_support_reserved_words_spaces_backticks_and_dots(mysql):
    table = mysql.table(' odd.`table')
    mysql.execute(f'CREATE TABLE {quote(table)} '
                  '( `key` INT PRIMARY KEY, `select` VARCHAR(50), `tick``col` VARCHAR(50)) ENGINE=InnoDB')
    assert mysql.service.insert_row(mysql.id, table,
                                    {'key': 1, 'select': '中文', 'tick`col': 'value'}).affected == 1
    result = mysql.service.fetch_rows(mysql.id, table, search='中文', sort_column='select')
    assert result.rows == [{'key': 1, 'select': '中文', 'tick`col': 'value'}]
    assert mysql.service.update_row(mysql.id, table, {'key': 1}, {'tick`col': "'quoted'"}).affected == 1
    assert mysql.service.delete_rows(mysql.id, table, [{'key': 1}]).affected == 1


def test_read_only_allows_browsing_and_rejects_writes(mysql):
    table = simple_table(mysql)
    mysql.service.insert_row(mysql.id, table, {'id': 1, 'name': 'unchanged'})
    read_only = mysql.service.save_connection(replace(mysql.connection, id=mysql.id + '_ro', read_only=True))
    service, connection_id = mysql.service, read_only.id
    assert service.test_connection(read_only) == 'Connected to MySQL successfully.'
    assert service.execute(connection_id, f'SELECT * FROM {quote(table)}').rows[0]['name'] == 'unchanged'
    assert service.execute(connection_id, f'DESCRIBE {quote(table)}').rows
    assert any(item.name == table for item in service.list_tables(connection_id))
    assert service.get_schema(connection_id, table)
    assert service.fetch_rows(connection_id, table, search='unchanged').total == 1
    for query in [f'INSERT INTO {quote(table)} VALUES (2, \'blocked\')',
                  f'UPDATE {quote(table)} SET name=\'blocked\'',
                  f'DELETE FROM {quote(table)}', f'DROP TABLE {quote(table)}',
                  'COMMIT', f'/* comment */ UPDATE {quote(table)} SET name=\'blocked\'']:
        with pytest.raises(PermissionError):
            service.execute(connection_id, query)
    for operation in [lambda: service.insert_row(connection_id, table, {'id': 2, 'name': 'blocked'}),
                      lambda: service.update_row(connection_id, table, {'id': 1}, {'name': 'blocked'}),
                      lambda: service.delete_rows(connection_id, table, [{'id': 1}])]:
        with pytest.raises(PermissionError):
            operation()
    assert mysql.service.fetch_rows(mysql.id, table).rows == [{'id': 1, 'name': 'unchanged'}]
    assert sum(not entry.success for entry in service.list_history()) == 9


def test_grid_guards_primary_keys_and_unknown_columns(mysql):
    no_key, compound = mysql.table('no_key'), mysql.table('compound')
    mysql.execute(f'CREATE TABLE {quote(no_key)} (name VARCHAR(50)) ENGINE=InnoDB')
    for operation in [lambda: mysql.service.insert_row(mysql.id, no_key, {'name': 'blocked'}),
                      lambda: mysql.service.update_row(mysql.id, no_key, {'name': 'x'}, {'name': 'blocked'}),
                      lambda: mysql.service.delete_rows(mysql.id, no_key, [{'name': 'x'}])]:
        with pytest.raises(ValueError, match='primary key'):
            operation()
    mysql.execute(f'CREATE TABLE {quote(compound)} '
                  '(tenant INT, id INT, name VARCHAR(50), PRIMARY KEY (tenant, id)) ENGINE=InnoDB')
    mysql.service.insert_row(mysql.id, compound, {'tenant': 1, 'id': 1, 'name': 'safe'})
    for keys in [{}, {'id': 1}, {'tenant': 1, 'id': None}, {'tenant': 1, 'id': 1, 'name': 'safe'}]:
        with pytest.raises(ValueError, match='every primary key'):
            mysql.service.update_row(mysql.id, compound, keys, {'name': 'blocked'})
        with pytest.raises(ValueError, match='every primary key'):
            mysql.service.delete_rows(mysql.id, compound, [keys])
    with pytest.raises(ValueError, match='Unknown column'):
        mysql.service.insert_row(mysql.id, compound, {'unknown': 1})
    with pytest.raises(ValueError, match='Unknown column'):
        mysql.service.update_row(mysql.id, compound, {'tenant': 1, 'id': 1}, {'unknown': 1})
    assert mysql.service.fetch_rows(mysql.id, compound).rows == [{'tenant': 1, 'id': 1, 'name': 'safe'}]


def test_read_only_transaction_blocks_writes_hidden_inside_select(mysql):
    import pymysql
    table = simple_table(mysql)
    function = mysql.table('write_function')
    mysql.execute(f'''CREATE FUNCTION {quote(function)}() RETURNS INT
        DETERMINISTIC MODIFIES SQL DATA
        BEGIN
            INSERT INTO {quote(table)} VALUES (1, 'must not persist');
            RETURN 1;
        END''')
    read_only = mysql.service.save_connection(replace(mysql.connection, id=mysql.id + '_ro', read_only=True))
    # The statement passes the editor's SELECT whitelist. The server's actual
    # read-only transaction must independently reject the function's INSERT.
    with pytest.raises(pymysql.err.OperationalError) as error:
        mysql.service.execute(read_only.id, f'SELECT {quote(function)}() AS attempted_write')
    assert error.value.args[0] == 1792
    assert mysql.service.fetch_rows(mysql.id, table).rows == []
    failure = next(entry for entry in mysql.service.list_history() if not entry.success)
    assert 'READ ONLY' in failure.error


def test_duplicate_errors_and_batch_delete_roll_back_and_record_history(mysql):
    import pymysql
    parent, child = mysql.table('parent'), mysql.table('child')
    mysql.execute(f'CREATE TABLE {quote(parent)} (id INT PRIMARY KEY, name VARCHAR(50)) ENGINE=InnoDB')
    mysql.execute(f'CREATE TABLE {quote(child)} (id INT PRIMARY KEY, parent_id INT, '
                  f'FOREIGN KEY(parent_id) REFERENCES {quote(parent)}(id)) ENGINE=InnoDB')
    for index in (1, 2):
        mysql.service.insert_row(mysql.id, parent, {'id': index, 'name': f'parent {index}'})
    mysql.service.insert_row(mysql.id, child, {'id': 1, 'parent_id': 2})
    with pytest.raises(pymysql.err.IntegrityError) as duplicate:
        mysql.service.insert_row(mysql.id, parent, {'id': 1, 'name': 'duplicate'})
    assert duplicate.value.args[0] == 1062
    with pytest.raises(pymysql.err.IntegrityError) as foreign_key:
        mysql.service.delete_rows(mysql.id, parent, [{'id': 1}, {'id': 2}])
    assert foreign_key.value.args[0] == 1451
    assert [row['id'] for row in mysql.service.fetch_rows(mysql.id, parent).rows] == [1, 2]
    failures = [entry for entry in mysql.service.list_history() if not entry.success]
    assert len(failures) == 2
    assert all(entry.error and entry.elapsed_ms >= 0 and entry.affected == 0 for entry in failures)
    assert any('duplicate' in entry.query for entry in failures)
    assert all(entry.connection_id == mysql.id for entry in failures)
    assert mysql.execute('SELECT 1 AS recovered').rows == [{'recovered': 1}]


def test_result_cap_pagination_cap_and_duplicate_column_labels(mysql):
    table = simple_table(mysql)
    values = ','.join(f"({index}, 'row {index}')" for index in range(1, 1006))
    mysql.execute(f'INSERT INTO {quote(table)} VALUES {values}')
    result = mysql.execute(f'SELECT * FROM {quote(table)} ORDER BY id')
    assert len(result.rows) == 1000
    assert result.truncated
    assert result.rows[-1]['id'] == 1000
    page = mysql.service.fetch_rows(mysql.id, table, page_size=2000)
    assert len(page.rows) == 1000
    assert page.total == 1005
    assert len(mysql.service.fetch_rows(mysql.id, table, page=2, page_size=1000).rows) == 5
    duplicate = mysql.execute('SELECT 1 AS same, 2 AS same, 3 AS same_2')
    assert duplicate.columns == ['same', 'same_2', 'same_2_2']
    assert list(duplicate.rows[0].values()) == [1, 2, 3]


def test_query_editor_rejects_multiple_statements_and_recovers(mysql):
    import pymysql
    table = simple_table(mysql)
    with pytest.raises(pymysql.err.ProgrammingError) as error:
        mysql.execute(f"INSERT INTO {quote(table)} VALUES (1, 'first'); "
                      f"INSERT INTO {quote(table)} VALUES (2, 'second')")
    assert error.value.args[0] == 1064
    assert mysql.service.fetch_rows(mysql.id, table).rows == []
    mysql.execute(f"INSERT INTO {quote(table)} VALUES (3, 'committed')")
    assert mysql.service.fetch_rows(mysql.id, table).rows == [{'id': 3, 'name': 'committed'}]
    assert any(not entry.success and entry.error for entry in mysql.service.list_history())


def test_default_insert_and_empty_deletion(mysql):
    table = mysql.table()
    mysql.execute(f'CREATE TABLE {quote(table)} '
                  "(id INT PRIMARY KEY AUTO_INCREMENT, name VARCHAR(50) NOT NULL DEFAULT 'ready') ENGINE=InnoDB")
    assert mysql.service.insert_row(mysql.id, table, {}).affected == 1
    assert mysql.service.fetch_rows(mysql.id, table).rows == [{'id': 1, 'name': 'ready'}]
    assert mysql.service.delete_rows(mysql.id, table, []).affected == 0
    assert mysql.service.update_row(mysql.id, table, {'id': 1}, {}).affected == 0
    assert mysql.service.fetch_rows(mysql.id, table).total == 1
