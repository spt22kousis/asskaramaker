import pytest

from karatap.clock import SongClock


def feed(clock, gen, t0, p0, n=20, period=0.01, jitter=None):
    for k in range(n):
        j = jitter[k % len(jitter)] if jitter else 0.0
        clock.add_anchor(gen, t0 + k * period + j, p0 + k * period)


def test_interpolates_between_callbacks():
    c = SongClock()
    gen = c.reset(10.0, 1.0)
    c.set_playing(True)
    feed(c, gen, t0=100.0, p0=10.0)
    assert c.song_ms_at(100.2345) == pytest.approx(10234.5, abs=1e-6)


def test_speed_scaling_and_latency_in_wall_time():
    c = SongClock()
    gen = c.reset(20.0, 0.5)          # stretched position 20 s at half speed = song 10 s
    c.set_playing(True)
    feed(c, gen, t0=50.0, p0=20.0)
    assert c.song_ms_at(50.1) == pytest.approx(10050.0)
    # 40 ms of reaction latency is 40 ms of wall time = 20 ms of song time at 0.5x
    assert c.song_ms_at(50.1, latency_ms=40) == pytest.approx(10030.0)


def test_median_rejects_jittery_anchors_and_old_generations():
    c = SongClock()
    old = c.reset(0.0, 1.0)
    gen = c.reset(5.0, 1.0)
    c.set_playing(True)
    c.add_anchor(old, 0.0, 99.0)      # stale generation ignored
    feed(c, gen, t0=1.0, p0=5.0, jitter=[0, 0, 0.004, 0, -0.003, 0, 0.03])
    assert c.song_ms_at(1.5) == pytest.approx(5500.0, abs=0.5)


def test_paused_returns_fixed_position_and_compensation_applies():
    c = SongClock()
    c.reset(3.0, 0.75, comp_ms=8.0)
    assert c.song_ms_at(123.0) == pytest.approx(3.0 * 0.75 * 1000 + 8.0)
