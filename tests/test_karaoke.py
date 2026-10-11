from karatap.karaoke import KaraLine, furigana_for, split_furigana

SNOOZE = "{\\-A}{\\k18}排|<は{\\k18}#|<い{\\k22}気|<き{\\k21}に{\\k15}酔|<よ{\\k17}っ"
SPACES = "{\\-A}{\\k19}あ{\\k39}の {\\k28}話|<は{\\k18}#|<な{\\k18}#|<し{\\k2} {\\k56}は"
NOTEBOOK = "{\\k1}新作動画|<しんさくどうが{\\k1}{\\k1}投稿|<とうこう{\\k1}{\\k1}だ{\\k1}"
MIDFX = "{\\-A}{\\k16}有|<う{\\k7} {\\-A}{\\k22}人|<ひ"


def test_parse_syllables():
    k = KaraLine.parse(SNOOZE)
    assert [s.text for s in k.syls] == ["排|<は", "#|<い", "気|<き", "に", "酔|<よ", "っ"]
    assert [s.value for s in k.syls] == [18, 18, 22, 21, 15, 17]
    assert k.prefix == ""
    assert not any(s.is_gap for s in k.syls)


def test_space_syllable_is_gap_but_trailing_space_is_not():
    k = KaraLine.parse(SPACES)
    texts = [s.text for s in k.syls]
    assert texts == ["あ", "の ", "話|<は", "#|<な", "#|<し", " ", "は"]
    assert [s.is_gap for s in k.syls] == [False, False, False, False, False, True, False]


def test_notebook_empty_syllables_are_gaps():
    k = KaraLine.parse(NOTEBOOK)
    assert [s.text for s in k.syls] == ["新作動画|<しんさくどうが", "", "投稿|<とうこう", "", "だ", ""]
    assert k.tappable() == [0, 2, 4]


def test_rebuild_identity_and_values():
    for text in (SNOOZE, SPACES, NOTEBOOK, MIDFX):
        k = KaraLine.parse(text)
        assert k.rebuild([s.value for s in k.syls]) == text
    k = KaraLine.parse(SNOOZE)
    assert k.rebuild([1, 2, 3, 4, 5, 6]) == \
        "{\\-A}{\\k1}排|<は{\\k2}#|<い{\\k3}気|<き{\\k4}に{\\k5}酔|<よ{\\k6}っ"


def test_insert_goes_before_inline_fx():
    k = KaraLine.parse(SNOOZE)
    out = k.rebuild([s.value for s in k.syls], {0: 30})
    assert out.startswith("{\\k30}{\\-A}{\\k18}排")
    k = KaraLine.parse(MIDFX)
    assert k.rebuild([16, 7, 22], {2: 4}) == "{\\-A}{\\k16}有|<う{\\k7} {\\k4}{\\-A}{\\k22}人|<ひ"


def test_tag_kinds_preserved_and_inserted_gap_uses_neighbour_kind():
    k = KaraLine.parse("{\\kf10}a{\\K20}b{\\ko5}c")
    assert [s.tag for s in k.syls] == ["kf", "K", "ko"]
    assert k.rebuild([1, 2, 3], {1: 9}) == "{\\kf1}a{\\K9}{\\K2}b{\\ko3}c"


def test_k_inside_mixed_block_and_non_k_tags():
    k = KaraLine.parse("{\\an8\\k10\\-B}a{\\kt5\\k20}b")
    assert [(s.value, s.text) for s in k.syls] == [(10, "a"), (20, "b")]
    assert k.rebuild([11, 22]) == "{\\an8\\k11\\-B}a{\\kt5\\k22}b"


def test_furigana():
    assert split_furigana("排|<は") == ("排", "は")
    assert split_furigana("の ") == ("の", "")
    f = furigana_for(KaraLine.parse(SNOOZE))
    assert (f[1].base, f[1].ruby, f[1].continuation, f[1].group) == ("排", "い", True, 0)
    assert (f[3].base, f[3].ruby, f[3].continuation) == ("に", "", False)
