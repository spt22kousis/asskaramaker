"""Syllable page with the layout on, dragging taps halfway through a line, the preview, the default template."""
from test_project import template
from test_structure import LYRICS, project, tap_line

from karatap.ass_doc import AssDocument
from karatap.preview import (Piece, Unit, ass_colour, kara_units, parse_styles, piece_progress, play_res,
                             unit_progress)
from karatap.project import (EVEN_STYLES, ODD_STYLES, SINGLE_STYLES, add_template_lines, default_template,
                             is_template_line, new_project_bytes, pick_style)
from karatap.timing import ONSET, TapMachine


def three_lines(tmp_path):
    doc, c = project(tmp_path)
    tap_line(c, 0, 10000, 12000)
    tap_line(c, 1, 12200, 14000)
    tap_line(c, 2, 14200, 16000)
    return doc, c


def test_syllable_page_goes_by_the_line_taps(tmp_path):
    doc, c = three_lines(tmp_path)
    assert c.line_start(1) == 9000                     # on screen together with line 0
    assert [c.sing_range(i) for i in range(3)] == [(10000, 12000), (12200, 14000), (14200, 16000)]
    assert c.line_edges(1) == (12200, 14000)
    assert c.preroll_target(1) == 11200
    c.start_take(0)
    c.down("F", 10000), c.up("F", 10900), c.down("J", 11000)
    assert c.current_line() == 0                       # still holding line 0's last syllable
    assert c.up("J", 11950) == [0]
    assert c.current_line() == 1
    assert c.after_line_done(0, 11960) is None         # no jump back to before line 0
    assert c.line_edges(0) is None                     # its syllables decide it now


def test_list_and_seeks_without_layout_unchanged(tmp_path):
    doc, c = project(tmp_path, layout=False)
    ev = c.lines[0].event
    assert c.sing_range(0) == (ev.start_ms, ev.end_ms) == c.line_edges(0)


def test_drag_taps_halfway_through_a_line_then_go_on(tmp_path):
    doc, c = three_lines(tmp_path)
    c.start_take(1)                                    # line 1: う え お
    c.down("F", 12200), c.up("F", 12500)
    c.down("J", 12600), c.up("J", 12900)
    assert c.tapped_bounds(1) == [(12200, 12500), (12600, 12900)]
    hs = {h.refs: h for h in c.handles(1)}
    assert (("T0",),) in hs and (("T1",),) in hs     # the line tap can still be moved
    steps = len(c.undo_stack)
    c.begin_drag(1)
    assert c.move_handle(1, hs[(("e", 0),)], 12700) == 12600       # kept before the next syllable
    c.discard_if_unchanged()
    assert len(c.undo_stack) == steps                  # nothing saved changed: no undo step
    s1 = next(h for h in c.handles(1) if h.refs == (("e", 0), ("s", 1)))     # now one shared boundary
    c.move_handle(1, s1, 12580)
    c.down("F", 13000), c.up("F", 13500)               # carry on tapping
    assert c.results[1].bounds == [(12200, 12580), (12580, 12900), (13000, 13500)]


def test_backspace_after_a_drag_keeps_the_moved_time(tmp_path):
    m = TapMachine(3)
    m.down("F", 100), m.up("F", 200), m.down("J", 300), m.up("J", 400)
    m.move(0, "start", 120)
    m.move(1, "end", 380)
    assert (m.start, m.end) == ([120, 300, None], [200, 380, None])
    m.undo()
    assert (m.start, m.end, m.next) == ([120, None, None], [200, None, None], 1)


def test_onset_mode_open_syllable_shares_its_start(tmp_path):
    m = TapMachine(3, ONSET)
    m.down("F", 100), m.up("F", 150), m.down("J", 300)          # the second press ends the first
    assert m.end_ev[0] == m.start_ev[1]
    m.move(1, "start", 280)
    assert (m.end[0], m.start[1], m.end[1]) == (280, 280, None)


def test_dragging_a_kept_syllable_of_a_punch_in(tmp_path):
    doc, c = three_lines(tmp_path)
    c.start_take(1)
    for k, t in enumerate((12200, 12700, 13200)):
        c.down("FJ"[k % 2], t), c.up("FJ"[k % 2], t + 400)
    c.start_take(1, c.lines[1].taps[1])                # retap from え, う is kept
    c.down("F", 12800), c.up("F", 13100)
    h = next(h for h in c.handles(1) if h.refs == (("s", 0),))
    c.move_handle(1, h, 12300)
    c.down("J", 13200), c.up("J", 13500)
    assert c.results[1].bounds == [(12300, 12600), (12800, 13100), (13200, 13500)]


def test_preview_lines_follow_the_export(tmp_path):
    doc, c = three_lines(tmp_path)
    c.start_take(0)
    c.down("F", 10000), c.up("F", 10900), c.down("J", 11000), c.up("J", 11950)
    pl = c.preview_lines()
    assert [(p.line, p.style, p.timed) for p in pl] == [(0, "Odd", True), (1, "Even", False), (2, "Odd", False)]
    assert (pl[0].start, pl[0].end) == (9000, 11950)
    assert sum(u.pieces[0].dur for u in kara_units(pl[0].text)) == 11950 - 9000


def test_kara_units_group_readings():
    units = kara_units("{\\-A}{\\k18}排|<は{\\k18}#|<い{\\kf22}気|<き{\\k21}に {\\k5}")
    assert [u.base for u in units] == ["排", "気", "に ", ""]
    assert [[(p.ruby, p.start, p.dur) for p in u.pieces] for u in units[:2]] == \
        [[("は", 0, 180), ("い", 180, 180)], [("き", 360, 220)]]
    assert units[1].pieces[0].sweep and not units[0].pieces[0].sweep
    u = Unit("排", [Piece("は", 0, 100, False), Piece("い", 100, 300, False)])
    assert unit_progress(u, -1) == 0 and unit_progress(u, 50) == 0.25 and unit_progress(u, 100) == 1
    assert piece_progress(Piece("", 100, 200, True), 150) == 0.25


def test_styles_and_resolution():
    doc = default_template()
    styles = parse_styles(doc)
    k1 = styles["K1"]
    assert (k1.font, k1.size, k1.alignment, k1.margins, k1.outline) == \
        ("Noto Sans JP ExtraBold", 120, 1, (120, 30, 240), 4)
    assert styles["noK"].primary == (255, 255, 57, 255)
    assert ass_colour("&H80FF7600") == (0, 118, 255, 127)
    assert play_res(doc) == (1920, 1080)
    assert play_res(template()) == (1920, 1440)


def test_default_template_styles_for_the_layout():
    doc = default_template()
    names = doc.style_names()
    assert (pick_style(names, ODD_STYLES), pick_style(names, EVEN_STYLES), pick_style(names, SINGLE_STYLES)) == \
        ("K1", "K2", "K0")
    assert sum(is_template_line(e) for e in doc.events) == 13
    assert not [e for e in doc.events if not is_template_line(e)]


def test_new_project_from_the_default_template(tmp_path):
    data = new_project_bytes(default_template(), tmp_path / "a.mp4", tmp_path / "a.ass", LYRICS)
    doc = AssDocument(data)
    assert {e.style for e in doc.karaoke_events()} == {"K1"}


def test_template_lines_added_on_export():
    bare = template([l for l in template().lines if "template" not in l and "code" not in l])
    data = add_template_lines(bare.raw, default_template())
    out = AssDocument(data)
    tpl = [e for e in out.events if is_template_line(e)]
    assert len(tpl) == 13 and tpl[0].line_no < out.karaoke_events()[0].line_no
    assert out.style_names() == ["Default", "Odd", "Even", "noK-furigana", "K2-furigana", "K1-furigana",
                                 "K1", "K2", "noK", "K0"]
    assert [e.text for e in out.karaoke_events()] == [e.text for e in bare.karaoke_events()]
    # the file's own lines are kept byte for byte
    assert all(line in out.lines for line in bare.lines)
