"""Preview page: the exported lines drawn on a video-sized frame while the song plays."""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from ..controller import PreviewLine
from ..preview import Style, Unit, kara_units, piece_progress, unit_progress
from .timeline import TimelineView
from .widgets import BG, DIM, ui_font

HIGHLIGHT = QColor(0, 118, 255)      # sung colour when a style's primary and secondary are the same
RUBY_GAP = 0.05                      # space between a kanji and its reading, in the reading's size
HINT = ("照匯出的 ASS 畫出每行的樣式、位置和 \\k 填色（不含 template 特效）。半透明的行還沒打音節。　"
        "空白：播放／暫停　←／→：倒退／前進 2 秒　↑／↓：上一行／下一行　點下方時間軸＝跳到那裡")


def qcolor(rgba: tuple[int, int, int, int]) -> QColor:
    return QColor(*rgba)


def style_font(s: Style) -> QFont:
    f = QFont(s.font)
    f.setPixelSize(max(1, round(s.size)))
    f.setBold(s.bold)
    f.setItalic(s.italic)
    if s.scale_x != 100:
        f.setStretch(max(1, min(4000, round(s.scale_x))))
    if s.spacing:
        f.setLetterSpacing(QFont.AbsoluteSpacing, s.spacing)
    return f


@dataclass
class _Drawn:
    """One unit laid out in script pixels."""
    unit: Unit
    path: QPainterPath
    x0: float
    x1: float
    rubies: list[tuple[QPainterPath, float, float]]     # per piece: path, x range


@dataclass
class _Line:
    src: PreviewLine
    style: Style
    ruby_style: Style
    units: list[_Drawn]
    top: float
    bottom: float


class PreviewView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.res = (1920, 1080)
        self.lines: list[_Line] = []
        self.song_ms = 0.0
        self.setMinimumHeight(200)

    def set_content(self, lines: list[PreviewLine], styles: dict[str, Style], res: tuple[int, int]) -> None:
        self.res = res
        self.lines = [self._layout(pl, styles) for pl in lines]
        self.update()

    def set_time(self, ms: float) -> None:
        if ms != self.song_ms:
            self.song_ms = ms
            self.update()

    # -- layout (script pixels) ------------------------------------------------------------
    def _layout(self, pl: PreviewLine, styles: dict[str, Style]) -> _Line:
        st = styles.get(pl.style) or Style(pl.style)
        ruby = styles.get(pl.style + "-furigana")
        if ruby is None:            # like karaskel: half the size of the line's style
            ruby = Style(**{**st.__dict__, "size": st.size / 2, "outline": st.outline / 2})
        font, rfont = style_font(st), style_font(ruby)
        fm, rfm = QFontMetricsF(font), QFontMetricsF(rfont)
        units = kara_units(pl.text)
        widths = [fm.horizontalAdvance(u.base) for u in units]
        w = sum(widths)
        W, H = self.res
        ml, mr, mv = (e or s for e, s in zip(pl.margins, st.margins))
        col, row = (st.alignment - 1) % 3, (st.alignment - 1) // 3
        x = (ml, (W + ml - mr - w) / 2, W - mr - w)[col]
        base = (H - mv - fm.descent(), H / 2 + (fm.ascent() - fm.descent()) / 2, mv + fm.ascent())[row]
        ruby_base = base - fm.ascent() - rfm.descent() - RUBY_GAP * ruby.size
        drawn = []
        for u, uw in zip(units, widths):
            path = QPainterPath()
            path.addText(QPointF(x, base), font, u.base)
            rubies = []
            rw = [rfm.horizontalAdvance(p.ruby) for p in u.pieces]
            rx = x + (uw - sum(rw)) / 2
            for p, pw in zip(u.pieces, rw):
                rp = QPainterPath()
                if p.ruby:
                    rp.addText(QPointF(rx, ruby_base), rfont, p.ruby)
                rubies.append((rp, rx, rx + pw))
                rx += pw
            drawn.append(_Drawn(u, path, x, x + uw, rubies))
            x += uw
        top = ruby_base - rfm.ascent() if any(p.ruby for u in units for p in u.pieces) else base - fm.ascent()
        return _Line(pl, st, ruby, drawn, top, base + fm.descent())

    # -- painting ----------------------------------------------------------------------------
    def _frame(self) -> tuple[QRectF, float]:
        W, H = self.res
        scale = min(self.width() / W, self.height() / H)
        w, h = W * scale, H * scale
        return QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h), scale

    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), BG.darker(130))
        frame, scale = self._frame()
        p.fillRect(frame, Qt.black)
        visible = [ln for ln in self.lines if ln.src.start <= self.song_ms < ln.src.end]
        if not self.lines:
            p.setPen(DIM)
            p.setFont(ui_font(14))
            p.drawText(frame, Qt.AlignCenter, "還沒有可以顯示的行（先打行軸或音節）")
            return
        p.setClipRect(frame)
        p.translate(frame.topLeft())
        p.scale(scale, scale)
        for ln in visible:
            self._draw_line(p, ln, self.song_ms - ln.src.start)

    def _draw_line(self, p: QPainter, ln: _Line, t: float) -> None:
        p.save()
        if not ln.src.timed:
            p.setOpacity(0.45)
        for st, paths in ((ln.style, [d.path for d in ln.units]),
                          (ln.ruby_style, [r[0] for d in ln.units for r in d.rubies])):
            if st.outline > 0:
                pen = QPen(qcolor(st.outline_colour), st.outline * 2)
                pen.setJoinStyle(Qt.RoundJoin)
                for path in paths:
                    p.strokePath(path, pen)
        for d in ln.units:
            frac = unit_progress(d.unit, t) if ln.src.timed else 0.0
            self._fill(p, d.path, ln.style, d.x0, d.x1, frac, ln)
            for piece, (rp, a, b) in zip(d.unit.pieces, d.rubies):
                frac = piece_progress(piece, t) if ln.src.timed else 0.0
                self._fill(p, rp, ln.ruby_style, a, b, frac, ln)
        p.restore()

    @staticmethod
    def _fill(p: QPainter, path: QPainterPath, st: Style, x0: float, x1: float, frac: float, ln: _Line) -> None:
        if path.isEmpty():
            return
        sung = qcolor(st.primary)
        if st.primary == st.secondary:
            sung = HIGHLIGHT
        p.fillPath(path, qcolor(st.secondary))
        if frac <= 0:
            return
        p.save()
        p.setClipRect(QRectF(x0, ln.top - 50, (x1 - x0) * frac, ln.bottom - ln.top + 100), Qt.IntersectClip)
        p.fillPath(path, sung)
        p.restore()


class PreviewPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.view = PreviewView()
        self.timeline = TimelineView()
        self.timeline.setFixedHeight(150)
        hint = QLabel(HINT)
        hint.setWordWrap(True)
        hint.setFont(ui_font(13))
        hint.setStyleSheet("color: #9aa0aa")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 4, 0, 0)
        lay.addWidget(hint)
        lay.addWidget(self.view, 1)
        lay.addWidget(self.timeline)
