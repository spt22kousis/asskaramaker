"""Waveform peaks and spectrogram images for a stretch of the song (numpy only)."""
from __future__ import annotations

import numpy as np


def _segment(pcm: np.ndarray, sr: int, t0_ms: float, t1_ms: float) -> tuple[np.ndarray, int]:
    """Mono float samples for [t0, t1] (zero-padded outside the song) and the index of t0."""
    a = int(round(t0_ms / 1000 * sr))
    b = int(round(t1_ms / 1000 * sr))
    out = np.zeros(max(0, b - a), np.float32)
    lo, hi = max(a, 0), min(b, len(pcm))
    if hi > lo:
        chunk = pcm[lo:hi].astype(np.float32)
        if chunk.ndim == 2:
            chunk = chunk.mean(axis=1)
        out[lo - a:hi - a] = chunk / 32768.0
    return out, a


def peaks(pcm: np.ndarray, sr: int, t0_ms: float, t1_ms: float, width: int) -> tuple[np.ndarray, np.ndarray]:
    """Per-pixel (min, max) of the signal in [-1, 1]."""
    x, _ = _segment(pcm, sr, t0_ms, t1_ms)
    if width <= 0 or len(x) == 0:
        return np.zeros(0), np.zeros(0)
    edges = np.linspace(0, len(x), width + 1).astype(int)
    mins = np.zeros(width, np.float32)
    maxs = np.zeros(width, np.float32)
    for i in range(width):
        seg = x[edges[i]:max(edges[i + 1], edges[i] + 1)]
        if len(seg):
            mins[i], maxs[i] = seg.min(), seg.max()
    return mins, maxs


ENVELOPE_MS = 5.0


def envelope(pcm: np.ndarray, sr: int, bin_ms: float = ENVELOPE_MS) -> tuple[np.ndarray, np.ndarray, float]:
    """(min, max) of the whole song per bin of about `bin_ms`, computed once for the song timeline,
    and the bin's exact length in ms (a whole number of samples)."""
    mono = pcm.astype(np.float32).mean(axis=1) if pcm.ndim == 2 else pcm.astype(np.float32)
    n = max(1, int(round(sr * bin_ms / 1000)))
    exact = n / sr * 1000.0
    bins = len(mono) // n
    if bins == 0:
        return np.zeros(0, np.float32), np.zeros(0, np.float32), exact
    x = mono[:bins * n].reshape(bins, n) / 32768.0
    return x.min(axis=1), x.max(axis=1), exact


def envelope_peaks(env: tuple[np.ndarray, np.ndarray, float], t0_ms: float, t1_ms: float,
                   width: int) -> tuple[np.ndarray, np.ndarray]:
    """Per-pixel (min, max) for [t0, t1] from an envelope (zeros outside the song)."""
    lo_env, hi_env, bin_ms = env
    mins = np.zeros(max(width, 0), np.float32)
    maxs = np.zeros(max(width, 0), np.float32)
    if width <= 0 or len(lo_env) == 0:
        return mins, maxs
    edges = np.floor(np.linspace(t0_ms, t1_ms, width + 1) / bin_ms).astype(int)
    for i in range(width):
        a, b = max(edges[i], 0), min(max(edges[i + 1], edges[i] + 1), len(lo_env))
        if b > a:
            mins[i], maxs[i] = lo_env[a:b].min(), hi_env[a:b].max()
    return mins, maxs


def spectrogram(pcm: np.ndarray, sr: int, t0_ms: float, t1_ms: float, width: int, height: int,
                fmin: float = 80.0, fmax: float = 8000.0, n_fft: int = 2048,
                dynamic_db: float = 70.0) -> np.ndarray:
    """uint8 image (height, width) of log-frequency power: row 0 = fmax, last row = fmin."""
    x, _ = _segment(pcm, sr, t0_ms, t1_ms)
    if width <= 0 or height <= 0:
        return np.zeros((max(height, 0), max(width, 0)), np.uint8)
    # One analysis frame centred on each pixel column.
    centres = np.linspace(0, len(x), width, endpoint=False) + len(x) / width / 2
    padded = np.pad(x, (n_fft // 2, n_fft // 2))
    starts = np.clip(centres.astype(int), 0, max(len(padded) - n_fft, 0))
    idx = starts[:, None] + np.arange(n_fft)[None, :]
    frames = padded[np.clip(idx, 0, len(padded) - 1)] * np.hanning(n_fft).astype(np.float32)
    power = np.abs(np.fft.rfft(frames, axis=1)) ** 2            # (width, n_fft/2+1)
    freqs = np.fft.rfftfreq(n_fft, 1 / sr)
    rows = np.geomspace(fmax, fmin, height)                       # top row = high frequency
    bins = np.clip(np.searchsorted(freqs, rows), 1, len(freqs) - 1)
    db = 10 * np.log10(power[:, bins].T + 1e-12)                  # (height, width)
    top = db.max() if db.size else 0.0
    img = np.clip((db - (top - dynamic_db)) / dynamic_db, 0, 1)
    return (img * 255).astype(np.uint8)


def colormap() -> np.ndarray:
    """256 x RGB lookup from black through purple and orange to pale yellow."""
    stops = np.array([[0, 0, 0], [40, 10, 80], [150, 30, 110], [235, 100, 50], [255, 230, 150]], float)
    pos = np.linspace(0, 1, len(stops))
    t = np.linspace(0, 1, 256)
    return np.stack([np.interp(t, pos, stops[:, c]) for c in range(3)], axis=1).astype(np.uint8)
