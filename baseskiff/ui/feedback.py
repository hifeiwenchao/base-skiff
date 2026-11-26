"""Non-blocking feedback with copyable full error details."""
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QFrame, QHBoxLayout, QLabel, QPushButton


class FeedbackBar(QFrame):
    def __init__(self,parent=None,language='zh'):
        super().__init__(parent)
        self.language=language
        self.setObjectName('feedbackBar')
        self.full_message=''
        row=QHBoxLayout(self)
        row.setContentsMargins(12,7,12,7)
        self.message_label=QLabel()
        self.message_label.setTextFormat(Qt.TextFormat.PlainText)
        self.message_label.setWordWrap(True)
        self.message_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        row.addWidget(self.message_label,1)
        self.copy_button=QPushButton()
        self.copy_button.clicked.connect(lambda:QApplication.clipboard().setText(self.full_message))
        row.addWidget(self.copy_button)
        self.close_button=QPushButton('×')
        self.close_button.clicked.connect(self.hide)
        row.addWidget(self.close_button)
        self.timer=QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.hide)
        self.hide()

    def show_message(self,message,*,error=False):
        self.timer.stop()
        self.full_message=str(message)
        preview=' '.join(self.full_message.split())
        visible=preview[:240]+('…' if len(preview)>240 else '')
        self.message_label.setMaximumHeight(62)
        self.message_label.setText(visible)
        self.message_label.setToolTip(self.full_message)
        self.copy_button.setText('复制详情' if self.language=='zh' else 'Copy details')
        self.close_button.setToolTip('关闭提示' if self.language=='zh' else 'Dismiss')
        self.setStyleSheet('QFrame#feedbackBar {background:'+('#fff3ef' if error else '#eef6ef')+';border:1px solid '+('#dfb7ac' if error else '#c2d9c7')+';border-radius:6px;}')
        self.show()
        if not error:
            self.timer.start(5000)
