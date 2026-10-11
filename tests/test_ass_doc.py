import pytest

from conftest import DATA, EXAMPLES
from karatap.ass_doc import AssDocument, format_time, parse_time

EXAMPLE_FILES = sorted(EXAMPLES.glob("*.ass")) if EXAMPLES.exists() else []


@pytest.mark.parametrize("path", [DATA / "notebook_sample.ass", *EXAMPLE_FILES],
                         ids=lambda p: p.name)
def test_roundtrip_is_byte_identical(path):
    raw = path.read_bytes()
    doc = AssDocument(raw)
    assert doc.to_bytes() == raw


@pytest.mark.skipif(not (EXAMPLES / "snooze_short.ass").exists(), reason="sample not present")
def test_snooze_short_karaoke_lines():
    doc = AssDocument.load(EXAMPLES / "snooze_short.ass")
    kara = doc.karaoke_events()
    assert len(kara) == 6
    assert all(e.kind == "Comment" for e in kara)
    assert kara[0].text.startswith("{\\-A}{\\k18}排|<は")
    assert doc.section_value("Aegisub Project Garbage", "Audio File") == "snooze.mp4"


def test_notebook_sample_skips_fx_line_with_k_tag():
    doc = AssDocument.load(DATA / "notebook_sample.ass")
    kara = doc.karaoke_events()
    assert len(kara) == 3
    assert not any(e.effect == "fx" for e in kara)


def test_untemplated_dialogue_with_k_counts_as_karaoke():
    raw = ("[Events]\r\n"
           "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\r\n"
           "Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,{\\k1}あ{\\k1}い\r\n"     # Arctime + LLM, before templating
           "Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,plain title\r\n"         # no \k
           "Comment: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,{\\k1}note\r\n"           # comment, empty effect
           "Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,fx,{\\k5}x\r\n"           # template output
           "Comment: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,Karaoke,{\\k1}う\r\n").encode("utf-8")
    kara = AssDocument(raw).karaoke_events()
    assert [e.text for e in kara] == ["{\\k1}あ{\\k1}い", "{\\k1}う"]


def test_replacement_changes_only_that_line():
    path = DATA / "notebook_sample.ass"
    raw = path.read_bytes()
    doc = AssDocument(raw)
    ev = doc.karaoke_events()[0]
    new = ev.with_fields(text="{\\k5}x").render()
    out = doc.to_bytes({ev.line_no: new})
    old_line = doc.lines[ev.line_no].encode("utf-8")
    assert out == raw.replace(old_line, new.encode("utf-8"))
    assert out.startswith(b"\xef\xbb\xbf") and out.count(b"\r\n") == raw.count(b"\r\n")


def test_lf_and_missing_final_newline_preserved():
    raw = "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\r\n" \
          "Comment: 0,0:00:01.00,0:00:02.00,K1,,0,0,0,karaoke,{\\k100}a, b".encode("utf-8")
    doc = AssDocument(raw)
    assert doc.to_bytes() == raw
    assert doc.karaoke_events()[0].text == "{\\k100}a, b"     # commas inside Text are kept


def test_time_parse_format():
    assert parse_time("0:00:02.71") == 2710
    assert parse_time("1:02:03.4") == 3723400
    assert format_time(2710) == "0:00:02.71"
    assert format_time(2715) == "0:00:02.72"        # half up
    assert format_time(3723400, like="01:00:00.00") == "01:02:03.40"
