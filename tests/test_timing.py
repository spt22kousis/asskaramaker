import pytest

from karatap.timing import (END_KEY, HOLD, INSERT_GAP, ONSET, TAPPED, TapMachine,
                            TimingOptions, compute_line, to_cs)


def bounds(m):
    return list(zip(m.start, m.end))


# -- TapMachine -----------------------------------------------------------------

def test_hold_basic_and_key_repeat_filtered():
    m = TapMachine(3, HOLD)
    m.down("F", 1000)
    m.down("F", 1030)          # OS key repeat: ignored
    m.up("F", 1200)
    m.down("F", 1300)
    m.up("F", 1450)
    m.down("J", 1500)
    m.up("J", 1700)
    assert bounds(m) == [(1000, 1200), (1300, 1450), (1500, 1700)]
    assert m.down("F", 1800) is None            # nothing left


def test_hold_two_key_overlap_ends_previous_at_next_press():
    m = TapMachine(3, HOLD)
    m.down("F", 1000)
    m.down("J", 1200)          # F still held -> syllable 0 ends here
    m.up("F", 1250)            # late release of F is ignored
    m.down("F", 1400)          # J still held
    m.up("J", 1420)
    m.up("F", 1600)
    assert bounds(m) == [(1000, 1200), (1200, 1400), (1400, 1600)]


def test_onset_mode_release_ends_only_line_last():
    m = TapMachine(3, ONSET, line_last=[False, False, True])
    m.down("F", 1000); m.up("F", 1050)
    m.down("J", 1200); m.up("J", 1250)
    m.down("F", 1400)
    assert m.end[:2] == [1200, 1400] and m.end[2] is None
    m.up("F", 1700)
    assert bounds(m) == [(1000, 1200), (1200, 1400), (1400, 1700)]


def test_onset_mode_end_key():
    m = TapMachine(2, ONSET, line_last=[False, True], onset_end=END_KEY)
    m.down("F", 1000); m.up("F", 1050)
    m.down("J", 1200); m.up("J", 1250)       # release ignored in end-key mode
    assert m.end[1] is None
    m.end_key(1600)
    assert bounds(m) == [(1000, 1200), (1200, 1600)]


def test_onset_line_last_ended_by_next_line_press_if_never_released():
    m = TapMachine(3, ONSET, line_last=[False, True, True], onset_end=END_KEY)
    m.down("F", 1000); m.down("J", 1200); m.down("K", 1500)
    assert m.end[1] == 1500


def test_undo_replays_state():
    m = TapMachine(3, HOLD)
    m.down("F", 1000); m.up("F", 1100)
    m.down("J", 1200); m.up("J", 1300)
    assert m.undo() == (1, 1200)
    assert m.next == 1 and bounds(m)[1] == (None, None) and bounds(m)[0] == (1000, 1100)
    m.down("J", 1210); m.up("J", 1320)
    assert bounds(m)[1] == (1210, 1320)


def test_skip():
    m = TapMachine(4, HOLD)
    m.skip_to(2)
    m.down("F", 5000); m.up("F", 5100)
    assert m.start == [None, None, 5000, None]


# -- compute_line ---------------------------------------------------------------

KEEP_OPTS = TimingOptions()


def test_to_cs_rounds_half_up():
    assert to_cs(1004.9) == 100 and to_cs(1005) == 101 and to_cs(999.5) == 100


def test_keep_mode_lead_insert_merge_and_tail():
    # 3 syllables, no gap syllables; line 1.00-3.00
    t = compute_line([False] * 3, [(1200, 1500), (1600, 2000), (2000, 2400)], 1000, 3000, KEEP_OPTS)
    assert t.inserts == {0: 20}                 # leading gap inserted
    assert t.values == [40, 40, 100]            # gap 1500-1600 merged into syl 0; tail into last
    assert t.total == 200 and (t.start_cs, t.end_cs) == (100, 300)
    assert t.warnings == []


def test_keep_mode_uses_existing_gap_syllables():
    gaps = [True, False, True, True, False, True]
    t = compute_line(gaps, [(1200, 1500), (1600, 2000)], 1000, 3000, KEEP_OPTS)
    assert t.inserts == {}
    assert t.values == [20, 30, 10, 0, 40, 100]
    assert t.total == 200


def test_insert_gap_policy():
    opts = TimingOptions(gap_policy=INSERT_GAP)
    t = compute_line([False] * 2, [(1000, 1500), (1600, 2000)], 1000, 2000, opts)
    assert t.values == [50, 40] and t.inserts == {1: 10}


def test_onset_bounds_make_gap_syllables_zero():
    gaps = [False, True, False]
    t = compute_line(gaps, [(1000, 1400), (1400, 1900)], 1000, 2000, KEEP_OPTS)
    assert t.values == [40, 0, 60]


def test_rounding_sums_exactly():
    b = [(1004.4, 1123.6), (1123.6, 1248.5), (1248.5, 1374.9), (1374.9, 1501.2)]
    t = compute_line([False] * 4, b, 1000, 1600, KEEP_OPTS)
    assert t.total == 60
    # boundaries round to 100, 112, 125, 137 (cs); values are their differences
    assert t.values == [12, 13, 12, 23]
    assert t.inserts == {}


def test_out_of_bounds_warns_and_clamps():
    t = compute_line([False] * 2, [(900, 1200), (1300, 2100)], 1000, 2000, KEEP_OPTS)
    assert t.warnings == ["early", "late"]
    assert t.total == 100 and t.inserts == {}
    assert t.values == [30, 70]


def test_tapped_mode_with_padding():
    opts = TimingOptions(line_timing=TAPPED, pre_pad_ms=200, post_pad_ms=300)
    t = compute_line([True, False, False, True], [(1200, 1500), (1500, 1800)], 0, 99999, opts)
    assert (t.start_cs, t.end_cs) == (100, 210)
    assert t.values == [20, 30, 30, 30]
    assert t.warnings == []


def test_tapped_mode_without_padding_inserts_nothing():
    opts = TimingOptions(line_timing=TAPPED)
    t = compute_line([False, False], [(1200, 1500), (1500, 1800)], 0, 0, opts)
    assert (t.start_cs, t.end_cs, t.values, t.inserts) == (120, 180, [30, 30], {})


def test_bounds_count_must_match():
    with pytest.raises(ValueError):
        compute_line([False, True, False], [(0, 1)], 0, 10, KEEP_OPTS)
