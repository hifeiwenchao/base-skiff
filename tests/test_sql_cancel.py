"""SQL Console cancellation keeps late worker replies out of the UI."""
from threading import Event

from baseskiff.models import QueryResult
from baseskiff.service import DatabaseService
from baseskiff.ui.window import MainWindow


def test_console_cancel_interrupts_running_query_and_ignores_late_result(qtbot, tmp_path, monkeypatch):
    window = MainWindow(DatabaseService(tmp_path), language='en')
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: not window.busy and window.data_table.rowCount() > 0, timeout=15000)
    window.show_console(window.connections[0], 'playground.sqlite')

    started, release, tokens = Event(), Event(), []

    def execute(_connection_id, _query, *, cancel_token=None, database=None):
        tokens.append(cancel_token)
        started.set()
        release.wait(2)
        return QueryResult(columns=['answer'], rows=[{'answer': 42}], total=1)

    monkeypatch.setattr(window.service, 'execute', execute)
    window.query_editor.setPlainText('SELECT 42 AS answer')
    window.run_query()
    qtbot.waitUntil(started.is_set, timeout=3000)
    assert window.run_button.text() == 'Cancel query'

    window.run_button.click()
    assert tokens[0].cancelled
    assert window._query_state == 'cancelled'
    release.set()
    qtbot.waitUntil(lambda: not window.busy, timeout=3000)
    assert window.query_result_table.rowCount() == 0
    window.close()
