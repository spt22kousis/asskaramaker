"""Custom-painted widgets: the big syllable cue and the ruby line view."""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPen
from PySide6.QtWidgets import QWidget

from ..controller import Controller, LineInfo
from ..karaoke import merge_syllables, split_points, split_syllable

JP_FONTS = ["Noto Sans JP", "Yu Gothic UI", "Yu Gothic", "Meiryo", "MS Gothic", "Microsoft JhengHei"]
# Small interface text uses Windows' own UI font: it is hinted for small sizes, Noto Sans JP is not.
UI_FONTS = ["Microsoft JhengHei UI", "Microsoft JhengHei", "Yu Gothic UI", "Meiryo UI", "Noto Sans JP"]

BG = QColor("#16181d")
FG = QColor("#e8e8ea")
DIM = QColor("#6b6f7a")
DONE = QColor("#4fa3ff")
ACTIVE = QColor("#ffcc33")
NEXT = QColor("#ffffff")
PRESSED_BG = QColor("#3a3214")
WARN = QColor("#ff5c5c")
EDIT = QColor("#c58cff")


def split_punct(text: str) -> tuple[str, str, str]:
    """'唱！' -> ('', '唱', '！'): punctuation glued to a kanji doesn't get furigana over it."""
    i, j = 0, len(text)
    while i < j and not text[i].isalnum():
        i += 1
    while j > i and not text[j - 1].isalnum():
        j -= 1
    if i == j:
        return "", text, ""
    return text[:i], text[i:j], text[j:]


def ruby_layout(base: str, ruby_w: float, bm: QFontMetricsF) -> tuple[float, float, float]:
    """Place furigana over the kanji only. Returns (unit width, base x offset, ruby x offset)."""
    lead, core, _ = split_punct(base)
    lw, cw = bm.horizontalAdvance(lead), bm.horizontalAdvance(core)
    base_x = max(0.0, ruby_w / 2 - lw - cw / 2)
    center = base_x + lw + cw / 2
    width = max(base_x + bm.horizontalAdvance(base), center + ruby_w / 2)
    return width, base_x, center - ruby_w / 2


def jp_font(px: float, bold: bool = True) -> QFont:
    """Big lyrics: Japanese glyph shapes."""
    f = QFont()
    f.setFamilies(JP_FONTS)
    f.setPixelSize(max(1, int(px)))
    f.setBold(bold)
    return f


def ui_font(px: float, bold: bool = False) -> QFont:
    """Labels, lists, help and other small text."""
    f = QFont()
    f.setFamilies(UI_FONTS)
    f.setPixelSize(max(1, int(px)))
    f.setBold(bold)
    return f


def apply_ui_font(app) -> None:
    """Default font of every widget (menus, buttons, forms, dialogs)."""
    f = app.font()
    f.setFamilies(UI_FONTS)
    app.setFont(f)


@dataclass
class CueItem:
    base: str
    ruby_parts: list[str]     # readings of every beat in this kanji group
    current: int              # which ruby part this beat is (-1: no ruby)
    continuation: bool


def cue_item(line: LineInfo, si: int) -> CueItem:
    f = line.furi[si]
    if not f.ruby:
        return CueItem(f.base, [], -1, False)
    members = [k for k, g in enumerate(line.furi) if g.group == f.group and not line.kara.syls[k].is_gap]
    return CueItem(f.base, [line.furi[k].ruby for k in members], members.index(si), f.continuation)


class CueWidget(QWidget):
    """Huge 'press this next' display, readable from the corner of the eye."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.items: list[CueItem] = []
        self.pressed = False
        self.message = ""
        self.setMinimumHeight(170)

    def set_state(self, items: list[CueItem], pressed: bool, message: str = "") -> None:
        if (items, pressed, message) != (self.items, self.pressed, self.message):
            self.items, self.pressed, self.message = items, pressed, message
            self.update()

    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        r = self.rect()
        p.fillRect(r, PRESSED_BG if self.pressed else BG)
        if self.message:
            p.setPen(DIM)
            p.setFont(jp_font(r.height() * 0.16))
            p.drawText(r, Qt.AlignCenter, self.message)
            return
        if not self.items:
            return
        h = r.height()
        cur = self.items[0]
        # Biggest size whose ruby + base + continuation note fit the height and ~half the width.
        big = h * 0.6
        while big > 10:
            rh = QFontMetricsF(jp_font(big * 0.4)).height()
            bh = QFontMetricsF(jp_font(big)).height()
            if rh + bh + big * 0.22 <= h * 0.96 and self._item_width(cur, big) <= r.width() * 0.5:
                break
            big *= 0.95
        rh = QFontMetricsF(jp_font(big * 0.4)).height()
        bh = QFontMetricsF(jp_font(big)).height()
        base_top = (h - (rh + bh + big * 0.22)) / 2 + rh
        baseline = base_top + QFontMetricsF(jp_font(big)).ascent()
        cx = r.width() * 0.38
        base_rect = self._draw_item(p, cur, cx, baseline, big, main=True)
        # Upcoming syllables, smaller, to the right, on the same baseline.
        x = max(base_rect.right(), cx + self._item_width(cur, big) / 2) + big * 0.4
        for k, it in enumerate(self.items[1:4]):
            size = big * (0.42 - 0.07 * k)
            w = self._item_width(it, size)
            if x + w > r.width():
                break
            self._draw_item(p, it, x + w / 2, baseline, size, main=False, dim_level=k)
            x += w + size * 0.5

    def _item_width(self, it: CueItem, size: float) -> float:
        rw = QFontMetricsF(jp_font(size * 0.4)).horizontalAdvance("".join(it.ruby_parts))
        return ruby_layout(it.base, rw, QFontMetricsF(jp_font(size)))[0]

    def _draw_item(self, p: QPainter, it: CueItem, cx: float, baseline: float, size: float,
                   main: bool, dim_level: int = 0) -> QRectF:
        bf = jp_font(size)
        bm = QFontMetricsF(bf)
        bw = bm.horizontalAdvance(it.base)
        base_rect = QRectF(cx - bw / 2, baseline - bm.ascent(), bw, bm.height())
        if main:
            # The press feedback is the background; the text always means "press this next".
            base_col = DIM if it.continuation else NEXT
        else:
            base_col = QColor(DIM).darker(100 + 25 * dim_level)
        p.setFont(bf)
        p.setPen(base_col)
        p.drawText(base_rect, Qt.AlignCenter, it.base)
        if it.ruby_parts:
            rf = jp_font(size * 0.4)
            rm = QFontMetricsF(rf)
            total = sum(rm.horizontalAdvance(s) for s in it.ruby_parts)
            lead, core, _ = split_punct(it.base)
            x = base_rect.left() + bm.horizontalAdvance(lead) + bm.horizontalAdvance(core) / 2 - total / 2
            y = base_rect.top() - rm.descent()
            p.setFont(rf)
            for k, part in enumerate(it.ruby_parts):
                w = rm.horizontalAdvance(part)
                if main:
                    col = NEXT if k == it.current else (DONE if k < it.current else DIM)
                else:
                    col = base_col if k == it.current else QColor(base_col).darker(160)
                p.setPen(col)
                p.drawText(QRectF(x, y - rm.ascent(), w, rm.height()), Qt.AlignCenter, part)
                x += w
        if main and it.continuation:
            p.setFont(ui_font(size * 0.16))
            p.setPen(DIM)
            p.drawText(QRectF(cx - size * 2, base_rect.bottom(), size * 4, size * 0.22),
                       Qt.AlignHCenter | Qt.AlignTop, "（同一字的下一拍）")
        return base_rect


@dataclass
class Unit:
    base: str
    base_syls: list[int]              # syllables whose state colours the base text
    ruby: list[tuple[str, int]]       # (reading, syllable idx)


def line_units(line: LineInfo) -> list[Unit]:
    units: list[Unit] = []
    for i, f in enumerate(line.furi):
        syl = line.kara.syls[i]
        if syl.is_gap:
            if syl.text:
                units.append(Unit(syl.text.replace("\\h", " ").replace("\\N", " "), [], []))
            continue
        trail = syl.text[len(syl.text.rstrip()):]
        if f.continuation and units and units[-1].ruby:
            units[-1].ruby.append((f.ruby, i))
            units[-1].base_syls.append(i)
            continue
        units.append(Unit(f.base + trail, [i], [(f.ruby, i)] if f.ruby else []))
    return units


@dataclass
class EditSpot:
    x: float
    row: str          # 'base' or 'ruby'
    kind: str         # 'merge' (boundary that can be joined), 'fixed' (can't), 'split' (possible cut)
    syl: int
    at: int = 0       # split: piece index inside the syllable


class LineView(QWidget):
    """Previous / current / next line with furigana and per-syllable state.

    While editing is enabled (playback paused), clicking between two
    characters of the current line splits or merges syllables there.
    """

    edit_requested = Signal(int, str, int, int)     # line, 'split'/'merge', syllable, piece
    syllable_menu = Signal(int, int, QPoint)         # right-click: line, syllable, global position

    def __init__(self, parent=None):
        super().__init__(parent)
        self.ctrl: Controller | None = None
        self.song_ms = 0.0
        self.edit_enabled = False
        self._spots: list[EditSpot] = []
        self._spot_line = -1
        self._syl_rects: list[tuple[QRectF, int]] = []     # main line: where each syllable is drawn
        self._ruby_bottom = 0.0
        self._hit = 0.0
        self.setMinimumHeight(170)

    def mousePressEvent(self, ev) -> None:
        if ev.button() == Qt.RightButton:
            pos = ev.position()
            hits = [(r, si) for r, si in self._syl_rects if r.adjusted(-2, -2, 2, 2).contains(pos)]
            if hits:
                si = min(hits, key=lambda h: h[0].width() * h[0].height())[1]
                self.syllable_menu.emit(self._spot_line, si, ev.globalPosition().toPoint())
            return
        if not self.edit_enabled or not self._spots or ev.button() != Qt.LeftButton:
            return
        pos = ev.position()
        row = "ruby" if pos.y() < self._ruby_bottom else "base"
        for pool in ([s for s in self._spots if s.row == row], self._spots):
            if pool:
                best = min(pool, key=lambda s: abs(s.x - pos.x()))
                if abs(best.x - pos.x()) <= self._hit:
                    self.edit_requested.emit(self._spot_line, best.kind, best.syl, best.at)
                    return

    def set_controller(self, ctrl: Controller | None) -> None:
        self.ctrl = ctrl
        self.update()

    def set_time(self, ms: float) -> None:
        self.song_ms = ms
        self.update()

    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        r = self.rect()
        p.fillRect(r, BG.lighter(115))
        c = self.ctrl
        if not c or not c.lines:
            return
        cur = c.current_line()
        h = r.height()
        rows = [(cur - 1, h * 0.20, 0.09, False), (cur, h * 0.62, 0.20, True), (cur + 1, h * 0.92, 0.09, False)]
        for li, baseline, scale, main in rows:
            if 0 <= li < len(c.lines):
                self._draw_line(p, li, baseline, h * scale, main)
        self._draw_progress(p, cur)

    def _draw_line(self, p: QPainter, li: int, baseline: float, size: float, main: bool) -> None:
        c = self.ctrl
        line = c.lines[li]
        states = c.syllable_states(li)
        units = line_units(line)
        width = self.width() - 40
        while True:
            bf, rf = jp_font(size), jp_font(size * 0.45)
            bm, rm = QFontMetricsF(bf), QFontMetricsF(rf)
            layouts = [ruby_layout(u.base, sum(rm.horizontalAdvance(t) for t, _ in u.ruby), bm)
                       for u in units]
            widths = [l[0] for l in layouts]
            total = sum(widths)
            if total <= width or size < 8:
                break
            size *= width / total * 0.98
        x = (self.width() - total) / 2
        status = c.status(li)
        label = f"{li + 1}"
        p.setFont(ui_font(max(10.0, size * 0.35)))
        p.setPen(WARN if status == "warning" else DIM)
        p.drawText(QRectF(4, baseline - bm.ascent(), 60, bm.height()), Qt.AlignLeft | Qt.AlignVCenter,
                   label + (" ⚠" if status == "warning" else ""))

        def colour(sis: list[int]) -> QColor:
            st = [states.get(s) for s in sis]
            if "active" in st:
                return ACTIVE
            if "next" in st and main:
                return NEXT
            if st and all(s == "done" for s in st):
                return DONE
            return FG if main else DIM

        spots: list[EditSpot] = []
        syl_rects: list[tuple[QRectF, int]] = []
        marked = c.marked(li)
        kara = line.kara
        for n, (u, (w, base_x, ruby_x)) in enumerate(zip(units, layouts)):
            bw = bm.horizontalAdvance(u.base)
            col = colour(u.base_syls)
            p.setFont(bf)
            p.setPen(col)
            rect = QRectF(x + base_x, baseline - bm.ascent(), bw, bm.height())
            p.drawText(rect, Qt.AlignCenter, u.base)
            if u.base_syls:
                syl_rects.append((rect, u.base_syls[0]))
            if marked & set(u.base_syls + [si for _, si in u.ruby]):
                p.setPen(QPen(WARN, max(2.0, size * 0.05)))
                y = rect.bottom() + 1
                p.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            if main and any(states.get(s) == "next" for s in u.base_syls[:1]):
                p.setPen(QPen(NEXT, 2))
                y = rect.bottom() - 2
                p.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            if u.ruby:
                rx = x + ruby_x
                p.setFont(rf)
                for j, (text, si) in enumerate(u.ruby):
                    tw = rm.horizontalAdvance(text)
                    p.setPen(colour([si]))
                    ruby_rect = QRectF(rx, rect.top() - rm.height(), tw, rm.height())
                    p.drawText(ruby_rect, Qt.AlignCenter, text)
                    syl_rects.append((ruby_rect, si))
                    if main:
                        if j:
                            spots.append(self._boundary(kara, u.ruby[j - 1][1], rx, "ruby"))
                        spots += self._split_spots(kara, si, rx, rm, "ruby")
                    rx += tw
            elif main and len(u.base_syls) == 1:
                spots += self._split_spots(kara, u.base_syls[0], rect.left(), bm, "base")
            if main and n + 1 < len(units) and u.base_syls and units[n + 1].base_syls:
                a, b = u.base_syls[-1], units[n + 1].base_syls[0]
                if b == a + 1:
                    spots.append(self._boundary(kara, a, x + w, "base"))
            x += w

        if main:
            self._spots, self._spot_line = spots, li
            self._syl_rects = syl_rects
            self._ruby_bottom = baseline - bm.ascent()
            self._hit = size * 0.45
            if self.edit_enabled:
                self._draw_spots(p, spots, baseline, bm, rm)

    @staticmethod
    def _boundary(kara, si: int, x: float, row: str) -> EditSpot:
        """Boundary after syllable si: 'merge' if the two can be joined, else 'fixed'."""
        kind = "merge" if merge_syllables(kara, si) is not None else "fixed"
        return EditSpot(x, row, kind, si)

    @staticmethod
    def _split_spots(kara, si: int, x0: float, fm: QFontMetricsF, row: str) -> list[EditSpot]:
        pieces = split_points(kara, si)
        out = []
        for k in range(1, len(pieces)):
            if split_syllable(kara, si, k) is not None:
                out.append(EditSpot(x0 + fm.horizontalAdvance("".join(pieces[:k])), row, "split", si, k))
        return out

    def _draw_spots(self, p: QPainter, spots: list[EditSpot], baseline: float,
                    bm: QFontMetricsF, rm: QFontMetricsF) -> None:
        """Existing syllable boundaries as ticks, possible split points as dots."""
        base_top = baseline - bm.ascent()
        for s in spots:
            if s.row == "base":
                y0, y1 = base_top + bm.height() * 0.15, baseline + bm.descent() * 0.6
            else:
                y0, y1 = base_top - rm.height() * 0.95, base_top - rm.height() * 0.1
            if s.kind in ("merge", "fixed"):
                p.setPen(QPen(EDIT if s.kind == "merge" else DIM, 2))
                p.drawLine(QPointF(s.x, y0), QPointF(s.x, y1))
            else:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(EDIT).darker(140))
                r = 2.5
                p.drawEllipse(QPointF(s.x, y1), r, r)

    def _draw_progress(self, p: QPainter, li: int) -> None:
        c = self.ctrl
        if not (0 <= li < len(c.lines)):
            return
        s, e = c.sing_range(li)
        span = max(1.0, e - s)
        frac = (self.song_ms - s) / span
        y = self.height() - 6
        x0, x1 = 20.0, self.width() - 20.0
        p.setPen(Qt.NoPen)
        p.setBrush(DIM.darker(150))
        p.drawRect(QRectF(x0, y, x1 - x0, 3))
        if frac > 0:
            p.setBrush(WARN if frac > 1 else DONE)
            p.drawRect(QRectF(x0, y, (x1 - x0) * min(frac, 1.0), 3))
