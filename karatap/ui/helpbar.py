"""Help at the top of a page: one always-visible line, the full list on click."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QToolButton, QVBoxLayout, QWidget

from .widgets import ui_font


class HelpBar(QWidget):
    toggled = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.hint = QLabel()
        self.hint.setFont(ui_font(15))
        self.hint.setTextFormat(Qt.RichText)
        self.hint.setWordWrap(True)
        self.button = QToolButton()
        self.button.setCheckable(True)
        self.button.setFocusPolicy(Qt.NoFocus)
        self.button.setFont(ui_font(14))
        self.button.setStyleSheet("QToolButton { padding: 3px 10px; border: 1px solid #3a4150; border-radius: 4px; }"
                                  "QToolButton:checked { background: #2a3140; }")
        self.button.toggled.connect(self._toggled)
        self.full = QLabel()
        self.full.setFont(ui_font(13))
        self.full.setWordWrap(True)
        self.full.setStyleSheet("color: #c4c8d0; background: #1b1e24; padding: 8px 10px; border-radius: 4px;")
        self.full.hide()
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.hint, 1)
        row.addWidget(self.button, 0, Qt.AlignTop)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.addLayout(row)
        lay.addWidget(self.full)
        self._hint_html = ""
        self._toggled(False)

    def set_texts(self, hint_html: str, full_text: str) -> None:
        self.set_hint(hint_html)
        self.full.setText(full_text)

    def set_hint(self, html: str) -> None:
        if html != self._hint_html:
            self._hint_html = html
            self.hint.setText(html)

    def set_open(self, open_: bool) -> None:
        self.button.setChecked(open_)

    def _toggled(self, open_: bool) -> None:
        self.full.setVisible(open_)
        self.button.setText("收起說明 ▲" if open_ else "操作說明 ▼")
        self.toggled.emit(open_)


def key(text: str) -> str:
    """A key name styled like a keycap inside a hint."""
    return (f"<span style='background:#2a3140; color:#ffffff; font-weight:bold;'>&nbsp;{text}&nbsp;</span>")
