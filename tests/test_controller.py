from conftest import DATA, EXAMPLES

import pytest

from karatap.ass_doc import AssDocument
from karatap.controller import IMPORTED, TAPPED_OK, UNTAPPED, WARNING, Controller, LineResult
from karatap.karaoke import KaraLine
from karatap.settings import Settings
from karatap.timing import INSERT_GAP, ONSET, TAPPED


def make(settings=None, progress=None):
    doc = AssDocument.load(DATA / "notebook_sample.ass")
    return doc, Controller(doc, settings or Settings(), progress)


def tap_hold(c, key, start, end):
    c.down(key, start)
    return c.up(key, end)


def test_hold_line_by_line_and_export_only_touches_tapped_line():
    doc, c = make()
    # line 0: 新作動画 / 投稿 / だ  (1.00 - 4.00)
    assert c.upcoming(3) == [(0, 0), (0, 2), (0, 4)]
    tap_hold(c, "F", 1200, 1900)
    tap_hold(c, "J", 2000, 2600)
    done = tap_hold(c, "F", 2600, 3300)
    assert done == [0]
    assert c.status(0) == TAPPED_OK and c.status(1) == UNTAPPED
    out = c.export_bytes()
    line = doc.karaoke_events()[0]
    # lead-in 20 inserted; release gaps go to the notebook's empty syllables; rest of line to the trailing one
    expected = line.render().replace(
        "{\\k1}新作動画|<しんさくどうが{\\k1}{\\k1}投稿|<とうこう{\\k1}{\\k1}だ{\\k1}",
        "{\\k20}{\\k70}新作動画|<しんさくどうが{\\k10}{\\k60}投稿|<とうこう{\\k0}{\\k70}だ{\\k70}")
    assert out == doc.raw.replace(line.render().encode("utf-8"), expected.encode("utf-8"))


def test_k_values_sum_to_line_length_on_real_file():
    if not (EXAMPLES / "snooze_short.ass").exists():
        pytest.skip("sample not present")
    doc = AssDocument.load(EXAMPLES / "snooze_short.ass")
    c = Controller(doc, Settings())
    for li, line in enumerate(c.lines):
        s, e = line.event.start_ms, line.event.end_ms
        n = len(line.taps)
        step = (e - s - 100) / n
        for k in range(n):
            t0 = s + 50 + k * step + 3.3
            c.down("FJ"[k % 2], t0)
            c.up("FJ"[k % 2], t0 + step * 0.8)
    assert all(c.status(li) == TAPPED_OK for li in range(len(c.lines)))
    new = AssDocument(c.export_bytes())
    for old_ev, new_ev in zip(doc.events, new.events):
        if not old_ev.is_karaoke:
            assert old_ev.render() == new_ev.render()
            continue
        k = KaraLine.parse(new_ev.text)
        assert sum(sy.value for sy in k.syls) * 10 == new_ev.end_ms - new_ev.start_ms
        assert (new_ev.start_ms, new_ev.end_ms) == (old_ev.start_ms, old_ev.end_ms)
        # visible text unchanged
        assert [sy.text for sy in k.syls if sy.text] == \
            [sy.text for sy in KaraLine.parse(old_ev.text).syls if sy.text]


def test_onset_mode_continuous_across_lines():
    s = Settings(mode=ONSET, continuous=True)
    doc, c = make(s)
    for t in (1200, 2000):
        c.down("F", t); c.up("F", t + 50)
    c.down("F", 2600)                     # last syllable of line 0, held...
    done = c.down("J", 4600)              # ...ended by the first press of line 1
    assert done == [0]
    t = c.timing_for(0)
    # gap syllables are 0 in onset mode; the late end (4.60 > 4.00) is clamped and flagged
    assert t.values == [80, 0, 60, 0, 140, 0]
    assert t.inserts == {0: 20} and t.warnings == ["late"]


def test_continuous_skips_untouched_line():
    s = Settings(continuous=True)
    doc, c = make(s)
    assert c.current_line() == 0
    assert c.tick(4000 + 301, keys_held=False) is None
    assert c.current_line() == 1
    assert c.upcoming(1) == [(1, 0)]


def test_line_mode_loops_unfinished_line_and_seeks_after_finish():
    s = Settings(preroll_ms=1000)
    doc, c = make(s)
    a = c.tick(4000 + 1501, keys_held=False)
    assert a.kind == "seek" and a.ms == 0
    tap_hold(c, "F", 1200, 1900)
    tap_hold(c, "J", 2000, 2600)
    done = tap_hold(c, "F", 2600, 3300)
    a = c.after_line_done(done[0], now_ms=3350)
    assert a.kind == "seek" and a.ms == 3500 and a.line == 1      # forward to next preroll
    assert c.after_line_done(done[0], now_ms=3700) is None        # already inside the preroll
    a = c.after_line_done(done[0], now_ms=4450)
    assert a.kind == "seek" and a.ms == 3500                      # too late: replay preroll


def test_out_of_bounds_asks_then_extend_fixes():
    doc, c = make()
    tap_hold(c, "F", 900, 1900)
    tap_hold(c, "J", 2000, 2600)
    done = tap_hold(c, "F", 2600, 4120)
    assert c.status(0) == WARNING
    assert c.after_line_done(done[0], 4200).kind == "ask_oob"
    c.extend_line(0)
    assert c.status(0) == TAPPED_OK
    ev = AssDocument(c.export_bytes()).karaoke_events()[0]
    assert ev.get("start") == "0:00:00.90" and ev.get("end") == "0:00:04.12"


def test_undo_reopens_finished_line_and_restores_previous_result():
    doc, c = make()
    tap_hold(c, "F", 1200, 1900); tap_hold(c, "J", 2000, 2600); tap_hold(c, "F", 2600, 3300)
    old = c.results[0]
    c.start_take(0)                                    # retap line 0
    tap_hold(c, "F", 1210, 1900); tap_hold(c, "J", 2000, 2600); tap_hold(c, "F", 2600, 3300)
    assert c.results[0].bounds[0][0] == 1210
    rewind = c.undo()
    assert rewind == 1600                              # 2600 press minus 1 s preroll
    assert c.results[0] is old
    tap_hold(c, "F", 2610, 3300)
    assert c.results[0].bounds[2][0] == 2610


def test_tapped_line_timing_writes_times():
    s = Settings(line_timing=TAPPED, pre_pad_ms=100, post_pad_ms=200, gap_policy=INSERT_GAP)
    doc, c = make(s)
    tap_hold(c, "F", 1200, 1900); tap_hold(c, "J", 2000, 2600); tap_hold(c, "F", 2600, 3300)
    ev = AssDocument(c.export_bytes()).karaoke_events()[0]
    assert ev.get("start") == "0:00:01.10" and ev.get("end") == "0:00:03.50"
    k = KaraLine.parse(ev.text)
    assert sum(s.value for s in k.syls) == 240


def test_edit_split_is_saved_and_exported(tmp_path):
    from karatap.karaoke import split_syllable
    p = tmp_path / "x.karatap.json"
    doc, c = make(progress=p)
    tap_hold(c, "F", 1200, 1900); tap_hold(c, "J", 2000, 2600); tap_hold(c, "F", 2600, 3300)
    # line 1 (untapped): split 低音|<ていおん's reading after the first beat
    k = c.lines[1].kara
    si = next(i for i, s in enumerate(k.syls) if s.text.startswith("低音"))
    new = split_syllable(k, si, 1)
    assert c.edit_line(1, new) is False          # nothing to clear
    assert c.lines[1].edited and c.current_line() == 1
    # line 0 (tapped): editing clears its taps
    k0 = c.lines[0].kara
    assert c.edit_line(0, split_syllable(k0, 0, 1)) is True and 0 not in c.results
    c.save_progress()

    doc2, c2 = make(progress=p)
    c2.load_progress()
    assert c2.lines[1].kara.text == new and c2.lines[0].edited
    out = AssDocument(c2.export_bytes())
    texts = [e.text for e in out.karaoke_events()]
    assert texts[1] == new                        # untapped but edited: new split is written
    assert "低|<て" not in texts[1] and "低音|<て{\\k0}#|<いおん" in texts[1]
    c2.revert_line(1)
    assert not c2.lines[1].edited


TIMED = ("[Events]\r\n"
         "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\r\n"
         # real timing: 30 + 50 + 20(gap) + 100 = 200 cs = line length
         "Comment: 0,0:00:01.00,0:00:03.00,K1,,0,0,0,karaoke,{\\k30}あ{\\k50}い{\\k20} {\\k100}う\r\n"
         # placeholders: {\k1} x3 on a 2 s line -> not imported
         "Comment: 0,0:00:04.00,0:00:06.00,K1,,0,0,0,karaoke,{\\k1}か{\\k1}き{\\k1}く\r\n")


def make_timed():
    doc = AssDocument(TIMED.encode("utf-8"))
    c = Controller(doc, Settings())
    return doc, c


def test_import_existing_k():
    doc, c = make_timed()
    assert c.import_file_timing() == 1
    assert c.results[0].bounds == [(1000, 1300), (1300, 1800), (2000, 3000)]
    assert c.status(0) == IMPORTED and c.status(1) == UNTAPPED
    assert c.export_bytes() == doc.raw            # untouched imported lines are copied as they are
    assert c.progress_data()["lines"] == {}       # and not stored in the progress file


def test_import_real_file_round_trips():
    if not (EXAMPLES / "snooze_short.ass").exists():
        pytest.skip("sample not present")
    doc = AssDocument.load(EXAMPLES / "snooze_short.ass")
    for timing in ("keep", "tapped"):
        c = Controller(doc, Settings(line_timing=timing))
        assert c.import_file_timing() == len(c.lines) == 6
        assert c.export_bytes() == doc.raw


def test_import_skips_lines_with_results_or_edits():
    doc, c = make_timed()
    c.results[0] = LineResult([(1100, 1200), (1200, 1300), (1400, 1500)])
    c.edit_line(1, "{\\k1}かき{\\k1}く")
    assert c.import_file_timing() == 0


def test_drag_handles_shared_and_separate():
    doc, c = make_timed()
    c.import_file_timing()
    hs = c.handles(0)
    refs = [h.refs for h in hs]
    # あ start | あ end = い start (shared) | い end | う start | う end | line start | line end
    assert refs == [(("s", 0),), (("e", 0), ("s", 1)), (("e", 1),), (("s", 2),), (("e", 2),),
                    (("L0",),), (("L1",),)]
    shared = hs[1]
    c.begin_drag(0)
    assert c.move_handle(0, shared, 1404) == 1400                 # 10 ms steps
    assert c.results[0].bounds[:2] == [(1000, 1400), (1400, 1800)]
    assert c.move_handle(0, shared, 5000) == 1800                 # can't pass い's end
    assert c.status(0) == TAPPED_OK                               # no longer the file's timing
    out = AssDocument(c.export_bytes()).karaoke_events()[0].text
    assert out == "{\\k80}あ{\\k0}い{\\k20} {\\k100}う"
    assert c.undo_drag() == 0
    assert c.status(0) == IMPORTED and c.export_bytes() == doc.raw


def test_drag_line_edge_sets_override():
    doc, c = make_timed()
    c.import_file_timing()
    l1 = next(h for h in c.handles(0) if h.refs == (("L1",),))
    c.begin_drag(0)
    c.move_handle(0, l1, 3500)
    ev = AssDocument(c.export_bytes()).karaoke_events()[0]
    # line now ends at 3.50; the extra time goes to the last syllable
    assert ev.get("end") == "0:00:03.50"
    assert ev.text == "{\\k30}あ{\\k50}い{\\k20} {\\k150}う"


def test_progress_roundtrip(tmp_path):
    p = tmp_path / "x.karatap.json"
    doc, c = make(progress=p)
    tap_hold(c, "F", 1200, 1900); tap_hold(c, "J", 2000, 2600); tap_hold(c, "F", 2600, 3300)
    c.save_progress()
    doc2, c2 = make(progress=p)
    c2.load_progress()
    assert c2.results[0].bounds == c.results[0].bounds
    assert c2.export_bytes() == c.export_bytes()
    assert c2.load_messages == []
