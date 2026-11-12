"""Loaded Redis trees settle instead of repeatedly rebuilding while idle."""
from PySide6.QtWidgets import QApplication
from shiboken6 import isValid

from baseskiff.models import RedisKeyPage, TableInfo
from tests.redis_tree_helpers import seed_database
from tests.test_redis_database_tree import db_node
from tests.test_redis_database_tree import window as window


def load_keys(window, keys):
    blocked = window.tree.blockSignals(True)
    window.tree.topLevelItem(0).setExpanded(True)
    db_node(window, 0).setExpanded(True)
    window.tree.blockSignals(blocked)
    seed_database(window, window.redis_connection, [])
    # Loading a result updates a DB whose scoped search row is already mounted.
    window._cache_redis_key_page((window.redis_connection.id, '0'), RedisKeyPage(keys), '', 'prefix')
    return db_node(window, 0)


def test_loaded_keys_do_not_reset_the_tree_while_idle_or_scrolling(window):
    database = load_keys(window, [TableInfo(f'app:tenant:{i:04}:profile') for i in range(500)])
    resets = []
    window.tree.model().modelReset.connect(lambda: resets.append(True))
    # Drain deferred widget/layout work without timers or database activity.
    for _ in range(12):
        QApplication.processEvents()
    resets.clear()
    scrollbar = window.tree.verticalScrollBar()
    for i in range(20):
        scrollbar.setValue(scrollbar.maximum() if i % 2 else 0)
        QApplication.processEvents()
    assert resets == [], 'Loaded trees must not recreate every key on each event-loop turn'
    assert isValid(database)
    assert database is db_node(window, 0)
    assert window.redis_search_group.isVisible()
    assert not window.busy


def test_hiding_an_already_closed_mode_menu_does_not_schedule_a_tree_reset(window):
    load_keys(window, [TableInfo('team:one')])
    for _ in range(6):
        QApplication.processEvents()
    resets = []
    window.tree.model().modelReset.connect(lambda: resets.append(True))
    window.redis_key_mode.hidePopup()
    window.redis_key_mode.hidePopup()
    for _ in range(6):
        QApplication.processEvents()
    assert resets == [], 'Native hide events for a closed combo must leave its owning tree alone'


def test_native_escape_closes_mode_menu_and_releases_pending_tree_update(window, qtbot):
    from PySide6.QtCore import Qt

    load_keys(window, [TableInfo('team:one')])
    for _ in range(6):
        QApplication.processEvents()
    mode = window.redis_key_mode
    mode.showPopup()
    assert mode.view().isVisible()
    # A batch may finish while the native mode menu is open.
    window.rebuild_tree()
    assert window._tree_rebuild_pending
    qtbot.keyClick(mode.view().window(), Qt.Key.Key_Escape)
    for _ in range(8):
        QApplication.processEvents()
    assert not mode.view().isVisible()
    assert not mode.popup_open, 'Native cancellation must release the popup lifecycle guard'
    assert not window._tree_rebuild_pending
    assert mode.currentData() == 'prefix'
    assert window.redis_search_group.isVisible()
    resets = []
    window.tree.model().modelReset.connect(lambda: resets.append(True))
    for _ in range(8):
        QApplication.processEvents()
    assert resets == [], 'Closing the real menu must not restart the idle rebuild loop'
