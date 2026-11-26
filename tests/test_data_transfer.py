import csv
import sqlite3

import pytest

from baseskiff.adapters import SQLAdapter
from baseskiff.cancellation import CancellationToken, QueryCancelled
from baseskiff.data_transfer import CsvOptions, import_csv, preview_csv
from baseskiff.models import Connection


@pytest.fixture
def adapter(tmp_path):
    path = tmp_path / 'target.sqlite'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE items(id INTEGER PRIMARY KEY, name TEXT NOT NULL, amount NUMERIC, active BOOLEAN)')
    return SQLAdapter(Connection('target', path=str(path))), path


def test_preview_and_mapped_stream_import(adapter, tmp_path):
    target, path = adapter
    source = tmp_path / 'source.csv'
    source.write_text('名称,编号,金额,启用\n测试,1,12.50,true\nempty,2,\\N,false\n', encoding='utf-8-sig')
    options = CsvOptions(null_value='\\N')
    header, rows = preview_csv(source, options)
    assert header == ['名称', '编号', '金额', '启用'] and len(rows) == 2
    result = import_csv(target, source, table='items', mapping={0:'name', 1:'id', 2:'amount', 3:'active'}, options=options)
    assert result.affected == 2
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT * FROM items ORDER BY id').fetchall() == [(1, '测试', 12.5, 1), (2, 'empty', None, 0)]


def test_bad_row_and_cancel_roll_back_entire_import(adapter, tmp_path):
    target, path = adapter
    source = tmp_path / 'source.csv'
    source.write_text('id,name\n1,first\nbad,second\n')
    with pytest.raises(ValueError, match='3'):
        import_csv(target, source, table='items', mapping={0:'id',1:'name'})
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT COUNT(*) FROM items').fetchone()[0] == 0
    source.write_text('id,name\n1,first\n2,second\n')
    token = CancellationToken()
    with pytest.raises(QueryCancelled):
        import_csv(target, source, table='items', mapping={0:'id',1:'name'}, cancel_token=token,
                   progress=lambda count: token.cancel(), batch_size=1)
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT COUNT(*) FROM items').fetchone()[0] == 0


def test_import_readonly_and_duplicate_mapping_rejected(adapter, tmp_path):
    target, path = adapter
    source = tmp_path / 'source.csv'
    source.write_text('a,b\n1,2\n')
    with pytest.raises(ValueError):
        import_csv(target, source, table='items', mapping={0:'name',1:'name'})
    target.connection.read_only = True
    with pytest.raises(PermissionError):
        import_csv(target, source, table='items', mapping={0:'id'})


def test_csv_codec_delimiter_and_null_export(adapter, tmp_path):
    target, _ = adapter
    target.execute("INSERT INTO items(id,name) VALUES (1,'中文')")
    destination = tmp_path / 'gb.csv'
    target.export_csv(destination, table='items', encoding='gb18030', null_value='\\N')
    with destination.open(encoding='gb18030', newline='') as stream:
        assert list(csv.reader(stream))[1] == ['1', '中文', '\\N', '\\N']


def test_malformed_csv_reports_line_and_rolls_back(adapter, tmp_path):
    target, path = adapter
    source = tmp_path / 'bad.csv'
    source.write_text('id,name\n1,first\n2,"unclosed\n')
    with pytest.raises(ValueError, match='CSV line 3'):
        import_csv(target, source, table='items', mapping={0:'id',1:'name'})
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT COUNT(*) FROM items').fetchone()[0] == 0


def test_date_time_and_enum_values_are_not_coerced_incorrectly():
    from datetime import datetime

    from baseskiff.data_transfer import convert_csv_value
    from baseskiff.models import ColumnInfo
    options = CsvOptions()
    assert convert_csv_value('2026-11-26 13:14:15', ColumnInfo('d','DATE'), options) == datetime(2026,11,26,13,14,15)
    for kind, value in [("ENUM('int','text')", 'text'), ("ENUM('boolean','x')", 'boolean'), ("SET('real','x')", 'real,x')]:
        assert convert_csv_value(value, ColumnInfo('choice',kind), options) == value
