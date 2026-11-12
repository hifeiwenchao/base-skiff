"""Native Redis key folders; display grouping never changes the actual key."""
from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem

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


def append_redis_keys(root, connection, tables, empty_label, count_label):
    separator = connection.redis_separator
    folders = {}
    leaves = {}
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
                parent.addChild(folder)
                folders[path] = folder
            folder.setData(0, KEY_COUNT_ROLE, folder.data(0, KEY_COUNT_ROLE) + 1)
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
        parent.addChild(item)
        leaves[key] = item
    for folder in folders.values():
        count = folder.data(0, KEY_COUNT_ROLE)
        folder.setText(0, f'{folder.text(0)} ({count})')
        folder.setToolTip(0, f'{folder.data(0, SEARCH_ROLE)}\n{count} {count_label}')
    for parent in (root, *folders.values()):
        children = parent.takeChildren()
        children.sort(key=lambda item: (item.data(0, NODE_KIND_ROLE) != 'redis_folder',
                                        item.text(0).casefold(), item.text(0)))
        parent.addChildren(children)
    return leaves
