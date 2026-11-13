"""Redis search drafts reach the server only after an explicit submission."""
import pytest
from PySide6.QtCore import Qt

from tests.test_redis_database_tree import db_node, run_pending
from tests.test_redis_database_tree import window as window


@pytest.mark.parametrize('edit', ['text', 'mode', 'clear'])
def test_editing_search_keeps_loaded_results_without_querying(window, qtbot, edit):
    db_node(window, 0).setExpanded(True)
    run_pending(window)
    window.redis_key_search.setText('team:')
    window.redis_key_search.returnPressed.emit()
    run_pending(window)
    assert [table.name for table in window.tables] == ['team:one']

    if edit == 'text':
        window.redis_key_search.setText('raw:')
    elif edit == 'mode':
        window.redis_key_mode.setCurrentIndex(window.redis_key_mode.findData('exact'))
    else:
        window.redis_key_search.clear()
    qtbot.wait(500)  # Exceeds the former automatic-search delay.

    assert not window.requests, 'Editing a draft must never enqueue a Redis query'
    assert [table.name for table in window.tables] == ['team:one']


@pytest.mark.parametrize('gesture', ['button', 'enter'])
@pytest.mark.parametrize('mode', ['prefix', 'exact'])
def test_search_button_and_enter_submit_the_literal_draft_once(window, qtbot, gesture, mode):
    text = ' team[*]? '
    with window.service._redis_adapter(window.redis_connection.id, database='0').session() as client:
        client.set(text, 'literal')
    db_node(window, 0).setExpanded(True)
    run_pending(window)
    window.redis_key_search.setText(text)
    window.redis_key_mode.setCurrentIndex(window.redis_key_mode.findData(mode))
    assert not window.requests
    if gesture == 'button':
        qtbot.mouseClick(window.redis_key_search_button, Qt.MouseButton.LeftButton)
    else:
        qtbot.keyClick(window.redis_key_search, Qt.Key.Key_Return)
    assert len(window.requests) == 1
    run_pending(window)
    qtbot.wait(500)
    assert not window.requests, 'An explicit search must not leave another timed search queued'
    assert [table.name for table in window.tables] == [text]
    assert window.redis_key_search.text() == text


def test_catalog_refresh_uses_submitted_filter_and_preserves_draft(window):
    db_node(window, 0).setExpanded(True)
    run_pending(window)
    window.redis_key_search.setText('team:')
    window.redis_key_search.returnPressed.emit()
    run_pending(window)
    window.redis_key_search.setText('raw:')
    window.redis_key_mode.setCurrentIndex(window.redis_key_mode.findData('exact'))

    window.refresh_catalog()
    run_pending(window)

    assert [table.name for table in window.tables] == ['team:one']
    assert window.redis_key_search.text() == 'raw:'
    assert window.redis_key_mode.currentData() == 'exact'


def test_returning_to_a_cached_db_restores_its_draft_without_submitting(window, qtbot):
    db_node(window, 0).setExpanded(True)
    run_pending(window)
    window.redis_key_search.setText('unsubmitted:')
    window.redis_key_mode.setCurrentIndex(window.redis_key_mode.findData('exact'))
    db_node(window, 1).setExpanded(True)
    run_pending(window)
    window.redis_key_search.setText('another draft')
    db_node(window, 0).setExpanded(False)
    db_node(window, 0).setExpanded(True)
    # Reopening DB0 validates the DB switch, but must not search its draft.
    run_pending(window)
    qtbot.wait(500)

    assert window.redis_key_search.text() == 'unsubmitted:'
    assert window.redis_key_mode.currentData() == 'exact'
    assert {table.name for table in window.tables} == {'team:one', b'raw:\xff'}
    assert not window.requests


def test_search_draft_does_not_expand_the_loaded_unfiltered_folders(window):
    db_node(window, 0).setExpanded(True)
    run_pending(window)
    window.redis_key_search.setText('team:')
    window.rebuild_tree()

    from baseskiff.ui.redis_tree import NODE_KIND_ROLE, iter_items
    folders = [item for item in iter_items(db_node(window, 0))
               if item.data(0, NODE_KIND_ROLE) == 'redis_folder']
    assert folders and not any(item.isExpanded() for item in folders)
    assert not window.requests
