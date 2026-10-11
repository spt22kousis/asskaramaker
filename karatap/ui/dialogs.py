"""Latency calibration and settings dialogs."""
from __future__ import annotations

import bisect
import statistics
import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
                               QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QRadioButton, QSpinBox, QVBoxLayout)

from ..audio import Player, make_metronome
from ..settings import Settings
from ..timing import END_KEY, END_RELEASE, INSERT_GAP, MERGE_PREV
from .widgets import ui_font

WARMUP_TAPS = 4


def summarize_offsets(errs: list[float]) -> tuple[float, float, int]:
    """Robust mean/std of tap errors (ms): drop taps further than 3 MADs from the median."""
    med = statistics.median(errs)
    mad = statistics.median(abs(e - med) for e in errs) or 1.0
    kept = [e for e in errs if abs(e - med) <= 3 * 1.4826 * mad]
    sd = statistics.pstdev(kept) if len(kept) > 1 else 0.0
    return statistics.fmean(kept), sd, len(kept)


class CalibrationDialog(QDialog):
    """Plays a metronome; the user taps along; the mean offset becomes the latency setting."""

    def __init__(self, player: Player, settings: Settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("延遲校正")
        self.player = player
        self.settings = settings
        self.tap_keys = {ord(ch.upper()) for ch in settings.tap_keys} | {Qt.Key_Space}
        self.clicks: list[float] = []
        self.errs: list[float] = []
        self.result: float | None = None

        self.info = QLabel(f"按「開始」後會播放節拍器（{settings.metronome_bpm:g} BPM）。\n"
                           f"用打軸鍵（{settings.tap_keys}）或空白鍵跟著拍子敲，前 {WARMUP_TAPS} 下不計。")
        self.stats = QLabel("—")
        self.stats.setFont(ui_font(22, bold=True))
        self.start_btn = QPushButton("開始")
        self.start_btn.clicked.connect(self.start)
        self.apply_btn = QPushButton("套用")
        self.apply_btn.setEnabled(False)
        self.apply_btn.clicked.connect(self.accept)
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        row = QHBoxLayout()
        for b in (self.start_btn, self.apply_btn, cancel):
            row.addWidget(b)
        lay = QVBoxLayout(self)
        lay.addWidget(self.info)
        lay.addWidget(self.stats)
        lay.addLayout(row)
        self.resize(520, 220)
        self.finished.connect(lambda _: self.player.pause())

    def start(self) -> None:
        sr = self.player.sr
        pcm, self.clicks = make_metronome(sr, self.settings.metronome_bpm, beats=40)
        self.errs = []
        self.player.pause()
        self.player.set_buffer(pcm, 1.0, 0.0, 0.0)
        self.player.play()
        self.start_btn.setText("重新開始")
        self.stats.setText("跟著拍子敲…")
        self.setFocus()

    def keyPressEvent(self, ev) -> None:
        now = time.perf_counter()
        if ev.isAutoRepeat() or ev.key() not in self.tap_keys or not self.player.playing:
            return super().keyPressEvent(ev) if ev.key() == Qt.Key_Escape else None
        song = self.player.clock.song_ms_at(now)
        k = bisect.bisect_left(self.clicks, song)
        near = min((c for c in self.clicks[max(0, k - 1):k + 1]), key=lambda c: abs(c - song))
        self.errs.append(song - near)
        taps = self.errs[WARMUP_TAPS:]
        if len(taps) >= 4:
            mean, sd, n = summarize_offsets(taps)
            self.result = mean
            self.stats.setText(f"偏移 {mean:+.1f} ms   標準差 {sd:.1f} ms   （{n} 下）")
            self.apply_btn.setEnabled(n >= 8)
        else:
            self.stats.setText(f"暖身中… {len(self.errs)}")


class ShiftDialog(QDialog):
    """How far to move the times, and of which lines."""

    def __init__(self, n_selected: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("時間平移")
        self.ms = QSpinBox()
        self.ms.setRange(-600000, 600000)
        self.ms.setSingleStep(10)
        self.ms.setSuffix(" ms")
        self.all = QRadioButton("整首")
        self.selected = QRadioButton(f"清單裡選取的 {n_selected} 行")
        self.selected.setEnabled(n_selected > 0)
        (self.selected if n_selected > 1 else self.all).setChecked(True)
        hint = QLabel("正數＝往後（變晚），負數＝往前（變早）。行軸、音節時間和手動設定的出現／消失時間會一起移動。")
        hint.setWordWrap(True)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form = QFormLayout()
        form.addRow("平移", self.ms)
        form.addRow("範圍", self.all)
        form.addRow("", self.selected)
        lay = QVBoxLayout(self)
        lay.addWidget(hint)
        lay.addLayout(form)
        lay.addWidget(buttons)
        self.resize(420, 180)

    @property
    def delta(self) -> int:
        return self.ms.value()

    @property
    def only_selected(self) -> bool:
        return self.selected.isChecked()


class SettingsDialog(QDialog):
    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("設定")
        s = settings
        self.latency = QDoubleSpinBox(); self.latency.setRange(-500, 500); self.latency.setDecimals(1)
        self.latency.setSuffix(" ms"); self.latency.setValue(s.latency_ms)
        self.preroll = QSpinBox(); self.preroll.setRange(0, 10000); self.preroll.setSingleStep(100)
        self.preroll.setSuffix(" ms"); self.preroll.setValue(s.preroll_ms)
        self.pre_pad = QSpinBox(); self.pre_pad.setRange(0, 5000); self.pre_pad.setSuffix(" ms")
        self.pre_pad.setValue(s.pre_pad_ms)
        self.post_pad = QSpinBox(); self.post_pad.setRange(0, 5000); self.post_pad.setSuffix(" ms")
        self.post_pad.setValue(s.post_pad_ms)
        self.speeds = QLineEdit(", ".join(f"{v:g}" for v in s.speeds))
        self.tap_keys = QLineEdit(s.tap_keys)
        self.end_key = QLineEdit(s.end_key); self.end_key.setMaxLength(1)
        self.gap = QComboBox()
        self.gap.addItem("併進前一個音節", MERGE_PREV)
        self.gap.addItem("自動插入空的 {\\k}", INSERT_GAP)
        self.gap.setCurrentIndex(self.gap.findData(s.gap_policy))
        self.onset_end = QComboBox()
        self.onset_end.addItem("放開按鍵", END_RELEASE)
        self.onset_end.addItem("結束鍵", END_KEY)
        self.onset_end.setCurrentIndex(self.onset_end.findData(s.onset_end))
        self.comp = QCheckBox("補償保持音高變速造成的時間偏移"); self.comp.setChecked(s.compensate_stretch)
        self.bpm = QDoubleSpinBox(); self.bpm.setRange(40, 240); self.bpm.setValue(s.metronome_bpm)

        form = QFormLayout()
        form.addRow("敲擊延遲（扣除）", self.latency)
        form.addRow("行前預播", self.preroll)
        form.addRow("模式 2 行前留白", self.pre_pad)
        form.addRow("模式 2 行後留白", self.post_pad)
        form.addRow("速度清單", self.speeds)
        form.addRow("打軸鍵（每個字元一個鍵）", self.tap_keys)
        form.addRow("結束鍵", self.end_key)
        form.addRow("長按模式：放開後的空白", self.gap)
        form.addRow("起點模式：行尾音節結束於", self.onset_end)
        form.addRow("", self.comp)
        form.addRow("校正節拍器 BPM", self.bpm)
        self.error = QLabel()
        self.error.setStyleSheet("color: #ff5c5c")
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._ok)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(self.error)
        lay.addWidget(buttons)
        self.settings = settings

    def _ok(self) -> None:
        try:
            speeds = [float(x) for x in self.speeds.text().replace("，", ",").split(",") if x.strip()]
        except ValueError:
            speeds = []
        if not speeds or any(not 0.25 <= v <= 2.0 for v in speeds):
            self.error.setText("速度清單要是 0.25～2 之間的數字，用逗號分隔")
            return
        keys = "".join(dict.fromkeys(self.tap_keys.text().upper().replace(" ", "")))
        end_key = self.end_key.text().upper()
        reserved = set("RMSPCTEV")
        if not keys or reserved & set(keys + end_key) or end_key in keys:
            self.error.setText("打軸鍵不能空白，不能和結束鍵重複，也不能用 R M S P C T E V（已是快捷鍵）")
            return
        s = self.settings
        s.latency_ms = self.latency.value()
        s.preroll_ms = self.preroll.value()
        s.pre_pad_ms = self.pre_pad.value()
        s.post_pad_ms = self.post_pad.value()
        s.speeds = speeds
        if s.speed not in speeds:
            s.speed = speeds[0]
        s.tap_keys = keys
        s.end_key = end_key or "L"
        s.gap_policy = self.gap.currentData()
        s.onset_end = self.onset_end.currentData()
        s.compensate_stretch = self.comp.isChecked()
        s.metronome_bpm = self.bpm.value()
        self.accept()
