"""The whole song on one strip: waveform, each line's sung span, the layout's rows."""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFontMetricsF, QImage, QPainter, QPen
from PySide6.QtWidgets import QWidget

from ..ass_doc import format_time
from ..controller import Controller
from ..onsets import snap
from ..waveform import envelope_peaks
from .widgets import BG, DIM, FG, ui_font

LANE_H = 14            # one layout row
RULER_H = 18
EDGE_PX = 6            # how close the mouse must be to a line edge to grab it
SNAP_PX = 8
MIN_SPAN, DEFAULT_SPAN = 2000.0, 20000.0

STATE_COLOR = {"tapped": QColor("#4fa3ff"), "syllables": QColor("#7fd1b9"), "active": QColor("#ffcc33"),
               "next": QColor("#4fa3ff"), "todo": QColor("#4fa3ff")}
ODD, EVEN, SINGLE = QColor("#5b8def"), QColor("#b07cf0"), QColor("#8a8f99")
LATE = QColor("#ff5c5c")
PLAYHEAD = QColor("#ff4d6d")


class TimelineView(QWidget):
    seek_requested = Signal(float)
    line_clicked = Signal(int)
    line_activated = Signal(int)
    changed = Signal(int)             # a line tap was dragged

    def __init__(self, parent=None):
        super().__init__(parent)
        self.ctrl: Controller | None = None
        self.env = None
        self.length_ms = 0.0
        self.t0 = 0.0
        self.span = DEFAULT_SPAN
        self.song_ms = 0.0
        self.playing = False
        self.editable = False
        self.current = -1
        self.onsets: np.ndarray | None = None
        self.snap_on = False
        self._drag: tuple[int, str] | None = None
        self._press: tuple[float, float] | None = None     # (x, t0) when the button went down
        self._panned = False
        self._cache_key = None
        self._image: QImage | None = None
        self.setMinimumHeight(150)
        self.setMouseTracking(True)

    # -- inputs --------------------------------------------------------------------------
    def set_controller(self, ctrl: Controller | None) -> None:
        self.ctrl = ctrl
        self.update()

    def set_audio(self, length_ms: float, env) -> None:
        """Song length and its waveform envelope (computed off the GUI thread; None until ready)."""
        self.length_ms = length_ms
        self.env = env
        self._cache_key = None
        self.update()

    def set_onsets(self, onsets: np.ndarray | None) -> None:
        self.onsets = onsets
        self.update()

    def set_snap(self, on: bool) -> None:
        self.snap_on = on
        self.update()

    def set_state(self, song_ms: float, playing: bool, editable: bool, current: int) -> None:
        self.song_ms, self.playing, self.editable, self.current = song_ms, playing, editable, current
        if playing and not self._drag and not (self.t0 <= song_ms <= self.t0 + self.span * 0.92):
            self.t0 = self._clamp_t0(song_ms - self.span * 0.08)       # turn the page
        if not editable and self._drag:
            self._end_drag()
        self.update()

    def show_ms(self, ms: float) -> None:
        """Scroll so that ms is visible."""
        if not (self.t0 + self.span * 0.05 <= ms <= self.t0 + self.span * 0.9):
            self.t0 = self._clamp_t0(ms - self.span * 0.1)
            self.update()

    # -- geometry ------------------------------------------------------------------------
    def _clamp_t0(self, t0: float) -> float:
        return min(max(t0, -1000.0), max(0.0, self.length_ms - self.span * 0.5))

    def _x(self, ms: float) -> float:
        return (ms - self.t0) / self.span * self.width()

    def _ms(self, x: float) -> float:
        return self.t0 + x / max(1, self.width()) * self.span

    def _lanes_h(self) -> int:
        if not self.ctrl or not self.ctrl.layout_on:
            return 0
        return (LANE_H + 2) * self.ctrl.layout_params.rows + 2

    def _main(self) -> QRectF:
        top = self._lanes_h()
        return QRectF(0, top, self.width(), max(10, self.height() - RULER_H - top))

    def _spans(self) -> list[tuple[int, float, float, str]]:
        """(line, start, end, state) for every line with a span; the line being held runs to now."""
        c = self.ctrl
        out = []
        t = c.line_take
        open_line = t.first + t.machine.open_target if t and t.machine.open_target is not None else None
        for li in range(len(c.lines)):
            if li == open_line:
                s = t.machine.start[li - t.first]
                out.append((li, s, max(s, self.song_ms), "active"))
                continue
            sp = c.sung_span(li)
            if sp:
                out.append((li, sp[0], sp[1], c.line_state(li)))
        return out

    # -- painting --------------------------------------------------------------------------
    def _wave_image(self, main: QRectF) -> QImage | None:
        if self.env is None:
            return None
        w, h = self.width(), int(main.height())
        key = (round(self.t0), round(self.span), w, h)
        if key == self._cache_key:
            return self._image
        img = QImage(w, h, QImage.Format_RGB32)
        img.fill(BG)
        mins, maxs = envelope_peaks(self.env, self.t0, self.t0 + self.span, w)
        p = QPainter(img)
        p.setPen(QPen(QColor("#3d4757"), 1))
        mid = h / 2
        for x in range(w):
            p.drawLine(QPointF(x + 0.5, mid - maxs[x] * mid * 0.95), QPointF(x + 0.5, mid - mins[x] * mid * 0.95))
        p.end()
        self._cache_key, self._image = key, img
        return img

    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), BG)
        c = self.ctrl
        main = self._main()
        img = self._wave_image(main)
        if img is not None:
            p.drawImage(QPointF(0, main.top()), img)
        else:
            p.setPen(DIM)
            p.drawText(main, Qt.AlignCenter, "載入音訊中…")
        if not c:
            return
        font = ui_font(12)
        fm = QFontMetricsF(font)
        p.setFont(font)
        lo, hi = self.t0, self.t0 + self.span

        # Layout rows: when each line is on screen.
        if c.layout_on:
            prm = c.layout_params
            for li, span in enumerate(c.display_spans()):
                if span is None:
                    continue
                s, e, pl = span
                if e < lo or s > hi:
                    continue
                even = prm.rows == 2 and pl.style == prm.even_style and prm.even_style != prm.odd_style
                col = EVEN if even else (ODD if prm.rows == 2 and pl.style == prm.odd_style else SINGLE)
                r = QRectF(self._x(s), 2 + (LANE_H + 2) * even, self._x(e) - self._x(s), LANE_H)
                p.setPen(QPen(LATE, 1.5) if pl.late else Qt.NoPen)
                p.setBrush(QColor(col.red(), col.green(), col.blue(), 150))
                p.drawRect(r)
                if r.width() > 18:
                    p.setPen(FG)
                    p.drawText(r.adjusted(3, 0, 0, 0), Qt.AlignLeft | Qt.AlignVCenter, str(li + 1))

        # Sung spans over the waveform.
        for li, s, e, state in self._spans():
            if e < lo or s > hi:
                continue
            col = STATE_COLOR.get(state, STATE_COLOR["tapped"])
            r = QRectF(self._x(s), main.top() + 2, max(1.0, self._x(e) - self._x(s)), main.height() - 4)
            fill = QColor(col)
            fill.setAlpha(55)
            p.setBrush(fill)
            p.setPen(QPen(col.lighter(130) if li == self.current else col, 2 if li == self.current else 1))
            p.drawRect(r)
            label = f"{li + 1} {c.lines[li].plain}"
            if r.width() > 14:
                p.setPen(FG)
                p.drawText(QRectF(r.left() + 3, r.top() + 1, r.width() - 5, fm.height()),
                           Qt.AlignLeft | Qt.AlignVCenter, fm.elidedText(label, Qt.ElideRight, r.width() - 5))
            if self.editable and c.line_draggable(li):          # grips on editable edges
                p.setPen(QPen(col.lighter(150), 3))
                for x in (r.left(), r.right()):
                    p.drawLine(QPointF(x, r.center().y() - 8), QPointF(x, r.center().y() + 8))

        # Onsets the edges snap to.
        if self.snap_on and self.onsets is not None:
            p.setPen(QPen(QColor("#8a8f99"), 1))
            a, b = np.searchsorted(self.onsets, [lo, hi])
            for t in self.onsets[a:b]:
                x = self._x(t)
                p.drawLine(QPointF(x, main.bottom() - 6), QPointF(x, main.bottom()))

        self._draw_ruler(p, fm)
        x = self._x(self.song_ms)
        p.setPen(QPen(PLAYHEAD, 2))
        p.drawLine(QPointF(x, 0), QPointF(x, self.height()))

    def _draw_ruler(self, p: QPainter, fm: QFontMetricsF) -> None:
        y = self.height() - RULER_H
        p.fillRect(QRectF(0, y, self.width(), RULER_H), BG.darker(115))
        step = next((s for s in (500, 1000, 2000, 5000, 10000, 15000, 30000, 60000)
                     if self.width() / (self.span / s) >= 70), 60000)
        t = (self.t0 // step) * step
        p.setPen(DIM)
        while t <= self.t0 + self.span:
            x = self._x(t)
            p.drawLine(QPointF(x, y), QPointF(x, y + 4))
            if t >= 0:
                label = format_time(t)[2:7] if step >= 1000 else format_time(t)[2:]
                p.drawText(QRectF(x + 3, y, 80, RULER_H), Qt.AlignLeft | Qt.AlignVCenter, label)
            t += step

    # -- mouse --------------------------------------------------------------------------------
    def _line_at(self, x: float) -> int | None:
        if not self.ctrl:
            return None
        ms = self._ms(x)
        hits = [(li, s, e) for li, s, e, _ in self._spans() if s <= ms <= e]
        return min(hits, key=lambda h: h[2] - h[1])[0] if hits else None

    def _edge_at(self, x: float) -> tuple[int, str] | None:
        c = self.ctrl
        if not c or not self.editable:
            return None
        best = None
        for li in range(len(c.lines)):
            if not c.line_draggable(li):
                continue
            s, e = c.line_taps[li]
            for edge, t in (("start", s), ("end", e)):
                d = abs(self._x(t) - x)
                if d <= EDGE_PX and (best is None or d < best[0]):
                    best = (d, li, edge)
        return (best[1], best[2]) if best else None

    def mousePressEvent(self, ev) -> None:
        if ev.button() != Qt.LeftButton:
            return
        x = ev.position().x()
        edge = self._edge_at(x)
        if edge:
            self.ctrl.begin_line_drag(edge[0])
            self._drag = edge
            return
        self._press = (x, self.t0)
        self._panned = False

    def mouseMoveEvent(self, ev) -> None:
        x = ev.position().x()
        if self._drag:
            li, edge = self._drag
            ms = self._ms(x)
            if self.snap_on and not (ev.modifiers() & Qt.AltModifier):
                ms = snap(ms, self.onsets, SNAP_PX * self.span / max(1, self.width()))
            self.ctrl.move_line_edge(li, edge, ms)
            self.update()
            return
        if self._press and ev.buttons() & Qt.LeftButton:
            dx = x - self._press[0]
            if self._panned or abs(dx) > 4:
                self._panned = True
                self.t0 = self._clamp_t0(self._press[1] - dx / max(1, self.width()) * self.span)
                self.update()
            return
        self.setCursor(Qt.SizeHorCursor if self._edge_at(x) else Qt.ArrowCursor)

    def mouseReleaseEvent(self, ev) -> None:
        if self._drag:
            self._end_drag()
            return
        if self._press and not self._panned:
            x = ev.position().x()
            li = self._line_at(x)
            if li is not None:
                self.line_clicked.emit(li)
            self.seek_requested.emit(max(0.0, self._ms(x)))
        self._press = None

    def mouseDoubleClickEvent(self, ev) -> None:
        li = self._line_at(ev.position().x())
        if li is not None:
            self.line_activated.emit(li)

    def _end_drag(self) -> None:
        li = self._drag[0]
        self._drag = None
        self.changed.emit(li)
        self.update()

    def wheelEvent(self, ev) -> None:
        d = ev.angleDelta()
        steps = (d.y() or d.x()) / 120
        if not steps:
            return
        if ev.modifiers() & Qt.ControlModifier:
            anchor = self._ms(ev.position().x())
            new_span = min(max(self.span * 0.8 ** steps, MIN_SPAN), max(MIN_SPAN, self.length_ms or DEFAULT_SPAN))
            frac = (anchor - self.t0) / self.span
            self.span = new_span
            self.t0 = self._clamp_t0(anchor - frac * new_span)
        else:
            self.t0 = self._clamp_t0(self.t0 - steps * self.span * 0.15)
        self.update()
