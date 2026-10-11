from karatap.layout import LayoutParams, Placement, place


def test_notebook_rule():
    spans = [(10000, 12000), (12500, 14000), (14500, 16000), (16500, 18000), (25000, 27000)]
    assert place(spans, LayoutParams()) == [
        Placement(9000, 12000, "Odd"),        # first of a segment: 1 s early
        Placement(9000, 14000, "Even"),       # second: together with the first
        Placement(12300, 16000, "Odd"),       # then 0.3 s after the same row is free
        Placement(14300, 18000, "Even"),
        Placement(25000, 27000, "Default"),   # alone in its segment: as sung
    ]


def test_late_lines_are_clamped_and_flagged():
    spans = [(10000, 12400), (12450, 13000), (12600, 14000)]
    out = place(spans, LayoutParams())
    assert out[2] == Placement(12600, 14000, "Odd", late=True)


def test_untimed_lines_split_segments_and_params_apply():
    p = LayoutParams(odd_style="K1", even_style="K2", single_style="K1", lead_ms=500, tail_ms=200)
    out = place([(1000, 2000), None, (2500, 3000), (3100, 4000)], p)
    assert out[0] == Placement(1000, 2200, "K1")
    assert out[1] is None
    assert out[2] == Placement(2000, 3200, "K1")
    assert out[3] == Placement(2000, 4200, "K2")


def test_start_never_negative():
    assert place([(300, 900), (1000, 2000)], LayoutParams())[0].start == 0
