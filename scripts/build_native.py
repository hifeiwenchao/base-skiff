"""Build an isolated macOS SwiftUI app with a standalone Qt-free worker.

This is an explicit migration preview build; scripts/build.py remains unchanged.
"""
from __future__ import annotations

import argparse
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# Support both `python scripts/build_native.py` and module/import use.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from scripts.build import reserve_build_directories  # noqa: E402


def clean_environment():
    """Smoke the artifact without access to the build interpreter or Conda libs."""
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith(('CONDA', 'PYTHON', 'DYLD_', '_PYI'))
                   and key not in {'VIRTUAL_ENV', 'BASESKIFF_PYTHON',
                                   'BASESKIFF_WORKER', 'BASESKIFF_PROJECT_ROOT', 'BASESKIFF_SOURCE'}}
    environment['PATH'] = '/usr/bin:/bin:/usr/sbin:/sbin'
    return environment


def freeze_worker(output, cache):
    subprocess.run([
        sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean',
        '--distpath', str(output), '--workpath', str(cache),
        str(PROJECT_ROOT / 'BaseSkiffWorker.spec'),
    ], cwd=PROJECT_ROOT, check=True)
    worker = output / 'BaseSkiffWorker'
    if not worker.is_file():
        raise RuntimeError('PyInstaller did not produce the standalone worker.')
    return worker


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dist-dir', type=Path, default=PROJECT_ROOT / 'dist')
    parser.add_argument('--work-dir', type=Path, default=PROJECT_ROOT / 'build')
    parser.add_argument('--smoke-test', action='store_true')
    parser.add_argument('--worker-only', action='store_true',
                        help='Freeze the worker independently; do not build the Swift app.')
    args = parser.parse_args(argv)
    if sys.platform != 'darwin':
        parser.error('Native macOS builds must run on macOS.')
    output, cache = reserve_build_directories(args.dist_dir.resolve(), args.work_dir.resolve())
    worker = freeze_worker(output, cache / 'worker')
    if args.worker_only:
        print(f'Built standalone worker: {worker}', flush=True)
        if args.smoke_test:
            import json
            with tempfile.TemporaryDirectory(prefix='baseskiff-native-worker-smoke-') as data:
                completed = subprocess.run([str(worker), '--data-dir', data],
                    input='{"version":1,"id":"hello","method":"hello","params":{}}\n',
                    text=True, capture_output=True, env=clean_environment(),
                    cwd=data, check=True, timeout=60)
                response = json.loads(completed.stdout)
                if response.get('result', {}).get('qt_loaded') is not False:
                    raise RuntimeError('Frozen worker handshake failed or imported Qt.')
            print('Standalone worker smoke passed without external Python.', flush=True)
        return 0
    swift_cache = cache / 'swift'
    subprocess.run(['swift', 'build', '--package-path', str(PROJECT_ROOT / 'macos'),
                    '--scratch-path', str(swift_cache), '-c', 'release'], check=True)
    completed = subprocess.run(['swift', 'build', '--package-path', str(PROJECT_ROOT / 'macos'),
                    '--scratch-path', str(swift_cache), '-c', 'release', '--show-bin-path'],
                    check=True, capture_output=True, text=True)
    swift_binary = Path(completed.stdout.strip()) / 'BaseSkiffNative'
    artifact = output / 'BaseSkiff.app'
    contents = artifact / 'Contents'
    executables = contents / 'MacOS'
    resources = contents / 'Resources'
    executables.mkdir(parents=True)
    resources.mkdir()
    shutil.copy2(swift_binary, executables / 'BaseSkiffNative')
    # Move the newly built, never launched worker only; existing artifacts stay untouched.
    shutil.move(str(worker), str(executables / 'BaseSkiffWorker'))
    icon = PROJECT_ROOT / 'assets' / 'app-icon.icns'
    if icon.exists():
        shutil.copy2(icon, resources / icon.name)
    info = {
        'CFBundleName': 'BaseSkiff', 'CFBundleDisplayName': 'BaseSkiff Native Preview',
        'CFBundleIdentifier': 'app.baseskiff.native-preview',
        'CFBundleExecutable': 'BaseSkiffNative', 'CFBundlePackageType': 'APPL',
        'CFBundleShortVersionString': '0.1.0', 'CFBundleVersion': '1',
        'CFBundleIconFile': 'app-icon.icns', 'LSMinimumSystemVersion': '14.0',
        'NSHighResolutionCapable': True,
    }
    with (contents / 'Info.plist').open('wb') as stream:
        plistlib.dump(info, stream)
    # Ad-hoc local signing only; this does not provide Developer ID or notarization.
    subprocess.run(['/usr/bin/codesign', '--force', '--sign', '-', str(artifact)], check=True)
    print(f'Built native migration preview: {artifact}', flush=True)
    if args.smoke_test:
        with tempfile.TemporaryDirectory(prefix='baseskiff-native-smoke-') as data:
            environment = clean_environment()
            environment['BASESKIFF_DATA_DIR'] = data
            subprocess.run([str(executables / 'BaseSkiffNative'), '--smoke-test'],
                           cwd=data, env=environment, check=True, timeout=120)
        print('Native bundled smoke passed without external Python.', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
