"""Tapping session: lines, takes, results, progress file and export.

UI-free: the GUI feeds it key events in song time and asks it what to show
and where to seek. A "take" is one uninterrupted run of tapping starting at
some line; it continues across lines until the user jumps, retaps or seeks.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import dataclass, field, replace
from pathlib import Path

from .ass_doc import AssDocument, Event, format_time
from .karaoke import Furi, KaraLine, furigana_for
from .settings import Settings
from .timing import KEEP, TAPPED, LineTiming, TapMachine, TimingOptions, compute_line, to_cs

UNTAPPED, TAPPED_OK, WARNING, PARTIAL, IMPORTED = "untapped", "tapped", "warning", "partial", "imported"
FROM_TAPS, FROM_FILE = "tap", "file"     # LineResult.source

LOOP_GRACE_MS = 1500     # line mode: replay the line if it ends this long without being finished
SKIP_GRACE_MS = 300      # continuous mode: skip a line nobody touched once it is this far past its end
IMPORT_MIN_FILL = 0.5    # a line's \k must cover this much of it to count as already timed
DRAG_UNDO_LIMIT = 100


@dataclass
class LineInfo:
    idx: int
    event: Event
    kara: KaraLine
    furi: list[Furi]
    taps: list[int]          # indices of tappable syllables

    @property
    def key(self) -> str:
        return str(self.event.line_no)

    @property
    def gaps(self) -> list[bool]:
        return [s.is_gap for s in self.kara.syls]

    @property
    def edited(self) -> bool:
        """Syllable split changed in the tool (export writes the new text)."""
        return self.kara.text != self.event.text


def make_line(idx: int, event: Event, text: str) -> LineInfo:
    k = KaraLine.parse(text)
    return LineInfo(idx, event, k, furigana_for(k), k.tappable())


@dataclass
class LineResult:
    bounds: list[tuple[float, float]]
    start_override: int | None = None    # ms; set by "extend line" or dragging the line edge
    end_override: int | None = None
    source: str = FROM_TAPS              # FROM_FILE: \k already in the ASS, untouched so far


def file_bounds(line: LineInfo) -> list[tuple[float, float]] | None:
    """Syllable (start, end) from the \\k values already in the ASS, or None if they look like placeholders."""
    start, end = line.event.start_ms, line.event.end_ms
    total = sum(s.value for s in line.kara.syls) * 10
    if end <= start or total < IMPORT_MIN_FILL * (end - start):
        return None
    t, out = float(start), []
    for s in line.kara.syls:
        a, t = t, t + s.value * 10
        if not s.is_gap:
            out.append((a, t))
    return out


@dataclass
class Handle:
    """A draggable boundary on the waveform. refs: ('s', j) / ('e', j) for syllable
    start/end, ('L0',) / ('L1',) for the line edges; a shared boundary has two refs."""
    ms: float
    lo: float
    hi: float
    refs: tuple


@dataclass
class Take:
    first_line: int
    targets: list[tuple[int, int]]       # (line idx, syllable idx)
    first_target: dict[int, int]         # line idx -> index of its first target
    machine: TapMachine
    committed: set[int] = field(default_factory=set)
    backup: dict[int, LineResult | None] = field(default_factory=dict)

    def line_targets(self, li: int) -> range:
        start = self.first_target[li]
        n = 0
        while start + n < len(self.targets) and self.targets[start + n][0] == li:
            n += 1
        return range(start, start + n)


@dataclass
class Action:
    kind: str            # 'seek', 'ask_oob'
    ms: float = 0.0
    line: int = -1


def _sha1(b: bytes) -> str:
    return hashlib.sha1(b).hexdigest()


class Controller:
    def __init__(self, doc: AssDocument, settings: Settings, progress_path: Path | None = None):
        self.doc = doc
        self.settings = settings
        self.progress_path = progress_path
        evs = sorted(doc.karaoke_events(), key=lambda e: (e.start_ms, e.line_no))
        self.lines: list[LineInfo] = []
        for e in evs:
            line = make_line(len(self.lines), e, e.text)
            if line.taps:
                self.lines.append(line)
        self.results: dict[int, LineResult] = {}
        self.cursor = 0
        self.take: Take | None = None
        self.dirty = False
        self.load_messages: list[str] = []
        self.drag_history: list[tuple[int, LineResult | None]] = []

    # -- options -----------------------------------------------------------------
    def timing_options(self) -> TimingOptions:
        s = self.settings
        return TimingOptions(s.line_timing, s.gap_policy, s.pre_pad_ms, s.post_pad_ms)

    def line_start(self, li: int) -> int:
        r = self.results.get(li)
        if r and r.start_override is not None:
            return r.start_override
        return self.lines[li].event.start_ms

    def line_end(self, li: int) -> int:
        r = self.results.get(li)
        if r and r.end_override is not None:
            return r.end_override
        return self.lines[li].event.end_ms

    def preroll_target(self, li: int) -> float:
        return max(0.0, self.line_start(li) - self.settings.preroll_ms)

    # -- results -----------------------------------------------------------------
    def timing_for(self, li: int) -> LineTiming | None:
        r = self.results.get(li)
        if not r:
            return None
        line = self.lines[li]
        return compute_line(line.gaps, r.bounds, self.line_start(li), self.line_end(li),
                            self.timing_options())

    def status(self, li: int) -> str:
        t = self.timing_for(li)
        if t is None:
            if self.take and li in self.take.first_target and li not in self.take.committed:
                m = self.take.machine
                if any(m.start[i] is not None for i in self.take.line_targets(li)):
                    return PARTIAL
            return UNTAPPED
        if t.warnings:
            return WARNING
        return IMPORTED if self.results[li].source == FROM_FILE else TAPPED_OK

    def import_file_timing(self) -> int:
        """Use \\k already in the ASS as the result of lines that have none. Returns how many."""
        n = 0
        for li, line in enumerate(self.lines):
            if li in self.results or line.edited:
                continue
            b = file_bounds(line)
            if b:
                self.results[li] = LineResult(b, source=FROM_FILE)
                n += 1
        return n

    def extend_line(self, li: int) -> None:
        """Grow the line's start/end to cover taps that fell outside it."""
        r = self.results.get(li)
        if not r:
            return
        first = to_cs(r.bounds[0][0]) * 10
        last = to_cs(r.bounds[-1][1]) * 10
        if first < self.line_start(li):
            r.start_override = first
        if last > self.line_end(li):
            r.end_override = last
        r.source = FROM_TAPS
        self.dirty = True

    # -- dragging boundaries on the waveform --------------------------------------------
    def handles(self, li: int) -> list[Handle]:
        r = self.results.get(li)
        if not r:
            return []
        b = r.bounds
        out: list[Handle] = []
        for j, (s, e) in enumerate(b):
            if j and abs(b[j - 1][1] - s) < 1.0:            # previous end == this start: one handle
                out.append(Handle(s, b[j - 1][0], e, (("e", j - 1), ("s", j))))
                continue
            if j:
                out.append(Handle(b[j - 1][1], b[j - 1][0], s, (("e", j - 1),)))
            out.append(Handle(s, b[j - 1][1] if j else 0.0, e, (("s", j),)))
        out.append(Handle(b[-1][1], b[-1][0], math.inf, (("e", len(b) - 1),)))
        if self.settings.line_timing == KEEP:
            out.append(Handle(self.line_start(li), 0.0, b[0][0], (("L0",),)))
            out.append(Handle(self.line_end(li), b[-1][1], math.inf, (("L1",),)))
        return out

    def begin_drag(self, li: int) -> None:
        """Remember line li's result so the coming drag can be undone."""
        r = self.results.get(li)
        self.drag_history.append((li, replace(r, bounds=list(r.bounds)) if r else None))
        del self.drag_history[:-DRAG_UNDO_LIMIT]

    def move_handle(self, li: int, h: Handle, ms: float) -> float:
        """Move a boundary (10 ms steps, kept between its neighbours). Returns where it landed."""
        r = self.results[li]
        ms = min(max(round(ms / 10) * 10, h.lo), h.hi)
        ev = self.lines[li].event
        for ref in h.refs:
            if ref[0] == "s":
                r.bounds[ref[1]] = (ms, r.bounds[ref[1]][1])
            elif ref[0] == "e":
                r.bounds[ref[1]] = (r.bounds[ref[1]][0], ms)
            elif ref[0] == "L0":
                r.start_override = None if ms == ev.start_ms else int(ms)
            else:
                r.end_override = None if ms == ev.end_ms else int(ms)
        r.source = FROM_TAPS
        self.dirty = True
        return ms

    def undo_drag(self) -> int | None:
        """Undo the last drag. Returns the line it changed."""
        if not self.drag_history:
            return None
        li, prev = self.drag_history.pop()
        if prev is None:
            self.results.pop(li, None)
        else:
            self.results[li] = prev
        self.dirty = True
        return li

    def clear_line(self, li: int) -> None:
        if self.results.pop(li, None) is not None:
            self.dirty = True

    def edit_line(self, li: int, text: str) -> bool:
        """Replace line li's karaoke text (new syllable split). Returns True if its taps were cleared."""
        line = self.lines[li]
        new = make_line(li, line.event, text)
        if not new.taps:
            raise ValueError("a line needs at least one syllable to tap")
        self.lines[li] = new
        cleared = self.results.pop(li, None) is not None
        self.start_take(li)
        self.dirty = True
        return cleared

    def revert_line(self, li: int) -> bool:
        """Back to the syllable split from the ASS file."""
        return self.edit_line(li, self.lines[li].event.text)

    # -- takes -------------------------------------------------------------------
    def start_take(self, li: int) -> None:
        li = max(0, min(li, len(self.lines) - 1))
        targets: list[tuple[int, int]] = []
        first: dict[int, int] = {}
        last_flags: list[bool] = []
        for line in self.lines[li:]:
            first[line.idx] = len(targets)
            for k, si in enumerate(line.taps):
                targets.append((line.idx, si))
                last_flags.append(k == len(line.taps) - 1)
        s = self.settings
        m = TapMachine(len(targets), s.mode, last_flags, s.onset_end)
        self.take = Take(li, targets, first, m)
        self.cursor = li

    def _ensure_take(self) -> Take:
        if self.take is None:
            self.start_take(self.cursor)
        return self.take

    def current_line(self) -> int:
        t = self.take
        if t is None:
            return self.cursor
        m = t.machine
        if m.next < len(t.targets):
            return t.targets[m.next][0]
        return t.targets[-1][0] if t.targets else self.cursor

    def upcoming(self, n: int) -> list[tuple[int, int]]:
        """Next `n` targets to press, as (line idx, syllable idx)."""
        t = self._ensure_take()
        return t.targets[t.machine.next:t.machine.next + n]

    def active_target(self) -> tuple[int, int] | None:
        t = self.take
        if t is None or t.machine.open_target is None:
            return None
        return t.targets[t.machine.open_target]

    def syllable_states(self, li: int) -> dict[int, str]:
        """syllable idx -> 'done' / 'active' / 'next' (others are pending)."""
        out: dict[int, str] = {}
        t = self.take
        if t and li in t.first_target:
            m = t.machine
            for ti in t.line_targets(li):
                si = t.targets[ti][1]
                if ti == m.open_target:
                    out[si] = "active"
                elif m.start[ti] is not None:
                    out[si] = "done"
                elif ti == m.next:
                    out[si] = "next"
            if out or li not in self.results:
                return out
        if li in self.results:
            return {si: "done" for si in self.lines[li].taps}
        return out

    # -- key events (times are song ms, already latency-corrected) ----------------
    def down(self, key, t: float) -> list[int]:
        take = self._ensure_take()
        if take.machine.down(key, t) is None:
            return []
        return self._sync()

    def up(self, key, t: float) -> list[int]:
        take = self._ensure_take()
        if take.machine.up(key, t) is None:
            return []
        return self._sync()

    def end_key(self, t: float) -> list[int]:
        take = self._ensure_take()
        if take.machine.end_key(t) is None:
            return []
        return self._sync()

    def undo(self) -> float | None:
        """Undo the last press. Returns the song ms to rewind to, or None."""
        take = self.take
        if take is None:
            return None
        r = take.machine.undo()
        if r is None:
            return None
        self._sync()
        return max(0.0, r[1] - self.settings.preroll_ms)

    def _sync(self) -> list[int]:
        """Commit lines the take finished; restore lines an undo reopened. Returns newly finished lines."""
        take = self.take
        m = take.machine
        done_lines = []
        for li in list(take.first_target):
            rng = take.line_targets(li)
            complete = all(m.is_done(i) for i in rng)
            if complete and li not in take.committed:
                take.backup.setdefault(li, self.results.get(li))
                old = self.results.get(li)
                self.results[li] = LineResult(
                    [(m.start[i], m.end[i]) for i in rng],
                    old.start_override if old else None,
                    old.end_override if old else None)
                take.committed.add(li)
                done_lines.append(li)
            elif not complete and li in take.committed:
                take.committed.discard(li)
                prev = take.backup.pop(li, None)
                if prev is None:
                    self.results.pop(li, None)
                else:
                    self.results[li] = prev
        self.cursor = self.current_line()
        self.dirty = True
        return done_lines

    def after_line_done(self, li: int, now_ms: float) -> Action | None:
        """What to do after line `li` was finished (line-by-line mode only)."""
        if self.settings.continuous:
            return None
        timing = self.timing_for(li)
        if timing and timing.warnings and self.settings.line_timing == KEEP:
            return Action("ask_oob", line=li)
        nxt = li + 1
        take = self.take
        if nxt >= len(self.lines) or take is None or nxt not in take.first_target:
            return None
        if any(take.machine.start[i] is not None for i in take.line_targets(nxt)):
            return None          # already tapping the next line
        target = self.preroll_target(nxt)
        if now_ms < target or now_ms > self.line_start(nxt) - 100:
            return Action("seek", target, nxt)
        return None

    def tick(self, now_ms: float, keys_held: bool) -> Action | None:
        """Called periodically while playing."""
        take = self._ensure_take()
        if keys_held:
            return None
        li = self.current_line()
        if li not in take.first_target or li in take.committed:
            return None
        m = take.machine
        touched = any(m.start[i] is not None for i in take.line_targets(li))
        if self.settings.continuous:
            if not touched and now_ms > self.line_end(li) + SKIP_GRACE_MS:
                nxt = li + 1
                if nxt in take.first_target:
                    m.skip_to(take.first_target[nxt])
                    self.cursor = nxt
            return None
        if now_ms > self.line_end(li) + LOOP_GRACE_MS:
            self.start_take(li)
            return Action("seek", self.preroll_target(li), li)
        return None

    # -- export ------------------------------------------------------------------
    def export_bytes(self) -> bytes:
        repl: dict[int, str] = {}
        for li, line in enumerate(self.lines):
            timing = self.timing_for(li)
            ev = line.event
            if timing is None:
                if line.edited:       # untapped, but its syllable split was changed
                    repl[ev.line_no] = ev.with_fields(text=line.kara.text).render()
                continue
            r = self.results[li]
            if r.source == FROM_FILE:  # \k from the file, untouched: keep the line as it is
                continue
            changes = {"text": line.kara.rebuild(timing.values, timing.inserts)}
            if (self.settings.line_timing == TAPPED or r.start_override is not None
                    or r.end_override is not None):
                if timing.start_cs * 10 != ev.start_ms:
                    changes["start"] = format_time(timing.start_cs * 10, like=ev.get("start"))
                if timing.end_cs * 10 != ev.end_ms:
                    changes["end"] = format_time(timing.end_cs * 10, like=ev.get("end"))
            new_ev = ev.with_fields(**changes)
            line_text = new_ev.render()
            if line_text != self.doc.lines[ev.line_no]:
                repl[ev.line_no] = line_text
        return self.doc.to_bytes(repl)

    # -- progress file ---------------------------------------------------------------
    def progress_data(self) -> dict:
        return {
            "version": 1,
            "ass_sha1": _sha1(self.doc.raw),
            "cursor": self.cursor,
            "lines": {
                self.lines[li].key: {
                    "text": self.lines[li].event.text,
                    "bounds": [list(b) for b in r.bounds],
                    "start_override": r.start_override,
                    "end_override": r.end_override,
                }
                for li, r in sorted(self.results.items()) if r.source != FROM_FILE
            },
            "edits": {
                l.key: {"text": l.event.text, "edited": l.kara.text}
                for l in self.lines if l.edited
            },
        }

    def save_progress(self) -> None:
        if not self.progress_path:
            return
        tmp = self.progress_path.with_suffix(self.progress_path.suffix + ".tmp")
        tmp.write_text(json.dumps(self.progress_data(), ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.progress_path)
        self.dirty = False

    def load_progress(self) -> None:
        self.load_messages = []
        if not self.progress_path or not self.progress_path.exists():
            return
        data = json.loads(self.progress_path.read_text(encoding="utf-8"))
        by_key = {l.key: l for l in self.lines}
        by_text: dict[str, list[LineInfo]] = {}
        for l in self.lines:
            by_text.setdefault(l.event.text, []).append(l)

        def find(key: str, text: str) -> LineInfo | None:
            line = by_key.get(key)
            if line is None or line.event.text != text:
                cands = by_text.get(text, [])
                line = cands[0] if len(cands) == 1 else None
            return line

        dropped = 0
        for key, rec in data.get("edits", {}).items():     # syllable splits first: taps depend on them
            line = find(key, rec["text"])
            if line is None:
                dropped += 1
                continue
            self.lines[line.idx] = make_line(line.idx, line.event, rec["edited"])
        for key, rec in data.get("lines", {}).items():
            line = find(key, rec["text"])
            line = self.lines[line.idx] if line else None
            if line is None or len(rec["bounds"]) != len(line.taps):
                dropped += 1
                continue
            self.results[line.idx] = LineResult([tuple(b) for b in rec["bounds"]],
                                                rec.get("start_override"), rec.get("end_override"))
        self.cursor = min(max(0, int(data.get("cursor", 0))), max(0, len(self.lines) - 1))
        if data.get("ass_sha1") != _sha1(self.doc.raw):
            self.load_messages.append("ASS 檔在上次存檔後有變動，已依行內容比對進度。")
        if dropped:
            self.load_messages.append(f"有 {dropped} 行的進度對不上目前的 ASS，已略過。")
