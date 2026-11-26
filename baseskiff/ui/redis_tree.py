"""Native Redis key folders; display grouping never changes the actual key."""
from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem
from shiboken6 import isValid

from baseskiff.redis_codec import safe_text
from baseskiff.ui.icons import icon

NODE_KIND_ROLE = Qt.ItemDataRole.UserRole + 1
NODE_ID_ROLE = Qt.ItemDataRole.UserRole + 2
SEARCH_ROLE = Qt.ItemDataRole.UserRole + 3
KEY_COUNT_ROLE = Qt.ItemDataRole.UserRole + 4
SCOPE_ROLE = Qt.ItemDataRole.UserRole + 5


class ConnectionTree(QTreeWidget):
    resized = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._ignore_double_click_release = False

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.resized.emit()

    def drawBranches(self, painter, rectangle, index):
        super().drawBranches(painter, rectangle, index)
        if not self.model().hasChildren(index):
            return
        # Qt's stylesheet branch background suppresses the native indicator on
        # macOS. Draw it in the final indentation cell, next to this item.
        rtl = self.layoutDirection() == Qt.LayoutDirection.RightToLeft
        x = (rectangle.left() + self.indentation() / 2 if rtl else
             rectangle.right() + 1 - self.indentation() / 2)
        y = rectangle.top() + rectangle.height() / 2
        if self.isExpanded(index):
            points = [QPointF(x - 3.5, y - 1.5), QPointF(x, y + 2),
                      QPointF(x + 3.5, y - 1.5)]
        else:
            direction = -1 if rtl else 1
            points = [QPointF(x - 1.5 * direction, y - 3.5),
                      QPointF(x + 2 * direction, y),
                      QPointF(x - 1.5 * direction, y + 3.5)]
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor('#83927d'), 1.4, Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.drawPolyline(QPolygonF(points))
        painter.restore()

    def mouseDoubleClickEvent(self, event):
        item = self.itemAt(event.position().toPoint())
        if (event.button() == Qt.MouseButton.LeftButton and item is not None
                and self.model().hasChildren(self.indexFromItem(item))
                and item.data(0, NODE_KIND_ROLE) in {'connection', 'redis_database', 'redis_folder'}):
            # The first label click already toggles this branch. Qt's default
            # double-click expansion would immediately undo that same action.
            self._ignore_double_click_release = True
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def mousePressEvent(self, event):
        # A fresh press starts a new gesture, even if the preceding synthetic
        # double-click did not include its final release.
        self._ignore_double_click_release = False
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if (event.button() == Qt.MouseButton.LeftButton
                and self._ignore_double_click_release):
            self._ignore_double_click_release = False
            event.accept()
            return
        super().mouseReleaseEvent(event)


def iter_items(parent):
    """Walk descendants without depending on the depth of a Redis key."""
    pending = [parent.child(index) for index in reversed(range(parent.childCount()))]
    while pending:
        item = pending.pop()
        yield item
        pending.extend(item.child(index) for index in reversed(range(item.childCount())))


def _redis_item_order(item):
    kind = item.data(0, NODE_KIND_ROLE)
    rank = {'redis_search': -1, 'redis_folder': 0, 'key': 1, 'redis_load_more': 3}.get(kind, 2)
    label = getattr(item, '_redis_base_label', item.text(0))
    return rank, label.casefold(), label


def _insert_redis_item(parent, item):
    """Insert a new node without detaching existing rows or their widgets."""
    order = _redis_item_order(item)
    low, high = 0, parent.childCount()
    while low < high:
        middle = (low + high) // 2
        if _redis_item_order(parent.child(middle)) <= order:
            low = middle + 1
        else:
            high = middle
    parent.insertChild(low, item)


def reset_redis_keys(root):
    """Discard references before the owning branch is rebuilt or removed."""
    root._redis_node_cache = None


def append_redis_keys(root, connection, tables, empty_label, count_label):
    """Append a server batch without rebuilding any already loaded key nodes."""
    separator = connection.redis_separator
    scope = (connection.id, connection.database or '0', separator)
    cached = getattr(root, '_redis_node_cache', None)
    if cached is not None:
        old_scope, folders, leaves = cached
        # Qt may delete wrappers when a branch is cleared. Validate a sentinel
        # before touching any cached item, including standalone tree roots.
        sentinel = next(iter(folders.values()), next(iter(leaves.values()), None))
        if (old_scope != scope or (sentinel is not None and
                (not isValid(sentinel) or sentinel.parent() is None))):
            cached = None
    if cached is None:
        folders, leaves = {}, {}
        root._redis_node_cache = (scope, folders, leaves)
    affected = {}
    root._redis_new_folders = []
    def changed(parent):
        affected[id(parent)] = parent

    for table in tables:
        key = table.name
        if key in leaves:
            continue
        if isinstance(key, bytes):
            raw_parts = key.split(separator.encode('utf-8')) if separator else [key]
            parts = []
            for part in raw_parts:
                try:
                    parts.append(part.decode('utf-8'))
                except UnicodeDecodeError:
                    parts.append(part)
        else:
            parts = key.split(separator) if separator else [key]
        parent = root
        for depth, segment in enumerate(parts[:-1], start=1):
            path = tuple(parts[:depth])
            folder = folders.get(path)
            if folder is None:
                folder = QTreeWidgetItem([safe_text(segment) if segment else empty_label])
                folder.setIcon(0, icon('folder', '#879780', 14))
                folder.setData(0, Qt.ItemDataRole.UserRole, (connection.id, ''))
                folder.setData(0, NODE_KIND_ROLE, 'redis_folder')
                folder.setData(0, SCOPE_ROLE, (connection.id, connection.database or '0'))
                folder.setData(0, NODE_ID_ROLE, ('redis_folder', connection.id, connection.database or '0', separator, *path))
                folder.setData(0, SEARCH_ROLE, separator.join(safe_text(part) for part in path) + separator)
                folder.setData(0, KEY_COUNT_ROLE, 0)
                folder._redis_base_label = folder.text(0)
                _insert_redis_item(parent, folder)
                changed(parent)
                folders[path] = folder
                root._redis_new_folders.append(folder)
            folder.setData(0, KEY_COUNT_ROLE, folder.data(0, KEY_COUNT_ROLE) + 1)
            changed(folder)
            parent = folder
        shown_key = safe_text(key)
        item = QTreeWidgetItem([shown_key])
        item.setIcon(0, icon('key', '#9da994', 14))
        item.setData(0, Qt.ItemDataRole.UserRole, (connection.id, key))
        item.setData(0, NODE_KIND_ROLE, 'key')
        item.setData(0, SCOPE_ROLE, (connection.id, connection.database or '0'))
        item.setData(0, NODE_ID_ROLE, ('key', connection.id, connection.database or '0', key))
        item.setData(0, SEARCH_ROLE, shown_key)
        item.setToolTip(0, shown_key)
        _insert_redis_item(parent, item)
        changed(parent)
        leaves[key] = item
    for folder in affected.values():
        if folder is root or folder.data(0, NODE_KIND_ROLE) != 'redis_folder':
            continue
        count = folder.data(0, KEY_COUNT_ROLE)
        folder.setText(0, f'{folder._redis_base_label} ({count})')
        folder.setToolTip(0, f'{folder.data(0, SEARCH_ROLE)}\n{count} {count_label}')
    return leaves
