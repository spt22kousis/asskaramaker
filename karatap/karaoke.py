"""Karaoke line text: syllables, gap syllables, furigana, and rewriting \\k values.

A syllable starts at a \\k/\\kf/\\K/\\ko tag and runs until the next one.
Its visible text is everything outside override blocks up to the next tag.
Text is never modified; rewriting only replaces tag numbers and inserts new
`{\\kN}` blocks for gap syllables.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

KTAG_RE = re.compile(r"\\(kf|ko|k|K)(\d+)")
_INVISIBLE_RE = re.compile(r"\\[Nnh]")
_SIMPLE_SPAN = re.compile(r"^\{\\(kf|ko|k|K)(\d+)\}([^{}]*)$")
SMALL_KANA = set("ぁぃぅぇぉゃゅょゎァィゥェォャュョヮ")
SUNG_PUNCT = set("&＆")


def _is_filler(ch: str) -> bool:
    """Characters that are never sung on their own: whitespace and punctuation."""
    return ch.isspace() or (unicodedata.category(ch).startswith("P") and ch not in SUNG_PUNCT)
OPENING = set("「『（(“‘【〈《")
_ASCII_WORD = re.compile(r"[A-Za-z]")


@dataclass
class Block:
    start: int      # index of '{'
    end: int        # index after '}'
    has_k: bool


@dataclass
class Syllable:
    tag: str                 # 'k', 'kf', 'ko' or 'K'
    value: int               # centiseconds as written
    num_span: tuple[int, int]
    block: int               # index into KaraLine.blocks
    text: str = ""           # visible text, furigana syntax included

    @property
    def is_gap(self) -> bool:
        """Gaps are never tapped; the tool fills their time.

        A syllable is a gap when its text is empty or only spaces and
        punctuation (！ 「 " …). '&' is sung ("and") and symbols such as ♡
        may be sung, so those stay tappable.
        """
        return all(_is_filler(ch) for ch in _INVISIBLE_RE.sub("", self.text))


@dataclass
class KaraLine:
    text: str
    prefix: str              # visible text before the first \k tag
    syls: list[Syllable]
    blocks: list[Block]

    @classmethod
    def parse(cls, text: str) -> "KaraLine":
        syls: list[Syllable] = []
        blocks: list[Block] = []
        prefix_parts: list[str] = []
        i = 0
        n = len(text)
        while i < n:
            if text[i] == "{":
                j = text.find("}", i)
                end = n if j == -1 else j + 1
                body_start = i + 1
                body = text[body_start:end - 1 if j != -1 else n]
                bi = len(blocks)
                found = False
                for m in KTAG_RE.finditer(body):
                    found = True
                    syls.append(Syllable(m.group(1), int(m.group(2)),
                                         (body_start + m.start(2), body_start + m.end(2)), bi))
                blocks.append(Block(i, end, found))
                i = end
            else:
                j = text.find("{", i)
                j = n if j == -1 else j
                if syls:
                    syls[-1].text += text[i:j]
                else:
                    prefix_parts.append(text[i:j])
                i = j
        return cls(text, "".join(prefix_parts), syls, blocks)

    def tappable(self) -> list[int]:
        return [i for i, s in enumerate(self.syls) if not s.is_gap]

    def insert_pos(self, syl_index: int) -> int:
        """Where a new syllable goes so that it ends up right before syls[syl_index].

        It is placed before any tag-only blocks (e.g. `{\\-A}`) that directly
        precede that syllable's tag, so inline-fx markers stay attached to the
        syllable they were written for.
        """
        bi = self.syls[syl_index].block
        while (bi > 0 and not self.blocks[bi - 1].has_k
               and self.blocks[bi - 1].end == self.blocks[bi].start):
            bi -= 1
        return self.blocks[bi].start

    def rebuild(self, values: list[int], inserts: dict[int, int] | None = None) -> str:
        """New text with syllable values replaced and gap syllables inserted.

        `inserts` maps a syllable index to the value of a new empty syllable
        inserted right before it.
        """
        if len(values) != len(self.syls):
            raise ValueError("values must match syllable count")
        inserts = inserts or {}
        edits: list[tuple[int, int, str]] = []   # (start, end, replacement)
        for syl, v in zip(self.syls, values):
            if v < 0:
                raise ValueError(f"negative \\k value {v}")
            edits.append((syl.num_span[0], syl.num_span[1], str(v)))
        for idx, v in inserts.items():
            pos = self.insert_pos(idx)
            edits.append((pos, pos, "{\\%s%d}" % (self.syls[idx].tag, v)))
        out = []
        last = 0
        # Insertions sort before replacements at the same position because their span is empty.
        for start, end, rep in sorted(edits, key=lambda e: (e[0], e[1])):
            out.append(self.text[last:start])
            out.append(rep)
            last = end
        out.append(self.text[last:])
        return "".join(out)


def atoms(text: str) -> list[str]:
    """Smallest pieces a syllable can be split into, concatenating back to `text`.

    Each kana / kanji / digit is a piece; small kana join the previous one;
    an English word stays whole; punctuation and spaces join a neighbour
    (opening brackets the next piece, everything else the previous one).
    """
    out: list[str] = []
    pending = ""            # punctuation waiting for the next piece
    word = False            # last piece is an English word still being read
    for ch in text:
        if ch in SMALL_KANA and out and not pending:
            out[-1] += ch
            word = False
        elif _ASCII_WORD.match(ch) or (ch == "'" and word):
            if word and not pending:
                out[-1] += ch
            else:
                out.append(pending + ch)
                pending = ""
                word = True
        elif ch.isalnum():
            out.append(pending + ch)
            pending = ""
            word = False
        else:
            if ch in OPENING or not out:
                pending += ch
            elif pending:
                pending += ch
            else:
                out[-1] += ch
            word = False
    if pending:
        if out:
            out[-1] += pending
        else:
            out.append(pending)
    return out


def _spans(line: KaraLine) -> list[tuple[int, int]]:
    starts = [line.blocks[s.block].start for s in line.syls]
    return list(zip(starts, starts[1:] + [len(line.text)]))


def _ruby_parts(text: str) -> tuple[str, str, str] | None:
    """'排|<は' -> ('排', '|<', 'は'); None for plain text."""
    if "|" not in text:
        return None
    base, rest = text.split("|", 1)
    sep = "|<" if rest.startswith("<") else "|"
    return base, sep, rest[len(sep) - 1:]


def split_points(line: KaraLine, i: int) -> list[str]:
    """Pieces syllable i can be split into: reading pieces for ruby, text pieces otherwise."""
    syl = line.syls[i]
    if syl.is_gap:
        return []
    rp = _ruby_parts(syl.text)
    return atoms(rp[2]) if rp else atoms(syl.text)


def split_syllable(line: KaraLine, i: int, at: int) -> str | None:
    """Text with syllable i split before its piece number `at`; None if not possible."""
    pieces = split_points(line, i)
    a, b = _spans(line)[i]
    m = _SIMPLE_SPAN.match(line.text[a:b])
    if not m or not 0 < at < len(pieces):
        return None
    syl = line.syls[i]
    rp = _ruby_parts(syl.text)
    if rp:
        base, sep, _ = rp
        first, second = base + sep + "".join(pieces[:at]), "#" + sep + "".join(pieces[at:])
    else:
        first, second = "".join(pieces[:at]), "".join(pieces[at:])
    v1, v2 = (syl.value + 1) // 2, syl.value // 2
    new = "{\\%s%d}%s{\\%s%d}%s" % (syl.tag, v1, first, syl.tag, v2, second)
    return line.text[:a] + new + line.text[b:]


def merge_syllables(line: KaraLine, i: int) -> str | None:
    """Text with syllables i and i+1 joined into one; None if the syntax can't express it."""
    if i + 1 >= len(line.syls):
        return None
    s1, s2 = line.syls[i], line.syls[i + 1]
    if s1.is_gap or s2.is_gap:
        return None
    (a, _), (b, c) = _spans(line)[i], _spans(line)[i + 1]
    if not (_SIMPLE_SPAN.match(line.text[a:b]) and _SIMPLE_SPAN.match(line.text[b:c])):
        return None
    r1, r2 = _ruby_parts(s1.text), _ruby_parts(s2.text)
    if r1 is None and r2 is None:
        joined = s1.text + s2.text
    elif r1 and r2 and r2[0] == "#":
        joined = s1.text + r2[2]                       # 新|<し + #|<ん -> 新|<しん
    elif r1 and r2 and r1[0] != "#":
        joined = r1[0] + r2[0] + r1[1] + r1[2] + r2[2]  # 新|<しん + 作|<さく -> 新作|<しんさく
    else:
        return None                                    # ruby next to plain text
    return line.text[:a] + "{\\%s%d}%s" % (s1.tag, s1.value + s2.value, joined) + line.text[c:]


@dataclass
class Furi:
    base: str              # text to show big (continuations resolved to their kanji)
    ruby: str              # reading for this syllable ('' if none)
    continuation: bool     # True for '#|<...' beats
    group: int             # syllable index that starts this kanji group


def split_furigana(text: str) -> tuple[str, str]:
    """'排|<は' -> ('排', 'は'); 'の ' -> ('の', '')."""
    t = text.strip()
    if "|" in t:
        base, ruby = t.split("|", 1)
        if ruby.startswith("<"):
            ruby = ruby[1:]
        return base, ruby
    return t, ""


def furigana_for(line: KaraLine) -> list[Furi]:
    """Display info per syllable; '#' beats inherit the kanji of their group."""
    out: list[Furi] = []
    group = 0
    group_base = ""
    for i, syl in enumerate(line.syls):
        base, ruby = split_furigana(syl.text)
        if syl.is_gap:
            out.append(Furi(base, ruby, False, i))
            continue
        if base == "#" and out:
            out.append(Furi(group_base, ruby, True, group))
            continue
        group = i
        group_base = base
        out.append(Furi(base, ruby, False, group))
    return out
