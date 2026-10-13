"""Pure timing logic: key events -> syllable boundaries -> \\k centiseconds.

Everything here works in song time (milliseconds of the original-speed
song) and has no dependency on audio or UI, so it is fully unit-testable.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

HOLD = "hold"        # press = syllable start, release = syllable end
ONSET = "onset"      # only presses count; length = next press - this press

KEEP = "keep"        # keep the line's existing start/end, only fill \k
TAPPED = "tapped"    # line start/end come from the taps

MERGE_PREV = "merge_prev"    # release gap without a gap syllable goes to the previous syllable
INSERT_GAP = "insert_gap"    # ...or becomes a new empty {\k}

END_RELEASE = "release"      # onset mode: a line's last syllable ends on key release
END_KEY = "end_key"          # ...or on the dedicated end key


@dataclass
class TapEvent:
    kind: str          # 'down', 'up', 'end', 'skip'
    key: object = None
    t: float = 0.0     # song ms
    to: int = 0        # 'skip': first target index to continue from


class TapMachine:
    """Turns a stream of key events into (start, end) per target syllable.

    Targets are a flat list of tappable syllables that may span several
    lines; `line_last[i]` marks the last target of each line (used by the
    onset mode's release rule). The machine keeps its event log so that
    `undo()` can rebuild state by replaying.
    """

    def __init__(self, n_targets: int, mode: str = HOLD,
                 line_last: list[bool] | None = None, onset_end: str = END_RELEASE):
        self.n = n_targets
        self.mode = mode
        self.line_last = line_last or [False] * n_targets
        self.onset_end = onset_end
        self.events: list[TapEvent] = []
        self._reset()

    def _reset(self) -> None:
        self.start: list[float | None] = [None] * self.n
        self.end: list[float | None] = [None] * self.n
        self.next = 0
        self.held: dict[object, int] = {}
        self._open: int | None = None   # started but not yet ended
        # Which event set each start / end (indices into self.events), so they can be moved.
        self.start_ev: list[int | None] = [None] * self.n
        self.end_ev: list[int | None] = [None] * self.n

    # -- event entry points ------------------------------------------------
    def down(self, key, t: float) -> int | None:
        """Returns the target index started by this press, or None if ignored."""
        if key in self.held or self.next >= self.n:
            return None   # OS key repeat, or nothing left to tap
        return self._log(TapEvent("down", key, t))

    def up(self, key, t: float) -> int | None:
        """Returns the target index ended by this release, if any."""
        if key not in self.held:
            return None
        return self._log(TapEvent("up", key, t))

    def end_key(self, t: float) -> int | None:
        if self._open is None:
            return None
        return self._log(TapEvent("end", None, t))

    def skip_to(self, target: int) -> None:
        """Leave targets before `target` untapped and continue from there."""
        if target <= self.next:
            return
        self._log(TapEvent("skip", to=min(target, self.n)))

    def undo(self) -> tuple[int, float] | None:
        """Drop the last press (and everything after it).

        Returns (target index, press time) of the undone press, or None.
        """
        for k in range(len(self.events) - 1, -1, -1):
            if self.events[k].kind == "down":
                undone = self.events[k]
                self.events = self.events[:k]
                self._replay()
                return self.next, undone.t
        return None

    def move(self, i: int, edge: str, t: float) -> None:
        """Move target i's 'start' or 'end' to t by changing the press or release that set it."""
        k = (self.start_ev if edge == "start" else self.end_ev)[i]
        if k is None:
            raise ValueError(f"target {i} has no {edge}")
        self.events[k].t = t
        self._replay()

    # -- internals -----------------------------------------------------------

    def _log(self, ev: TapEvent) -> int | None:
        self.events.append(ev)
        return self._apply(ev, len(self.events) - 1)

    def _replay(self) -> None:
        self._reset()
        for k, ev in enumerate(self.events):
            self._apply(ev, k)

    def _close_open(self, t: float, k: int) -> int | None:
        i = self._open
        if i is not None:
            self.end[i] = max(t, self.start[i])
            self.end_ev[i] = k
            self._open = None
        return i

    def _apply(self, ev: TapEvent, k: int) -> int | None:
        if ev.kind == "down":
            self._close_open(ev.t, k)
            i = self.next
            self.start[i] = ev.t
            self.start_ev[i] = k
            self.held[ev.key] = i
            self._open = i
            self.next += 1
            return i
        if ev.kind == "up":
            i = self.held.pop(ev.key, None)
            if i is None or i != self._open:
                return None   # already ended by a later press (overlapping keys)
            if self.mode == HOLD or (self.line_last[i] and self.onset_end == END_RELEASE):
                return self._close_open(ev.t, k)
            return None
        if ev.kind == "end":
            return self._close_open(ev.t, k)
        if ev.kind == "skip":
            self._open = None
            self.held.clear()
            self.next = ev.to
            return None
        raise ValueError(ev.kind)

    def is_done(self, i: int) -> bool:
        return self.start[i] is not None and self.end[i] is not None

    @property
    def open_target(self) -> int | None:
        return self._open


# --------------------------------------------------------------------------
# boundaries -> \k values

@dataclass
class TimingOptions:
    line_timing: str = KEEP
    gap_policy: str = MERGE_PREV
    pre_pad_ms: int = 0       # TAPPED mode: line starts this much before the first tap
    post_pad_ms: int = 0      # TAPPED mode: line ends this much after the last release


@dataclass
class LineTiming:
    values: list[int]                 # new \k value per existing syllable
    inserts: dict[int, int]           # syllable index -> value of a new gap syllable before it
    start_cs: int                     # line start/end in centiseconds
    end_cs: int
    warnings: list[str] = field(default_factory=list)   # 'early', 'late'

    @property
    def total(self) -> int:
        return sum(self.values) + sum(self.inserts.values())


def to_cs(ms: float) -> int:
    """Absolute ms -> centiseconds, rounded half up."""
    return int(math.floor(ms / 10.0 + 0.5))


def compute_line(gaps: list[bool], bounds: list[tuple[float, float]],
                 line_start_ms: int, line_end_ms: int,
                 opts: TimingOptions) -> LineTiming:
    """Turn tapped (start, end) song-ms boundaries into \\k values for one line.

    `gaps[i]` says whether syllable i is a gap syllable; `bounds` has one
    entry per non-gap syllable, in order. Boundaries are rounded to 10 ms
    first and the \\k values are differences of rounded boundaries, so the
    values always sum to the line length.
    """
    taps = [i for i, g in enumerate(gaps) if not g]
    if len(bounds) != len(taps) or not taps:
        raise ValueError("bounds must have one entry per tappable syllable")

    starts = [to_cs(s) for s, _ in bounds]
    ends = [to_cs(e) for _, e in bounds]
    for j in range(len(taps)):          # keep boundaries monotonic
        if j:
            starts[j] = max(starts[j], ends[j - 1])
        ends[j] = max(ends[j], starts[j])

    warnings: list[str] = []
    if opts.line_timing == KEEP:
        L0, L1 = to_cs(line_start_ms), to_cs(line_end_ms)
        if starts[0] < L0:
            warnings.append("early")
        if ends[-1] > L1:
            warnings.append("late")
        starts = [min(max(s, L0), L1) for s in starts]
        ends = [min(max(e, L0), L1) for e in ends]
    else:
        L0 = max(0, starts[0] - to_cs(opts.pre_pad_ms))
        L1 = ends[-1] + to_cs(opts.post_pad_ms)

    n = len(gaps)
    values = [0] * n
    inserts: dict[int, int] = {}

    lead = starts[0] - L0
    if taps[0] > 0:
        values[0] = lead                  # reuse the existing leading gap syllable
    elif lead > 0:
        inserts[0] = lead

    for j, i in enumerate(taps):
        if j + 1 < len(taps):
            nxt = taps[j + 1]
            gap = starts[j + 1] - ends[j]
            if nxt - i > 1:               # gap syllable(s) in between: first takes the gap
                values[i] = ends[j] - starts[j]
                values[i + 1] = gap
            elif gap > 0 and opts.gap_policy == INSERT_GAP:
                values[i] = ends[j] - starts[j]
                inserts[nxt] = gap
            else:
                values[i] = starts[j + 1] - starts[j]
        else:
            if i + 1 < n:                 # trailing gap syllable takes the rest
                values[i] = ends[j] - starts[j]
                values[i + 1] = L1 - ends[j]
            else:
                values[i] = L1 - starts[j]

    return LineTiming(values, inserts, L0, L1, warnings)
