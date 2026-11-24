from PySide6.QtCore import Qt

from baseskiff.models import ColumnInfo, IndexInfo
from baseskiff.ui.sql_structure import SqlStructureDialog


def test_dialog_stages_locally_and_emits_only_on_apply(qtbot):
    dialog = SqlStructureDialog('SQLite', 'items', [ColumnInfo('id', 'INTEGER', True), ColumnInfo('value')], [], language='en')
    qtbot.addWidget(dialog)
    dialog.show()
    emitted = []
    dialog.apply_requested.connect(emitted.append)
    qtbot.mouseClick(dialog.add_column_button, Qt.MouseButton.LeftButton)
    dialog.name_edit.setText('description')
    dialog.type_edit.setText('TEXT')
    qtbot.mouseClick(dialog.stage_button, Qt.MouseButton.LeftButton)
    assert not emitted
    assert 'ADD COLUMN "description" TEXT' in dialog.sql_preview.toPlainText()
    qtbot.mouseClick(dialog.apply_button, Qt.MouseButton.LeftButton)
    assert emitted == [dialog.statements]
    assert not dialog.apply_button.isEnabled()


def test_existing_sqlite_type_and_constraint_index_are_disabled(qtbot):
    dialog = SqlStructureDialog('SQLite', 'items', [ColumnInfo('id', 'INTEGER', True)], [IndexInfo('pk', ('id',), True, 'pk')])
    qtbot.addWidget(dialog)
    dialog.tree.setCurrentItem(dialog.tree.topLevelItem(0).child(0))
    assert not dialog.type_edit.isEnabled()
    dialog.tree.setCurrentItem(dialog.tree.topLevelItem(1).child(0))
    assert not dialog.drop_button.isEnabled()


def test_reset_discards_and_read_only_prevents_staging(qtbot):
    dialog = SqlStructureDialog('PostgreSQL', 'items', [ColumnInfo('value')], [], read_only=True)
    qtbot.addWidget(dialog)
    assert not dialog.add_column_button.isEnabled()
    assert not dialog.apply_button.isEnabled()


def test_partial_failure_locks_draft_until_catalog_reload(qtbot):
    dialog = SqlStructureDialog('SQLite', 'items', [ColumnInfo('id', 'INTEGER', True)], [], language='en')
    qtbot.addWidget(dialog)
    dialog._new_column()
    dialog.name_edit.setText('value')
    dialog._stage()
    original = dialog.statements
    dialog.show_apply_error('First statement committed; reload required.')
    dialog._refresh()
    dialog._reset()
    dialog.set_busy(False)
    assert dialog.statements == original
    assert not dialog.body.isEnabled()
    assert not dialog.apply_button.isEnabled()
    assert not dialog.reset_button.isEnabled()
    dialog.mark_applied([ColumnInfo('id', 'INTEGER', True), ColumnInfo('value')], [])
    assert dialog.body.isEnabled()
    assert dialog.statements == []
