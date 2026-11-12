"""Build BaseSkiff on the current operating system with PyInstaller."""

from __future__ import annotations

import argparse
import importlib.util
import os
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


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
    dist_root.mkdir(parents=True, exist_ok=True)
    dist_dir = Path(tempfile.mkdtemp(prefix=datetime.now().strftime("build-%Y%m%d-%H%M%S-"), dir=dist_root))
    work_dir = args.work_dir.resolve() / dist_dir.name
    command = [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
        "--distpath", str(dist_dir), "--workpath", str(work_dir),
        str(PROJECT_ROOT / "BaseSkiff.spec"),
    ]
    subprocess.run(command, cwd=PROJECT_ROOT, check=True)

    if sys.platform == "darwin":
        artifact = dist_dir / "BaseSkiff.app"
        executable = artifact / "Contents" / "MacOS" / "BaseSkiff"
    else:
        artifact = dist_dir / "BaseSkiff"
        executable = artifact / ("BaseSkiff.exe" if sys.platform == "win32" else "BaseSkiff")
    print(f"Built: {artifact}", flush=True)

    if args.smoke_test:
        environment = os.environ.copy()
        if sys.platform.startswith("linux") and not environment.get("DISPLAY"):
            environment.setdefault("QT_QPA_PLATFORM", "offscreen")
        subprocess.run(
            [str(executable), "--smoke-test"], cwd=PROJECT_ROOT,
            env=environment, check=True, timeout=60,
        )
        print("Bundled application smoke test passed.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
