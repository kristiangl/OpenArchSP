"""Build the frozen desktop app, including the native DWG converter."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
binary_name = "openarch-dwg-helper.exe" if os.name == "nt" else "openarch-dwg-helper"
helper = ROOT / "dwg-helper" / "target" / "release" / binary_name
if not helper.is_file():
    raise SystemExit(f"DWG converter not built: {helper}")


def platform_icon() -> Path | None:
    """Render the square SVG mark into the native icon format when required."""
    if sys.platform not in {"win32", "darwin"}:
        return None

    from PIL import Image
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer

    icon_dir = ROOT / "packaging" / "icons"
    icon_dir.mkdir(parents=True, exist_ok=True)
    renderer = QSvgRenderer(str(ROOT / "src" / "openarchsp" / "assets" / "app-icon.svg"))
    if not renderer.isValid():
        raise SystemExit("The OpenArch app icon SVG could not be loaded.")

    source = QImage(1024, 1024, QImage.Format.Format_ARGB32_Premultiplied)
    source.fill(0)
    painter = QPainter(source)
    renderer.render(painter)
    painter.end()
    if sys.platform == "win32":
        png_path = icon_dir / "openarchsp-icon-source.png"
        if not source.save(str(png_path)):
            raise SystemExit("Could not render the Windows application icon.")
        Image.open(png_path).save(
            icon_dir / "openarchsp.ico",
            format="ICO",
            sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
        )
        png_path.unlink()
        return icon_dir / "openarchsp.ico"

    with tempfile.TemporaryDirectory(prefix="openarchsp-iconset-") as temp_dir:
        iconset = Path(temp_dir) / "OpenArch.iconset"
        iconset.mkdir()
        base = Image.open(_write_qimage(source, Path(temp_dir) / "base.png")).convert("RGBA")
        for pixels, filename in (
            (16, "icon_16x16.png"),
            (32, "icon_16x16@2x.png"),
            (32, "icon_32x32.png"),
            (64, "icon_32x32@2x.png"),
            (128, "icon_128x128.png"),
            (256, "icon_128x128@2x.png"),
            (256, "icon_256x256.png"),
            (512, "icon_256x256@2x.png"),
            (512, "icon_512x512.png"),
            (1024, "icon_512x512@2x.png"),
        ):
            base.resize((pixels, pixels), Image.Resampling.LANCZOS).save(iconset / filename)
        output = icon_dir / "openarchsp.icns"
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(output)], check=True)
    return output


def _write_qimage(image: object, path: Path) -> Path:
    if not image.save(str(path)):
        raise SystemExit(f"Could not render application icon PNG: {path}")
    return path


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
    f"{ROOT / 'src' / 'openarchsp' / 'assets'}{separator}openarchsp/assets",
    "--add-data",
    f"{ROOT / 'THIRD_PARTY_NOTICES.md'}{separator}.",
    "--add-data",
    f"{ROOT / 'LICENSES' / 'MPL-2.0.txt'}{separator}LICENSES",
    str(ROOT / "src" / "openarchsp" / "app.py"),
]
native_icon = platform_icon()
if native_icon:
    arguments.extend(["--icon", str(native_icon)])
subprocess.run(arguments, cwd=ROOT, check=True)
