from types import SimpleNamespace

from PySide6.QtWidgets import QFileDialog

from baseskiff.models import QueryResult
from baseskiff.ui.data_transfer import CsvImportDialog, CsvOptionsDialog
from baseskiff.ui.query_plan import QueryPlanDialog
from tests.test_workbench_optimization_ui import ready_window


def test_csv_import_preview_mapping_and_commit(qtbot, tmp_path, monkeypatch):
    window = ready_window(qtbot, tmp_path)
    cid = window.current_connection.id
    window.service.execute(cid, 'CREATE TABLE import_target(id INTEGER PRIMARY KEY, name TEXT)')
    window.open_table('import_target')
    qtbot.waitUntil(lambda: not window.busy)
    source = tmp_path / 'source.csv'
    source.write_text('id,name\n1,one\n2,two\n')
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *a, **kw: (str(source), 'CSV'))
    monkeypatch.setattr(CsvOptionsDialog, 'exec', lambda self: self.DialogCode.Accepted)
    def accept_mapping(dialog):
        assert dialog.preview.rowCount() == 2
        assert dialog.mapping() == {0:'id', 1:'name'}
        return dialog.DialogCode.Accepted
    monkeypatch.setattr(CsvImportDialog, 'exec', accept_mapping)
    window.import_table_csv()
    qtbot.waitUntil(lambda: not window.busy)
    assert window.service.execute(cid, 'SELECT * FROM import_target ORDER BY id').rows == [{'id':1,'name':'one'},{'id':2,'name':'two'}]
    window.close()


def test_driver_error_highlights_selected_statement_offset(qtbot, tmp_path, monkeypatch):
    window = ready_window(qtbot, tmp_path)
    window.show_console(window.current_connection)
    text = 'SELECT 1;\nSELECT broken'
    window.query_editor.setPlainText(text)
    cursor = window.query_editor.textCursor()
    cursor.setPosition(10)
    cursor.setPosition(len(text), cursor.MoveMode.KeepAnchor)
    window.query_editor.setTextCursor(cursor)
    def fail(*args, **kwargs):
        error = ValueError('syntax error')
        error.diag = SimpleNamespace(statement_position='8')
        raise error
    monkeypatch.setattr(window.service, 'execute', fail)
    window.run_query()
    qtbot.waitUntil(lambda: not window.busy)
    selections = window.query_editor.extraSelections()
    assert selections
    assert selections[0].cursor.selectionStart() >= 17
    window.close()


def test_plan_dialog_shows_sqlite_tree_and_estimate_notice(qtbot):
    result = QueryResult(columns=['id','parent','detail'], rows=[
        {'id':1,'parent':0,'detail':'SEARCH customers'},
        {'id':2,'parent':1,'detail':'USING PRIMARY KEY'}], elapsed_ms=1.5)
    dialog = QueryPlanDialog(result, language='en')
    qtbot.addWidget(dialog)
    assert dialog.tree.topLevelItemCount() == 1
    assert dialog.tree.topLevelItem(0).childCount() == 1


def test_metadata_request_is_async_and_scoped(qtbot, tmp_path):
    window = ready_window(qtbot, tmp_path)
    connection = window.current_connection
    window.show_console(connection)
    window._sql_metadata.clear()
    window._sync_sql_metadata()
    window.query_editor.setPlainText('SELECT c. FROM customers c')
    cursor = window.query_editor.textCursor()
    cursor.setPosition(9)
    window.query_editor.setTextCursor(cursor)
    window.query_editor.show_completions()
    qtbot.waitUntil(lambda: not window.busy)
    assert 'customers' in window._sql_metadata[window._console_scope_for(connection)]
    assert 'name' in window.query_editor.completion_candidates()
    window.close()


def test_explicit_metadata_refresh_invalidates_old_fields(qtbot, tmp_path):
    window = ready_window(qtbot, tmp_path)
    connection = window.current_connection
    window.show_console(connection)
    scope = window._console_scope_for(connection)
    window._sql_metadata[scope] = {'customers':['stale_field']}
    window.refresh_sql_metadata()
    qtbot.waitUntil(lambda: not window.busy)
    assert 'stale_field' not in window._sql_metadata[scope].get('customers', [])
    window._request_sql_table_metadata('customers')
    qtbot.waitUntil(lambda: not window.busy)
    assert 'name' in window._sql_metadata[scope]['customers']
    window.close()
