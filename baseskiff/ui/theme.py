"""The quiet, sage visual system shared by the native Qt widgets."""
STYLE = '''
* { font-family: "Inter", ".AppleSystemUIFont", "Segoe UI", "Noto Sans", "PingFang SC", sans-serif; font-size: 13px; color: #303c36; }
QMainWindow, QDialog { background: #ffffff; }
QWidget#sidebar { background: #f5f7f4; border-right: 1px solid #e4e9e4; }
QWidget#workspace, QWidget#page { background: #ffffff; }
QWidget#topbar { background: #ffffff; border-bottom: 1px solid #e9ede9; }
QWidget#footer { background: #fafbf9; border-top: 1px solid #e5eae5; }
QLabel { background: transparent; }
QLabel#brand { font-size: 22px; font-weight: 700; letter-spacing: -0.8px; color: #244f3f; }
QLabel#eyebrow { color: #89928c; font-size: 10px; font-weight: 600; letter-spacing: 1.6px; }
QLabel#heading { color: #25392f; font-size: 26px; font-weight: 650; letter-spacing: -0.7px; }
QLabel#subheading, QLabel#muted { color: #8a928d; font-size: 12px; }
QLabel#dialogTitle { color: #25392f; font-size: 23px; font-weight: 650; }
QLabel#fieldLabel { color: #536158; font-size: 12px; font-weight: 550; }
QLabel#badge { color: #47735c; background: #edf5ef; border: 1px solid #dceade; border-radius: 5px; padding: 3px 8px; font-size: 11px; }
QLabel#statusBadge { color: #4c8765; background: #eef6ef; border-radius: 5px; padding: 4px 8px; font-size: 11px; }
QLabel#tableSubtitle { color: #8a928d; font-size: 12px; padding-top: 4px; }
QLabel#error { color: #b75c55; background: #fff3f0; border: 1px solid #f1d9d2; border-radius: 6px; padding: 9px 12px; }
QPushButton, QToolButton { background: #ffffff; border: 1px solid #dfe5df; border-radius: 6px; padding: 7px 12px; color: #536259; min-height: 18px; }
QPushButton:hover, QToolButton:hover { background: #f1f5f0; border-color: #cad7cc; }
QPushButton:pressed { background: #e8f0e7; }
QPushButton:disabled, QToolButton:disabled { color: #b3bcb5; background: #f6f8f5; border-color: #edf0eb; }
QPushButton#primaryButton { background: #397b60; border: 1px solid #397b60; color: #ffffff; font-weight: 550; padding: 8px 15px; }
QPushButton#primaryButton:hover { background: #2d684f; border-color: #2d684f; }
QPushButton#primaryButton:disabled { background: #aec6b6; border-color: #aec6b6; }
QPushButton#secondaryButton { background: #f9fbf8; }
QPushButton#dangerButton { color: #b45f58; border-color: #ebd6d0; }
QPushButton#navButton { text-align: left; background: transparent; border: none; padding: 10px 14px; border-radius: 6px; color: #748178; }
QPushButton#navButton:hover { background: #eaf0e8; }
QPushButton#navButton[active="true"] { background: #e4eee3; color: #38674e; font-weight: 600; }
QPushButton#iconButton { background: transparent; border: none; padding: 5px; border-radius: 5px; }
QPushButton#iconButton:hover { background: #edf3eb; }
QPushButton#textButton { background: transparent; border: none; color: #6c8372; padding: 5px 3px; }
QLineEdit, QSpinBox, QComboBox { background: #ffffff; border: 1px solid #e0e6df; border-radius: 6px; padding: 8px 11px; min-height: 17px; selection-background-color: #d9e9db; }
QSpinBox::up-button, QSpinBox::down-button { subcontrol-origin: border; width: 0px; height: 0px; }
QLineEdit:focus, QSpinBox:focus, QComboBox:focus { border: 1px solid #79a78a; }
QLineEdit:disabled { background: #f4f6f2; color: #9aa29a; }
QLineEdit#sidebarSearch { background: #edf1eb; border: 1px solid #e5eae1; font-size: 12px; }
QLineEdit#tableSearch { border: 1px solid #e3e8e1; padding-left: 10px; }
QComboBox::drop-down { border: none; width: 23px; }
QComboBox QAbstractItemView { border: 1px solid #e0e6df; background: #ffffff; selection-background-color: #e7f0e5; color: #3a4c3f; }
QTreeWidget { background: transparent; border: none; outline: none; font-size: 12px; }
QTreeWidget::item { height: 33px; margin: 1px 0; border-radius: 5px; padding-left: 2px; }
QTreeWidget::item:selected { background: #e3ede1; color: #35664b; }
QTreeWidget::item:hover:!selected { background: #edf1e9; }
QTreeWidget::branch { background: transparent; }
QTreeWidget::branch:has-children { image: none; }
QTreeWidget::branch:selected { background: #e3ede1; }
QTabWidget::pane { border: none; border-top: 1px solid #e4eae3; top: -1px; }
QTabBar::tab { background: #ffffff; color: #839085; border: none; border-bottom: 2px solid transparent; padding: 12px 19px 13px 11px; margin-right: 13px; }
QTabBar::tab:selected { color: #397b60; border-bottom: 2px solid #397b60; font-weight: 600; }
QTabBar::tab:hover { color: #3e7557; }
QTableWidget { background: #ffffff; alternate-background-color: #fcfdfb; border: none; gridline-color: #edf0eb; selection-background-color: #eef5eb; selection-color: #314e3a; outline: none; font-size: 12px; }
QTableWidget::item { padding: 7px 13px; border: none; border-bottom: 1px solid #edf0eb; }
QTableWidget::item:selected { background: #edf4e9; color: #35563e; }
QTableWidget::item:focus { border: none; outline: none; }
QHeaderView { background: #f8faf6; }
QHeaderView::section { background: #f8faf6; color: #7c897f; border: none; border-bottom: 1px solid #e5eae1; border-right: 1px solid #edf0e8; padding: 11px 13px; font-size: 11px; font-weight: 550; }
QTableCornerButton::section { background: #f8faf6; border: none; border-bottom: 1px solid #e5eae1; }
QPlainTextEdit { border: 1px solid #e3e9e1; border-radius: 7px; background: #fafcf8; padding: 12px; font-family: "Menlo", "Consolas", "DejaVu Sans Mono", monospace; font-size: 12px; selection-background-color: #d9e9d6; }
QCheckBox { spacing: 7px; }
QCheckBox::indicator { width: 14px; height: 14px; border: 1px solid #d1dcd0; background: #fff; border-radius: 3px; }
QCheckBox::indicator:checked { background: #589570; border: 3px solid #d8e8d5; }
QScrollBar:vertical { background: transparent; width: 8px; margin: 2px; }
QScrollBar::handle:vertical { background: #d5ded2; border-radius: 3px; min-height: 30px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal { background: transparent; height: 8px; margin: 2px; }
QScrollBar::handle:horizontal { background: #d5ded2; border-radius: 3px; min-width: 30px; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
QScrollArea { border: none; background: transparent; }
QFrame#card { background: #ffffff; border: 1px solid #e2e9df; border-radius: 9px; }
QFrame#tip { background: #f3f7ef; border: 1px solid #e3ebdc; border-radius: 7px; }
QFrame#inspector { background: #fcfdfb; border-left: 1px solid #e4eae1; }
QSplitter::handle { background: #e7ede3; height: 1px; width: 1px; }
QSplitter#workspaceSplitter::handle:horizontal { background: #f2f5ef; width: 6px; }
QSplitter#workspaceSplitter::handle:horizontal:hover { background: #aac3af; }
QSplitter#workspaceSplitter::handle:horizontal:pressed { background: #79a78a; }
QMenu { background: #fff; border: 1px solid #dce5d9; border-radius: 7px; padding: 5px; }
QMenu::item { padding: 8px 20px; border-radius: 4px; }
QMenu::item:selected { background: #eaf2e6; }
QToolTip { color: #fff; background: #355542; border: none; padding: 5px 8px; border-radius: 4px; }
QDialogButtonBox { background: transparent; }
QMessageBox { background: #ffffff; }
QTabBar#tableTabs { background: #f4f7f0; border-bottom: 1px solid #dde6d8; }
QTabBar#tableTabs::tab { background: #edf2e9; color: #71816a; padding: 11px 15px; margin: 5px 2px 0 0; border: 1px solid #e0e7db; border-bottom: none; border-top-left-radius: 6px; border-top-right-radius: 6px; min-width: 90px; max-width: 270px; }
QTabBar#tableTabs::tab:selected { background: #ffffff; color: #365f43; border-top: 2px solid #78a282; }
QTabBar#tableTabs::tab:hover:!selected { background: #e4eddf; }
QTabBar#tableTabs QToolButton { background: #f4f7f0; border: none; }
QPushButton#tableTabClose { min-width: 0; max-width: 22px; min-height: 0; max-height: 22px; padding: 0; margin: 0; border: none; border-radius: 4px; background: transparent; }
QPushButton#tableTabClose:hover { background: #dce8d7; }
QPushButton#tableTabClose:pressed { background: #ccddc5; }
QWidget#redisKeySearch { background: #f3f7ef; border: 1px solid #e0e8db; border-radius: 5px; padding: 0; }
QWidget#redisKeySearch QLabel { background: transparent; border: none; font-size: 10px; }
QWidget#redisKeySearch QLineEdit { min-width: 0; padding: 5px 3px; font-size: 11px; }
QWidget#redisKeySearch QComboBox { min-width: 0; padding: 4px 18px 4px 5px; font-size: 10px; }
'''
