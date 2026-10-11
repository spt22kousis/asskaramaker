import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DATA = Path(__file__).resolve().parent / "data"
EXAMPLES = ROOT / "ass_example"
