"""A candle whose price the venue did not state makes the series MISSING, not neutral.

`Analyzer.analyze` refuses a series with a non-finite or non-positive OHLC
value. Two readers that compute off the rows themselves did not:

* the scanner's `_scan_symbol`: `np.array` turns a null close into NaN, the
  engine read it hands the rows to fails, and `price > sma50` is False, so
  driven, a clean 4h uptrend read LONG 0.59 with every close read and SHORT
  0.5 (over the 0.4 setup gate) with one null close at bar 80;
* the MTF analyzer's `_analyze_single_tf`: the EMA stays NaN from the null bar
  on, so a daily downtrend read "neutral" and the alignment gate skipped.

And a volume the venue did not state is sanitized to 0 by `analyze()`, which
made OBV constant, which the OBV trend read as "falling" (`>` else falling),
and the OBV voter cast -1.0 at weight 0.6.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import numpy as np
import pytest

from bot.compat import UTC
from bot.core.analyzer import Analyzer
from bot.core.multi_timeframe import MTFConfluence, _analyze_single_tf
from bot.core.ta_utils import Regime
from bot.skills import scan_skill
from bot.utils.candles import ohlc_on_record, volume_on_record
from bot.utils.models import MarketSignal

H4 = 4 * 3600 * 1000
D = 86_400_000


def _up4h(null_at=None, forming_null=False, n=100):
    last_closed = (int(time.time() * 1000) // H4) * H4 - H4
    t0 = last_closed - (n - 1) * H4
    out, p = [], 100.0
    for i in range(n):
        o, c = p, p * 1.004
        p = c
        r = [t0 + i * H4, o, c * 1.002, o * 0.998, c, 1000.0]
        if i == null_at:
            r[4] = None
        out.append(r)
    if forming_null:
        out.append([last_closed + H4, p, p * 1.001, p * 0.999, None, 10.0])
    return out


class _Ex:
    def __init__(self, rows):
        self.rows = rows

    async def fetch_ohlcv(self, s, tf, limit=100):
        return self.rows


# ── the shared reading ──────────────────────────────────────────────────


@pytest.mark.parametrize("bad", [None, float("nan"), float("inf"), 0, -1.0, "x", True])
def test_one_unreadable_price_fails_the_series(bad):
    rows = [[0, 1.0, 2.0, 0.5, 1.5, 10.0], [1, 1.0, 2.0, 0.5, 1.5, 10.0]]
    assert ohlc_on_record(rows) is True
    for col in (1, 2, 3, 4):
        broken = [list(r) for r in rows]
        broken[1][col] = bad
        assert ohlc_on_record(broken) is False, (col, bad)


def test_a_row_short_of_a_close_fails_the_series():
    assert ohlc_on_record([[0, 1.0, 2.0, 0.5]]) is False


def test_the_volume_column_is_not_part_of_the_price_check():
    assert ohlc_on_record([[0, 1.0, 2.0, 0.5, 1.5, None]]) is True


@pytest.mark.parametrize("v,expected", [(0.0, 0.0), (12.5, 12.5), (None, None),
                                        (float("nan"), None), (-1.0, None), (True, None),
                                        ("x", None), (float("inf"), None)])
def test_a_volume_is_on_record_only_when_stated(v, expected):
    assert volume_on_record(v) == expected


# ── the scanner ─────────────────────────────────────────────────────────


def test_a_clean_uptrend_reads_long():
    r = asyncio.run(scan_skill._scan_symbol(_Ex(_up4h()), "SOL/USDT", None))
    assert r and r["dir"] == "LONG"


@pytest.mark.parametrize("bar", [80, 98])
def test_a_null_close_on_a_closed_bar_is_a_missing_symbol_not_a_short(bar):
    r = asyncio.run(scan_skill._scan_symbol(_Ex(_up4h(null_at=bar)), "SOL/USDT", None))
    assert r is None, f"a series with a null close read {r and r['dir']}"


def test_a_null_close_on_the_forming_bar_costs_only_the_mark():
    """The forming bar is dropped before the window is read; its close was the
    MARK, and a null mark falls back to the last closed close rather than
    raising (`float(None)`)."""
    rows = _up4h(forming_null=True)
    r = asyncio.run(scan_skill._scan_symbol(_Ex(rows), "SOL/USDT", None))
    assert r and r["dir"] == "LONG"
    assert r["price"] == pytest.approx(rows[-2][4])


def test_the_scan_counts_a_missing_series_as_unread(monkeypatch):
    """`_scan_batch`'s coverage note names what it could not read."""
    import bot.formatters.signal_card as signal_card

    monkeypatch.setattr(scan_skill, "UNIVERSE", ["SOL/USDT", "ETH/USDT"])
    monkeypatch.setattr(scan_skill, "_push_scan_to_dashboard", lambda *a, **k: None)
    monkeypatch.setattr(signal_card, "render_scan_results_card",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no png")))

    class _Two:
        async def fetch_ohlcv(self, s, tf, limit=100):
            return _up4h(null_at=80) if s == "SOL/USDT" else _up4h()

    msg = SimpleNamespace(edit_text=AsyncMock(), delete=AsyncMock())
    update = SimpleNamespace(message=SimpleNamespace(reply_text=AsyncMock(return_value=msg)),
                             effective_user=SimpleNamespace(id=1),
                             effective_chat=SimpleNamespace(id=1))
    engine = SimpleNamespace(analyzer=None, _pending_ideas={}, _pending_atr={}, _halted=False,
                             scanner=SimpleNamespace(_get_exchange=AsyncMock(return_value=_Two())))
    asyncio.run(scan_skill._scan_batch(update, SimpleNamespace(bot=SimpleNamespace(),
                                                               bot_data={}),
                                       engine, top_n=10, patterns=False, ai=False))
    text = msg.edit_text.await_args_list[-1].args[0]
    assert "SOLUSDT" not in text and "ETHUSDT" in text
    assert "1 of 2" in text or "1/2" in text


# ── the MTF analyzer ────────────────────────────────────────────────────


def _down(n, tf, null_at=None):
    out, p = [], 100.0
    for i in range(n):
        o, c = p, p * 0.99
        p = c
        row = [i * tf, o, o * 1.002, c * 0.998, c, 1000.0]
        if i == null_at:
            row[4] = None
        out.append(row)
    return out


def test_a_null_close_drops_the_timeframe_rather_than_reading_it_neutral():
    assert _analyze_single_tf(_down(200, D, null_at=150), "1d") is None
    assert _analyze_single_tf(_down(200, D), "1d")["trend"] == "bearish"


def test_a_daily_downtrend_with_one_null_close_does_not_read_neutral():
    m = MTFConfluence()
    ok = m.analyze(candles_4h=_down(200, H4), candles_1d=_down(200, D))
    bad = m.analyze(candles_4h=_down(200, H4), candles_1d=_down(200, D, null_at=150))
    assert ok.htf_trend == "bearish" and sorted(ok.per_tf) == ["1d", "4h"]
    assert sorted(bad.per_tf) == ["4h"], "the unreadable daily series is MISSING"
    assert bad.htf_trend == "bearish", "it read neutral off a NaN EMA"
    assert bad.confidence < ok.confidence, "one timeframe fewer is less confidence"


# ── OBV ─────────────────────────────────────────────────────────────────


def _indicators(volume):
    rng = np.random.default_rng(7)
    rows, p = [], 100.0
    for i in range(100):
        o = p
        c = p * (1 + rng.normal(0.0008, 0.004))
        p = c
        rows.append([i * 3_600_000, o, max(o, c) * 1.002, min(o, c) * 0.998, c, volume])
    arr = np.array(rows, dtype=float)
    return Analyzer._compute_indicators(arr[:, 2], arr[:, 3], arr[:, 4], arr[:, 5],
                                        opens=arr[:, 1], times=arr[:, 0])


def _voters(ind):
    sig = MarketSignal(symbol="SOL/USDT", price=100.0, change_pct_24h=None,
                       volume_usd_24h=0, timestamp=datetime.now(UTC))
    bd: list = []
    Analyzer._score_confluence(ind, Regime.RANGE, sig, breakdown=bd)
    return {n: (v, w) for n, v, w in bd}


def test_a_volume_nobody_stated_is_flat_obv_and_casts_no_vote():
    ind = _indicators(0.0)            # what analyze()'s sanitizer makes of a null
    assert ind["obv_trend"] == "flat"
    assert "obv" not in _voters(ind), "a flat OBV voted -1.0 at weight 0.6"


def test_a_real_volume_still_votes():
    ind = _indicators(1000.0)
    assert ind["obv_trend"] in ("rising", "falling")
    assert _voters(ind)["obv"][0] in (1.0, -1.0)


def test_a_non_finite_obv_is_no_trend_at_all():
    ind = _indicators(float("nan"))
    assert "obv_trend" not in ind
    assert "obv" not in _voters(ind)
