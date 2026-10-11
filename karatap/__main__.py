"""python -m karatap [file.ass] [media]"""
from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    from PySide6.QtWidgets import QApplication

    from .settings import Settings
    from .ui.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("karatap")
    win = MainWindow(Settings.load())
    win.show()
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if args:
        win.open(Path(args[0]), Path(args[1]) if len(args) > 1 else None)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
