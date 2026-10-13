"""A new karaoke project: an earlier song's ASS as the template plus new lyrics.

From the template the new file keeps everything except its events: of those
only the karaoke templater's own lines (Effect starting with template, code or
mixin) stay. The Aegisub project section points at the new media, so Aegisub
opens the video with the file. The lyrics become `Comment` lines with Effect
`karaoke` and zero times; timing happens in the tool.

Without a template of their own, projects use the built-in one
(data/default_template.ass: the club's styles and template / code lines).
"""
from __future__ import annotations

import os
from collections import Counter
from pathlib import Path

from .ass_doc import _SECTION_RE, DEFAULT_EVENT_FORMAT, AssDocument, Event

TEMPLATE_CLASSES = ("template", "code", "mixin")
GARBAGE = "aegisub project garbage"
STALE_KEYS = {"audio file", "video file", "keyframes file", "timecodes file", "video position",
              "active line", "scroll position"}
VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".mov", ".avi", ".m2ts", ".ts", ".flv", ".wmv"}
DEFAULT_TEMPLATE = Path(__file__).parent / "data" / "default_template.ass"
# Style names the layout picks by default (the default template's first, then the notebook's).
ODD_STYLES, EVEN_STYLES, SINGLE_STYLES = ("Odd", "K1"), ("Even", "K2"), ("Default", "K0")


def default_template() -> AssDocument:
    return AssDocument.load(DEFAULT_TEMPLATE)


def is_template_line(ev: Event) -> bool:
    words = ev.effect.strip().lower().split()
    return bool(words) and words[0] in TEMPLATE_CLASSES


def media_ref(media: Path, ass_path: Path) -> str:
    """Media path as Aegisub stores it: relative to the ASS when possible."""
    try:
        return os.path.relpath(media, ass_path.parent)
    except ValueError:          # another drive
        return str(media)


def pick_style(names: list[str], preferred: tuple[str, ...]) -> str:
    for p in preferred:
        if p in names:
            return p
    return names[0] if names else "Default"


def karaoke_event(fmt: list[str], style: str, text: str) -> str:
    values = {"layer": "0", "start": "0:00:00.00", "end": "0:00:00.00", "style": style, "name": "",
              "marginl": "0", "marginr": "0", "marginv": "0", "effect": "karaoke", "text": text,
              "marked": "0"}
    return "Comment: " + ",".join(values.get(f, "") for f in fmt)


def _pop_blanks(out: list[str]) -> int:
    n = 0
    while out and not out[-1].strip():
        out.pop()
        n += 1
    return n


def new_project_bytes(template: AssDocument, media: Path, ass_path: Path, texts: list[str]) -> bytes:
    style = pick_style(template.style_names(), ODD_STYLES + SINGLE_STYLES)
    eols = Counter(e for e in template.eols if e)
    eol = eols.most_common(1)[0][0] if eols else "\r\n"
    drop = {e.line_no for e in template.events if not is_template_line(e)}
    sections = [m.group(1).strip().lower() for m in map(_SECTION_RE.match, template.lines) if m]
    ref = media_ref(media, ass_path)
    media_lines = [f"Audio File: {ref}"]
    if media.suffix.lower() in VIDEO_EXTS:
        media_lines.append(f"Video File: {ref}")

    out: list[str] = []
    fmt = list(DEFAULT_EVENT_FORMAT)
    wrote_events = False

    def write_events() -> None:
        nonlocal wrote_events
        blanks = _pop_blanks(out)
        out.extend(karaoke_event(fmt, style, t) for t in texts)
        out.extend([""] * blanks)
        wrote_events = True

    section = None
    for no, line in enumerate(template.lines):
        m = _SECTION_RE.match(line)
        if m:
            if section == "events":
                write_events()
            if section == "script info" and GARBAGE not in sections:
                blanks = _pop_blanks(out)       # Aegisub keeps its section right after [Script Info]
                out += ["", "[Aegisub Project Garbage]", *media_lines] + [""] * max(1, blanks)
            section = m.group(1).strip().lower()
            out.append(line)
            if section == GARBAGE:
                out.extend(media_lines)
            continue
        if section == GARBAGE and ":" in line and line.split(":", 1)[0].strip().lower() in STALE_KEYS:
            continue
        if section == "events":
            if no in drop:
                continue
            if line.lower().startswith("format:"):
                fmt = [f.strip().lower() for f in line.split(":", 1)[1].split(",")]
        out.append(line)
    if section == "events":
        write_events()
    if not wrote_events:
        _pop_blanks(out)
        out += ["", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
        out.extend(karaoke_event(fmt, style, t) for t in texts)
    text = eol.join(out) + eol
    return template.bom + text.encode(template.encoding, "surrogateescape")


def _section_format(doc: AssDocument, names: tuple[str, ...]) -> tuple[list[str], int | None, int | None]:
    """A section's Format fields, the line number of its Format line and of its last line with content."""
    fmt, fmt_no, last, cur = [], None, None, None
    for no, line in enumerate(doc.lines):
        m = _SECTION_RE.match(line)
        if m:
            cur = m.group(1).strip().lower()
            continue
        if cur not in names or not line.strip():
            continue
        last = no
        if line.lower().startswith("format:"):
            fmt = [f.strip().lower() for f in line.split(":", 1)[1].split(",")]
            fmt_no = no
    return fmt, fmt_no, last


def add_template_lines(data: bytes, template: AssDocument) -> bytes:
    """The file with the template's template / code / mixin lines added before its first event,
    and the template's styles it lacks added after its own."""
    doc = AssDocument(data)
    after: dict[int, list[str]] = {}
    before: dict[int, list[str]] = {}
    style_sections = ("v4+ styles", "v4 styles")
    have = set(doc.style_names())
    fmt, _, last = _section_format(doc, style_sections)
    tfmt, _, _ = _section_format(template, style_sections)
    new_styles = []
    for line in template.lines:
        if line.lower().startswith("style:"):
            values = [v.strip() for v in line.split(":", 1)[1].split(",", len(tfmt) - 1)]
            if values and values[0] not in have:
                by_name = dict(zip(tfmt, values))
                new_styles.append("Style: " + ",".join(by_name.get(f, "") for f in fmt))
    if new_styles and last is not None and fmt:
        after[last] = new_styles
    efmt, efmt_no, _ = _section_format(doc, ("events",))
    efmt = efmt or list(DEFAULT_EVENT_FORMAT)
    lines = [f"{e.kind}: " + ",".join(e.get(f) if f in e.fmt else "" for f in efmt)
             for e in template.events if is_template_line(e)]
    if lines:
        if doc.events:
            before.setdefault(doc.events[0].line_no, []).extend(lines)
        elif efmt_no is not None:
            after.setdefault(efmt_no, []).extend(lines)
    return doc.to_bytes(after=after, before=before)
