"""Main window: wires keys, playback and the Controller together."""
from __future__ import annotations

import threading
import time
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QKeySequence
from PySide6.QtWidgets import (QApplication, QFileDialog, QInputDialog, QLabel, QListWidget,
                               QListWidgetItem, QMainWindow, QMessageBox, QSplitter, QVBoxLayout,
                               QWidget)

from ..ass_doc import AssDocument, format_time
from ..audio import AudioError, AudioSource, Player, pick_output_device
from ..clock import SongClock
from ..controller import IMPORTED, PARTIAL, TAPPED_OK, UNTAPPED, WARNING, Action, Controller
from ..karaoke import merge_syllables, split_syllable
from ..settings import Settings
from ..timing import HOLD, KEEP, ONSET, TAPPED
from .dialogs import CalibrationDialog, SettingsDialog
from .wave import WaveView
from .widgets import CueWidget, LineView, cue_item, jp_font

STATUS_ICON = {UNTAPPED: "○", TAPPED_OK: "●", WARNING: "⚠", PARTIAL: "◐", IMPORTED: "◆"}
STATUS_COLOR = {UNTAPPED: "#9aa0aa", TAPPED_OK: "#4fa3ff", WARNING: "#ff5c5c", PARTIAL: "#ffcc33",
                IMPORTED: "#7fd1b9"}

HELP = """打軸鍵：{taps}（可多鍵交替／雙押）　結束鍵：{end}
空白：播放／暫停　←／→：倒退／前進 2 秒
R：重敲本行　Backspace：復原上一下　↑／↓：上一行／下一行　Ctrl+G：跳到第幾行
M：長按／起點模式　S：切換速度　P：保持音高　C：逐行／連續　T：行時間模式
E：延長選取行的行時間（敲出界時）　暫停時點目前那行字與字之間：切開／合併音節（紫線＝可合併的分界，灰線＝不能合併的分界，點＝可切開處）
V：波形／頻譜　暫停時拖曳波形上的邊界微調（Shift＋拖曳＝行頭／行尾）　點波形＝移動播放位置　Ctrl+Z：復原拖曳
Ctrl+S：存進度　Ctrl+E：匯出新 ASS　Ctrl+L：延遲校正　Ctrl+,：設定"""


class Bridge(QObject):
    """Delivers results from worker / audio threads to the GUI thread."""
    audio_loaded = Signal(object, object)       # AudioSource | None, error str | None
    variant_ready = Signal(float, bool)
    playback_finished = Signal()


def progress_path_for(ass: Path) -> Path:
    return ass.with_suffix(".karatap.json")


class MainWindow(QMainWindow):
    def __init__(self, settings: Settings):
        super().__init__()
        self.settings = settings
        self.ctrl: Controller | None = None
        self.ass_path: Path | None = None
        self.source: AudioSource | None = None
        self.bridge = Bridge()
        self.bridge.audio_loaded.connect(self._on_audio_loaded)
        self.bridge.variant_ready.connect(self._on_variant_ready)
        self.bridge.playback_finished.connect(self._on_finished)
        self.held: set[int] = set()
        self.flash_until = 0.0
        self.variant_key: tuple[float, bool] | None = None
        self._render_queue: list[tuple[float, bool]] = []
        self._render_busy = False
        self._last_statuses: list[str] = []
        self._frame = 0

        self.clock = SongClock()
        try:
            device, sr = pick_output_device()
            self.player: Player | None = Player(self.clock, device, sr)
            self.player.on_finished = self.bridge.playback_finished.emit
            self.sr = sr
        except Exception as e:      # no sound card: the window still opens
            self.player = None
            self.sr = 48000
            QMessageBox.warning(self, "音訊", f"無法開啟音訊輸出：{e}")

        self.setWindowTitle("karatap 卡拉OK打軸")
        self.cue = CueWidget()
        self.view = LineView()
        self.wave = WaveView()
        self.wave.set_mode(settings.wave_mode)
        self.wave.seek_requested.connect(self.seek_to)
        self.wave.changed.connect(self._after_drag)
        self.info = QLabel()
        self.info.setFont(jp_font(15, bold=False))
        self.info.setTextFormat(Qt.RichText)
        self.help = QLabel()
        self.help.setFont(jp_font(12, bold=False))
        self.help.setStyleSheet("color: #8a8f99")
        self.help.setWordWrap(True)
        self.list = QListWidget()
        self.list.setFont(jp_font(14, bold=False))
        self.list.setFocusPolicy(Qt.NoFocus)
        self.list.itemDoubleClicked.connect(lambda it: self.jump(self.list.row(it)))
        self.view.edit_requested.connect(self.edit_syllables)

        right = QWidget()
        lay = QVBoxLayout(right)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.info)
        lay.addWidget(self.cue, 3)
        lay.addWidget(self.view, 2)
        lay.addWidget(self.wave, 2)
        lay.addWidget(self.help)
        split = QSplitter()
        split.addWidget(self.list)
        split.addWidget(right)
        split.setStretchFactor(1, 1)
        split.setSizes([330, 1100])
        self.setCentralWidget(split)
        self.setStyleSheet("QMainWindow, QWidget { background: #101216; color: #e8e8ea; }"
                           "QListWidget::item:selected { background: #2a3140; }")
        self._build_menu()
        self._update_help()
        self.resize(1440, 860)

        QApplication.instance().installEventFilter(self)
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.PreciseTimer)
        self.timer.timeout.connect(self._tick)
        self.timer.start(16)
        self.autosave = QTimer(self)
        self.autosave.timeout.connect(self._autosave)
        self.autosave.start(10000)

    # -- menu --------------------------------------------------------------------
    def _build_menu(self) -> None:
        def act(menu, text, slot, shortcut=None):
            a = QAction(text, self)
            if shortcut:
                a.setShortcut(QKeySequence(shortcut))
                a.setShortcutContext(Qt.ApplicationShortcut)
            a.triggered.connect(slot)
            menu.addAction(a)
            return a

        m = self.menuBar().addMenu("檔案")
        act(m, "開啟 ASS…", self.open_dialog, "Ctrl+O")
        act(m, "存進度", self.save_progress, "Ctrl+S")
        act(m, "匯出新 ASS…", self.export, "Ctrl+E")
        m.addSeparator()
        act(m, "離開", self.close)
        m = self.menuBar().addMenu("打軸")
        act(m, "跳到第幾行…", self.ask_jump, "Ctrl+G")
        act(m, "延長選取行的行時間 (E)", lambda: self.extend_line(self.list.currentRow()))
        act(m, "清除選取行的打軸結果", self.clear_selected)
        act(m, "還原目前這行的音節切分", self.revert_split)
        m = self.menuBar().addMenu("工具")
        act(m, "延遲校正…", self.calibrate, "Ctrl+L")
        act(m, "切換波形／頻譜 (V)", self.toggle_wave_mode)
        act(m, "復原上一次拖曳", self.undo_drag, "Ctrl+Z")
        act(m, "設定…", self.open_settings, "Ctrl+,")

    def _update_help(self) -> None:
        self.help.setText(HELP.format(taps=" ".join(self.settings.tap_keys), end=self.settings.end_key))

    # -- opening -------------------------------------------------------------------
    def open_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "開啟 ASS", "", "ASS 字幕 (*.ass)")
        if path:
            self.open(Path(path))

    def open(self, ass: Path, media: Path | None = None) -> None:
        self._autosave()
        try:
            doc = AssDocument.load(ass)
        except OSError as e:
            QMessageBox.critical(self, "開啟失敗", str(e))
            return
        ctrl = Controller(doc, self.settings, progress_path_for(ass))
        if not ctrl.lines:
            QMessageBox.warning(self, "沒有可打軸的行", "找不到卡拉OK行（Effect 為 karaoke，或 Effect 空白且含 \\k 的 Dialogue）。")
            return
        try:
            ctrl.load_progress()
        except (OSError, ValueError, KeyError) as e:
            QMessageBox.warning(self, "進度檔", f"讀取進度失敗，從頭開始：{e}")
        imported = ctrl.import_file_timing()
        self.ctrl = ctrl
        self.ass_path = ass
        self.view.set_controller(ctrl)
        self.wave.set_controller(ctrl)
        if imported:
            self.statusBar().showMessage(f"有 {imported} 行沿用檔案裡原有的 \\k（◆），重敲或拖曳後才會改", 6000)
        self.setWindowTitle(f"karatap — {ass.name}")
        self._rebuild_list()
        for msg in ctrl.load_messages:
            QMessageBox.information(self, "進度檔", msg)
        media = media or self._guess_media(doc, ass)
        if media is None:
            path, _ = QFileDialog.getOpenFileName(self, "選擇影片或音檔", str(ass.parent),
                                                  "媒體 (*.mp4 *.mkv *.webm *.mov *.mp3 *.m4a *.flac *.wav *.ogg *.opus);;全部 (*)")
            media = Path(path) if path else None
        if media is not None:
            self.load_audio(media)
        self.jump(ctrl.cursor, play=False)

    def _guess_media(self, doc: AssDocument, ass: Path) -> Path | None:
        for key in ("Audio File", "Video File"):
            v = doc.section_value("Aegisub Project Garbage", key)
            if v and not v.startswith("?"):
                p = (ass.parent / v).resolve()
                if p.exists():
                    return p
        for ext in (".mp4", ".mkv", ".mp3", ".m4a", ".flac", ".wav"):
            p = ass.with_suffix(ext)
            if p.exists():
                return p
        return None

    def load_audio(self, media: Path) -> None:
        if self.player:
            self.player.pause()
        self.source = None
        self.variant_key = None
        self._render_queue.clear()
        self.cue.set_state([], False, "載入音訊中…")

        def work():
            try:
                src = AudioSource(media, self.sr)
                self.bridge.audio_loaded.emit(src, None)
            except (AudioError, OSError) as e:
                self.bridge.audio_loaded.emit(None, str(e))

        threading.Thread(target=work, daemon=True).start()

    def _on_audio_loaded(self, src, err) -> None:
        if err:
            QMessageBox.critical(self, "音訊載入失敗", err)
            return
        self.source = src
        self.wave.set_audio(src.base, src.sr)
        self.apply_variant()
        for sp in self.settings.speeds:     # pre-render the other speeds in the background
            self._queue_render(sp, self.settings.keep_pitch)

    # -- speed variants --------------------------------------------------------------
    def _queue_render(self, speed: float, keep: bool) -> None:
        if self.source is None or self.source.get(speed, keep) or (speed, keep) in self._render_queue:
            return
        self._render_queue.append((speed, keep))
        self._pump_render()

    def _pump_render(self) -> None:
        if self._render_busy or not self._render_queue:
            return
        speed, keep = self._render_queue.pop(0)
        src = self.source
        self._render_busy = True
        comp = self.settings.compensate_stretch

        def work():
            try:
                src.render(speed, keep, comp)
                self.bridge.variant_ready.emit(speed, keep)
            except AudioError as e:
                print("render failed:", e)
                self.bridge.variant_ready.emit(-1.0, keep)

        threading.Thread(target=work, daemon=True).start()

    def _on_variant_ready(self, speed: float, keep: bool) -> None:
        self._render_busy = False
        if speed > 0 and (speed, keep) == (self.settings.speed, self.settings.keep_pitch):
            self.apply_variant()
        self._pump_render()

    def apply_variant(self) -> None:
        """Switch playback to the current speed/pitch setting (rendering it first if needed)."""
        if self.source is None or self.player is None:
            return
        s = self.settings
        v = self.source.get(s.speed, s.keep_pitch)
        if v is None:
            self._render_queue = [k for k in self._render_queue if k != (s.speed, s.keep_pitch)]
            self._render_queue.insert(0, (s.speed, s.keep_pitch))
            self._pump_render()
            return
        pos = self.player.song_ms() if self.variant_key else self._pending_seek()
        playing = self.player.playing
        self.player.pause()
        self.player.set_buffer(v.pcm, v.speed, v.comp_ms, pos)
        self.variant_key = (s.speed, s.keep_pitch)
        if playing:
            self.player.play()

    def _pending_seek(self) -> float:
        return self.ctrl.preroll_target(self.ctrl.current_line()) if self.ctrl else 0.0

    # -- key handling ------------------------------------------------------------------
    def eventFilter(self, obj, ev) -> bool:
        et = ev.type()
        if et not in (QEvent.KeyPress, QEvent.KeyRelease):
            if et == QEvent.ApplicationDeactivate:
                self.held.clear()
            return False
        if QApplication.activeWindow() is not self or QApplication.activeModalWidget():
            return False
        now = time.perf_counter()
        key = ev.key()
        mods = ev.modifiers() & (Qt.ControlModifier | Qt.AltModifier)
        if mods:
            return False            # menu shortcuts
        tap_keys = {ord(c) for c in self.settings.tap_keys}
        if key in tap_keys:
            if not ev.isAutoRepeat():
                self._tap(key, et == QEvent.KeyPress, now)
            return True
        if et != QEvent.KeyPress:
            return False
        if key == ord(self.settings.end_key):
            if not ev.isAutoRepeat():
                self._end_key(now)
            return True
        handler = {
            Qt.Key_Space: self.toggle_play,
            Qt.Key_R: lambda: self.jump(self.ctrl.current_line()) if self.ctrl else None,
            Qt.Key_Backspace: self.undo,
            Qt.Key_Up: lambda: self.jump(self.ctrl.current_line() - 1) if self.ctrl else None,
            Qt.Key_Down: lambda: self.jump(self.ctrl.current_line() + 1) if self.ctrl else None,
            Qt.Key_Left: lambda: self.nudge(-2000),
            Qt.Key_Right: lambda: self.nudge(2000),
            Qt.Key_M: self.toggle_mode,
            Qt.Key_S: self.cycle_speed,
            Qt.Key_P: self.toggle_pitch,
            Qt.Key_C: self.toggle_continuous,
            Qt.Key_T: self.toggle_line_timing,
            Qt.Key_E: lambda: self.extend_line(self.list.currentRow()),
            Qt.Key_V: self.toggle_wave_mode,
        }.get(key)
        if handler is None:
            return False
        if not ev.isAutoRepeat() or key in (Qt.Key_Left, Qt.Key_Right):
            handler()
        return True

    def _song_ms_of(self, perf: float) -> float:
        return self.clock.song_ms_at(perf, latency_ms=self.settings.latency_ms)

    def _tap(self, key: int, press: bool, now: float) -> None:
        if not self.ctrl or not self.player or not self.player.playing:
            if press:
                self.statusBar().showMessage("播放中才能敲（空白鍵播放）", 2000)
            return
        t = self._song_ms_of(now)
        if press:
            self.held.add(key)
            self.flash_until = now + 0.09
            done = self.ctrl.down(key, t)
        else:
            self.held.discard(key)
            done = self.ctrl.up(key, t)
        self._after_done(done)

    def _end_key(self, now: float) -> None:
        if self.ctrl and self.player and self.player.playing:
            self._after_done(self.ctrl.end_key(self._song_ms_of(now)))

    def _after_done(self, done: list[int]) -> None:
        if not done:
            return
        self._refresh_list()
        self._autosave(force=True)
        for li in done:
            timing = self.ctrl.timing_for(li)
            if timing and timing.warnings:
                self.statusBar().showMessage(self._oob_text(li, timing.warnings), 6000)
            self._handle(self.ctrl.after_line_done(li, self.player.song_ms()))

    def _oob_text(self, li: int, warnings: list[str]) -> str:
        r = self.ctrl.results[li]
        parts = []
        if "early" in warnings:
            parts.append(f"早於行開始 {(self.ctrl.line_start(li) - r.bounds[0][0]) / 1000:.2f}s")
        if "late" in warnings:
            parts.append(f"晚於行結束 {(r.bounds[-1][1] - self.ctrl.line_end(li)) / 1000:.2f}s")
        return f"⚠ 第 {li + 1} 行敲出界：" + "，".join(parts)

    def _handle(self, action: Action | None) -> None:
        if action is None or self.player is None:
            return
        if action.kind == "seek":
            self.player.seek(action.ms)
        elif action.kind == "ask_oob":
            self.player.pause()
            li = action.line
            box = QMessageBox(self)
            box.setWindowTitle("敲出界")
            box.setText(self._oob_text(li, self.ctrl.timing_for(li).warnings))
            ext = box.addButton("延長行時間", QMessageBox.AcceptRole)
            redo = box.addButton("重敲", QMessageBox.DestructiveRole)
            box.addButton("先保留（夾在行內）", QMessageBox.RejectRole)
            box.exec()
            if box.clickedButton() is redo:
                self.jump(li, play=True)
                return
            if box.clickedButton() is ext:
                self.ctrl.extend_line(li)
                self._refresh_list()
            if li + 1 < len(self.ctrl.lines):
                self.player.seek(self.ctrl.preroll_target(li + 1))
            self.player.play()

    # -- commands ------------------------------------------------------------------------
    def toggle_play(self) -> None:
        if not self.player or self.source is None:
            return
        if self.player.playing:
            self.player.pause()
        else:
            if self.variant_key is None:
                self.apply_variant()
            self.player.play()

    def jump(self, li: int, play: bool | None = None) -> None:
        if not self.ctrl:
            return
        li = max(0, min(li, len(self.ctrl.lines) - 1))
        self.ctrl.start_take(li)
        if self.player and self.variant_key:
            self.player.seek(self.ctrl.preroll_target(li))
            if play:
                self.player.play()
        self.list.setCurrentRow(li)
        self._refresh_list()

    def ask_jump(self) -> None:
        if not self.ctrl:
            return
        n, ok = QInputDialog.getInt(self, "跳到第幾行", "行號：", self.ctrl.current_line() + 1,
                                    1, len(self.ctrl.lines))
        if ok:
            self.jump(n - 1)

    def nudge(self, ms: float) -> None:
        if self.player and self.variant_key:
            self.player.seek(max(0.0, self.player.song_ms() + ms))

    def undo(self) -> None:
        if not self.ctrl:
            return
        rewind = self.ctrl.undo()
        if rewind is None:
            self.statusBar().showMessage("沒有可以復原的敲擊", 2000)
            return
        if self.player:
            self.player.seek(rewind)
        self._refresh_list()

    def toggle_mode(self) -> None:
        self.settings.mode = ONSET if self.settings.mode == HOLD else HOLD
        self._restart_take("模式")

    def _restart_take(self, what: str) -> None:
        if self.ctrl:
            self.ctrl.start_take(self.ctrl.current_line())
            self.statusBar().showMessage(f"{what}已切換，本行從頭開始敲", 3000)
        self.settings.save()

    def cycle_speed(self) -> None:
        sp = self.settings.speeds
        i = sp.index(self.settings.speed) if self.settings.speed in sp else -1
        self.settings.speed = sp[(i + 1) % len(sp)]
        self.settings.save()
        self.apply_variant()

    def toggle_pitch(self) -> None:
        self.settings.keep_pitch = not self.settings.keep_pitch
        self.settings.save()
        self.apply_variant()

    def toggle_continuous(self) -> None:
        self.settings.continuous = not self.settings.continuous
        self.settings.save()

    def toggle_line_timing(self) -> None:
        self.settings.line_timing = TAPPED if self.settings.line_timing == KEEP else KEEP
        self.settings.save()
        self._refresh_list()

    def extend_line(self, li: int) -> None:
        if not self.ctrl or li < 0:
            return
        if self.ctrl.status(li) != WARNING:
            self.statusBar().showMessage("選取的行沒有敲出界", 2000)
            return
        self.ctrl.extend_line(li)
        self._refresh_list()
        self._autosave(force=True)

    def _confirm_clear(self, li: int) -> bool:
        if li not in self.ctrl.results:
            return True
        return QMessageBox.question(
            self, "改變音節切分",
            f"第 {li + 1} 行已經打好了，改變切分會清掉這行的打軸結果。要繼續嗎？") == QMessageBox.Yes

    def edit_syllables(self, li: int, kind: str, syl: int, at: int) -> None:
        if not self.ctrl or (self.player and self.player.playing):
            return
        kara = self.ctrl.lines[li].kara
        new = split_syllable(kara, syl, at) if kind == "split" else merge_syllables(kara, syl)
        if new is None:
            self.statusBar().showMessage(
                "這裡不能合併：振假名的語法表達不了（例如漢字＋一般文字、接續拍＋下一個漢字）", 4000)
            return
        if not self._confirm_clear(li):
            return
        self.ctrl.edit_line(li, new)
        self._last_statuses = []
        self._refresh_list()
        self._autosave(force=True)

    def revert_split(self) -> None:
        if not self.ctrl:
            return
        li = self.ctrl.current_line()
        if not self.ctrl.lines[li].edited:
            self.statusBar().showMessage("這行的音節切分沒有改過", 2000)
            return
        if self._confirm_clear(li):
            self.ctrl.revert_line(li)
            self._last_statuses = []
            self._refresh_list()
            self._autosave(force=True)

    def seek_to(self, ms: float) -> None:
        if self.player and self.variant_key:
            self.player.seek(ms)

    def _after_drag(self, li: int) -> None:
        self._last_statuses = []
        self._refresh_list()
        self._autosave(force=True)

    def undo_drag(self) -> None:
        if not self.ctrl:
            return
        li = self.ctrl.undo_drag()
        if li is None:
            self.statusBar().showMessage("沒有可以復原的拖曳", 2000)
            return
        self.statusBar().showMessage(f"已復原第 {li + 1} 行的拖曳", 2000)
        self._after_drag(li)

    def toggle_wave_mode(self) -> None:
        self.settings.wave_mode = "spec" if self.settings.wave_mode == "wave" else "wave"
        self.settings.save()
        self.wave.set_mode(self.settings.wave_mode)

    def clear_selected(self) -> None:
        li = self.list.currentRow()
        if self.ctrl and li >= 0:
            self.ctrl.clear_line(li)
            self._refresh_list()
            self._autosave(force=True)

    def calibrate(self) -> None:
        if not self.player:
            return
        pos = self.player.song_ms()
        self.player.pause()
        dlg = CalibrationDialog(self.player, self.settings, self)
        ok = dlg.exec()
        self.player.pause()
        if ok and dlg.result is not None:
            self.settings.latency_ms = round(dlg.result, 1)
            self.settings.save()
            self.statusBar().showMessage(f"延遲已設為 {self.settings.latency_ms:+.1f} ms", 4000)
        self.variant_key = None
        if self.source:
            v = self.source.get(self.settings.speed, self.settings.keep_pitch) or self.source.get(1.0, True)
            self.player.set_buffer(v.pcm, v.speed, v.comp_ms, pos)
            self.variant_key = (self.settings.speed, self.settings.keep_pitch)
            if v.speed != self.settings.speed:
                self.apply_variant()

    def open_settings(self) -> None:
        old = (self.settings.compensate_stretch, self.settings.onset_end, tuple(self.settings.speeds))
        if SettingsDialog(self.settings, self).exec():
            self.settings.save()
            self._update_help()
            if self.source and old[0] != self.settings.compensate_stretch:
                self.source.forget_variants()
                self.variant_key = None
            if old[1] != self.settings.onset_end and self.ctrl:
                self.ctrl.start_take(self.ctrl.current_line())
            self.apply_variant()
            self._refresh_list()

    # -- saving ----------------------------------------------------------------------------
    def _autosave(self, force: bool = False) -> None:
        if self.ctrl and (force or self.ctrl.dirty):
            try:
                self.ctrl.save_progress()
            except OSError as e:
                self.statusBar().showMessage(f"自動存檔失敗：{e}", 5000)

    def save_progress(self) -> None:
        self._autosave(force=True)
        self.statusBar().showMessage("進度已儲存", 2000)

    def export(self) -> None:
        if not self.ctrl or not self.ass_path:
            return
        default = self.ass_path.with_name(self.ass_path.stem + ".timed.ass")
        path, _ = QFileDialog.getSaveFileName(self, "匯出新 ASS", str(default), "ASS 字幕 (*.ass)")
        if not path:
            return
        out = Path(path)
        if out.resolve() == self.ass_path.resolve():
            QMessageBox.warning(self, "匯出", "不能覆蓋原始 ASS，請換一個檔名。")
            return
        statuses = [self.ctrl.status(i) for i in range(len(self.ctrl.lines))]
        out.write_bytes(self.ctrl.export_bytes())
        QMessageBox.information(
            self, "匯出完成",
            f"已寫入 {out.name}\n已敲 {statuses.count(TAPPED_OK) + statuses.count(WARNING)} 行"
            f"（其中 {statuses.count(WARNING)} 行出界被夾在行內），未敲 {statuses.count(UNTAPPED)} 行（保持原樣）。")

    def closeEvent(self, ev) -> None:
        self._autosave(force=True)
        self.settings.save()
        if self.player:
            self.player.close()
        super().closeEvent(ev)

    def _on_finished(self) -> None:
        self.statusBar().showMessage("播放結束", 3000)

    # -- display -----------------------------------------------------------------------
    def _rebuild_list(self) -> None:
        self.list.clear()
        self._last_statuses = []
        for line in self.ctrl.lines:
            self.list.addItem(QListWidgetItem())
        self._refresh_list()

    def _refresh_list(self) -> None:
        if not self.ctrl:
            return
        statuses = [self.ctrl.status(i) for i in range(len(self.ctrl.lines))]
        for i, (line, st) in enumerate(zip(self.ctrl.lines, statuses)):
            if i < len(self._last_statuses) and self._last_statuses[i] == st:
                continue
            plain = "".join(s.text if s.is_gap else f.base
                            for f, s in zip(line.furi, line.kara.syls) if not f.continuation)
            it = self.list.item(i)
            it.setText(f"{i + 1:>3} {STATUS_ICON[st]} {format_time(line.event.start_ms)}  {plain}")
            it.setForeground(QColor(STATUS_COLOR[st]))
        self._last_statuses = statuses

    def _tick(self) -> None:
        now = time.perf_counter()
        playing = bool(self.player and self.player.playing)
        song = self.clock.song_ms_at(now) if self.player else 0.0
        c = self.ctrl
        if c and playing:
            self._handle(c.tick(song, bool(self.held)))
        s = self.settings
        speed_txt = f"{s.speed:g}x" + ("（保持音高）" if s.keep_pitch else "（變調）")
        if self.source and self.source.get(s.speed, s.keep_pitch) is None:
            speed_txt += " <span style='color:#ffcc33'>準備中…</span>"
        mode = "長按" if s.mode == HOLD else "起點"
        timing = "保留行時間" if s.line_timing == KEEP else "由打軸決定行時間"
        flow = "連續" if s.continuous else "逐行"
        state = "▶" if playing else "⏸"
        self.info.setText(
            f"{state} <b>{format_time(song)}</b>　模式：<b>{mode}</b>　速度：<b>{speed_txt}</b>　"
            f"{flow}　{timing}　延遲 {s.latency_ms:+.0f} ms")
        if not c:
            self.cue.set_state([], False, "Ctrl+O 開啟 ASS")
            return
        up = c.upcoming(4)
        pressed = (bool(self.held) and s.mode == HOLD) or now < self.flash_until
        if self.source is None:
            msg = "載入音訊中…" if self.player else "沒有音訊輸出"
        elif not up:
            msg = "這一輪敲完了（↑／↓ 換行，R 重敲）"
        else:
            msg = ""
        items = [cue_item(c.lines[li], si) for li, si in up]
        self.cue.set_state(items, pressed, msg)
        self.view.edit_enabled = not playing
        self.view.set_time(song)
        self.wave.set_state(song, editable=not playing)
        cur = c.current_line()
        if self.list.currentRow() != cur and not self.list.underMouse():
            self.list.setCurrentRow(cur)
        self._frame += 1
        if self._frame % 10 == 0:       # statuses only change on taps; this catches partial lines
            self._refresh_list()
