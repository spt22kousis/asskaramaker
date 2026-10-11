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
from dataclasses import asdict, dataclass, field, fields, replace
from pathlib import Path

from .ass_doc import AssDocument, Event, format_time
from .karaoke import Furi, KaraLine, furigana_for
from .layout import LayoutParams, Placement, place
from .settings import Settings
from .timing import KEEP, TAPPED, LineTiming, TapMachine, TimingOptions, compute_line, to_cs

UNTAPPED, TAPPED_OK, WARNING, PARTIAL, IMPORTED = "untapped", "tapped", "warning", "partial", "imported"
FROM_TAPS, FROM_FILE = "tap", "file"     # LineResult.source

LOOP_GRACE_MS = 1500     # line mode: replay the line if it ends this long without being finished
SKIP_GRACE_MS = 300      # continuous mode: skip a line nobody touched once it is this far past its end
IMPORT_MIN_FILL = 0.5    # a line's \k must cover this much of it to count as already timed
DRAG_UNDO_LIMIT = 100
MIN_LINE_MS = 100        # a dragged line keeps at least this length


@dataclass
class LineInfo:
    idx: int
    event: Event
    kara: KaraLine
    furi: list[Furi]
    taps: list[int]          # indices of tappable syllables
    added_key: str | None = None   # set for lines added in the tool (no event in the file)

    @property
    def key(self) -> str:
        return self.added_key or str(self.event.line_no)

    @property
    def added(self) -> bool:
        return self.added_key is not None

    @property
    def gaps(self) -> list[bool]:
        return [s.is_gap for s in self.kara.syls]

    @property
    def edited(self) -> bool:
        """Syllable split changed in the tool (export writes the new text)."""
        return self.kara.text != self.event.text

    @property
    def plain(self) -> str:
        """The line's text without furigana."""
        return "".join(s.text if s.is_gap else f.base
                       for f, s in zip(self.furi, self.kara.syls) if not f.continuation)


def make_line(idx: int, event: Event, text: str, added_key: str | None = None) -> LineInfo:
    k = KaraLine.parse(text)
    return LineInfo(idx, event, k, furigana_for(k), k.tappable(), added_key)


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
class LineTake:
    """Tapping whole lines (one press or hold per line), from line `first` on."""
    first: int
    machine: TapMachine
    committed: set[int] = field(default_factory=set)
    backup: dict[int, tuple[float, float] | None] = field(default_factory=dict)


@dataclass
class Action:
    kind: str            # 'seek', 'ask_oob'
    ms: float = 0.0
    line: int = -1


def progress_path_for(ass: Path) -> Path:
    return ass.with_suffix(".karatap.json")


def _sha1(b: bytes) -> str:
    return hashlib.sha1(b).hexdigest()


class Controller:
    def __init__(self, doc: AssDocument, settings: Settings, progress_path: Path | None = None):
        self.doc = doc
        self.settings = settings
        self.progress_path = progress_path
        evs = sorted(doc.karaoke_events(), key=lambda e: (e.start_ms, e.line_no))
        self._first_event = evs[0] if evs else None
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
        self.drag_history: list[tuple[int, LineResult | None, tuple | None]] = []
        # Line timing: sung (start, end) per line from line tapping.
        self.line_taps: dict[int, tuple[float, float]] = {}
        self.line_take: LineTake | None = None
        self.line_cursor = 0
        # Odd/Even layout: when on, line times and styles come from layout.place().
        self.layout_on = False
        self.layout_params = LayoutParams()
        self.layout_overrides: dict[int, tuple[int | None, int | None]] = {}   # manual start/end
        # Lines removed in the tool (key -> original text) and the next key for an added line.
        self.deleted: dict[str, str] = {}
        self.next_added = 1
        self.line_drag_history: list[tuple[int, tuple[float, float] | None]] = []

    # -- options -----------------------------------------------------------------
    def timing_options(self) -> TimingOptions:
        s = self.settings
        line_timing = KEEP if self.layout_on else s.line_timing   # the layout already decided the line times
        return TimingOptions(line_timing, s.gap_policy, s.pre_pad_ms, s.post_pad_ms)

    def line_start(self, li: int) -> float:
        if self.layout_on:
            return self._layout_span(li)[0]
        r = self.results.get(li)
        if r and r.start_override is not None:
            return r.start_override
        if self.lines[li].added:
            return self._added_span(li)[0]
        return self.lines[li].event.start_ms

    def line_end(self, li: int) -> float:
        if self.layout_on:
            return self._layout_span(li)[1]
        r = self.results.get(li)
        if r and r.end_override is not None:
            return r.end_override
        if self.lines[li].added:
            return self._added_span(li)[1]
        return self.lines[li].event.end_ms

    @staticmethod
    def _gap_span(li: int, spans: list) -> tuple[float, float]:
        """For a line without times: the gap between its timed neighbours."""
        start = next((spans[k][1] for k in range(li - 1, -1, -1) if spans[k]), 0.0)
        end = next((spans[k][0] for k in range(li + 1, len(spans)) if spans[k]), None)
        return start, end if end is not None and end > start else start + 10000.0

    def _added_span(self, li: int) -> tuple[float, float]:
        own = self.sung_span(li)
        if own:
            return own
        return self._gap_span(li, [self.sung_span(i) for i in range(len(self.lines))])

    # -- layout --------------------------------------------------------------------
    def sung_span(self, li: int) -> tuple[float, float] | None:
        """When the line is sung: its syllables if tapped, else its line tap.

        Without the layout the line's own times count; with it they are the
        layout's output, so untapped lines have no span.
        """
        r = self.results.get(li)
        if r:
            return r.bounds[0][0], r.bounds[-1][1]
        if li in self.line_taps:
            return self.line_taps[li]
        if not self.layout_on and not self.lines[li].added:
            ev = self.lines[li].event
            return float(ev.start_ms), float(ev.end_ms)
        return None

    def placements(self) -> list[Placement | None]:
        return place([self.sung_span(i) for i in range(len(self.lines))], self.layout_params)

    def _layout_span(self, li: int) -> tuple[float, float]:
        pl = self.placements()
        p = pl[li]
        if p is None:
            # Not timed yet: assume it fills the gap between its timed neighbours.
            return self._gap_span(li, [(q.start, q.end) if q else None for q in pl])
        s, e = self.layout_overrides.get(li, (None, None))
        return (p.start if s is None else s), (p.end if e is None else e)

    def display_spans(self) -> list[tuple[float, float, Placement] | None]:
        """Every placed line's on-screen (start, end, placement), manual times applied."""
        out = []
        for li, p in enumerate(self.placements()):
            if p is None:
                out.append(None)
                continue
            s, e = self.layout_overrides.get(li, (None, None))
            out.append((p.start if s is None else s, p.end if e is None else e, p))
        return out

    def set_layout_override(self, li: int, start: int | None, end: int | None) -> None:
        if start is None and end is None:
            self.layout_overrides.pop(li, None)
        else:
            self.layout_overrides[li] = (start, end)
        self.dirty = True

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
            if li in self.results or line.edited or line.added:
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
        if self.layout_on:          # taps only fall outside a manual override: drop it
            self.set_layout_override(li, None, None)
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
        if self.settings.line_timing == KEEP or self.layout_on:
            out.append(Handle(self.line_start(li), 0.0, b[0][0], (("L0",),)))
            out.append(Handle(self.line_end(li), b[-1][1], math.inf, (("L1",),)))
        return out

    def begin_drag(self, li: int) -> None:
        """Remember line li's result so the coming drag can be undone."""
        r = self.results.get(li)
        self.drag_history.append((li, replace(r, bounds=list(r.bounds)) if r else None,
                                  self.layout_overrides.get(li)))
        del self.drag_history[:-DRAG_UNDO_LIMIT]

    def move_handle(self, li: int, h: Handle, ms: float) -> float:
        """Move a boundary (10 ms steps, kept between its neighbours). Returns where it landed."""
        r = self.results[li]
        ms = min(max(round(ms / 10) * 10, h.lo), h.hi)
        ev = self.lines[li].event
        p = self.placements()[li] if self.layout_on else None
        for ref in h.refs:
            if ref[0] == "s":
                r.bounds[ref[1]] = (ms, r.bounds[ref[1]][1])
            elif ref[0] == "e":
                r.bounds[ref[1]] = (r.bounds[ref[1]][0], ms)
            elif p is not None:                 # the layout's line edges: a manual override
                s, e = self.layout_overrides.get(li, (None, None))
                if ref[0] == "L0":
                    s = None if ms == to_cs(p.start) * 10 else int(ms)
                else:
                    e = None if ms == to_cs(p.end) * 10 else int(ms)
                self.set_layout_override(li, s, e)
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
        li, prev, override = self.drag_history.pop()
        if prev is None:
            self.results.pop(li, None)
        else:
            self.results[li] = prev
        if override is None:
            self.layout_overrides.pop(li, None)
        else:
            self.layout_overrides[li] = override
        self.dirty = True
        return li

    def clear_line(self, li: int) -> None:
        if self.results.pop(li, None) is not None:
            self.dirty = True

    def edit_line(self, li: int, text: str) -> bool:
        """Replace line li's karaoke text (new syllable split). Returns True if its taps were cleared."""
        line = self.lines[li]
        ev = line.event.with_fields(text=text) if line.added else line.event
        new = make_line(li, ev, text, line.added_key)
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

    # -- editing the list of lines ---------------------------------------------------------
    def _snapshot(self, li: int) -> tuple:
        return (self.lines[li], self.results.get(li), self.line_taps.get(li),
                self.layout_overrides.get(li))

    def _restructure(self, items: list[tuple], cursor: int) -> None:
        """Replace the line list; items are (line, result, line tap, layout override)."""
        self.lines, self.results, self.line_taps, self.layout_overrides = [], {}, {}, {}
        for i, (line, r, tap, ov) in enumerate(items):
            line.idx = i
            self.lines.append(line)
            if r is not None:
                self.results[i] = r
            if tap is not None:
                self.line_taps[i] = tap
            if ov is not None:
                self.layout_overrides[i] = ov
        self.take = self.line_take = None
        self.drag_history.clear()
        self.line_drag_history.clear()
        self.cursor = self.line_cursor = max(0, min(cursor, len(self.lines) - 1))
        self.dirty = True

    def _new_event(self, like: Event, text: str) -> Event:
        """An event for a line added in the tool, shaped like `like`."""
        changes = {"start": "0:00:00.00", "end": "0:00:00.00", "effect": "karaoke", "text": text}
        ev = Event(-1, "Comment", like.head, list(like.fields), like.fmt)
        return ev.with_fields(**{k: v for k, v in changes.items() if k in like.fmt})

    def _added_line(self, like: Event, text: str) -> LineInfo:
        line = make_line(0, self._new_event(like, text), text, f"n{self.next_added}")
        if not line.taps:
            raise ValueError("a line needs at least one syllable to tap")
        self.next_added += 1
        return line

    def _forget(self, line: LineInfo) -> None:
        if not line.added:
            self.deleted[line.key] = line.event.text

    def _own_span(self, li: int) -> tuple[float, float] | None:
        r = self.results.get(li)
        if r:
            return r.bounds[0][0], r.bounds[-1][1]
        return self.line_taps.get(li)

    def delete_line(self, li: int) -> None:
        if len(self.lines) <= 1:
            raise ValueError("at least one line has to stay")
        items = [self._snapshot(i) for i in range(len(self.lines)) if i != li]
        self._forget(self.lines[li])
        self._restructure(items, li)

    def insert_line(self, after: int, text: str) -> int:
        """Add a line after line `after` (-1: at the top). Returns its index."""
        like = self.lines[max(0, after)].event
        items = [self._snapshot(i) for i in range(len(self.lines))]
        items.insert(after + 1, (self._added_line(like, text), None, None, None))
        self._restructure(items, after + 1)
        return after + 1

    def replace_text(self, li: int, texts: list[str]) -> None:
        """New lyrics for line li; more texts become new lines after it.

        The line's syllable timing is dropped; its line tap and manual times stay.
        """
        line = self.lines[li]
        ev = line.event.with_fields(text=texts[0]) if line.added else line.event
        first = make_line(li, ev, texts[0], line.added_key)
        if not first.taps:
            raise ValueError("a line needs at least one syllable to tap")
        items = [self._snapshot(i) for i in range(len(self.lines))]
        items[li] = (first, None, self.line_taps.get(li), self.layout_overrides.get(li))
        for k, text in enumerate(texts[1:]):
            items.insert(li + 1 + k, (self._added_line(line.event, text), None, None, None))
        self._restructure(items, li)

    def split_options(self, li: int) -> list[tuple[int, str]]:
        """Where line li can be split: (syllable index, '前半｜後半')."""
        line = self.lines[li]
        parts = [s.text if s.is_gap else ("" if f.continuation else f.base)
                 for f, s in zip(line.furi, line.kara.syls)]
        out = []
        for k in range(1, len(parts)):
            if line.furi[k].continuation:
                continue
            if any(t < k for t in line.taps) and any(t >= k for t in line.taps):
                out.append((k, "".join(parts[:k]).rstrip() + "｜" + "".join(parts[k:]).lstrip()))
        return out

    def split_line(self, li: int, k: int) -> None:
        """Split line li before syllable k; tapped syllable times go with their halves."""
        line = self.lines[li]
        pos = line.kara.insert_pos(k)
        text_a, text_b = line.kara.text[:pos], line.kara.text[pos:]
        ev_a = line.event.with_fields(text=text_a) if line.added else line.event
        a = make_line(li, ev_a, text_a, line.added_key)
        b = self._added_line(line.event, text_b)
        if not a.taps:
            raise ValueError("a line needs at least one syllable to tap")
        n = len(a.taps)
        r, tap = self.results.get(li), self.line_taps.get(li)
        if r:
            ra, rb = LineResult(list(r.bounds[:n])), LineResult(list(r.bounds[n:]))
            ta, tb = (ra.bounds[0][0], ra.bounds[-1][1]), (rb.bounds[0][0], rb.bounds[-1][1])
        else:
            ra = rb = tb = None
            ta = tap
        items = [self._snapshot(i) for i in range(len(self.lines))]
        items[li:li + 1] = [(a, ra, ta, None), (b, rb, tb, None)]
        self._restructure(items, li)

    def merge_loses_syllables(self, li: int) -> bool:
        """Merging keeps syllable times only when both lines have them."""
        return (li in self.results) != (li + 1 in self.results)

    def merge_with_next(self, li: int) -> None:
        """Join line li and the next one, a space syllable between them."""
        a, b = self.lines[li], self.lines[li + 1]
        edge_space = a.kara.syls[-1].text.endswith(" ") or b.kara.text.startswith(" ") \
            or (b.kara.syls and b.kara.syls[0].text.startswith(" ") and not b.kara.prefix)
        joiner = "" if edge_space else "{\\%s1} " % a.kara.syls[-1].tag
        text = a.kara.text + joiner + b.kara.text
        ev = a.event.with_fields(text=text) if a.added else a.event
        merged = make_line(li, ev, text, a.added_key)
        ra, rb = self.results.get(li), self.results.get(li + 1)
        result = LineResult(list(ra.bounds) + list(rb.bounds)) if ra and rb else None
        sa, sb = self._own_span(li), self._own_span(li + 1)
        span = (sa[0], sb[1]) if sa and sb else (sa or sb)
        items = [self._snapshot(i) for i in range(len(self.lines))]
        items[li:li + 2] = [(merged, result, span, None)]
        self._forget(b)
        self._restructure(items, li)

    def shift_times(self, lines: list[int] | None, delta: float) -> tuple[int, int]:
        """Move the timing of the given lines (all when None) by delta ms.

        Line taps, syllable times and manual line times move. Returns (moved,
        lines without any timing of the tool's own, left where they are).
        """
        targets = range(len(self.lines)) if lines is None else sorted(set(lines))
        times = []
        for li in targets:
            sp = self._own_span(li)
            if sp:
                times.append(sp[0])
            times += [v for v in self.layout_overrides.get(li, ()) if v is not None]
            if li in self.results and not self.layout_on:
                times.append(self.line_start(li))
        if times and min(times) + delta < 0:
            raise ValueError("times would become negative")
        moved = untouched = 0
        for li in targets:
            changed = False
            if li in self.line_taps:
                s, e = self.line_taps[li]
                self.line_taps[li] = (s + delta, e + delta)
                changed = True
            r = self.results.get(li)
            if r:
                r.bounds = [(s + delta, e + delta) for s, e in r.bounds]
                line = self.lines[li]
                if not line.added and not self.layout_on:     # the file's own line times move along
                    if r.start_override is None:
                        r.start_override = line.event.start_ms
                    if r.end_override is None:
                        r.end_override = line.event.end_ms
                if r.start_override is not None:
                    r.start_override = int(round(r.start_override + delta))
                if r.end_override is not None:
                    r.end_override = int(round(r.end_override + delta))
                r.source = FROM_TAPS
                changed = True
            ov = self.layout_overrides.get(li)
            if ov:
                self.layout_overrides[li] = tuple(None if v is None else int(round(v + delta)) for v in ov)
                changed = True
            moved += changed
            untouched += not changed
        self.take = self.line_take = None
        self.drag_history.clear()
        self.line_drag_history.clear()
        self.dirty = True
        return moved, untouched

    # -- dragging line taps on the song timeline ------------------------------------------------
    def line_draggable(self, li: int) -> bool:
        """Line taps can be dragged; lines with tapped syllables are edited on their waveform."""
        return li in self.line_taps and li not in self.results

    def begin_line_drag(self, li: int) -> None:
        self.line_drag_history.append((li, self.line_taps.get(li)))
        del self.line_drag_history[:-DRAG_UNDO_LIMIT]
        self.line_take = None           # its replay log would bring the old times back

    def move_line_edge(self, li: int, edge: str, ms: float) -> float:
        """Move a line tap's 'start' or 'end' (10 ms steps). Returns where it landed."""
        s, e = self.line_taps[li]
        ms = round(ms / 10) * 10
        if edge == "start":
            ms = min(max(0.0, ms), e - MIN_LINE_MS)
            self.line_taps[li] = (ms, e)
        else:
            ms = max(ms, s + MIN_LINE_MS)
            self.line_taps[li] = (s, ms)
        self.dirty = True
        return ms

    def undo_line_drag(self) -> int | None:
        if not self.line_drag_history:
            return None
        li, prev = self.line_drag_history.pop()
        self.line_take = None
        if prev is None:
            self.line_taps.pop(li, None)
        else:
            self.line_taps[li] = prev
        self.dirty = True
        return li

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
        if timing and timing.warnings and (self.settings.line_timing == KEEP or self.layout_on):
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

    # -- line tapping (whole lines, before the syllables) ---------------------------------
    def start_line_take(self, li: int) -> None:
        li = max(0, min(li, len(self.lines) - 1))
        n = len(self.lines) - li
        s = self.settings
        # Every line is "last" for the onset mode's release rule: release (or the end key) ends it.
        self.line_take = LineTake(li, TapMachine(n, s.mode, [True] * n, s.onset_end))
        self.line_cursor = li

    def _ensure_line_take(self) -> LineTake:
        if self.line_take is None:
            self.start_line_take(self.line_cursor)
        return self.line_take

    def line_current(self) -> int:
        """The line the next press starts."""
        t = self.line_take
        if t is None:
            return self.line_cursor
        return t.first + min(t.machine.next, t.machine.n - 1)

    def line_state(self, li: int) -> str:
        """'active' / 'next' while tapping; else 'syllables', 'tapped' or 'todo'."""
        t = self.line_take
        if t and li >= t.first:
            k, m = li - t.first, t.machine
            if k == m.open_target:
                return "active"
            if k == m.next:
                return "next"
        if li in self.results:
            return "syllables"
        return "tapped" if li in self.line_taps else "todo"

    def line_seek_target(self, li: int) -> float:
        """Where to start playing to tap line li."""
        sp = self.sung_span(li)
        if sp:
            return max(0.0, sp[0] - self.settings.preroll_ms)
        for k in range(li - 1, -1, -1):
            sp = self.sung_span(k)
            if sp:
                return max(0.0, sp[1] - self.settings.preroll_ms)
        return 0.0

    def line_down(self, key, t: float) -> list[int]:
        if self._ensure_line_take().machine.down(key, t) is None:
            return []
        return self._line_sync()

    def line_up(self, key, t: float) -> list[int]:
        if self._ensure_line_take().machine.up(key, t) is None:
            return []
        return self._line_sync()

    def line_end_key(self, t: float) -> list[int]:
        if self._ensure_line_take().machine.end_key(t) is None:
            return []
        return self._line_sync()

    def line_undo(self) -> float | None:
        """Undo the last line press. Returns the song ms to rewind to, or None."""
        if self.line_take is None:
            return None
        r = self.line_take.machine.undo()
        if r is None:
            return None
        self._line_sync()
        return max(0.0, r[1] - self.settings.preroll_ms)

    def _line_sync(self) -> list[int]:
        """Store finished lines; restore lines an undo reopened. Returns newly finished lines."""
        t = self.line_take
        m = t.machine
        done = []
        for k in range(m.n):
            li = t.first + k
            if m.is_done(k):
                if li not in t.committed:
                    t.backup.setdefault(li, self.line_taps.get(li))
                    self.line_taps[li] = (m.start[k], m.end[k])
                    t.committed.add(li)
                    done.append(li)
            elif li in t.committed:
                t.committed.discard(li)
                prev = t.backup.pop(li, None)
                if prev is None:
                    self.line_taps.pop(li, None)
                else:
                    self.line_taps[li] = prev
        self.line_cursor = self.line_current()
        self.dirty = True
        return done

    def clear_line_tap(self, li: int) -> None:
        if self.line_taps.pop(li, None) is not None:
            self.dirty = True

    # -- export ------------------------------------------------------------------
    def _render(self, li: int, placements: list[Placement | None] | None) -> str | None:
        """Line li as it goes into the exported file; None when the file's line stays as it is."""
        line = self.lines[li]
        ev = line.event
        timing = self.timing_for(li)
        original = None if line.added else self.doc.lines[ev.line_no]

        def times(start: float, end: float) -> dict:
            return {"start": format_time(start, like=ev.get("start")),
                    "end": format_time(end, like=ev.get("end"))}

        if placements is not None and placements[li] is not None:
            # The layout decides every timed line's times and style.
            if timing:
                text = line.kara.rebuild(timing.values, timing.inserts)
                start, end = timing.start_cs * 10, timing.end_cs * 10
            else:           # only the line was tapped: keep the placeholder \k
                text = line.kara.text
                start, end = to_cs(self.line_start(li)) * 10, to_cs(self.line_end(li)) * 10
            new_ev = ev.with_fields(text=text, style=placements[li].style, **times(start, end))
        elif timing is None:
            changes = {"text": line.kara.text}
            span = self._own_span(li) if line.added else None
            if span:
                changes.update(times(to_cs(span[0]) * 10, to_cs(span[1]) * 10))
            if not line.added and not line.edited:
                return None
            new_ev = ev.with_fields(**changes)
        else:
            r = self.results[li]
            if r.source == FROM_FILE and not line.added:
                return None     # \k from the file, untouched: keep the line as it is
            changes = {"text": line.kara.rebuild(timing.values, timing.inserts)}
            if (line.added or self.settings.line_timing == TAPPED or r.start_override is not None
                    or r.end_override is not None):
                if line.added or timing.start_cs * 10 != ev.start_ms:
                    changes["start"] = format_time(timing.start_cs * 10, like=ev.get("start"))
                if line.added or timing.end_cs * 10 != ev.end_ms:
                    changes["end"] = format_time(timing.end_cs * 10, like=ev.get("end"))
            new_ev = ev.with_fields(**changes)
        text = new_ev.render()
        return None if text == original else text

    def export_bytes(self) -> bytes:
        repl: dict[int, str | None] = {int(key): None for key in self.deleted}
        after: dict[int, list[str]] = {}
        before: dict[int, list[str]] = {}
        placements = self.placements() if self.layout_on else None
        for li, line in enumerate(self.lines):
            text = self._render(li, placements)
            if not line.added:
                if text is not None:
                    repl[line.event.line_no] = text
                continue
            # A new line goes after the file line shown before it (or before the one after it).
            prev = next((self.lines[k] for k in range(li - 1, -1, -1) if not self.lines[k].added), None)
            if prev is not None:
                after.setdefault(prev.event.line_no, []).append(text)
                continue
            nxt = next((l for l in self.lines[li + 1:] if not l.added), None)
            anchor = nxt.event.line_no if nxt else self._first_event.line_no
            before.setdefault(anchor, []).append(text)
        return self.doc.to_bytes(repl, after, before)

    # -- progress file ---------------------------------------------------------------
    def progress_data(self) -> dict:
        return {
            "version": 1,
            "ass_sha1": _sha1(self.doc.raw),
            "cursor": self.cursor,
            "line_cursor": self.line_cursor,
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
            "line_taps": {
                self.lines[li].key: {"text": self.lines[li].event.text, "span": list(sp)}
                for li, sp in sorted(self.line_taps.items())
            },
            "layout": {
                "on": self.layout_on,
                "params": asdict(self.layout_params),
                "overrides": {
                    self.lines[li].key: {"text": self.lines[li].event.text, "start": s, "end": e}
                    for li, (s, e) in sorted(self.layout_overrides.items())
                },
            },
            "structure": {
                "deleted": dict(sorted(self.deleted.items())),
                "added": [{"key": l.key, "after": self.lines[i - 1].key if i else None, "text": l.event.text}
                          for i, l in enumerate(self.lines) if l.added],
                "next": self.next_added,
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

        def finder():
            by_key = {l.key: l for l in self.lines}
            by_text: dict[str, list[LineInfo]] = {}
            for l in self.lines:
                by_text.setdefault(l.event.text, []).append(l)

            def find(key: str, text: str) -> LineInfo | None:
                line = by_key.get(key)
                if line is None or line.event.text != text:
                    cands = [c for c in by_text.get(text, []) if not c.added]
                    line = cands[0] if len(cands) == 1 else None
                return line
            return find

        find = finder()
        dropped = 0
        for key, rec in data.get("edits", {}).items():     # syllable splits first: taps depend on them
            line = find(key, rec["text"])
            if line is None:
                dropped += 1
                continue
            self.lines[line.idx] = make_line(line.idx, line.event, rec["edited"])
        # Lines removed and added in the tool.
        st = data.get("structure", {})
        self.next_added = int(st.get("next", 1))
        gone = set()
        for key, text in st.get("deleted", {}).items():
            line = find(key, text)
            if line is None:
                dropped += 1
                continue
            gone.add(line.idx)
            self.deleted[line.key] = text
        self.lines = [l for l in self.lines if l.idx not in gone]
        for rec in st.get("added", []):
            pos = 0
            if rec.get("after") is not None:
                pos = next((i + 1 for i, l in enumerate(self.lines) if l.key == rec["after"]), len(self.lines))
            like = self.lines[pos - 1].event if pos else (self.lines[0].event if self.lines else self._first_event)
            line = make_line(0, self._new_event(like, rec["text"]), rec["text"], rec["key"])
            if not line.taps:
                dropped += 1
                continue
            self.lines.insert(pos, line)
        for i, l in enumerate(self.lines):
            l.idx = i
        find = finder()
        for key, rec in data.get("lines", {}).items():
            line = find(key, rec["text"])
            line = self.lines[line.idx] if line else None
            if line is None or len(rec["bounds"]) != len(line.taps):
                dropped += 1
                continue
            self.results[line.idx] = LineResult([tuple(b) for b in rec["bounds"]],
                                                rec.get("start_override"), rec.get("end_override"))
        for key, rec in data.get("line_taps", {}).items():
            line = find(key, rec["text"])
            if line is None:
                dropped += 1
                continue
            self.line_taps[line.idx] = tuple(rec["span"])
        layout = data.get("layout")
        if layout:
            self.layout_on = bool(layout.get("on"))
            known = {f.name for f in fields(LayoutParams)}
            self.layout_params = LayoutParams(**{k: v for k, v in layout.get("params", {}).items()
                                                 if k in known})
            for key, rec in layout.get("overrides", {}).items():
                line = find(key, rec["text"])
                if line is not None:
                    self.layout_overrides[line.idx] = (rec.get("start"), rec.get("end"))
        self.cursor = min(max(0, int(data.get("cursor", 0))), max(0, len(self.lines) - 1))
        self.line_cursor = min(max(0, int(data.get("line_cursor", 0))), max(0, len(self.lines) - 1))
        if data.get("ass_sha1") != _sha1(self.doc.raw):
            self.load_messages.append("ASS 檔在上次存檔後有變動，已依行內容比對進度。")
        if dropped:
            self.load_messages.append(f"有 {dropped} 行的進度對不上目前的 ASS，已略過。")
