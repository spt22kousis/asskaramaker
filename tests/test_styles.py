"""Lyric styles of a template without the usual names: never a furigana style, the ones with templates first."""
import json

from test_structure import LYRICS

from karatap.ass_doc import AssDocument
from karatap.controller import Controller
from karatap.project import (add_template_lines, available_styles, default_styles, default_template, layout_styles,
                             lyric_styles, new_project_bytes, pick_style, template_styles)
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


# A file timed elsewhere (e.g. karaoke.vmoe.info): only a Default style, no template lines.
VMOE = "\r\n".join([
    "[Script Info]",
    "ScriptType: v4.00+",
    "PlayResX: 1920",
    "PlayResY: 1080",
    "",
    "[V4+ Styles]",
    "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
    "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
    "MarginR, MarginV, Encoding",
    f"Style: Default,Arial,120,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,{STYLE}",
    "",
    "[Events]",
    "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    "Dialogue: 0,0:00:15.00,0:00:16.50,Default,,0,0,0,karaoke,{\\k50}螺{\\k50}旋{\\k50}状",
    "Dialogue: 0,0:00:16.80,0:00:18.80,Default,,0,0,0,karaoke,{\\k100}微{\\k100}熱",
    "Dialogue: 0,0:00:19.00,0:00:20.00,Default,,0,0,0,karaoke,{\\k50}カ{\\k50}レ",
    "",
])


def vmoe(tmp_path, progress=None) -> Controller:
    if progress is not None:
        (tmp_path / "v.karatap.json").write_text(json.dumps(progress), encoding="utf-8")
    c = Controller(AssDocument(VMOE.encode("utf-8")), Settings(), tmp_path / "v.karatap.json")
    c.load_progress()
    c.import_file_timing()
    return c


def test_file_without_templates_lays_out_with_the_built_in_styles(tmp_path):
    c = vmoe(tmp_path)
    assert available_styles(c.doc)[:1] == ["Default"] and {"K1", "K2", "K0"} <= set(available_styles(c.doc))
    p = c.layout_params
    assert (p.odd_style, p.even_style, p.single_style, p.one_style) == ("K1", "K2", "K0", "K0")
    c.layout_on = True
    assert [pl.style for pl in c.placements()] == ["K1", "K2", "K1"]
    assert not [i for i in c.check() if "樣式" in i.text]
    # Export with the built-in template added: every lyric line's style is defined.
    out = AssDocument(add_template_lines(c.export_bytes(), default_template()))
    assert {e.style for e in out.karaoke_events()} <= set(out.style_names())


def test_file_with_its_own_odd_even_styles_keeps_them():
    doc = AssDocument(VMOE.replace("Style: Default,", "Style: Odd,").encode("utf-8"))
    assert layout_styles(doc)[0] == "Odd"
    assert layout_styles(op_template()) == default_styles(op_template())      # has templates: its own


def test_saved_styles_the_file_lacks_are_replaced_on_load(tmp_path):
    params = {"odd_style": "Odd", "even_style": "Even", "single_style": "Default", "one_style": "Default"}
    c = vmoe(tmp_path, {"version": 1, "layout": {"on": True, "params": params}})
    p = c.layout_params
    assert (p.odd_style, p.even_style, p.single_style) == ("K1", "K2", "Default")    # Default is in the file
    assert c.dirty and any("Odd" in m and "K1" in m for m in c.load_messages)
    params.update(odd_style="K1", even_style="K2")
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    c = vmoe(fresh, {"version": 1, "layout": {"on": True, "params": params}})
    assert not any("樣式" in m for m in c.load_messages) and not c.dirty      # all there: nothing to say


def test_check_warns_about_styles_the_file_lacks(tmp_path):
    c = vmoe(tmp_path)
    c.layout_on = True
    c.layout_params.even_style = "Nope"
    found = [i for i in c.check() if "樣式" in i.text]
    assert len(found) == 1 and "Nope" in found[0].text and "不在這個 ASS" in found[0].text
    assert found[0].level == "warn" and found[0].page == "layout" and found[0].line == 1
    # With template lines of its own the file gets no built-in styles: one warning, not a template one too.
    data = new_project_bytes(op_template(), tmp_path / "a.mp4", tmp_path / "a.ass", LYRICS)
    c2 = Controller(AssDocument(data), Settings(), tmp_path / "a.karatap.json")
    c2.layout_on = True
    c2.layout_params.single_style = "K1"           # one tapped line: a segment of one
    c2.start_line_take(0)
    c2.line_down("F", 1000), c2.line_up("F", 2000)
    found = [i for i in c2.check() if "樣式" in i.text]
    assert len(found) == 1 and "K1" in found[0].text and "不在這個 ASS" in found[0].text
