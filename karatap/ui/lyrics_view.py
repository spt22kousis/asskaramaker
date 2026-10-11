"""Lyrics editor list: every line with its furigana, words coloured by how
their reading was split. Clicking a word offers the other ways to split it."""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QPoint, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFontMetricsF, QPainter
from PySide6.QtWidgets import QListWidget, QListWidgetItem, QStyle, QStyledItemDelegate

from ..lyrics import Group, LyricLine
from ..readings import WHOLE
from .widgets import DIM, FG, jp_font, ui_font

ROW_H = 66
BASE_PX = 22
RUBY_PX = 12
LEFT = 44

COL_CHECK = QColor(255, 204, 51, 90)       # ambiguous, not yet confirmed
COL_WHOLE = QColor(150, 150, 160, 70)      # kept as one (jukujikun)
COL_AUTO = QColor(79, 209, 140)            # underline: split automatically


@dataclass
class Box:
    rect: QRectF
    group: int          # index into line.groups, -1 for plain text


def layout_line(line: LyricLine, x0: float, top: float) -> list[tuple[Box, str, list[tuple[str, str]]]]:
    """(box, plain text or '', [(piece, ruby)]) per item, left to right."""
    bm = QFontMetricsF(jp_font(BASE_PX, bold=False))
    rm = QFontMetricsF(jp_font(RUBY_PX, bold=False))
    out = []
    x = x0
    gi = -1
    for it in line.items:
        if isinstance(it, str):
            w = bm.horizontalAdvance(it)
            out.append((Box(QRectF(x, top, w, ROW_H - 8), -1), it, []))
        else:
            gi += 1
            w = sum(max(bm.horizontalAdvance(t), rm.horizontalAdvance(r)) for t, r in it.pieces)
            out.append((Box(QRectF(x, top, w, ROW_H - 8), gi), "", it.pieces))
        x += w
    return out


class _Delegate(QStyledItemDelegate):
    def __init__(self, view: "LyricsView"):
        super().__init__(view)
        self.view = view

    def sizeHint(self, option, index) -> QSize:
        return QSize(400, ROW_H)

    def paint(self, p: QPainter, option, index) -> None:
        li = index.row()
        line = self.view.lines[li]
        r = QRectF(option.rect)
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        if option.state & QStyle.State_Selected:
            p.fillRect(r, QColor("#2a3140"))
        p.setFont(ui_font(12))
        p.setPen(DIM)
        p.drawText(QRectF(r.left() + 4, r.top(), LEFT - 8, r.height()), Qt.AlignRight | Qt.AlignVCenter,
                   str(li + 1))
        bf, rf = jp_font(BASE_PX, bold=False), jp_font(RUBY_PX, bold=False)
        bm, rm = QFontMetricsF(bf), QFontMetricsF(rf)
        base_y = r.top() + 6 + rm.height()
        for box, text, pieces in layout_line(line, r.left() + LEFT, r.top() + 4):
            if box.group < 0:
                p.setFont(bf)
                p.setPen(FG)
                p.drawText(QRectF(box.rect.left(), base_y, box.rect.width() + 2, bm.height()),
                           Qt.AlignLeft | Qt.AlignVCenter, text)
                continue
            g = line.groups[box.group]
            if g.needs_check:
                p.fillRect(box.rect, COL_CHECK)
            elif g.state == WHOLE or (len(g.pieces) == 1 and len(g.base) > 1):
                p.fillRect(box.rect, COL_WHOLE)
            x = box.rect.left()
            for t, ruby in pieces:
                w = max(bm.horizontalAdvance(t), rm.horizontalAdvance(ruby))
                p.setFont(rf)
                p.setPen(QColor("#9ecbff"))
                p.drawText(QRectF(x, r.top() + 4, w, rm.height()), Qt.AlignCenter, ruby)
                p.setFont(bf)
                p.setPen(FG)
                p.drawText(QRectF(x, base_y, w, bm.height()), Qt.AlignCenter, t)
                x += w
            if not g.needs_check and g.state != WHOLE and len(g.pieces) > 1:
                p.setPen(COL_AUTO)
                y = base_y + bm.height() + 1
                p.drawLine(int(box.rect.left()) + 1, int(y), int(box.rect.right()) - 1, int(y))
        p.restore()


class LyricsView(QListWidget):
    """Shows `lines`; emits group_clicked(line, group, global pos) when a word is clicked."""

    group_clicked = Signal(int, int, QPoint)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.lines: list[LyricLine] = []
        self.setItemDelegate(_Delegate(self))
        self.setStyleSheet("QListWidget { background: #16181d; color: #e8e8ea; }")
        self.setUniformItemSizes(True)

    def set_lines(self, lines: list[LyricLine], keep_row: int | None = None) -> None:
        self.lines = lines
        self.clear()
        for _ in lines:
            self.addItem(QListWidgetItem())
        if lines:
            self.setCurrentRow(min(max(0, keep_row or 0), len(lines) - 1))

    def refresh(self) -> None:
        self.viewport().update()

    def mousePressEvent(self, ev) -> None:
        super().mousePressEvent(ev)
        if ev.button() != Qt.LeftButton:
            return
        idx = self.indexAt(ev.position().toPoint())
        if not idx.isValid():
            return
        rect = self.visualRect(idx)
        pos = ev.position()
        for box, _, _ in layout_line(self.lines[idx.row()], rect.left() + LEFT, rect.top() + 4):
            if box.group >= 0 and box.rect.adjusted(-2, 0, 2, 0).contains(pos):
                self.group_clicked.emit(idx.row(), box.group, ev.globalPosition().toPoint())
                return


def option_label(g: Group, k: int) -> str:
    pieces = g.dist.options[k]
    text = "　".join(f"{t}（{r}）" if r else t for t, r in pieces)
    if k == len(g.dist.options) - 1 and len(g.dist.options) > 1:
        text += "　← 整組不拆"
    return text


def counts(lines: list[LyricLine]) -> tuple[int, int]:
    """(words to check, words kept whole)."""
    check = whole = 0
    for line in lines:
        for g in line.groups:
            check += g.needs_check
            whole += g.state == WHOLE and len(g.base) > 1
    return check, whole

