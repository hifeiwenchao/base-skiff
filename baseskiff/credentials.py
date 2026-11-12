"""Encrypt local database credentials with an application-local key file."""
from __future__ import annotations

import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


class LocalCredentialError(RuntimeError):
    """A local key or saved credential cannot be safely read or updated."""


class LocalCredentials:
    def __init__(self, key_path: Path):
        self.key_path = key_path

    def _key_bytes(self, create: bool) -> bytes:
        try:
            return self.key_path.read_bytes()
        except FileNotFoundError:
            if not create:
                raise LocalCredentialError(
                    'The local credential key is missing. Restore credentials.key to use saved passwords.') from None
            try:
                descriptor = os.open(self.key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                # Writers are serialized by the metadata database transaction.
                return self.key_path.read_bytes()
            else:
                with os.fdopen(descriptor, 'wb') as key_file:
                    data = Fernet.generate_key()
                    key_file.write(data)
                    key_file.flush()
                    os.fsync(key_file.fileno())
                return data

    def _cipher(self, create: bool = False) -> Fernet:
        try:
            data = self._key_bytes(create)
            self.key_path.chmod(0o600)
            return Fernet(data)
        except (OSError, ValueError, TypeError) as error:
            raise LocalCredentialError('The local credential key is invalid or inaccessible.') from error

    @staticmethod
    def _decrypt(cipher: Fernet, value: bytes) -> str:
        try:
            return cipher.decrypt(value).decode('utf-8')
        except (InvalidToken, ValueError, TypeError, UnicodeDecodeError) as error:
            raise LocalCredentialError(
                'A saved credential cannot be decrypted. Restore its local key and credential data.') from error

    def decrypt(self, value: bytes) -> str:
        return self._decrypt(self._cipher(), value)

    def encrypt(self, value: str, reference_token: bytes | None = None) -> bytes:
        cipher = self._cipher(create=reference_token is None)
        if reference_token is not None:
            # A syntactically valid replacement key must not silently overwrite
            # credentials belonging to the original key.
            self._decrypt(cipher, reference_token)
        return cipher.encrypt(value.encode('utf-8'))
