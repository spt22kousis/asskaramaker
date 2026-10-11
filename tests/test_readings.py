import pytest

from karatap.readings import AMBIGUOUS, AUTO, WHOLE, distribute, variants


def split(base, reading):
    d = distribute(base, reading)
    return d.state, " ".join(f"{t}|{r}" for t, r in d.options[0])


@pytest.mark.parametrize("base,reading,expected", [
    ("大熱唱", "だいねっしょう", "大|だい 熱|ねっ 唱|しょう"),     # ねつ -> ねっ
    ("新作動画", "しんさくどうが", "新|しん 作|さく 動|どう 画|が"),
    ("半端", "はんぱ", "半|はん 端|ぱ"),                          # は -> ぱ
    ("人々", "ひとびと", "人|ひと 々|びと"),                       # 々 repeats with rendaku
    ("物語", "ものがたり", "物|もの 語|がたり"),                   # verb stem of かた.る
    ("学校", "がっこう", "学|がっ 校|こう"),
    ("39", "さんきゅー", "3|さん 9|きゅー"),
])
def test_word_splits_per_kanji(base, reading, expected):
    assert split(base, reading) == (AUTO, expected)


def test_kana_in_the_word_is_read_as_written():
    assert split("見つめ", "みつめ") == (AUTO, "見|み つ| め|")


def test_jukujikun_stays_whole():
    assert split("今日", "きょう") == (WHOLE, "今日|きょう")
    assert split("大人", "おとな") == (WHOLE, "大人|おとな")
    # trailing kana that the reading repeats is split off even then
    assert split("今日も", "きょうも") == (WHOLE, "今日|きょう も|")


def test_ruby_that_repeats_kana_is_dropped():
    assert split("ああ", "ああ") == (AUTO, "あ| あ|")


def test_ties_are_ambiguous_and_whole_is_always_an_option():
    d = distribute("恋気", "こいき")
    assert d.state == AMBIGUOUS
    opts = [" ".join(f"{t}|{r}" for t, r in o) for o in d.options]
    assert {"恋|こ 気|いき", "恋|こい 気|き"} <= set(opts[:2])
    assert opts[-1] == "恋気|こいき"


def test_variants():
    v = dict(variants("かた.る"))
    assert v["かた"] == 0 and v["がた"] == 1 and v["かたり"] == 1 and v["がたり"] == 2
    assert dict(variants("ねつ"))["ねっ"] == 1
