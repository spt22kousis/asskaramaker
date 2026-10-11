"""User settings, stored as JSON in the user's config directory."""
from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from .timing import END_RELEASE, HOLD, KEEP, MERGE_PREV


def config_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home()))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "karatap"


@dataclass
class Settings:
    latency_ms: float = 0.0            # subtracted from every tap (calibration result)
    speeds: list[float] = field(default_factory=lambda: [1.0, 0.75, 0.5])
    speed: float = 1.0
    keep_pitch: bool = False           # pitch-changing speed variants render in under a second
    compensate_stretch: bool = True    # correct the time-stretch filter's transient shift
    mode: str = HOLD
    line_timing: str = KEEP
    gap_policy: str = MERGE_PREV
    onset_end: str = END_RELEASE
    pre_pad_ms: int = 0
    post_pad_ms: int = 0
    preroll_ms: int = 1000
    continuous: bool = False
    tap_keys: str = "DFJK"             # each character is a tap key
    end_key: str = "L"
    metronome_bpm: float = 100.0
    wave_mode: str = "wave"            # 'wave' or 'spec' under the line view

    @classmethod
    def load(cls, path: Path | None = None) -> "Settings":
        path = path or config_dir() / "settings.json"
        s = cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return s
        known = {f.name for f in fields(cls)}
        for k, v in data.items():
            if k in known:
                setattr(s, k, v)
        return s

    def save(self, path: Path | None = None) -> None:
        path = path or config_dir() / "settings.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
