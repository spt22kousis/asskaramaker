"""Mapping from high-resolution system time to song time.

The audio callback reports anchors: "the sample at stretched-track position
P (seconds) reaches the speaker at perf_counter time T". The stretched track
advances in lockstep with real time, so P - T is constant while playing; we
keep the median of recent anchors to reject jitter and interpolate between
callbacks. Song time = stretched position * speed (+ stretch compensation).
"""
from __future__ import annotations

import statistics
import threading
from collections import deque


class SongClock:
    def __init__(self, window: int = 64):
        self._lock = threading.Lock()
        self._offsets: deque[float] = deque(maxlen=window)
        self._gen = 0
        self.speed = 1.0
        self.comp_ms = 0.0          # added to song time (stretch filter compensation)
        self.playing = False
        self._paused_pos = 0.0      # stretched seconds, used while not playing

    def reset(self, stretched_pos: float, speed: float, comp_ms: float = 0.0) -> int:
        """Start a new timeline (after seek / speed change / pause). Returns its generation."""
        with self._lock:
            self._gen += 1
            self._offsets.clear()
            self.speed = speed
            self.comp_ms = comp_ms
            self._paused_pos = stretched_pos
            return self._gen

    def set_playing(self, playing: bool, stretched_pos: float | None = None) -> None:
        with self._lock:
            self.playing = playing
            if stretched_pos is not None:
                self._paused_pos = stretched_pos

    def add_anchor(self, gen: int, perf_s: float, stretched_s: float) -> None:
        with self._lock:
            if gen == self._gen:
                self._offsets.append(stretched_s - perf_s)

    def stretched_at(self, perf_s: float) -> float:
        with self._lock:
            if not self.playing or not self._offsets:
                return self._paused_pos
            return statistics.median(self._offsets) + perf_s

    def song_ms_at(self, perf_s: float, latency_ms: float = 0.0) -> float:
        """Song time (ms) of what was audible at `perf_s` minus the tapping latency.

        The latency is real (wall-clock) time, so it is removed before the
        speed scaling: at 0.5x a 30 ms reaction delay is 15 ms of song time.
        """
        p = self.stretched_at(perf_s - latency_ms / 1000.0)
        return p * self.speed * 1000.0 + self.comp_ms
