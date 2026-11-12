"""Building a new release must leave previously launched bundles readable."""
import shutil
import sys
from pathlib import Path

import pytest

from scripts import build


@pytest.mark.parametrize('platform', ['darwin', 'win32', 'linux'])
def test_repeated_builds_preserve_previous_and_legacy_bundles(tmp_path, monkeypatch, capsys, platform):
    distribution = tmp_path / 'distribution with spaces'
    legacy = distribution / ('BaseSkiff.app' if platform == 'darwin' else 'BaseSkiff')
    legacy.mkdir(parents=True)
    (legacy / 'running-marker').write_text('legacy process still needs this')
    built = []
    checked = []

    # PyInstaller is the slow external boundary. Model its destructive replacement
    # of an existing bundle so this catches the original --noconfirm overwrite.
    def run(command, **kwargs):
        if '--distpath' in command:
            output = Path(command[command.index('--distpath') + 1])
            artifact = output / legacy.name
            if artifact.exists():
                shutil.rmtree(artifact)
            executable = (artifact / 'Contents' / 'MacOS' / 'BaseSkiff' if platform == 'darwin'
                          else artifact / ('BaseSkiff.exe' if platform == 'win32' else 'BaseSkiff'))
            executable.parent.mkdir(parents=True, exist_ok=True)
            executable.write_text(f'build {len(built) + 1}')
            built.append((artifact, executable))
        else:
            executable = Path(command[0])
            checked.append((executable, executable.read_text()))

    monkeypatch.setattr(build.subprocess, 'run', run)
    monkeypatch.setattr(build.importlib.util, 'find_spec', lambda name: object())
    monkeypatch.setattr(sys, 'platform', platform)
    monkeypatch.setattr(sys, 'argv', ['build.py', '--dist-dir', str(distribution),
                                    '--work-dir', str(tmp_path / 'work'), '--smoke-test'])
    assert build.main() == 0
    assert build.main() == 0

    assert (legacy / 'running-marker').read_text() == 'legacy process still needs this'
    assert built[0][0] != built[1][0]
    assert built[0][1].read_text() == 'build 1'
    assert built[1][1].read_text() == 'build 2'
    assert checked == [(built[0][1], 'build 1'), (built[1][1], 'build 2')]
    output = capsys.readouterr().out
    for artifact, _ in built:
        assert artifact.parent.parent == distribution
        assert str(artifact) in output
