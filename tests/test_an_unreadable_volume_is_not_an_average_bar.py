"""An unreadable volume is not a 1.0x average.

#508 gave the sweep detector a None path for absent volume and kept the old
fallback beside it: `np.mean` over a window holding a NaN is NaN, `nan > 0` is
False, and the ratio became 1.0, recorded as `volume_ratio` 1.0 and printed
"vol 1.0x avg" and "x1.0" where an absent volume prints a dash. /sweep, the
bridge's /patterns and the scanners hand raw venue volumes to the detector
with no finite filter. A zero average took the same fallback.
"""
import numpy as np
import pytest

from bot.core.liquidity_sweep import detect_sweeps
from bot.formatters.market_cards import render_sweeps
from tests.test_liquidity_sweep_own_close import _book


def _sweeps(volumes):
    opens, highs, lows, closes, vols = _book(kind="bull", close=99.5, volume=80.0)
    if volumes == "nan_in_window":
        vols = vols.astype(float)
        vols[len(vols) - 10] = np.nan
    elif volumes == "nan_on_bar":
        vols = vols.astype(float)
        vols[:] = np.where(np.arange(len(vols)) >= len(vols) - 5, np.nan, vols)
    elif volumes == "zero":
        vols = np.zeros(len(vols))
    return detect_sweeps(opens, highs, lows, closes, vols)


def test_a_measured_window_still_reads_a_multiple():
    sigs = _sweeps("measured")
    assert sigs and sigs[0].volume_ratio is not None
    assert "×" in render_sweeps("BTC/USDT", 100.0, sigs)


def test_a_nan_in_the_window_is_unmeasured_not_one_x():
    sigs = _sweeps("nan_in_window")
    assert sigs, "the price action is still a sweep"
    assert sigs[0].volume_ratio is None
    assert "1.0x" not in sigs[0].description
    card = render_sweeps("BTC/USDT", 100.0, sigs)
    assert "vol   —" in card and "×1.0" not in card


def test_a_nan_on_the_sweep_bar_is_unmeasured():
    # The bar is inside the window, so its NaN leaves the average unmeasured.
    sigs = _sweeps("nan_on_bar")
    assert sigs and sigs[0].volume_ratio is None


def test_a_zero_average_is_unmeasured():
    sigs = _sweeps("zero")
    assert sigs and sigs[0].volume_ratio is None
    assert "1.0x" not in sigs[0].description


def test_the_confidence_does_not_move_on_an_unmeasured_ratio():
    # 1.0x added nothing to the confidence; unmeasured adds nothing either.
    nan = _sweeps("nan_in_window")[0]
    bare = _book(kind="bull", close=99.5, volume=80.0)
    absent = detect_sweeps(bare[0], bare[1], bare[2], bare[3], None)[0]
    assert nan.confidence == absent.confidence



@pytest.mark.parametrize("kind,close,swept", [
    ("bull", 99.5, "bullish_sweep"), ("bear", 100.5, "bearish_sweep"),
])
def test_both_sides_read_a_zero_average_as_unmeasured(kind, close, swept):
    # The detector computes the ratio twice, once per side of the book.
    book = _book(kind=kind, close=close, volume=80.0)
    measured = detect_sweeps(*book)
    assert [s.sweep_type for s in measured] == [swept]
    assert measured[0].volume_ratio is not None and measured[0].volume_ratio > 1
    zero = detect_sweeps(*book[:4], np.zeros(len(book[4])))
    assert [s.sweep_type for s in zero] == [swept]
    assert zero[0].volume_ratio is None
