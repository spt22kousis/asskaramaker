"""Audio: ffmpeg decoding, speed variants, and a sounddevice player.

Speed variants are rendered ahead of time with ffmpeg:
  * keep pitch: `rubberband` (falls back to `atempo` if unavailable)
  * change pitch: `asetrate` + `aresample` (exact resampling)
The player owns the audio clock: each callback reports which stretched
sample reaches the speaker when, feeding `SongClock`.
"""
from __future__ import annotations

import functools
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .clock import SongClock

CHANNELS = 2


class AudioError(RuntimeError):
    pass


def ffmpeg_exe() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise AudioError("找不到 ffmpeg，請確認它在 PATH 裡")
    return exe


def _run_ffmpeg(args: list[str], stdin: bytes | None = None) -> bytes:
    cmd = [ffmpeg_exe(), "-v", "error", *(["-nostdin"] if stdin is None else []), *args]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    p = subprocess.run(cmd, input=stdin, capture_output=True, creationflags=flags)
    if p.returncode != 0:
        raise AudioError(p.stderr.decode("utf-8", "replace").strip() or "ffmpeg failed")
    return p.stdout


def decode(path: str | Path, sr: int) -> np.ndarray:
    """Decode the first audio stream to int16 stereo frames at `sr`."""
    raw = _run_ffmpeg(["-i", str(path), "-map", "0:a:0", "-vn", "-ac", str(CHANNELS),
                       "-ar", str(sr), "-f", "s16le", "-"])
    return np.frombuffer(raw, np.int16).reshape(-1, CHANNELS)


def _filter_pcm(pcm: np.ndarray, sr: int, filt: str, channels: int = CHANNELS,
                dtype: str = "s16le") -> bytes:
    return _run_ffmpeg(["-f", dtype, "-ar", str(sr), "-ac", str(channels), "-i", "-",
                        "-af", filt, "-f", dtype, "-ar", str(sr), "-ac", str(channels), "-"],
                       stdin=pcm.tobytes())


@functools.lru_cache(maxsize=1)
def has_rubberband() -> bool:
    try:
        out = _run_ffmpeg(["-hide_banner", "-filters"])
    except AudioError:
        return False
    return b" rubberband " in out


def speed_filter(speed: float, keep_pitch: bool, sr: int) -> tuple[str, float]:
    """ffmpeg filter for a speed variant and the exact speed it produces."""
    if keep_pitch:
        if has_rubberband():
            return f"rubberband=tempo={speed:.6f}", speed
        parts, s = [], speed
        while s < 0.5:                # atempo accepts 0.5..100 per instance
            parts.append("atempo=0.5")
            s /= 0.5
        parts.append(f"atempo={s:.6f}")
        return ",".join(parts), speed
    rate = int(round(sr * speed))
    return f"asetrate={rate},aresample={sr}", rate / sr


def measure_stretch_error(filt: str, speed: float, sr: int, n_clicks: int = 48) -> float:
    """Mean timing error (song ms) of transients after stretching with `filt`.

    Time stretchers smear transients, typically making them audible a few ms
    early. We stretch a synthetic click track over a noise bed and compare
    where each click's onset lands (in song time) with where it should be.
    Negative = clicks come early.
    """
    rng = np.random.default_rng(7)
    times = np.cumsum(rng.uniform(0.3, 0.6, n_clicks)) + 0.5
    x = rng.normal(0, 0.02, int((times[-1] + 1.0) * sr)).astype(np.float32)
    k = np.arange(int(0.01 * sr))
    click = (0.8 * np.sin(2 * np.pi * 2000 * k / sr) * np.exp(-k / (0.002 * sr))).astype(np.float32)
    for t in times:
        i = int(round(t * sr))
        x[i:i + len(click)] += click
    y = np.frombuffer(_filter_pcm(x, sr, filt, channels=1, dtype="f32le"), np.float32)
    w = max(1, int(0.002 * sr))
    env = np.convolve(np.abs(y), np.ones(w) / w, mode="same")
    errs = []
    for t in times:
        lo = int((t - 0.1) / speed * sr)
        hi = int((t + 0.1) / speed * sr)
        seg = env[max(lo, 0):hi]
        if len(seg) == 0:
            continue
        p = int(np.argmax(seg))
        first = int(np.flatnonzero(seg > 0.5 * seg[p])[0])
        errs.append((max(lo, 0) + first) / sr * speed - t)
    return float(np.median(errs) * 1000.0) if errs else 0.0


@dataclass
class Variant:
    pcm: np.ndarray        # int16 frames x CHANNELS
    speed: float           # exact playback speed this buffer represents
    comp_ms: float         # added to song time to undo stretch timing error


class AudioSource:
    """Decoded song plus lazily rendered speed variants (thread-safe)."""

    def __init__(self, path: str | Path, sr: int):
        self.path = Path(path)
        self.sr = sr
        self.base = decode(self.path, sr)
        self._variants: dict[tuple[float, bool], Variant] = {
            (1.0, True): Variant(self.base, 1.0, 0.0),
            (1.0, False): Variant(self.base, 1.0, 0.0),
        }
        self._lock = threading.Lock()

    @property
    def duration_ms(self) -> float:
        return len(self.base) / self.sr * 1000.0

    def get(self, speed: float, keep_pitch: bool) -> Variant | None:
        with self._lock:
            return self._variants.get((round(speed, 4), keep_pitch))

    def render(self, speed: float, keep_pitch: bool, compensate: bool = True) -> Variant:
        """Render (or load from the disk cache) a speed variant. Slow; call off the GUI thread."""
        key = (round(speed, 4), keep_pitch)
        v = self.get(speed, keep_pitch)
        if v:
            return v
        filt, exact = speed_filter(speed, keep_pitch, self.sr)
        v = self._load_cached(filt, exact)
        if v is None:
            pcm = np.frombuffer(_filter_pcm(self.base, self.sr, filt), np.int16).reshape(-1, CHANNELS)
            comp = -measure_stretch_error(filt, exact, self.sr) if keep_pitch else 0.0
            v = Variant(pcm, exact, comp)
            self._store_cached(filt, v)
        if not compensate:
            v = Variant(v.pcm, v.speed, 0.0)
        with self._lock:
            self._variants[key] = v
        return v

    def forget_variants(self) -> None:
        """Drop in-memory variants (e.g. after the compensation setting changed)."""
        with self._lock:
            self._variants = {k: v for k, v in self._variants.items() if k[0] == 1.0}

    # Rendered variants are cached on disk so reopening a song is instant.
    def _cache_base(self, filt: str) -> Path:
        st = self.path.stat()
        ident = f"{self.path.resolve()}|{st.st_size}|{st.st_mtime_ns}|{self.sr}|{filt}"
        return cache_dir() / hashlib.sha1(ident.encode("utf-8")).hexdigest()

    def _load_cached(self, filt: str, exact: float) -> Variant | None:
        try:
            base = self._cache_base(filt)
            meta = json.loads(base.with_suffix(".json").read_text(encoding="utf-8"))
            pcm = np.load(base.with_suffix(".npy"))
            return Variant(pcm, exact, float(meta["comp_ms"]))
        except (OSError, ValueError, KeyError):
            return None

    def _store_cached(self, filt: str, v: Variant) -> None:
        try:
            base = self._cache_base(filt)
            base.parent.mkdir(parents=True, exist_ok=True)
            np.save(base.with_suffix(".npy"), v.pcm)
            base.with_suffix(".json").write_text(json.dumps({"comp_ms": v.comp_ms}), encoding="utf-8")
        except OSError:
            pass


def cache_dir() -> Path:
    if sys.platform == "win32":
        root = Path(os.environ.get("LOCALAPPDATA", Path.home()))
    else:
        root = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return root / "karatap" / "cache"


def make_metronome(sr: int, bpm: float, beats: int, lead_s: float = 1.0) -> tuple[np.ndarray, list[float]]:
    """Click track (int16 stereo) and the click times in ms."""
    interval = 60.0 / bpm
    n = int((lead_s + beats * interval + 1.0) * sr)
    x = np.zeros(n, np.float32)
    k = np.arange(int(0.03 * sr))
    times = []
    for b in range(beats):
        t = lead_s + b * interval
        freq = 1600 if b % 4 == 0 else 1000
        click = 0.6 * np.sin(2 * np.pi * freq * k / sr) * np.exp(-k / (0.004 * sr))
        i = int(round(t * sr))
        x[i:i + len(k)] += click.astype(np.float32)
        times.append(t * 1000.0)
    pcm = (np.clip(x, -1, 1) * 32767).astype(np.int16)
    return np.repeat(pcm[:, None], CHANNELS, axis=1), times


def pick_output_device() -> tuple[int | None, int]:
    """Prefer WASAPI on Windows (accurate timing, low latency). Returns (device, samplerate)."""
    import sounddevice as sd
    try:
        for api in sd.query_hostapis():
            if api["name"] == "Windows WASAPI" and api["default_output_device"] >= 0:
                dev = api["default_output_device"]
                return dev, int(sd.query_devices(dev)["default_samplerate"])
    except Exception:
        pass
    dev = sd.default.device[1]
    dev = None if dev is None or dev < 0 else dev
    return dev, int(sd.query_devices(dev, "output")["default_samplerate"])


class Player:
    """Plays one int16 buffer at a time and feeds the SongClock from the audio callback."""

    def __init__(self, clock: SongClock, device: int | None, sr: int):
        import sounddevice as sd
        self.clock = clock
        self.sr = sr
        self._lock = threading.Lock()
        self._buf: np.ndarray | None = None
        self._pos = 0
        self._playing = False
        self._speed = 1.0
        self._comp = 0.0
        self._gen = clock.reset(0.0, 1.0)
        self.on_finished = None      # called from the audio thread when the buffer runs out
        self._stream = sd.OutputStream(samplerate=sr, channels=CHANNELS, dtype="int16",
                                       device=device, latency="low", callback=self._callback)
        self._fallback_latency = float(self._stream.latency)
        self._stream.start()

    def close(self) -> None:
        self._stream.stop()
        self._stream.close()

    # -- control (GUI thread) --------------------------------------------------
    def set_buffer(self, pcm: np.ndarray, speed: float, comp_ms: float, song_ms: float) -> None:
        """Switch to another buffer (speed variant / metronome), keeping the song position."""
        with self._lock:
            self._buf = pcm
            self._speed = speed
            self._comp = comp_ms
            self._seek_locked(song_ms)

    def seek(self, song_ms: float) -> None:
        with self._lock:
            self._seek_locked(song_ms)

    def _seek_locked(self, song_ms: float) -> None:
        n = len(self._buf) if self._buf is not None else 0
        pos = int(round(max(0.0, song_ms) / 1000.0 / self._speed * self.sr))
        self._pos = min(pos, n)
        self._gen = self.clock.reset(self._pos / self.sr, self._speed, self._comp)
        self.clock.set_playing(self._playing)

    def play(self) -> None:
        with self._lock:
            if self._buf is None:
                return
            self._playing = True
            self._gen = self.clock.reset(self._pos / self.sr, self._speed, self._comp)
            self.clock.set_playing(True)

    def pause(self) -> None:
        with self._lock:
            self._playing = False
            self.clock.set_playing(False, self._pos / self.sr)

    @property
    def playing(self) -> bool:
        return self._playing

    def song_ms(self) -> float:
        return self.clock.song_ms_at(time.perf_counter())

    # -- audio thread ----------------------------------------------------------
    def _callback(self, outdata, frames, time_info, status) -> None:
        now = time.perf_counter()
        finished = False
        with self._lock:
            buf = self._buf
            if not self._playing or buf is None:
                outdata.fill(0)
                return
            pos = self._pos
            chunk = buf[pos:pos + frames]
            n = len(chunk)
            outdata[:n] = chunk
            outdata[n:] = 0
            self._pos = pos + n
            gen = self._gen
            if n < frames:
                self._playing = False
                finished = True
        lat = time_info.outputBufferDacTime - time_info.currentTime
        if not (0.0 <= lat < 1.0) or time_info.outputBufferDacTime == 0:
            lat = self._fallback_latency
        self.clock.add_anchor(gen, now + lat, pos / self.sr)
        if finished:
            self.clock.set_playing(False, (pos + n) / self.sr)
            cb = self.on_finished
            if cb:
                cb()
