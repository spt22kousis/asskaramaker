"""New project: media + template ASS -> lyrics -> readings check -> a new ASS."""
from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (QApplication, QDialog, QFileDialog, QFormLayout, QHBoxLayout,
                               QInputDialog, QLabel, QLineEdit, QMenu, QMessageBox, QPlainTextEdit,
                               QPushButton, QStackedWidget, QVBoxLayout, QWidget)

from ..ass_doc import AssDocument
from ..controller import Controller, progress_path_for
from ..lyrics import (LLM_READINGS_PROMPT, LyricLine, fetch_url, has_taps, make_line, parse_any,
                      parse_text, plain_text, to_editor_text, to_kara_text)
from ..project import (EVEN_STYLES, ODD_STYLES, SINGLE_STYLES, default_template, is_template_line,
                       new_project_bytes, pick_style)
from ..settings import Settings
from .lyrics_view import LyricsView, counts, option_label
from .widgets import ui_font

MEDIA_FILTER = "媒體 (*.mp4 *.mkv *.webm *.mov *.mp3 *.m4a *.flac *.wav *.ogg *.opus);;全部 (*)"


def segs_to_editor(lines) -> str:
    return "\n".join("".join(f"[{s.base}|{s.ruby}]" if s.ruby else s.base for s in segs)
                     for segs in lines)


class _Fetcher(QObject):
    done = Signal(object, object)        # html str | None, error str | None


def _hint(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setWordWrap(True)
    lab.setStyleSheet("color: #8a8f99")
    return lab


class NewProjectDialog(QDialog):
    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("新專案")
        self.settings = settings
        self.template: AssDocument | None = None
        self.lines: list[LyricLine] = []
        self.created: tuple[Path, Path] | None = None       # (ass, media)
        self.fetcher = _Fetcher()
        self.fetcher.done.connect(self._fetched)

        self.pages = QStackedWidget()
        self.pages.addWidget(self._files_page())
        self.pages.addWidget(self._source_page())
        self.pages.addWidget(self._check_page())
        self.back_btn = QPushButton("上一步")
        self.back_btn.clicked.connect(lambda: self._go(self.pages.currentIndex() - 1))
        self.next_btn = QPushButton("下一步")
        self.next_btn.clicked.connect(self._next)
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        self.step = QLabel()
        nav = QHBoxLayout()
        nav.addWidget(self.step)
        nav.addStretch(1)
        for b in (cancel, self.back_btn, self.next_btn):
            nav.addWidget(b)
        lay = QVBoxLayout(self)
        lay.addWidget(self.pages, 1)
        lay.addLayout(nav)
        self.resize(1000, 720)
        self._go(0)

    # -- page 1: files ------------------------------------------------------------------
    def _files_page(self) -> QWidget:
        w = QWidget()
        self.media_edit = QLineEdit()
        self.template_edit = QLineEdit(self.settings.last_template)
        self.template_edit.setPlaceholderText("空白＝內建預設樣板")
        self.out_edit = QLineEdit()
        self.template_info = _hint("")

        def row(edit: QLineEdit, browse) -> QHBoxLayout:
            h = QHBoxLayout()
            h.addWidget(edit, 1)
            b = QPushButton("瀏覽…")
            b.clicked.connect(browse)
            h.addWidget(b)
            return h

        form = QFormLayout()
        form.addRow("影片或音檔", row(self.media_edit, self._browse_media))
        form.addRow("樣板 ASS", row(self.template_edit, self._browse_template))
        form.addRow("", self.template_info)
        form.addRow("新的 ASS 存到", row(self.out_edit, self._browse_out))
        lay = QVBoxLayout(w)
        lay.addWidget(_hint("樣板 ASS：拿一首做好的歌的 ASS，新檔案會沿用它的樣式和 template／code 行"
                            "（舊歌的歌詞和特效行不會帶過來）。樣板檔本身不會被改動。空白就用內建的預設樣板。"))
        lay.addLayout(form)
        lay.addStretch(1)
        self.template_edit.editingFinished.connect(self._load_template)
        self._load_template()
        return w

    def _browse_media(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "選擇影片或音檔", self.settings.last_dir, MEDIA_FILTER)
        if not path:
            return
        media = Path(path)
        self.media_edit.setText(str(media))
        self.settings.last_dir = str(media.parent)
        if not self.out_edit.text():
            out = media.with_suffix(".ass")
            n = 2
            while out.exists():
                out = media.with_name(f"{media.stem} ({n}).ass")
                n += 1
            self.out_edit.setText(str(out))

    def _browse_template(self) -> None:
        start = self.template_edit.text() or self.settings.last_dir
        path, _ = QFileDialog.getOpenFileName(self, "選擇樣板 ASS", start, "ASS 字幕 (*.ass)")
        if path:
            self.template_edit.setText(path)
            self._load_template()

    def _browse_out(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "新的 ASS 存到", self.out_edit.text(), "ASS 字幕 (*.ass)",
                                              options=QFileDialog.DontConfirmOverwrite)
        if path:
            self.out_edit.setText(path)

    def _load_template(self) -> None:
        self.template = None
        text = self.template_edit.text().strip()
        path = Path(text)
        if text and not path.is_file():
            self.template_info.setText("")
            return
        try:
            doc = AssDocument.load(path) if text else default_template()
        except OSError as e:
            self.template_info.setText(f"讀不到：{e}")
            return
        styles = doc.style_names()
        n_tpl = sum(is_template_line(e) for e in doc.events)
        if not styles:
            self.template_info.setText("這個檔案裡沒有樣式，不能當樣板。")
            return
        self.template = doc
        self.template_info.setText(("" if text else "內建預設樣板　｜　")
                                   + f"樣式：{'、'.join(styles)}　｜　template／code 行：{n_tpl} 行")

    def _paths(self) -> tuple[Path, Path]:
        """(media, new ASS) as absolute paths."""
        return (Path(self.media_edit.text().strip()).absolute(),
                Path(self.out_edit.text().strip()).absolute())

    def _check_files(self) -> str | None:
        media, out = self._paths()
        if not media.is_file():
            return "請選擇影片或音檔。"
        self._load_template()
        if self.template is None:
            return "請選擇有樣式的樣板 ASS。"
        if out.suffix.lower() != ".ass":
            return "新檔案的副檔名要是 .ass。"
        if out.exists():
            return f"{out.name} 已經存在，請換一個檔名（不會覆蓋現有的檔案）。"
        if not out.parent.is_dir():
            return "存檔的資料夾不存在。"
        if self.template.path and out.resolve() == self.template.path.resolve():
            return "不能存成樣板檔本身。"
        return None

    # -- page 2: lyrics source ------------------------------------------------------------
    def _source_page(self) -> QWidget:
        w = QWidget()
        paste = QPushButton("從剪貼簿貼上")
        paste.clicked.connect(self._paste_clipboard)
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("https://utaten.com/lyric/…　或萌娘百科的網址")
        fetch = QPushButton("抓取")
        fetch.clicked.connect(self._fetch)
        llm = QPushButton("複製 LLM 提示詞（歌詞沒有假名時）")
        llm.clicked.connect(self._copy_llm_prompt)
        self.source_edit = QPlainTextEdit()
        self.source_edit.setFont(ui_font(15))
        self.source_status = _hint("")

        top = QHBoxLayout()
        top.addWidget(paste)
        top.addWidget(QLabel("或網址："))
        top.addWidget(self.url_edit, 1)
        top.addWidget(fetch)
        lay = QVBoxLayout(w)
        lay.addWidget(_hint(
            "最簡單：在 Utaten 或萌娘百科的歌詞頁，用滑鼠選取整段歌詞按 Ctrl+C，再按「從剪貼簿貼上」。"
            "也可以貼網頁原始碼（F12 複製的 HTML）、之前 notebook 產生的 {\\k1} 歌詞行，或直接打字。"
            "下面的框框裡 [漢字|かな] 代表振假名，一行就是一句；可以直接在這裡修改。"))
        lay.addLayout(top)
        lay.addWidget(self.source_edit, 1)
        h = QHBoxLayout()
        h.addWidget(self.source_status, 1)
        h.addWidget(llm)
        lay.addLayout(h)
        return w

    def _show_source(self, lines, what: str) -> None:
        if not lines:
            self.source_status.setText(f"{what}裡找不到歌詞。")
            return
        self.source_edit.setPlainText(segs_to_editor(lines))
        n_ruby = sum(1 for segs in lines for s in segs if s.ruby)
        self.source_status.setText(f"{what}：{len(lines)} 行，{n_ruby} 個詞有假名。")

    def _paste_clipboard(self) -> None:
        md = QApplication.clipboard().mimeData()
        html = md.html() if md.hasHtml() else None
        text = md.text() if md.hasText() else ""
        self._show_source(parse_any(text, html), "剪貼簿")

    def _fetch(self) -> None:
        url = self.url_edit.text().strip()
        if not url:
            return
        self.source_status.setText("抓取中…")

        def work():
            try:
                self.fetcher.done.emit(fetch_url(url), None)
            except Exception as e:      # network errors of every kind end up here
                self.fetcher.done.emit(None, str(e))

        threading.Thread(target=work, daemon=True).start()

    def _fetched(self, html, err) -> None:
        if err:
            self.source_status.setText(f"抓取失敗：{err}　（可以改用選取歌詞後 Ctrl+C）")
            return
        self._show_source(parse_any(html), "網頁")

    def _copy_llm_prompt(self) -> None:
        lines = parse_any(self.source_edit.toPlainText())
        if not lines:
            self.source_status.setText("先把歌詞貼進框框。")
            return
        plain = "\n".join(plain_text(make_line(segs)) for segs in lines)
        QApplication.clipboard().setText(LLM_READINGS_PROMPT.format(lyrics=plain))
        self.source_status.setText("已複製提示詞。貼到 ChatGPT 等 LLM，再把它的回答整段貼回框框（取代原本的內容）。")

    # -- page 3: readings check -------------------------------------------------------------
    def _check_page(self) -> QWidget:
        w = QWidget()
        self.view = LyricsView()
        self.view.group_clicked.connect(self._group_menu)
        self.view.itemDoubleClicked.connect(lambda _: self._edit_line())
        self.check_status = QLabel()
        self.check_status.setFont(ui_font(14))
        buttons = QHBoxLayout()
        for text, slot in (("編輯這行…", self._edit_line), ("在下面新增一行…", self._add_line),
                           ("刪除這行", self._delete_line), ("和下一行合併", self._merge_line)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            buttons.addWidget(b)
        buttons.addStretch(1)
        lay = QVBoxLayout(w)
        lay.addWidget(_hint(
            "每個漢字會分到自己的讀音（一個字一個音節）。<b>黃底</b>＝有不只一種分法，請點一下確認；"
            "<b>灰底</b>＝分不開（熟字訓等），整組當一個音節；綠底線＝自動分好。點任何一個詞都可以換分法。"
            "雙擊一行可以直接修改文字，[漢字|かな] 是振假名。建立後就不能增刪行了，請在這裡改好。"))
        lay.addWidget(self.check_status)
        lay.addWidget(self.view, 1)
        lay.addLayout(buttons)
        return w

    def _update_check_status(self) -> None:
        check, whole = counts(self.lines)
        no_taps = sum(not has_taps(l) for l in self.lines)
        parts = [f"共 {len(self.lines)} 行"]
        parts.append(f"<span style='color:#ffcc33'>還有 {check} 處要確認</span>" if check else "沒有要確認的分法")
        if whole:
            parts.append(f"{whole} 處整組不拆")
        if no_taps:
            parts.append(f"<span style='color:#ff5c5c'>{no_taps} 行只有標點，建立時會略過</span>")
        self.check_status.setText("　｜　".join(parts))
        self.view.refresh()

    def _group_menu(self, li: int, gi: int, pos) -> None:
        g = self.lines[li].groups[gi]
        menu = QMenu(self)
        acts = []
        for k in range(len(g.dist.options)):
            a = menu.addAction(option_label(g, k))
            a.setCheckable(True)
            a.setChecked(k == g.choice)
            acts.append(a)
        menu.addSeparator()
        edit = menu.addAction("編輯這行…")
        chosen = menu.exec(pos)
        if chosen is edit:
            self._edit_line(li)
        elif chosen in acts:
            g.choice = acts.index(chosen)
            g.confirmed = True
            self._update_check_status()

    def _current(self) -> int:
        return self.view.currentRow()

    def _ask_text(self, title: str, text: str) -> str | None:
        new, ok = QInputDialog.getMultiLineText(
            self, title, "[漢字|かな] 是振假名；換行會拆成好幾行；全部清空＝刪除這行。", text)
        return new if ok else None

    def _edit_line(self, li: int | None = None) -> None:
        li = self._current() if li is None else li
        if not 0 <= li < len(self.lines):
            return
        new = self._ask_text("編輯這行", to_editor_text(self.lines[li]))
        if new is None:
            return
        old = self.lines[li]
        self.lines[li:li + 1] = [make_line(segs, previous=old) for segs in parse_text(new)]
        self.view.set_lines(self.lines, li)
        self._update_check_status()

    def _add_line(self) -> None:
        li = self._current() + 1
        new = self._ask_text("新增一行", "")
        if not new:
            return
        self.lines[li:li] = [make_line(segs) for segs in parse_text(new)]
        self.view.set_lines(self.lines, li)
        self._update_check_status()

    def _delete_line(self) -> None:
        li = self._current()
        if 0 <= li < len(self.lines):
            del self.lines[li]
            self.view.set_lines(self.lines, li)
            self._update_check_status()

    def _merge_line(self) -> None:
        li = self._current()
        if not 0 <= li < len(self.lines) - 1:
            return
        a, b = self.lines[li], self.lines[li + 1]
        merged = make_line(parse_text(to_editor_text(a) + " " + to_editor_text(b))[0],
                           previous=LyricLine(a.items + b.items))
        self.lines[li:li + 2] = [merged]
        self.view.set_lines(self.lines, li)
        self._update_check_status()

    # -- navigation --------------------------------------------------------------------------
    def _go(self, page: int) -> None:
        page = max(0, min(page, self.pages.count() - 1))
        self.pages.setCurrentIndex(page)
        self.back_btn.setEnabled(page > 0)
        self.next_btn.setText("建立專案" if page == self.pages.count() - 1 else "下一步")
        titles = ["1／3　選檔案", "2／3　貼歌詞", "3／3　檢查讀音"]
        self.step.setText(titles[page])

    def _next(self) -> None:
        page = self.pages.currentIndex()
        if page == 0:
            err = self._check_files()
            if err:
                QMessageBox.warning(self, "新專案", err)
                return
            self.settings.last_template = self.template_edit.text().strip()
        elif page == 1:
            segs = parse_any(self.source_edit.toPlainText())
            if not segs:
                QMessageBox.warning(self, "新專案", "框框裡沒有歌詞。")
                return
            old = {(g.base, g.ruby): g for l in self.lines for g in l.groups}
            self.lines = [make_line(s, previous=LyricLine(list(old.values()))) for s in segs]
            self.view.set_lines(self.lines)
            self._update_check_status()
        else:
            self._create()
            return
        self._go(page + 1)

    def _create(self) -> None:
        check, _ = counts(self.lines)
        if check and QMessageBox.question(
                self, "建立專案", f"還有 {check} 處分法沒有確認，要用預設的分法建立嗎？") != QMessageBox.Yes:
            return
        texts = [to_kara_text(l) for l in self.lines if has_taps(l)]
        if not texts:
            QMessageBox.warning(self, "建立專案", "沒有可以打軸的歌詞。")
            return
        err = self._check_files()
        if err:
            QMessageBox.warning(self, "建立專案", err)
            return
        media, out = self._paths()
        data = new_project_bytes(self.template, media, out, texts)
        try:
            with open(out, "xb") as f:         # never overwrite
                f.write(data)
            doc = AssDocument(data, out)
            ctrl = Controller(doc, self.settings, progress_path_for(out))
            styles = doc.style_names()
            p = ctrl.layout_params
            p.odd_style = pick_style(styles, ODD_STYLES)
            p.even_style = pick_style(styles, EVEN_STYLES + (p.odd_style,))
            p.single_style = p.one_style = pick_style(styles, SINGLE_STYLES + (p.odd_style,))
            ctrl.layout_on = True
            ctrl.save_progress()
        except OSError as e:
            QMessageBox.critical(self, "建立專案", f"寫入失敗：{e}")
            return
        self.settings.last_dir = str(out.parent)
        self.created = (out, media)
        self.accept()
