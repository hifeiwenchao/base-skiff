"""Starting a continuation must leave the already populated tree in place."""
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication
from shiboken6 import isValid

from tests.test_redis_database_tree import db_node
from tests.test_redis_database_tree import window as window
from tests.test_redis_key_batches_ui import initial_page, keys, more, next_expansion, page


@pytest.mark.parametrize('reopen', [False, True])
def test_starting_next_batch_keeps_existing_nodes_without_reset(window, reopen):
    initial_page(window, page(0, 500, object()))
    QApplication.processEvents()
    branch = db_node(window, 0)
    next_row = more(window)
    search_host = window.tree.itemWidget(window._redis_search_item, 0)
    resets = QSignalSpy(window.tree.model().modelReset)
    if reopen:
        next_expansion(window)
    else:
        window.tree_clicked(next_row, 0)
    QApplication.processEvents()
    assert len(window.requests) == 1
    assert resets.count() == 0
    assert isValid(branch) and db_node(window, 0) is branch
    assert isValid(next_row) and more(window) is next_row
    assert window.tree.itemWidget(window._redis_search_item, 0) is search_host
    assert 'Loading more' in next_row.text(0)
    assert not (next_row.flags() & Qt.ItemFlag.ItemIsEnabled)
    assert len(keys(window)) == 500
    window.requests.pop()[1](page(500, 1000))
    assert len(keys(window)) == 1000


def test_failed_continuation_restores_retry_row_without_resetting_loaded_tree(window):
    initial_page(window, page(0, 500, object()))
    QApplication.processEvents()
    next_row = more(window)
    window.tree_clicked(next_row, 0)
    resets = QSignalSpy(window.tree.model().modelReset)
    window.requests.pop()[2]('temporary error')
    QApplication.processEvents()
    assert resets.count() == 0
    assert isValid(next_row) and more(window) is next_row
    assert 'Load more' in next_row.text(0)
    assert next_row.flags() & Qt.ItemFlag.ItemIsEnabled
    assert len(keys(window)) == 500
    window.tree_clicked(next_row, 0)
    assert len(window.requests) == 1


def test_completed_batch_keeps_native_nodes_and_search_widget(window):
    initial_page(window, page(0, 500, object()))
    QApplication.processEvents()
    branch = db_node(window, 0)
    from baseskiff.ui.redis_tree import NODE_KIND_ROLE, iter_items
    leaf = next(item for item in iter_items(branch) if item.data(0, NODE_KIND_ROLE) == 'key')
    search = window._redis_search_item
    host = window.tree.itemWidget(search, 0)
    window.tree_clicked(more(window), 0)
    resets = QSignalSpy(window.tree.model().modelReset)
    window.requests.pop()[1](page(500, 1000, object()))
    QApplication.processEvents()
    assert resets.count() == 0
    assert isValid(branch) and isValid(leaf)
    assert db_node(window, 0) is branch
    assert search is window._redis_search_item
    assert window.tree.itemWidget(search, 0) is host
    assert branch.child(0) is search
    assert len(keys(window)) == 1000
