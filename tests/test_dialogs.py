"""Dialog contracts tested against real Qt widgets, without a server."""
import sqlite3
from datetime import date, datetime
from decimal import Decimal

import pytest
from PySide6.QtWidgets import QDialog

from baseskiff.models import ColumnInfo, Connection


def test_translator_switches_language_and_formats_values():
    from baseskiff.i18n import Translator

    translator = Translator("zh")
    assert translator.tr("cancel") == "取消"
    translator.set_language("en")
    assert translator.tr("cancel") == "Cancel"
    assert translator.tr("rows_count", count=12) == "12 rows"
    assert translator.tr("unknown.key") == "unknown.key"


def test_connection_editor_preserves_identity_and_private_fields(qtbot, tmp_path):
    from baseskiff.ui.dialogs import ConnectionDialog

    original = Connection("Existing", kind="PostgreSQL", id="original-id", port=5439,
                          username="alice", password="secret", group="Production", ssl=True)
    dialog = ConnectionDialog(connection=original, language="en")
    qtbot.addWidget(dialog)
    dialog.name_edit.setText("Renamed")
    connection = dialog.current_connection()
    assert (connection.id, connection.name, connection.port) == ("original-id", "Renamed", 5439)
    assert (connection.password, connection.group, connection.ssl) == ("secret", "Production", True)
    assert original.name == "Existing"


@pytest.mark.parametrize('kind,port', [('Redis', 6379), ('SQLite', 0)])
def test_selected_connection_type_has_default_port_and_visible_fields(qtbot, kind, port):
    from baseskiff.ui.dialogs import ConnectionDialog

    dialog = ConnectionDialog(kind=kind, language="en")
    qtbot.addWidget(dialog)
    dialog.show()
    assert dialog.current_connection().port == port
    assert dialog.host_edit.isVisible() is (kind != 'SQLite')
    assert dialog.path_edit.isVisible() is (kind == 'SQLite')


def test_connection_validation_keeps_dialog_open_and_test_emits_configuration(qtbot, tmp_path):
    from baseskiff.ui.dialogs import ConnectionDialog

    dialog = ConnectionDialog(kind="SQLite", language="en")
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.name_edit.clear()
    dialog.accept()
    assert dialog.result() == QDialog.Rejected
    assert dialog.error_label.text()
    dialog.name_edit.setText("Local")
    dialog.path_edit.setText(str(tmp_path / "new.db"))
    with qtbot.waitSignal(dialog.test_requested) as emitted:
        dialog.test_button.click()
    assert emitted.args[0].path.endswith("new.db")
    dialog.accept()
    assert dialog.result() == QDialog.Accepted
    assert dialog.result_connection().kind == "SQLite"


def test_insert_omits_generated_values_and_distinguishes_null_from_empty(qtbot):
    from baseskiff.ui.dialogs import RowDialog

    columns = [ColumnInfo("id", "INTEGER", primary_key=True, nullable=False),
               ColumnInfo("created_at", "TEXT", default="CURRENT_TIMESTAMP"),
               ColumnInfo("note", "TEXT"), ColumnInfo("nickname", "TEXT")]
    dialog = RowDialog(columns, language="en")
    qtbot.addWidget(dialog)
    dialog.null_checks["note"].setChecked(True)
    dialog.null_checks["nickname"].setChecked(False)
    assert dialog.values() == {"note": None, "nickname": ""}


def test_edit_preserves_primary_key_and_numeric_boolean_json_types(qtbot):
    from baseskiff.ui.dialogs import RowDialog

    columns = [ColumnInfo("id", "INTEGER", True, False), ColumnInfo("quantity", "INTEGER"),
               ColumnInfo("price", "REAL"), ColumnInfo("active", "BOOLEAN"),
               ColumnInfo("document", "JSON")]
    row = {"id": 7, "quantity": 2, "price": 2.5, "active": True, "document": {"a": [1]}}
    dialog = RowDialog(columns, row, language="en")
    qtbot.addWidget(dialog)
    assert not dialog.fields["id"].isEnabled()
    dialog.fields["quantity"].setText("9")
    dialog.fields["price"].setText("10.25")
    dialog.fields["active"].setText("false")
    dialog.fields["document"].setPlainText('{"a": [2], "valid": true}')
    assert dialog.values() == {"id": 7, "quantity": 9, "price": 10.25,
                               "active": False, "document": {"a": [2], "valid": True}}


@pytest.mark.parametrize('data_type', ['NUMERIC', 'DECIMAL(30, 12)', 'NUMBER'])
def test_insert_exact_sql_numeric_keeps_decimal_precision(qtbot, data_type):
    from baseskiff.ui.dialogs import RowDialog

    dialog = RowDialog([ColumnInfo('amount', data_type, nullable=False)], language='en')
    qtbot.addWidget(dialog)
    text = '123456789012345678.123456789012'
    dialog.fields['amount'].setText(text)
    value = dialog.values()['amount']
    assert isinstance(value, Decimal)
    assert value == Decimal(text)


@pytest.mark.parametrize('data_type,text,expected', [
    ('DATE', '2024-02-29', date(2024, 2, 29)),
    ('TIMESTAMP(6)', '2024-02-29T12:34:56.123456', datetime(2024, 2, 29, 12, 34, 56, 123456)),
    ('DATETIME', '2024-02-29T12:34:56.123456', datetime(2024, 2, 29, 12, 34, 56, 123456)),
    ('timestamp with time zone', '2024-02-29T12:34:56.123456+08:00',
     datetime.fromisoformat('2024-02-29T12:34:56.123456+08:00')),
])
def test_insert_sql_date_and_timestamp_use_typed_values(qtbot, data_type, text, expected):
    from baseskiff.ui.dialogs import RowDialog

    dialog = RowDialog([ColumnInfo('recorded_at', data_type, nullable=False)], language='en')
    qtbot.addWidget(dialog)
    dialog.fields['recorded_at'].setText(text)
    value = dialog.values()['recorded_at']
    assert type(value) is type(expected)
    assert value == expected


@pytest.mark.parametrize("column,value", [(ColumnInfo("quantity", "INTEGER"), "nine"),
                                         (ColumnInfo("document", "JSON"), "{broken")])
def test_invalid_row_value_is_not_accepted(qtbot, column, value):
    from baseskiff.ui.dialogs import RowDialog

    dialog = RowDialog([column], language="en")
    qtbot.addWidget(dialog)
    dialog.null_checks[column.name].setChecked(False)
    field = dialog.fields[column.name]
    if hasattr(field, "setPlainText"):
        field.setPlainText(value)
    else:
        field.setText(value)
    dialog.accept()
    assert dialog.result() == QDialog.Rejected
    assert dialog.error_label.text()
    with pytest.raises(ValueError):
        dialog.values()


def test_mongodb_insert_omits_empty_object_id(qtbot):
    from baseskiff.ui.dialogs import RowDialog

    dialog = RowDialog([ColumnInfo("_id", "ObjectId", primary_key=True, nullable=False),
                        ColumnInfo("name", "TEXT", nullable=False)], language="en")
    qtbot.addWidget(dialog)
    dialog.fields["name"].setText("Ada")
    assert dialog.values() == {"name": "Ada"}


def test_redis_string_value_preserves_json_looking_text(qtbot):
    from baseskiff.ui.dialogs import RowDialog

    columns = [ColumnInfo("key", "TEXT", True, False), ColumnInfo("type", "TEXT"),
               ColumnInfo("value", "JSON")]
    original = {"key": "settings", "type": "string", "value": '{"enabled": true}'}
    dialog = RowDialog(columns, original, language="en")
    qtbot.addWidget(dialog)
    assert dialog.values() == original
    dialog.fields["value"].setPlainText("a plain Redis string")
    assert dialog.values()["value"] == "a plain Redis string"


def test_redis_hash_value_parses_json_and_type_changes_are_validated(qtbot):
    from baseskiff.ui.dialogs import RowDialog

    columns = [ColumnInfo("key", "TEXT", True, False), ColumnInfo("type", "TEXT"),
               ColumnInfo("value", "JSON")]
    dialog = RowDialog(columns, {"key": "profile", "type": "hash", "value": {"name": "Ada"}}, language="en")
    qtbot.addWidget(dialog)
    dialog.fields["value"].setPlainText('{"name": "Grace"}')
    assert dialog.values()["value"] == {"name": "Grace"}
    dialog.fields["type"].setText("string")
    assert dialog.values()["value"] == '{"name": "Grace"}'


def test_create_sqlite_file_initializes_a_real_database(qtbot, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QFileDialog

    from baseskiff.ui.dialogs import ConnectionDialog

    path = tmp_path / "new.sqlite3"
    dialog = ConnectionDialog(language="en")
    qtbot.addWidget(dialog)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(path), ""))
    dialog.create_file_button.click()
    assert path.read_bytes().startswith(b"SQLite format 3\x00")
    with sqlite3.connect(path) as database:
        assert database.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert database.execute("SELECT name FROM sqlite_master").fetchall() == []
    assert dialog.path_edit.text() == str(path)


def test_create_sqlite_file_preserves_existing_database(qtbot, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QFileDialog

    from baseskiff.ui.dialogs import ConnectionDialog

    path = tmp_path / "existing.sqlite3"
    with sqlite3.connect(path) as database:
        database.execute("CREATE TABLE precious (value TEXT)")
        database.execute("INSERT INTO precious VALUES ('keep me')")
    original = path.read_bytes()
    dialog = ConnectionDialog(language="en")
    qtbot.addWidget(dialog)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(path), ""))
    dialog.create_file_button.click()
    assert path.read_bytes() == original
    assert dialog.path_edit.text() == str(path)


def test_create_sqlite_file_failure_keeps_previous_path(qtbot, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QFileDialog

    from baseskiff.ui.dialogs import ConnectionDialog

    dialog = ConnectionDialog(language="en")
    qtbot.addWidget(dialog)
    dialog.path_edit.setText("previous.sqlite3")
    missing_parent = tmp_path / "missing" / "new.sqlite3"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(missing_parent), ""))
    dialog.create_file_button.click()
    assert not missing_parent.exists()
    assert dialog.path_edit.text() == "previous.sqlite3"
    assert dialog.error_label.text()


def test_mongo_reference_object_id_preserves_type_and_can_be_edited(qtbot):
    from bson import ObjectId

    from baseskiff.ui.dialogs import RowDialog

    old_id = ObjectId("000000000000000000000001")
    new_id = ObjectId("000000000000000000000002")
    dialog = RowDialog([ColumnInfo("reference", "ObjectId")], {"reference": old_id}, language="en")
    qtbot.addWidget(dialog)
    assert dialog.values()["reference"] == old_id
    dialog.fields["reference"].setText(str(new_id))
    assert dialog.values()["reference"] == new_id
    dialog.fields["reference"].setText("not an ObjectId")
    dialog.accept()
    assert dialog.result() == QDialog.Rejected
    assert dialog.error_label.text()


def test_untouched_bson_values_keep_their_original_types(qtbot):
    from bson.binary import Binary
    from bson.decimal128 import Decimal128

    from baseskiff.ui.dialogs import RowDialog

    original = {"amount": Decimal128("12.50"), "binary": Binary(b"abc", subtype=4)}
    dialog = RowDialog([ColumnInfo("amount", "Decimal128"), ColumnInfo("binary", "Binary")],
                       original, language="en")
    qtbot.addWidget(dialog)
    values = dialog.values()
    assert values["amount"] == original["amount"]
    assert values["binary"] == original["binary"]
    assert values["binary"].subtype == 4


def test_mongo_nested_editor_preserves_bson_types_when_sibling_changes(qtbot):
    import json
    from datetime import datetime

    from bson import Binary, Decimal128, Int64, ObjectId

    from baseskiff.ui.dialogs import RowDialog

    nested = {'identifier': ObjectId(), 'amount': Decimal128('1.2500'),
              'counter': Int64(12), 'when': datetime(2026, 11, 11, 10, 0),
              'binary': Binary(b'\x00\xff', subtype=128), 'label': 'before'}
    dialog = RowDialog([ColumnInfo('document', 'JSON')], {'document': nested},
                       language='en', bson_mode=True)
    qtbot.addWidget(dialog)
    text = json.loads(dialog.fields['document'].toPlainText())
    text['label'] = 'after'
    dialog.fields['document'].setPlainText(json.dumps(text))
    result = dialog.values()['document']
    assert result == dict(nested, label='after')
    assert isinstance(result['counter'], Int64)
    assert isinstance(result['binary'], Binary) and result['binary'].subtype == 128


def test_sql_json_editor_keeps_extended_json_looking_objects_literal(qtbot):
    import json

    from baseskiff.ui.dialogs import RowDialog

    document = {'identifier': {'$oid': 'literal SQL JSON value'}, 'label': 'before'}
    dialog = RowDialog([ColumnInfo('document', 'JSON')], {'document': document}, language='en')
    qtbot.addWidget(dialog)
    changed = dict(document, label='after')
    dialog.fields['document'].setPlainText(json.dumps(changed))
    assert dialog.values()['document'] == changed
