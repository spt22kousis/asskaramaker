import time

import numpy as np

from karatap.onsets import detect, snap

SR = 48000


def tone_bursts(starts_ms, length_ms=250, total_ms=4000, freq=440.0, seed=0, sr=SR):
    rng = np.random.default_rng(seed)
    n = int(sr * total_ms / 1000)
    x = rng.normal(0, 30, n)                      # quiet noise floor
    t = np.arange(int(sr * length_ms / 1000)) / sr
    burst = 8000 * np.sin(2 * np.pi * freq * t) * np.exp(-t * 6)
    fade = int(sr * 0.02)
    burst[-fade:] *= np.linspace(1, 0, fade)      # 20 ms fade out, no click at the end
    for s in starts_ms:
        a = int(sr * s / 1000)
        x[a:a + len(burst)] += burst
    pcm = np.clip(x, -32768, 32767).astype(np.int16)
    return np.stack([pcm, pcm], axis=1)


def test_finds_tone_onsets():
    starts = [500, 1300, 1700, 2600, 3300]
    found = detect(tone_bursts(starts), SR)
    for s in starts:
        assert np.min(np.abs(found - s)) <= 15, (s, found)
    assert len(found) <= len(starts) + 2


def test_snap():
    onsets = np.array([1000.0, 2000.0])
    assert snap(1030, onsets, 50) == 1000
    assert snap(1100, onsets, 50) == 1100
    assert snap(1980, onsets, 50) == 2000
    assert snap(1500, None, 50) == 1500


def test_a_song_length_is_fast_enough():
    pcm = tone_bursts(range(500, 239000, 700), total_ms=240000)
    t = time.perf_counter()
    detect(pcm, SR)
    assert time.perf_counter() - t < 10


def test_no_drift_at_44100():
    # 44.1 kHz frames are 220 samples (4.99 ms), not 5 ms: late in the song that adds up.
    starts = [500, 150000]
    found = detect(tone_bursts(starts, total_ms=151000, sr=44100), 44100)
    for s in starts:
        assert np.min(np.abs(found - s)) <= 15, (s, found)
