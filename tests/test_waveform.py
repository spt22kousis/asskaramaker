import numpy as np

from karatap.waveform import colormap, peaks, spectrogram

SR = 48000


def tone(freq, seconds, amp=0.5):
    t = np.arange(int(SR * seconds)) / SR
    x = (amp * np.sin(2 * np.pi * freq * t) * 32767).astype(np.int16)
    return np.repeat(x[:, None], 2, axis=1)


def test_peaks_follow_the_signal_and_pad_outside_the_song():
    pcm = np.concatenate([np.zeros((SR, 2), np.int16), tone(440, 1.0)])
    mins, maxs = peaks(pcm, SR, -1000, 2000, 300)          # 1 s before the song starts
    assert len(maxs) == 300
    assert maxs[:100].max() == 0                           # padding
    assert maxs[100:200].max() == 0                        # silence
    assert 0.45 < maxs[220] < 0.55 and -0.55 < mins[220] < -0.45


def test_spectrogram_puts_a_tone_on_the_right_row():
    pcm = tone(1000, 2.0)
    img = spectrogram(pcm, SR, 0, 2000, 200, 120)
    assert img.shape == (120, 200) and img.dtype == np.uint8
    rows = np.geomspace(8000, 80, 120)
    peak_row = int(np.argmax(img[:, 100]))
    assert abs(rows[peak_row] - 1000) / 1000 < 0.1


def test_colormap_shape():
    lut = colormap()
    assert lut.shape == (256, 3) and tuple(lut[0]) == (0, 0, 0)
