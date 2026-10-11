"""Karaoke line text: syllables, gap syllables, furigana, and rewriting \\k values.

A syllable starts at a \\k/\\kf/\\K/\\ko tag and runs until the next one.
Its visible text is everything outside override blocks up to the next tag.
Text is never modified; rewriting only replaces tag numbers and inserts new
`{\\kN}` blocks for gap syllables.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

KTAG_RE = re.compile(r"\\(kf|ko|k|K)(\d+)")
_INVISIBLE_RE = re.compile(r"\\[Nnh]")


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
        """Empty or whitespace-only syllables are gaps: never tapped, filled by the tool."""
        return _INVISIBLE_RE.sub("", self.text).strip() == ""


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
