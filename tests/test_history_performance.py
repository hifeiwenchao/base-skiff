"""History summaries should not copy full query bodies unnecessarily."""
import pytest

from baseskiff.models import HistoryEntry
from baseskiff.ui.window import MainWindow


class SummaryService:
    def list_connections(self):
        return []

    def count_history(self):
        return 42

    def list_history(self):
        return []


@pytest.fixture
def window(qtbot):
    widget = MainWindow(SummaryService(), language='en')
    qtbot.addWidget(widget)
    return widget


def entry(query, name='Local'):
    return HistoryEntry('h', 'c', name, query, '2026-11-12T10:00:00', 1, 2, True)


def test_overview_only_requests_history_count(window, monkeypatch):
    monkeypatch.setattr(window.service, 'list_history',
                        lambda: pytest.fail('Overview must not load complete history bodies'))
    monkeypatch.setattr(window, 'submit', lambda function, callback, *_args: callback(function()))
    window.render_overview()
    assert window.stat_values[2].text() == '42'


def test_history_preview_formats_only_visible_prefix(window):
    class LargeQuery(str):
        def replace(self, *args, **kwargs):
            pytest.fail('Preview must slice before formatting a full query')

    query = LargeQuery('SELECT\n' + 'x' * 10000 + ' END')
    window.render_history([entry(query)])
    assert window.history_table.item(0, 0).text() == query[:150].replace('\n', ' ')
    assert window._history[0].query is query


def test_clearing_history_search_restores_rows_without_reading_query_bodies(window):
    class UnreadQuery(str):
        def __add__(self, _other):
            pytest.fail('An empty filter must not copy query bodies')

    window.render_history([entry('hidden'), entry('visible')])
    window.filter_history('visible')
    assert window.history_table.isRowHidden(0)
    window._history = [entry(UnreadQuery('hidden')), entry(UnreadQuery('visible'))]
    window.filter_history('')
    assert all(not window.history_table.isRowHidden(index) for index in range(2))


def test_history_search_still_finds_full_case_insensitive_queries_and_names(window):
    query = 'x' * 1000 + 'NeEdLe'
    window.render_history([entry(query), entry('other', 'MixedName')])
    window.filter_history('needle')
    assert not window.history_table.isRowHidden(0)
    assert window.history_table.isRowHidden(1)
    window.filter_history('mixedname')
    assert window.history_table.isRowHidden(0)
    assert not window.history_table.isRowHidden(1)
