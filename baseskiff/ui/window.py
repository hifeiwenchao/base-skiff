"""BaseSkiff's native, asynchronous database workspace."""
from __future__ import annotations

import csv
import inspect
import json
import math
import re
import sys
import uuid
from dataclasses import replace
from datetime import datetime
from functools import partial
from pathlib import Path

from PySide6.QtCore import (
    QEvent,
    QItemSelection,
    QItemSelectionModel,
    QSettings,
    QSignalBlocker,
    QSize,
    QStringListModel,
    Qt,
    QThreadPool,
    QTimer,
    Signal,
)
from PySide6.QtGui import QColor, QFont, QKeySequence, QPainter, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QCompleter,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressDialog,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTabBar,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from baseskiff.adapters import MAX_REDIS_KEYS, REDIS_KEY_SEARCH_MODES
from baseskiff.cancellation import CancellationToken
from baseskiff.i18n import Translator
from baseskiff.models import DATABASE_COLORS, ColumnInfo, Connection, QueryResult, RedisKeyPage
from baseskiff.redis_codec import json_safe, safe_text
from baseskiff.service import DatabaseService
from baseskiff.ui.connection_type import ConnectionTypeDialog
from baseskiff.ui.dialogs import ConnectionDialog, RedisKeyDialog, RowDialog
from baseskiff.ui.editor import QueryEditor, QueryHighlighter
from baseskiff.ui.icons import icon
from baseskiff.ui.redis_tree import (
    NODE_ID_ROLE,
    NODE_KIND_ROLE,
    SCOPE_ROLE,
    SEARCH_ROLE,
    ConnectionTree,
    append_redis_keys,
    iter_items,
)
from baseskiff.ui.redis_value import RedisValueWidget
from baseskiff.ui.session_state import decode_workspace, encode_workspace
from baseskiff.ui.sql_structure import SqlStructureDialog
from baseskiff.ui.theme import STYLE
from baseskiff.ui.workers import Worker
from baseskiff.ui.workspace_tabs import WorkspaceTab


def display_value(value):
    if value is None:
        return 'NULL'
    if isinstance(value, (dict, list, tuple, set, bytes)):
        return json.dumps(json_safe(value), ensure_ascii=False, default=str)
    return str(value)


def label(text='', name='', parent=None):
    widget = QLabel(text, parent)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    if name:
        widget.setObjectName(name)
    return widget


def button(text='', symbol='', callback=None, name='', tooltip=''):
    widget = QPushButton(text)
    if symbol:
        widget.setIcon(icon(symbol, '#ffffff' if name == 'primaryButton' else '#7a8a7d', 16))
        widget.setIconSize(QSize(16, 16))
    if name:
        widget.setObjectName(name)
    if callback:
        widget.clicked.connect(callback)
    if tooltip:
        widget.setToolTip(tooltip)
    widget.setCursor(Qt.CursorShape.PointingHandCursor)
    return widget


class QueryRunButton(QPushButton):
    """A normal enabled button switches between run and cancel actions."""


def layout(widget, margin=0, spacing=0, horizontal=False):
    result = QHBoxLayout(widget) if horizontal else QVBoxLayout(widget)
    result.setContentsMargins(margin, margin, margin, margin)
    result.setSpacing(spacing)
    return result


class RedisSearchModeCombo(QComboBox):
    popup_requested = Signal()
    popup_closed = Signal()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.popup_open = False
        self.view().installEventFilter(self)

    def showPopup(self):
        self.popup_open = True
        self.popup_requested.emit()
        super().showPopup()

    def hidePopup(self):
        self._finish_popup()
        super().hidePopup()

    def eventFilter(self, watched, event):
        if watched is self.view() and event.type() == QEvent.Type.Hide:
            # Native Escape / outside clicks can bypass Python hidePopup.
            self._finish_popup()
        return super().eventFilter(watched, event)

    def _finish_popup(self):
        if not self.popup_open:
            return
        self.popup_open = False
        # The activated index still refers to the displayed model. Replacing
        # that model must wait until the selection signal has been delivered.
        # Qt also calls hidePopup when a closed combo is hidden/reparented.
        # Reporting those calls would make tree rebuilding schedule itself.
        QTimer.singleShot(0, self.popup_closed.emit)

    def wheelEvent(self, event):
        # Scrolling the sidebar must never select another logical database.
        event.ignore()

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        x = 9 if self.layoutDirection() == Qt.LayoutDirection.RightToLeft else self.width() - 23
        icon('chevron-down', '#82917e' if self.isEnabled() else '#b4bcb1', 14).paint(
            painter, x, (self.height() - 14) // 2, 14, 14)


class TableTabBar(QTabBar):
    """Right-click menus target tabs without activating their workspace."""

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            event.accept()
            return
        super().mouseReleaseEvent(event)


class StatusDelegate(QStyledItemDelegate):
    """Subtle text badges for common status values, with text as well as color."""
    COLORS = {
        'active': ('#eaf3e7', '#54804e'), 'completed': ('#eaf3e7', '#54804e'),
        'paid': ('#eaf3e7', '#54804e'), 'success': ('#eaf3e7', '#54804e'),
        'pending': ('#fbf2df', '#a88b45'), 'trial': ('#eef0fb', '#7c7fb0'),
        'inactive': ('#f0f1ee', '#92988b'), 'cancelled': ('#fbebE7', '#b47768'),
        'failed': ('#fbece8', '#b47768'),
    }

    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        # Qt shapes the complete display string before eliding it. Bound that
        # paint-only work; copying, tooltips, raw values and exports stay intact.
        if len(option.text) > 4096:
            option.text = option.text[:4096] + '…'

    def paint(self, painter, option, index):
        text = index.data() or ''
        if str(text).lower() not in self.COLORS:
            return super().paint(painter, option, index)
        painter.save()
        painter.fillRect(option.rect, QColor('#edf4e9') if option.state & QStyle.StateFlag.State_Selected else QColor('#ffffff' if index.row() % 2 == 0 else '#fcfdfb'))
        background, foreground = self.COLORS[str(text).lower()]
        rect = option.rect.adjusted(12, 10, -12, -10)
        rect.setWidth(min(rect.width(), painter.fontMetrics().horizontalAdvance(text) + 20))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(background))
        painter.drawRoundedRect(rect, 4, 4)
        painter.setPen(QColor(foreground))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
        painter.restore()


class MainWindow(QMainWindow):
    export_progress = Signal(object, int)
    def __init__(self, service=None, language=None):
        super().__init__()
        self.service = service or DatabaseService()
        self.settings = QSettings('BaseSkiff', 'BaseSkiff-Smoke' if getattr(service, 'ephemeral', False) else 'BaseSkiff')
        self.language = language or str(self.settings.value('language', 'zh'))
        self.translator = Translator(self.language)
        self.current_connection: Connection | None = None
        self.current_table = ''
        self.tables = []
        self.columns: list[ColumnInfo] = []
        self.connections = []
        self.page = 1
        self.page_size = 50
        self.total_rows = 0
        self.current_result = QueryResult()
        self.query_result = QueryResult()
        self.sort_column = ''
        self.descending = False
        self.where_clause = ''
        self.order_by = ''
        self._grid_updating = False
        self._tasks = []
        self._pending = 0
        self._generation = 0
        self._rows_request = 0
        self._query_running = False
        self._query_cancel_token = None
        self._export_jobs = {}
        self._sql_metadata = {}
        self._query_state = ''
        self._query_error = ''
        self._closed = False
        self._connection_tables = {}
        self._tree_expansion = {}
        self._tree_search_text = ''
        self._tree_active_key = None
        self._redis_key_searches = {}
        self._redis_search_history = {}
        self._redis_search_history_loaded = set()
        self._redis_applied_searches = {}
        self._redis_key_pages = {}
        self._redis_key_page_searches = {}
        self._redis_filtered_results = set()
        self._redis_filter_updates = {}
        self._redis_search_dirty = set()
        self._redis_key_request = 0
        self._redis_search_tokens = {}
        self._redis_search_owner = None
        self._redis_snapshot = None
        self._redis_writes = {}
        self._redis_database_catalogs = {}
        self._redis_database_requests = {}
        self._redis_database_switches = {}
        self._redis_database_targets = {}
        self._redis_database_intents = {}
        self._redis_database_errors = {}
        self._redis_key_statuses = {}
        self._tree_rebuild_pending = False
        self._tree_rebuild_scheduled = False
        self._tree_event_depth = 0
        self._redis_search_item = None
        self._history = []
        self._navigation = 'workspace'
        self._workspace_tabs = {}
        self._connection_workspaces = {}
        self._active_workspace = None
        # SQL Console is scoped by connection + database rather than by a
        # table tab.  Keep its transient result/status in memory while the
        # editor text is persisted through DatabaseService.
        self._console_workspaces = {}
        self._console_scope = None
        self._console_save_timer = None
        self.setWindowTitle('BaseSkiff — Your data, in one place')
        self.setWindowIcon(icon('boat', '#397b60', 64))
        self.resize(1440, 900)
        self.setMinimumSize(1080, 700)
        self.setStyleSheet(STYLE)
        self._build()
        self._console_save_timer = QTimer(self)
        self._console_save_timer.setSingleShot(True)
        self._console_save_timer.setInterval(350)
        self._console_save_timer.timeout.connect(self._save_console_now)
        self.export_progress.connect(self._update_export_progress)
        self._shortcuts()
        self.retranslate()
        self.connections = self.service.list_connections()
        self.rebuild_tree()
        QTimer.singleShot(0, self._open_initial)

    def t(self, zh, en):
        return zh if self.language == 'zh' else en

    @property
    def busy(self):
        return self._pending > 0

    def _build(self):
        base = QWidget()
        self.setCentralWidget(base)
        shell = layout(base, horizontal=True)
        self.shell_splitter = QSplitter(Qt.Orientation.Horizontal, objectName='workspaceSplitter')
        self.shell_splitter.setChildrenCollapsible(False)
        self.shell_splitter.setHandleWidth(6)
        shell.addWidget(self.shell_splitter)
        self.sidebar = QWidget(objectName='sidebar')
        self.sidebar.setMinimumWidth(200)
        self.sidebar.setMaximumWidth(560)
        self.sidebar.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        side = layout(self.sidebar, 18, 7)
        brand_row = QHBoxLayout()
        brand_row.setContentsMargins(6, 11, 0, 16)
        logo = label()
        logo.setPixmap(icon('boat', '#397b60', 32).pixmap(32, 32))
        brand_row.addWidget(logo)
        brand_row.addWidget(label('BaseSkiff', 'brand'))
        brand_row.addStretch()
        side.addLayout(brand_row)
        self.side_search = QLineEdit(objectName='sidebarSearch')
        self.side_search.addAction(icon('search', '#93a18f', 15), QLineEdit.ActionPosition.LeadingPosition)
        self.side_search.textChanged.connect(self.filter_tree)
        side.addWidget(self.side_search)
        side.addSpacing(15)
        self.overview_nav = button('', 'grid', self.show_overview, 'navButton')
        self.workspace_nav = button('', 'database', self.show_workspace, 'navButton')
        self.history_nav = button('', 'history', self.show_history, 'navButton')
        self.history_nav.hide()
        for nav in (self.overview_nav, self.workspace_nav):
            side.addWidget(nav)
        side.addSpacing(23)
        connection_heading = QHBoxLayout()
        self.connection_label = label('', 'eyebrow')
        connection_heading.addWidget(self.connection_label)
        connection_heading.addStretch()
        self.new_side_button = button('', 'plus', self.add_connection, 'iconButton')
        self.new_side_button.setFixedSize(26, 26)
        connection_heading.addWidget(self.new_side_button)
        side.addLayout(connection_heading)
        self._redis_search_parking = QWidget(self.sidebar)
        self._redis_search_parking.hide()
        self.redis_search_group = QWidget(self._redis_search_parking, objectName='redisKeySearch')
        redis_search_layout = layout(self.redis_search_group, 0, 6)
        self.redis_key_heading = label('', 'muted')
        redis_search_layout.addWidget(self.redis_key_heading)
        self.redis_key_search = QLineEdit(objectName='redisKeySearchInput')
        self.redis_key_search.setClearButtonEnabled(True)
        self.redis_key_search.setMinimumWidth(0)
        self.redis_key_search.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        search_row = QHBoxLayout()
        search_row.setSpacing(4)
        search_row.addWidget(self.redis_key_search, 1)
        self.redis_key_search_button = button('', 'search', self._search_redis_keys, 'redisKeySearchSubmit')
        search_row.addWidget(self.redis_key_search_button)
        self.redis_key_history_button = self.redis_key_search.addAction(
            icon('history', '#588068', 15), QLineEdit.ActionPosition.TrailingPosition)
        self.redis_key_history_button.triggered.connect(self._show_redis_search_history)
        redis_search_layout.addLayout(search_row)
        self.redis_key_mode = RedisSearchModeCombo(objectName='redisKeySearchMode')
        self.redis_key_mode.setMinimumWidth(65)
        self.redis_key_mode.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.redis_key_mode.popup_closed.connect(self._queue_tree_rebuild)
        for mode in REDIS_KEY_SEARCH_MODES:
            self.redis_key_mode.addItem(mode, mode)
        redis_search_layout.addWidget(self.redis_key_mode)
        self.redis_key_status = label('', 'muted')
        self.redis_key_status.setWordWrap(True)
        redis_search_layout.addWidget(self.redis_key_status)
        self.redis_key_search.textChanged.connect(self._redis_search_changed)
        self.redis_key_search.returnPressed.connect(self._search_redis_keys)
        self.redis_key_mode.currentIndexChanged.connect(self._redis_search_changed)
        self.redis_key_history_model = QStringListModel(self)
        self.redis_key_completer = QCompleter(self.redis_key_history_model, self)
        self.redis_key_completer.setCaseSensitivity(Qt.CaseSensitivity.CaseSensitive)
        self.redis_key_completer.setFilterMode(Qt.MatchFlag.MatchStartsWith)
        self.redis_key_completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self.redis_key_completer.activated.connect(self._select_redis_search_text)
        self.redis_key_search.setCompleter(self.redis_key_completer)
        self.redis_search_group.hide()
        self.tree = ConnectionTree()
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(17)
        self.tree.setIconSize(QSize(15, 15))
        self.tree.setUniformRowHeights(False)
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.tree.itemClicked.connect(self.tree_clicked)
        self.tree.itemExpanded.connect(self._tree_item_expanded)
        self.tree.itemCollapsed.connect(self._tree_item_collapsed)
        self.tree.resized.connect(self._resize_redis_search)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.tree_menu)
        side.addWidget(self.tree, 1)
        tip = QFrame(objectName='tip')
        tip_layout = layout(tip, 12, 8)
        tip_title = QHBoxLayout()
        tip_title.addWidget(label('✧', 'muted'))
        self.tip_title = label()
        self.tip_title.setStyleSheet('font-size: 12px; font-weight: 550; color: #6a805c;')
        tip_title.addWidget(self.tip_title, 1)
        tip_layout.addLayout(tip_title)
        self.tip_text = label('', 'muted')
        self.tip_text.setWordWrap(True)
        self.tip_text.setStyleSheet('font-size: 11px; color: #96a08e; line-height: 1.5;')
        tip_layout.addWidget(self.tip_text)
        side.addWidget(tip)
        side.addSpacing(9)
        bottom_side = QHBoxLayout()
        self.local_label = label('', 'muted')
        bottom_side.addWidget(label('◉', 'muted'))
        bottom_side.addWidget(self.local_label, 1)
        bottom_side.addWidget(button('', 'help', self.show_help, 'iconButton'))
        side.addLayout(bottom_side)
        self.shell_splitter.addWidget(self.sidebar)
        right = QWidget(objectName='workspace')
        right.setMinimumWidth(640)
        right.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        right_layout = layout(right)
        top = QWidget(objectName='topbar')
        top.setFixedHeight(57)
        top_layout = layout(top, 0, 12, True)
        top_layout.setContentsMargins(28, 0, 23, 0)
        self.breadcrumb = label('', 'muted')
        top_layout.addWidget(self.breadcrumb, 1)
        self.top_info = label('BASESKIFF  /  WORKSPACE', 'eyebrow')
        top_layout.addWidget(self.top_info)
        top_layout.addSpacing(12)
        self.language_button = button('', 'globe', self.toggle_language, 'iconButton')
        top_layout.addWidget(self.language_button)
        self.help_button = button('', 'help', self.show_help, 'iconButton')
        top_layout.addWidget(self.help_button)
        right_layout.addWidget(top)
        self.stack = QStackedWidget()
        self.stack.addWidget(self._workspace_page())
        self.stack.addWidget(self._overview_page())
        self.stack.addWidget(self._history_page())
        right_layout.addWidget(self.stack, 1)
        footer = QWidget(objectName='footer')
        footer.setFixedHeight(33)
        footer_layout = layout(footer, 0, 8, True)
        footer_layout.setContentsMargins(23, 0, 23, 0)
        self.footer_dot = label('●')
        self.footer_dot.setStyleSheet('color: #75a67c; font-size: 8px;')
        footer_layout.addWidget(self.footer_dot)
        self.footer_status = label('', 'muted')
        self.footer_status.setStyleSheet('font-size: 10px; color: #929c8d;')
        footer_layout.addWidget(self.footer_status, 1)
        self.footer_detail = label('BaseSkiff 0.1.0', 'muted')
        self.footer_detail.setStyleSheet('font-size: 10px; color: #a4ac9e;')
        footer_layout.addWidget(self.footer_detail)
        right_layout.addWidget(footer)
        self.shell_splitter.addWidget(right)
        self.shell_splitter.setStretchFactor(0, 0)
        self.shell_splitter.setStretchFactor(1, 1)
        try:
            sidebar_width = int(self.settings.value('sidebar_width', 240))
        except (TypeError, ValueError, OverflowError):
            sidebar_width = 240
        sidebar_width = max(200, min(560, sidebar_width))
        self.shell_splitter.setSizes([sidebar_width, self.width() - sidebar_width - 6])
        self.shell_splitter.handle(1).setCursor(Qt.CursorShape.SplitHCursor)
        self.shell_splitter.splitterMoved.connect(self._remember_sidebar_width)

    def _remember_sidebar_width(self, _position, _index):
        self.settings.setValue('sidebar_width', self.sidebar.width())

    def _workspace_page(self):
        page = QWidget(objectName='page')
        main = layout(page)
        self.table_tabs = TableTabBar(objectName='tableTabs')
        self.table_tabs.setDocumentMode(True)
        self.table_tabs.setTabsClosable(True)
        self.table_tabs.setMovable(True)
        self.table_tabs.setExpanding(False)
        self.table_tabs.setUsesScrollButtons(True)
        self.table_tabs.setElideMode(Qt.TextElideMode.ElideRight)
        self.table_tabs.currentChanged.connect(self._table_tab_changed)
        self.table_tabs.tabBarClicked.connect(self._table_tab_changed)
        self.table_tabs.tabCloseRequested.connect(self.close_table_tab)
        self.table_tabs.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table_tabs.customContextMenuRequested.connect(self._table_tab_menu)
        main.addWidget(self.table_tabs)
        heading_wrap = QWidget()
        self.table_heading_wrap = heading_wrap
        heading_wrap.setObjectName('workspaceHeading')
        heading_layout = layout(heading_wrap, 0, 7)
        heading_layout.setContentsMargins(30, 25, 30, 16)
        title_row = QHBoxLayout()
        title_row.setSpacing(11)
        self.table_icon = label()
        self.table_icon.setPixmap(icon('table', '#71917a', 27).pixmap(27, 27))
        title_row.addWidget(self.table_icon)
        self.table_heading = label('customers', 'heading')
        title_row.addWidget(self.table_heading)
        self.table_badge = label('TABLE', 'badge')
        title_row.addWidget(self.table_badge)
        title_row.addStretch()
        self.connection_badge = label('', 'statusBadge')
        title_row.addWidget(self.connection_badge)
        self.connection_actions = button('', 'settings', self.edit_current_connection, 'iconButton')
        title_row.addWidget(self.connection_actions)
        heading_layout.addLayout(title_row)
        self.table_subtitle = label('', 'tableSubtitle')
        heading_layout.addWidget(self.table_subtitle)
        main.addWidget(heading_wrap)
        # The object title and subtitle duplicate the selected tree item and
        # consume a full horizontal band above every table. Keep the widgets
        # for state, accessibility and existing integrations, but collapse the
        # presentation region so the data workspace starts higher.
        heading_wrap.hide()
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.setContentsMargins(29, 0, 29, 0)
        self.tabs.addTab(self._data_tab(), icon('table', '#78937a', 15), '')
        # Keep a hidden compatibility slot for callers that still select the
        # former Query index.  The editor itself lives in the independent
        # Console workspace below, so table workspaces expose only Data and
        # Structure to users.
        self.tabs.addTab(QWidget(), icon('code', '#78937a', 15), '')
        self.tabs.setTabVisible(1, False)
        self.tabs.addTab(self._schema_tab(), icon('columns', '#78937a', 15), '')
        main.addWidget(self.tabs, 1)
        self.console_page = self._query_tab()
        self.console_page.setObjectName('consolePage')
        main.addWidget(self.console_page, 1)
        self.console_page.hide()
        return page

    def _make_table(self):
        table = QTableWidget()
        table.setAlternatingRowColors(True)
        table.setShowGrid(False)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setWordWrap(False)
        table.verticalHeader().setDefaultSectionSize(43)
        table.verticalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        table.verticalHeader().setMinimumWidth(39)
        table.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        table.horizontalHeader().setStretchLastSection(True)
        table.horizontalHeader().setMinimumSectionSize(65)
        table.horizontalHeader().setDefaultSectionSize(160)
        table.setItemDelegate(StatusDelegate(table))
        table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        table.customContextMenuRequested.connect(lambda point, t=table: self.table_menu(t, point))
        return table

    def _data_tab(self):
        tab = QWidget()
        tab_layout = layout(tab)
        tab_layout.setContentsMargins(24, 0, 24, 0)
        criteria = QWidget(objectName='tableCriteriaBar')
        self.table_criteria_bar = criteria
        criteria_layout = layout(criteria, 0, 8, True)
        criteria_layout.setContentsMargins(0, 12, 0, 0)
        self.where_label = label('WHERE', 'criteriaLabel')
        criteria_layout.addWidget(self.where_label)
        self.where_edit = QLineEdit(objectName='tableWhere')
        self.where_edit.setClearButtonEnabled(True)
        self.where_edit.setPlaceholderText('id = 1')
        self.where_edit.returnPressed.connect(self.apply_table_query)
        criteria_layout.addWidget(self.where_edit, 2)
        self.order_by_label = label('ORDER BY', 'criteriaLabel')
        criteria_layout.addWidget(self.order_by_label)
        self.order_by_edit = QLineEdit(objectName='tableOrderBy')
        self.order_by_edit.setClearButtonEnabled(True)
        self.order_by_edit.setPlaceholderText('id DESC')
        self.order_by_edit.returnPressed.connect(self.apply_table_query)
        criteria_layout.addWidget(self.order_by_edit, 2)
        self.apply_table_query_button = button('', 'search', self.apply_table_query, 'secondaryButton')
        self.apply_table_query_button.setObjectName('applyTableQuery')
        criteria_layout.addWidget(self.apply_table_query_button)
        self.clear_table_query_button = button('', 'close', self.clear_table_query, 'iconButton')
        self.clear_table_query_button.setObjectName('clearTableQuery')
        criteria_layout.addWidget(self.clear_table_query_button)
        tab_layout.addWidget(criteria)
        toolbar = QWidget()
        toolbar_layout = layout(toolbar, 0, 8, True)
        toolbar_layout.setContentsMargins(0, 17, 0, 17)
        self.data_search = QLineEdit(objectName='tableSearch')
        self.data_search.setClearButtonEnabled(True)
        self.data_search.setMaximumWidth(280)
        self.data_search.addAction(icon('search', '#98a490', 15), QLineEdit.ActionPosition.LeadingPosition)
        self.search_timer = QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(350)
        self.search_timer.timeout.connect(self._search_rows)
        self.data_search.textChanged.connect(lambda: self.search_timer.start())
        toolbar_layout.addWidget(self.data_search)
        self.refresh_button = button('', 'refresh', self.refresh_data, 'iconButton')
        toolbar_layout.addWidget(self.refresh_button)
        toolbar_layout.addStretch()
        self.delete_row_button = button('', 'trash', self.delete_selected_rows, 'iconButton')
        self.delete_row_button.setEnabled(False)
        toolbar_layout.addWidget(self.delete_row_button)
        self.edit_row_button = button('', 'edit', self.edit_selected_row, 'iconButton')
        self.edit_row_button.setEnabled(False)
        toolbar_layout.addWidget(self.edit_row_button)
        self.export_button = button('', 'download', self.show_export_menu)
        self.export_button.setMinimumWidth(88)
        toolbar_layout.addWidget(self.export_button)
        self.delete_key_button = button('', 'trash', self.delete_redis_key, 'iconButton')
        self.delete_key_button.hide()
        toolbar_layout.addWidget(self.delete_key_button)
        self.add_row_button = button('', 'plus', self.add_row, 'primaryButton')
        self.add_row_button.setMinimumWidth(110)
        toolbar_layout.addWidget(self.add_row_button)
        self.revert_table_button = button('', 'undo', self.revert_table_changes, 'secondaryButton')
        self.revert_table_button.setObjectName('revertTableChanges')
        self.commit_table_button = button('', 'check', self.commit_table_changes, 'primaryButton')
        self.commit_table_button.setObjectName('commitTableChanges')
        toolbar_layout.addWidget(self.revert_table_button)
        toolbar_layout.addWidget(self.commit_table_button)
        tab_layout.addWidget(toolbar)
        self.data_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.data_table = self._make_table()
        self.data_table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked
                                        | QAbstractItemView.EditTrigger.SelectedClicked
                                        | QAbstractItemView.EditTrigger.EditKeyPressed)
        self.data_table.itemChanged.connect(self._grid_item_changed)
        self.data_table.itemSelectionChanged.connect(self.selection_changed)
        self.data_table.horizontalHeader().sectionClicked.connect(self.sort_rows)
        self.data_splitter.addWidget(self.data_table)
        self.inspector = QFrame(objectName='inspector')
        self.inspector.setMinimumWidth(225)
        self.inspector.setMaximumWidth(290)
        inspector_outer = layout(self.inspector)
        inspector_scroll = QScrollArea()
        inspector_scroll.setWidgetResizable(True)
        inspector_body = QWidget()
        self.inspector_layout = layout(inspector_body, 18, 13)
        inspector_scroll.setWidget(inspector_body)
        inspector_outer.addWidget(inspector_scroll)
        self.inspector.hide()
        self.data_splitter.addWidget(self.inspector)
        self.data_views = QStackedWidget()
        self.data_views.addWidget(self.data_splitter)
        self.redis_view = RedisValueWidget(language=self.language)
        self.redis_view.refresh_requested.connect(self.refresh_data)
        self.redis_view.mutation_requested.connect(self._mutate_redis_entry)
        self.redis_view.ttl_requested.connect(self._set_redis_ttl)
        self.data_views.addWidget(self.redis_view)
        tab_layout.addWidget(self.data_views, 1)
        pager = QWidget()
        self.data_pager = pager
        pager_layout = layout(pager, 0, 9, True)
        pager_layout.setContentsMargins(4, 12, 0, 16)
        self.record_count = label('', 'muted')
        pager_layout.addWidget(self.record_count, 1)
        self.page_size_combo = QComboBox()
        self.page_size_combo.addItems(['25', '50', '100', '250'])
        self.page_size_combo.setCurrentText('50')
        self.page_size_combo.setFixedWidth(78)
        self.page_size_combo.currentTextChanged.connect(self.change_page_size)
        pager_layout.addWidget(self.page_size_combo)
        self.page_label = label('', 'muted')
        pager_layout.addWidget(self.page_label)
        self.previous_button = button('', 'chevron-left', lambda: self.change_page(-1), 'iconButton')
        self.next_button = button('', 'chevron-right', lambda: self.change_page(1), 'iconButton')
        self.count_total_button = button('', 'refresh', self.toggle_count_total, 'textButton')
        self.count_total_button.setCheckable(True)
        pager_layout.addWidget(self.count_total_button)
        pager_layout.addWidget(self.previous_button)
        pager_layout.addWidget(self.next_button)
        self.inspector_button = button('', 'panel', self.toggle_inspector, 'iconButton')
        pager_layout.addSpacing(7)
        pager_layout.addWidget(self.inspector_button)
        tab_layout.addWidget(pager)
        return tab

    def _query_tab(self):
        page = QWidget()
        page_layout = layout(page, 0, 12)
        tools = QHBoxLayout()
        tools.setContentsMargins(0, 15, 0, 0)
        self.query_hint = label('', 'muted')
        tools.addWidget(self.query_hint, 1)
        self.query_template_button = button('', 'code', self.insert_query_template, 'textButton')
        tools.addWidget(self.query_template_button)
        self.sql_tools_button = button('SQL', 'settings', self.show_sql_tools, 'textButton')
        tools.addWidget(self.sql_tools_button)
        self.run_button = QueryRunButton()
        self.run_button.setObjectName('primaryButton')
        self.run_button.setIcon(icon('play', '#ffffff', 16))
        self.run_button.setIconSize(QSize(16, 16))
        self.run_button.clicked.connect(self.run_query)
        self.run_button.setCursor(Qt.CursorShape.PointingHandCursor)
        tools.addWidget(self.run_button)
        page_layout.addLayout(tools)
        splitter = QSplitter(Qt.Orientation.Vertical)
        self.query_editor = QueryEditor()
        self.query_editor.setMinimumHeight(130)
        self.highlighter = QueryHighlighter(self.query_editor.document())
        self.query_editor.textChanged.connect(self._console_text_changed)
        self.query_editor.execute_requested.connect(self.run_query)
        splitter.addWidget(self.query_editor)
        results = QWidget()
        results_layout = layout(results, 0, 9)
        result_heading = QHBoxLayout()
        self.query_status = label('', 'muted')
        result_heading.addWidget(self.query_status, 1)
        self.query_export_button = button('', 'download', lambda: self.show_export_menu(True), 'textButton')
        result_heading.addWidget(self.query_export_button)
        results_layout.addLayout(result_heading)
        self.query_result_table = self._make_table()
        results_layout.addWidget(self.query_result_table, 1)
        splitter.addWidget(results)
        splitter.setSizes([245, 340])
        page_layout.addWidget(splitter, 1)
        return page

    def _schema_tab(self):
        page = QWidget()
        page_layout = layout(page, 0, 15)
        self.schema_note = label('', 'muted')
        self.schema_note.setContentsMargins(3, 16, 0, 0)
        page_layout.addWidget(self.schema_note)
        self.schema_table = self._make_table()
        self.schema_table.verticalHeader().hide()
        page_layout.addWidget(self.schema_table, 1)
        return page

    def _overview_page(self):
        page = QWidget(objectName='page')
        page_layout = layout(page, 30, 20)
        title_row = QHBoxLayout()
        texts = QVBoxLayout()
        self.overview_title = label('', 'heading')
        self.overview_subtitle = label('', 'subheading')
        texts.addWidget(self.overview_title)
        texts.addWidget(self.overview_subtitle)
        title_row.addLayout(texts, 1)
        self.new_connection_button = button('', 'plus', self.add_connection, 'primaryButton')
        title_row.addWidget(self.new_connection_button)
        page_layout.addLayout(title_row)
        stats_row = QHBoxLayout()
        self.stat_values = []
        self.stat_labels = []
        for symbol in ['database', 'table', 'history']:
            card = QFrame(objectName='card')
            card_layout = layout(card, 20, 10)
            card_top = QHBoxLayout()
            caption = label('', 'muted')
            card_top.addWidget(caption, 1)
            image = label()
            image.setPixmap(icon(symbol, '#86a389', 22).pixmap(22, 22))
            card_top.addWidget(image)
            card_layout.addLayout(card_top)
            value = label('—')
            value.setStyleSheet('font-size: 29px; font-weight: 600; color: #3a5944;')
            card_layout.addWidget(value)
            self.stat_values.append(value)
            self.stat_labels.append(caption)
            stats_row.addWidget(card)
        page_layout.addLayout(stats_row)
        self.sources_heading = label('', 'eyebrow')
        page_layout.addWidget(self.sources_heading)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.connection_cards = QWidget(objectName='page')
        self.cards_layout = QGridLayout(self.connection_cards)
        self.cards_layout.setContentsMargins(0, 0, 0, 0)
        self.cards_layout.setSpacing(16)
        scroll.setWidget(self.connection_cards)
        page_layout.addWidget(scroll, 1)
        supported = QFrame(objectName='tip')
        supported_layout = layout(supported, 18, 7)
        self.supported_label = label('', 'muted')
        supported_layout.addWidget(self.supported_label)
        supported_layout.addWidget(label('PostgreSQL     ·     MySQL     ·     SQLite     ·     MongoDB     ·     Redis     ·     Oracle', 'muted'))
        page_layout.addWidget(supported)
        return page

    def _history_page(self):
        page = QWidget(objectName='page')
        page_layout = layout(page, 30, 18)
        row = QHBoxLayout()
        texts = QVBoxLayout()
        self.history_title = label('', 'heading')
        self.history_subtitle = label('', 'subheading')
        texts.addWidget(self.history_title)
        texts.addWidget(self.history_subtitle)
        row.addLayout(texts, 1)
        self.clear_history_button = button('', 'trash', self.clear_history, 'secondaryButton')
        row.addWidget(self.clear_history_button)
        page_layout.addLayout(row)
        self.history_search = QLineEdit()
        self.history_search.addAction(icon('search'), QLineEdit.ActionPosition.LeadingPosition)
        self.history_search.textChanged.connect(self.filter_history)
        page_layout.addWidget(self.history_search)
        splitter = QSplitter(Qt.Orientation.Vertical)
        self.history_table = self._make_table()
        self.history_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.history_table.itemSelectionChanged.connect(self.select_history)
        self.history_table.cellDoubleClicked.connect(self.reopen_history)
        splitter.addWidget(self.history_table)
        self.history_detail = QPlainTextEdit()
        self.history_detail.setReadOnly(True)
        splitter.addWidget(self.history_detail)
        splitter.setSizes([450, 160])
        page_layout.addWidget(splitter, 1)
        self.history_note = label('', 'muted')
        page_layout.addWidget(self.history_note)
        return page

    def retranslate(self):
        self.shell_splitter.handle(1).setToolTip(self.t('左右拖动以调整侧栏宽度', 'Drag to resize the sidebar'))
        self.redis_view.set_language(self.language)
        self.overview_nav.setText(self.t('  数据源概览', '  Overview'))
        self.workspace_nav.setText(self.t('  数据工作台', '  Workspace'))
        self.history_nav.setText(self.t('  操作历史', '  History'))
        self.side_search.setPlaceholderText(self.t('搜索连接与数据表…', 'Search connections…'))
        self.connection_label.setText(self.t('我的连接', 'CONNECTIONS'))
        self.new_side_button.setToolTip(self.t('新建连接', 'New connection'))
        for index, text in enumerate((self.t('前缀', 'Prefix'), self.t('精确', 'Exact'))):
            self.redis_key_mode.setItemText(index, text)
        self.redis_key_search.setPlaceholderText(self.t('搜索 Redis key…', 'Search Redis keys…'))
        self.redis_key_mode.setToolTip(self.t('字面前缀匹配 · 精确匹配完整键名',
                                            'Literal prefix · Exact full key'))
        self.redis_key_search_button.setToolTip(self.t('搜索（Enter）', 'Search (Enter)'))
        self.redis_key_search_button.setAccessibleName(self.t('搜索', 'Search'))
        self.redis_key_history_button.setText(self.t('搜索历史', 'Search history'))
        self.redis_key_history_button.setToolTip(self.t('搜索历史', 'Search history'))
        self.tip_title.setText(self.t('为专注而设计', 'A little more flow'))
        self.tip_text.setText(self.t('⌘ / Ctrl + Enter 运行查询\n⌘ / Ctrl + K 快速查找连接', '⌘ / Ctrl + Enter to run a query\n⌘ / Ctrl + K to find a connection'))
        self.local_label.setText(self.t('本地工作空间', 'Local workspace'))
        self.language_button.setText('中文  /  EN')
        self.language_button.setToolTip(self.t('Switch to English', '切换为中文'))
        self.help_button.setToolTip(self.t('使用帮助', 'Help & shortcuts'))
        self.data_search.setPlaceholderText(self.t('筛选当前数据表…', 'Filter table records…'))
        self.where_label.setText(self.t('WHERE', 'WHERE'))
        self.order_by_label.setText(self.t('ORDER BY', 'ORDER BY'))
        self.where_edit.setPlaceholderText(self.t('例如：status = \'Active\'', "e.g. status = 'Active'"))
        self.order_by_edit.setPlaceholderText(self.t('例如：created_at DESC', 'e.g. created_at DESC'))
        self.apply_table_query_button.setText(self.t('查询', 'Apply'))
        self.apply_table_query_button.setToolTip(self.t('执行 WHERE / ORDER BY', 'Apply WHERE / ORDER BY'))
        self.clear_table_query_button.setToolTip(self.t('清空 WHERE / ORDER BY', 'Clear WHERE / ORDER BY'))
        self.revert_table_button.setText(self.t('撤销修改', 'Revert'))
        self.commit_table_button.setText(self.t('提交', 'Commit'))
        self.refresh_button.setToolTip(self.t('刷新', 'Refresh'))
        self.delete_row_button.setToolTip(self.t('删除选中记录', 'Delete selected records'))
        self.edit_row_button.setToolTip(self.t('编辑选中记录', 'Edit selected record'))
        self.export_button.setText(self.t('导出', 'Export'))
        self.add_row_button.setText(self.t('新增记录', 'Add record'))
        self.inspector_button.setToolTip(self.t('记录详情', 'Record inspector'))
        for i, text in enumerate([self.t('数据', 'Data'), self.t('查询编辑器', 'Query'), self.t('表结构', 'Structure')]):
            self.tabs.setTabText(i, text)
        self.run_button.setText(self.t('运行查询', 'Run query'))
        self.query_template_button.setText(self.t('插入示例', 'Insert example'))
        self.query_export_button.setText(self.t('导出结果', 'Export results'))
        self.schema_note.setText(self.t('数据表字段、类型与约束。带钥匙标记的字段为主键。', 'Columns, types and constraints. A key identifies the primary key.'))
        self.overview_title.setText(self.t('你的数据，井然有序。', 'All your data. A little calmer.'))
        self.overview_subtitle.setText(self.t('连接、探索与管理数据库，让每一次操作都清晰可见。', 'Connect, explore and manage your databases in one thoughtful workspace.'))
        self.new_connection_button.setText(self.t('新建连接', 'New connection'))
        for item, text in zip(self.stat_labels, [self.t('数据源连接', 'Connections'), self.t('当前数据表', 'Tables in workspace'), self.t('已记录的操作', 'Recorded operations')]):
            item.setText(text)
        self.sources_heading.setText(self.t('你的数据源', 'YOUR DATA SOURCES'))
        self.supported_label.setText(self.t('一个轻巧的工作空间，连接你熟悉的数据源。', 'A lighter workspace for the databases you already love.'))
        self.history_title.setText(self.t('每一步，都有迹可循。', 'Every query has a story.'))
        self.history_subtitle.setText(self.t('查询、修改与执行结果，保存在本机，随时回顾。', 'Your queries, changes and results, saved locally for a clearer picture.'))
        self.history_search.setPlaceholderText(self.t('搜索语句或数据源…', 'Search queries or connections…'))
        self.history_detail.setPlaceholderText(self.t('选择一条记录查看完整语句', 'Select an operation to see the complete query'))
        self.history_note.setText(self.t('双击记录可在编辑器中重新打开。历史可能包含查询中的敏感数据，可随时清空。', 'Double-click to reopen in the editor. History may contain sensitive query values; you can clear it at any time.'))
        self.clear_history_button.setText(self.t('清空历史', 'Clear history'))
        self._update_table_tab_labels()
        self.update_context()
        self.update_pager()
        self.update_nav()
        self.render_schema()
        self.update_query_status()
        self.footer_status.setText(self.t('正在处理…', 'Working…') if self.busy else self.t('工作空间已就绪', 'Workspace ready'))
        if self.inspector.isVisible():
            self.render_inspector()

    def toggle_language(self):
        self.language = 'en' if self.language == 'zh' else 'zh'
        self.translator.set_language(self.language)
        self.settings.setValue('language', self.language)
        self.retranslate()
        self.rebuild_tree()
        if self._navigation == 'overview':
            self.render_overview()
        elif self._navigation == 'history':
            self.render_history(self._history)

    def _shortcuts(self):
        for shortcut, callback in [('Ctrl+Return', self.run_query), ('Meta+Return', self.run_query),
                                   ('Shift+Return', self.run_query), ('Ctrl+k', self.side_search.setFocus),
                                   ('Meta+k', self.side_search.setFocus), ('Ctrl+n', self.add_connection),
                                   ('Meta+n', self.add_connection), ('Ctrl+r', self.refresh_data),
                                   ('Meta+r', self.refresh_data)]:
            action = QShortcut(QKeySequence(shortcut), self)
            action.activated.connect(callback)

    def submit(self, function, callback=None, error=None):
        if self._closed:
            return
        worker = Worker(function)
        self._tasks.append(worker)
        self._pending += 1
        self.footer_status.setText(self.t('正在处理…', 'Working…'))
        def result(value):
            if not self._closed and callback:
                callback(value)
        def failed(message):
            if not self._closed:
                (error or self.show_error)(message)
        def finished():
            self._pending = max(0, self._pending - 1)
            if worker in self._tasks:
                self._tasks.remove(worker)
            if not self._closed and not self.busy:
                self.footer_status.setText(self.t('工作空间已就绪', 'Workspace ready'))
        worker.signals.result.connect(result)
        worker.signals.error.connect(failed)
        worker.signals.finished.connect(finished)
        QThreadPool.globalInstance().start(worker)

    def _open_initial(self):
        if self._restore_session():
            return
        if self.connections:
            self.activate_connection(next((c for c in self.connections if c.is_demo), self.connections[0]))
        else:
            self.show_overview()

    @staticmethod
    def _redis_scope(connection):
        if not connection or connection.kind != 'Redis':
            return None
        return connection.id, connection.database or '0'

    @staticmethod
    def _history_database(connection):
        if not connection:
            return ''
        return connection.database or (Path(connection.path).name if connection.kind == 'SQLite' else '')

    def _connection_operation(self, method, connection, *args, **kwargs):
        """Bind the selected DB before a worker is queued, including writes."""
        if connection.kind == 'Redis':
            kwargs['database'] = connection.database or '0'
        return partial(getattr(self.service, method), connection.id, *args, **kwargs)

    def _redis_database_entries(self, connection):
        catalog = self._redis_database_catalogs.get(connection.id)
        entries = ({item.index: item.key_count for item in catalog.databases} if catalog else
                   dict.fromkeys(range(16)))
        try:
            selected = int(connection.database or '0')
        except (TypeError, ValueError):
            selected = 0
        entries.setdefault(selected, None)
        if catalog is None or not catalog.complete:
            for cid, database in self._redis_key_pages:
                if cid == connection.id:
                    entries.setdefault(int(database), None)
        return entries, catalog is None or not catalog.complete

    def _load_redis_databases(self, connection=None):
        connection = connection or self.current_connection
        if not connection or connection.kind != 'Redis':
            return
        cid = connection.id
        if cid in self._redis_database_requests:
            return
        token = object()
        self._redis_database_requests[cid] = token
        def finish():
            if self._redis_database_requests.get(cid) is not token:
                return False
            self._redis_database_requests.pop(cid)
            return any(c.id == cid for c in self.connections)
        def loaded(catalog):
            if finish():
                self._redis_database_catalogs[cid] = catalog
                self.rebuild_tree()
        def failed(message):
            if finish():
                self._redis_database_errors[(cid, None)] = message
                self.rebuild_tree()
        self.submit(partial(self.service.list_redis_databases, cid), loaded, failed)

    def _queue_tree_rebuild(self):
        self._tree_rebuild_pending = True
        if self._tree_rebuild_scheduled or self.redis_key_mode.popup_open:
            return
        self._tree_rebuild_scheduled = True
        def rebuild():
            self._tree_rebuild_scheduled = False
            if not self._closed and self._tree_rebuild_pending:
                self.rebuild_tree()
        QTimer.singleShot(0, rebuild)

    def _tree_item_expanded(self, item):
        kind = item.data(0, NODE_KIND_ROLE)
        identity, scope = item.data(0, NODE_ID_ROLE), item.data(0, SCOPE_ROLE)
        self._tree_expansion[identity] = True
        self._tree_event_depth += 1
        try:
            if kind == 'redis_database':
                connection = next((c for c in self.connections if c.id == scope[0]), None)
                if connection:
                    self._select_redis_database(connection, scope[1], _load_more=True)
            elif kind == 'connection':
                connection = next((c for c in self.connections if c.id == identity[1]), None)
                if connection and connection.kind == 'Redis':
                    if connection.id not in self._redis_database_catalogs:
                        self._load_redis_databases(connection)
                    # The DB may have stayed expanded while its root was
                    # collapsed and its search host was parked during rebuild.
                    self._queue_tree_rebuild()
        finally:
            self._tree_event_depth -= 1
        self._update_redis_search_visibility()

    def _tree_item_collapsed(self, item):
        self._tree_expansion[item.data(0, NODE_ID_ROLE)] = False
        self._update_redis_search_visibility()

    def _update_redis_search_visibility(self):
        item = self._redis_search_item
        visible = (item is not None and item.data(0, SCOPE_ROLE) == self._redis_scope(self.current_connection))
        while item is not None and visible:
            visible = not item.isHidden()
            item = item.parent()
            if item is not None:
                visible = visible and item.isExpanded()
        self.redis_search_group.setVisible(visible)

    def _select_redis_database(self, connection, database, initial_table='', *, _force=False, _generation=None,
                               _load_more=False):
        database = str(database)
        scope = connection.id, database
        self._tree_expansion[('connection', connection.id)] = True
        self._tree_expansion[('redis_database', *scope)] = True
        cid = connection.id
        navigation_generation = self._generation if _generation is None else _generation
        if cid in self._redis_database_switches:
            self._redis_database_intents[cid] = (connection, database, initial_table, navigation_generation, _load_more)
            return
        if not _force and self._redis_scope(self.current_connection) == scope:
            if initial_table:
                self.open_table(initial_table)
            if scope not in self._redis_key_pages or scope in self._redis_search_dirty:
                self._request_redis_keys(replace(connection, database=database))
            elif _load_more:
                self._request_redis_keys(replace(connection, database=database), append=True)
            if self._redis_search_item is None or self._redis_search_item.data(0, SCOPE_ROLE) != scope:
                self._queue_tree_rebuild()
            return
        cid = connection.id
        token = object()
        generation = navigation_generation
        origin_scope = self._redis_scope(self.current_connection)
        origin_workspace = self._active_workspace
        self._redis_database_switches[cid] = token
        self._redis_database_targets[cid] = database
        self._redis_database_errors.pop(scope, None)
        self._queue_tree_rebuild()
        def finish():
            if self._redis_database_switches.get(cid) is not token:
                return False
            self._redis_database_switches.pop(cid)
            self._redis_database_targets.pop(cid, None)
            return any(c.id == cid for c in self.connections)
        def done(saved):
            if not finish():
                return
            self.connections = [saved if item.id == cid else item for item in self.connections]
            intent = self._redis_database_intents.pop(cid, None)
            target_table, target_generation, target_load_more = initial_table, generation, _load_more
            if intent:
                target_connection, target, target_table, target_generation, target_load_more = intent
                if target != database:
                    self._select_redis_database(target_connection, target, target_table, _force=True,
                                                _generation=target_generation, _load_more=target_load_more)
                    self.rebuild_tree()
                    return
            if target_generation == self._generation or (origin_scope is not None and
                                                         self._redis_scope(self.current_connection) == origin_scope and
                                                         self._active_workspace is origin_workspace):
                self._activate_redis_scope(saved, initial_table=target_table, _load_more=target_load_more)
            self.rebuild_tree()
        def failed(message):
            if not finish():
                return
            self._redis_database_errors[scope] = message
            intent = self._redis_database_intents.pop(cid, None)
            if intent and intent[1] != database:
                target_connection, target, table, target_generation, target_load_more = intent
                self._select_redis_database(target_connection, target, table, _force=True,
                                            _generation=target_generation, _load_more=target_load_more)
            elif generation == self._generation or (intent and intent[3] == self._generation):
                self.show_error(message)
            self.rebuild_tree()
        self.submit(partial(self.service.set_redis_database, cid, int(database)), done, failed)

    def _activate_redis_scope(self, connection, initial_table='', open_query=None, *, _load_more=False):
        self._capture_workspace()
        scope = self._workspace_scope(connection)
        state = self._connection_workspaces.get(scope)
        if state is None or state.closed:
            state = WorkspaceTab(connection)
            state.inner_tab = 1
            self._connection_workspaces[scope] = state
        state.connection = connection
        self._activate_workspace(state, capture=False)
        if initial_table:
            self.open_table(initial_table)
        if open_query is not None:
            self.query_editor.setPlainText(open_query)
            self.tabs.setCurrentIndex(1)
        if scope not in self._redis_key_pages or scope in self._redis_search_dirty:
            self._request_redis_keys(connection)
        elif _load_more:
            self._request_redis_keys(connection, append=True)

    def _park_redis_search(self):
        focus = QApplication.focusWidget()
        focused = focus if focus is not None and (focus is self.redis_search_group or
                                                  self.redis_search_group.isAncestorOf(focus)) else None
        position = self.redis_key_search.cursorPosition()
        start = self.redis_key_search.selectionStart()
        length = len(self.redis_key_search.selectedText().encode('utf-16-le')) // 2
        anchor = (start + length if position == start else start) if start >= 0 else position
        saved = self._redis_search_owner, focused, position, anchor
        self.redis_search_group.hide()
        self.redis_search_group.setParent(self._redis_search_parking)
        self._redis_search_item = None
        return saved

    def _mount_redis_search(self, database_item):
        control = QTreeWidgetItem([''])
        control.setData(0, NODE_KIND_ROLE, 'redis_search')
        scope = database_item.data(0, SCOPE_ROLE)
        control.setData(0, NODE_ID_ROLE, ('redis_search', *scope))
        control.setData(0, SCOPE_ROLE, scope)
        control.setFlags(Qt.ItemFlag.ItemIsEnabled)
        database_item.insertChild(0, control)
        host = QWidget()
        host.setAutoFillBackground(True)
        host_layout = layout(host, 0, 0)
        host_layout.addWidget(self.redis_search_group)
        self.redis_search_group.show()
        control.setSizeHint(0, QSize(120, self.redis_search_group.sizeHint().height() + 6))
        self.tree.setItemWidget(control, 0, host)
        self._redis_search_item = control
        self._resize_redis_search()

    def _resize_redis_search(self):
        if self._redis_search_item is None:
            return
        host = self.tree.itemWidget(self._redis_search_item, 0)
        if host is None:
            return
        index = self.tree.indexFromItem(self._redis_search_item)
        option = QStyleOptionViewItem()
        self.tree.initViewItemOption(option)
        self.tree.itemDelegate().initStyleOption(option, index)
        option.rect = self.tree.visualRect(index)
        content = self.tree.style().subElementRect(QStyle.SubElement.SE_ItemViewItemText,
                                                  option, self.tree)
        # Item widgets start at the style's text inset, beyond the branch
        # indentation. Bound them to the visible viewport even before the
        # native delegate has delivered its final editor geometry.
        content_left = content.left() + self.tree.horizontalOffset()
        width = max(0, self.tree.viewport().width() - max(0, content_left))
        host.setMaximumWidth(width)
        self.redis_search_group.setMaximumWidth(width)
        height = max(self.redis_search_group.heightForWidth(width), self.redis_search_group.sizeHint().height())
        self._redis_search_item.setSizeHint(0, QSize(width, height + 6))

    def rebuild_tree(self):
        if self.redis_key_mode.popup_open or self._tree_event_depth:
            self._queue_tree_rebuild()
            return
        self._tree_rebuild_pending = False
        if not self._tree_search_text:
            self._capture_tree_expansion()
        # Keep the previous filtered marker until the old tree's expansion has
        # been captured, including rebuilds deferred by an open mode popup.
        for scope, filtered in self._redis_filter_updates.items():
            if filtered:
                self._redis_filtered_results.add(scope)
            else:
                self._redis_filtered_results.discard(scope)
        self._redis_filter_updates.clear()
        search_focus = self._park_redis_search()
        scroll = self.tree.horizontalScrollBar().value(), self.tree.verticalScrollBar().value()
        active_key = ((self.current_connection.id, self.current_table, self.current_connection.database or '0')
                      if self.current_connection and self.current_table else None)
        reveal_active = active_key != self._tree_active_key
        self.tree.blockSignals(True)
        self.tree.clear()
        redis_databases = []
        for connection in self.connections:
            color = DATABASE_COLORS.get(connection.kind, '#64816c')
            root = QTreeWidgetItem([connection.name])
            root.setIcon(0, icon('database', color, 16))
            root.setData(0, Qt.ItemDataRole.UserRole, (connection.id, ''))
            root.setData(0, NODE_KIND_ROLE, 'connection')
            root.setData(0, NODE_ID_ROLE, ('connection', connection.id))
            root.setData(0, SEARCH_ROLE, connection.name)
            route = f' · SSH {connection.ssh_host}:{connection.ssh_port}' if connection.ssh_enabled else ''
            root.setToolTip(0, f'{connection.kind} · {connection.path or connection.host}{route}')
            self.tree.addTopLevelItem(root)
            if reveal_active and active_key and active_key[0] == connection.id:
                self._tree_expansion[('connection', connection.id)] = True
            leaves = {}
            if connection.kind == 'Redis':
                entries, incomplete = self._redis_database_entries(connection)
                for database, count in sorted(entries.items()):
                    scope = connection.id, str(database)
                    db = QTreeWidgetItem([f'DB{database}' + (f'  [{count:,}]' if count is not None else '')])
                    db.setIcon(0, icon('database', '#879780', 14))
                    db.setData(0, Qt.ItemDataRole.UserRole, (connection.id, ''))
                    db.setData(0, NODE_KIND_ROLE, 'redis_database')
                    identity = ('redis_database', *scope)
                    db.setData(0, NODE_ID_ROLE, identity)
                    db.setData(0, SCOPE_ROLE, scope)
                    db.setData(0, SEARCH_ROLE, f'DB{database}')
                    db.setChildIndicatorPolicy(QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator)
                    db.setToolTip(0, self._redis_database_errors.get(scope, self.t('展开以浏览此数据库中的键', 'Expand to browse keys in this database')))
                    root.addChild(db)
                    redis_databases.append(db)
                    self._tree_expansion.setdefault(identity, False)
                    if reveal_active and active_key and active_key[0] == connection.id and active_key[2] == str(database):
                        self._tree_expansion[identity] = True
                    page = self._redis_key_pages.get(scope)
                    if page is not None:
                        scoped_leaves = append_redis_keys(db, replace(connection, database=str(database)), page.keys,
                                                         self.t('（空）', '(empty)'), self.t('个已载入的键', 'loaded keys'))
                        leaves.update({(key, str(database)): item for key, item in scoped_leaves.items()})
                        if getattr(page, 'continuation', None) is not None:
                            more = QTreeWidgetItem()
                            self._configure_redis_load_more(more, scope)
                            more.setIcon(0, icon('refresh', '#71917a', 14))
                            more.setData(0, NODE_KIND_ROLE, 'redis_load_more')
                            more.setData(0, NODE_ID_ROLE, ('redis_load_more', *scope))
                            more.setData(0, SCOPE_ROLE, scope)
                            more.setData(0, SEARCH_ROLE, f'{connection.name} DB{database}')
                            more.setForeground(0, QColor('#54804e'))
                            more.setToolTip(0, self.t('保留已加载的键，再载入下一批',
                                                     'Keep loaded keys and load the next batch'))
                            db.addChild(more)
                    else:
                        pending = self._redis_database_targets.get(connection.id) == str(database)
                        hint = QTreeWidgetItem([self.t('正在连接…', 'Connecting…') if pending else
                                                self.t('展开以载入键', 'Expand to load keys')])
                        hint.setData(0, NODE_KIND_ROLE, 'redis_placeholder')
                        hint.setData(0, SCOPE_ROLE, scope)
                        hint.setFlags(Qt.ItemFlag.NoItemFlags)
                        db.addChild(hint)
                if incomplete:
                    other = QTreeWidgetItem([self.t('选择其他 DB…', 'Choose another DB…')])
                    other.setData(0, NODE_KIND_ROLE, 'redis_other_database')
                    other.setData(0, NODE_ID_ROLE, ('redis_other_database', connection.id))
                    other.setData(0, Qt.ItemDataRole.UserRole, (connection.id, ''))
                    root.addChild(other)
            else:
                tables = self._connection_tables.get(connection.id, [])
                sql_tables = None
                if connection.kind in {'SQLite', 'PostgreSQL', 'MySQL', 'Oracle'} and connection.id in self._connection_tables:
                    database = self._history_database(connection)
                    console = QTreeWidgetItem([self.t('控制台', 'Console')])
                    console.setIcon(0, icon('code', '#9da994', 14))
                    console.setData(0, Qt.ItemDataRole.UserRole, (connection.id, ''))
                    console.setData(0, NODE_KIND_ROLE, 'sql_console')
                    console.setData(0, NODE_ID_ROLE, ('sql_console', connection.id, database))
                    console.setData(0, SCOPE_ROLE, (connection.id, database))
                    console.setData(0, SEARCH_ROLE, self.t('控制台', 'Console'))
                    root.addChild(console)

                    history = QTreeWidgetItem([self.t('操作历史', 'History')])
                    history.setIcon(0, icon('history', '#9da994', 14))
                    history.setData(0, Qt.ItemDataRole.UserRole, (connection.id, ''))
                    history.setData(0, NODE_KIND_ROLE, 'sql_history')
                    history.setData(0, NODE_ID_ROLE, ('sql_history', connection.id, database))
                    history.setData(0, SCOPE_ROLE, (connection.id, database))
                    history.setData(0, SEARCH_ROLE, self.t('操作历史', 'History'))
                    root.addChild(history)

                    sql_tables = QTreeWidgetItem([self.t('数据表', 'Tables')])
                    sql_tables.setIcon(0, icon('table', '#9da994', 14))
                    sql_tables.setData(0, Qt.ItemDataRole.UserRole, (connection.id, ''))
                    sql_tables.setData(0, NODE_KIND_ROLE, 'sql_tables')
                    sql_tables.setData(0, NODE_ID_ROLE, ('sql_tables', connection.id, database))
                    sql_tables.setData(0, SCOPE_ROLE, (connection.id, database))
                    sql_tables.setData(0, SEARCH_ROLE, self.t('数据表', 'Tables'))
                    root.addChild(sql_tables)
                    self._tree_expansion.setdefault(sql_tables.data(0, NODE_ID_ROLE), True)

                table_parent = sql_tables or root
                for table in tables:
                    child = QTreeWidgetItem([table.name])
                    child.setIcon(0, icon('table', '#9da994', 14))
                    child.setData(0, Qt.ItemDataRole.UserRole, (connection.id, table.qualified_name))
                    child.setData(0, NODE_KIND_ROLE, 'table')
                    child.setData(0, NODE_ID_ROLE, ('table', connection.id, table.qualified_name))
                    child.setData(0, SEARCH_ROLE, table.name)
                    table_parent.addChild(child)
                    leaves[(table.qualified_name, connection.database or '0')] = child
            root_identity = root.data(0, NODE_ID_ROLE)
            self._tree_expansion.setdefault(root_identity, bool(self.current_connection and self.current_connection.id == connection.id))
            selected = leaves.get((active_key[1], active_key[2])) if active_key and active_key[0] == connection.id else None
            if selected is not None:
                self.tree.setCurrentItem(selected)
                parent = selected.parent()
                while parent is not None:
                    identity = parent.data(0, NODE_ID_ROLE)
                    if reveal_active or identity not in self._tree_expansion:
                        self._tree_expansion[identity] = True
                    parent = parent.parent()
            for item in (root, *iter_items(root)):
                item.setExpanded(self._tree_expansion.get(item.data(0, NODE_ID_ROLE), False))
                scope = item.data(0, SCOPE_ROLE)
                if item.data(0, NODE_KIND_ROLE) == 'redis_folder' and self._redis_applied_searches.get(scope, ('', 'prefix'))[0]:
                    item.setExpanded(True)
        self._tree_active_key = active_key
        self.tree.blockSignals(False)
        self.filter_tree(self.side_search.text())
        for database in redis_databases:
            if (database.data(0, SCOPE_ROLE) == self._redis_scope(self.current_connection)
                    and database.isExpanded() and database.parent().isExpanded()
                    and not database.isHidden() and not database.parent().isHidden()):
                self._sync_redis_search()
                self._mount_redis_search(database)
                break
        # Selecting a different key may reveal its ancestors, but must not
        # reset the user's viewport. Settle the new rows/search host before
        # restoring the bars so Qt cannot clamp them to a temporary range.
        self.tree.doItemsLayout()
        self.tree.horizontalScrollBar().setValue(scroll[0])
        self.tree.verticalScrollBar().setValue(scroll[1])
        owner, focused, position, anchor = search_focus
        if owner == self._redis_search_owner and focused is not None and self.redis_search_group.isVisible():
            if focused is self.redis_key_search:
                self.redis_key_search.setSelection(anchor, position - anchor)
            focused.setFocus(Qt.FocusReason.OtherFocusReason)

    def _capture_tree_expansion(self):
        for item in iter_items(self.tree.invisibleRootItem()):
            kind, identity = item.data(0, NODE_KIND_ROLE), item.data(0, NODE_ID_ROLE)
            if kind not in {'connection', 'redis_database', 'redis_folder', 'sql_tables'}:
                continue
            scope = item.data(0, SCOPE_ROLE)
            if kind == 'redis_folder' and (scope in self._redis_filtered_results or
                                          self._redis_applied_searches.get(scope, ('', 'prefix'))[0]):
                continue
            self._tree_expansion[identity] = item.isExpanded()

    def filter_tree(self, text):
        text = text.strip().casefold()
        blocked = self.tree.blockSignals(True)
        searching = bool(self._tree_search_text)
        if text and not searching:
            self._capture_tree_expansion()
        self._tree_search_text = text
        entries = []
        pending = [(self.tree.topLevelItem(index), False)
                   for index in reversed(range(self.tree.topLevelItemCount()))]
        while pending:
            item, inherited_match = pending.pop()
            value = item.data(0, SEARCH_ROLE) or item.text(0)
            matched = inherited_match or text in value.casefold()
            entries.append((item, matched))
            pending.extend((item.child(index), matched) for index in reversed(range(item.childCount())))
        visible = {}
        for item, matched in reversed(entries):
            child_match = any(visible[id(item.child(index))] for index in range(item.childCount()))
            visible[id(item)] = matched or child_match
            item.setHidden(not visible[id(item)])
            if text and child_match:
                item.setExpanded(True)
            elif not text and searching:
                item.setExpanded(self._tree_expansion.get(item.data(0, NODE_ID_ROLE), False))
        self.tree.blockSignals(blocked)
        self._update_redis_search_visibility()

    def tree_clicked(self, item, _column):
        kind = item.data(0, NODE_KIND_ROLE)
        if kind in {'redis_search', 'redis_placeholder'}:
            return
        if kind == 'redis_load_more':
            scope = item.data(0, SCOPE_ROLE)
            connection = next((c for c in self.connections if c.id == scope[0]), None)
            if connection:
                self._request_redis_keys(replace(connection, database=scope[1]), append=True)
            return
        if kind in {'redis_folder', 'redis_database', 'sql_tables'} or (kind == 'connection' and item.childCount()):
            item.setExpanded(not item.isExpanded())
            return
        cid, table = item.data(0, Qt.ItemDataRole.UserRole)
        connection = next((c for c in self.connections if c.id == cid), None)
        if not connection:
            return
        if kind == 'sql_history':
            scope = item.data(0, SCOPE_ROLE) or (connection.id, self._history_database(connection))
            self.show_history(connection, scope[1])
            return
        if kind == 'sql_console':
            scope = item.data(0, SCOPE_ROLE) or (connection.id, self._history_database(connection))
            self.show_console(connection, scope[1])
            return
        if kind == 'redis_other_database':
            database, accepted = QInputDialog.getInt(self, self.t('选择 Redis 数据库', 'Select Redis database'),
                                                    self.t('DB 编号', 'DB index'), 0, 0, 2147483647)
            if accepted:
                self._select_redis_database(connection, str(database))
            return
        scope = item.data(0, SCOPE_ROLE)
        if kind == 'key' and scope:
            scoped = replace(connection, database=scope[1])
            self._select_redis_database(scoped, scope[1], initial_table=table)
        elif not self.current_connection or self.current_connection.id != cid:
            self.activate_connection(connection, table)
        elif table:
            self.open_table(table)
        else:
            self.activate_connection(connection, self.current_table)

    def activate_connection(self, connection, initial_table='', open_query=None):
        self._leave_console()
        if connection.kind == 'Redis':
            if initial_table or open_query is not None:
                scope = self._redis_scope(connection)
                self._tree_expansion[('connection', connection.id)] = True
                self._tree_expansion[('redis_database', *scope)] = True
                self._activate_redis_scope(connection, initial_table, open_query)
            else:
                self._capture_workspace()
                scope = self._workspace_scope(connection)
                state = self._connection_workspaces.get(scope)
                if state is None or state.closed:
                    state = WorkspaceTab(connection)
                    state.inner_tab = 1
                    self._connection_workspaces[scope] = state
                state.connection = connection
                self._activate_workspace(state, capture=False)
            self._load_redis_databases(connection)
            return
        previous = self._active_workspace
        self._capture_workspace()
        scope = self._workspace_scope(connection)
        state = self._connection_workspaces.get(scope)
        if state is None or state.closed:
            state = WorkspaceTab(connection)
            state.inner_tab = 1
            self._connection_workspaces[scope] = state
        else:
            state.connection = connection
        self._activate_workspace(state, capture=False)
        generation = self._generation
        self.tables = []
        def loaded(tables):
            if generation != self._generation:
                return
            self.tables = tables
            self._connection_tables[connection.id] = tables
            self.rebuild_tree()
            preferred = initial_table or (previous.table if previous and previous.identity[:2] == scope else '')
            target = preferred if any(t.qualified_name == preferred for t in tables) else next((t.qualified_name for t in tables if t.name == 'customers'), tables[0].qualified_name if tables else '')
            if target:
                self.open_table(target)
            else:
                self.update_context()
                if open_query is None and not self.query_editor.toPlainText():
                    self.insert_query_template()
            if open_query is not None:
                self.show_console(connection, self._history_database(connection), content=open_query)
        def failed(message):
            if generation == self._generation:
                self.connection_badge.setText(self.t('连接失败', 'Connection failed'))
                self.table_subtitle.setText(message)
                self.show_error(message)
        self.submit(partial(self.service.list_tables, connection.id), loaded, failed)

    def _redis_search_state(self, scope):
        text, mode = self._redis_key_searches.get(scope, ('', 'prefix'))
        if mode not in REDIS_KEY_SEARCH_MODES:
            mode = 'prefix'
            self._redis_key_searches[scope] = (text, mode)
        return text, mode

    @staticmethod
    def _redis_history_value(entry, name, default=None):
        if isinstance(entry, dict):
            return entry.get(name, default)
        return getattr(entry, name, default)

    def _load_redis_search_history(self, scope):
        if scope is None or scope in self._redis_search_history_loaded:
            return
        connection_id, database = scope
        try:
            entries = list(self.service.list_redis_search_history(connection_id, database))
        except Exception:
            entries = []
        self._redis_search_history[scope] = entries
        self._redis_search_history_loaded.add(scope)
        if self._redis_scope(self.current_connection) == scope:
            self._refresh_redis_search_completer()

    def _refresh_redis_search_completer(self):
        scope = self._redis_scope(self.current_connection)
        entries = self._redis_search_history.get(scope, [])
        self.redis_key_history_model.setStringList([
            str(self._redis_history_value(entry, 'text', '')) for entry in entries
            if self._redis_history_value(entry, 'text', '') != ''
        ])

    def _select_redis_search_text(self, text):
        scope = self._redis_scope(self.current_connection)
        entry = next((item for item in self._redis_search_history.get(scope, [])
                      if self._redis_history_value(item, 'text') == text), None)
        if entry is not None:
            self._select_redis_search_history(entry)

    def _select_redis_search_history(self, entry):
        connection = self.current_connection
        scope = self._redis_scope(connection)
        if scope is None:
            return
        text = str(self._redis_history_value(entry, 'text', ''))
        mode = self._redis_history_value(entry, 'mode', 'prefix')
        if mode not in REDIS_KEY_SEARCH_MODES:
            mode = 'prefix'
        self.redis_key_search.blockSignals(True)
        self.redis_key_mode.blockSignals(True)
        self.redis_key_search.setText(text)
        self.redis_key_mode.setCurrentIndex(self.redis_key_mode.findData(mode))
        self.redis_key_search.blockSignals(False)
        self.redis_key_mode.blockSignals(False)
        self._redis_key_searches[scope] = (text, mode)
        self._search_redis_keys()

    def _toggle_redis_search_favorite(self, entry):
        identifier = self._redis_history_value(entry, 'id')
        if identifier is None:
            return
        favorite = not bool(self._redis_history_value(entry, 'is_favorite', False))
        try:
            if not self.service.set_redis_search_favorite(identifier, favorite):
                return
        except Exception as error:
            self.show_error(str(error))
            return
        if isinstance(entry, dict):
            entry['is_favorite'] = favorite
        else:
            entry.is_favorite = favorite
        self._refresh_redis_search_completer()

    def _delete_redis_search_history(self, entry):
        identifier = self._redis_history_value(entry, 'id')
        if identifier is None:
            return
        try:
            if not self.service.delete_redis_search_history(identifier):
                return
        except Exception as error:
            self.show_error(str(error))
            return
        scope = self._redis_scope(self.current_connection)
        entries = self._redis_search_history.get(scope, [])
        self._redis_search_history[scope] = [item for item in entries
                                             if self._redis_history_value(item, 'id') != identifier]
        self._refresh_redis_search_completer()

    def _show_redis_search_history(self):
        scope = self._redis_scope(self.current_connection)
        if scope is None:
            return
        self._load_redis_search_history(scope)
        menu = QMenu(self)
        entries = self._redis_search_history.get(scope, [])
        if not entries:
            empty = menu.addAction(self.t('暂无搜索历史', 'No search history'))
            empty.setEnabled(False)
        for entry in entries:
            text = str(self._redis_history_value(entry, 'text', ''))
            mode = self._redis_history_value(entry, 'mode', 'prefix')
            favorite = bool(self._redis_history_value(entry, 'is_favorite', False))
            select = menu.addAction(('★ ' if favorite else '') + text)
            select.triggered.connect(lambda _checked=False, item=entry: self._select_redis_search_history(item))
            tools = menu.addMenu(self.t('管理：', 'Manage: ') + text)
            tools.addAction(self.t('取消收藏' if favorite else '收藏',
                                   'Unfavorite' if favorite else 'Favorite'),
                            lambda item=entry: self._toggle_redis_search_favorite(item))
            tools.addAction(self.t('删除', 'Delete'),
                            lambda item=entry: self._delete_redis_search_history(item))
            select.setToolTip(self.t(f'{text} · {mode}', f'{text} · {mode}'))
        menu.exec(self.redis_key_search.mapToGlobal(
            self.redis_key_search.rect().bottomRight()))

    def _sync_redis_search(self):
        connection = self.current_connection
        if not connection or connection.kind != 'Redis':
            self.redis_search_group.hide()
            self._redis_search_owner = None
            return
        if self.redis_key_mode.popup_open:
            return
        owner = self._redis_scope(connection)
        self._load_redis_search_history(owner)
        text, mode = self._redis_search_state(owner)
        if owner != self._redis_search_owner or self.redis_key_mode.currentData() != mode:
            self._redis_search_owner = owner
            self.redis_key_search.blockSignals(True)
            self.redis_key_mode.blockSignals(True)
            self.redis_key_search.setText(text)
            self.redis_key_mode.setCurrentIndex(self.redis_key_mode.findData(mode))
            self.redis_key_search.blockSignals(False)
            self.redis_key_mode.blockSignals(False)
        database = connection.database or '0'
        self.redis_key_heading.setText(self.t(f'DB{database} 中的键', f'Keys in DB{database}'))
        self.redis_key_search.setPlaceholderText(self.t(f'搜索 DB{database} 中的键…', f'Search keys in DB{database}…'))
        status, message = self._redis_key_statuses.get(owner, ('', ''))
        if (text, mode) != self._redis_applied_searches.get(owner, ('', 'prefix')):
            text = self.t('按回车或点击搜索', 'Press Enter or click Search')
        elif status == 'loading':
            text = self.t('正在搜索…', 'Searching…')
        elif status == 'loading_more':
            count = len(self._redis_key_pages[owner].keys)
            text = self.t(f'已加载 {count:,} 个键 · 正在载入下一批（最多 {MAX_REDIS_KEYS:,} 个）…',
                          f'{count:,} keys loaded · Loading up to {MAX_REDIS_KEYS:,} more…')
        elif status == 'failed':
            text = self.t('搜索失败：', 'Search failed: ') + message
        else:
            page = self._redis_key_pages.get(owner)
            count = len(page.keys) if page is not None else 0
            text = self.t(f'已加载 {count:,} 个键 · 每次最多加载 {MAX_REDIS_KEYS:,} 个',
                          f'{count:,} keys loaded · Up to {MAX_REDIS_KEYS:,} per load') if page is not None else ''
        self.redis_key_status.setText(text)

    def _redis_search_changed(self, *_args):
        connection = self.current_connection
        if not connection or connection.kind != 'Redis':
            return
        scope = self._redis_scope(connection)
        if self._redis_search_owner != scope:
            return
        # Draft edits are local. Keep the submitted scan and its results valid
        # until Search / Enter explicitly applies the new text and mode.
        self._redis_key_searches[scope] = (self.redis_key_search.text(), self.redis_key_mode.currentData())
        self._sync_redis_search()
        self._update_redis_load_more(scope)

    def _cache_redis_key_page(self, scope, page, text, mode, *, append=False):
        # Merge by the original key, never its shortened or escaped label.
        previous = self._redis_key_pages.get(scope) if append else None
        keys = {table.name: table for table in previous.keys} if previous else {}
        keys.update((table.name, table) for table in page.keys)
        page = RedisKeyPage(list(keys.values()), page.truncated, getattr(page, 'continuation', None))
        self._redis_key_pages[scope] = page
        self._redis_key_page_searches[scope] = (text, mode)
        self._redis_key_statuses.pop(scope, None)
        active = self._redis_scope(self.current_connection) == scope
        if active:
            self.tables = page.keys
            self._connection_tables[scope[0]] = page.keys
        self._redis_filter_updates[scope] = bool(text)
        self.rebuild_tree()
        self._sync_redis_search()

    def _search_redis_keys(self, *_args):
        connection = self.current_connection
        if not connection or connection.kind != 'Redis':
            return
        scope = self._redis_scope(connection)
        if self._redis_search_owner == scope:
            self._redis_key_searches[scope] = (self.redis_key_search.text(), self.redis_key_mode.currentData())
        text, mode = self._redis_search_state(scope)
        if not self._redis_applied_searches.get(scope, ('', 'prefix'))[0] and text:
            self._capture_tree_expansion()
        self._redis_applied_searches[scope] = (text, mode)
        if text:
            try:
                recorded = self.service.record_redis_search(connection.id, connection.database or '0', text, mode)
            except Exception as error:
                self.show_error(str(error))
                recorded = None
            if recorded is not None:
                entries = self._redis_search_history.setdefault(scope, [])
                identifier = self._redis_history_value(recorded, 'id')
                entries[:] = [item for item in entries
                               if self._redis_history_value(item, 'id') != identifier
                               and self._redis_history_value(item, 'text') != text]
                entries.insert(0, recorded)
                self._redis_search_history_loaded.add(scope)
                self._refresh_redis_search_completer()
        self._refresh_redis_keys()

    def _refresh_redis_keys(self):
        """Reload the submitted filter without applying an unfinished draft."""
        connection = self.current_connection
        if not connection or connection.kind != 'Redis':
            return
        scope = self._redis_scope(connection)
        self._redis_search_dirty.add(scope)
        self._request_redis_keys(connection)

    def _can_append_redis_keys(self, scope):
        page = self._redis_key_pages.get(scope)
        return (page is not None and getattr(page, 'continuation', None) is not None
                and scope not in self._redis_search_dirty
                and self._redis_key_page_searches.get(scope) == self._redis_applied_searches.get(scope, ('', 'prefix'))
                and self._redis_search_state(scope) == self._redis_applied_searches.get(scope, ('', 'prefix'))
                and self._redis_key_statuses.get(scope, ('', ''))[0] not in {'loading', 'loading_more'})

    def _configure_redis_load_more(self, item, scope):
        loading = self._redis_key_statuses.get(scope, ('', ''))[0] == 'loading_more'
        item.setText(0, self.t('正在载入更多…', 'Loading more…') if loading else
                     self.t(f'加载更多（每批最多 {MAX_REDIS_KEYS:,} 个）…',
                            f'Load more (up to {MAX_REDIS_KEYS:,})…'))
        item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
                      if self._can_append_redis_keys(scope) else Qt.ItemFlag.NoItemFlags)

    def _update_redis_load_more(self, scope):
        # Continuation state changes only the final DB row. Keep existing keys,
        # search widgets, selection and scroll in place while the worker runs.
        for index in range(self.tree.topLevelItemCount()):
            root = self.tree.topLevelItem(index)
            if root.data(0, NODE_ID_ROLE) != ('connection', scope[0]):
                continue
            for index in range(root.childCount()):
                database = root.child(index)
                if database.data(0, SCOPE_ROLE) != scope or not database.childCount():
                    continue
                item = database.child(database.childCount() - 1)
                if item.data(0, NODE_KIND_ROLE) == 'redis_load_more':
                    self._configure_redis_load_more(item, scope)
                    return True
        return False

    def _request_redis_keys(self, connection, *, append=False):
        scope = self._redis_scope(connection)
        text, mode = self._redis_applied_searches.get(scope, ('', 'prefix'))
        if append and not self._can_append_redis_keys(scope):
            return
        if (self._redis_key_statuses.get(scope, ('', ''))[0] in {'loading', 'loading_more'} and
                scope in self._redis_search_tokens and scope not in self._redis_search_dirty):
            return
        continuation = self._redis_key_pages[scope].continuation if append else None
        if not append:
            # A fresh search owns a new scan, even if it fails or its text is
            # unchanged. An older cursor may not resume into that result set.
            self._redis_key_page_searches.pop(scope, None)
        self._redis_key_request += 1
        token = object()
        self._redis_search_tokens[scope] = token
        self._redis_search_dirty.discard(scope)
        self._redis_key_statuses[scope] = ('loading_more' if append else 'loading', '')
        self._sync_redis_search()
        if not append or not self._update_redis_load_more(scope):
            self.rebuild_tree()
        def current():
            return (self._redis_search_tokens.get(scope) is token and
                    scope not in self._redis_search_dirty and
                    self._redis_applied_searches.get(scope, ('', 'prefix')) == (text, mode) and
                    any(c.id == connection.id for c in self.connections))
        def loaded(page):
            if current():
                self._cache_redis_key_page(scope, page, text, mode, append=append)
        def failed(message):
            if current():
                self._redis_key_statuses[scope] = ('failed', message)
                self._sync_redis_search()
                if not append or not self._update_redis_load_more(scope):
                    self.rebuild_tree()
        options = {'continuation': continuation} if append else {}
        self.submit(self._connection_operation('search_redis_keys', connection, text, mode, **options), loaded, failed)

    @staticmethod
    def _workspace_scope(connection):
        return connection.id, connection.database or ('0' if connection.kind == 'Redis' else '')

    @staticmethod
    def _capture_grid(table):
        return {
            'widths': [table.columnWidth(i) for i in range(table.columnCount())],
            'selected': [i.row() for i in table.selectionModel().selectedRows()],
            'current': (table.currentRow(), table.currentColumn()),
            'scroll': (table.horizontalScrollBar().value(), table.verticalScrollBar().value()),
        }

    @staticmethod
    def _restore_grid(table, saved):
        # Individual cell selections fragment Qt's selection and repeatedly
        # rescan it through itemSelectionChanged. Restore whole row ranges once.
        with QSignalBlocker(table):
            for index, width in enumerate(saved.get('widths', [])):
                if index < table.columnCount():
                    table.setColumnWidth(index, width)
            row, column = saved.get('current', (-1, -1))
            if 0 <= row < table.rowCount() and 0 <= column < table.columnCount():
                table.setCurrentCell(row, column, QItemSelectionModel.SelectionFlag.NoUpdate)
            selection = QItemSelection()
            rows = sorted({row for row in saved.get('selected', []) if 0 <= row < table.rowCount()})
            if rows and table.columnCount():
                model = table.model()
                first = last = rows[0]
                for row in rows[1:]:
                    if row != last + 1:
                        selection.select(model.index(first, 0), model.index(last, table.columnCount() - 1))
                        first = row
                    last = row
                selection.select(model.index(first, 0), model.index(last, table.columnCount() - 1))
            table.selectionModel().select(selection, QItemSelectionModel.SelectionFlag.ClearAndSelect
                                          | QItemSelectionModel.SelectionFlag.Rows)
            x, y = saved.get('scroll', (0, 0))
            table.horizontalScrollBar().setValue(x)
            table.verticalScrollBar().setValue(y)

    def _capture_workspace(self):
        state = self._active_workspace
        if state is None or state.closed:
            self.search_timer.stop()
            return
        if self.search_timer.isActive():
            state.dirty = True
            self.page = 1
        self.search_timer.stop()
        for name in ('page', 'page_size', 'total_rows', 'columns', 'current_result',
                     'query_result', 'sort_column', 'descending'):
            setattr(state, name, getattr(self, name))
        state.search = self.data_search.text()
        state.where_draft = self.where_edit.text()
        state.order_by_draft = self.order_by_edit.text()
        state.query_text = self.query_editor.toPlainText()
        cursor = self.query_editor.textCursor()
        state.query_cursor = cursor.position(), cursor.anchor()
        state.query_running, state.query_state, state.query_error = self._query_running, self._query_state, self._query_error
        state.inner_tab = self.tabs.currentIndex()
        state.inspector = not self.inspector.isHidden()
        state.redis_snapshot = self._redis_snapshot
        state.redis_draft = (self.redis_view.string_editor.toPlainText()
                             if self._redis_snapshot and self.redis_view._editable_string() else None)
        state.redis_search = self.redis_view.entry_search.text()
        for name, table in (('data', self.data_table), ('query', self.query_result_table),
                            ('schema', self.schema_table), ('redis', self.redis_view.table)):
            state.views[name] = self._capture_grid(table)
        state.views['editor_scroll'] = (self.query_editor.horizontalScrollBar().value(),
                                        self.query_editor.verticalScrollBar().value())
        state.views['redis_editor_scroll'] = self.redis_view.string_editor.verticalScrollBar().value()

    def _workspace_state(self):
        """Also support a connection-only query editor and legacy direct callers."""
        state = self._active_workspace
        if (state is None or state.closed or not self.current_connection or
                state.identity != (*self._workspace_scope(self.current_connection), self.current_table)):
            if not self.current_connection:
                return None
            state = WorkspaceTab(self.current_connection, self.current_table)
            self._active_workspace = state
        self._capture_workspace()
        return state

    def _workspace_is_active(self, state):
        return (state is not None and state is self._active_workspace and self.current_connection is not None and
                state.identity == (*self._workspace_scope(self.current_connection), self.current_table))

    @staticmethod
    def _console_scope_for(connection, database=None):
        if not connection:
            return None
        if database is None:
            database = MainWindow._history_database(connection)
        return connection.id, '' if database is None else str(database)

    def _console_text_changed(self):
        """Keep Console edits local and persist them after a short idle period."""
        scope = self._console_scope
        if scope is None or self._closed:
            return
        state = self._console_workspaces.get(scope)
        if state is None:
            return
        state.query_text = self.query_editor.toPlainText()
        cursor = self.query_editor.textCursor()
        state.query_cursor = cursor.position(), cursor.anchor()
        if self._console_save_timer is not None:
            self._console_save_timer.start()

    def _save_console_now(self):
        if self._console_save_timer is not None:
            self._console_save_timer.stop()
        scope = self._console_scope
        if scope is None:
            return
        state = self._console_workspaces.get(scope)
        if state is None or self._closed:
            return
        content = self.query_editor.toPlainText() if self._console_scope == scope else state.query_text
        state.query_text = content
        try:
            if len(scope) == 3:
                self.service.save_console_document(scope[0], scope[1], scope[2], state.console_name or scope[2], content)
            else:
                self.service.save_console(scope[0], scope[1], content)
        except Exception as error:
            if not self._closed:
                self.show_error(str(error))

    def _leave_console(self):
        if self._console_scope is None:
            return
        self._save_console_now()
        self._console_scope = None
        self._active_workspace = None

    def show_console(self, connection, database=None, content=None, *, document_id='', document_name=''):
        """Open the persistent SQL Console for one connection/database pair."""
        if not connection or connection.kind not in {'SQLite', 'PostgreSQL', 'MySQL', 'Oracle'}:
            return
        scope = self._console_scope_for(connection, database)
        if scope is None:
            return
        if document_id:
            scope = (*scope, str(document_id))
        if self._console_scope != scope:
            self._save_console_now()
            self._capture_workspace()
            state = self._console_workspaces.get(scope)
            if state is None:
                state = WorkspaceTab(connection)
                if document_id:
                    saved = next((record for record in self.service.list_console_documents(scope[0], scope[1])
                                  if record['document_id'] == document_id), None)
                    state.query_text = saved['content'] if saved else ''
                    state.console_name = document_name or (saved['name'] if saved else document_id)
                else:
                    state.query_text = self.service.load_console(scope[0], scope[1])
                self._console_workspaces[scope] = state
            else:
                state.connection = connection
            self._active_workspace = None
            self._console_scope = scope
            self.current_connection = connection
            self.current_table = ''
            self.columns = []
            self.current_result = QueryResult()
            self.query_result = state.query_result
            self._query_running = state.query_running
            self._query_cancel_token = state.query_cancel_token
            self._query_state = state.query_state
            self._query_error = state.query_error
            self._redis_snapshot = None
            self.query_editor.blockSignals(True)
            self.query_editor.setPlainText(state.query_text)
            self.query_editor.blockSignals(False)
            cursor = self.query_editor.textCursor()
            position, anchor = state.query_cursor
            end = self.query_editor.document().characterCount() - 1
            cursor.setPosition(min(anchor, end))
            cursor.setPosition(min(position, end), cursor.MoveMode.KeepAnchor)
            self.query_editor.setTextCursor(cursor)
            self.populate_table(self.query_result_table, state.query_result)
        else:
            self.current_connection = connection
        state = self._console_workspaces[scope]
        if content is not None:
            self.query_editor.setPlainText(content)
            state.query_text = content
        self._navigation = 'workspace'
        self.stack.setCurrentIndex(0)
        self._sync_sql_metadata()
        if connection.id not in self._connection_tables:
            self.refresh_sql_metadata()
        self.update_context()
        self.update_query_status()
        self.update_nav()

    def _console_state(self):
        if self._console_scope is None:
            return None
        return self._console_workspaces.get(self._console_scope)

    def _table_tab_changed(self, index):
        if index < 0:
            return
        state = self._workspace_tabs.get(self.table_tabs.tabData(index))
        if state is not None and state is not self._active_workspace:
            if self._pending_grid_count_for(state) and not self._confirm_pending_grid(state):
                current = self._tab_index(self._active_workspace.identity) if self._active_workspace else -1
                self.table_tabs.blockSignals(True)
                self.table_tabs.setCurrentIndex(current)
                self.table_tabs.blockSignals(False)
                return
            self._leave_console()
            self._activate_workspace(state)

    @staticmethod
    def _pending_grid_count_for(state):
        if state is None or state.closed:
            return 0
        return len(state.pending_updates) + len(state.pending_inserts) + len(state.pending_deletes)

    def _confirm_pending_grid(self, state, *, discard=True):
        if not self._pending_grid_count_for(state):
            return True
        answer = QMessageBox.warning(
            self,
            self.t('有未提交修改', 'Unsaved table changes'),
            self.t('当前表格有未提交修改，是否放弃这些修改？', 'This table has uncommitted changes. Discard them?'),
            QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Discard:
            return False
        if discard:
            state.pending_updates.clear()
            state.pending_inserts.clear()
            state.pending_deletes.clear()
            state.dirty = True
        return True

    def _tab_index(self, identity):
        return next((i for i in range(self.table_tabs.count())
                     if self.table_tabs.tabData(i) == identity), -1)

    def _table_tab_menu(self, point):
        index = self.table_tabs.tabAt(point)
        if index < 0:
            return
        identity = self.table_tabs.tabData(index)
        state = self._workspace_tabs.get(identity)
        if state is None or state.closed:
            return
        def target_index():
            # Menus run a nested event loop: tabs can move, close, or reopen.
            return self._tab_index(identity) if self._workspace_tabs.get(identity) is state else -1
        def close_current():
            self.close_table_tab(target_index())
        def close_others():
            if target_index() < 0:
                return
            if state is not self._active_workspace:
                self._activate_workspace(state)
            others = [self.table_tabs.tabData(i) for i in range(self.table_tabs.count())
                      if self.table_tabs.tabData(i) != identity]
            for other in others:
                self.close_table_tab(self._tab_index(other))
        def close_all():
            if target_index() >= 0:
                self.close_all_table_tabs()
        menu = QMenu(self)
        menu.addAction(self.t('关闭当前标签页', 'Close tab'), close_current)
        action = menu.addAction(self.t('关闭其他标签页', 'Close other tabs'), close_others)
        action.setEnabled(self.table_tabs.count() > 1)
        menu.addAction(self.t('关闭所有标签页', 'Close all tabs'), close_all)
        menu.exec(self.table_tabs.mapToGlobal(point))

    def close_all_table_tabs(self):
        if not self.table_tabs.count():
            return
        if any(not self._confirm_pending_grid(state, discard=False) for state in self._workspace_tabs.values()):
            return
        self._capture_workspace()
        for state in self._workspace_tabs.values():
            state.close()
        self._workspace_tabs.clear()
        with QSignalBlocker(self.table_tabs):
            while self.table_tabs.count():
                self.table_tabs.removeTab(self.table_tabs.count() - 1)
        self._clear_workspace(self.current_connection)

    def _update_table_tab_labels(self):
        for index in range(self.table_tabs.count()):
            state = self._workspace_tabs.get(self.table_tabs.tabData(index))
            if state is None:
                continue
            connection = state.connection
            table = safe_text(state.table) if connection.kind == 'Redis' else state.table
            short = table if len(table) <= 32 else table[:29] + '…'
            scope = f'{connection.name} · DB{connection.database or "0"}' if connection.kind == 'Redis' else connection.name
            self.table_tabs.setTabText(index, f'{short} · {scope}')
            self.table_tabs.setTabToolTip(index, f'{table}\n{scope} · {connection.kind}\n{connection.database or connection.path}')
            self.table_tabs.setTabIcon(index, icon('table', DATABASE_COLORS.get(connection.kind, '#71917a'), 14))
            close_button = self.table_tabs.tabButton(index, QTabBar.ButtonPosition.RightSide)
            if close_button:
                close_button.setToolTip(self.t('关闭标签', 'Close tab'))
                close_button.setAccessibleName(self.t('关闭标签', 'Close tab'))
        self.table_tabs.setAccessibleName(self.t('已打开的数据表', 'Open tables'))

    def _activate_workspace(self, state, *, capture=True):
        if state.closed:
            return
        if capture:
            self._capture_workspace()
        self._generation += 1
        self._redis_key_request += 1
        self._active_workspace = state
        self.current_connection, self.current_table = state.connection, state.table
        if state.connection.kind == 'Redis':
            self.connections = [state.connection if c.id == state.connection.id else c for c in self.connections]
            page = self._redis_key_pages.get(self._redis_scope(state.connection))
            self.tables = page.keys if page else []
            self._connection_tables[state.connection.id] = self.tables
        else:
            self.tables = self._connection_tables.get(state.connection.id, [])
        for name in ('page', 'page_size', 'total_rows', 'columns', 'current_result',
                     'query_result', 'sort_column', 'descending'):
            setattr(self, name, getattr(state, name))
        self.where_clause, self.order_by = state.where_clause, state.order_by
        self._query_running, self._query_state, self._query_error = state.query_running, state.query_state, state.query_error
        self._query_cancel_token = state.query_cancel_token
        self._redis_snapshot = state.redis_snapshot
        for widget, value, setter in ((self.data_search, state.search, 'setText'),
                                      (self.where_edit, state.where_draft, 'setText'),
                                      (self.order_by_edit, state.order_by_draft, 'setText'),
                                      (self.page_size_combo, str(state.page_size), 'setCurrentText'),
                                      (self.query_editor, state.query_text, 'setPlainText')):
            widget.blockSignals(True)
            getattr(widget, setter)(value)
            widget.blockSignals(False)
        cursor = self.query_editor.textCursor()
        position, anchor = state.query_cursor
        end = self.query_editor.document().characterCount() - 1
        cursor.setPosition(min(anchor, end))
        cursor.setPosition(min(position, end), cursor.MoveMode.KeepAnchor)
        self.query_editor.setTextCursor(cursor)
        with QSignalBlocker(self.data_table):
            self.populate_table(self.data_table, state.current_result, (state.page - 1) * state.page_size)
        self.populate_table(self.query_result_table, state.query_result)
        self.render_schema()
        self.redis_view.set_value(state.redis_snapshot, read_only=state.connection.read_only)
        self.redis_view.entry_search.setText(state.redis_search)
        if state.redis_draft is not None and self.redis_view._editable_string():
            self.redis_view.string_editor.setPlainText(state.redis_draft)
        self.redis_view.set_busy(state.identity in self._redis_writes or state.loading)
        self.inspector.setVisible(state.inspector)
        # Keep the legacy index in memory for callers that still run a query
        # directly from a table; the Query tab itself remains hidden and all
        # normal SQL execution happens in Console.
        self.tabs.setCurrentIndex(state.inner_tab)
        header = self.data_table.horizontalHeader()
        header.setSortIndicatorShown(bool(state.sort_column))
        if state.sort_column in state.current_result.columns:
            header.setSortIndicator(state.current_result.columns.index(state.sort_column),
                                    Qt.SortOrder.DescendingOrder if state.descending else Qt.SortOrder.AscendingOrder)
        for name, table in (('data', self.data_table), ('query', self.query_result_table),
                            ('schema', self.schema_table), ('redis', self.redis_view.table)):
            self._restore_grid(table, state.views.get(name, {}))
        self.redis_view._update_controls()
        x, y = state.views.get('editor_scroll', (0, 0))
        self.query_editor.horizontalScrollBar().setValue(x)
        self.query_editor.verticalScrollBar().setValue(y)
        self.redis_view.string_editor.verticalScrollBar().setValue(state.views.get('redis_editor_scroll', 0))
        index = self._tab_index(state.identity) if state.table else -1
        self.table_tabs.blockSignals(True)
        self.table_tabs.setCurrentIndex(index)
        self.table_tabs.blockSignals(False)
        self.show_workspace()
        self.update_context()
        self.update_pager()
        self.update_query_status()
        self.selection_changed()
        self.rebuild_tree()
        if state.error:
            self.table_subtitle.setText(state.error)
        if state.schema_dirty and state.table and not state.missing:
            self._load_table_schema(state)
        if state.dirty and state.table and not state.missing:
            self.refresh_data()
        scope = self._redis_scope(state.connection)
        if scope in self._redis_search_dirty:
            self._redis_search_dirty.discard(scope)
            self._refresh_redis_keys()

    def close_table_tab(self, index):
        if not 0 <= index < self.table_tabs.count():
            return
        identity = self.table_tabs.tabData(index)
        state = self._workspace_tabs.pop(identity, None)
        if state is not None and not self._confirm_pending_grid(state):
            self._workspace_tabs[identity] = state
            return
        active = self._workspace_is_active(state)
        if active:
            self._capture_workspace()
        if state:
            state.close()
        self.table_tabs.blockSignals(True)
        self.table_tabs.removeTab(index)
        self.table_tabs.blockSignals(False)
        if active:
            self._active_workspace = None
            if self.table_tabs.count():
                selected = min(index, self.table_tabs.count() - 1)
                self._activate_workspace(self._workspace_tabs[self.table_tabs.tabData(selected)], capture=False)
            else:
                self._clear_workspace(self.current_connection)

    def _clear_workspace(self, connection=None):
        self._save_console_now()
        self._console_scope = None
        self.search_timer.stop()
        self._generation += 1
        self._redis_key_request += 1
        self._active_workspace = None
        self.current_connection = connection
        self.current_table = ''
        self.columns = []
        self.current_result, self.query_result = QueryResult(), QueryResult()
        self.page, self.total_rows = 1, 0
        self.sort_column, self.descending = '', False
        self.where_clause, self.order_by = '', ''
        self._query_running, self._query_state, self._query_error = False, '', ''
        self._query_cancel_token = None
        self._redis_snapshot = None
        self.redis_view.clear()
        self.data_search.blockSignals(True)
        self.data_search.clear()
        self.data_search.blockSignals(False)
        self.where_edit.blockSignals(True)
        self.order_by_edit.blockSignals(True)
        self.where_edit.clear()
        self.order_by_edit.clear()
        self.where_edit.blockSignals(False)
        self.order_by_edit.blockSignals(False)
        self.query_editor.clear()
        self.populate_table(self.data_table, self.current_result)
        self.populate_table(self.query_result_table, self.query_result)
        self.render_schema()
        self.inspector.hide()
        self.update_context()
        self.update_pager()
        self.update_query_status()
        self.selection_changed()
        self.rebuild_tree()

    def _load_table_schema(self, state):
        state.schema_dirty = False
        request = state.request('schema')
        def loaded(columns):
            if not state.accepts('schema', request):
                return
            state.columns = columns
            if state.connection.kind in {'SQLite', 'PostgreSQL', 'MySQL', 'Oracle'}:
                scope = self._console_scope_for(state.connection)
                self._sql_metadata.setdefault(scope, {})[str(state.table)] = [c.name for c in columns]
                self._sync_sql_metadata()
            if self._workspace_is_active(state):
                self.columns = columns
                self.render_schema()
                self.update_context()
                self.selection_changed()
        def failed(message):
            self._workspace_error(state, 'schema', request, message)
        self.submit(self._connection_operation('get_schema', state.connection, state.table), loaded, failed)

    def _workspace_error(self, state, operation, request, message):
        if not state.accepts(operation, request):
            return
        state.error = message
        if operation == 'rows':
            state.loading = False
        if self._workspace_is_active(state):
            self.redis_view.set_busy(False)
            self.show_error(message)

    def open_table(self, table):
        if not self.current_connection:
            return
        self._leave_console()
        identity = (*self._workspace_scope(self.current_connection), table)
        state = self._workspace_tabs.get(identity)
        if state is not None:
            if state is not self._active_workspace:
                self._activate_workspace(state)
            self.show_workspace()
            return
        self._capture_workspace()
        state = WorkspaceTab(self.current_connection, table)
        state.page_size = self.page_size
        state.count_total = self.current_connection.is_demo
        self._workspace_tabs[identity] = state
        self._register_table_tab(state)
        self._activate_workspace(state, capture=False)
        self.insert_query_template()
        state.query_text = self.query_editor.toPlainText()
        self._load_table_schema(state)
        self.refresh_data()

    def _register_table_tab(self, state):
        identity = state.identity
        self.table_tabs.blockSignals(True)
        index = self.table_tabs.addTab('')
        self.table_tabs.setTabData(index, identity)
        self.table_tabs.setTabButton(index, QTabBar.ButtonPosition.LeftSide, None)
        self.table_tabs.setTabButton(index, QTabBar.ButtonPosition.RightSide, None)
        close_button = button('', 'close', lambda _checked=False, key=identity:
                              self.close_table_tab(self._tab_index(key)), 'tableTabClose')
        close_button.setFixedSize(22, 22)
        close_button.setIconSize(QSize(13, 13))
        close_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.table_tabs.setTabButton(index, QTabBar.ButtonPosition.RightSide, close_button)
        self.table_tabs.blockSignals(False)
        self._update_table_tab_labels()

    def _save_session(self):
        self._capture_workspace()
        states = [self._workspace_tabs.get(self.table_tabs.tabData(i)) for i in range(self.table_tabs.count())]
        descriptors = [encode_workspace(state) for state in states if state is not None and not state.closed]
        active = self._tab_index(self._active_workspace.identity) if self._active_workspace else -1
        payload = {'version': 1, 'tabs': descriptors, 'active': active,
                   'console': list(self._console_scope) if self._console_scope else None}
        self.settings.setValue('workspaceSession', json.dumps(payload, ensure_ascii=False))

    def _restore_session(self):
        try:
            payload = json.loads(str(self.settings.value('workspaceSession', '{}')))
            if not isinstance(payload, dict) or payload.get('version') != 1:
                return False
            records = payload.get('tabs', [])
            if not isinstance(records, list):
                return False
            active = payload.get('active', 0)
            active_state = None
            for index, record in enumerate(records[:100]):
                state = decode_workspace(record, self.connections)
                if state is None or state.identity in self._workspace_tabs:
                    continue
                self._workspace_tabs[state.identity] = state
                self._register_table_tab(state)
                if index == active:
                    active_state = state
            console = payload.get('console')
            if isinstance(console, list) and len(console) in (2, 3):
                connection = next((c for c in self.connections if c.id == console[0]), None)
                if connection and connection.kind in {'SQLite', 'PostgreSQL', 'MySQL', 'Oracle'}:
                    self.show_console(connection, console[1], document_id=console[2] if len(console) == 3 else '')
                    return True
            if self._workspace_tabs:
                self._activate_workspace(active_state or next(iter(self._workspace_tabs.values())), capture=False)
                return True
        except (ValueError, TypeError, KeyError):
            pass
        return False

    def refresh_data(self, *, force=False):
        if not self.current_connection or not self.current_table:
            return
        state = self._workspace_state()
        if state.missing:
            return
        if (not force and self.current_connection.kind in {'SQLite', 'PostgreSQL', 'MySQL', 'Oracle'}
                and self._pending_grid_count()):
            return
        state.dirty = False
        if self.current_connection.kind == 'Redis':
            self._refresh_redis_value(state)
            return
        self._fetch_tab_rows(state)

    def _fetch_tab_rows(self, state):
        connection, table = state.connection, state.table
        page, page_size = state.page, state.page_size
        search, sort_column, descending = state.search, state.sort_column, state.descending
        where_clause, order_by = state.where_clause, state.order_by
        signature = (search, sort_column, descending, where_clause, order_by, page_size)
        if state.pagination_signature != signature or page == 1:
            state.page_cursors.clear()
            state.pagination_signature = signature
        request = state.request('rows')
        self._rows_request += 1
        def loaded(result):
            if not state.accepts('rows', request):
                return
            if self._pending_grid_count_for(state):
                deleted = {self._grid_key(item, state.columns) for item in state.pending_deletes}
                updates = {key: record['values'] for key, record in state.pending_updates.items()}
                result.rows = [row for row in result.rows if self._grid_key(row, state.columns) not in deleted]
                for row in result.rows:
                    values = updates.get(self._grid_key(row, state.columns), {})
                    row.update(values)
                result.total = max(0, result.total - len(deleted) + len(state.pending_inserts))
            state.current_result, state.total_rows, state.error = result, result.total, ''
            if result.next_cursor is not None:
                state.page_cursors[page + 1] = result.next_cursor
            last_page = max(1, math.ceil(result.total / page_size))
            if result.total_known and state.page > last_page:
                state.page = last_page
                if self._workspace_is_active(state):
                    self.page = last_page
                self._fetch_tab_rows(state)
                return
            if self._workspace_is_active(state):
                self.current_result, self.total_rows = result, result.total
                self.populate_table(self.data_table, result, (page - 1) * page_size)
                self.update_pager()
                self.update_context()
                self.footer_detail.setText(f'{result.elapsed_ms:.1f} ms  ·  {connection.kind}  ·  BaseSkiff 0.1.0')
                self.selection_changed()
        kwargs = dict(page=page, page_size=page_size, search=search, sort_column=sort_column,
                      descending=descending, where_clause=where_clause, order_by=order_by)
        if connection.kind in {'SQLite', 'PostgreSQL', 'MySQL', 'Oracle'}:
            kwargs.update(count_total=('exact' if state.count_total and not connection.is_demo else state.count_total), after_key=state.page_cursors.get(page))
        self.submit(partial(self.service.fetch_rows, connection.id, table, **kwargs), loaded,
                    lambda message: self._workspace_error(state, 'rows', request, message))

    def _refresh_redis_value(self, state=None):
        state = state or self._workspace_state()
        if state is None:
            return
        key, connection = state.table, state.connection
        request = state.request('rows')
        self._rows_request += 1
        state.redis_draft = None
        state.loading = True
        if self._workspace_is_active(state):
            self.redis_view.set_busy(True)
        def loaded(snapshot):
            if not state.accepts('rows', request):
                return
            state.redis_snapshot, state.error, state.loading = snapshot, '', False
            state.redis_draft = None
            rows = [] if snapshot.kind == 'none' else [{'key': key, 'type': snapshot.kind, 'value': snapshot.value}]
            state.current_result = QueryResult(columns=['key', 'type', 'value'], rows=rows,
                                               total=len(rows), truncated=snapshot.truncated)
            state.total_rows = len(rows)
            if self._workspace_is_active(state):
                self._redis_snapshot = snapshot
                self.redis_view.set_value(snapshot, read_only=connection.read_only)
                self.redis_view.set_busy(state.identity in self._redis_writes)
                self.current_result, self.total_rows = state.current_result, state.total_rows
                self.populate_table(self.data_table, self.current_result)
                self.footer_detail.setText(f'{snapshot.kind.upper()} · Redis · BaseSkiff 0.1.0')
                self.update_context()
                self.update_pager()
                self.selection_changed()
        self.submit(self._connection_operation('read_redis_key', connection, key), loaded,
                    lambda message: self._workspace_error(state, 'rows', request, message))

    def _mutate_redis_entry(self, action, payload):
        connection, snapshot = self.current_connection, self._redis_snapshot
        identity = (*self._redis_scope(connection), snapshot.key) if connection and connection.kind == 'Redis' and snapshot else None
        if (not connection or connection.kind != 'Redis' or connection.read_only or not snapshot
                or snapshot.key != self.current_table or identity in self._redis_writes):
            return
        state = self._workspace_state()
        key = snapshot.key
        token = object()
        self._redis_writes[identity] = token
        self.redis_view.set_busy(True)
        def finish():
            if self._redis_writes.get(identity) is token:
                self._redis_writes.pop(identity)
            return not state.closed and self._workspace_is_active(state)
        def done(result):
            active = finish()
            target = self._workspace_tabs.get(identity, state)
            if not target.closed:
                target.dirty = True
            active = active or self._workspace_is_active(target)
            if active:
                self.footer_detail.setText(self.t('已保存 Redis 数据', 'Redis value saved'))
                self.refresh_data()
                self._refresh_redis_keys()
        def failed(message):
            active = finish()
            if state.closed:
                return
            state.error = message
            if active:
                self.redis_view.set_busy(False)
                if not self.redis_view.restore_failed_edit(message):
                    self.show_error(message)
        self.submit(self._connection_operation('mutate_redis_entry', connection, key, snapshot.kind, action, dict(payload)), done, failed)

    def _set_redis_ttl(self, seconds):
        connection, snapshot = self.current_connection, self._redis_snapshot
        identity = (*self._redis_scope(connection), snapshot.key) if connection and connection.kind == 'Redis' and snapshot else None
        if (not connection or connection.kind != 'Redis' or connection.read_only or not snapshot
                or snapshot.key != self.current_table or identity in self._redis_writes):
            return
        state = self._workspace_state()
        key, expected_ttl_ms = snapshot.key, snapshot.ttl_ms
        token = object()
        self._redis_writes[identity] = token
        self.redis_view.set_busy(True)

        def finish():
            if self._redis_writes.get(identity) is token:
                self._redis_writes.pop(identity)
            return not state.closed and self._workspace_is_active(state)

        def done(_result):
            active = finish()
            target = self._workspace_tabs.get(identity, state)
            if not target.closed:
                target.dirty = True
            active = active or self._workspace_is_active(target)
            if active:
                self.footer_detail.setText(self.t('TTL 已更新', 'TTL updated'))
                self.refresh_data()

        def failed(message):
            active = finish()
            if state.closed:
                return
            state.error = message
            if active:
                self.redis_view.set_busy(False)
                self.show_error(message)

        self.submit(self._connection_operation(
            'set_redis_ttl', connection, key, seconds,
            expected_ttl_ms=expected_ttl_ms,
        ), done, failed)

    def populate_table(self, table, result, offset=0):
        self._grid_updating = True
        table.setUpdatesEnabled(False)
        try:
            table.clear()
            table.setColumnCount(len(result.columns))
            table.setRowCount(len(result.rows))
            table.setHorizontalHeaderLabels(result.columns)
            table.setVerticalHeaderLabels([str(offset + i + 1) for i in range(len(result.rows))])
            mono = QFont('Menlo' if sys.platform == 'darwin' else 'Consolas', 11)
            mono.setPixelSize(12)
            editable_sql = bool(table is self.data_table and self.current_connection
                                and self.current_connection.kind in {'SQLite', 'PostgreSQL', 'MySQL', 'Oracle'}
                                and not self.current_connection.read_only
                                and any(column.primary_key for column in self.columns))
            primary_keys = {column.name for column in self.columns if column.primary_key}
            for i, row in enumerate(result.rows):
                for j, column in enumerate(result.columns):
                    value = row.get(column)
                    text = display_value(value)
                    item = QTableWidgetItem(text)
                    item.setToolTip(text)
                    item.setData(Qt.ItemDataRole.UserRole, value)
                    if editable_sql:
                        if column in primary_keys:
                            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                        else:
                            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
                    if value is None:
                        item.setForeground(QColor('#b6beb0'))
                        font = QFont(mono)
                        font.setItalic(True)
                        item.setFont(font)
                    elif column in ('id', 'email', 'created_at', 'updated_at') or isinstance(value, (int, float)):
                        item.setFont(mono)
                        item.setForeground(QColor('#8e9b83' if column == 'id' else '#6c7c62'))
                    if isinstance(value, (int, float)):
                        item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                    table.setItem(i, j, item)
            for j, column in enumerate(result.columns):
                if column in ('id', '_id'):
                    table.setColumnWidth(j, 72 if column == 'id' else 220)
                elif column == 'email':
                    table.setColumnWidth(j, 225)
                elif column in ('status', 'plan', 'quantity', 'price'):
                    table.setColumnWidth(j, 105)
                elif column in ('created_at', 'updated_at'):
                    table.setColumnWidth(j, 160)
                else:
                    table.setColumnWidth(j, 155)
        finally:
            table.setUpdatesEnabled(True)
            self._grid_updating = False

    def _search_rows(self):
        self.page = 1
        self.refresh_data()

    def apply_table_query(self):
        connection = self.current_connection
        if not connection or connection.kind not in {'SQLite', 'PostgreSQL', 'MySQL', 'Oracle'} or not self.current_table:
            return
        state = self._workspace_state()
        if state is None:
            return
        if self._pending_grid_count_for(state):
            self.show_error(self.t('请先提交或撤销当前修改，再执行新的筛选。', 'Commit or revert the current edits before applying a new filter.'))
            return
        state.where_clause = self.where_edit.text().strip()
        state.order_by = self.order_by_edit.text().strip()
        state.where_draft = self.where_edit.text()
        state.order_by_draft = self.order_by_edit.text()
        self.where_clause, self.order_by = state.where_clause, state.order_by
        state.page = self.page = 1
        self.refresh_data()

    def clear_table_query(self):
        self.where_edit.clear()
        self.order_by_edit.clear()
        self.apply_table_query()

    def sort_rows(self, index):
        if index >= len(self.current_result.columns):
            return
        column = self.current_result.columns[index]
        self.descending = not self.descending if column == self.sort_column else False
        self.sort_column = column
        if self.current_connection and self.current_connection.kind in {'SQLite', 'PostgreSQL', 'MySQL', 'Oracle'}:
            quote_char = '`' if self.current_connection.kind == 'MySQL' else '"'
            quoted = quote_char + column.replace(quote_char, quote_char * 2) + quote_char
            self.order_by_edit.setText(f"{quoted} {'DESC' if self.descending else 'ASC'}")
            state = self._workspace_state()
            if state:
                state.order_by = self.order_by = self.order_by_edit.text()
                state.order_by_draft = self.order_by_edit.text()
        self.data_table.horizontalHeader().setSortIndicator(index, Qt.SortOrder.DescendingOrder if self.descending else Qt.SortOrder.AscendingOrder)
        self.data_table.horizontalHeader().setSortIndicatorShown(True)
        self.refresh_data()

    def change_page(self, delta):
        result = self.current_result
        if delta > 0 and not result.total_known and not result.has_more:
            return
        maximum = max(1, math.ceil(self.total_rows / self.page_size)) if result.total_known else self.page + 1
        self.page = min(max(1, self.page + delta), maximum)
        self.refresh_data()

    def change_page_size(self, text):
        self.page_size = int(text)
        self.page = 1
        self.refresh_data()

    def toggle_count_total(self):
        state = self._workspace_state()
        if state is None:
            return
        state.count_total = self.count_total_button.isChecked()
        self.refresh_data()

    def update_pager(self):
        result = self.current_result
        first = (self.page - 1) * self.page_size + 1 if result.rows else 0
        last = (self.page - 1) * self.page_size + len(result.rows) if result.rows else 0
        if result.total_known:
            self.record_count.setText(self.t(f'显示 {first}–{last} 条，共 {self.total_rows:,} 条记录', f'Showing {first}–{last} of {self.total_rows:,} records'))
            pages = max(1, math.ceil(self.total_rows / self.page_size))
            self.page_label.setText(self.t(f'第 {self.page} / {pages} 页', f'Page {self.page} of {pages}'))
            more = self.page < pages
        else:
            self.record_count.setText(self.t(f'显示 {first}–{last} 条 · 总数未统计', f'Showing {first}–{last} · total not counted'))
            self.page_label.setText(self.t(f'第 {self.page} 页', f'Page {self.page}'))
            more = result.has_more
        self.previous_button.setEnabled(self.page > 1)
        self.next_button.setEnabled(more)
        self.count_total_button.setText(self.t('统计总数', 'Count rows'))
        state = self._active_workspace
        with QSignalBlocker(self.count_total_button):
            self.count_total_button.setChecked(bool(state and state.count_total))
        self.count_total_button.setVisible(bool(self.current_connection and self.current_connection.kind in {'SQLite', 'PostgreSQL', 'MySQL', 'Oracle'}))

    def update_context(self):
        connection = self.current_connection
        self._sync_redis_search()
        console_active = self._console_scope is not None
        sql = bool(connection and connection.kind in {'SQLite', 'PostgreSQL', 'MySQL', 'Oracle'})
        self.query_editor.set_sql_enabled(sql)
        self.sql_tools_button.setVisible(sql)
        self.table_tabs.setVisible(not console_active)
        self.tabs.setVisible(not console_active)
        self.console_page.setVisible(console_active)
        redis = bool(connection and connection.kind == 'Redis')
        self.data_views.setCurrentWidget(self.redis_view if redis else self.data_splitter)
        self.data_search.setVisible(not redis)
        sql_table = bool(connection and connection.kind in {'SQLite', 'PostgreSQL', 'MySQL', 'Oracle'} and self.current_table)
        self.table_criteria_bar.setVisible(sql_table)
        self.where_edit.setVisible(sql_table)
        self.order_by_edit.setVisible(sql_table)
        self.apply_table_query_button.setVisible(sql_table)
        self.clear_table_query_button.setVisible(sql_table)
        self.revert_table_button.setVisible(sql_table)
        self.commit_table_button.setVisible(sql_table)
        self.data_pager.setVisible(not redis)
        self.edit_row_button.setVisible(not redis)
        self.refresh_button.setVisible(not redis)
        self.delete_row_button.setVisible(not redis)
        self.delete_key_button.setVisible(redis)
        self.delete_key_button.setEnabled(bool(redis and self.current_table and not connection.read_only))
        self.delete_key_button.setToolTip(self.t('删除整个 key', 'Delete entire key'))
        self.add_row_button.setText(self.t('新建 key', 'New key') if redis else self.t('新增记录', 'Add record'))
        shown_table = safe_text(self.current_table) if redis else self.current_table
        if self._navigation == 'workspace':
            self.breadcrumb.setText(self.t('工作空间', 'Workspace') + (f'   /   {connection.name}' if connection else '') + (f'   /   {shown_table}' if self.current_table else ''))
        self.table_heading.setText((shown_table if redis else shown_table.split('.')[-1]) if self.current_table else self.t('数据工作台', 'Workspace'))
        self.table_badge.setText(self.t('集合', 'COLLECTION') if connection and connection.kind == 'MongoDB' else 'KEYS' if connection and connection.kind == 'Redis' else 'TABLE')
        self.connection_badge.setText(f'●  {connection.kind}' + (' · SSH' if connection.ssh_enabled else '') + (self.t(' · 只读', ' · Read only') if connection.read_only else '') if connection else self.t('未连接', 'Not connected'))
        self.connection_badge.setToolTip(f'{connection.ssh_username}@{connection.ssh_host}:{connection.ssh_port}' if connection and connection.ssh_enabled else '')
        if connection:
            subtitle = self.t('本地示例数据库 · 所有修改仅作用于本机示例数据', 'Local playground · Changes only affect this sample database') if connection.is_demo else f'{connection.database or Path(connection.path).name or connection.name}  ·  {connection.host if connection.kind != "SQLite" else self.t("本地文件", "Local file")}'
            if self.columns and not redis:
                subtitle += self.t(f'   ·   {len(self.columns)} 个字段', f'   ·   {len(self.columns)} columns')
            self.table_subtitle.setText(subtitle)
        else:
            self.table_subtitle.setText(self.t('从左侧选择连接，或创建你的第一个数据源。', 'Choose a connection or add your first data source.'))
        missing = bool(self._active_workspace and self._active_workspace.missing)
        if missing:
            self.table_subtitle.setText(self._active_workspace.error)
        writable = bool(connection and self.current_table and not missing and not connection.read_only and any(c.primary_key for c in self.columns))
        self.add_row_button.setEnabled(bool(connection and not connection.read_only) if redis else writable)
        pending = self._pending_grid_count()
        committing = bool(self._active_workspace and self._active_workspace.grid_committing)
        self.commit_table_button.setEnabled(bool(sql_table and pending and writable and not committing))
        self.revert_table_button.setEnabled(bool(sql_table and pending))
        self.run_button.setEnabled(connection is not None)
        self.run_button.setText(self.t('中断查询', 'Cancel query') if self._query_running else self.t('运行查询', 'Run query'))
        self.run_button.setIcon(icon('close' if self._query_running else 'play', '#ffffff', 16))
        self.run_button.setProperty('_query_running', self._query_running)
        self.run_button.setToolTip(self.t('中断正在执行的查询', 'Interrupt the running query') if self._query_running else
                                   self.t('执行当前选中语句或全文', 'Run the selected statement or the full editor'))
        self.export_button.setEnabled(bool(self.current_result.columns))
        self.sql_tools_button.setText((self._console_state().console_name or 'SQL') if self._console_state() else 'SQL')
        self.query_hint.setText(self.t('⌘ / Ctrl + Enter 执行 · Shift + Enter 执行选中 · 最多返回 1,000 行',
                                      '⌘ / Ctrl + Enter to run · Shift + Enter selection · Up to 1,000 rows'))
        if connection and connection.kind == 'MongoDB':
            self.query_hint.setText(self.t('JSON 命令：find / insertOne / updateOne / deleteOne', 'JSON commands: find / insertOne / updateOne / deleteOne'))
        elif connection and connection.kind == 'Redis':
            self.query_hint.setText(self.t('Redis 命令，例如 GET key 或 SET key value', 'Redis commands, such as GET key or SET key value'))
        self.connection_actions.setEnabled(connection is not None)

    def _pending_grid_count(self):
        state = self._active_workspace
        if state is None or state.closed:
            return 0
        return len(state.pending_updates) + len(state.pending_inserts) + len(state.pending_deletes)

    def render_schema(self):
        if not hasattr(self, 'schema_table'):
            return
        headers = [self.t('字段名称', 'Column'), self.t('数据类型', 'Type'), self.t('主键', 'Primary key'), self.t('允许为空', 'Nullable'), self.t('默认值', 'Default')]
        rows = [{'name': c.name, 'type': c.data_type, 'pk': '⌁' if c.primary_key else '—', 'null': self.t('是', 'Yes') if c.nullable else self.t('否', 'No'), 'default': c.default} for c in self.columns]
        result = QueryResult(columns=['name', 'type', 'pk', 'null', 'default'], rows=rows)
        self.populate_table(self.schema_table, result)
        self.schema_table.setHorizontalHeaderLabels(headers)

    def _selected_rows(self):
        return sorted({index.row() for index in self.data_table.selectionModel().selectedRows()})

    def _grid_key(self, row, columns=None):
        columns = columns or self.columns
        return tuple(row.get(column.name) for column in columns if column.primary_key)

    @staticmethod
    def _grid_value(column, text, original):
        if original is not None and text == display_value(original):
            return original
        stripped = text.strip()
        if column.nullable and stripped.upper() == 'NULL':
            return None
        kind = column.data_type.upper()
        if original is None and not stripped and column.nullable:
            return None
        if isinstance(original, bool) or 'BOOL' in kind:
            if stripped.lower() in {'true', '1', 'yes', 'on'}:
                return True
            if stripped.lower() in {'false', '0', 'no', 'off'}:
                return False
            raise ValueError('Enter true or false.')
        if isinstance(original, int) or any(token in kind for token in ('INT', 'SERIAL')):
            try:
                return int(stripped)
            except ValueError:
                raise ValueError('Enter a valid integer.') from None
        if isinstance(original, float) or any(token in kind for token in ('REAL', 'FLOA', 'DOUB')):
            try:
                return float(stripped)
            except ValueError:
                raise ValueError('Enter a valid number.') from None
        if 'JSON' in kind or isinstance(original, (dict, list)):
            try:
                return json.loads(text)
            except (TypeError, ValueError):
                raise ValueError('Enter valid JSON.') from None
        return text

    def _grid_item_changed(self, item):
        if self._grid_updating or not self.current_connection or self.current_connection.kind not in {'SQLite', 'PostgreSQL', 'MySQL', 'Oracle'}:
            return
        if self.current_connection.read_only or not any(column.primary_key for column in self.columns):
            return
        row_index, column_index = item.row(), item.column()
        if not 0 <= row_index < len(self.current_result.rows) or not 0 <= column_index < len(self.columns):
            return
        column = self.columns[column_index]
        if column.primary_key:
            return
        state = self._workspace_state()
        row = self.current_result.rows[row_index]
        key = self._grid_key(row)
        if not key:
            return
        record = state.pending_updates.get(key)
        if record is None:
            record = {'keys': {name: row[name] for name in (column.name for column in self.columns if column.primary_key)},
                      'original': dict(row), 'values': {}}
            state.pending_updates[key] = record
        try:
            value = self._grid_value(column, item.text(), record['original'].get(column.name))
        except ValueError as error:
            self._grid_updating = True
            try:
                item.setText(display_value(record['original'].get(column.name)))
            finally:
                self._grid_updating = False
            self.show_error(str(error))
            return
        original = record['original'].get(column.name)
        if value == original:
            record['values'].pop(column.name, None)
        else:
            record['values'][column.name] = value
        row[column.name] = value
        if not record['values']:
            state.pending_updates.pop(key, None)
        item.setData(Qt.ItemDataRole.UserRole, value)
        item.setToolTip(display_value(value))
        self.update_context()

    def commit_table_changes(self):
        connection = self.current_connection
        state = self._workspace_state()
        if (not state or not connection or connection.kind not in {'SQLite', 'PostgreSQL', 'MySQL', 'Oracle'}
                or connection.read_only or not self._pending_grid_count()):
            return
        updates = [(record['keys'], record['values']) for record in state.pending_updates.values()]
        inserts, deletes = list(state.pending_inserts), list(state.pending_deletes)
        identity = state.identity
        state.grid_committing = True
        self.update_context()
        def done(result):
            target = self._workspace_tabs.get(identity, state)
            if target.closed:
                return
            target.grid_committing = False
            target.pending_updates.clear()
            target.pending_inserts.clear()
            target.pending_deletes.clear()
            target.dirty = True
            if self._workspace_is_active(target):
                self.footer_detail.setText(self.t(f'已提交 · {result.affected} 行', f'Committed · {result.affected} rows'))
                self.refresh_data()
                self.update_context()
        def failed(message):
            if not state.closed:
                state.grid_committing = False
                state.error = message
                if self._workspace_is_active(state):
                    self.update_context()
                    self.show_error(message)
        self.submit(self._connection_operation('apply_grid_changes', connection, self.current_table,
                                               updates=updates, inserts=inserts, deletes=deletes), done, failed)

    def revert_table_changes(self):
        state = self._workspace_state()
        if state is None or not self._pending_grid_count():
            return
        state.pending_updates.clear()
        state.pending_inserts.clear()
        state.pending_deletes.clear()
        state.page = self.page = 1
        self.refresh_data()
        self.update_context()

    def selection_changed(self):
        selected = self._selected_rows()
        writable = bool(self.current_connection and not self.current_connection.read_only and any(c.primary_key for c in self.columns))
        self.edit_row_button.setEnabled(writable and len(selected) == 1)
        self.delete_row_button.setEnabled(writable and bool(selected))
        if self.inspector.isVisible():
            self.render_inspector()

    def toggle_inspector(self):
        self.inspector.setVisible(not self.inspector.isVisible())
        self.render_inspector()

    def render_inspector(self):
        while self.inspector_layout.count():
            item = self.inspector_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.inspector_layout.addWidget(label(self.t('记录详情', 'RECORD DETAILS'), 'eyebrow'))
        selected = self._selected_rows()
        if not selected or selected[0] >= len(self.current_result.rows):
            note = label(self.t('选择一条记录查看详情', 'Select a record to inspect'), 'muted')
            note.setWordWrap(True)
            self.inspector_layout.addWidget(note)
        else:
            row = self.current_result.rows[selected[0]]
            for key, value in row.items():
                self.inspector_layout.addWidget(label(key, 'muted'))
                text = label(display_value(value))
                text.setWordWrap(True)
                text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                self.inspector_layout.addWidget(text)
        self.inspector_layout.addStretch()

    def insert_query_template(self):
        connection = self.current_connection
        if not connection:
            return
        table = self.current_table or 'customers'
        if connection.kind == 'MongoDB':
            query = json.dumps({'find': table, 'filter': {}, 'limit': 100}, indent=2)
        elif connection.kind == 'Redis':
            query = 'SCAN 0 COUNT 100'
        else:
            quoted = '.'.join(('`' + part.replace('`', '``') + '`') if connection.kind == 'MySQL' else ('"' + part.replace('"', '""') + '"') for part in table.split('.'))
            query = f'SELECT *\nFROM {quoted}\n' + ('FETCH FIRST 100 ROWS ONLY;' if connection.kind == 'Oracle' else 'LIMIT 100;')
        self.query_editor.setPlainText(query)

    def run_query(self, _checked=False, *, current_only=False, explain=False):
        if self._query_running:
            self.cancel_query()
            return
        if not self.current_connection or not self.query_editor.toPlainText().strip() or not self.run_button.isEnabled():
            return
        console_active = self._console_scope is not None
        if console_active:
            self.show_workspace()
        else:
            # Keep direct programmatic callers compatible with the former
            # table Query action; the visible table workspace no longer
            # exposes that inner tab.
            self.show_workspace()
            self.tabs.setCurrentIndex(1)
        cursor = self.query_editor.textCursor()
        query = self.query_editor.current_statement() if current_only or explain else (cursor.selectedText().replace('\u2029', '\n') if cursor.hasSelection() else self.query_editor.toPlainText())
        state = self._console_state() if console_active else self._workspace_state()
        if state is None:
            return
        connection = state.connection
        request = state.request('query')
        cancel_token = CancellationToken()
        self._query_cancel_token = cancel_token
        state.query_cancel_token = cancel_token
        self._query_running, self._query_state = True, 'running'
        state.query_running, state.query_state, state.query_error = True, 'running', ''
        self.update_context()
        self.update_query_status()
        def done(result):
            changed = (not result.columns or connection.kind in ('MongoDB', 'Redis') or
                       not re.match(r'^\s*(SELECT|SHOW|DESCRIBE|DESC|EXPLAIN|VALUES)\b', query, re.IGNORECASE))
            if not state.accepts('query', request):
                if changed and any(c.id == connection.id for c in self.connections):
                    self.refresh_catalog(connection, owner=state)
                return
            state.query_running, state.query_state, state.query_result = False, 'success', result
            state.query_cancel_token = None
            if self._query_cancel_token is cancel_token:
                self._query_cancel_token = None
            active_console = self._console_state() is state
            if (active_console or self._workspace_is_active(state)):
                self._query_running, self._query_state, self.query_result = False, 'success', result
                self.populate_table(self.query_result_table, result)
                self.update_context()
                self.update_query_status()
            if changed:
                self.refresh_catalog(connection, owner=state)
        def failed(message):
            if not state.accepts('query', request):
                return
            state.query_running, state.query_state, state.query_error = False, 'failed', message
            state.query_cancel_token = None
            state.query_result = QueryResult()
            if self._query_cancel_token is cancel_token:
                self._query_cancel_token = None
            active_console = self._console_state() is state
            if active_console or self._workspace_is_active(state):
                self._query_running, self._query_state, self._query_error = False, 'failed', message
                self.query_result = state.query_result
                self.populate_table(self.query_result_table, self.query_result)
                self.update_context()
                self.update_query_status()
        supports_cancel = False
        try:
            supports_cancel = 'cancel_token' in inspect.signature(self.service.execute).parameters
        except (TypeError, ValueError):
            supports_cancel = False
        execute_kwargs = ({'cancel_token': cancel_token}
                          if connection.kind in {'SQLite', 'PostgreSQL', 'MySQL', 'Oracle'} and supports_cancel else {})
        self.submit(self._connection_operation('explain' if explain else 'execute', connection, query, **execute_kwargs), done, failed)

    def cancel_query(self):
        state = self._console_state() if self._console_scope is not None else self._workspace_state()
        token = state.query_cancel_token if state is not None else None
        if token is None and not self._query_running:
            return
        if token is not None:
            accepted = token.cancel(lambda callback: self.submit(callback, lambda _: None, lambda _: None))
            if not accepted:
                self.query_status.setText(self.t('正在提交，等待数据库确认结果…', 'Committing; waiting for the database result…'))
                return
        self._query_cancel_token = None
        if state is not None:
            state.query_cancel_token = None
            # Invalidate the request immediately. The worker may still unwind
            # through a driver, but its eventual signal cannot update a newer
            # Console/table state.
            state.request('query')
            state.query_running = False
            state.query_state = 'cancelled'
            state.query_error = ''
        self._query_running = False
        self._query_state = 'cancelled'
        self._query_error = ''
        self.update_context()
        self.update_query_status()

    def update_query_status(self):
        if self._query_state == 'running':
            text = self.t('查询正在执行…', 'Running query…')
        elif self._query_state == 'failed':
            text = self.t('执行失败：', 'Query failed: ') + self._query_error
        elif self._query_state == 'cancelled':
            text = self.t('查询已中断', 'Query cancelled')
        elif self._query_state == 'success':
            result = self.query_result
            count = len(result.rows) if result.columns else result.affected
            text = self.t(f'执行成功 · {count:,} 行 · {result.elapsed_ms:.1f} ms', f'Success · {count:,} rows · {result.elapsed_ms:.1f} ms')
            if result.truncated:
                text += self.t(' · 结果已截断', ' · Result truncated')
        else:
            text = ''
        self.query_status.setText(text)

    def refresh_catalog(self, connection=None, owner=None):
        """Refresh the origin catalog without borrowing the active editor's state."""
        connection = connection or self.current_connection
        if not connection:
            return
        scope = self._workspace_scope(connection)
        if owner is None and self._active_workspace and self._active_workspace.identity[:2] == scope:
            owner = self._active_workspace
        if connection.kind == 'Redis':
            self._redis_search_dirty.add(scope)
            for state in self._workspace_tabs.values():
                if state.identity[:2] == scope:
                    state.dirty = True
            if self.current_connection and self._workspace_scope(self.current_connection) == scope:
                self._refresh_redis_keys()
                if self.current_table:
                    self.refresh_data()
            return
        cid = connection.id
        token = object()
        self._catalog_requests = getattr(self, '_catalog_requests', {})
        self._catalog_requests[cid] = token
        def loaded(tables):
            if self._catalog_requests.get(cid) is not token or not any(c.id == cid for c in self.connections):
                return
            self._connection_tables[cid] = tables
            existing = {t.qualified_name for t in tables}
            for state in self._workspace_tabs.values():
                if state.identity[:2] != scope:
                    continue
                state.missing = state.table not in existing
                state.dirty = not state.missing
                state.schema_dirty = not state.missing
                if state.missing:
                    state.request('rows')
                    state.request('schema')
                    state.columns, state.current_result, state.total_rows = [], QueryResult(), 0
                    state.error = self.t('数据表已不存在，请重新选择数据表。', 'This table no longer exists. Choose another table.')
            active = self._active_workspace
            if self.current_connection and self.current_connection.id == cid:
                self.tables = tables
                self.rebuild_tree()
                if active and active.table:
                    if active.missing:
                        self._capture_workspace()
                        # Capture preserves the query draft; catalog invalidation
                        # owns the data and schema that have just disappeared.
                        active.columns, active.current_result, active.total_rows = [], QueryResult(), 0
                        self._activate_workspace(active, capture=False)
                    else:
                        self._load_table_schema(active)
                        self.refresh_data()
                else:
                    self.update_context()
        def failed(message):
            if (self._catalog_requests.get(cid) is token and
                    any(c.id == cid for c in self.connections)):
                self._origin_error(owner, message)
        self.submit(partial(self.service.list_tables, cid), loaded, failed)

    def _origin_error(self, state, message):
        if state is not None and not state.closed:
            state.error = message
            if self._workspace_is_active(state):
                self.show_error(message)

    def add_row(self):
        if not self.current_connection or self.current_connection.read_only:
            return
        connection = self.current_connection
        origin = self._workspace_state()
        state = origin
        generation = self._generation
        table = self.current_table
        redis = connection.kind == 'Redis'
        if redis:
            dialog = RedisKeyDialog(parent=self, language=self.language)
            dialog.setWindowTitle(self.t('新建 key', 'New key') + f' · DB{connection.database or "0"}')
        else:
            if not self.columns:
                return
            dialog = RowDialog(self.columns, parent=self, language=self.language,
                               bson_mode=connection.kind == 'MongoDB')
        if dialog.exec():
            values = dialog.values()
            if redis:
                def created(_):
                    if generation == self._generation and self._redis_scope(self.current_connection) == self._redis_scope(connection):
                        self.open_table(values['key'])
                    self.refresh_catalog(connection, owner=origin)
                self.submit(self._connection_operation('insert_row', connection, table, values), created,
                            lambda message: self._origin_error(origin, message))
            else:
                state.pending_inserts.append(dict(values))
                row = {column.name: values.get(column.name) for column in self.columns}
                if len(state.current_result.rows) < state.page_size:
                    state.current_result.rows.append(row)
                state.current_result.total = state.total_rows + 1
                self.current_result = state.current_result
                self.total_rows = state.total_rows = max(state.total_rows + 1, len(state.current_result.rows))
                self.populate_table(self.data_table, state.current_result, (state.page - 1) * state.page_size)
                self.update_pager()
                self.update_context()

    def _stage_update(self, state, keys, values):
        key = tuple(keys.get(column.name) for column in self.columns if column.primary_key)
        row = next((item for item in state.current_result.rows if self._grid_key(item) == key), None)
        if row is None:
            return
        record = state.pending_updates.get(key)
        if record is None:
            record = {'keys': dict(keys), 'original': dict(row), 'values': {}}
            state.pending_updates[key] = record
        for name, value in values.items():
            if value == record['original'].get(name):
                record['values'].pop(name, None)
            else:
                record['values'][name] = value
            row[name] = value
        if not record['values']:
            state.pending_updates.pop(key, None)
        self.populate_table(self.data_table, state.current_result, (state.page - 1) * state.page_size)
        self.update_context()

    def delete_redis_key(self):
        connection = self.current_connection
        if not connection or connection.kind != 'Redis' or connection.read_only or not self.current_table:
            return
        key = self.current_table
        state = self._workspace_state()
        identity = state.identity
        answer = QMessageBox.question(self, self.t('删除 key', 'Delete key'),
                                      self.t(f'确认删除整个 key“{safe_text(key)}”及其所有数据？',
                                             f'Delete the entire key “{safe_text(key)}” and all its data?'),
                                      QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                                      QMessageBox.StandardButton.Cancel)
        if answer != QMessageBox.StandardButton.Yes:
            return
        def done(_):
            target = self._workspace_tabs.get(identity, state)
            if not target.closed:
                target.dirty = True
            if self._workspace_is_active(target):
                self.refresh_data()
                self._refresh_redis_keys()
        def failed(message):
            if not state.closed:
                state.error = message
                if self._workspace_is_active(state):
                    self.show_error(message)
        self.submit(self._connection_operation('delete_rows', connection, key, [{'key': key}]), done, failed)

    def edit_selected_row(self):
        selected = self._selected_rows()
        if len(selected) != 1 or not self.edit_row_button.isEnabled():
            return
        row = self.current_result.rows[selected[0]]
        keys = {c.name: row.get(c.name) for c in self.columns if c.primary_key}
        if not keys:
            return
        edit_columns = self.columns
        if self.current_connection.kind == 'MongoDB':
            # MongoDB's sampled schema is only a hint. A different document may
            # store a string where the first document has a number or object.
            edit_columns = [replace(column, data_type=type(row[column.name]).__name__)
                            if row[column.name] is not None else column
                            for column in self.columns if column.name in row]
        dialog = RowDialog(edit_columns, row=row, parent=self, language=self.language,
                           bson_mode=self.current_connection.kind == 'MongoDB')
        if dialog.exec():
            cid, table = self.current_connection.id, self.current_table
            values = {key: value for key, value in dialog.values().items() if key not in keys and (key not in row or value != row[key])}
            if not values:
                return
            if self.current_connection.kind in {'SQLite', 'PostgreSQL', 'MySQL', 'Oracle'}:
                self._stage_update(self._workspace_state(), keys, values)
            else:
                self._mutate(self._connection_operation('update_row', self.current_connection, table, keys, values), cid, table)

    def delete_selected_rows(self):
        selected = self._selected_rows()
        if not selected or not self.delete_row_button.isEnabled():
            return
        keys = [{c.name: self.current_result.rows[index].get(c.name) for c in self.columns if c.primary_key} for index in selected]
        answer = QMessageBox.question(self, self.t('删除记录', 'Delete records'), self.t(f'确认删除选中的 {len(keys)} 条记录？此操作无法撤销。', f'Delete {len(keys)} selected records? This action cannot be undone.'), QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Cancel)
        if answer == QMessageBox.StandardButton.Yes:
            cid, table = self.current_connection.id, self.current_table
            if self.current_connection.kind in {'SQLite', 'PostgreSQL', 'MySQL', 'Oracle'}:
                state = self._workspace_state()
                selected_keys = {tuple(item.get(column.name) for column in self.columns if column.primary_key)
                                 for item in (self.current_result.rows[index] for index in selected)}
                for key_row in keys:
                    key = tuple(key_row.get(column.name) for column in self.columns if column.primary_key)
                    insert = next((item for item in state.pending_inserts
                                   if tuple(item.get(column.name) for column in self.columns if column.primary_key) == key), None)
                    if insert is not None:
                        state.pending_inserts.remove(insert)
                    elif key not in {tuple(item.get(column.name) for column in self.columns if column.primary_key)
                                     for item in state.pending_deletes}:
                        state.pending_deletes.append(key_row)
                state.current_result.rows = [
                    row for row in state.current_result.rows
                    if self._grid_key(row) not in selected_keys
                ]
                for key in selected_keys:
                    state.pending_updates.pop(key, None)
                self.current_result = state.current_result
                self.total_rows = state.total_rows = max(0, state.total_rows - len(selected_keys))
                last_page = max(1, math.ceil(state.total_rows / state.page_size))
                if state.page > last_page:
                    state.page = self.page = last_page
                self.refresh_data(force=True)
                self.update_pager()
                self.update_context()
            else:
                self._mutate(self._connection_operation('delete_rows', self.current_connection, table, keys), cid, table)

    def _mutate(self, function, cid, table):
        state = self._workspace_state()
        identity = state.identity if state else None
        def done(result):
            target = self._workspace_tabs.get(identity, state)
            if not target or target.closed:
                return
            target.dirty = True
            if self._workspace_is_active(target):
                self.footer_detail.setText(self.t(f'已保存 · {result.affected} 行', f'Saved · {result.affected} rows'))
                self.refresh_data()
        def failed(message):
            if state and not state.closed:
                state.error = message
                if self._workspace_is_active(state):
                    self.show_error(message)
        self.submit(function, done, failed)

    def add_connection(self):
        self.connection_dialog()

    def edit_current_connection(self):
        if self.current_connection:
            self.connection_dialog(self.current_connection)

    def connection_dialog(self, connection=None):
        kind = None
        if connection is None:
            chooser = ConnectionTypeDialog(parent=self, language=self.language)
            if not chooser.exec() or chooser.selected_kind is None:
                return
            kind = chooser.selected_kind
        dialog = ConnectionDialog(parent=self, connection=connection, language=self.language, kind=kind)
        dialog.test_requested.connect(lambda candidate: self.test_dialog_connection(dialog, candidate))
        if dialog.exec():
            candidate = dialog.result_connection()
            def saved(result):
                self.connections = self.service.list_connections()
                self._close_connection_tabs(candidate.id)
                self.rebuild_tree()
                self.activate_connection(result or candidate)
            self.submit(lambda: self.service.save_connection(candidate), saved)

    def test_dialog_connection(self, dialog, candidate):
        dialog.test_button.setEnabled(False)
        dialog.test_status.setText(self.t('正在测试连接…', 'Testing connection…'))
        def done(result):
            if dialog.isVisible():
                dialog.test_button.setEnabled(True)
                dialog.test_status.setText(self.t('连接成功', 'Connection successful') + (f' · {result}' if result else ''))
        def failed(message):
            if dialog.isVisible():
                dialog.test_button.setEnabled(True)
                dialog.test_status.setText(self.t('连接失败：', 'Connection failed: ') + message)
        self.submit(lambda: self.service.test_connection(candidate), done, failed)

    def tree_menu(self, point):
        item = self.tree.itemAt(point)
        if not item:
            return
        kind = item.data(0, NODE_KIND_ROLE)
        if kind in {'redis_folder', 'redis_database', 'redis_search', 'redis_placeholder', 'redis_other_database', 'redis_load_more',
                    'sql_tables', 'sql_history', 'sql_console'}:
            return
        cid, table = item.data(0, Qt.ItemDataRole.UserRole)
        connection = next((c for c in self.connections if c.id == cid), None)
        if not connection:
            return
        menu = QMenu(self)
        menu.addAction(icon('database'), self.t('打开连接', 'Open connection'), lambda: self.activate_connection(connection))
        if kind == 'table' and connection.kind in {'SQLite', 'PostgreSQL', 'MySQL', 'Oracle'}:
            menu.addAction(icon('table'), self.t('打开数据表', 'Open table'),
                           lambda: self.activate_connection(connection, table))
            self._add_sql_table_operations(menu, connection, table)
            menu.addSeparator()
        menu.addAction(icon('edit'), self.t('编辑连接', 'Edit connection'), lambda: self.connection_dialog(connection))
        menu.addSeparator()
        menu.addAction(icon('trash'), self.t('移除连接', 'Remove connection'), lambda: self.remove_connection(connection))
        menu.exec(self.tree.viewport().mapToGlobal(point))

    def _add_sql_table_operations(self, menu, connection, table):
        """Keep all table DDL actions under the same context-menu submenu."""
        ddl = menu.addMenu(icon('settings'), self.t('表结构操作', 'Table operations'))
        ddl.addAction(self.t('修改表结构…', 'Modify table structure…'), lambda: self.modify_sql_structure(connection, table))
        ddl.addSeparator()
        ddl.addAction(self.t('重命名表', 'Rename table'), lambda: self.rename_sql_table(connection, table))
        ddl.addAction(self.t('清空表数据', 'Truncate table'), lambda: self.truncate_sql_table(connection, table))
        ddl.addAction(self.t('添加字段', 'Add column'), lambda: self.add_sql_column(connection, table))
        ddl.addAction(self.t('删除字段', 'Drop column'), lambda: self.drop_sql_column(connection, table))
        ddl.addSeparator()
        ddl.addAction(self.t('查看索引', 'View indexes'), lambda: self.show_sql_indexes(connection, table))
        ddl.addAction(self.t('创建索引', 'Create index'), lambda: self.create_sql_index(connection, table))
        ddl.addAction(self.t('删除索引', 'Delete index'), lambda: self.drop_sql_index(connection, table))
        ddl.addSeparator()
        ddl.addAction(icon('trash'), self.t('删除表', 'Drop table'), lambda: self.drop_sql_table(connection, table))
        return ddl

    def _run_sql_ddl(self, connection, operation, success_text):
        """Submit a SQL DDL operation and refresh the source catalog afterward."""
        def done(result):
            if self.current_connection and self.current_connection.id == connection.id:
                self.footer_detail.setText(self.t(success_text[0], success_text[1]))
            self.refresh_catalog(connection)
        self.submit(operation, done, self.show_error)

    def rename_sql_table(self, connection, table):
        if connection.read_only:
            self.show_error(self.t('只读连接不能修改表结构。', 'A read-only connection cannot change table structure.'))
            return
        name, accepted = QInputDialog.getText(self, self.t('重命名表', 'Rename table'),
                                              self.t('新表名：', 'New table name:'), text=str(table).split('.')[-1])
        if not accepted or not name.strip():
            return
        self._run_sql_ddl(connection,
                          partial(self.service.rename_table, connection.id, table, name.strip()),
                          ('表已重命名', 'Table renamed'))

    def truncate_sql_table(self, connection, table):
        if connection.read_only:
            self.show_error(self.t('只读连接不能修改表结构。', 'A read-only connection cannot change table structure.'))
            return
        answer = QMessageBox.question(self, self.t('清空表数据', 'Truncate table'),
                                      self.t(f'确认清空“{table}”中的全部数据？此操作无法撤销。',
                                             f'Clear all rows from “{table}”? This cannot be undone.'),
                                      QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                                      QMessageBox.StandardButton.Cancel)
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._run_sql_ddl(connection,
                          partial(self.service.truncate_table, connection.id, table),
                          ('表数据已清空', 'Table data cleared'))

    def add_sql_column(self, connection, table):
        if connection.read_only:
            self.show_error(self.t('只读连接不能修改表结构。', 'A read-only connection cannot change table structure.'))
            return
        name, accepted = QInputDialog.getText(self, self.t('添加字段', 'Add column'),
                                              self.t('字段名：', 'Column name:'))
        if not accepted or not name.strip():
            return
        data_type, accepted = QInputDialog.getText(self, self.t('字段类型', 'Column type'),
                                                   self.t('例如 TEXT、INTEGER、VARCHAR(255)：',
                                                          'For example TEXT, INTEGER or VARCHAR(255):'), text='TEXT')
        if not accepted or not data_type.strip():
            return
        self._run_sql_ddl(connection,
                          partial(self.service.add_column, connection.id, table, name.strip(), data_type.strip()),
                          ('字段已添加', 'Column added'))

    def drop_sql_column(self, connection, table):
        if connection.read_only:
            self.show_error(self.t('只读连接不能修改表结构。', 'A read-only connection cannot change table structure.'))
            return
        def loaded(columns):
            names = [column.name for column in columns]
            if not names:
                self.show_error(self.t('没有可删除的字段。', 'There are no columns to delete.'))
                return
            name, accepted = QInputDialog.getItem(self, self.t('删除字段', 'Drop column'),
                                                   self.t('选择字段：', 'Choose a column:'), names, 0, False)
            if not accepted:
                return
            answer = QMessageBox.question(self, self.t('删除字段', 'Drop column'),
                                          self.t(f'确认删除字段“{name}”？此操作无法撤销。',
                                                 f'Delete column “{name}”? This cannot be undone.'),
                                          QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                                          QMessageBox.StandardButton.Cancel)
            if answer == QMessageBox.StandardButton.Yes:
                self._run_sql_ddl(connection,
                                  partial(self.service.drop_column, connection.id, table, name),
                                  ('字段已删除', 'Column deleted'))
        self.submit(partial(self.service.get_schema, connection.id, table), loaded, self.show_error)

    def drop_sql_table(self, connection, table):
        """Drop a table only after exact-name entry and a separate confirmation."""
        if connection.read_only:
            self.show_error(self.t('只读连接不能删除表。', 'A read-only connection cannot drop a table.'))
            return
        expected = str(table)
        entered, accepted = QInputDialog.getText(
            self, self.t('删除表', 'Drop table'),
            self.t(f'请输入表名“{expected}”以继续：', f'Type the table name “{expected}” to continue:'))
        if not accepted or entered != expected:
            return
        answer = QMessageBox.question(
            self, self.t('确认删除表', 'Confirm drop table'),
            self.t(f'确认永久删除表“{expected}”？此操作无法撤销。',
                   f'Permanently drop table “{expected}”? This cannot be undone.'),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._run_sql_ddl(connection,
                          partial(self.service.drop_table, connection.id, table),
                          ('表已删除', 'Table dropped'))

    def show_sql_indexes(self, connection, table):
        def loaded(indexes):
            if not indexes:
                QMessageBox.information(self, self.t('查看索引', 'View indexes'),
                                        self.t('此表没有索引。', 'This table has no indexes.'))
                return
            lines = [f'{item.name}  ·  {self.t("唯一", "unique") if item.unique else self.t("普通", "regular")}  ·  {", ".join(item.columns)}'
                     for item in indexes]
            QMessageBox.information(self, self.t('查看索引', 'View indexes'), '\n'.join(lines))
        self.submit(partial(self.service.list_indexes, connection.id, table), loaded, self.show_error)

    def create_sql_index(self, connection, table):
        if connection.read_only:
            self.show_error(self.t('只读连接不能修改索引。', 'A read-only connection cannot change indexes.'))
            return
        def loaded(columns):
            names = [column.name for column in columns]
            if not names:
                self.show_error(self.t('此表没有可用字段。', 'This table has no columns.'))
                return
            name, accepted = QInputDialog.getText(self, self.t('创建索引', 'Create index'),
                                                  self.t('索引名：', 'Index name:'))
            if not accepted or not name.strip():
                return
            selected, accepted = QInputDialog.getText(
                self, self.t('索引字段', 'Index columns'),
                self.t('输入字段名，多个字段用逗号分隔：', 'Enter column names separated by commas:'), text=names[0])
            if not accepted:
                return
            selected_names = [value.strip() for value in selected.split(',') if value.strip()]
            if not selected_names or any(value not in names for value in selected_names):
                self.show_error(self.t('索引字段无效。', 'One or more index columns are invalid.'))
                return
            unique = QMessageBox.question(
                self, self.t('唯一索引', 'Unique index'),
                self.t('是否创建唯一索引？', 'Create a unique index?'),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes
            self._run_sql_ddl(connection,
                              partial(self.service.create_index, connection.id, table, name.strip(), selected_names, unique),
                              ('索引已创建', 'Index created'))
        self.submit(partial(self.service.get_schema, connection.id, table), loaded, self.show_error)

    def drop_sql_index(self, connection, table):
        """Delete a selected index; selection itself supplies the index name."""
        if connection.read_only:
            self.show_error(self.t('只读连接不能修改索引。', 'A read-only connection cannot change indexes.'))
            return
        def loaded(indexes):
            candidates = [item for item in indexes
                          if item.origin != 'pk' and item.name.upper() != 'PRIMARY']
            if not candidates:
                self.show_error(self.t('没有可删除的索引。', 'There are no removable indexes.'))
                return
            labels = [f'{item.name}  ·  {", ".join(item.columns)}' for item in candidates]
            selected, accepted = QInputDialog.getItem(
                self, self.t('删除索引', 'Delete index'),
                self.t('选择要删除的索引：', 'Choose the index to delete:'), labels, 0, False)
            if not accepted:
                return
            index = candidates[labels.index(selected)]
            answer = QMessageBox.question(
                self, self.t('确认删除索引', 'Confirm delete index'),
                self.t(f'确认删除索引“{index.name}”？此操作无法撤销。',
                       f'Delete index “{index.name}”? This cannot be undone.'),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return
            self._run_sql_ddl(connection,
                              partial(self.service.drop_index, connection.id, table, index.name),
                              ('索引已删除', 'Index deleted'))
        self.submit(partial(self.service.list_indexes, connection.id, table), loaded, self.show_error)

    delete_sql_index = drop_sql_index

    def remove_connection(self, connection):
        response = QMessageBox.question(self, self.t('移除连接', 'Remove connection'), self.t(f'从 BaseSkiff 移除“{connection.name}”？数据库文件和远程数据不会被删除。', f'Remove “{connection.name}” from BaseSkiff? Database files and server data will be kept.'), QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Cancel)
        if response != QMessageBox.StandardButton.Yes:
            return
        def done(_):
            self.connections = self.service.list_connections()
            self._connection_tables.pop(connection.id, None)
            self._close_connection_tabs(connection.id)
            if self.current_connection and self.current_connection.id == connection.id:
                self._clear_workspace()
            self.rebuild_tree()
            if self._navigation == 'overview':
                self.render_overview()
        self.submit(partial(self.service.delete_connection, connection.id), done)

    def _close_connection_tabs(self, cid):
        if self._console_scope is not None and self._console_scope[0] == cid:
            self._save_console_now()
            self._console_scope = None
            self._active_workspace = None
        for scope in list(self._console_workspaces):
            if scope[0] == cid:
                self._console_workspaces.pop(scope, None)
        for index in reversed(range(self.table_tabs.count())):
            if self.table_tabs.tabData(index)[0] == cid:
                self.close_table_tab(index)
        for scope, state in list(self._connection_workspaces.items()):
            if scope[0] == cid:
                state.close()
                self._connection_workspaces.pop(scope)
        for scope in list(self._redis_key_pages):
            if scope[0] == cid:
                self._redis_key_pages.pop(scope)
                self._redis_key_page_searches.pop(scope, None)
        for scope in list(self._redis_search_tokens):
            if scope[0] == cid:
                self._redis_search_tokens.pop(scope)
                self._redis_search_dirty.discard(scope)
        self._redis_database_catalogs.pop(cid, None)
        for scope in list(self._redis_filter_updates):
            if scope[0] == cid:
                self._redis_filter_updates.pop(scope)
        self._redis_filtered_results = {scope for scope in self._redis_filtered_results if scope[0] != cid}
        for scope in list(self._redis_key_statuses):
            if scope[0] == cid:
                self._redis_key_statuses.pop(scope)
        for scope in list(self._redis_database_errors):
            if scope[0] == cid:
                self._redis_database_errors.pop(scope)
        self._redis_database_requests.pop(cid, None)
        self._redis_database_switches.pop(cid, None)
        self._redis_database_intents.pop(cid, None)
        self._redis_database_targets.pop(cid, None)
        getattr(self, '_catalog_requests', {}).pop(cid, None)

    def table_menu(self, table, point):
        index = table.indexAt(point)
        if not index.isValid():
            return
        menu = QMenu(self)
        menu.addAction(icon('copy'), self.t('复制单元格', 'Copy cell'), lambda: self.copy_text(table.item(index.row(), index.column()).text()))
        if table is self.data_table:
            menu.addAction(icon('edit'), self.t('编辑记录', 'Edit record'), self.edit_selected_row).setEnabled(self.edit_row_button.isEnabled())
            menu.addAction(icon('trash'), self.t('删除记录', 'Delete record'), self.delete_selected_rows).setEnabled(self.delete_row_button.isEnabled())
        menu.exec(table.viewport().mapToGlobal(point))

    def copy_text(self, text):
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText(text)

    def _sync_sql_metadata(self):
        connection = self.current_connection
        if not connection:
            return
        tables = self._connection_tables.get(connection.id, [])
        self.query_editor.set_metadata([t.qualified_name for t in tables],
            self._sql_metadata.get(self._console_scope_for(connection), {}))

    def refresh_sql_metadata(self):
        connection = self.current_connection
        if not connection or connection.kind not in {'SQLite', 'PostgreSQL', 'MySQL', 'Oracle'}:
            return
        scope = self._console_scope_for(connection)
        def load():
            tables = self._connection_operation('list_tables', connection)()
            # Warm only a bounded set; typing never queries the server.
            columns = {}
            for table in tables[:30]:
                columns[table.qualified_name] = [c.name for c in self._connection_operation('get_schema', connection, table.qualified_name)()]
            return tables, columns
        def loaded(result):
            tables, columns = result
            if not any(c.id == connection.id for c in self.connections):
                return
            self._connection_tables[connection.id] = tables
            self._sql_metadata[scope] = columns
            if self.current_connection and self._console_scope_for(self.current_connection) == scope:
                self._sync_sql_metadata()
                self.footer_detail.setText(self.t(f'提示缓存已更新 · {len(columns)} 张表', f'Completion cache updated · {len(columns)} tables'))
                self.rebuild_tree()
        self.submit(load, loaded, self.show_error)

    def show_sql_tools(self):
        menu = QMenu(self)
        menu.addAction(self.t('执行当前语句', 'Run current statement'), lambda: self.run_query(current_only=True))
        menu.addAction(self.t('格式化 SQL', 'Format SQL'), self.query_editor.format_sql)
        menu.addAction(self.t('执行计划', 'Execution plan'), lambda: self.run_query(explain=True))
        menu.addAction(self.t('刷新表和字段提示', 'Refresh completion metadata'), self.refresh_sql_metadata)
        menu.addSeparator()
        consoles = menu.addMenu(self.t('Console 工作区', 'Console workspaces'))
        connection = self.current_connection
        scope = self._console_scope_for(connection)
        consoles.addAction(self.t('默认 Console', 'Default Console'), lambda: self.show_console(connection))
        for record in self.service.list_console_documents(scope[0], scope[1]):
            consoles.addAction(record['name'], lambda _checked=False, record=record: self.show_console(connection, document_id=record['document_id']))
        consoles.addSeparator()
        consoles.addAction(self.t('新建 Console…', 'New Console…'), self.new_console_document)
        if self._console_scope and len(self._console_scope) == 3:
            consoles.addAction(self.t('重命名 Console…', 'Rename Console…'), self.rename_console_document)
            consoles.addAction(self.t('删除此 Console…', 'Delete this Console…'), self.delete_console_document)
        menu.addAction(self.t('打开 SQL 文件…', 'Open SQL file…'), self.open_sql_file)
        menu.addAction(self.t('保存 SQL 文件…', 'Save SQL file…'), self.save_sql_file)
        menu.exec(self.sql_tools_button.mapToGlobal(self.sql_tools_button.rect().bottomLeft()))

    def new_console_document(self):
        name, accepted = QInputDialog.getText(self, self.t('新建 Console', 'New Console'), self.t('名称', 'Name'))
        if accepted and name.strip():
            self.show_console(self.current_connection, document_id=uuid.uuid4().hex, document_name=name.strip())
            self._save_console_now()

    def rename_console_document(self):
        state = self._console_state()
        if not state or len(self._console_scope) != 3:
            return
        name, accepted = QInputDialog.getText(self, self.t('重命名 Console', 'Rename Console'), self.t('名称', 'Name'), text=state.console_name)
        if accepted and name.strip():
            state.console_name = name.strip()
            self._save_console_now()
            self.update_context()

    def delete_console_document(self):
        scope = self._console_scope
        if not scope or len(scope) != 3:
            return
        state = self._console_state()
        if state.query_running:
            self.show_error(self.t('请先中断此 Console 的查询。', 'Cancel this Console query first.'))
            return
        if QMessageBox.question(self, self.t('删除 Console', 'Delete Console'),
            self.t('删除此 Console 及其保存的 SQL？', 'Delete this Console and its saved SQL?'),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        connection = state.connection
        self.show_console(connection)
        self.service.delete_console_document(*scope)
        self._console_workspaces.pop(scope, None)

    def open_sql_file(self):
        path, _ = QFileDialog.getOpenFileName(self, self.t('打开 SQL 文件', 'Open SQL file'), '', 'SQL (*.sql);;All files (*)')
        if path:
            try:
                if Path(path).stat().st_size > 2 * 1024 * 1024:
                    raise ValueError(self.t('编辑器最多打开 2 MiB SQL 文件。', 'The editor supports SQL files up to 2 MiB.'))
                self.query_editor.setPlainText(Path(path).read_text(encoding='utf-8-sig'))
            except (OSError, UnicodeError, ValueError) as error:
                self.show_error(str(error))

    def save_sql_file(self):
        path, _ = QFileDialog.getSaveFileName(self, self.t('保存 SQL 文件', 'Save SQL file'), 'query.sql', 'SQL (*.sql)')
        if path:
            try:
                Path(path).write_text(self.query_editor.toPlainText(), encoding='utf-8')
            except OSError as error:
                self.show_error(str(error))

    def modify_sql_structure(self, connection, table):
        def load():
            return (self._connection_operation('get_schema', connection, table)(),
                    self._connection_operation('list_indexes', connection, table)())
        def loaded(metadata):
            dialog = SqlStructureDialog(connection.kind, table, *metadata, parent=self,
                                        language=self.language, read_only=connection.read_only)
            dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
            def apply(statements):
                def operation():
                    completed = 0
                    for statement in statements:
                        try:
                            self._connection_operation('execute', connection, statement)()
                        except Exception as error:
                            raise RuntimeError(self.t(f'已完成 {completed}/{len(statements)} 条；其余未执行。请关闭后重新打开检查实际结构。\n',
                                f'Completed {completed}/{len(statements)} statements; remaining steps were not executed. Reopen to inspect the actual structure.\n') + str(error)) from error
                        completed += 1
                    return load()
                def done(result):
                    dialog.mark_applied(*result)
                    self.refresh_catalog(connection)
                def failed(message):
                    dialog.show_apply_error(message)
                    self.refresh_catalog(connection)
                self.submit(operation, done, failed)
            dialog.apply_requested.connect(apply)
            dialog.show()
        self.submit(load, loaded, self.show_error)

    def show_export_menu(self, query=False):
        if not self.current_connection or self.current_connection.kind not in {'SQLite', 'PostgreSQL', 'MySQL', 'Oracle'}:
            self.export_data(query)
            return
        menu = QMenu(self)
        menu.addAction(self.t('当前显示数据 CSV…', 'Displayed data CSV…'), lambda: self.export_data(query))
        menu.addAction(self.t('数据库全部结果 CSV…', 'All database results CSV…'), lambda: self.export_all_data(query))
        source = self.query_export_button if query else self.export_button
        menu.exec(source.mapToGlobal(source.rect().bottomLeft()))

    def _update_export_progress(self, dialog, count):
        if dialog in self._export_jobs:
            dialog.setLabelText(self.t(f'已导出 {count:,} 行…', f'Exported {count:,} rows…'))

    def export_all_data(self, query=False):
        connection = self.current_connection
        if not connection or connection.kind not in {'SQLite', 'PostgreSQL', 'MySQL', 'Oracle'}:
            return
        options = {}
        if query:
            options['query'] = self.query_editor.current_statement()
        else:
            state = self._workspace_state()
            if not state or not state.table:
                return
            options = dict(table=state.table, search=state.search, sort_column=state.sort_column,
                           descending=state.descending, where_clause=state.where_clause, order_by=state.order_by)
        path, _ = QFileDialog.getSaveFileName(self, self.t('导出全部数据库结果', 'Export all database results'), 'export.csv', 'CSV (*.csv)')
        if not path:
            return
        token = CancellationToken()
        dialog = QProgressDialog(self.t('准备导出…', 'Preparing export…'), self.t('取消', 'Cancel'), 0, 0, self)
        dialog.setWindowTitle(self.t('后台导出 CSV', 'Exporting CSV'))
        dialog.setMinimumDuration(0)
        self._export_jobs[dialog] = token
        dialog.canceled.connect(lambda: token.cancel(lambda callback: self.submit(callback, lambda _: None, lambda _: None)))
        def finish():
            self._export_jobs.pop(dialog, None)
            dialog.close()
            dialog.deleteLater()
        def done(result):
            finish()
            self.footer_detail.setText(self.t(f'已导出 {result.affected:,} 行 · {path}', f'Exported {result.affected:,} rows · {path}'))
        def failed(message):
            finish()
            if token.cancelled:
                self.footer_detail.setText(self.t('导出已取消', 'Export cancelled'))
            else:
                self.show_error(message)
        self.submit(self._connection_operation('export_csv', connection, path, **options,
                    cancel_token=token, progress=lambda count: self.export_progress.emit(dialog, count)), done, failed)
        dialog.show()

    def export_data(self, query=False):
        # QPushButton.clicked passes a bool; the explicit query button uses True.
        result = self.query_result if query is True else self.current_result
        if query is not True and self.current_connection and self.current_connection.kind == 'Redis' and self._redis_snapshot:
            snapshot = self._redis_snapshot
            if snapshot.kind == 'hash':
                rows = [{'field': field, 'value': value} for field, value in snapshot.value.items()]
                result = QueryResult(columns=['field', 'value'], rows=rows)
            elif snapshot.kind == 'list':
                result = QueryResult(columns=['index', 'value'], rows=[{'index': index, 'value': value} for index, value in enumerate(snapshot.value)])
            elif snapshot.kind == 'set':
                result = QueryResult(columns=['member'], rows=[{'member': value} for value in snapshot.value])
            elif snapshot.kind == 'zset':
                result = QueryResult(columns=['member', 'score'], rows=[{'member': member, 'score': score} for member, score in snapshot.value.items()])
            elif snapshot.kind == 'stream':
                result = QueryResult(columns=['id', 'fields'], rows=[{'id': entry, 'fields': fields} for entry, fields in snapshot.value])
        if not result.columns:
            self.show_error(self.t('没有可导出的结果。', 'There are no results to export.'))
            return
        filename = 'redis-value' if isinstance(self.current_table, bytes) else self.current_table or 'query'
        filename = filename.replace('\x00', '_')
        path, _ = QFileDialog.getSaveFileName(self, self.t('导出当前结果', 'Export current results'), f'{filename}.csv', 'CSV (*.csv)')
        if not path:
            return
        try:
            with open(path, 'w', newline='', encoding='utf-8-sig') as stream:
                writer = csv.writer(stream)
                writer.writerow(result.columns)
                writer.writerows(['' if row.get(column) is None else display_value(row.get(column)) for column in result.columns] for row in result.rows)
            self.footer_detail.setText(self.t(f'已导出 {len(result.rows)} 行', f'Exported {len(result.rows)} rows'))
        except OSError as exc:
            self.show_error(str(exc))

    def show_workspace(self):
        self._navigation = 'workspace'
        self.stack.setCurrentIndex(0)
        self.update_context()
        self.update_nav()

    def show_overview(self):
        self._navigation = 'overview'
        self.stack.setCurrentIndex(1)
        self.breadcrumb.setText(self.t('工作空间   /   数据源概览', 'Workspace   /   Overview'))
        self.update_nav()
        self.render_overview()

    def update_nav(self):
        for key, widget in [('overview', self.overview_nav), ('workspace', self.workspace_nav), ('history', self.history_nav)]:
            widget.setProperty('active', key == self._navigation)
            widget.style().unpolish(widget)
            widget.style().polish(widget)

    def render_overview(self):
        self.stat_values[0].setText(str(len(self.connections)))
        self.stat_values[1].setText(str(len(self.tables)))
        self.submit(self.service.count_history, lambda count: self.stat_values[2].setText(str(count)))
        while self.cards_layout.count():
            child = self.cards_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        for i, connection in enumerate(self.connections):
            card = QFrame(objectName='card')
            card.setMinimumHeight(180)
            card_layout = layout(card, 22, 12)
            top = QHBoxLayout()
            db_icon = label()
            db_icon.setPixmap(icon('database', DATABASE_COLORS.get(connection.kind, '#7d9c79'), 28).pixmap(28, 28))
            top.addWidget(db_icon)
            top.addStretch()
            top.addWidget(label(connection.kind, 'badge'))
            top.addWidget(button('', 'settings', lambda checked=False, c=connection: self.connection_dialog(c), 'iconButton'))
            card_layout.addLayout(top)
            title = label(connection.name)
            title.setStyleSheet('font-size: 17px; font-weight: 600; color: #3d5543;')
            card_layout.addWidget(title)
            detail = self.t('本地示例数据库', 'Local sample database') if connection.is_demo else (Path(connection.path).name if connection.kind == 'SQLite' else f'{connection.host}:{connection.port} / {connection.database}')
            if connection.ssh_enabled:
                detail += f'  ·  SSH {connection.ssh_host}'
            card_layout.addWidget(label(detail, 'muted'))
            bottom = QHBoxLayout()
            bottom.addWidget(label(self.t('仅限读取', 'Read only') if connection.read_only else self.t('可读写', 'Read & write'), 'muted'))
            bottom.addStretch()
            bottom.addWidget(button(self.t('打开工作台  →', 'Open workspace  →'), '', lambda checked=False, c=connection: self.activate_connection(c), 'textButton'))
            card_layout.addLayout(bottom)
            self.cards_layout.addWidget(card, i // 2, i % 2)
        add_card = QFrame(objectName='card')
        add_layout = layout(add_card, 25, 12)
        add_card.setMinimumHeight(180)
        add_layout.addStretch()
        add_layout.addWidget(button(self.t('连接新的数据源', 'Connect a new data source'), 'plus', self.add_connection, 'secondaryButton'))
        text = label(self.t('让所有数据，各就其位。', 'Make room for your next idea.'), 'muted')
        text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        add_layout.addWidget(text)
        add_layout.addStretch()
        n = len(self.connections)
        self.cards_layout.addWidget(add_card, n // 2, n % 2)
        self.cards_layout.setRowStretch(n // 2 + 1, 1)
        self.cards_layout.setColumnStretch(0, 1)
        self.cards_layout.setColumnStretch(1, 1)

    def show_history(self, connection=None, database=None):
        self._navigation = 'history'
        self.stack.setCurrentIndex(2)
        if connection is None:
            self.breadcrumb.setText(self.t('工作空间   /   操作历史', 'Workspace   /   History'))
        else:
            self.breadcrumb.setText(self.t(f'{connection.name}   /   操作历史',
                                           f'{connection.name}   /   History'))
        self.update_nav()
        self._history_scope = ((connection.id, database) if connection is not None else None)
        self.submit(partial(self.service.list_history,
                            connection.id if connection is not None else None,
                            database), self.render_history)

    def render_history(self, entries):
        self._history = entries
        def local_time(value):
            try:
                return datetime.fromisoformat(value).astimezone().strftime('%Y-%m-%d %H:%M:%S')
            except ValueError:
                return value
        rows = [{'query': item.query[:150].replace('\n', ' '), 'connection': item.connection_name + (f' · DB{item.database}' if getattr(item, 'database', '') else ''), 'time': local_time(item.timestamp), 'rows': item.affected, 'duration': f'{item.elapsed_ms:.1f} ms', 'status': 'success' if item.success else 'failed'} for item in entries]
        self.populate_table(self.history_table, QueryResult(columns=['query', 'connection', 'time', 'rows', 'duration', 'status'], rows=rows))
        self.history_table.setHorizontalHeaderLabels([self.t('操作语句', 'Query'), self.t('数据源', 'Connection'), self.t('执行时间', 'Time'), self.t('行数', 'Rows'), self.t('耗时', 'Duration'), self.t('状态', 'Status')])
        self.history_table.setColumnWidth(0, 370)
        self.history_table.setColumnWidth(1, 185)
        self.history_table.setColumnWidth(2, 165)
        self.history_table.setColumnWidth(3, 65)
        self.history_table.setColumnWidth(4, 90)
        self.filter_history(self.history_search.text())

    def filter_history(self, text):
        text = text.lower()
        for i, entry in enumerate(self._history):
            self.history_table.setRowHidden(i, bool(text) and text not in (entry.query + entry.connection_name).lower())

    def select_history(self):
        indices = self.history_table.selectionModel().selectedRows()
        if indices and indices[0].row() < len(self._history):
            entry = self._history[indices[0].row()]
            self.history_detail.setPlainText(entry.query + (f'\n\n-- ERROR: {entry.error}' if entry.error else ''))

    def reopen_history(self, row, _column=0):
        if row >= len(self._history):
            return
        entry = self._history[row]
        query = entry.query
        if re.match(r'^(INSERT \S+ \{|UPDATE \S+ SET \{|DELETE \S+ WHERE \[|REDIS (?:ADD|UPDATE|DELETE) )', query):
            query = '-- ' + self.t('此记录来自表格操作，供回顾使用。', 'Recorded grid operation, for reference.') + '\n' + '\n'.join('-- ' + line for line in query.splitlines())
        connection = next((c for c in self.connections if c.id == entry.connection_id), None)
        if not connection:
            self.show_error(self.t('此记录的数据源连接已被移除。', 'The connection for this operation was removed.'))
            return
        if connection.kind == 'Redis' and getattr(entry, 'database', ''):
            connection = replace(connection, database=entry.database)
        if (not self.current_connection or self.current_connection.id != connection.id
                or self._redis_scope(self.current_connection) != self._redis_scope(connection)):
            self.activate_connection(connection, open_query=query)
            return
        if connection.kind in {'SQLite', 'PostgreSQL', 'MySQL', 'Oracle'}:
            self.show_console(connection, self._history_database(connection), content=query)
        else:
            self.show_workspace()
            self.tabs.setCurrentIndex(1)
            self.query_editor.setPlainText(query)

    def clear_history(self):
        response = QMessageBox.question(self, self.t('清空历史', 'Clear history'), self.t('确认清空本机的全部操作历史？此操作无法撤销。', 'Clear all local operation history? This cannot be undone.'), QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Cancel)
        if response == QMessageBox.StandardButton.Yes:
            scope = getattr(self, '_history_scope', None)
            self.submit(partial(self.service.clear_history,
                                scope[0] if scope else None,
                                scope[1] if scope else None),
                        lambda _: (self.history_detail.clear(), self.render_history([])))

    def show_error(self, message):
        self.footer_status.setText(self.t('操作失败', 'Operation failed'))
        QMessageBox.warning(self, self.t('操作未完成', 'Operation could not be completed'), message)

    def show_help(self):
        QMessageBox.information(self, 'BaseSkiff', self.t(
            'BaseSkiff 0.1.0\n一个轻巧、专注的数据库工作空间。\n\n• 点击左侧 + 创建连接，右键可编辑或移除。\n• 选中记录后点击工具栏编辑；选中多行后可批量删除。\n• 无主键的数据表通过查询编辑器修改。\n• 导出保存当前页或当前查询结果为 CSV。\n• ⌘ / Ctrl + Enter 执行查询；+ K 查找；+ N 新建连接。\n• 密码加密保存在本机，下次启动仍可使用。\n\nSQL：标准 SQL（单条语句）\nMongoDB：JSON 命令，例如 {"find": "users", "filter": {}}\nRedis：命令，例如 GET mykey\n\n本地示例数据可自由修改，不会连接外部服务器。',
            'BaseSkiff 0.1.0\nA thoughtful workspace for your databases.\n\n• Use + to add a connection; right-click to edit or remove.\n• Select a record and click Edit in the toolbar; select several to delete.\n• Tables without a primary key can be changed in the query editor.\n• Export saves the current page or current query result as CSV.\n• ⌘ / Ctrl + Enter runs queries; + K finds; + N adds a connection.\n• Passwords are encrypted on this computer and remain available after restarting.\n\nSQL: standard SQL, one statement at a time\nMongoDB: JSON commands, e.g. {"find": "users", "filter": {}}\nRedis: commands, e.g. GET mykey\n\nThe local playground is yours to explore, with no external connection.'))

    def closeEvent(self, event):
        if any(not self._confirm_pending_grid(state, discard=False) for state in self._workspace_tabs.values()):
            event.ignore()
            return
        self._save_session()
        self._save_console_now()
        for state in list(self._workspace_tabs.values()) + list(self._console_workspaces.values()):
            if state.query_cancel_token is not None:
                state.query_cancel_token.cancel(lambda callback: self.submit(callback, lambda _: None, lambda _: None))
            state.close()
        for token in self._export_jobs.values():
            token.cancel(lambda callback: self.submit(callback, lambda _: None, lambda _: None))
        self._closed = True
        self.settings.setValue('language', self.language)
        event.accept()
