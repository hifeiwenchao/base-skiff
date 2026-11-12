"""SSH metadata and secrets use the same secure lifecycle as database login."""
import json
from dataclasses import asdict, replace
from unittest.mock import patch

import pytest

from baseskiff.models import Connection
from baseskiff.service import DatabaseService


@pytest.fixture
def service(tmp_path):
    return DatabaseService(tmp_path)


def candidate(**kwargs):
    return Connection(name='SSH database', kind='MySQL', host='db.internal', database='test',
                      username='db-user', password='db-secret', ssh_enabled=True,
                      ssh_host='bastion.example', ssh_username='ssh-user',
                      ssh_password='ssh-secret', ssh_passphrase='key-secret', **kwargs)


def test_save_excludes_every_secret_from_metadata_and_return_values(service):
    connection = candidate()
    saved = service.save_connection(connection)
    assert saved.password == saved.ssh_password == saved.ssh_passphrase == ''
    body = service.metadata_path.read_bytes()
    for secret in ('db-secret', 'ssh-secret', 'key-secret'):
        assert secret.encode() not in body
    listing = next(c for c in service.list_connections() if c.id == connection.id)
    assert listing.ssh_host == 'bastion.example' and listing.ssh_enabled
    assert listing.password == listing.ssh_password == listing.ssh_passphrase == ''
    hydrated = service._connection(connection.id)
    assert (hydrated.password, hydrated.ssh_password, hydrated.ssh_passphrase) == ('db-secret', 'ssh-secret', 'key-secret')


def test_blank_credentials_on_edit_preserve_each_distinct_secret(service):
    original = service.save_connection(candidate())
    service.save_connection(replace(original, name='Renamed'))
    hydrated = service._connection(original.id)
    assert (hydrated.password, hydrated.ssh_password, hydrated.ssh_passphrase) == ('db-secret', 'ssh-secret', 'key-secret')
    service.save_connection(replace(original, ssh_password='changed'))
    hydrated = service._connection(original.id)
    assert hydrated.password == 'db-secret' and hydrated.ssh_password == 'changed'
    assert hydrated.ssh_passphrase == 'key-secret'


def test_existing_legacy_metadata_loads_without_ssh_migration(service):
    original = Connection(name='Old database', kind='MySQL')
    payload = {key: value for key, value in asdict(original).items() if not key.startswith('ssh_') and key != 'password'}
    with service._metadata() as db:
        db.execute('INSERT INTO connections VALUES (?, ?, ?)', (original.id, json.dumps(payload), service._now()))
    stored = service._connection(original.id)
    assert not stored.ssh_enabled and stored.ssh_port == 22


def test_test_connection_hydrates_ssh_credentials_without_saving_edits(service):
    saved = service.save_connection(candidate())
    observed = []
    class Probe:
        def test(self):
            return 'ok'
    with patch('baseskiff.service.adapter_for', lambda connection: observed.append(connection) or Probe()):
        assert service.test_connection(replace(saved, ssh_host='temporary.example')) == 'ok'
    assert observed[0].ssh_password == 'ssh-secret'
    assert observed[0].ssh_passphrase == 'key-secret'
    assert observed[0].password == 'db-secret'
    assert service._connection(saved.id).ssh_host == 'bastion.example'


def test_delete_removes_all_three_local_credential_items(service):
    saved = service.save_connection(candidate())
    with service._metadata() as db:
        assert db.execute('SELECT COUNT(*) FROM credentials').fetchone()[0] == 3
    service.delete_connection(saved.id)
    with service._metadata() as db:
        assert db.execute('SELECT COUNT(*) FROM credentials').fetchone()[0] == 0
    assert service._password(saved.id) == ''


def test_database_and_ssh_passwords_are_distinct_after_reopening(service):
    saved = service.save_connection(candidate())
    reopened = DatabaseService(service.data_dir)
    assert reopened._password(saved.id) == 'db-secret'
    assert reopened._password(f'{saved.id}:ssh_password') == 'ssh-secret'
    assert reopened._password(f'{saved.id}:ssh_passphrase') == 'key-secret'


@pytest.mark.parametrize('changes', [{'ssh_host': ''}, {'ssh_username': ''}, {'ssh_port': 0},
                                     {'ssh_auth_method': 'invalid'}, {'kind': 'SQLite', 'path': '/tmp/db.sqlite'}])
def test_invalid_enabled_ssh_configuration_not_persisted(service, changes):
    connection = replace(candidate(), **changes)
    with pytest.raises(ValueError):
        service.save_connection(connection)
    assert all(c.id != connection.id for c in service.list_connections())
