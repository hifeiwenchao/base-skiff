"""Session tab ownership is independent from the shared Qt view."""
from baseskiff.models import Connection


def test_closed_tab_cannot_accept_result_after_identity_is_reopened():
    from baseskiff.ui.workspace_tabs import WorkspaceTab
    connection = Connection(name='Redis', kind='Redis', database='2')
    old = WorkspaceTab(connection, b'key\xff')
    request = old.request('rows')
    old.close()
    reopened = WorkspaceTab(connection, b'key\xff')
    assert old.identity == reopened.identity == (connection.id, '2', b'key\xff')
    assert not old.accepts('rows', request)
    assert reopened is not old


def test_latest_request_owns_results_without_canceling_other_operations():
    from baseskiff.ui.workspace_tabs import WorkspaceTab
    tab = WorkspaceTab(Connection(name='local'), 'customers')
    query = tab.request('query')
    rows = tab.request('rows')
    newer = tab.request('rows')
    assert not tab.accepts('rows', rows)
    assert tab.accepts('rows', newer)
    assert tab.accepts('query', query)
