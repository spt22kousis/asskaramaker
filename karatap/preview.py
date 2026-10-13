"""What the exported lines look like before Aegisub's template: styles, positions and the \\k fill.

Only what plain ASS shows is modelled (no template effects): each line's style
(font, size, colours, outline, alignment, margins), furigana above its kanji and
the sung part turning from the secondary to the primary colour.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .ass_doc import _SECTION_RE, AssDocument
from .karaoke import KaraLine, furigana_for

RGBA = tuple[int, int, int, int]


@dataclass
class Style:
    name: str
    font: str = "Arial"
    size: float = 20.0
    primary: RGBA = (255, 255, 255, 255)
    secondary: RGBA = (255, 0, 0, 255)
    outline_colour: RGBA = (0, 0, 0, 255)
    back: RGBA = (0, 0, 0, 255)
    bold: bool = False
    italic: bool = False
    scale_x: float = 100.0
    spacing: float = 0.0
    outline: float = 2.0
    shadow: float = 0.0
    alignment: int = 2
    margins: tuple[int, int, int] = (10, 10, 10)      # MarginL, MarginR, MarginV


def ass_colour(value: str) -> RGBA:
    """'&H80FF7600' (alpha, blue, green, red; alpha 00 = opaque) -> (r, g, b, a)."""
    h = value.strip().lstrip("&").lstrip("Hh").rstrip("&") or "0"
    try:
        n = int(h, 16)
    except ValueError:
        n = 0
    return n & 0xFF, (n >> 8) & 0xFF, (n >> 16) & 0xFF, 255 - ((n >> 24) & 0xFF)


def _number(value: str, default: float) -> float:
    try:
        return float(value)
    except ValueError:
        return default


def parse_styles(doc: AssDocument) -> dict[str, Style]:
    styles: dict[str, Style] = {}
    section, fmt = None, []
    for line in doc.lines:
        m = _SECTION_RE.match(line)
        if m:
            section = m.group(1).strip().lower()
            continue
        if section not in ("v4+ styles", "v4 styles") or ":" not in line:
            continue
        head, rest = line.split(":", 1)
        head = head.strip().lower()
        if head == "format":
            fmt = [f.strip().lower() for f in rest.split(",")]
        elif head == "style" and fmt:
            values = dict(zip(fmt, (v.strip() for v in rest.split(",", len(fmt) - 1))))
            d = Style(values.get("name", ""))

            def num(key: str, default: float) -> float:
                return _number(values.get(key, ""), default)

            styles[d.name] = Style(
                d.name, values.get("fontname", d.font), num("fontsize", d.size),
                ass_colour(values.get("primarycolour", "&H00FFFFFF")),
                ass_colour(values.get("secondarycolour", "&H000000FF")),
                ass_colour(values.get("outlinecolour", "&H00000000")),
                ass_colour(values.get("backcolour", "&H00000000")),
                num("bold", 0) != 0, num("italic", 0) != 0, num("scalex", 100), num("spacing", 0),
                num("outline", d.outline), num("shadow", 0), int(num("alignment", 2)),
                (int(num("marginl", 10)), int(num("marginr", 10)), int(num("marginv", 10))))
    return styles


def play_res(doc: AssDocument) -> tuple[int, int]:
    """The script's resolution (what positions and sizes are measured in)."""
    def value(key: str) -> int | None:
        v = doc.section_value("Script Info", key)
        n = int(_number(v, 0)) if v else 0
        return n if n > 0 else None
    x, y = value("PlayResX"), value("PlayResY")
    if x is None and y is None:
        return 384, 288
    if x is None:
        return round(y * 4 / 3), y
    if y is None:
        return x, round(x * 3 / 4)
    return x, y


@dataclass
class Piece:
    """One \\k syllable: its reading (furigana, '' if none) and when it is sung."""
    ruby: str
    start: int          # ms from the line's start
    dur: int
    sweep: bool         # \kf / \K fill gradually; \k / \ko switch at once


@dataclass
class Unit:
    """Text drawn as one piece: a syllable, or a kanji with all the syllables of its reading."""
    base: str
    pieces: list[Piece] = field(default_factory=list)


def _visible(text: str) -> str:
    return text.replace("\\h", " ").replace("\\N", " ").replace("\\n", " ")


def kara_units(text: str) -> list[Unit]:
    k = KaraLine.parse(text)
    units: list[Unit] = []
    if _visible(k.prefix).strip():
        units.append(Unit(_visible(k.prefix), [Piece("", 0, 0, False)]))     # before any \k: already sung
    t = 0
    for syl, f in zip(k.syls, furigana_for(k)):
        piece = Piece(f.ruby, t, syl.value * 10, syl.tag in ("kf", "K"))
        t += syl.value * 10
        if f.continuation and units:
            units[-1].pieces.append(piece)
            continue
        # keep spaces around plain text; a kanji|reading syllable shows its kanji
        base = f.base if "|" in syl.text else syl.text
        units.append(Unit(_visible(base), [piece]))
    return units


def piece_progress(p: Piece, t: float) -> float:
    """How much of the piece is sung at t (ms from the line's start), 0..1."""
    if t < p.start:
        return 0.0
    if p.sweep and p.dur > 0:
        return min(1.0, (t - p.start) / p.dur)
    return 1.0


def unit_progress(u: Unit, t: float) -> float:
    total = sum(p.dur for p in u.pieces)
    if total <= 0:
        return 1.0 if t >= u.pieces[0].start else 0.0
    return sum(piece_progress(p, t) * p.dur for p in u.pieces) / total
