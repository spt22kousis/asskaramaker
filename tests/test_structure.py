"""Adding, deleting, splitting and merging lines after the project exists; shifting; line drags."""
from conftest import DATA
from test_project import template

import pytest

from karatap.ass_doc import AssDocument
from karatap.controller import Controller
from karatap.project import new_project_bytes
from karatap.settings import Settings

LYRICS = ["{\\k1}あ{\\k1}い", "{\\k1}う{\\k1}え{\\k1}お", "{\\k1}か"]


def project(tmp_path, layout=True):
    data = new_project_bytes(template(), tmp_path / "a.mp4", tmp_path / "a.ass", LYRICS)
    doc = AssDocument(data)
    c = Controller(doc, Settings(), tmp_path / "a.karatap.json")
    c.layout_on = layout
    return doc, c


def tap_line(c, li, a, b):
    c.start_line_take(li)
    c.line_down("F", a)
    c.line_up("F", b)


def tap_syllables(c, li, start, step=300):
    c.start_take(li)
    for k in range(len(c.lines[li].taps)):
        c.down("FJ"[k % 2], start + k * step)
        c.up("FJ"[k % 2], start + k * step + step - 50)


def karaoke(data):
    return [(e.kind, e.get("start"), e.get("end"), e.style, e.text) for e in AssDocument(data).karaoke_events()]


def test_to_bytes_removes_and_inserts_lines():
    doc = AssDocument(b"a\r\nb\r\nc")
    assert doc.to_bytes({1: None}) == b"a\r\nc"
    assert doc.to_bytes(after={0: ["x", "y"]}, before={2: ["z"]}) == b"a\r\nx\r\ny\r\nb\r\nz\r\nc"
    assert doc.to_bytes(after={2: ["x"]}) == b"a\r\nb\r\nc\r\nx"           # still no final line break
    assert doc.to_bytes({2: None}, after={2: ["x"]}) == b"a\r\nb\r\nx"


def test_delete_and_insert_survive_reload_and_export(tmp_path):
    doc, c = project(tmp_path)
    c.delete_line(1)
    assert [l.plain for l in c.lines] == ["あい", "か"]
    li = c.insert_line(0, "{\\k1}さ{\\k1}し")
    assert li == 1 and c.lines[1].added and c.lines[1].key == "n1"
    tap_line(c, 0, 10000, 12000)
    tap_line(c, 1, 12500, 14000)
    c.save_progress()

    c2 = Controller(doc, Settings(), tmp_path / "a.karatap.json")
    c2.load_progress()
    assert [l.plain for l in c2.lines] == ["あい", "さし", "か"]
    assert c2.line_taps == {0: (10000, 12000), 1: (12500, 14000)}
    rows = karaoke(c2.export_bytes())
    assert [r[4] for r in rows] == ["{\\k1}あ{\\k1}い", "{\\k1}さ{\\k1}し", "{\\k1}か"]
    assert rows[1][:4] == ("Comment", "0:00:09.00", "0:00:14.00", "Even")


def test_insert_at_top_goes_before_the_first_line(tmp_path):
    doc, c = project(tmp_path)
    c.insert_line(-1, "{\\k1}ま")
    assert [r[4] for r in karaoke(c.export_bytes())] == ["{\\k1}ま"] + LYRICS


def test_replace_text_drops_syllables_but_keeps_the_line_tap(tmp_path):
    doc, c = project(tmp_path)
    tap_line(c, 0, 10000, 12000)
    tap_syllables(c, 0, 10000)
    assert 0 in c.results
    c.replace_text(0, ["{\\k1}あ{\\k1}ん", "{\\k1}ね"])
    assert 0 not in c.results and c.line_taps[0] == (10000, 12000)
    assert [l.plain for l in c.lines] == ["あん", "ね", "うえお", "か"]
    assert c.lines[0].edited and not c.lines[0].added and c.lines[1].added


def test_split_keeps_syllable_times(tmp_path):
    doc, c = project(tmp_path)
    tap_line(c, 1, 10000, 12000)
    tap_syllables(c, 1, 10000)
    assert [label for _, label in c.split_options(1)] == ["う｜えお", "うえ｜お"]
    c.split_line(1, 2)
    assert [l.plain for l in c.lines] == ["あい", "うえ", "お", "か"]
    assert c.results[1].bounds == [(10000, 10250), (10300, 10550)]
    assert c.results[2].bounds == [(10600, 10850)]
    assert c.sung_span(2) == (10600, 10850)
    rows = karaoke(c.export_bytes())
    assert rows[1][4] == "{\\k100}{\\k30}う{\\k25}え"           # lead-in from the layout, then syllables
    assert rows[2][0] == "Comment" and rows[2][4].endswith("{\\k25}お")


def test_split_without_syllables_keeps_the_tap_on_the_first_half(tmp_path):
    doc, c = project(tmp_path)
    tap_line(c, 1, 10000, 12000)
    c.split_line(1, 1)
    assert c.line_taps == {1: (10000, 12000)}


def test_merge_joins_times_and_adds_a_space(tmp_path):
    doc, c = project(tmp_path)
    tap_line(c, 0, 10000, 11000)
    tap_syllables(c, 0, 10000)
    tap_line(c, 1, 11500, 13000)
    tap_syllables(c, 1, 11500)
    c.merge_with_next(0)
    assert c.lines[0].kara.text == "{\\k1}あ{\\k1}い{\\k1} {\\k1}う{\\k1}え{\\k1}お"
    assert len(c.results[0].bounds) == 5 and c.line_taps[0] == (10000, 12350)
    assert c.deleted == {str(doc.karaoke_events()[1].line_no): LYRICS[1]}
    texts = [r[4] for r in karaoke(c.export_bytes())]
    assert len(texts) == 2 and texts[1] == LYRICS[2]


def test_shift_times(tmp_path):
    doc, c = project(tmp_path)
    tap_line(c, 0, 10000, 12000)
    tap_line(c, 1, 12500, 14000)
    tap_syllables(c, 1, 12500)
    c.set_layout_override(0, None, 12100)
    assert c.shift_times(None, -1000) == (2, 1)
    assert c.line_taps[0] == (9000, 11000)
    assert c.results[1].bounds[0] == (11500, 11750)
    assert c.layout_overrides[0] == (None, 11100)
    assert c.shift_times([0], 500) == (1, 0) and c.line_taps[0] == (9500, 11500)
    with pytest.raises(ValueError):
        c.shift_times(None, -20000)


def test_shift_moves_file_line_times_without_layout():
    doc = AssDocument.load(DATA / "notebook_sample.ass")
    c = Controller(doc, Settings())
    tap_syllables(c, 0, 1200)
    ev = c.lines[0].event
    c.shift_times([0], 200)
    assert (c.line_start(0), c.line_end(0)) == (ev.start_ms + 200, ev.end_ms + 200)
    new = AssDocument(c.export_bytes()).karaoke_events()[0]
    assert (new.start_ms, new.end_ms) == (ev.start_ms + 200, ev.end_ms + 200)


def test_line_edge_drag_and_undo(tmp_path):
    doc, c = project(tmp_path)
    tap_line(c, 0, 10000, 12000)
    assert c.line_draggable(0) and not c.line_draggable(1)
    c.begin_line_drag(0)
    assert c.move_line_edge(0, "start", 11950) == 11900       # keeps 100 ms
    assert c.move_line_edge(0, "end", 13004) == 13000
    assert c.line_taps[0] == (11900, 13000)
    assert c.undo_edit() == "拖曳行軸" and c.line_taps[0] == (10000, 12000)
    tap_syllables(c, 0, 10000)
    assert not c.line_draggable(0)


def test_merge_keeps_the_only_line_span(tmp_path):
    doc, c = project(tmp_path)
    tap_line(c, 0, 10000, 11000)
    tap_syllables(c, 0, 10000)
    assert c.merge_loses_syllables(0)
    c.merge_with_next(0)
    assert 0 not in c.results and c.line_taps[0] == (10000, 10550)


def test_shift_in_layout_mode_leaves_line_overrides_alone(tmp_path):
    doc, c = project(tmp_path)
    tap_line(c, 0, 10000, 12000)
    tap_syllables(c, 0, 10000)
    c.shift_times(None, -1000)
    r = c.results[0]
    assert (r.start_override, r.end_override) == (None, None)
    assert r.bounds[0] == (9000, 9250)


def test_shift_refuses_negative_file_line_times():
    doc = AssDocument.load(DATA / "notebook_sample.ass")
    c = Controller(doc, Settings())
    tap_syllables(c, 0, 1200)
    with pytest.raises(ValueError):
        c.shift_times([0], -(c.line_start(0) + 10))


def test_display_spans_apply_overrides(tmp_path):
    doc, c = project(tmp_path)
    tap_line(c, 0, 10000, 12000)
    tap_line(c, 1, 12500, 14000)
    c.set_layout_override(1, 9500, None)
    spans = c.display_spans()
    assert spans[0][:2] == (9000, 12000) and spans[1][:2] == (9500, 14000) and spans[2] is None


def test_undo_of_a_line_drag_drops_the_take(tmp_path):
    doc, c = project(tmp_path)
    tap_line(c, 0, 10000, 12000)
    c.begin_line_drag(0)
    c.move_line_edge(0, "end", 12500)
    c.start_line_take(0)
    c.undo_edit()
    assert c.line_take is None and c.line_taps[0] == (10000, 12000)
