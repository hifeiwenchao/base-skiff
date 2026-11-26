from PySide6.QtCore import Qt

from baseskiff.service import DatabaseService
from baseskiff.ui.window import MainWindow
from tests.test_workbench_optimization_ui import ready_window


def test_display_preferences_change_density_font_and_persist(qtbot,tmp_path):
    window=ready_window(qtbot,tmp_path)
    window.set_display_preferences('compact',14)
    assert window.data_table.verticalHeader().defaultSectionSize() < 40
    assert window.query_editor.font().pointSize() == 14
    window.set_display_preferences('comfortable',13)
    assert window.data_table.verticalHeader().defaultSectionSize() >= 43
    window.close()
    second=MainWindow(DatabaseService(tmp_path),language='en')
    qtbot.addWidget(second)
    assert second.display_density == 'comfortable' and second.display_font_size == 13
    second.close()


def test_pending_edits_have_marker_count_and_summary(qtbot,tmp_path):
    window=ready_window(qtbot,tmp_path)
    column=window.current_result.columns.index('name')
    window.data_table.item(0,column).setText('Pending name')
    assert '1' in window.commit_table_button.text()
    assert '●' in window.table_tabs.tabText(window.table_tabs.currentIndex())
    assert window.data_table.item(0,column).data(Qt.ItemDataRole.BackgroundRole) is not None
    changes=window.pending_change_rows()
    assert changes[0]['field']=='name'
    assert changes[0]['new']=='Pending name'
    window.revert_table_changes()
    qtbot.waitUntil(lambda:not window.busy)
    window.close()


def test_error_feedback_is_nonblocking_and_copyable(qtbot,tmp_path):
    window=ready_window(qtbot,tmp_path)
    window.show_error('a useful database error')
    assert window.feedback_bar.isVisible()
    assert 'useful' in window.feedback_bar.message_label.text()
    window.feedback_bar.copy_button.click()
    from PySide6.QtWidgets import QApplication
    assert QApplication.clipboard().text()=='a useful database error'
    window.close()


def test_narrow_toolbar_moves_low_frequency_actions_to_more(qtbot,tmp_path):
    window=ready_window(qtbot,tmp_path)
    window.resize(1080,700)
    qtbot.wait(50)
    window._update_responsive_layout()
    assert window.more_table_button.isVisible()
    assert window.import_button.isHidden()
    assert window.run_current_button is not None and window.format_sql_button is not None
    window.close()


def test_narrow_english_large_font_pending_toolbar_fits(qtbot,tmp_path):
    window=ready_window(qtbot,tmp_path)
    window.resize(1080,700)
    window.shell_splitter.setSizes([400,680])
    window.set_display_preferences('compact',16)
    window.data_table.item(0,window.current_result.columns.index('name')).setText('draft')
    qtbot.wait(50)
    window._update_responsive_layout()
    for widget in (window.add_row_button,window.commit_table_button,window.change_summary_button,window.more_table_button):
        assert window.table_toolbar.rect().contains(widget.geometry()), (widget.text(),widget.geometry(),window.table_toolbar.rect())
    window.revert_table_changes()
    qtbot.waitUntil(lambda:not window.busy)
    window.close()


def test_task_cancel_targets_background_console(qtbot,tmp_path,monkeypatch):
    from dataclasses import replace
    from threading import Event

    from baseskiff.models import QueryResult
    window=ready_window(qtbot,tmp_path)
    first=window.connections[0]
    second=window.service.save_connection(replace(first,id='second-ui',name='Second'))
    window.connections=window.service.list_connections()
    started,release=Event(),Event()
    def execute(cid,query,*,cancel_token=None,database=None):
        started.set()
        release.wait(3)
        return QueryResult()
    monkeypatch.setattr(window.service,'execute',execute)
    try:
        window.show_console(first)
        window.query_editor.setPlainText('SELECT 1')
        window.run_query()
        qtbot.waitUntil(started.is_set)
        token=window._console_state().query_cancel_token
        origin=window._console_state()
        window.show_console(second)
        window._cancel_background_operation(token)
        assert origin.query_state=='cancelled'
        assert window._console_state().query_state!='cancelled'
    finally:
        release.set()
        qtbot.waitUntil(lambda:not window.busy)
        window.close()


def test_font_changes_existing_monospace_cells_without_losing_edits(qtbot,tmp_path):
    window=ready_window(qtbot,tmp_path)
    item=window.data_table.item(0,0)
    window.set_display_preferences('compact',16)
    assert item.font().pointSize()==16
    window.set_display_preferences('compact',11)
    assert item.font().pointSize()==11
    window.close()


def test_task_row_reuse_does_not_keep_previous_cancel_button(qtbot):
    from time import monotonic
    from types import SimpleNamespace

    from baseskiff.ui.task_panel import TaskPanel
    cancelled=[]
    tasks=[SimpleNamespace(presentation={'source':'A','operation':'execute','started':monotonic(),'cancel':lambda:cancelled.append('A')}),
           SimpleNamespace(presentation={'source':'B','operation':'read','started':monotonic(),'cancel':None})]
    panel=TaskPanel(lambda:tasks,language='en')
    qtbot.addWidget(panel)
    assert panel.table.cellWidget(0,3) is not None
    tasks.pop(0)
    panel.refresh()
    assert panel.table.cellWidget(0,3) is None


def test_multiline_error_does_not_expand_workspace(qtbot,tmp_path):
    window=ready_window(qtbot,tmp_path)
    window.resize(1080,700)
    window.show_error('bad SQL\n'*100)
    qtbot.wait(20)
    assert window.feedback_bar.height() <= 100
    assert window.minimumSizeHint().height() <= 760
    window.close()
