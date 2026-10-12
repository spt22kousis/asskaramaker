"""Undo/redo, backups, retapping from a syllable, review marks and the pre-export check."""
from conftest import DATA
from test_project import template
from test_structure import project, tap_line, tap_syllables

import json

from karatap.ass_doc import AssDocument
from karatap.controller import IMPORTED, Controller
from karatap.settings import Settings


def make_timed():
    text = (b"[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
            b"Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,{\\k40}\xe3\x81\x82{\\k40}\xe3\x81\x84{\\k120}\xe3\x81\x86\n")
    doc = AssDocument(text)
    return doc, Controller(doc, Settings())


# -- undo / redo -------------------------------------------------------------------------------
def test_structure_edits_undo_and_redo(tmp_path):
    doc, c = project(tmp_path)
    tap_line(c, 0, 10000, 12000)
    c.delete_line(1)
    c.merge_with_next(0)
    assert [l.plain for l in c.lines] == ["あい か"]
    assert c.undo_edit() == "合併行"
    assert [l.plain for l in c.lines] == ["あい", "か"] and c.line_taps == {0: (10000, 12000)}
    assert c.undo_edit() == "刪除行"
    assert [l.plain for l in c.lines] == ["あい", "うえお", "か"]
    assert c.redo_edit() == "刪除行"
    assert [l.plain for l in c.lines] == ["あい", "か"]
    c.insert_line(0, "{\\k1}さ")                 # a new edit clears what could be redone
    assert c.redo_label() is None and c.undo_label() == "插入行"


def test_tapping_a_line_is_one_undo_step(tmp_path):
    doc, c = project(tmp_path)
    tap_line(c, 0, 10000, 12000)
    tap_syllables(c, 0, 10000)
    first = list(c.results[0].bounds)
    tap_syllables(c, 0, 10100)                   # retapped worse
    assert c.undo_label() == "打音節 第 1 行"
    c.undo_edit()
    assert c.results[0].bounds == first
    c.undo_edit()
    assert 0 not in c.results and c.line_taps == {0: (10000, 12000)}
    assert c.undo_edit() == "打行軸 第 1 行" and c.line_taps == {}


def test_backspace_after_a_commit_leaves_no_empty_undo_step(tmp_path):
    doc, c = project(tmp_path)
    tap_syllables(c, 0, 10000)
    assert len(c.undo_stack) == 1
    c.undo()                                     # Backspace: reopens the line
    assert 0 not in c.results and c.undo_stack == []


def test_drag_without_moving_is_not_an_undo_step():
    doc, c = make_timed()
    c.import_file_timing()
    c.begin_drag(0)
    c.discard_if_unchanged()
    assert c.undo_stack == []


def test_repeated_layout_changes_merge(tmp_path):
    doc, c = project(tmp_path)
    for v in (900, 800, 700):
        c.checkpoint("版面設定", merge="layout")
        c.layout_params.lead_ms = v
    assert len(c.undo_stack) == 1
    c.undo_edit()
    assert c.layout_params.lead_ms == 1000


def test_cleared_file_timing_stays_cleared_through_undo_and_reload(tmp_path):
    doc, c = make_timed()
    c.progress_path = tmp_path / "x.karatap.json"
    c.import_file_timing()
    c.clear_line(0)
    c.checkpoint("something")
    c.undo_edit()
    assert 0 not in c.results                    # not imported again
    c.save_progress()
    c2 = Controller(doc, Settings(), c.progress_path)
    c2.load_progress()
    assert c2.import_file_timing() == 0
    c.undo_edit()                                # undo the clear itself
    assert c.status(0) == IMPORTED


# -- backups -------------------------------------------------------------------------------------
def test_backups_and_restore(tmp_path):
    doc, c = project(tmp_path)
    c.backup_root = tmp_path / "bk"
    tap_line(c, 0, 10000, 12000)
    c.save_progress()                            # the first save of a session makes a backup
    (path, summary), = c.backups()
    assert "行軸 1 行" in summary
    tap_line(c, 1, 12500, 14000)
    c.restore_backup(path)
    assert c.line_taps == {0: (10000, 12000)}
    assert c.undo_edit() == "從備份還原" and 1 in c.line_taps


# -- retapping from a syllable -------------------------------------------------------------------------
def test_retap_from_a_syllable_keeps_the_earlier_ones(tmp_path):
    doc, c = project(tmp_path)
    tap_syllables(c, 1, 10000)                   # う え お
    old = list(c.results[1].bounds)
    c.start_take(1, from_syllable=c.lines[1].taps[1])
    assert c.take_seek_target() == old[1][0] - Settings().preroll_ms
    assert c.syllable_states(1) == {c.lines[1].taps[0]: "done", c.lines[1].taps[1]: "next"}
    assert c.upcoming(2) == [(1, c.lines[1].taps[1]), (1, c.lines[1].taps[2])]
    c.down("F", 10320); c.up("F", 10500)
    assert c.down("J", 10620) == [] and c.up("J", 10800) == [1]
    assert c.results[1].bounds == [old[0], (10320, 10500), (10620, 10800)]


def test_retap_replay_keeps_the_punch_in(tmp_path):
    doc, c = project(tmp_path)
    c.layout_on = False                          # times from taps so line_end is the tapped end
    tap_line(c, 1, 10000, 10850)
    tap_syllables(c, 1, 10000)
    c.start_take(1, from_syllable=c.lines[1].taps[2])
    action = c.tick(20000, keys_held=False)
    assert action.kind == "seek" and c.take.punch == (1, c.lines[1].taps[2])


# -- review and marks ------------------------------------------------------------------------------------
def test_review_follows_the_tapped_times(tmp_path):
    doc, c = project(tmp_path)
    tap_syllables(c, 0, 10000)                   # あ 10000-10250, い 10300-10550
    tap_syllables(c, 1, 12000)
    c.review_ms = 10400
    taps = c.lines[0].taps
    assert c.current_line() == 0
    assert c.syllable_states(0) == {taps[0]: "done", taps[1]: "active"}
    assert c.review_upcoming(3) == [(0, taps[1]), (1, c.lines[1].taps[0]), (1, c.lines[1].taps[1])]
    c.review_ms = 11800                          # shortly before line 2
    assert c.current_line() == 1
    assert c.syllable_at(1, 11800) == c.lines[1].taps[0]


def test_marks(tmp_path):
    doc, c = project(tmp_path)
    tap_syllables(c, 0, 10000)
    tap_syllables(c, 1, 12000)
    t0, t1 = c.lines[0].taps, c.lines[1].taps
    assert c.toggle_mark(1, t1[2]) and c.toggle_mark(0, t0[1])
    assert c.next_mark(0) == (0, t0[1]) and c.next_mark(0, t0[1]) == (1, t1[2])
    assert c.next_mark(1, t1[2]) == (0, t0[1])          # wraps around
    assert c.unmark_last() == (0, t0[1]) and c.marked(0) == set()
    assert any("標記" in i.text for i in c.check())
    tap_syllables(c, 1, 12000)                   # retapping the line clears its marks
    assert c.marked(1) == set()


def test_marks_are_saved(tmp_path):
    doc, c = project(tmp_path)
    tap_syllables(c, 0, 10000)
    c.toggle_mark(0, c.lines[0].taps[1])
    c.save_progress()
    c2 = Controller(doc, Settings(), c.progress_path)
    c2.load_progress()
    assert c2.marked(0) == {c.lines[0].taps[1]}


# -- pre-export check -----------------------------------------------------------------------------------
def test_check_finds_problems(tmp_path):
    doc, c = project(tmp_path)
    tap_line(c, 0, 10000, 12400)
    tap_line(c, 1, 12450, 13000)
    tap_line(c, 2, 12600, 14000)                 # overlaps line 2, appears late
    c.start_take(0)
    c.down("F", 10000); c.up("F", 10020)         # 20 ms: a slip
    c.down("J", 10100); c.up("J", 14000)         # 3.9 s
    issues = c.check()
    texts = [(i.line, i.level, i.text) for i in issues]
    assert (0, "warn", "音節「あ」只有 20 ms，可能是誤觸") in texts
    assert (0, "warn", "音節「い」長達 3.9 秒") in texts
    assert any(l == 1 and "重疊" in t for l, _, t in texts)
    assert any(l == 2 and "已經開唱" in t for l, _, t in texts)
    assert (1, "info", "還沒打音節") in texts
    assert [i.level for i in issues] == sorted((i.level for i in issues), key=["error", "warn", "info"].index)


def test_check_reports_untimed_lines_for_the_line_page(tmp_path):
    doc, c = project(tmp_path)
    issues = c.check()
    assert {(i.text, i.page) for i in issues} == {("還沒打行軸", "lines")}


def test_check_flags_taps_outside_the_line():
    doc = AssDocument.load(DATA / "notebook_sample.ass")
    c = Controller(doc, Settings())
    c.start_take(0)
    for k in range(len(c.lines[0].taps)):
        c.down("F", 500 + k * 300); c.up("F", 700 + k * 300)    # line starts at 1.00
    assert c.check()[0].level == "error"


def test_a_mark_goes_to_the_syllable_just_heard(tmp_path):
    doc, c = project(tmp_path)
    tap_syllables(c, 0, 10000)                   # あ 10000-10250, い 10300-10550
    tap_syllables(c, 1, 12000)
    t0, t1 = c.lines[0].taps, c.lines[1].taps
    assert c.mark_target(9000) is None
    assert c.mark_target(10100) == (0, t0[0])
    assert c.mark_target(10600) == (0, t0[1])    # after い ended: still い
    assert c.mark_target(11900) == (0, t0[1])    # before line 2 starts: the last one heard
    assert c.mark_target(12010) == (1, t1[0])


def test_undo_keeps_marks_made_since(tmp_path):
    doc, c = project(tmp_path)
    tap_syllables(c, 0, 10000)
    c.insert_line(0, "{\\k1}さ")
    c.toggle_mark(0, c.lines[0].taps[0])
    c.undo_edit()
    assert c.marked(0) == {c.lines[0].taps[0]} and len(c.lines) == 3


def test_failed_split_edit_leaves_no_undo_step(tmp_path):
    import pytest
    doc, c = project(tmp_path)
    with pytest.raises(ValueError):
        c.edit_line(0, "{\\k1}！")
    assert c.undo_stack == []


def test_merging_the_last_line_leaves_no_undo_step(tmp_path):
    import pytest
    doc, c = project(tmp_path)
    with pytest.raises(ValueError):
        c.merge_with_next(len(c.lines) - 1)
    assert c.undo_stack == []


def test_unmark_after_deleting_a_marked_line(tmp_path):
    doc, c = project(tmp_path)
    tap_syllables(c, 0, 10000)
    tap_syllables(c, 1, 12000)
    c.toggle_mark(0, c.lines[0].taps[0])
    c.toggle_mark(1, c.lines[1].taps[0])
    c.delete_line(1)
    assert c.unmark_last() == (0, c.lines[0].taps[0])     # the deleted line's mark is skipped
    assert c.unmark_last() is None
    c.undo_edit()                                          # the deleted line comes back with its mark
    assert c.marked(1) == {c.lines[1].taps[0]}


def test_backspace_still_unmarks_after_undo_and_reopening(tmp_path):
    doc, c = project(tmp_path)
    tap_syllables(c, 0, 10000)
    t0 = c.lines[0].taps
    c.toggle_mark(0, t0[0])
    c.toggle_mark(0, t0[1])
    c.insert_line(2, "{\\k1}さ")
    c.undo_edit()
    assert c.unmark_last() == (0, t0[1])
    c.save_progress()
    c2 = Controller(doc, Settings(), c.progress_path)
    c2.load_progress()
    assert c2.unmark_last() == (0, t0[0])


def test_inserting_several_lines_is_one_undo_step(tmp_path):
    doc, c = project(tmp_path)
    assert c.insert_lines(0, ["{\\k1}さ", "{\\k1}し"]) == 1
    assert [l.plain for l in c.lines] == ["あい", "さ", "し", "うえお", "か"]
    assert c.undo_edit() == "插入行" and len(c.lines) == 3


def test_a_damaged_backup_changes_nothing(tmp_path):
    import pytest
    doc, c = project(tmp_path)
    tap_line(c, 0, 10000, 12000)
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"line_taps": {"0": {"span": [1, 2]}}}), encoding="utf-8")   # no "text"
    with pytest.raises(ValueError):
        c.restore_backup(bad)
    assert c.line_taps == {0: (10000, 12000)} and c.undo_label() == "打行軸 第 1 行"


def test_layout_fixed_gives_the_same_times(tmp_path):
    doc, c = project(tmp_path)
    tap_line(c, 0, 10000, 12000)
    tap_line(c, 1, 12500, 14000)
    tap_syllables(c, 0, 10000)
    plain = ([c.line_start(i) for i in range(3)], c.export_bytes())
    with c.layout_fixed():
        assert ([c.line_start(i) for i in range(3)], c.export_bytes()) == plain
    assert c._placements is None
