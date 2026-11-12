"""Local encrypted credentials survive restarts without system credential access."""
import builtins
import os
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest
from cryptography.fernet import Fernet

from baseskiff.models import Connection
from baseskiff.service import DatabaseService


@pytest.fixture(autouse=True)
def block_system_credentials(monkeypatch):
    attempted = []
    original_import = builtins.__import__

    def guard(name, *args, **kwargs):
        if name == 'keyring' or name.startswith('keyring.'):
            attempted.append(name)
            raise AssertionError('System credential storage must not be loaded')
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, '__import__', guard)
    return attempted


def connection(index=0):
    return Connection(f'Local {index}', kind='MySQL', password=f'database secret {index}',
                      ssh_password=f'SSH secret {index}', ssh_passphrase=f'key phrase {index}')


def test_all_three_secrets_survive_a_new_service_and_new_process(tmp_path):
    original = connection()
    saved = DatabaseService(tmp_path).save_connection(original)
    reopened = DatabaseService(tmp_path)
    assert reopened._connection(saved.id) == original
    code = """import sys
import builtins
original_import = builtins.__import__
def guard(name, *args, **kwargs):
    if name == 'keyring' or name.startswith('keyring.'):
        raise AssertionError('System credential storage must not be loaded')
    return original_import(name, *args, **kwargs)
builtins.__import__ = guard
from baseskiff.service import DatabaseService
c = DatabaseService(sys.argv[1])._connection(sys.argv[2])
assert (c.password, c.ssh_password, c.ssh_passphrase) == ('database secret 0', 'SSH secret 0', 'key phrase 0')
"""
    subprocess.run([sys.executable, '-c', code, str(tmp_path), saved.id], check=True,
                   capture_output=True, text=True, timeout=20)


def test_secrets_are_encrypted_on_disk_and_excluded_from_public_metadata(tmp_path):
    service = DatabaseService(tmp_path)
    original = connection()
    saved = service.save_connection(original)
    key_file = tmp_path / 'credentials.key'
    cipher = Fernet(key_file.read_bytes())
    with sqlite3.connect(service.metadata_path) as database:
        payload = database.execute('SELECT payload FROM connections WHERE id=?', (saved.id,)).fetchone()[0]
        tokens = database.execute('SELECT value FROM credentials').fetchall()
    assert len(tokens) == 3
    assert {cipher.decrypt(token[0]).decode() for token in tokens} == {
        original.password, original.ssh_password, original.ssh_passphrase}
    for value in (original.password, original.ssh_password, original.ssh_passphrase):
        assert value not in payload
        assert value.encode() not in service.metadata_path.read_bytes()
    listed = next(value for value in service.list_connections() if value.id == saved.id)
    assert (saved.password, saved.ssh_password, saved.ssh_passphrase) == ('', '', '')
    assert (listed.password, listed.ssh_password, listed.ssh_passphrase) == ('', '', '')
    if os.name != 'nt':
        assert key_file.stat().st_mode & 0o777 == 0o600
        assert service.metadata_path.stat().st_mode & 0o777 == 0o600


def test_keyring_is_never_imported_for_save_read_test_or_delete(tmp_path, block_system_credentials):
    service = DatabaseService(tmp_path)
    saved = service.save_connection(connection())
    assert service._connection(saved.id).password == 'database secret 0'
    reopened = DatabaseService(tmp_path)
    assert reopened._connection(saved.id).ssh_password == 'SSH secret 0'
    service.delete_connection(saved.id)
    assert not block_system_credentials


def test_blank_edits_preserve_persisted_secrets_and_replace_only_requested_secret(tmp_path):
    service = DatabaseService(tmp_path)
    original = connection()
    saved = service.save_connection(original)
    service = DatabaseService(tmp_path)
    service.save_connection(replace(saved, name='Renamed', ssh_password='new SSH secret'))
    recovered = DatabaseService(tmp_path)._connection(saved.id)
    assert recovered == replace(original, name='Renamed', ssh_password='new SSH secret')


def test_deleting_connection_removes_its_persisted_secrets_only(tmp_path):
    service = DatabaseService(tmp_path)
    first = service.save_connection(connection(1))
    second_original = connection(2)
    second = service.save_connection(second_original)
    service.delete_connection(first.id)
    reopened = DatabaseService(tmp_path)
    assert reopened._connection(second.id) == second_original
    with pytest.raises(ValueError, match='no longer exists'):
        reopened._connection(first.id)
    with sqlite3.connect(service.metadata_path) as database:
        keys = [row[0] for row in database.execute('SELECT key FROM credentials')]
    assert len(keys) == 3
    assert all(not key.startswith(first.id) for key in keys)


def test_empty_credentials_need_no_local_key_file(tmp_path):
    service = DatabaseService(tmp_path)
    saved = service.save_connection(Connection('Passwordless', kind='MySQL'))
    assert service._connection(saved.id).password == ''
    assert not (tmp_path / 'credentials.key').exists()


def test_concurrent_services_preserve_one_key_and_every_saved_secret(tmp_path):
    services = [DatabaseService(tmp_path) for _ in range(6)]
    values = [connection(index) for index in range(len(services))]
    with ThreadPoolExecutor(max_workers=len(services)) as workers:
        saved = list(workers.map(lambda pair: pair[0].save_connection(pair[1]), zip(services, values)))
    key = (tmp_path / 'credentials.key').read_bytes()
    recovered = DatabaseService(tmp_path)
    for result, original in zip(saved, values):
        assert recovered._connection(result.id) == original
    assert (tmp_path / 'credentials.key').read_bytes() == key


@pytest.mark.parametrize('damage', ['missing', 'corrupt', 'different'])
def test_damaged_key_fails_without_replacing_key_or_committing_metadata(tmp_path, damage):
    service = DatabaseService(tmp_path)
    original = connection()
    saved = service.save_connection(original)
    key_path = tmp_path / 'credentials.key'
    if damage == 'missing':
        key_path.unlink()
    else:
        key_path.write_bytes(b'broken local key' if damage == 'corrupt' else Fernet.generate_key())
    damaged = key_path.read_bytes() if key_path.exists() else None
    with pytest.raises(RuntimeError, match='credential|Credential'):
        DatabaseService(tmp_path)._connection(saved.id)
    with pytest.raises(RuntimeError, match='credential|Credential'):
        service.save_connection(replace(saved, name='Must roll back', password='replacement secret'))
    assert next(item for item in service.list_connections() if item.id == saved.id).name == original.name
    assert (key_path.read_bytes() if key_path.exists() else None) == damaged


def test_corrupt_ciphertext_is_not_silently_treated_as_an_empty_password(tmp_path):
    service = DatabaseService(tmp_path)
    saved = service.save_connection(connection())
    with sqlite3.connect(service.metadata_path) as database:
        database.execute('UPDATE credentials SET value=? WHERE key=?', (b'broken ciphertext', saved.id))
    with pytest.raises(RuntimeError, match='credential|Credential'):
        DatabaseService(tmp_path)._connection(saved.id)


def test_key_creation_failure_rolls_back_new_connection(tmp_path, monkeypatch):
    service = DatabaseService(tmp_path)
    original = connection()
    original_open = os.open

    def fail_key(path, *args, **kwargs):
        if str(path).endswith('credentials.key'):
            raise PermissionError('Key file creation denied')
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr('baseskiff.credentials.os.open', fail_key)
    with pytest.raises(RuntimeError, match='credential|Credential'):
        service.save_connection(original)
    assert all(item.id != original.id for item in service.list_connections())
    assert not (tmp_path / 'credentials.key').exists()
