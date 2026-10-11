import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DATA = Path(__file__).resolve().parent / "data"
EXAMPLES = ROOT / "ass_example"


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _backups_in_tmp(tmp_path_factory, monkeypatch):
    """Progress backups go to a temp folder, never the user's own."""
    import karatap.controller as controller
    root = tmp_path_factory.mktemp("backups")
    monkeypatch.setattr(controller, "default_backup_root", lambda: root)
