"""Build the frozen desktop app, including the native DWG converter."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
binary_name = "openarch-dwg-helper.exe" if os.name == "nt" else "openarch-dwg-helper"
helper = ROOT / "dwg-helper" / "target" / "release" / binary_name
if not helper.is_file():
    raise SystemExit(f"DWG converter not built: {helper}")

separator = os.pathsep
arguments = [
    sys.executable,
    "-m",
    "PyInstaller",
    "--noconfirm",
    "--clean",
    "--windowed",
    "--name",
    "OpenArchSweptPaths",
    "--paths",
    str(ROOT / "src"),
    "--collect-all",
    "ezdxf",
    "--collect-all",
    "shapely",
    "--add-binary",
    f"{helper}{separator}openarchsp/bin",
    "--add-data",
    f"{ROOT / 'THIRD_PARTY_NOTICES.md'}{separator}.",
    "--add-data",
    f"{ROOT / 'LICENSES' / 'MPL-2.0.txt'}{separator}LICENSES",
    str(ROOT / "src" / "openarchsp" / "app.py"),
]
subprocess.run(arguments, cwd=ROOT, check=True)
