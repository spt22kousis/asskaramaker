"""Line timing page: tap once per line while the song plays."""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFontMetricsF, QPainter
from PySide6.QtWidgets import QListWidget, QListWidgetItem, QSplitter, QVBoxLayout, QWidget

from ..ass_doc import format_time
from ..controller import Controller
from .helpbar import HelpBar, key
from .widgets import ACTIVE, BG, DIM, NEXT, PRESSED_BG, jp_font, ui_font

STATE_ICON = {"todo": "○", "tapped": "●", "syllables": "◆", "active": "◐", "next": "▶"}
STATE_COLOR = {"todo": "#9aa0aa", "tapped": "#4fa3ff", "syllables": "#7fd1b9", "active": "#ffcc33",
               "next": "#ffffff"}

HINT = ("{space} 播放　每一行按住 {taps} 其中一鍵：按下＝這行開始唱，放開＝唱完　"
        "{bs} 復原　{r} 從這行重打")

HELP = """這一頁先把每一行「什麼時候開始唱、什麼時候唱完」打出來，之後才到「② 音節」逐字打。
• 打軸鍵：{taps}。每一行按一下：按下＝這行開始唱，放開＝這行唱完。可以兩鍵交替：按下一鍵時，上一行自動結束。
• 起點模式（M 切換）：只看按下的時間，這行到按下一行或結束鍵 {end} 時才結束。
• 空白：播放／暫停　Backspace：復原上一下　R：從目前這行重打　↑／↓：上一行／下一行　←／→：倒退／前進 2 秒
• S：切換速度　P：保持音高　Ctrl+L：延遲校正（第一次用建議先做）
• 清單：○ 還沒打　● 打好了　◆ 已經打好音節（以音節的時間為準）　雙擊某一行＝從那行開始打"""


class LineCue(QWidget):
    """The line to press next, big; the one being held above it; what follows below."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.held = ""
        self.next = ""
        self.after: list[str] = []
        self.pressed = False
        self.message = ""
        self.setMinimumHeight(170)

    def set_state(self, held: str, nxt: str, after: list[str], pressed: bool, message: str = "") -> None:
        state = (held, nxt, after, pressed, message)
        if state != (self.held, self.next, self.after, self.pressed, self.message):
            self.held, self.next, self.after, self.pressed, self.message = state
            self.update()

    def _fit(self, text: str, px: float, width: float) -> float:
        while px > 8 and QFontMetricsF(jp_font(px)).horizontalAdvance(text) > width:
            px *= 0.92
        return px

    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.TextAntialiasing)
        r = self.rect()
        p.fillRect(r, PRESSED_BG if self.pressed else BG)
        w, h = r.width() - 40, r.height()
        if self.message:
            p.setPen(DIM)
            p.setFont(jp_font(h * 0.12))
            p.drawText(r, Qt.AlignCenter, self.message)
            return
        rows = [(self.held, 0.12, ACTIVE, 0.02), (self.next, 0.24, NEXT, 0.2)]
        rows += [(t, 0.10, QColor(DIM).darker(100 + 30 * k), 0.0) for k, t in enumerate(self.after[:2])]
        y = h * 0.06
        for text, size, col, gap in rows:
            px = self._fit(text, h * size, w) if text else h * size
            fm = QFontMetricsF(jp_font(px))
            y += gap * h * 0.3
            if text:
                p.setFont(jp_font(px))
                p.setPen(col)
                p.drawText(QRectF(20, y, w, fm.height()), Qt.AlignHCenter | Qt.AlignVCenter, text)
            y += fm.height() * 1.05


class LinePage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.list = QListWidget()
        self.list.setFont(ui_font(14))
        self.list.setFocusPolicy(Qt.NoFocus)
        self.cue = LineCue()
        self.help = HelpBar()
        split = QSplitter()
        split.addWidget(self.list)
        split.addWidget(self.cue)
        split.setStretchFactor(1, 1)
        split.setSizes([420, 1000])
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.help)
        outer.addWidget(split, 1)
        self._last: list[tuple] = []

    def set_help(self, taps: str, end: str) -> None:
        hint = HINT.format(space=key("空白"), taps=" ".join(key(t) for t in taps), bs=key("Backspace"),
                           r=key("R"))
        self.help.set_texts(hint, HELP.format(taps=" ".join(taps), end=end))

    def rebuild(self, ctrl: Controller) -> None:
        self.list.clear()
        self._last = []
        for _ in ctrl.lines:
            self.list.addItem(QListWidgetItem())
        self.refresh(ctrl)

    def refresh(self, ctrl: Controller) -> None:
        for i, line in enumerate(ctrl.lines):
            st = ctrl.line_state(i)
            sp = ctrl.sung_span(i) if st in ("tapped", "syllables") else None
            key = (st, sp)
            if i < len(self._last) and self._last[i] == key:
                continue
            times = f"　　{format_time(sp[0])}–{format_time(sp[1])}" if sp else ""
            it = self.list.item(i)
            it.setText(f"{i + 1:>3} {STATE_ICON[st]} {line.plain}{times}")
            it.setForeground(QColor(STATE_COLOR[st]))
            if i < len(self._last):
                self._last[i] = key
            else:
                self._last.append(key)
