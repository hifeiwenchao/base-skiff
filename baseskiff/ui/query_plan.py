"""Native tree rendering of EXPLAIN output and execution timing."""
import json

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QTreeWidget, QTreeWidgetItem, QVBoxLayout

from baseskiff.query_plan import normalize_query_plan
from baseskiff.ui.dialog_layout import configure_buttons, fit_dialog
from baseskiff.ui.theme import STYLE


class QueryPlanDialog(QDialog):
    def __init__(self, result, parent=None, language='zh'):
        super().__init__(parent)
        self.setStyleSheet(STYLE)
        zh = language == 'zh'
        self.setWindowTitle('执行计划' if zh else 'Execution plan')
        fit_dialog(self, 950, 600)
        layout = QVBoxLayout(self)
        note = QLabel((f'获取计划耗时 {result.elapsed_ms:.1f} ms。以下为优化器估算，不是实际执行耗时。' if zh else
                       f'Plan retrieved in {result.elapsed_ms:.1f} ms. Values are optimizer estimates, not measured execution timings.'))
        note.setWordWrap(True)
        layout.addWidget(note)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(['操作' if zh else 'Operation', '估算 / 详情' if zh else 'Estimates / details'])
        nodes = normalize_query_plan(result)
        items = {}
        for node in nodes:
            text = ', '.join(f'{key}: {value}' for key,value in node.estimates.items())
            item = QTreeWidgetItem([node.label, text])
            item.setToolTip(0, node.label)
            item.setToolTip(1, json.dumps(node.estimates, ensure_ascii=False, default=str, indent=2))
            items[node.id] = item
        attached = set()
        for node in nodes:
            parent = items.get(node.parent)
            # Invalid/looped parent ids remain visible as roots.
            if parent is not None and node.parent in attached and node.parent != node.id:
                parent.addChild(items[node.id])
            else:
                self.tree.addTopLevelItem(items[node.id])
            attached.add(node.id)
        self.tree.expandToDepth(2)
        self.tree.setColumnWidth(0, 480)
        self.tree.setTextElideMode(Qt.TextElideMode.ElideRight)
        layout.addWidget(self.tree, 1)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.button(QDialogButtonBox.StandardButton.Close).setText('关闭' if zh else 'Close')
        close.rejected.connect(self.reject)
        configure_buttons(close)
        layout.addWidget(close)
