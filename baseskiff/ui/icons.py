"""Small, consistent vector icons, rendered locally at native pixel density."""
from functools import lru_cache

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

PATHS = {
    'database': '<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 4 16 4 16 0V5M4 12c0 4 16 4 16 0"/>',
    'grid': '<rect x="3" y="3" width="18" height="18" rx="3"/><path d="M3 9h18M9 9v12"/>',
    'table': '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9h18M3 14h18M9 9v11M15 9v11"/>',
    'code': '<path d="m8 6-6 6 6 6m8-12 6 6-6 6m-3-16-2 20"/>',
    'history': '<path d="M3 11a9 9 0 1 1 2 7M3 4v7h7m2-4v6l4 2"/>',
    'plus': '<path d="M12 5v14M5 12h14"/>',
    'search': '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
    'refresh': '<path d="M20 8a9 9 0 0 0-15-3L2 8m0-6v6h6m-4 8a9 9 0 0 0 15 3l3-3m0 6v-6h-6"/>',
    'download': '<path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5"/>',
    'trash': '<path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7m4-7v7"/>',
    'edit': '<path d="m15 4 5 5M4 15 16 3a2 2 0 0 1 5 5L9 20l-6 1z"/>',
    'chevron-right': '<path d="m9 5 7 7-7 7"/>',
    'chevron-left': '<path d="m15 5-7 7 7 7"/>',
    'chevron-down': '<path d="m5 9 7 7 7-7"/>',
    'play': '<path d="m7 4 14 8-14 8z"/>',
    'key': '<circle cx="8" cy="8" r="5"/><path d="m12 12 9 9m-5-5 3-3m-6 0 3-3"/>',
    'columns': '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M9 3v18M15 3v18"/>',
    'settings': '<path d="m10 3-1 3-3 1-3 3 2 3-1 4 4 1 2 3 3-2 4 1 1-4 3-2-2-3 1-4-4-1-2-3z"/><circle cx="12" cy="12" r="3"/>',
    'help': '<circle cx="12" cy="12" r="9"/><path d="M9 9a3 3 0 1 1 5 2l-2 1v2m0 3h.01"/>',
    'external': '<path d="M14 3h7v7m0-7L10 14M10 3H3v18h18v-7"/>',
    'filter': '<path d="M3 4h18l-7 8v7l-4 2v-9z"/>',
    'check': '<path d="m5 12 4 4L19 6"/>',
    'close': '<path d="m6 6 12 12M6 18 18 6"/>',
    'folder': '<path d="M3 6h7l2 2h9v13H3zM3 6V3h7l2 3"/>',
    'globe': '<circle cx="12" cy="12" r="9"/><ellipse cx="12" cy="12" rx="4" ry="9"/><path d="M3 12h18"/>',
    'dot': '<circle cx="12" cy="12" r="4" fill="currentColor" stroke="none"/>',
    'panel': '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M15 4v16"/>',
    'bolt': '<path d="m13 2-9 12h7l-1 8 10-13h-8z"/>',
    'leaf': '<path d="M20 3C3 2 1 13 7 18c5 5 15-1 13-15ZM5 21 16 8"/>',
    'boat': '<path d="M4 16h16l-4 5H8z" fill="currentColor" stroke="none"/><path d="M11 2v12H3zM14 5v9h7z" fill="currentColor" stroke="none"/>',
    'copy': '<rect x="8" y="8" width="13" height="13" rx="2"/><path d="M16 8V3H3v13h5"/>',
}

def icon(name: str, color: str = '#7d8580', size: int = 20) -> QIcon:
    body = PATHS.get(name, PATHS['database']).replace('currentColor', color)
    svg = f'<svg width="24" height="24" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg" fill="none" stroke="{color}" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round">{body}</svg>'
    # QIcon copies share the rendered pixels until a caller changes its copy.
    return QIcon(_render_icon(svg, size))


@lru_cache(maxsize=128)
def _render_icon(svg: str, size: int) -> QIcon:
    pixmap = QPixmap(size * 2, size * 2)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    QSvgRenderer(QByteArray(svg.encode())).render(painter)
    painter.end()
    pixmap.setDevicePixelRatio(2)
    return QIcon(pixmap)
