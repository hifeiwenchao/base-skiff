"""Live background operations, showing only actions that can actually cancel."""
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)


class TaskPanel(QDialog):
    def __init__(self, tasks, parent=None, language='zh'):
        super().__init__(parent)
        self.tasks=tasks
        self.language=language
        self.setWindowTitle('后台任务' if language=='zh' else 'Background tasks')
        self.resize(760,350)
        outer=QVBoxLayout(self)
        outer.addWidget(QLabel('读取任务等待完成；可中断的操作提供取消按钮。' if language=='zh' else 'Reads finish in the background. Cancellable operations offer a Cancel button.'))
        self.table=QTableWidget(0,4)
        self.table.setHorizontalHeaderLabels(['来源','操作','耗时',''] if language=='zh' else ['Source','Operation','Elapsed',''])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        outer.addWidget(self.table)
        close=QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.button(QDialogButtonBox.StandardButton.Close).setText('关闭' if language=='zh' else 'Close')
        close.rejected.connect(self.reject)
        outer.addWidget(close)
        self.timer=QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(500)
        self.refresh()

    def refresh(self):
        from time import monotonic
        entries=list(self.tasks())
        self.table.setRowCount(len(entries))
        for row,worker in enumerate(entries):
            info=worker.presentation
            names={'execute':('执行查询','Run query'),'explain':('获取执行计划','Get execution plan'),
                   'fetch_rows':('加载数据','Load data'),'get_schema':('加载表结构','Load schema'),
                   'list_tables':('加载数据表','Load tables'),'export_csv':('导出 CSV','Export CSV'),
                   'import_csv':('导入 CSV','Import CSV'),'apply_grid_changes':('提交修改','Commit changes'),
                   'read_redis_key':('加载 Redis 值','Load Redis value'),'continue_redis_value':('加载更多值','Load more values'),
                   'search_redis_keys':('搜索 Redis 键','Search Redis keys')}
            operation=names.get(info['operation'],('后台操作','Background operation'))[0 if self.language=='zh' else 1]
            for column,value in enumerate([info['source'],operation,f'{monotonic()-info["started"]:.1f}s']):
                self.table.setItem(row,column,QTableWidgetItem(str(value)))
            self.table.removeCellWidget(row,3)
            if info['cancel'] is not None:
                button=QPushButton('取消' if self.language=='zh' else 'Cancel')
                button.clicked.connect(info['cancel'])
                self.table.setCellWidget(row,3,button)
