"""Frozen smoke failures must remain observable without Windows console streams."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

LAUNCHER = Path(__file__).resolve().parents[1] / 'scripts' / 'launcher.py'


def run_launcher(tmp_path, source, *, smoke=True):
    package = tmp_path / 'baseskiff'
    package.mkdir()
    (package / '__init__.py').write_text('', encoding='utf-8')
    (package / 'app.py').write_text(source, encoding='utf-8')
    log_path = tmp_path / 'frozen smoke.log'
    environment = os.environ.copy()
    environment['PYTHONPATH'] = str(tmp_path)
    environment['BASESKIFF_SMOKE_LOG'] = str(log_path)
    # Windowed Windows executables have no Python console streams. Execute the
    # real launcher with that environment while replacing only the Qt app entry.
    bootstrap = (
        'import runpy, sys; '
        + ('sys.stdout = None; sys.stderr = None; ' if smoke else '')
        + 'sys.argv = [sys.argv[1]] + sys.argv[2:]; '
        + 'runpy.run_path(sys.argv[0], run_name="__main__")'
    )
    command = [sys.executable, '-c', bootstrap, str(LAUNCHER)]
    if smoke:
        command.append('--smoke-test')
    result = subprocess.run(command, cwd=tmp_path, env=environment,
                            capture_output=True, text=True, encoding='utf-8', timeout=10)
    return result, log_path


def test_windowed_smoke_records_stdout_and_stderr_in_utf8(tmp_path):
    result, log_path = run_launcher(tmp_path, '''
import sys
def main():
    print('启动检查完成', flush=True)
    print('diagnostic stderr', file=sys.stderr, flush=True)
    return 0
''')
    assert result.returncode == 0
    assert log_path.exists(), 'The windowed launcher must create the smoke log'
    log = log_path.read_text(encoding='utf-8')
    assert '启动检查完成' in log
    assert 'diagnostic stderr' in log


@pytest.mark.parametrize('stage', ['import', 'main'])
def test_windowed_smoke_logs_startup_traceback_and_keeps_failure_exit(tmp_path, stage):
    source = ("raise RuntimeError('import startup failed')\n" if stage == 'import' else
              "def main():\n    raise RuntimeError('main startup failed')\n")
    result, log_path = run_launcher(tmp_path, source)
    assert result.returncode != 0
    assert log_path.exists(), 'Startup failures must be written before the app can initialize'
    log = log_path.read_text(encoding='utf-8')
    assert 'Traceback (most recent call last)' in log
    assert f'RuntimeError: {stage} startup failed' in log


def test_desktop_launch_ignores_smoke_log_configuration(tmp_path):
    result, log_path = run_launcher(tmp_path, "def main():\n    print('desktop startup')\n    return 0\n", smoke=False)
    assert result.returncode == 0
    assert 'desktop startup' in result.stdout
    assert not log_path.exists()
