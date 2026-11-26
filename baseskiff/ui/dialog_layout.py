"""Shared native dialog sizing, forms and action conventions."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialogButtonBox, QFormLayout, QLabel


def fit_dialog(dialog, width, height, minimum_width=440):
    """Keep initial client geometry inside the current logical screen."""
    screen = dialog.screen()
    available = screen.availableGeometry() if screen else None
    max_width = max(280, available.width() - 48) if available else width
    max_height = max(240, available.height() - 80) if available else height
    dialog.setMinimumSize(min(minimum_width, max_width), min(260, max_height))
    dialog.resize(min(width, max_width), min(height, max_height))


def configure_form(form):
    """Let translated labels wrap and move fields below labels when necessary."""
    form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
    form.setHorizontalSpacing(18)
    form.setVerticalSpacing(10)
    for row in range(form.rowCount()):
        item = form.itemAt(row, QFormLayout.ItemRole.LabelRole)
        label = item.widget() if item else None
        if isinstance(label, QLabel):
            label.setWordWrap(True)
            label.setTextFormat(Qt.TextFormat.PlainText)


def configure_buttons(buttons):
    # Explicit order is the same on every desktop: cancel, then primary.
    buttons.setStyleSheet('QDialogButtonBox { button-layout: 3; }')
    buttons.setCenterButtons(False)
    for button in buttons.buttons():
        button.setMinimumHeight(34)
        role = buttons.buttonRole(button)
        if role in (QDialogButtonBox.ButtonRole.AcceptRole, QDialogButtonBox.ButtonRole.YesRole):
            button.setObjectName('primaryButton')
            button.setDefault(True)
