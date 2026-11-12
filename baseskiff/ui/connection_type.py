"""Choose a database before entering its connection configuration."""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from baseskiff.i18n import Translator
from baseskiff.models import DATABASE_COLORS, DATABASE_TYPES
from baseskiff.ui.icons import icon
from baseskiff.ui.theme import STYLE

_PICKER_STYLE = '''
QPushButton#databaseTypeCard {
    background: #ffffff; border: 1px solid #e0e7df; border-radius: 9px;
    padding: 17px 18px; text-align: left; font-size: 13px; font-weight: 550;
}
QPushButton#databaseTypeCard:hover {
    background: #f5f9f2; border-color: #a9c5ad;
}
QPushButton#databaseTypeCard:focus {
    background: #f1f7ed; border: 2px solid #79a78a; padding: 16px 17px;
}
QPushButton#databaseTypeCard:pressed {
    background: #e8f1e3; border-color: #79a78a;
}
'''


class ConnectionTypeDialog(QDialog):
    """Return a selected database kind; selection itself has no side effects."""

    def __init__(self, parent: QWidget | None = None, language: str = 'zh') -> None:
        super().__init__(parent)
        self.selected_kind: str | None = None
        self.translator = Translator(language)
        self.tr = self.translator.tr
        self.setObjectName('connectionTypeDialog')
        self.setWindowTitle(self.tr('choose_database_type'))
        self.setStyleSheet(STYLE + _PICKER_STYLE)
        self.setMinimumWidth(590)
        self.resize(650, 470)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 25, 28, 24)
        outer.setSpacing(15)
        title = QLabel(self.windowTitle())
        title.setObjectName('dialogTitle')
        title.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        outer.addWidget(title)
        subtitle = QLabel(self.tr('choose_database_type_hint'))
        subtitle.setObjectName('muted')
        subtitle.setWordWrap(True)
        subtitle.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        outer.addWidget(subtitle)
        outer.addSpacing(5)

        choices = QGridLayout()
        choices.setHorizontalSpacing(13)
        choices.setVerticalSpacing(13)
        self.kind_buttons: dict[str, QPushButton] = {}
        descriptions = {'SQLite': 'type_sqlite', 'MongoDB': 'type_mongodb',
                        'Redis': 'type_redis'}
        previous: QPushButton | None = None
        for index, kind in enumerate(DATABASE_TYPES):
            description = self.tr(descriptions.get(kind, 'type_relational'))
            button = QPushButton(f'{kind}\n{description}', self)
            button.setObjectName('databaseTypeCard')
            button.setAccessibleName(kind)
            button.setAccessibleDescription(description)
            button.setIcon(icon('database', DATABASE_COLORS[kind], 28))
            button.setIconSize(QSize(28, 28))
            button.setMinimumHeight(82)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setAutoDefault(True)
            button.clicked.connect(lambda checked=False, value=kind: self._select_kind(value))
            choices.addWidget(button, index // 2, index % 2)
            self.kind_buttons[kind] = button
            if previous is not None:
                self.setTabOrder(previous, button)
            previous = button
        outer.addLayout(choices, 1)
        outer.addSpacing(5)

        footer = QHBoxLayout()
        footer.addStretch()
        self.cancel_button = QPushButton(self.tr('cancel'), self)
        self.cancel_button.setObjectName('secondaryButton')
        self.cancel_button.setMinimumHeight(34)
        self.cancel_button.setAutoDefault(True)
        self.cancel_button.clicked.connect(self.reject)
        footer.addWidget(self.cancel_button)
        outer.addLayout(footer)
        if previous is not None:
            self.setTabOrder(previous, self.cancel_button)
        self.kind_buttons[DATABASE_TYPES[0]].setFocus()

    def _select_kind(self, kind: str) -> None:
        self.selected_kind = kind
        self.accept()

    def reject(self) -> None:
        self.selected_kind = None
        super().reject()
