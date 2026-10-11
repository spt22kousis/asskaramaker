"""Lyrics from a web page or text -> karaoke lines, one syllable per character.

Accepted input:
- HTML: Utaten (div.hiragana with span.ruby/rb/rt), Moegirl (div.Lyrics-original
  with <ruby>), or any fragment with <ruby> tags, e.g. lyrics copied in a browser;
- karaoke text ({\\k1}新作|<しんさく{\\k1}...), e.g. the notebook's or an LLM's output;
- plain text, where [漢字|かな] marks furigana (the lyrics editor uses this).

Word furigana is split per kanji with `readings.distribute`; the rest follows
the same rules as llm_syllable_split_prompt.md: each kana / kanji / digit is a
syllable, small kana join the previous one, an English word stays whole,
spaces are their own syllable, punctuation sticks to a neighbour unless that
neighbour has furigana.
"""
from __future__ import annotations

import re
import urllib.request
from dataclasses import dataclass, field
from html.parser import HTMLParser

from .karaoke import KaraLine, _is_filler, atoms
from .readings import AMBIGUOUS, Distribution, Piece, distribute

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

LLM_READINGS_PROMPT = """\
你是日文歌詞的注音工具。替下面每一行歌詞裡的漢字標上讀音（平假名），格式是 [漢字|かな]：
- 逐字標：每個漢字各自一組，例如 [新|しん][作|さく]。熟字訓、當て字等無法逐字對應的，整組一起標，例如 [今日|きょう]。
- 送り仮名、假名、英文、數字、標點、空白都原樣保留，不要加 [ ]。
- 讀音照歌曲實際的唱法；不確定時用最常見的讀法。
- 一行輸入對應一行輸出，行數、順序、文字都不能改。
- 只輸出結果，不要解釋，不要用 code block。

歌詞：
{lyrics}"""


@dataclass
class Seg:
    """A piece of parsed lyrics: plain text, or a word with its furigana."""
    base: str
    ruby: str = ""


# Characters that would break the ASS / furigana syntax.
_UNSAFE = str.maketrans({"{": "｛", "}": "｝", "\\": "＼", "|": "｜"})


def _clean(s: str) -> str:
    return s.translate(_UNSAFE)


# -- HTML ----------------------------------------------------------------------------

_VOID = {"br", "img", "hr", "meta", "link", "input", "wbr", "area", "base", "col", "embed",
         "source", "track", "param"}
_SKIP = {"script", "style", "rp", "head", "title", "noscript", "template", "button"}
_BLOCK = {"div", "p", "li", "ul", "ol", "tr", "table", "section", "article", "dd", "dt",
          "blockquote", "h1", "h2", "h3", "h4", "h5", "h6"}
_PARENS = str.maketrans("", "", "（）()")


class _Node:
    __slots__ = ("tag", "classes", "children")

    def __init__(self, tag: str, classes: set[str]):
        self.tag = tag
        self.classes = classes
        self.children: list[_Node | str] = []


class _TreeBuilder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node("#root", set())
        self.stack = [self.root]

    def _node(self, tag, attrs) -> _Node:
        node = _Node(tag, set((dict(attrs).get("class") or "").split()))
        self.stack[-1].children.append(node)
        return node

    def handle_starttag(self, tag, attrs):
        node = self._node(tag, attrs)
        if tag not in _VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self._node(tag, attrs)

    def handle_endtag(self, tag):
        for k in range(len(self.stack) - 1, 0, -1):
            if self.stack[k].tag == tag:
                del self.stack[k:]
                return

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def _find(node: _Node, pred, first: bool = False) -> list[_Node]:
    out: list[_Node] = []
    todo = [node]
    while todo:
        n = todo.pop(0)
        for c in n.children:
            if isinstance(c, str):
                continue
            if pred(c):
                out.append(c)
                if first:
                    return out
            else:
                todo.append(c)
    return out


def _text(node: _Node) -> str:
    parts = []
    for c in node.children:
        if isinstance(c, str):
            parts.append(c)
        elif c.tag not in _SKIP:
            parts.append(_text(c))
    return "".join(parts)


def _is_ruby(n: _Node) -> bool:
    return n.tag == "ruby" or "ruby" in n.classes


def _is_rt(n: _Node) -> bool:
    return n.tag == "rt" or "rt" in n.classes


def _ruby_seg(n: _Node) -> Seg:
    base, ruby = [], []
    for c in n.children:
        if isinstance(c, str):
            base.append(c)
        elif _is_rt(c):
            ruby.append(_text(c))
        elif c.tag not in _SKIP:
            base.append(_text(c))
    return Seg("".join("".join(base).split()), "".join("".join(ruby).split()).translate(_PARENS))


_NL = Seg("\n")     # line break marker (identity-compared)


def _walk(node: _Node, out: list[Seg]) -> None:
    for c in node.children:
        if isinstance(c, str):
            out.append(Seg(c))
        elif c.tag in _SKIP or "Lyrics-translated" in c.classes:
            continue
        elif c.tag == "br":
            out.append(_NL)
        elif _is_ruby(c):
            out.append(_ruby_seg(c))
        else:
            block = c.tag in _BLOCK
            if block:
                out.append(_NL)
            _walk(c, out)
            if block:
                out.append(_NL)


def parse_html(html: str) -> list[list[Seg]]:
    b = _TreeBuilder()
    b.feed(html)
    b.close()
    roots = (_find(b.root, lambda n: n.tag == "div" and "hiragana" in n.classes, first=True)     # Utaten
             or _find(b.root, lambda n: "Lyrics-original" in n.classes)                         # Moegirl
             or [b.root])
    items: list[Seg] = []
    for r in roots:
        items.append(_NL)
        _walk(r, items)
        items.append(_NL)
    lines: list[list[Seg]] = []
    cur: list[Seg] = []
    for it in items:
        if it is _NL:
            if cur:
                lines.append(cur)
            cur = []
        else:
            # In HTML a newline in the text is just a space; only <br> and blocks break lines.
            cur.append(it if it.ruby else Seg(" ".join(it.base.split("\n"))))
    if cur:
        lines.append(cur)
    return [segs for segs in lines if any(s.base.strip() for s in segs)]


def looks_like_html(text: str) -> bool:
    return re.search(r"<(div|span|ruby|br|p|rt)\b", text, re.IGNORECASE) is not None


# -- text ----------------------------------------------------------------------------

_EDIT_RUBY = re.compile(r"\[([^\[\]|｜]+)[|｜]([^\[\]|｜]*)\]")
_EVENT_PREFIX = re.compile(r"^\s*(Dialogue|Comment)\s*:", re.IGNORECASE)


def _parse_edit(line: str) -> list[Seg]:
    segs: list[Seg] = []
    pos = 0
    for m in _EDIT_RUBY.finditer(line):
        if m.start() > pos:
            segs.append(Seg(line[pos:m.start()]))
        segs.append(Seg(m.group(1), m.group(2)))
        pos = m.end()
    if pos < len(line):
        segs.append(Seg(line[pos:]))
    return segs


def _parse_kara(line: str) -> list[Seg]:
    k = KaraLine.parse(line)
    segs = [Seg(k.prefix)] if k.prefix else []
    for syl in k.syls:
        t = re.sub(r"\\[Nnh]", " ", syl.text)
        if "|" not in t:
            segs.append(Seg(t))
            continue
        base, rest = t.split("|", 1)
        raw = rest[1:] if rest.startswith("<") else rest
        ruby = raw.rstrip()
        if base == "#" and segs and segs[-1].ruby:
            segs[-1].ruby += ruby                   # another beat of the same kanji
        else:
            segs.append(Seg(base, ruby))
        if raw[len(ruby):]:
            segs.append(Seg(raw[len(ruby):]))
    return segs


def parse_text(text: str) -> list[list[Seg]]:
    lines = []
    for raw in text.splitlines():
        if raw.lstrip().startswith("```"):
            continue                                # an LLM's code fence
        if _EVENT_PREFIX.match(raw):
            parts = raw.split(",", 9)
            raw = parts[9] if len(parts) == 10 else raw
        segs = _parse_kara(raw) if ("{\\" in raw or "|<" in raw) else _parse_edit(raw)
        if any(s.base.strip() for s in segs):
            lines.append(segs)
    return lines


def parse_any(text: str, html: str | None = None) -> list[list[Seg]]:
    if html:
        return parse_html(html)
    return parse_html(text) if looks_like_html(text) else parse_text(text)


def fetch_url(url: str, timeout: float = 20) -> str:
    if not re.match(r"https?://", url.strip(), re.IGNORECASE):
        raise ValueError("網址要以 http:// 或 https:// 開頭")
    req = urllib.request.Request(url.strip(), headers={"User-Agent": USER_AGENT,
                                                       "Accept-Language": "ja,zh-TW;q=0.8,en;q=0.5"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read(5_000_000)
        charset = resp.headers.get_content_charset() or "utf-8"
    return data.decode(charset, "replace")


# -- lines and syllables ---------------------------------------------------------------

@dataclass
class Group:
    """A word with furigana and how its reading is split over the characters."""
    base: str
    ruby: str
    dist: Distribution
    choice: int = 0
    confirmed: bool = False

    @property
    def pieces(self) -> list[Piece]:
        return self.dist.options[self.choice]

    @property
    def state(self) -> str:
        return self.dist.state

    @property
    def needs_check(self) -> bool:
        return self.dist.state == AMBIGUOUS and not self.confirmed


@dataclass
class LyricLine:
    items: list[str | Group] = field(default_factory=list)

    @property
    def groups(self) -> list[Group]:
        return [it for it in self.items if isinstance(it, Group)]


def make_line(segs: list[Seg], previous: LyricLine | None = None) -> LyricLine:
    """Build a line; choices made on `previous` carry over to identical words."""
    known = {(g.base, g.ruby): g for g in previous.groups} if previous else {}
    items: list[str | Group] = []

    def plain(s: str) -> None:
        if not s:
            return
        if items and isinstance(items[-1], str):
            items[-1] += s
        else:
            items.append(s)

    for s in segs:
        base, ruby = _clean(s.base), _clean(s.ruby).strip()
        core = base.strip()
        if not ruby or not core:
            plain(base)
            continue
        plain(base[:len(base) - len(base.lstrip())])
        old = known.get((core, ruby))
        g = Group(core, ruby, distribute(core, ruby))
        if old and old.choice < len(g.dist.options):
            g.choice, g.confirmed = old.choice, old.confirmed
        items.append(g)
        plain(base[len(base.rstrip()):])

    items = [re.sub(r"\s+", " ", it) if isinstance(it, str) else it for it in items]
    if items and isinstance(items[0], str):
        items[0] = items[0].lstrip()
    if items and isinstance(items[-1], str):
        items[-1] = items[-1].rstrip()
    return LyricLine([it for it in items if it != ""])


def plain_units(text: str) -> list[str]:
    """Syllables of plain text: spaces on their own, the rest split by `atoms`.

    atoms() sticks punctuation to the next piece when there is none before it,
    which is what the rules want next to furigana: a ！ right after a word
    with furigana goes with what follows, or stands alone before a space.
    """
    out: list[str] = []
    for chunk in re.split(r"( )", text):
        if chunk == " ":
            out.append(chunk)
        elif chunk:
            out.extend(atoms(chunk))
    return out


def units(line: LyricLine) -> list[tuple[str, str]]:
    """The line's syllables as (text, furigana)."""
    seq: list[str | tuple[str, str]] = []
    for it in line.items:
        pieces = [(it, "")] if isinstance(it, str) else it.pieces
        for text, ruby in pieces:
            if ruby:
                seq.append((text, ruby))
            elif seq and isinstance(seq[-1], str):
                seq[-1] += text
            else:
                seq.append(text)
    out: list[tuple[str, str]] = []
    for x in seq:
        if isinstance(x, str):
            out.extend((u, "") for u in plain_units(x))
        else:
            out.append(x)
    return out


def is_gap_text(text: str) -> bool:
    return all(_is_filler(ch) for ch in text)


def has_taps(line: LyricLine) -> bool:
    return any(not is_gap_text(t) for t, _ in units(line))


def to_kara_text(line: LyricLine) -> str:
    return "".join("{\\k1}" + (f"{t}|<{r}" if r else t) for t, r in units(line))


def to_editor_text(line: LyricLine) -> str:
    parts = []
    for it in line.items:
        if isinstance(it, str):
            parts.append(it)
        else:
            parts.extend(f"[{t}|{r}]" if r else t for t, r in it.pieces)
    return "".join(parts)


def plain_text(line: LyricLine) -> str:
    return "".join(it if isinstance(it, str) else it.base for it in line.items)
