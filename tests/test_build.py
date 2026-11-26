"""Building a new release must leave previously launched bundles readable."""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import build


@pytest.fixture(autouse=True)
def isolate_ci_summary(monkeypatch):
    # CI also runs pytest with its real summary path. Tests must never append
    # synthetic failures to the workflow's summary.
    monkeypatch.delenv('GITHUB_STEP_SUMMARY', raising=False)


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


@pytest.mark.parametrize('outcome', ['success', 'failure', 'timeout'])
def test_smoke_log_is_replayed_and_failure_is_preserved(tmp_path, monkeypatch, capsys, outcome):
    diagnostic = 'Frozen startup diagnostic: 驱动加载检查\n'
    failure = (subprocess.CalledProcessError(1, ['BaseSkiff.exe', '--smoke-test'])
               if outcome == 'failure' else
               subprocess.TimeoutExpired(['BaseSkiff.exe', '--smoke-test'], 60)
               if outcome == 'timeout' else None)

    def run(command, **kwargs):
        if '--distpath' in command:
            return
        log_path = kwargs['env'].get('BASESKIFF_SMOKE_LOG')
        if log_path:
            Path(log_path).write_text(diagnostic, encoding='utf-8')
        if failure:
            raise failure

    monkeypatch.setattr(build.subprocess, 'run', run)
    monkeypatch.setattr(build.importlib.util, 'find_spec', lambda name: object())
    monkeypatch.setattr(sys, 'argv', ['build.py', '--dist-dir', str(tmp_path / 'dist'),
                                    '--work-dir', str(tmp_path / 'work'), '--smoke-test'])
    if failure:
        with pytest.raises(type(failure)) as caught:
            build.main()
        assert caught.value is failure
    else:
        assert build.main() == 0

    output = capsys.readouterr().out
    assert diagnostic in output
    assert ('Bundled application smoke test passed.' in output) is (failure is None)


def test_ci_build_failure_replays_complete_output_and_summarizes_safe_tail(tmp_path, monkeypatch, capsys):
    summary_path = tmp_path / 'summary.md'
    diagnostic = ('outside retained tail\n' + '\n'.join(f'build line {index}' for index in range(105))
                  + '\n<script>markup</script>\n```\n')
    real_run = subprocess.run

    def run(command, **kwargs):
        # Replace only the expensive builder, retaining real pipe capture and
        # CalledProcessError output handling at the subprocess boundary.
        return real_run([sys.executable, '-c',
                         'import sys; sys.stdout.write(sys.argv[1]); sys.exit(7)', diagnostic], **kwargs)

    monkeypatch.setenv('GITHUB_STEP_SUMMARY', str(summary_path))
    monkeypatch.setattr(build.subprocess, 'run', run)
    monkeypatch.setattr(build.importlib.util, 'find_spec', lambda name: object())
    monkeypatch.setattr(sys, 'argv', ['build.py', '--dist-dir', str(tmp_path / 'dist'),
                                    '--work-dir', str(tmp_path / 'work')])
    with pytest.raises(subprocess.CalledProcessError) as caught:
        build.main()
    assert caught.value.returncode == 7
    assert diagnostic in capsys.readouterr().out
    assert summary_path.exists(), 'A failed CI build must publish a readable summary'
    summary = summary_path.read_text(encoding='utf-8')
    assert 'PyInstaller build failed' in summary
    assert 'build line 104' in summary
    assert 'outside retained tail' not in summary
    assert '<script>' not in summary
    assert '&lt;script&gt;markup&lt;/script&gt;' in summary


@pytest.mark.parametrize('outcome', ['success', 'failure', 'timeout'])
def test_ci_smoke_summary_preserves_outcome_and_includes_frozen_log(tmp_path, monkeypatch, capsys, outcome):
    summary_path = tmp_path / 'summary.md'
    diagnostic = 'Frozen smoke details: 数据库驱动\n'
    failure = (subprocess.CalledProcessError(1, ['BaseSkiff.exe']) if outcome == 'failure' else
               subprocess.TimeoutExpired(['BaseSkiff.exe'], 60) if outcome == 'timeout' else None)

    def run(command, **kwargs):
        if '--distpath' in command:
            return subprocess.CompletedProcess(command, 0, stdout='PyInstaller output\n')
        log_path = kwargs['env'].get('BASESKIFF_SMOKE_LOG')
        if log_path:
            Path(log_path).write_text(diagnostic, encoding='utf-8')
        if failure:
            raise failure

    monkeypatch.setenv('GITHUB_STEP_SUMMARY', str(summary_path))
    monkeypatch.setattr(build.subprocess, 'run', run)
    monkeypatch.setattr(build.importlib.util, 'find_spec', lambda name: object())
    monkeypatch.setattr(sys, 'argv', ['build.py', '--dist-dir', str(tmp_path / 'dist'),
                                    '--work-dir', str(tmp_path / 'work'), '--smoke-test'])
    if failure:
        with pytest.raises(type(failure)) as caught:
            build.main()
        assert caught.value is failure
        assert summary_path.exists(), 'Frozen smoke failures must appear in the CI summary'
        summary = summary_path.read_text(encoding='utf-8')
        assert diagnostic.rstrip('\n') in summary
        assert ('timed out' if outcome == 'timeout' else 'failed') in summary
    else:
        assert build.main() == 0
        assert not summary_path.exists()
    output = capsys.readouterr().out
    assert 'PyInstaller output\n' in output
    assert diagnostic in output


def test_ci_failure_summary_bounds_a_single_long_line(tmp_path, monkeypatch):
    summary_path = tmp_path / 'summary.md'
    failure = subprocess.CalledProcessError(1, ['PyInstaller'], output='discarded-prefix' + 'x' * 20000 + 'retained-tail')

    def run(command, **kwargs):
        raise failure

    monkeypatch.setenv('GITHUB_STEP_SUMMARY', str(summary_path))
    monkeypatch.setattr(build.subprocess, 'run', run)
    monkeypatch.setattr(build.importlib.util, 'find_spec', lambda name: object())
    monkeypatch.setattr(sys, 'argv', ['build.py', '--dist-dir', str(tmp_path / 'dist'),
                                    '--work-dir', str(tmp_path / 'work')])
    with pytest.raises(subprocess.CalledProcessError):
        build.main()
    assert summary_path.exists(), 'A long diagnostic must still produce a bounded CI summary'
    summary = summary_path.read_text(encoding='utf-8')
    assert 'discarded-prefix' not in summary
    assert 'retained-tail' in summary
    assert len(summary.encode('utf-8')) < 17000


def test_build_directory_timestamp_format_and_same_second_collision(tmp_path, monkeypatch):
    from datetime import datetime

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls):
            return cls(2026, 11, 25, 12, 20, 15)

    monkeypatch.setattr(build, 'datetime', FrozenDatetime)
    first = build.create_build_directory(tmp_path)
    marker = first / 'running-marker'
    marker.write_text('preserve')
    second = build.create_build_directory(tmp_path)
    assert first.name == 'build-20261125122015'
    assert second.name == 'build-20261125122016'
    assert marker.read_text() == 'preserve'


def test_timestamp_collision_rolls_over_date(tmp_path, monkeypatch):
    from datetime import datetime

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls):
            return cls(2026, 12, 31, 23, 59, 59)

    monkeypatch.setattr(build, 'datetime', FrozenDatetime)
    build.create_build_directory(tmp_path)
    assert build.create_build_directory(tmp_path).name == 'build-20270101000000'


def test_shared_work_root_reserves_distinct_cache_paths(tmp_path, monkeypatch):
    from datetime import datetime

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls):
            return cls(2026, 11, 26, 12, 0, 0)

    monkeypatch.setattr(build, 'datetime', FrozenDatetime)
    work = tmp_path / 'work'
    first_dist, first_work = build.reserve_build_directories(tmp_path / 'dist1', work)
    second_dist, second_work = build.reserve_build_directories(tmp_path / 'dist2', work)
    assert first_work != second_work
    assert first_dist.name == first_work.name == 'build-20261126120000'
    assert second_dist.name == second_work.name == 'build-20261126120001'
    assert not (tmp_path / 'dist2' / first_dist.name).exists()
