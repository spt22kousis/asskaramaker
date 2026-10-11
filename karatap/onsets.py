"""Where sounds start (onsets), for snapping dragged boundaries.

Spectral flux on the mix: how much the log spectrum grows from one 5 ms frame
to the next, summed over 150 Hz - 5 kHz (where singing is). Peaks above a
moving average are onsets. Instruments produce onsets too, so snapping only
pulls a boundary that is already close.
"""
from __future__ import annotations

import numpy as np

HOP_MS = 5.0
N_FFT = 1024
FMIN, FMAX = 150.0, 5000.0
MIN_GAP_MS = 50.0
CHUNK = 4096                 # frames per FFT batch (bounded memory)


def onset_strength(pcm: np.ndarray, sr: int) -> tuple[np.ndarray, float]:
    """Flux per frame and the time of frame 0 in ms (frames are HOP_MS apart)."""
    mono = pcm.astype(np.float32).mean(axis=1) if pcm.ndim == 2 else pcm.astype(np.float32)
    mono /= 32768.0
    hop = max(1, int(round(sr * HOP_MS / 1000)))
    if len(mono) < N_FFT + hop:
        return np.zeros(0, dtype=np.float32), 0.0
    n_frames = 1 + (len(mono) - N_FFT) // hop
    freqs = np.fft.rfftfreq(N_FFT, 1 / sr)
    band = (freqs >= FMIN) & (freqs <= FMAX)
    window = np.hanning(N_FFT).astype(np.float32)
    frames = np.lib.stride_tricks.as_strided(
        mono, shape=(n_frames, N_FFT), strides=(mono.strides[0] * hop, mono.strides[0]), writeable=False)
    flux = np.zeros(n_frames, dtype=np.float32)
    prev = None
    for a in range(0, n_frames, CHUNK):
        spec = np.abs(np.fft.rfft(frames[a:a + CHUNK] * window, axis=1))[:, band]
        logspec = np.log1p(100.0 * spec).astype(np.float32)
        if prev is not None:
            logspec = np.vstack([prev[None, :], logspec])
            flux[a:a + CHUNK] = np.maximum(np.diff(logspec, axis=0), 0).sum(axis=1)
        else:
            flux[a + 1:a + CHUNK] = np.maximum(np.diff(logspec, axis=0), 0).sum(axis=1)
        prev = logspec[-1]
    return flux, (N_FFT / 2) / sr * 1000.0


def pick_onsets(flux: np.ndarray, t0_ms: float, hop_ms: float = HOP_MS) -> np.ndarray:
    """Onset times (ms): local flux peaks clearly above the surrounding average."""
    if len(flux) < 3:
        return np.zeros(0)
    win = max(1, int(round(400 / hop_ms)))                 # 0.4 s moving average
    kernel = np.ones(win) / win
    avg = np.convolve(flux, kernel, mode="same")
    floor = 2.0 * float(np.median(flux))                   # ignore noise in quiet parts
    threshold = np.maximum(avg * 1.5, floor) + 1e-6
    reach = max(1, int(round(MIN_GAP_MS / 2 / hop_ms)))
    padded = np.pad(flux, reach, mode="constant", constant_values=-np.inf)
    windows = np.lib.stride_tricks.sliding_window_view(padded, 2 * reach + 1)
    is_peak = (flux >= windows.max(axis=1)) & (flux > threshold)
    idx = np.flatnonzero(is_peak)
    # equal neighbours can make plateaus: keep the first of each run
    if len(idx) > 1:
        idx = idx[np.concatenate([[True], np.diff(idx) > reach])]
    return t0_ms + idx * hop_ms


def detect(pcm: np.ndarray, sr: int) -> np.ndarray:
    flux, t0 = onset_strength(pcm, sr)
    return pick_onsets(flux, t0)


def snap(ms: float, onsets: np.ndarray | None, tolerance_ms: float) -> float:
    """The nearest onset within tolerance, else ms unchanged."""
    if onsets is None or len(onsets) == 0:
        return ms
    i = int(np.searchsorted(onsets, ms))
    best = min((onsets[j] for j in (i - 1, i) if 0 <= j < len(onsets)), key=lambda t: abs(t - ms))
    return float(best) if abs(best - ms) <= tolerance_ms else ms
