"""python -m karatap [file.ass] [media]
       python -m karatap --selftest report.txt   (checks the install, e.g. a packaged build)"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def selftest(report: Path) -> int:
    """Import everything, build the main window off-screen, check audio and ffmpeg."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    lines, ok = [], True
    try:
        import numpy
        import sounddevice
        from PySide6.QtWidgets import QApplication

        import shutil

        from .audio import has_rubberband, pick_output_device
        from .settings import Settings
        from .ui.main_window import MainWindow
        from .ui.widgets import apply_ui_font

        lines.append(f"numpy {numpy.__version__}, sounddevice {sounddevice.__version__}, "
                     f"portaudio {sounddevice.get_portaudio_version()[1]}")
        app = QApplication(sys.argv[:1])
        apply_ui_font(app)
        win = MainWindow(Settings())
        app.processEvents()
        if win.player:
            win.player.close()
        lines.append(f"window ok, audio output: {pick_output_device()}")
        from .readings import kanji_table
        lines.append(f"kanji readings: {len(kanji_table())}")
        ff = shutil.which("ffmpeg")
        lines.append(f"ffmpeg: {ff or 'NOT FOUND'}" + (f", rubberband: {has_rubberband()}" if ff else ""))
        ok = ff is not None
    except Exception as e:      # report anything, the point is to find out
        lines.append(f"FAILED: {e!r}")
        ok = False
    report.write_text("\n".join(lines + ["OK" if ok else "PROBLEM"]) + "\n", encoding="utf-8")
    return 0 if ok else 1


def main() -> int:
    if len(sys.argv) >= 3 and sys.argv[1] == "--selftest":
        return selftest(Path(sys.argv[2]))

    from PySide6.QtWidgets import QApplication

    from .settings import Settings
    from .ui.main_window import MainWindow
    from .ui.widgets import apply_ui_font

    app = QApplication(sys.argv)
    app.setApplicationName("karatap")
    apply_ui_font(app)
    win = MainWindow(Settings.load())
    win.show()
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if args:
        win.open(Path(args[0]), Path(args[1]) if len(args) > 1 else None)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
