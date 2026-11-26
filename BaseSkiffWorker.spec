# -*- mode: python ; coding: utf-8 -*-
"""Standalone, Qt-free service executable for the native macOS application."""
from pathlib import Path
import pkgutil
from PyInstaller.utils.hooks import (
    collect_all, collect_data_files, collect_dynamic_libs, copy_metadata,
    get_package_paths,
)

project_root = Path(SPECPATH)
datas, binaries = [], []
hiddenimports = ['sqlite3', 'ssl', 'decimal', 'uuid', 'gridfs']
for package in ('pymysql', 'psycopg', 'pymongo', 'bson', 'redis', 'msgpack',
                'oracledb', 'paramiko', 'bcrypt', 'nacl', 'cryptography'):
    package_datas, package_binaries, package_imports = collect_all(
        package, include_py_files=False,
        filter_submodules=lambda name: '.tests' not in name and '.test' not in name,
    )
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_imports

psycopg_binary_dir = get_package_paths('psycopg_binary')[1]
hiddenimports += ['psycopg_binary'] + [
    f'psycopg_binary.{module.name}'
    for module in pkgutil.iter_modules([psycopg_binary_dir])
]
datas += collect_data_files('psycopg_binary', include_py_files=False)
binaries += collect_dynamic_libs('psycopg_binary')
datas += copy_metadata('psycopg-binary')

analysis = Analysis(
    [str(project_root / 'baseskiff' / 'native' / 'entry.py')],
    pathex=[str(project_root)], binaries=binaries, datas=datas,
    hiddenimports=hiddenimports, hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=['PySide6', 'shiboken6', 'PyQt5', 'PyQt6', 'qtpy', 'keyring',
              'tkinter', 'matplotlib', 'IPython', 'pytest'],
    noarchive=False, optimize=1,
)
# Fail closed if an optional dependency unexpectedly drags Qt into the worker.
for entry in [*analysis.binaries, *analysis.datas]:
    name = entry[0].lower()
    if any(marker in name for marker in ('pyside', 'pyqt', 'qtcore', 'qtgui', 'qtwidgets', 'qtwebengine')):
        raise RuntimeError(f'Qt dependency is forbidden in native service worker: {entry[0]}')
pyz = PYZ(analysis.pure)
exe = EXE(
    pyz, analysis.scripts, analysis.binaries, analysis.datas, [],
    name='BaseSkiffWorker', debug=False, bootloader_ignore_signals=False,
    strip=False, upx=False, console=True, disable_windowed_traceback=False,
    argv_emulation=False, target_arch=None, codesign_identity=None,
    entitlements_file=None,
)
