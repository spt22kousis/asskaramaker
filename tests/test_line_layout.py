"""Line tapping and the Odd/Even layout on a project made from a template."""
from test_project import template

from karatap.ass_doc import AssDocument
from karatap.controller import Controller
from karatap.karaoke import KaraLine
from karatap.settings import Settings
from karatap.timing import ONSET

LYRICS = ["{\\k1}あ{\\k1}い", "{\\k1}う{\\k1}え", "{\\k1}お", "{\\k1}か"]


def project(tmp_path, settings=None):
    from karatap.project import new_project_bytes
    data = new_project_bytes(template(), tmp_path / "a.mp4", tmp_path / "a.ass", LYRICS)
    doc = AssDocument(data)
    c = Controller(doc, settings or Settings(), tmp_path / "a.karatap.json")
    c.layout_on = True
    return doc, c


def hold(c, key, a, b, line=True):
    if line:
        c.line_down(key, a)
        return c.line_up(key, b)
    c.down(key, a)
    return c.up(key, b)


def test_line_taps_feed_the_layout(tmp_path):
    doc, c = project(tmp_path)
    assert c.placements() == [None] * 4
    assert hold(c, "F", 10000, 12000) == [0]
    c.line_down("J", 12500)
    assert c.line_state(1) == "active" and c.line_state(2) == "next"
    assert c.line_down("F", 13900) == [1]           # an overlapping press ends line 1
    assert c.line_up("J", 14000) == []
    assert c.line_up("F", 16000) == [2]
    assert c.line_taps[1] == (12500, 13900)
    hold(c, "J", 25000, 27000)
    pl = c.placements()
    assert [(p.start, p.end, p.style) for p in pl] == [
        (9000, 12000, "Odd"), (9000, 13900, "Even"), (12300, 16000, "Odd"), (25000, 27000, "Default")]
    assert (c.line_start(1), c.line_end(1)) == (9000, 13900)


def test_line_undo_restores_previous_taps(tmp_path):
    doc, c = project(tmp_path)
    hold(c, "F", 10000, 12000)
    hold(c, "F", 12500, 14000)
    assert c.line_undo() == 12500 - Settings().preroll_ms
    assert 1 not in c.line_taps and c.line_current() == 1
    c.start_line_take(0)                   # retap line 0 only
    hold(c, "F", 10100, 12100)
    assert c.line_undo() is not None
    assert c.line_taps[0] == (10000, 12000)


def test_onset_line_taps(tmp_path):
    doc, c = project(tmp_path, Settings(mode=ONSET))
    c.line_down("F", 1000)
    c.line_up("F", 1100)                  # release ends the line (onset_end = release)
    assert c.line_taps[0] == (1000, 1100)


def test_export_uses_layout_times_styles_and_lead_in(tmp_path):
    doc, c = project(tmp_path)
    hold(c, "F", 10000, 12000)
    hold(c, "F", 12500, 14000)
    c.start_take(0)                        # syllables of line 0
    hold(c, "F", 10200, 10900, line=False)
    assert hold(c, "J", 11000, 11800, line=False) == [0]
    out = AssDocument(c.export_bytes())
    ev = out.karaoke_events()
    # line 0: shown 1 s before its first syllable; \k includes that lead-in
    assert (ev[0].get("start"), ev[0].get("end"), ev[0].style) == ("0:00:09.20", "0:00:11.80", "Odd")
    k = KaraLine.parse(ev[0].text)
    assert [s.value for s in k.syls] == [100, 80, 80]
    assert sum(s.value for s in k.syls) * 10 == ev[0].end_ms - ev[0].start_ms
    # line 1: only line-tapped, placeholders kept, synced with line 0
    assert (ev[1].get("start"), ev[1].get("end"), ev[1].style, ev[1].text) == \
        ("0:00:09.20", "0:00:14.00", "Even", LYRICS[1])
    # untimed lines are left alone
    assert ev[2].render() == doc.karaoke_events()[2].render()


def test_override_and_drag_handles(tmp_path):
    doc, c = project(tmp_path)
    hold(c, "F", 10000, 12000)
    c.start_take(0)
    hold(c, "F", 10200, 10900, line=False)
    hold(c, "J", 11000, 11800, line=False)
    l0 = [h for h in c.handles(0) if h.refs == (("L0",),)][0]
    assert l0.ms == 10200                 # alone in its segment: shown when sung
    c.begin_drag(0)
    assert c.move_handle(0, l0, 9500) == 9500
    assert c.layout_overrides[0] == (9500, None)
    assert c.line_start(0) == 9500
    c.move_handle(0, l0, 10200)           # back to the rule's value: no override
    assert 0 not in c.layout_overrides
    c.move_handle(0, l0, 9600)
    c.undo_drag()
    assert 0 not in c.layout_overrides


def test_progress_round_trip(tmp_path):
    doc, c = project(tmp_path)
    hold(c, "F", 10000, 12000)
    c.layout_params.lead_ms = 800
    c.set_layout_override(0, None, 12500)
    c.save_progress()
    c2 = Controller(doc, Settings(), tmp_path / "a.karatap.json")
    c2.load_progress()
    assert c2.layout_on and c2.layout_params.lead_ms == 800
    assert c2.line_taps == {0: (10000, 12000)}
    assert c2.layout_overrides == {0: (None, 12500)}
    assert c2.line_end(0) == 12500
