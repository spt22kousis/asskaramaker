"""Splitting a word's furigana over its kanji.

    大熱唱|だいねっしょう -> 大|だい 熱|ねっ 唱|しょう

Every kanji gets a reading from KANJIDIC (on'yomi, kun'yomi stems, and verb
stems such as 語(かた.る) -> かたり as in 物語), allowing the usual sound
changes: rendaku (は -> ば/ぱ), and a final つ/く/ち/き becoming っ. Kana inside the word must match the reading literally.
Words no split explains (jukujikun such as 今日|きょう) stay whole.
"""
from __future__ import annotations

import functools
import unicodedata
from dataclasses import dataclass
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data" / "kanji_readings.txt"

AUTO, AMBIGUOUS, WHOLE = "auto", "ambiguous", "whole"

_VOICED = dict(zip("かきくけこさしすせそたちつてとはひふへほ", "がぎぐげござじずぜぞだぢづでどばびぶべぼ"))
_VOICED_ALT = {"ち": "じ", "つ": "ず"}
_SEMI = dict(zip("はひふへほ", "ぱぴぷぺぽ"))
_SOKUON = set("つくちき")
_U_TO_I = dict(zip("うくぐすつぬぶむる", "いきぎしちにびみり"))
_ITERATION = set("々〻")
_DIGITS = {
    0: ("ぜろ", "れい"), 1: ("いち", "いっ"), 2: ("に",), 3: ("さん", "ざん"), 4: ("よん", "し", "よ"),
    5: ("ご",), 6: ("ろく", "ろっ"), 7: ("なな", "しち"), 8: ("はち", "はっ"), 9: ("きゅう", "く", "きゅー"),
}
_MAX_RESULTS = 50

Piece = tuple[str, str]       # (text, reading); reading '' for kana that is read as written


def to_hiragana(s: str) -> str:
    return "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in s)


def is_kana(ch: str) -> bool:
    return "ぁ" <= ch <= "ゖ" or "ァ" <= ch <= "ヺ" or ch == "ー"


def is_kanji(ch: str) -> bool:
    return ch in _ITERATION or unicodedata.name(ch, "").startswith("CJK")


@functools.lru_cache(maxsize=1)
def kanji_table() -> dict[str, tuple[str, ...]]:
    table: dict[str, tuple[str, ...]] = {}
    for line in DATA.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            k, readings = line.split("\t")
            table[k] = tuple(readings.split())
    return table


def variants(reading: str) -> list[tuple[str, int]]:
    """A KANJIDIC reading's forms inside a word, with a cost (number of changes).

    'かた.る' gives the stem かた and the verb stem かたり; each form may also
    be voiced at the start or end in っ.
    """
    out: dict[str, int] = {}

    def add(r: str, cost: int) -> None:
        if r and (r not in out or out[r] > cost):
            out[r] = cost

    stem, _, oku = reading.partition(".")
    bases = [(stem, 0)]
    if oku and oku[-1] in _U_TO_I:
        bases.append((stem + oku[:-1] + _U_TO_I[oku[-1]], 1))
    for base, c0 in bases:
        firsts = [(base, c0)]
        for table in (_VOICED, _VOICED_ALT, _SEMI):
            if base[:1] in table:
                firsts.append((table[base[0]] + base[1:], c0 + 1))
        for r, c in firsts:
            add(r, c)
            if len(r) > 1 and r[-1] in _SOKUON:
                add(r[:-1] + "っ", c + 1)
    return sorted(out.items(), key=lambda kv: kv[1])


def _options(base: str, i: int) -> list[tuple[str, int]]:
    ch = base[i]
    if ch in _ITERATION:
        prev = base[i - 1] if i else ""
        return [v for r in kanji_table().get(prev, ()) for v in variants(r)]
    d = unicodedata.digit(ch, None)
    if d is not None:
        return [(r, 0) for r in _DIGITS[d]]
    return [v for r in kanji_table().get(ch, ()) for v in variants(r)]


def align(base: str, reading: str) -> list[tuple[int, list[Piece]]]:
    """Every way to give each character of `base` its part of `reading`, cheapest first."""
    norm = to_hiragana(reading)
    found: dict[tuple[Piece, ...], int] = {}

    def rec(i: int, j: int, pieces: list[Piece], cost: int) -> None:
        if len(found) >= _MAX_RESULTS:
            return
        if i == len(base):
            if j == len(norm):
                key = tuple(pieces)
                found[key] = min(cost, found.get(key, cost))
            return
        ch = base[i]
        if is_kana(ch):
            if j < len(norm) and to_hiragana(ch) == norm[j]:
                rec(i + 1, j + 1, pieces + [(ch, "")], cost)
            return
        for v, c in _options(base, i):
            if norm.startswith(v, j):
                rec(i + 1, j + len(v), pieces + [(ch, reading[j:j + len(v)])], cost + c)

    rec(0, 0, [], 0)
    return sorted(((c, list(p)) for p, c in found.items()), key=lambda cp: cp[0])


@dataclass
class Distribution:
    state: str                    # AUTO, AMBIGUOUS or WHOLE
    options: list[list[Piece]]    # options[0] is the default


def distribute(base: str, reading: str) -> Distribution:
    """How to split the furigana `reading` of the word `base` into one piece per character."""
    if to_hiragana(base) == to_hiragana(reading):          # ruby that just repeats kana
        return Distribution(AUTO, [[(ch, "") for ch in base]])
    # Kana at the ends that the reading repeats (今日も|きょうも) are read as written.
    lead: list[Piece] = []
    tail: list[Piece] = []
    b, r = base, reading
    while len(b) > 1 and r and is_kana(b[0]) and to_hiragana(b[0]) == to_hiragana(r[0]):
        lead.append((b[0], ""))
        b, r = b[1:], r[1:]
    while len(b) > 1 and r and is_kana(b[-1]) and to_hiragana(b[-1]) == to_hiragana(r[-1]):
        tail.insert(0, (b[-1], ""))
        b, r = b[:-1], r[:-1]
    if not r:
        return Distribution(WHOLE, [[(base, reading)]])
    if len(b) == 1:
        return Distribution(AUTO, [lead + [(b, r)] + tail])
    found = align(b, r)
    if not found:
        return Distribution(WHOLE, [lead + [(b, r)] + tail])
    state = AUTO if len(found) == 1 or found[0][0] < found[1][0] else AMBIGUOUS
    options = [lead + pieces + tail for _, pieces in found]
    options.append(lead + [(b, r)] + tail)          # keeping the word whole is always possible
    return Distribution(state, options)
