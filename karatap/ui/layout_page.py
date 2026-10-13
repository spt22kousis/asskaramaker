"""Layout and export page: the Odd/Even rule's settings and the resulting line times."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout,
                               QHBoxLayout, QHeaderView, QInputDialog, QLabel, QMessageBox,
                               QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ..ass_doc import format_time, parse_time
from ..controller import Controller
from ..project import lyric_styles
from .widgets import ui_font

COLUMNS = ["行", "樣式", "出現", "消失", "開唱", "歌詞"]
COL_START, COL_END = 2, 3


class LayoutPage(QWidget):
    changed = Signal()              # settings or a manual time changed
    export_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.ctrl: Controller | None = None
        self._loading = False
        self.on = QCheckBox("套用版面（輸出時依下面的規則決定每行的出現／消失時間和樣式）")
        self.rows = QComboBox()
        self.rows.addItem("雙行（奇偶兩排交替）", 2)
        self.rows.addItem("單行（一次一行）", 1)
        self.odd, self.even, self.single, self.one = QComboBox(), QComboBox(), QComboBox(), QComboBox()
        self.gap = self._seconds(0, 30, "間隔超過這麼久就分成新的一段")
        self.lead = self._seconds(0, 10, "每段第一、二行比開唱早多久出現")
        self.slot = self._seconds(0, 5, "同一排的上一行消失後，隔多久出現下一行")
        self.tail = self._seconds(0, 5, "唱完後多留多久才消失")
        form = QFormLayout()
        self.form = form
        form.addRow("排數", self.rows)
        form.addRow("奇數行樣式", self.odd)
        form.addRow("偶數行樣式", self.even)
        form.addRow("整段只有一行時的樣式", self.single)
        form.addRow("單行樣式", self.one)
        form.addRow("分段間隔", self.gap)
        form.addRow("段首提前出現", self.lead)
        form.addRow("同排接續間隔", self.slot)
        form.addRow("唱完後停留", self.tail)
        for w in (self.rows, self.odd, self.even, self.single, self.one):
            w.currentIndexChanged.connect(self._apply)
        for w in (self.gap, self.lead, self.slot, self.tail):
            w.valueChanged.connect(self._apply)
        self.on.toggled.connect(self._apply)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setFont(ui_font(13))
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(len(COLUMNS) - 1, QHeaderView.Stretch)
        self.table.cellDoubleClicked.connect(self._edit_time)
        self.summary = QLabel()
        self.summary.setFont(ui_font(14))
        clear = QPushButton("清除選取行的手動時間")
        clear.clicked.connect(self._clear_override)
        export = QPushButton("匯出 ASS…（Ctrl+E）")
        export.clicked.connect(self.export_requested.emit)
        hint = QLabel("雙擊「出現」或「消失」可以手動改那一行的時間（藍字＝手動）；紅字＝出現時已經開唱了，"
                      "通常是上一行太短，可以手動調早一點。還沒打行軸的行輸出時保持原樣。")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #8a8f99")

        top = QHBoxLayout()
        top.addLayout(form)
        top.addStretch(1)
        buttons = QHBoxLayout()
        buttons.addWidget(clear)
        buttons.addStretch(1)
        buttons.addWidget(export)
        lay = QVBoxLayout(self)
        lay.addWidget(self.on)
        lay.addLayout(top)
        lay.addWidget(hint)
        lay.addWidget(self.summary)
        lay.addWidget(self.table, 1)
        lay.addLayout(buttons)

    @staticmethod
    def _seconds(lo: float, hi: float, tip: str) -> QDoubleSpinBox:
        s = QDoubleSpinBox()
        s.setRange(lo, hi)
        s.setDecimals(2)
        s.setSingleStep(0.1)
        s.setSuffix(" 秒")
        s.setToolTip(tip)
        return s

    def set_controller(self, ctrl: Controller | None) -> None:
        self.ctrl = ctrl
        if ctrl is None:
            return
        self._loading = True
        styles = lyric_styles(ctrl.doc.style_names())
        p = ctrl.layout_params
        self.rows.setCurrentIndex(self.rows.findData(p.rows) if self.rows.findData(p.rows) >= 0 else 0)
        for combo, value in ((self.odd, p.odd_style), (self.even, p.even_style), (self.single, p.single_style),
                             (self.one, p.one_style)):
            combo.clear()
            combo.addItems(styles if value in styles else styles + [value])     # a furigana one picked before stays
            combo.setCurrentText(value)
        self.gap.setValue(p.segment_gap_ms / 1000)
        self.lead.setValue(p.lead_ms / 1000)
        self.slot.setValue(p.slot_gap_ms / 1000)
        self.tail.setValue(p.tail_ms / 1000)
        self.on.setChecked(ctrl.layout_on)
        self._loading = False
        self.refresh()

    def _apply(self) -> None:
        if self._loading or not self.ctrl:
            return
        c, p = self.ctrl, self.ctrl.layout_params
        c.checkpoint("版面設定", merge="layout")
        c.layout_on = self.on.isChecked()
        p.rows = self.rows.currentData()
        p.odd_style, p.even_style = self.odd.currentText(), self.even.currentText()
        p.single_style, p.one_style = self.single.currentText(), self.one.currentText()
        p.segment_gap_ms = round(self.gap.value() * 1000)
        p.lead_ms = round(self.lead.value() * 1000)
        p.slot_gap_ms = round(self.slot.value() * 1000)
        p.tail_ms = round(self.tail.value() * 1000)
        c.dirty = True
        self.refresh()
        self.changed.emit()

    def refresh(self) -> None:
        c = self.ctrl
        if not c:
            return
        with c.layout_fixed():
            self._refresh(c)

    def _refresh(self, c: Controller) -> None:
        two = c.layout_params.rows == 2
        for w in (self.rows, self.odd, self.even, self.single, self.one, self.gap, self.lead, self.slot,
                  self.tail):
            w.setEnabled(c.layout_on)
        for w in (self.odd, self.even, self.single, self.gap):
            self.form.setRowVisible(w, two)
        self.form.setRowVisible(self.one, not two)
        self.form.labelForField(self.lead).setText("段首提前出現" if two else "提前出現")
        self.form.labelForField(self.slot).setText("同排接續間隔" if two else "上一行消失後隔多久出現")
        pl = c.placements() if c.layout_on else [None] * len(c.lines)
        self.table.setRowCount(len(c.lines))
        late = untimed = 0
        for i, line in enumerate(c.lines):
            p = pl[i]
            sung = c.sung_span(i)
            ov = c.layout_overrides.get(i, (None, None)) if c.layout_on else (None, None)
            if c.layout_on and p is None:
                untimed += 1
            if p is not None and p.late and ov[0] is None:
                late += 1
            cells = [
                str(i + 1),
                p.style if p else line.event.style,
                format_time(c.line_start(i)) if p else "—",
                format_time(c.line_end(i)) if p else "—",
                format_time(sung[0]) if sung else "—",
                line.plain,
            ]
            for col, text in enumerate(cells):
                it = QTableWidgetItem(text)
                if col in (COL_START, COL_END) and ov[col - COL_START] is not None:
                    it.setForeground(QColor("#4fa3ff"))
                elif col == COL_START and p is not None and p.late:
                    it.setForeground(QColor("#ff5c5c"))
                elif p is None and c.layout_on:
                    it.setForeground(QColor("#6b6f7a"))
                self.table.setItem(i, col, it)
        self.table.resizeColumnsToContents()
        if not c.layout_on:
            self.summary.setText("沒有套用版面：輸出時每行的時間和樣式照原本的檔案。")
        else:
            parts = [f"{len(c.lines) - untimed} 行已排好"]
            if untimed:
                parts.append(f"<span style='color:#9aa0aa'>{untimed} 行還沒打行軸</span>")
            if late:
                parts.append(f"<span style='color:#ff5c5c'>{late} 行出現時已開唱</span>")
            self.summary.setText("　｜　".join(parts))

    def _edit_time(self, row: int, col: int) -> None:
        c = self.ctrl
        if not c or not c.layout_on or col not in (COL_START, COL_END) or c.placements()[row] is None:
            return
        cur = c.line_start(row) if col == COL_START else c.line_end(row)
        text, ok = QInputDialog.getText(self, "手動時間", f"第 {row + 1} 行的{COLUMNS[col]}時間：",
                                        text=format_time(cur))
        if not ok:
            return
        try:
            ms = parse_time(text)
        except ValueError:
            QMessageBox.warning(self, "手動時間", "時間格式是 0:01:23.45")
            return
        s, e = c.layout_overrides.get(row, (None, None))
        if col == COL_START:
            s = ms
        else:
            e = ms
        start = s if s is not None else c.placements()[row].start
        end = e if e is not None else c.placements()[row].end
        if end <= start:
            QMessageBox.warning(self, "手動時間", "消失時間要晚於出現時間。")
            return
        c.checkpoint("手動時間")
        c.set_layout_override(row, s, e)
        self.refresh()
        self.changed.emit()

    def _clear_override(self) -> None:
        c = self.ctrl
        rows = {i.row() for i in self.table.selectedIndexes()}
        if not c or not rows:
            return
        c.checkpoint("清除手動時間")
        for r in rows:
            c.set_layout_override(r, None, None)
        self.refresh()
        self.changed.emit()
