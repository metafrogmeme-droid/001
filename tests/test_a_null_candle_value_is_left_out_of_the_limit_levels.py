"""A candle value the venue did not state is left out of the limit's levels.

`calculate_entry` places the executor's limit when an idea's limit would
cross the market, and it builds its levels (VWAP, EMA9/EMA20, the session
range) from recent 1h candles. The EMA and the range skipped a null value;
the VWAP read it as 0, so one null high and one null low among thirty bars
moved the VWAP from 100.00 to 97.78 and took it out of the cluster the limit
is priced from. Every reader now takes the same values: a price the row
states (`price_on_record`) and a volume it states (`volume_on_record`).
"""
from __future__ import annotations

import pytest

from bot.core.limit_entry import _calculate_vwap, calculate_entry


def _rows(n: int = 30) -> list[list]:
    return [[i, 100.0, 101.0, 99.0, 100.0, 10.0] for i in range(n)]


def _with(rows, *edits):
    out = [list(r) for r in rows]
    for i, col, v in edits:
        out[i][col] = v
    return out


def _levels(result) -> list[str]:
    return list(result.levels_used)


class TestTheVwap:
    @pytest.mark.parametrize("bad", [None, float("nan"), float("inf"), 0.0, -1.0, "n/a"])
    def test_an_unstated_high_low_or_close_is_left_out(self, bad):
        clean = _calculate_vwap(_rows())
        for col in (2, 3, 4):
            assert _calculate_vwap(_with(_rows(), (5, col, bad))) == clean

    def test_the_bar_left_out_carries_no_weight(self):
        rows = _with(_rows(), (0, 2, None), (0, 3, 50.0), (0, 4, 50.0), (0, 5, 1000.0))
        assert _calculate_vwap(rows) == 100.0

    @pytest.mark.parametrize("vol", [None, 0.0, -5.0, float("nan"), "x"])
    def test_a_bar_with_no_stated_volume_adds_nothing(self, vol):
        rows = _with(_rows(), (0, 2, 200.0), (0, 3, 200.0), (0, 4, 200.0), (0, 5, vol))
        assert _calculate_vwap(rows) == 100.0

    def test_nothing_stated_is_no_vwap(self):
        rows = [[i, 100.0, None, 99.0, 100.0, 10.0] for i in range(30)]
        assert _calculate_vwap(rows) is None


class TestTheEntry:
    def test_null_highs_and_lows_leave_the_long_limit_as_it_was(self):
        clean = calculate_entry(current_price=100.5, direction="LONG",
                                atr_value=1.0, ohlcv=_rows())
        bad = calculate_entry(current_price=100.5, direction="LONG", atr_value=1.0,
                              ohlcv=_with(_rows(), (5, 2, None), (9, 3, None)))
        assert (bad.limit_price, bad.tier, _levels(bad)) == (
            clean.limit_price, clean.tier, _levels(clean))

    def test_a_nan_close_does_not_reach_the_emas(self):
        clean = calculate_entry(current_price=100.5, direction="LONG",
                                atr_value=1.0, ohlcv=_rows())
        bad = calculate_entry(current_price=100.5, direction="LONG", atr_value=1.0,
                              ohlcv=_with(_rows(), (29, 4, float("nan"))))
        assert any(x.startswith("EMA9") for x in _levels(clean))
        assert _levels(bad) == _levels(clean)

    def test_a_nan_at_the_start_of_the_session_window_does_not_hide_the_high(self):
        rows = _with(_rows(), (29, 2, 102.0))
        clean = calculate_entry(current_price=100.5, direction="SHORT",
                                atr_value=1.0, ohlcv=rows)
        bad = calculate_entry(current_price=100.5, direction="SHORT", atr_value=1.0,
                              ohlcv=_with(rows, (-24, 2, float("nan"))))
        assert any(x.startswith("Session high") for x in _levels(clean))
        assert _levels(bad) == _levels(clean)

    def test_a_nan_at_the_start_of_the_session_window_does_not_hide_the_low(self):
        rows = _with(_rows(), (29, 3, 98.0))
        clean = calculate_entry(current_price=99.5, direction="LONG",
                                atr_value=1.0, ohlcv=rows)
        bad = calculate_entry(current_price=99.5, direction="LONG", atr_value=1.0,
                              ohlcv=_with(rows, (-24, 3, float("nan"))))
        assert any(x.startswith("Session low") for x in _levels(clean))
        assert _levels(bad) == _levels(clean)

    @pytest.mark.parametrize("direction", ["LONG", "SHORT"])
    def test_a_window_that_states_no_range_is_no_session_level(self, direction):
        rows = [[i, 100.0, None, None, 100.0, 10.0] for i in range(30)]
        got = calculate_entry(current_price=100.5, direction=direction,
                              atr_value=1.0, ohlcv=rows)
        assert not any(x.startswith("Session") for x in _levels(got))
        assert not any(x.startswith("VWAP") for x in _levels(got))
        assert got.limit_price > 0
