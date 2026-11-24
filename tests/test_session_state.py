from baseskiff.models import Connection
from baseskiff.ui.session_state import decode_workspace, encode_workspace
from baseskiff.ui.workspace_tabs import WorkspaceTab


def test_workspace_roundtrip_keeps_binary_key_without_password():
    connection = Connection('Redis', kind='Redis', database='2', password='secret')
    state = WorkspaceTab(connection, b'\xff:key', search='keep', where_clause='id>3', order_by='id DESC')
    state.pending_inserts.append({'secret': 'unsaved'})
    encoded = encode_workspace(state)
    assert 'secret' not in str(encoded)
    restored = decode_workspace(encoded, [connection])
    assert restored.table == b'\xff:key'
    assert restored.connection.database == '2'
    assert restored.search == 'keep'
    assert restored.pending_inserts == []
    assert restored.dirty and restored.schema_dirty


def test_session_ignores_missing_connection_and_invalid_keys():
    assert decode_workspace({'connection_id': 'gone'}, []) is None
    connection = Connection('Local')
    assert decode_workspace({'connection_id': connection.id, 'key_base64': '%wrong%'}, [connection]) is None
