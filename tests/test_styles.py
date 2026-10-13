"""Lyric styles of a template without the usual names: never a furigana style, the ones with templates first."""
from test_structure import LYRICS

from karatap.ass_doc import AssDocument
from karatap.controller import Controller
from karatap.project import default_styles, lyric_styles, new_project_bytes, pick_style, template_styles
from karatap.settings import Settings

STYLE = "100,100,0,0,1,2,0,2,0,0,10,1"
OP_TEMPLATE = "\r\n".join([
    "[Script Info]",
    "ScriptType: v4.00+",
    "PlayResX: 1920",
    "PlayResY: 1080",
    "",
    "[V4+ Styles]",
    "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
    "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
    "MarginR, MarginV, Encoding",
    *(f"Style: {n},Arial,70,&H00FFFFFF,&H00000000,&H00000000,&H00000000,0,0,0,0,{STYLE}"
      for n in ("OPCN-furigana", "OPJP-furigana", "OPJP", "OPCN")),
    "",
    "[Events]",
    "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    "Comment: 2,0:00:00.00,0:00:00.00,OPJP,,0,0,0,template char noblank,{\\an5\\pos($center,$middle)}",
    "Comment: 2,0:00:00.00,0:00:00.00,OPCN,,0,0,0,template char noblank,{\\an5\\pos($center,$middle)}",
    "",
])


def op_template() -> AssDocument:
    return AssDocument(OP_TEMPLATE.encode("utf-8"))


def test_no_usual_names_picks_a_style_with_templates():
    doc = op_template()
    assert lyric_styles(doc.style_names()) == ["OPJP", "OPCN"]
    assert template_styles(doc) == (["OPJP", "OPCN"], False)
    assert default_styles(doc) == ("OPJP", "OPJP", "OPJP")
    assert pick_style(["a-furigana", "b"], ()) == "b"
    assert pick_style(["a-furigana"], ()) == "a-furigana"           # nothing else there


def test_new_project_uses_the_picked_style(tmp_path):
    data = new_project_bytes(op_template(), tmp_path / "a.mp4", tmp_path / "a.ass", LYRICS)
    assert {e.style for e in AssDocument(data).karaoke_events()} == {"OPJP"}
    data = new_project_bytes(op_template(), tmp_path / "a.mp4", tmp_path / "a.ass", LYRICS, "OPCN")
    assert {e.style for e in AssDocument(data).karaoke_events()} == {"OPCN"}


def test_check_warns_about_styles_without_templates(tmp_path):
    data = new_project_bytes(op_template(), tmp_path / "a.mp4", tmp_path / "a.ass", LYRICS)
    c = Controller(AssDocument(data), Settings(), tmp_path / "a.karatap.json")
    c.layout_on = False
    assert not [i for i in c.check() if "template" in i.text]
    c.layout_on = True
    p = c.layout_params
    p.odd_style = p.even_style = p.single_style = "OPCN-furigana"
    c.start_line_take(0)
    c.line_down("F", 1000), c.line_up("F", 2000)
    found = [i for i in c.check() if "template" in i.text]
    assert len(found) == 1 and "OPCN-furigana" in found[0].text and "振假名" in found[0].text
    assert found[0].level == "warn" and found[0].page == "layout"
    # "template all" covers every style
    tpl = OP_TEMPLATE.replace("template char noblank", "template char noblank all", 1)
    data = new_project_bytes(AssDocument(tpl.encode("utf-8")), tmp_path / "a.mp4", tmp_path / "a.ass", LYRICS)
    c2 = Controller(AssDocument(data), Settings(), tmp_path / "b.karatap.json")
    c2.layout_params.odd_style = "OPCN-furigana"
    assert not [i for i in c2.check() if "template" in i.text]
