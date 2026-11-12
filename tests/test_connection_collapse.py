"""Connection roots collapse without losing database navigation or tree state."""
import pytest
from PySide6.QtCore import QPoint, QRect, Qt, QThreadPool
from PySide6.QtGui import QColor, QImage, QPainter

from baseskiff.models import Connection, TableInfo
from baseskiff.service import DatabaseService
from baseskiff.ui.window import MainWindow
from tests.redis_tree_helpers import key_children, seed_database
from tests.ui_mouse import double_click


@pytest.fixture
def connection_tree(qtbot, tmp_path):
    window = MainWindow(DatabaseService(tmp_path), language='en')
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: not window.busy and window.data_table.rowCount() > 0
                   and QThreadPool.globalInstance().activeThreadCount() == 0,
                   timeout=15000)

    def load(kind='PostgreSQL'):
        connection = Connection('Connection catalog', kind=kind)
        window.connections = [connection]
        window.current_connection = connection
        window.current_table = ''
        names = ['team:customers', 'team:orders'] if kind == 'Redis' else ['customers', 'orders']
        tables = [TableInfo(name) for name in names]
        if kind == 'Redis':
            seed_database(window, connection, tables)
        else:
            window._connection_tables = {connection.id: tables}
            window.rebuild_tree()
        return window.tree.topLevelItem(0)

    yield window, load
    window.close()


@pytest.mark.parametrize('kind', ['PostgreSQL', 'MongoDB', 'Redis'])
def test_connection_branch_arrow_hides_descendants_and_keeps_root(connection_tree, qtbot,
                                                                 monkeypatch, kind):
    window, load = connection_tree
    root = load(kind)
    child = root.child(0)
    calls = []
    monkeypatch.setattr(window, 'activate_connection', lambda *args: calls.append(args))
    monkeypatch.setattr(window, 'open_table', lambda *args: calls.append(args))
    rectangle = window.tree.visualItemRect(root)
    branch = QPoint(rectangle.left() - window.tree.indentation() // 2, rectangle.center().y())
    assert root.isExpanded()
    assert not window.tree.visualItemRect(child).isEmpty()

    qtbot.mouseClick(window.tree.viewport(), Qt.MouseButton.LeftButton, pos=branch)
    assert not root.isExpanded()
    assert not window.tree.visualItemRect(root).isEmpty()
    assert window.tree.visualItemRect(child).isEmpty()
    assert calls == []

    qtbot.mouseClick(window.tree.viewport(), Qt.MouseButton.LeftButton, pos=branch)
    assert root.isExpanded()
    assert not window.tree.visualItemRect(child).isEmpty()
    assert calls == []


@pytest.mark.parametrize('kind', ['PostgreSQL', 'MongoDB', 'Redis'])
def test_rebuild_preserves_deliberately_collapsed_active_connection(connection_tree, kind):
    window, load = connection_tree
    root = load(kind)
    window.current_table = 'team:customers' if kind == 'Redis' else 'customers'
    window.rebuild_tree()
    root = window.tree.topLevelItem(0)
    root.setExpanded(False)

    window.rebuild_tree()
    root = window.tree.topLevelItem(0)
    assert not root.isExpanded()
    assert not window.tree.visualItemRect(root).isEmpty()
    assert window.tree.visualItemRect(root.child(0)).isEmpty()


@pytest.mark.parametrize('kind', ['PostgreSQL', 'MongoDB', 'Redis'])
def test_child_search_temporarily_expands_connection_and_restores_collapse(connection_tree, kind):
    window, load = connection_tree
    root = load(kind)
    root.setExpanded(False)

    window.side_search.setText('customers')
    assert root.isExpanded()
    assert not root.isHidden()
    assert not window.tree.visualItemRect(root.child(0)).isEmpty()

    window.side_search.clear()
    assert not root.isExpanded()
    assert not root.isHidden()
    assert window.tree.visualItemRect(root.child(0)).isEmpty()


@pytest.mark.parametrize('active', [True, False])
@pytest.mark.parametrize('kind', ['PostgreSQL', 'MongoDB', 'Redis'])
def test_loaded_connection_label_only_toggles_without_database_actions(connection_tree, qtbot,
                                                                      monkeypatch, kind, active):
    window, load = connection_tree
    root = load(kind)
    connection = window.current_connection
    if not active:
        window.current_connection = Connection('Other database')
    current_connection = window.current_connection
    activated, opened = [], []
    monkeypatch.setattr(window, 'activate_connection', lambda *args: activated.append(args))
    monkeypatch.setattr(window, 'open_table', opened.append)
    rectangle = window.tree.visualItemRect(root)
    label = QPoint(rectangle.left() + 40, rectangle.center().y())

    qtbot.mouseClick(window.tree.viewport(), Qt.MouseButton.LeftButton, pos=label)
    assert not root.isExpanded()
    qtbot.mouseClick(window.tree.viewport(), Qt.MouseButton.LeftButton, pos=label)
    assert root.isExpanded()
    assert activated == []
    assert opened == []
    assert window.current_connection is current_connection
    assert root.data(0, Qt.ItemDataRole.UserRole) == (connection.id, '')


@pytest.mark.parametrize('loaded', [True, False])
@pytest.mark.parametrize('active', [True, False])
def test_empty_connection_label_activates_or_retries(connection_tree, qtbot, monkeypatch,
                                                   loaded, active):
    window, load = connection_tree
    root = load()
    connection = window.current_connection
    if loaded:
        window._connection_tables[connection.id] = []
    else:
        window._connection_tables.pop(connection.id)
    if not active:
        window.current_connection = Connection('Other database')
    window.rebuild_tree()
    root = window.tree.topLevelItem(0)
    activated, opened = [], []
    monkeypatch.setattr(window, 'activate_connection', lambda *args: activated.append(args))
    monkeypatch.setattr(window, 'open_table', opened.append)
    rectangle = window.tree.visualItemRect(root)
    position = QPoint(rectangle.left() + 40, rectangle.center().y())

    qtbot.mouseClick(window.tree.viewport(), Qt.MouseButton.LeftButton, pos=position)
    assert root.childCount() == 0
    assert activated == [(connection, '')]
    assert opened == []


@pytest.mark.parametrize('target', ['label', 'branch'])
@pytest.mark.parametrize('expanded', [True, False])
def test_native_connection_double_click_toggles_once_without_database_actions(connection_tree,
                                                                             qtbot,
                                                                             monkeypatch,
                                                                             target, expanded):
    window, load = connection_tree
    root = load()
    calls = []
    monkeypatch.setattr(window, 'activate_connection', lambda *args: calls.append(args))
    monkeypatch.setattr(window, 'open_table', lambda *args: calls.append(args))
    root.setExpanded(expanded)
    rectangle = window.tree.visualItemRect(root)
    x = (rectangle.left() + 40 if target == 'label' else
         rectangle.left() - window.tree.indentation() // 2)
    position = QPoint(x, rectangle.center().y())

    double_click(window.tree.viewport(), position)
    assert root.isExpanded() is not expanded
    qtbot.mouseClick(window.tree.viewport(), Qt.MouseButton.LeftButton, pos=position)
    assert root.isExpanded() is expanded
    assert calls == []


def test_incomplete_double_click_does_not_swallow_the_next_connection_click(connection_tree,
                                                                           qtbot):
    window, load = connection_tree
    root = load()
    rectangle = window.tree.visualItemRect(root)
    position = QPoint(rectangle.left() + 40, rectangle.center().y())

    qtbot.mouseClick(window.tree.viewport(), Qt.MouseButton.LeftButton, pos=position)
    qtbot.mouseDClick(window.tree.viewport(), Qt.MouseButton.LeftButton, pos=position)
    assert not root.isExpanded()
    qtbot.mouseClick(window.tree.viewport(), Qt.MouseButton.LeftButton, pos=position)
    assert root.isExpanded()


def test_language_switch_preserves_collapsed_connection_root(connection_tree):
    window, load = connection_tree
    root = load()
    root.setExpanded(False)

    window.toggle_language()
    root = window.tree.topLevelItem(0)
    assert window.language == 'zh'
    assert not root.isExpanded()
    assert not window.tree.visualItemRect(root).isEmpty()
    assert window.tree.visualItemRect(root.child(0)).isEmpty()


def test_collapsing_loaded_connection_preserves_the_live_workspace(connection_tree, qtbot):
    window, _load = connection_tree
    root = window.tree.topLevelItem(0)
    connection = window.current_connection
    table, result = window.current_table, window.current_result
    row_count = window.data_table.rowCount()
    query = 'SELECT * FROM customers WHERE plan = \'Pro\''
    window.query_editor.setPlainText(query)
    rectangle = window.tree.visualItemRect(root)

    qtbot.mouseClick(window.tree.viewport(), Qt.MouseButton.LeftButton,
                     pos=QPoint(rectangle.left() + 40, rectangle.center().y()))

    assert not root.isExpanded()
    assert window.current_connection is connection
    assert window.current_table == table
    assert window.current_result is result
    assert window.data_table.rowCount() == row_count
    assert window.query_editor.toPlainText() == query
    assert not window.busy


@pytest.mark.parametrize('direction', [Qt.LayoutDirection.LeftToRight,
                                      Qt.LayoutDirection.RightToLeft])
@pytest.mark.parametrize('expanded', [True, False])
@pytest.mark.parametrize('depth', [1, 3])
def test_branch_chevron_is_visible_next_to_its_item(connection_tree, direction,
                                                   expanded, depth):
    window, load = connection_tree
    item = load('Redis')
    if depth == 3:
        # Add another folder level so the branch paint rect includes ancestors.
        database = seed_database(window, window.current_connection, [TableInfo('team:region:customers')])
        item = key_children(database)[0]
    window.tree.setLayoutDirection(direction)
    item.setExpanded(expanded)
    indentation = window.tree.indentation()
    width = depth * indentation
    image = QImage(width, 35, QImage.Format.Format_ARGB32)
    image.fill(QColor('white'))
    painter = QPainter(image)
    window.tree.drawBranches(painter, QRect(0, 0, width, 35), window.tree.indexFromItem(item))
    painter.end()
    marked = [(x, y) for x in range(width) for y in range(image.height())
              if image.pixelColor(x, y).lightness() < 180]
    assert marked, 'Expandable tree items need a visible branch chevron'
    xs, ys = zip(*marked)
    if direction == Qt.LayoutDirection.LeftToRight:
        assert min(xs) >= width - indentation
    else:
        assert max(xs) < indentation
    if expanded:
        assert max(xs) - min(xs) > max(ys) - min(ys)
    else:
        assert max(ys) - min(ys) > max(xs) - min(xs)


def test_leaf_items_have_no_branch_chevron(connection_tree):
    window, load = connection_tree
    leaf = load().child(0)
    image = QImage(window.tree.indentation() * 2, 35, QImage.Format.Format_ARGB32)
    image.fill(QColor('white'))
    painter = QPainter(image)
    window.tree.drawBranches(painter, image.rect(), window.tree.indexFromItem(leaf))
    painter.end()

    assert all(image.pixelColor(x, y).lightness() >= 180
               for x in range(image.width()) for y in range(image.height()))
