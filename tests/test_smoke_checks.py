"""Frozen builds optimize Python; their smoke checks must still run and fail."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

_OPTIMIZED_SMOKE = r'''
import builtins
import sys
from pathlib import Path

# Running this regression must never reach the system credential backend.
original_import = builtins.__import__
def guard(name, *args, **kwargs):
    if name == 'keyring' or name.startswith('keyring.'):
        raise RuntimeError('System credential storage must not be loaded')
    return original_import(name, *args, **kwargs)
builtins.__import__ = guard

from PySide6.QtCore import QSettings
QSettings.setDefaultFormat(QSettings.Format.IniFormat)
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, sys.argv[3])
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.SystemScope, sys.argv[3])

failure, screenshot = sys.argv[1:3]
if failure in ('table', 'rows'):
    from baseskiff.ui.window import MainWindow
    original_update = MainWindow.update_pager
    def update_pager(self):
        original_update(self)
        if self.data_table.rowCount():
            if failure == 'table':
                self.current_table = 'wrong_table'
            else:
                self.total_rows = 127
    MainWindow.update_pager = update_pager
elif failure == 'sqlite':
    from baseskiff.service import DatabaseService
    original_execute = DatabaseService.execute
    def execute(self, connection_id, query):
        result = original_execute(self, connection_id, query)
        if query == 'SELECT COUNT(*) AS total FROM customers':
            result.rows[0]['total'] = 127
        return result
    DatabaseService.execute = execute
elif failure == 'libpq':
    import psycopg
    psycopg.pq.version = lambda: 0
elif failure == 'ssh':
    import baseskiff.ssh
    baseskiff.ssh.fingerprint = lambda key: 'invalid-fingerprint'
elif failure == 'msgpack':
    from dataclasses import replace
    import baseskiff.redis_codec
    original_preview = baseskiff.redis_codec.preview_value
    baseskiff.redis_codec.preview_value = lambda value: replace(original_preview(value), format='hex')
elif failure == 'raw':
    import baseskiff.redis_codec
    # Still valid binary MessagePack so the preview check does not mask this one.
    baseskiff.redis_codec.raw_bytes = lambda value: b'\x81\xa7changed\xc4\x02\xff\x00'
elif failure == 'screenshot':
    # A directory cannot be written as a PNG. Keep the actual Qt save boundary.
    Path(screenshot).mkdir()

from baseskiff.app import main
sys.argv = ['BaseSkiff', '--smoke-test', '--screenshot', screenshot]
raise SystemExit(main())
'''


def _run_optimized_smoke(tmp_path, failure=''):
    screenshot = tmp_path / 'workspace.png'
    result = subprocess.run(
        [sys.executable, '-O', '-c', _OPTIMIZED_SMOKE, failure, str(screenshot),
         str(tmp_path / 'preferences')],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen'},
        capture_output=True, text=True, timeout=45,
    )
    return result, screenshot


def test_optimized_smoke_checks_real_startup_drivers_credentials_and_screenshot(tmp_path):
    result, screenshot = _run_optimized_smoke(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert 'native workspace and SQLite checks passed' in result.stdout
    assert 'packaged drivers, Redis codecs and local credentials verified' in result.stdout
    assert screenshot.is_file(), 'Optimized smoke must actually save the screenshot'
    assert screenshot.read_bytes().startswith(b'\x89PNG\r\n\x1a\n')
    assert 'System credential storage must not be loaded' not in result.stderr


@pytest.mark.parametrize(('failure', 'diagnostic'), [
    ('table', 'customers'),
    ('rows', 'row count'),
    ('sqlite', 'SQLite'),
    ('libpq', 'libpq'),
    ('ssh', 'SSH'),
    ('msgpack', 'MessagePack'),
    ('raw', 'raw bytes'),
    ('screenshot', 'screenshot'),
])
def test_optimized_smoke_fails_with_a_diagnostic_for_failed_checks(tmp_path, failure, diagnostic):
    result, _ = _run_optimized_smoke(tmp_path, failure)

    assert result.returncode != 0, result.stdout + result.stderr
    assert 'BaseSkiff smoke test failed:' in result.stderr
    assert diagnostic in result.stderr
    assert 'checks passed' not in result.stdout
