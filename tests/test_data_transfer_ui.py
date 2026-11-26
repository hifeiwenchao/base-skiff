from baseskiff.models import ColumnInfo
from baseskiff.ui.data_transfer import CsvImportDialog, CsvOptionsDialog


def test_csv_options_and_explicit_mapping(qtbot):
    options = CsvOptionsDialog(language='en')
    qtbot.addWidget(options)
    assert options.options().null_value == '\\N'
    dialog = CsvImportDialog(['id','different'], [['1','text']], [ColumnInfo('id'),ColumnInfo('name')],language='en')
    qtbot.addWidget(dialog)
    assert dialog.mapping() == {0:'id'}
    dialog.mapping_widgets[1].setCurrentIndex(dialog.mapping_widgets[1].findData('name'))
    dialog.accept()
    assert dialog.result() == dialog.DialogCode.Accepted


def test_duplicate_mapping_cannot_accept(qtbot):
    dialog = CsvImportDialog(['id','other'], [], [ColumnInfo('id')],language='en')
    qtbot.addWidget(dialog)
    dialog.mapping_widgets[1].setCurrentIndex(1)
    dialog.accept()
    assert dialog.result() == 0
    assert 'duplicate' in dialog.error.text()
