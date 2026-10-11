"""Build the Windows release zip: dist/karatap-<version>-windows-x64.zip

    .venv/Scripts/python packaging/build_windows.py v0.1.0

Needs PyInstaller in the venv. ffmpeg is not bundled: users install it and
put it on PATH.
"""
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    version = sys.argv[1] if len(sys.argv) > 1 else "dev"
    dist, build = ROOT / "dist", ROOT / "build"
    subprocess.run([
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed",
        "--name", "karatap", "--paths", str(ROOT),
        "--distpath", str(dist), "--workpath", str(build), "--specpath", str(build),
        # Qt modules the app never uses; keeps the zip small
        *[a for m in ("QtWebEngineCore", "QtWebEngineWidgets", "QtPdf", "QtQuick", "QtQml",
                      "Qt3DCore", "QtMultimedia", "QtCharts", "QtDataVisualization")
          for a in ("--exclude-module", f"PySide6.{m}")],
        str(ROOT / "packaging" / "karatap_app.py"),
    ], check=True, cwd=ROOT)
    for doc in ("llm_syllable_split_prompt.md",):
        shutil.copy(ROOT / doc, dist / "karatap" / doc)
    archive = dist / f"karatap-{version}-windows-x64"
    shutil.make_archive(str(archive), "zip", dist, "karatap")
    print(f"built {archive}.zip")
    return 0


if __name__ == "__main__":
    sys.exit(main())
