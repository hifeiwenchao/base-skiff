"""Redis key hierarchy and navigation through actual native tree widgets."""
from dataclasses import replace

import pytest
from PySide6.QtCore import QPoint, Qt, QThreadPool

from baseskiff.models import Connection, TableInfo
from baseskiff.service import DatabaseService
from baseskiff.ui.window import MainWindow
from tests.redis_tree_helpers import database_node, key_children, seed_database
from tests.ui_mouse import double_click

KIND_ROLE = Qt.ItemDataRole.UserRole + 1
COUNT_ROLE = Qt.ItemDataRole.UserRole + 4


def descendants(item):
    pending = [item]
    while pending:
        current = pending.pop()
        yield current
        pending.extend(current.child(index) for index in reversed(range(current.childCount())))


def leaf(root, key):
    return next(item for item in descendants(root)
                if item.data(0, KIND_ROLE) == 'key'
                and item.data(0, Qt.ItemDataRole.UserRole)[1] == key)


def folder(parent, segment):
    return next(parent.child(index) for index in range(parent.childCount())
                if parent.child(index).data(0, KIND_ROLE) == 'redis_folder'
                and parent.child(index).text(0).startswith(segment + ' ('))


@pytest.fixture
def tree_window(qtbot, tmp_path):
    window = MainWindow(DatabaseService(tmp_path), language='en')
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: not window.busy and window.data_table.rowCount() > 0
                   and QThreadPool.globalInstance().activeThreadCount() == 0, timeout=15000)
    connection = Connection('Redis hierarchy', kind='Redis')
    window.connections = [connection]
    window.current_connection = connection
    window.current_table = ''

    def load(keys, separator=':'):
        candidate = replace(connection, redis_separator=separator)
        window.connections = [candidate]
        window.current_connection = candidate
        return seed_database(window, candidate, [TableInfo(key, kind='string') for key in keys])

    yield window, load
    window.close()


def test_redis_keys_form_counted_folders_with_full_original_leaf_names(tree_window):
    window, load = tree_window
    keys = ['h:match:cart:stock:1:4205457', 'h:match:event:emoji:one',
            'h:match:event:emoji:two', 'h:member:alice', 'z:ranking', 'unprefixed']
    root = load(keys)
    h = folder(root, 'h')
    assert h.text(0) == 'h (4)'
    assert h.data(0, COUNT_ROLE) == 4
    match = folder(h, 'match')
    assert match.text(0) == 'match (3)'
    one = folder(folder(folder(match, 'cart'), 'stock'), '1')
    item = leaf(root, keys[0])
    assert item.parent() is one
    assert item.text(0) == keys[0]
    assert item.toolTip(0) == keys[0]
    assert item.data(0, Qt.ItemDataRole.UserRole) == (window.current_connection.id, keys[0])
    assert not h.icon(0).isNull()
    assert sorted(item.text(0) for item in descendants(root) if item.data(0, KIND_ROLE) == 'key') == sorted(keys)


@pytest.mark.parametrize('separator,keys', [
    ('::', ['a::b::one', 'a::b::two', 'a:b:flat']),
    ('→', ['客户→订单→一', '客户→订单→二', 'untouched:key']),
    ('.', ['a.b.one', 'a.b.two', 'plain']),
])
def test_custom_delimiters_are_literal_and_preserve_every_key(tree_window, separator, keys):
    _window, load = tree_window
    root = load(keys, separator)
    assert any(item.data(0, KIND_ROLE) == 'redis_folder' for item in descendants(root))
    assert sorted(item.text(0) for item in descendants(root) if item.data(0, KIND_ROLE) == 'key') == sorted(keys)
    assert leaf(root, keys[-1]).parent() is root
    assert folder(root, keys[0].split(separator)[0]).data(0, COUNT_ROLE) == 2


def test_empty_delimiter_keeps_a_flat_key_list(tree_window):
    _window, load = tree_window
    keys = ['h:match:one', 'h:match:two', 'z:one']
    root = load(keys, '')
    assert len(key_children(root)) == len(keys)
    assert all(child.childCount() == 0 for child in key_children(root))
    assert sorted(item.text(0) for item in descendants(root) if item.data(0, KIND_ROLE) == 'key') == sorted(keys)


def test_prefix_keys_and_empty_path_segments_are_not_lost(tree_window):
    _window, load = tree_window
    keys = ['a', 'a:b', 'a:b:c', ':leading', 'a::middle', 'a:', '::']
    root = load(keys)
    leaves = [item for item in descendants(root) if item.data(0, KIND_ROLE) == 'key']
    assert sorted(item.text(0) for item in leaves) == sorted(keys)
    assert leaf(root, 'a').parent() is root
    assert leaf(root, 'a:b').parent() is folder(root, 'a')
    assert leaf(root, 'a::middle').parent().text(0) == '(empty) (1)'


def test_leaf_click_opens_the_exact_key(tree_window, monkeypatch):
    window, load = tree_window
    key = 'h:match:cart:stock:1:4205457'
    root = load([key])
    opened = []
    monkeypatch.setattr(window, 'open_table', opened.append)
    window.tree_clicked(leaf(root, key), 0)
    assert opened == [key]


def test_folder_click_only_toggles_expansion_even_on_inactive_connection(tree_window, monkeypatch):
    window, load = tree_window
    root = load(['h:match:one'])
    h = folder(root, 'h')
    window.current_connection = Connection('Other connection')
    operations = []
    monkeypatch.setattr(window, 'activate_connection', lambda *args: operations.append(args))
    monkeypatch.setattr(window, 'open_table', lambda *args: operations.append(args))
    h.setExpanded(False)
    window.tree_clicked(h, 0)
    assert h.isExpanded()
    window.tree_clicked(h, 0)
    assert not h.isExpanded()
    assert not operations


def test_folder_context_menu_cannot_remove_the_connection(tree_window, monkeypatch):
    window, load = tree_window
    h = folder(load(['h:match:one']), 'h')
    monkeypatch.setattr(window.tree, 'itemAt', lambda _point: h)
    menus = []
    monkeypatch.setattr('baseskiff.ui.window.QMenu', lambda *_args: menus.append('unexpected menu'))
    window.tree_menu(QPoint())
    assert menus == []


def test_native_folder_label_and_branch_arrow_each_toggle_once(tree_window, qtbot):
    window, load = tree_window
    h = folder(load(['h:match:one']), 'h')
    window.tree.scrollToItem(h)
    rectangle = window.tree.visualItemRect(h)
    label_point = QPoint(rectangle.left() + 40, rectangle.center().y())
    qtbot.mouseClick(window.tree.viewport(), Qt.MouseButton.LeftButton, pos=label_point)
    assert h.isExpanded()
    qtbot.mouseClick(window.tree.viewport(), Qt.MouseButton.LeftButton, pos=label_point)
    assert not h.isExpanded()
    branch_point = QPoint(rectangle.left() - window.tree.indentation() // 2, rectangle.center().y())
    qtbot.mouseClick(window.tree.viewport(), Qt.MouseButton.LeftButton, pos=branch_point)
    assert h.isExpanded()
    qtbot.mouseClick(window.tree.viewport(), Qt.MouseButton.LeftButton, pos=branch_point)
    assert not h.isExpanded()


@pytest.mark.parametrize('target', ['label', 'branch'])
@pytest.mark.parametrize('expanded', [True, False])
def test_native_folder_double_click_expands_without_database_actions(tree_window, qtbot, monkeypatch,
                                                                    target, expanded):
    window, load = tree_window
    h = folder(load(['h:match:one']), 'h')
    calls = []
    monkeypatch.setattr(window, 'activate_connection', lambda *args: calls.append(args))
    monkeypatch.setattr(window, 'open_table', lambda *args: calls.append(args))
    h.setExpanded(expanded)
    rectangle = window.tree.visualItemRect(h)
    x = (rectangle.left() + 40 if target == 'label' else
         rectangle.left() - window.tree.indentation() // 2)
    position = QPoint(x, rectangle.center().y())

    double_click(window.tree.viewport(), position)
    assert h.isExpanded() is not expanded
    qtbot.mouseClick(window.tree.viewport(), Qt.MouseButton.LeftButton, pos=position)
    assert h.isExpanded() is expanded
    assert calls == []


def test_incomplete_folder_double_click_does_not_swallow_next_click(tree_window, qtbot):
    window, load = tree_window
    h = folder(load(['h:match:one']), 'h')
    rectangle = window.tree.visualItemRect(h)
    position = QPoint(rectangle.left() + 40, rectangle.center().y())
    # QTest.mouseDClick sends only the double-click event. A real double click
    # begins with the ordinary first click, which already toggles the folder.
    qtbot.mouseClick(window.tree.viewport(), Qt.MouseButton.LeftButton, pos=position)
    qtbot.mouseDClick(window.tree.viewport(), Qt.MouseButton.LeftButton,
                     pos=position)
    assert h.isExpanded()
    qtbot.mouseClick(window.tree.viewport(), Qt.MouseButton.LeftButton, pos=position)
    assert not h.isExpanded()


@pytest.mark.parametrize('target', ['connection', 'database', 'folder'])
@pytest.mark.parametrize('complete', [True, False])
def test_double_click_does_not_swallow_the_next_key_click(tree_window, qtbot, monkeypatch,
                                                         target, complete):
    window, load = tree_window
    root = load(['h:one'])
    h = folder(root, 'h')
    item = root.parent() if target == 'connection' else root if target == 'database' else h
    h.setExpanded(True)
    item.setExpanded(False)
    rectangle = window.tree.visualItemRect(item)
    position = QPoint(rectangle.left() + 40, rectangle.center().y())
    if complete:
        double_click(window.tree.viewport(), position)
    else:
        qtbot.mouseClick(window.tree.viewport(), Qt.MouseButton.LeftButton, pos=position)
        qtbot.mouseDClick(window.tree.viewport(), Qt.MouseButton.LeftButton, pos=position)
    assert item.isExpanded()
    activated, opened = [], []
    monkeypatch.setattr(window, 'activate_connection', lambda *args: activated.append(args))
    monkeypatch.setattr(window, 'open_table', opened.append)
    key = leaf(root, 'h:one')
    rectangle = window.tree.visualItemRect(key)

    qtbot.mouseClick(window.tree.viewport(), Qt.MouseButton.LeftButton,
                     pos=QPoint(rectangle.left() + 40, rectangle.center().y()))

    assert activated == []
    assert opened == ['h:one']


def test_long_nested_key_has_full_tooltip_and_horizontal_scroll(tree_window, qtbot):
    window, load = tree_window
    key = 'h:match:cart:stock:1:' + 'a very long exact redis key ' * 8
    load([key])
    window.current_table = key
    window.rebuild_tree()
    root = window.tree.topLevelItem(0)
    qtbot.waitUntil(lambda: window.tree.horizontalScrollBar().maximum() > 0)
    assert leaf(root, key).toolTip(0) == key


def test_search_keeps_matching_ancestors_and_restores_expansion_on_clear(tree_window):
    window, load = tree_window
    root = load(['h:match:4205457', 'h:match:other', 'z:other'])
    h = folder(root, 'h')
    z = folder(root, 'z')
    h.setExpanded(False)
    z.setExpanded(False)
    window.filter_tree('4205457')
    assert not h.isHidden() and h.isExpanded()
    assert not folder(h, 'match').isHidden() and folder(h, 'match').isExpanded()
    assert not leaf(root, 'h:match:4205457').isHidden()
    assert leaf(root, 'h:match:other').isHidden()
    assert z.isHidden()
    window.filter_tree('')
    assert not h.isExpanded() and not z.isExpanded()
    assert not any(item.isHidden() for item in descendants(root))
    window.filter_tree('Redis hierarchy')
    assert not any(item.isHidden() for item in descendants(root))


def test_refresh_preserves_expansion_and_reveals_a_new_current_leaf(tree_window):
    window, load = tree_window
    root = load(['h:match:one', 'z:other'])
    window.current_table = 'h:match:one'
    window.rebuild_tree()
    root = database_node(window, window.current_connection)
    assert folder(root, 'h').isExpanded()
    folder(root, 'h').setExpanded(False)
    folder(root, 'z').setExpanded(True)
    window.rebuild_tree()
    root = database_node(window, window.current_connection)
    assert not folder(root, 'h').isExpanded()
    assert folder(root, 'z').isExpanded()
    window.current_table = 'z:other'
    window.rebuild_tree()
    root = database_node(window, window.current_connection)
    assert window.tree.currentItem() is leaf(root, 'z:other')
    assert folder(root, 'z').isExpanded()


@pytest.mark.parametrize('kind', ['PostgreSQL', 'MongoDB'])
def test_other_database_catalogs_remain_flat(tree_window, kind):
    window, _load = tree_window
    connection = Connection('Other catalog', kind=kind)
    window.connections = [connection]
    window.current_connection = connection
    window._connection_tables = {connection.id: [TableInfo('a:b:c'), TableInfo('other')]}
    window.rebuild_tree()
    root = window.tree.topLevelItem(0)
    assert root.childCount() == (3 if kind == 'PostgreSQL' else 2)
    if kind == 'PostgreSQL':
        assert [root.child(index).text(0) for index in range(3)] == ['Console', 'History', 'Tables']
        tables = root.child(2)
        assert [tables.child(index).text(0) for index in range(2)] == ['a:b:c', 'other']
        assert root.child(1).data(0, KIND_ROLE) == 'sql_history'
    else:
        assert [root.child(index).text(0) for index in range(2)] == ['a:b:c', 'other']
    window.filter_tree('b:c')
    assert not root.isHidden()
    if kind == 'PostgreSQL':
        assert not tables.child(0).isHidden()
        assert tables.child(1).isHidden()
    else:
        assert not root.child(0).isHidden()
        assert root.child(1).isHidden()
