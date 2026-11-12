# -*- mode: python ; coding: utf-8 -*-
"""Native, one-folder builds. Run on each target OS; PyInstaller is not a cross-compiler."""

from pathlib import Path
import pkgutil

import PySide6
from PyInstaller.compat import is_darwin, is_win
from PyInstaller.utils.hooks import (
    collect_all, collect_data_files, collect_dynamic_libs, copy_metadata,
    get_package_paths,
)


project_root = Path(SPECPATH)
datas = [(str(project_root / "assets"), "assets")]
binaries = []
hiddenimports = ["sqlite3", "ssl", "decimal", "uuid", "gridfs"]

# These drivers import implementation modules dynamically. In particular,
# psycopg_binary includes libpq and its supporting native libraries.
for package in ("pymysql", "psycopg", "pymongo", "bson", "redis", "msgpack", "oracledb", "paramiko", "bcrypt", "nacl"):
    package_datas, package_binaries, package_imports = collect_all(
        package,
        include_py_files=False,
        filter_submodules=lambda name: ".tests" not in name and ".test" not in name,
    )
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_imports

# psycopg_binary rejects direct import until psycopg has initialized. PyInstaller's
# isolated collect_submodules therefore cannot discover it with collect_all.
# Enumerate its extension modules without importing the package instead.
psycopg_binary_dir = get_package_paths("psycopg_binary")[1]
hiddenimports += ["psycopg_binary"] + [
    f"psycopg_binary.{module.name}"
    for module in pkgutil.iter_modules([psycopg_binary_dir])
]
datas += collect_data_files("psycopg_binary", include_py_files=False)
binaries += collect_dynamic_libs("psycopg_binary")
datas += copy_metadata("psycopg-binary")

# Only the native widget stack and SVG renderer belong in this application.
# Derive exclusions from the installed Qt version to cover new optional modules.
qt_modules = {"QtCore", "QtGui", "QtWidgets", "QtSvg", "QtSvgWidgets"}
excludes = [
    f"PySide6.{module.name}"
    for module in pkgutil.iter_modules(PySide6.__path__)
    if module.name.startswith("Qt") and module.name not in qt_modules
]
excludes += ["tkinter", "matplotlib", "IPython", "pytest", "PyQt5", "PyQt6", "keyring"]

icon = project_root / "assets" / ("app-icon.icns" if is_darwin else "app-icon.ico")

a = Analysis(
    [str(project_root / "scripts" / "launcher.py")],
    pathex=[str(project_root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=1,
)
# QtGui's optional PDF image plugin otherwise drags QtPdf into a widgets app.
# Excluding Python Qt modules alone does not remove that native plugin.
unused_qt = ("qpdf", "qtpdf", "qt6pdf", "qtwebengine", "qt6webengine")
a.binaries = [
    entry for entry in a.binaries
    if not any(name in entry[0].lower() for name in unused_qt)
]
a.datas = [
    entry for entry in a.datas
    if not any(name in entry[0].lower() for name in unused_qt)
]
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="BaseSkiff",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(icon) if is_darwin or is_win else None,
)
collection = COLLECT(
    exe, a.binaries, a.datas,
    strip=False,
    upx=False,
    name="BaseSkiff",
)

if is_darwin:
    app = BUNDLE(
        collection,
        name="BaseSkiff.app",
        icon=str(icon),
        bundle_identifier="app.baseskiff.desktop",
        info_plist={
            "CFBundleName": "BaseSkiff",
            "CFBundleDisplayName": "BaseSkiff",
            "CFBundleShortVersionString": "0.1.0",
            "CFBundleVersion": "1",
            "NSHighResolutionCapable": True,
        },
    )
