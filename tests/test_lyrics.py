from conftest import EXAMPLES

import pytest

from karatap.ass_doc import AssDocument
from karatap.lyrics import (has_taps, make_line, parse_any, parse_html, parse_text, plain_text,
                            to_editor_text, to_kara_text, units)
from karatap.readings import AMBIGUOUS, WHOLE


def kara(segs_lines):
    return [to_kara_text(make_line(s)) for s in segs_lines]


UTATEN = """<html><body><div class="lyricBody"><div class="hiragana">
<span class="ruby"><span class="rb">大熱唱</span><span class="rt">だいねっしょう</span></span>！ Hey Making
<br>
<span class="ruby"><span class="rb">今日</span><span class="rt">きょう</span></span>も
<span class="ruby"><span class="rb">見</span><span class="rt">み</span></span>つめてる<br>
</div><div class="romaji">daineshou</div></div></body></html>"""

MOEGIRL = """<div class="Lyrics Lyrics-has-ruby"><div class="Lyrics-line">
<div class="Lyrics-original"><ruby><rb>空</rb><rp>（</rp><rt>そら</rt><rp>）</rp></ruby>が<ruby><rb>青</rb><rp>（</rp><rt>あお</rt><rp>）</rp></ruby>い</div>
<div class="Lyrics-translated">天空很藍</div></div>
<div class="Lyrics-line"><div class="Lyrics-original">ラララ</div><div class="Lyrics-translated">啦啦啦</div></div></div>"""


def test_utaten_html():
    lines = parse_html(UTATEN)
    assert kara(lines) == [
        "{\\k1}大|<だい{\\k1}熱|<ねっ{\\k1}唱|<しょう{\\k1}！{\\k1} {\\k1}Hey{\\k1} {\\k1}Making",
        "{\\k1}今日|<きょう{\\k1}も{\\k1} {\\k1}見|<み{\\k1}つ{\\k1}め{\\k1}て{\\k1}る",
    ]


def test_moegirl_html_skips_translations():
    assert kara(parse_html(MOEGIRL)) == ["{\\k1}空|<そら{\\k1}が{\\k1}青|<あお{\\k1}い",
                                         "{\\k1}ラ{\\k1}ラ{\\k1}ラ"]


def test_copied_fragment_with_ruby_tags():
    html = "<p><ruby>新作<rt>しんさく</rt></ruby>だ</p><p>「ねえ」</p>"
    assert parse_any("", html=html) == parse_html(html)
    assert kara(parse_html(html)) == ["{\\k1}新|<しん{\\k1}作|<さく{\\k1}だ", "{\\k1}「ね{\\k1}え」"]


def test_notebook_output_is_resplit():
    lines = parse_text("{\\k1}大熱唱|<だいねっしょう{\\k1}{\\k1}! Hey Making Groovy{\\k1}\n"
                       "Comment: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,karaoke,{\\k1}半|<はん{\\k1}{\\k1}端|<ぱ{\\k1}{\\k1}な{\\k1}ら{\\k1} {\\k1}K.{\\k1}O.")
    assert kara(lines) == [
        "{\\k1}大|<だい{\\k1}熱|<ねっ{\\k1}唱|<しょう{\\k1}!{\\k1} {\\k1}Hey{\\k1} {\\k1}Making{\\k1} {\\k1}Groovy",
        "{\\k1}半|<はん{\\k1}端|<ぱ{\\k1}な{\\k1}ら{\\k1} {\\k1}K.{\\k1}O.",
    ]


def test_continuation_beats_join_their_kanji():
    lines = parse_text("{\\k18}排|<は{\\k18}#|<い{\\k22}気|<き")
    assert kara(lines) == ["{\\k1}排|<はい{\\k1}気|<き"]


def test_editor_syntax_and_llm_code_fences():
    lines = parse_text("```\n[空|そら]が[青|あお]い  　夜\n```")
    assert kara(lines) == ["{\\k1}空|<そら{\\k1}が{\\k1}青|<あお{\\k1}い{\\k1} {\\k1}夜"]


def test_punctuation_next_to_furigana_sticks_to_the_other_side():
    line = make_line(parse_text("「[夢|ゆめ]」の[中|なか]「[3|さん]")[0])
    assert units(line) == [("「", ""), ("夢", "ゆめ"), ("」の", ""), ("中", "なか"), ("「", ""), ("3", "さん")]
    line = make_line(parse_text('ル"[3|さん][9|きゅー]"に "MIRAI"を')[0])
    assert units(line) == [('ル"', ""), ("3", "さん"), ("9", "きゅー"), ('"に', ""), (" ", ""),
                           ('"MIRAI"', ""), ("を", "")]


def test_editor_text_round_trip_keeps_choices():
    line = make_line(parse_text("[恋気|こいき]と[大熱唱|だいねっしょう]")[0])
    amb, auto = line.groups
    assert amb.state == AMBIGUOUS and amb.needs_check
    auto.choice = len(auto.dist.options) - 1            # keep 大熱唱 whole
    text = to_editor_text(line)
    assert text == "[恋|こ][気|いき]と[大熱唱|だいねっしょう]"
    again = make_line(parse_text(text)[0], previous=line)
    assert units(again) == units(line)
    assert [g.needs_check for g in again.groups] == [False, False, False]


def test_whole_and_flags():
    line = make_line(parse_text("[今日|きょう]！")[0])
    assert line.groups[0].state == WHOLE
    assert plain_text(line) == "今日！"
    assert not has_taps(make_line(parse_text("！？")[0]))


def test_unsafe_characters_are_replaced():
    out = kara(parse_text("a{b}c\\d|e"))[0]
    assert out.replace("{\\k1}", "") == "a｛b｝c＼d｜e"


def test_existing_39music_lines_are_stable():
    """Lines already split by the rules come out unchanged."""
    path = EXAMPLES / "39music.ass"
    if not path.exists():
        pytest.skip("sample not present")
    doc = AssDocument.load(path)
    for ev in doc.karaoke_events():
        assert kara(parse_text(ev.text)) == [ev.text]
