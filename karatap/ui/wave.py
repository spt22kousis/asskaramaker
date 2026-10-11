"""Waveform / spectrogram of the current line with draggable syllable boundaries."""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFontMetricsF, QImage, QPainter, QPen
from PySide6.QtWidgets import QWidget

from ..controller import Controller, Handle
from ..onsets import snap
from ..waveform import colormap, peaks, spectrogram
from .widgets import ACTIVE, BG, DIM, DONE, FG, WARN, ui_font

GRAB_PX = 7          # how close the mouse must be to a boundary to grab it
SNAP_PX = 8          # a dragged boundary snaps to an onset this close
LINE_EDGE = QColor("#e8e8ea")
GAP_SHADE = QColor(0, 0, 0, 110)
SPAN_FILL = QColor(79, 163, 255, 38)


class WaveView(QWidget):
    seek_requested = Signal(float)       # song ms
    changed = Signal(int)                # line whose boundaries were dragged

    def __init__(self, parent=None):
        super().__init__(parent)
        self.ctrl: Controller | None = None
        self.pcm: np.ndarray | None = None
        self.sr = 48000
        self.mode = "wave"               # or 'spec'
        self.song_ms = 0.0
        self.editable = False
        self._cache_key = None
        self._image: QImage | None = None
        self._range: tuple[float, float] | None = None
        self._drag: tuple[int, Handle] | None = None
        self._frozen: tuple[float, float] | None = None
        self.onsets: np.ndarray | None = None
        self.snap_on = False
        self.setMinimumHeight(100)
        self.setMouseTracking(True)

    # -- inputs ------------------------------------------------------------------------
    def set_controller(self, ctrl: Controller | None) -> None:
        self.ctrl = ctrl
        self._cache_key = None
        self.update()

    def set_audio(self, pcm: np.ndarray | None, sr: int) -> None:
        self.pcm, self.sr = pcm, sr
        self._cache_key = None
        self.update()

    def set_mode(self, mode: str) -> None:
        self.mode = mode
        self._cache_key = None
        self.update()

    def set_onsets(self, onsets: np.ndarray | None) -> None:
        self.onsets = onsets
        self.update()

    def set_snap(self, on: bool) -> None:
        self.snap_on = on
        self.update()

    def set_state(self, song_ms: float, editable: bool) -> None:
        self.song_ms, self.editable = song_ms, editable
        if not editable and self._drag:
            self._end_drag()
        self.update()

    # -- geometry ------------------------------------------------------------------------
    def _line(self) -> int | None:
        c = self.ctrl
        if not c or not c.lines:
            return None
        return c.current_line()

    def _bounds(self, li: int) -> list[tuple[float | None, float | None]]:
        """Result bounds, or the partial taps of the running take."""
        c = self.ctrl
        r = c.results.get(li)
        if r:
            return list(r.bounds)
        t = c.take
        if t and li in t.first_target:
            m = t.machine
            return [(m.start[i], m.end[i]) for i in t.line_targets(li)]
        return []

    def _time_range(self, li: int) -> tuple[float, float]:
        if self._frozen:
            return self._frozen
        c = self.ctrl
        lo, hi = float(c.line_start(li)), float(c.line_end(li))
        for s, e in self._bounds(li):
            for v in (s, e):
                if v is not None:
                    lo, hi = min(lo, v), max(hi, v)
        pad = max(300.0, (hi - lo) * 0.08)
        return lo - pad, hi + pad

    def _x(self, ms: float) -> float:
        t0, t1 = self._range
        return (ms - t0) / (t1 - t0) * self.width()

    def _ms(self, x: float) -> float:
        t0, t1 = self._range
        return t0 + x / max(1, self.width()) * (t1 - t0)

    # -- painting --------------------------------------------------------------------------
    def _background(self, li: int) -> QImage | None:
        if self.pcm is None:
            return None
        t0, t1 = self._range
        w, h = self.width(), self.height()
        key = (li, round(t0), round(t1), w, h, self.mode, id(self.pcm))
        if key == self._cache_key:
            return self._image
        if self.mode == "spec":
            data = spectrogram(self.pcm, self.sr, t0, t1, w, h)
            rgb = np.ascontiguousarray(colormap()[data])
            img = QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888).copy()
        else:
            img = QImage(w, h, QImage.Format_RGB32)
            img.fill(BG)
            mins, maxs = peaks(self.pcm, self.sr, t0, t1, w)
            p = QPainter(img)
            p.setPen(QPen(QColor("#5c6b80"), 1))
            mid = h / 2
            for x in range(len(maxs)):
                p.drawLine(QPointF(x + 0.5, mid - maxs[x] * mid * 0.95),
                           QPointF(x + 0.5, mid - mins[x] * mid * 0.95))
            p.end()
        self._cache_key, self._image = key, img
        return img

    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), BG)
        li = self._line()
        if li is None:
            return
        c = self.ctrl
        self._range = self._time_range(li)
        img = self._background(li)
        if img is not None:
            p.drawImage(0, 0, img)
        else:
            p.setPen(DIM)
            p.drawText(self.rect(), Qt.AlignCenter, "載入音訊中…")
        h = self.height()
        bounds = self._bounds(li)
        line = c.lines[li]
        L0, L1 = c.line_start(li), c.line_end(li)

        # Shade what isn't sung: gaps between syllables (or, before any taps, outside the line).
        spans = sorted((self._x(s), self._x(e if e is not None else max(s, self.song_ms)))
                       for s, e in bounds if s is not None)
        if not spans:
            spans = [(self._x(L0), self._x(L1))]
        p.setPen(Qt.NoPen)
        p.setBrush(GAP_SHADE)
        x_prev = 0.0
        for a, b in spans:
            if a > x_prev:
                p.drawRect(QRectF(x_prev, 0, a - x_prev, h))
            x_prev = max(x_prev, b)
        if x_prev < self.width():
            p.drawRect(QRectF(x_prev, 0, self.width() - x_prev, h))

        # Syllable spans with labels.
        label_font = ui_font(13, bold=True)
        fm = QFontMetricsF(label_font)
        p.setFont(label_font)
        for j, (s, e) in enumerate(bounds):
            if s is None:
                continue
            si = line.taps[j]
            f = line.furi[si]
            xs = self._x(s)
            xe = self._x(e) if e is not None else self._x(max(s, self.song_ms))
            p.setPen(Qt.NoPen)
            p.setBrush(SPAN_FILL)
            p.drawRect(QRectF(xs, 0, max(1.0, xe - xs), h))
            text = f.ruby or f.base          # what is sung; the kanji are in the line view above
            p.setPen(FG)
            p.drawText(QRectF(xs + 3, 2, max(0.0, xe - xs - 4), fm.height()),
                       Qt.AlignLeft | Qt.AlignVCenter, fm.elidedText(text, Qt.ElideRight, max(0.0, xe - xs - 4)))

        # Boundaries: starts solid, ends dashed, line edges white.
        for j, (s, e) in enumerate(bounds):
            if s is not None:
                p.setPen(QPen(DONE, 2))
                p.drawLine(QPointF(self._x(s), 0), QPointF(self._x(s), h))
            if e is not None:
                p.setPen(QPen(DONE, 1.5, Qt.DashLine))
                p.drawLine(QPointF(self._x(e), 0), QPointF(self._x(e), h))
        status_warn = c.status(li) == "warning"
        p.setPen(QPen(WARN if status_warn else LINE_EDGE, 2, Qt.DashDotLine))
        for v in (L0, L1):
            p.drawLine(QPointF(self._x(v), 0), QPointF(self._x(v), h))

        if self._drag:
            hx = self._x(self._drag[1].ms)
            p.setPen(QPen(ACTIVE, 3))
            p.drawLine(QPointF(hx, 0), QPointF(hx, h))

        # Onsets a dragged boundary snaps to.
        if self.snap_on and self.onsets is not None:
            p.setPen(QPen(QColor("#8a8f99"), 1))
            t0, t1 = self._range
            a, b = np.searchsorted(self.onsets, [t0, t1])
            for t in self.onsets[a:b]:
                x = self._x(t)
                p.drawLine(QPointF(x, h - 7), QPointF(x, h))

        # Playhead.
        px = self._x(self.song_ms)
        p.setPen(QPen(QColor("#ff4d6d"), 2))
        p.drawLine(QPointF(px, 0), QPointF(px, h))

    # -- mouse ------------------------------------------------------------------------------
    def _nearest_handle(self, x: float, prefer_edges: bool) -> Handle | None:
        li = self._line()
        if li is None or self._range is None:
            return None
        hs = self.ctrl.handles(li)
        if prefer_edges:
            hs = [h for h in hs if h.refs[0][0] in ("L0", "L1")] or hs
        best = min(hs, key=lambda h: abs(self._x(h.ms) - x), default=None)
        if best is None or abs(self._x(best.ms) - x) > GRAB_PX:
            return None
        return best

    def mousePressEvent(self, ev) -> None:
        if ev.button() != Qt.LeftButton or self._range is None:
            return
        x = ev.position().x()
        li = self._line()
        if self.editable and li is not None:
            h = self._nearest_handle(x, bool(ev.modifiers() & Qt.ShiftModifier))
            if h is not None:
                self.ctrl.begin_drag(li)
                self._drag = (li, h)
                self._frozen = self._range
                self.update()
                return
        self.seek_requested.emit(max(0.0, self._ms(x)))

    def mouseMoveEvent(self, ev) -> None:
        x = ev.position().x()
        if self._drag:
            li, h = self._drag
            ms = self._ms(x)
            if self.snap_on and not (ev.modifiers() & Qt.AltModifier):
                t0, t1 = self._range
                ms = snap(ms, self.onsets, SNAP_PX * (t1 - t0) / max(1, self.width()))
            h.ms = self.ctrl.move_handle(li, h, ms)
            self.update()
            return
        near = self.editable and self._nearest_handle(x, bool(ev.modifiers() & Qt.ShiftModifier))
        self.setCursor(Qt.SizeHorCursor if near else Qt.ArrowCursor)

    def mouseReleaseEvent(self, ev) -> None:
        if self._drag:
            self._end_drag()

    def _end_drag(self) -> None:
        li = self._drag[0]
        self._drag = None
        self._frozen = None
        self.changed.emit(li)
        self.update()
