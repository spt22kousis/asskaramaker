"""Line layout: when each line appears and which style it uses.

Two rows (the default) alternate odd/even lines; one row shows one line at a time.

The rule comes from the notebook's 奇偶轉換器, applied to sung times (the
first syllable's start to the last syllable's end) so it can be redone after
every change:

- Lines more than `segment_gap_ms` apart start a new segment.
- A segment of one line uses the single style and appears when it is sung.
- Otherwise lines alternate odd/even. The first appears `lead_ms` early,
  the second together with it, and every later line `slot_gap_ms` after the
  previous line on the same row has disappeared.
- A line disappears `tail_ms` after it is sung.

With one row every line uses `one_style`, appears `lead_ms` before it is
sung but not before the previous line has gone (plus `slot_gap_ms`), and
disappears `tail_ms` after it is sung.

A line the rule would show only after its singing started is shown when the
singing starts instead, and flagged `late`.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class LayoutParams:
    odd_style: str = "Odd"
    even_style: str = "Even"
    single_style: str = "Default"
    segment_gap_ms: int = 2000
    lead_ms: int = 1000
    slot_gap_ms: int = 300
    tail_ms: int = 0
    rows: int = 2                  # 2: odd/even rows, 1: one line at a time
    one_style: str = "Default"     # the style of every line with one row


@dataclass
class Placement:
    start: float        # ms
    end: float
    style: str
    late: bool = False


def place(spans: list[tuple[float, float] | None], p: LayoutParams) -> list[Placement | None]:
    """Placement per line from its sung (start, end); None for lines without one."""
    if p.rows == 1:
        return _place_one_row(spans, p)
    out: list[Placement | None] = [None] * len(spans)
    seg: list[int] = []

    def flush() -> None:
        if len(seg) == 1:
            s, e = spans[seg[0]]
            out[seg[0]] = Placement(s, e + p.tail_ms, p.single_style)
        elif seg:
            for k, i in enumerate(seg):
                s, e = spans[i]
                if k == 0:
                    start = s - p.lead_ms
                elif k == 1:
                    start = out[seg[0]].start
                else:
                    start = out[seg[k - 2]].end + p.slot_gap_ms
                style = p.odd_style if k % 2 == 0 else p.even_style
                out[i] = Placement(max(0.0, min(start, s)), e + p.tail_ms, style, start > s)
        seg.clear()

    prev_end: float | None = None
    for i, sp in enumerate(spans):
        if sp is None:
            flush()
            prev_end = None
            continue
        if prev_end is not None and sp[0] - prev_end > p.segment_gap_ms:
            flush()
        seg.append(i)
        prev_end = sp[1]
    flush()
    return out


def _place_one_row(spans: list[tuple[float, float] | None], p: LayoutParams) -> list[Placement | None]:
    out: list[Placement | None] = [None] * len(spans)
    prev_end: float | None = None
    for i, sp in enumerate(spans):
        if sp is None:
            continue
        s, e = sp
        start = s - p.lead_ms
        if prev_end is not None:
            start = max(start, prev_end + p.slot_gap_ms)
        out[i] = Placement(max(0.0, min(start, s)), e + p.tail_ms, p.one_style, start > s)
        prev_end = e + p.tail_ms
    return out
