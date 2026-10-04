"""Pattern and sweep descriptions print a level with the one price formatter.

A fixed two decimals turned every sub-cent level into ``$0.00``. Four
decimals on a sweep still turned a sub-cent-of-a-cent level into
``$0.0000``. ``fmt_price`` is the formatter the cards already use: more
places as the price gets smaller, an em dash when the level cannot be
read, and a measured zero left as zero.
"""

import re

import numpy as np

from bot.core.chart_patterns import (
    detect_double_top_bottom,
    detect_liquidity_sweep,
    detect_rectangle,
)
from bot.core.liquidity_sweep import detect_sweeps
from bot.formatters.price_text import fmt_price

# ``$0.00`` as its own token. ``$0.00001230`` and ``$0.000000`` contain
# those characters and are a longer price, so the digit after the two
# places keeps them out of this match.
_COLLAPSED_DOLLAR = re.compile(r"\$0\.00(?!\d)")


def _no_collapsed_zero(text: str) -> None:
    assert _COLLAPSED_DOLLAR.search(text) is None, text


def _sweep_book(level: float, kind: str, *, volume: float | None):
    """40 bars. The swing at index 15 sits on ``level``; bar 37 sweeps it.

    The ratios are the book ``test_liquidity_sweep_own_close`` already
    qualifies, scaled so the swept level is the price under test.
    """
    n = 40
    opens = np.full(n, level)
    if volume is None:
        volumes = None
    else:
        volumes = np.full(n, 10.0)
        volumes[37] = volume
    if kind == "bull":
        flat = level * (100.0 / 99.0)
        highs = np.full(n, flat * (100.5 / 100.0))
        lows = np.full(n, flat)
        lows[15] = level
        lows[37] = level * (98.7 / 99.0)
        highs[37] = flat * (100.4 / 100.0)
        close = level * (99.5 / 99.0)
    else:
        flat = level * (100.0 / 101.0)
        highs = np.full(n, flat)
        lows = np.full(n, flat * (99.5 / 100.0))
        highs[15] = level
        highs[37] = level * (101.3 / 101.0)
        lows[37] = flat * (99.6 / 100.0)
        close = level * (100.4 / 101.0)
    closes = np.full(n, flat)
    closes[37] = close
    opens[37] = flat
    return opens, highs, lows, closes, volumes


def _sweep(level: float, kind: str, *, volume: float | None = 80.0):
    opens, highs, lows, closes, volumes = _sweep_book(level, kind, volume=volume)
    sigs = detect_sweeps(opens, highs, lows, closes, volumes)
    matched = [s for s in sigs if s.level_price == float(level)]
    assert matched, [(s.level_price, s.description) for s in sigs]
    pattern = detect_liquidity_sweep(
        highs, lows, closes, opens=opens, volumes=volumes,
    )
    assert pattern is not None
    assert pattern["description"] == matched[0].description
    return matched[0].description


def _rectangle(support: float, resistance: float):
    n = 8
    mid = (support + resistance) / 2.0
    closes = np.full(n, mid)
    highs = np.full(n, resistance)
    lows = np.full(n, support)
    swings = {
        "swing_highs": [(1, resistance), (4, resistance)],
        "swing_lows": [(2, support), (5, support)],
    }
    res = detect_rectangle(highs, lows, closes, swings=swings)
    assert res is not None
    assert res["name"] == "Rectangle"
    return res["description"]


class TestSweepDescriptions:
    def test_a_sub_cent_sweep_is_not_zero_dollars(self):
        level = 0.0000123
        for kind in ("bull", "bear"):
            for volume in (80.0, None):
                text = _sweep(level, kind, volume=volume)
                rendered = fmt_price(level)
                assert rendered in text, text
                assert rendered != "$0.00"
                assert rendered != "$0.0000"
                _no_collapsed_zero(text)

    def test_an_ordinary_sweep_still_reads_as_dollars(self):
        text = _sweep(1234.5, "bull")
        assert fmt_price(1234.5) in text
        assert "$1,234.50" in text

    def test_an_unreadable_bar_is_not_a_zero_dollar_sweep(self):
        opens, highs, lows, closes, volumes = _sweep_book(
            0.0000123, "bull", volume=80.0,
        )
        closes[37] = np.nan
        assert detect_sweeps(opens, highs, lows, closes, volumes) == []
        assert detect_liquidity_sweep(
            highs, lows, closes, opens=opens, volumes=volumes,
        ) is None
        for bad in (None, float("nan"), "nope"):
            rendered = fmt_price(bad)
            assert rendered == "\u2014"
            assert "$0.00" not in rendered


class TestChartPatternDescriptions:
    def test_a_sub_cent_range_is_not_zero_dollars(self):
        support, resistance = 0.0000102, 0.0000123
        text = _rectangle(support, resistance)
        assert text == (
            f"Range: {fmt_price(support)} - {fmt_price(resistance)}"
        )
        assert "$0.00001020" in text
        assert "$0.00001230" in text
        _no_collapsed_zero(text)

    def test_an_ordinary_range_still_reads_as_dollars(self):
        text = _rectangle(1200.0, 1234.5)
        assert text == "Range: $1,200.00 - $1,234.50"
        assert fmt_price(1234.5) in text

    def test_a_measured_zero_level_stays_the_formatters_zero(self):
        n = 12
        closes = np.full(n, 1.5)
        highs = np.full(n, 1.2)
        lows = np.full(n, 0.2)
        swings = {
            "swing_lows": [(2, 0.0), (10, 0.0)],
            "swing_highs": [(6, 1.0)],
        }
        res = detect_double_top_bottom(highs, lows, closes, swings=swings)
        assert res is not None
        assert res["name"] == "Double Bottom"
        assert fmt_price(0.0) == "$0.000000"
        assert "$0.000000" in res["description"]
        _no_collapsed_zero(res["description"])

    def test_an_unreadable_range_is_not_described_as_zero_dollars(self):
        n = 8
        closes = np.full(n, 1.0)
        highs = np.full(n, 1.1)
        lows = np.full(n, 0.9)
        swings = {
            "swing_highs": [(1, float("nan")), (4, float("nan"))],
            "swing_lows": [(2, 0.9), (5, 0.9)],
        }
        res = detect_rectangle(highs, lows, closes, swings=swings)
        assert res is None
