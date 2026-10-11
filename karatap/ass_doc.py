"""Lossless ASS reading and writing.

The document keeps every physical line exactly as read (text plus its own
line terminator), so writing it back without replacements reproduces the
original bytes. Only lines passed to `to_bytes(replacements=...)` change.
"""
from __future__ import annotations

import codecs
import re
from dataclasses import dataclass
from pathlib import Path

from .karaoke import KTAG_RE

_EOL_RE = re.compile(r"\r\n|\r|\n")
_EVENT_RE = re.compile(r"^(Dialogue|Comment)(:\s*)(.*)$", re.DOTALL)
_SECTION_RE = re.compile(r"^\s*\[(.+)\]\s*$")
_TIME_RE = re.compile(r"^\s*(\d+):(\d{1,2}):(\d{1,2})(?:[.,](\d+))?\s*$")

DEFAULT_EVENT_FORMAT = ["layer", "start", "end", "style", "name",
                        "marginl", "marginr", "marginv", "effect", "text"]

_BOMS = [
    (codecs.BOM_UTF8, "utf-8"),
    (codecs.BOM_UTF16_LE, "utf-16-le"),
    (codecs.BOM_UTF16_BE, "utf-16-be"),
]


def parse_time(s: str) -> int:
    """ASS time 'H:MM:SS.cc' -> milliseconds."""
    m = _TIME_RE.match(s)
    if not m:
        raise ValueError(f"bad ASS time: {s!r}")
    h, mi, sec, frac = m.groups()
    frac_ms = int(round(int(frac) * 10 ** (3 - len(frac)))) if frac else 0
    return ((int(h) * 60 + int(mi)) * 60 + int(sec)) * 1000 + frac_ms


def format_time(ms: int, like: str | None = None) -> str:
    """Milliseconds -> 'H:MM:SS.cc' (rounded half-up to centiseconds).

    `like` is an existing time string whose hour width is kept.
    """
    cs = (max(0, int(ms)) + 5) // 10
    h, rem = divmod(cs, 360000)
    mi, rem = divmod(rem, 6000)
    sec, cs = divmod(rem, 100)
    hw = 1
    if like:
        hw = max(1, len(like.strip().split(":")[0]))
    return f"{h:0{hw}d}:{mi:02d}:{sec:02d}.{cs:02d}"


@dataclass
class Event:
    """A Dialogue/Comment line in [Events]."""

    line_no: int          # index into AssDocument.lines
    kind: str             # 'Dialogue' or 'Comment'
    head: str             # separator after kind, e.g. ': ', exactly as in the file
    fields: list[str]     # raw field strings, in Format order
    fmt: list[str]        # lower-cased field names

    def _i(self, name: str) -> int:
        return self.fmt.index(name)

    def get(self, name: str) -> str:
        return self.fields[self._i(name)]

    def with_fields(self, **changes: str) -> "Event":
        fields = list(self.fields)
        for name, value in changes.items():
            fields[self._i(name)] = value
        return Event(self.line_no, self.kind, self.head, fields, self.fmt)

    @property
    def text(self) -> str:
        return self.get("text")

    @property
    def effect(self) -> str:
        return self.get("effect")

    @property
    def style(self) -> str:
        return self.get("style")

    @property
    def start_ms(self) -> int:
        return parse_time(self.get("start"))

    @property
    def end_ms(self) -> int:
        return parse_time(self.get("end"))

    @property
    def is_karaoke(self) -> bool:
        """Karaoke source line, by the same rule as Aegisub's karaoke templater.

        Effect 'karaoke' (lines the templater already commented out), or a
        not-yet-templated Dialogue with an empty Effect that has \\k tags.
        """
        effect = self.effect.strip().lower()
        if effect == "karaoke":
            return True
        return effect == "" and self.kind == "Dialogue" and KTAG_RE.search(self.text) is not None

    def render(self) -> str:
        return self.kind + self.head + ",".join(self.fields)


class AssDocument:
    def __init__(self, data: bytes, path: Path | None = None):
        self.path = path
        self.raw = data
        self.bom = b""
        self.encoding = "utf-8"
        for bom, enc in _BOMS:
            if data.startswith(bom):
                self.bom, self.encoding = bom, enc
                break
        # surrogateescape keeps undecodable bytes so the round trip stays exact.
        text = data[len(self.bom):].decode(self.encoding, "surrogateescape")
        self.lines: list[str] = []
        self.eols: list[str] = []
        pos = 0
        for m in _EOL_RE.finditer(text):
            self.lines.append(text[pos:m.start()])
            self.eols.append(m.group())
            pos = m.end()
        if pos < len(text):
            self.lines.append(text[pos:])
            self.eols.append("")
        self.events: list[Event] = []
        self._parse_events()

    @classmethod
    def load(cls, path: str | Path) -> "AssDocument":
        p = Path(path)
        return cls(p.read_bytes(), p)

    def _parse_events(self) -> None:
        section = None
        fmt = list(DEFAULT_EVENT_FORMAT)
        for no, line in enumerate(self.lines):
            m = _SECTION_RE.match(line)
            if m:
                section = m.group(1).strip().lower()
                continue
            if section != "events":
                continue
            if line.lower().startswith("format:"):
                fmt = [f.strip().lower() for f in line.split(":", 1)[1].split(",")]
                continue
            m = _EVENT_RE.match(line)
            if not m:
                continue
            fields = m.group(3).split(",", len(fmt) - 1)
            if len(fields) != len(fmt):
                continue
            self.events.append(Event(no, m.group(1), m.group(2), fields, fmt))

    def karaoke_events(self) -> list[Event]:
        return [e for e in self.events if e.is_karaoke]

    def section_value(self, section: str, key: str) -> str | None:
        """Value of 'key: value' inside [section], e.g. ('Aegisub Project Garbage', 'Audio File')."""
        cur = None
        for line in self.lines:
            m = _SECTION_RE.match(line)
            if m:
                cur = m.group(1).strip().lower()
                continue
            if cur == section.lower() and ":" in line:
                k, v = line.split(":", 1)
                if k.strip().lower() == key.lower():
                    return v.strip()
        return None

    def style_names(self) -> list[str]:
        names, cur = [], None
        for line in self.lines:
            m = _SECTION_RE.match(line)
            if m:
                cur = m.group(1).strip().lower()
            elif cur in ("v4+ styles", "v4 styles") and line.lower().startswith("style:"):
                names.append(line.split(":", 1)[1].split(",", 1)[0].strip())
        return names

    def default_eol(self) -> str:
        eols = [e for e in self.eols if e]
        return max(set(eols), key=eols.count) if eols else "\r\n"

    def to_bytes(self, replacements: dict[int, str | None] | None = None,
                 after: dict[int, list[str]] | None = None,
                 before: dict[int, list[str]] | None = None) -> bytes:
        """The document with lines replaced (None removes a line) and new lines inserted
        after / before existing line numbers. Everything else is written back as read."""
        replacements, after, before = replacements or {}, after or {}, before or {}
        eol = self.default_eol()
        out = []
        for no, (line, line_eol) in enumerate(zip(self.lines, self.eols)):
            for new in before.get(no, ()):
                out += [new, eol]
            removed = no in replacements and replacements[no] is None
            if not removed:
                out += [replacements.get(no, line), line_eol]
            new_lines = after.get(no)
            if new_lines:
                if line_eol:
                    for new in new_lines:
                        out += [new, eol]
                else:                   # the file's last line, without a line break
                    out += ([] if removed else [eol]) + [eol.join(new_lines)]
        return self.bom + "".join(out).encode(self.encoding, "surrogateescape")
