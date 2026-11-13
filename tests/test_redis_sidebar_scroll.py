"""Opening Redis keys keeps the navigation viewport where the user left it."""
import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QAbstractItemView, QApplication, QHeaderView

from baseskiff.models import TableInfo
from baseskiff.ui.redis_tree import NODE_KIND_ROLE, iter_items
from tests.redis_tree_helpers import seed_database
from tests.test_redis_database_tree import db_node, run_pending
from tests.test_redis_database_tree import window as window


def key_item(window, key):
    return next(item for item in iter_items(db_node(window, 0))
                if item.data(0, NODE_KIND_ROLE) == 'key'
                and item.data(0, Qt.ItemDataRole.UserRole)[1] == key)


@pytest.mark.parametrize('existing_tab', [False, True])
def test_clicking_a_key_preserves_sidebar_scroll_through_loading(window, qtbot, existing_tab):
    names = [f'item:{index:04}:long-label-for-horizontal-scrolling' for index in range(150)]
    with window.service._redis_adapter(window.redis_connection.id, database='0').session() as client:
        client.mset({name: 'value' for name in names})
    seed_database(window, window.redis_connection, [TableInfo(name) for name in names])
    for item in iter_items(db_node(window, 0)):
        if item.data(0, NODE_KIND_ROLE) == 'redis_folder':
            item.setExpanded(True)
    target = names[75]
    if existing_tab:
        window.open_table(target)
        run_pending(window)
        window.open_table(names[74])
        run_pending(window)
    window.tree.scrollToItem(key_item(window, target), QAbstractItemView.ScrollHint.PositionAtCenter)
    QApplication.processEvents()
    window.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
    window.tree.header().resizeSection(0, 650)
    QApplication.processEvents()
    window.tree.horizontalScrollBar().setValue(25)
    before = (window.tree.horizontalScrollBar().value(), window.tree.verticalScrollBar().value())
    assert before[0] == 25
    assert before[1] > 30, 'The test key must be well below the top of the tree'
    rectangle = window.tree.visualItemRect(key_item(window, target))
    point = QPoint(min(rectangle.right(), window.tree.viewport().width() - 12), rectangle.center().y())
    qtbot.mouseClick(window.tree.viewport(), Qt.MouseButton.LeftButton, pos=point)
    QApplication.processEvents()
    assert window.current_table == target
    assert (window.tree.horizontalScrollBar().value(), window.tree.verticalScrollBar().value()) == before
    run_pending(window)
    qtbot.wait(20)  # Deliver native queued layout/current-item scroll work.
    assert (window.tree.horizontalScrollBar().value(), window.tree.verticalScrollBar().value()) == before
    assert window._redis_snapshot.value == 'value'
