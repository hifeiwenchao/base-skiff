"""Build BaseSkiff on the current operating system with PyInstaller."""

from __future__ import annotations

import argparse
import html
import importlib.util
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def create_build_directory(root: Path) -> Path:
    """Reserve a timestamp path atomically without replacing earlier builds."""
    root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().replace(microsecond=0)
    while True:
        candidate = root / timestamp.strftime("build-%Y%m%d%H%M%S")
        try:
            candidate.mkdir()
        except FileExistsError:
            timestamp += timedelta(seconds=1)
        else:
            return candidate


def reserve_build_directories(dist_root: Path, work_root: Path) -> tuple[Path, Path]:
    """Reserve both paths, including builds sharing only their cache parent."""
    dist_root.mkdir(parents=True, exist_ok=True)
    work_root.mkdir(parents=True, exist_ok=True)
    if dist_root == work_root:
        raise ValueError('Distribution and work parents must be different directories.')
    timestamp = datetime.now().replace(microsecond=0)
    while True:
        name = timestamp.strftime('build-%Y%m%d%H%M%S')
        output, cache = dist_root / name, work_root / name
        try:
            output.mkdir()
        except FileExistsError:
            timestamp += timedelta(seconds=1)
            continue
        try:
            cache.mkdir()
        except FileExistsError:
            output.rmdir()
            timestamp += timedelta(seconds=1)
        except OSError:
            output.rmdir()
            raise
        else:
            return output, cache


def _failure_summary(path: str | None, title: str, details: str) -> None:
    if not path:
        return
    tail = '\n'.join(details.splitlines()[-100:])
    tail = tail.encode('utf-8')[-16384:].decode('utf-8', errors='ignore')
    try:
        with Path(path).open('a', encoding='utf-8') as summary:
            summary.write(f'### {title}\n\n<pre>{html.escape(tail)}</pre>\n\n')
    except OSError as error:
        # Diagnostic publishing must not hide the original build/smoke failure.
        print(f'Could not write CI failure summary: {error}', flush=True)


def _echo(output: str) -> None:
    if output:
        print(output, end='' if output.endswith('\n') else '\n', flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dist-dir", type=Path, default=PROJECT_ROOT / "dist",
        help="Parent output directory; each build creates a separate build-... subdirectory.",
    )
    parser.add_argument("--work-dir", type=Path, default=PROJECT_ROOT / "build")
    parser.add_argument(
        "--smoke-test", action="store_true",
        help="Run the bundled application with --smoke-test after building.",
    )
    args = parser.parse_args()

    if importlib.util.find_spec("PyInstaller") is None:
        parser.error("PyInstaller is missing. Install requirements.txt first.")

    # Frozen imports reopen the executable by pathname, using offsets cached at
    # startup. Replacing or moving a running bundle can therefore corrupt later
    # driver imports. Keep every build at its own permanent, exclusive path.
    dist_root = args.dist_dir.resolve()
    dist_dir, work_dir = reserve_build_directories(dist_root, args.work_dir.resolve())
    command = [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
        "--distpath", str(dist_dir), "--workpath", str(work_dir),
        str(PROJECT_ROOT / "BaseSkiff.spec"),
    ]
    summary_path = os.environ.get('GITHUB_STEP_SUMMARY')
    capture = ({'stdout': subprocess.PIPE, 'stderr': subprocess.STDOUT,
                'text': True, 'encoding': 'utf-8', 'errors': 'replace'}
               if summary_path else {})
    try:
        completed = subprocess.run(command, cwd=PROJECT_ROOT, check=True, **capture)
    except (subprocess.CalledProcessError, OSError) as error:
        output = getattr(error, 'stdout', None) or ''
        if summary_path:
            _echo(output)
            _failure_summary(summary_path, 'PyInstaller build failed', f'{error}\n\n{output}')
        raise
    else:
        if summary_path:
            _echo(getattr(completed, 'stdout', None) or '')

    if sys.platform == "darwin":
        artifact = dist_dir / "BaseSkiff.app"
        executable = artifact / "Contents" / "MacOS" / "BaseSkiff"
    else:
        artifact = dist_dir / "BaseSkiff"
        executable = artifact / ("BaseSkiff.exe" if sys.platform == "win32" else "BaseSkiff")
    print(f"Built: {artifact}", flush=True)

    if args.smoke_test:
        environment = os.environ.copy()
        smoke_log = dist_dir / 'smoke-test.log'
        environment['BASESKIFF_SMOKE_LOG'] = str(smoke_log)
        if sys.platform.startswith("linux") and not environment.get("DISPLAY"):
            environment.setdefault("QT_QPA_PLATFORM", "offscreen")
        failure = None
        try:
            subprocess.run(
                [str(executable), "--smoke-test"], cwd=PROJECT_ROOT,
                env=environment, check=True, timeout=60,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as error:
            failure = error
            raise
        finally:
            try:
                output = smoke_log.read_text(encoding='utf-8', errors='replace')
            except OSError as error:
                output = f'Bundled application smoke log unavailable: {error}\n'
            print(f'Bundled application smoke log: {smoke_log}', flush=True)
            _echo(output)
            if failure:
                title = ('Bundled application smoke test timed out'
                         if isinstance(failure, subprocess.TimeoutExpired)
                         else 'Bundled application smoke test failed')
                _failure_summary(summary_path, title, f'{failure}\n\n{output}')
        print("Bundled application smoke test passed.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
