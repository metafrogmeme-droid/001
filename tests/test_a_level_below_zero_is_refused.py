"""A stop or target the volatility puts at or below zero is refused, not floored.

With ATR around 40% of price at the default stop multiple, a LONG's stop (or
a SHORT's target) lands at or below zero. The analyzer used to move it to 1%
of the entry -- a stop 99% below entry, a level no analysis chose -- and the
idea went on to the card and the risk gate as though it had. There is no
honest level to offer, so there is no idea, and the refusal says which leg.

Both arms are driven on the same series shape: narrow bars give an idea, the
same bars ±90% wide give the refusal.
"""
from __future__ import annotations

import asyncio
import math
from datetime import datetime, timezone

import pytest

from bot.core import why_not
from bot.core.analyzer import Analyzer
from bot.utils.models import MarketSignal
from tests.candle_clock import anchored

NOW_MS = int(datetime(2026, 6, 1, 12, tzinfo=timezone.utc).timestamp() * 1000)


def _rows(width: float, drift: float, n: int = 150) -> list:
    out, p = [], 100.0
    for i in range(n):
        o = p
        p = p + drift + 0.4 * math.sin(i / 3.0)
        hi = max(o, p) * (1 + width)
        lo = max(0.01, min(o, p) * (1 - width))
        out.append([i * 3_600_000, o, hi, lo, p, 1000.0 + 10 * i])
    return anchored(out, "1h", now_ms=NOW_MS)


async def _no_fetch(*_a, **_k):
    return None


def _analyze(direction: str, rows: list):
    a = Analyzer()
    a._sentiment.refresh_fear_greed = _no_fetch

    async def _thesis(*_a, **_k):
        return {"direction": direction, "confidence": 0.95, "reasoning": "planted",
                "source": "LLM_TEST"}
    a._llm_thesis = _thesis
    a._regime_hard_gate_reason = lambda *_x: None
    a._direction_guard_action = lambda *_x: None
    sig = MarketSignal(symbol="BTC/USDT", price=rows[-1][4], change_pct_24h=1.0,
                       volume_usd_24h=1e8)
    as_of = datetime.fromtimestamp(NOW_MS / 1000 + 60, tz=timezone.utc)
    idea = asyncio.run(a.analyze(sig, rows, as_of=as_of, timeframe="1h"))
    return idea, a.get_no_trade_reason("BTC/USDT")


@pytest.mark.parametrize("direction,drift,leg", [("LONG", 0.15, "stop"), ("SHORT", 0.02, "target")])
def test_a_level_the_volatility_puts_below_zero_is_refused(direction, drift, leg):
    idea, why = _analyze(direction, _rows(0.9, drift))
    assert idea is None
    assert why["stage"] == "level_below_zero"
    assert f"computed {leg}" in why["reason"] and "at or below zero" in why["reason"]
    level = why["sl"] if leg == "stop" else why["tp"]
    assert level <= 0, "the record carries the level the analysis computed, unfloored"


@pytest.mark.parametrize("direction,drift", [("LONG", 0.15), ("SHORT", 0.02)])
def test_the_same_series_with_narrow_bars_still_yields_an_idea(direction, drift):
    idea, why = _analyze(direction, _rows(0.003, drift))
    assert idea is not None, why
    assert idea.direction.value == direction
    assert idea.stop_loss > 0 and idea.take_profit > 0


def test_no_floored_level_reaches_an_idea():
    # The 1%-of-entry floor is gone: nothing produced lands exactly there.
    for direction, drift in (("LONG", 0.15), ("SHORT", 0.02)):
        idea, _ = _analyze(direction, _rows(0.9, drift))
        assert idea is None


def test_the_refusal_has_its_own_words():
    assert why_not.stage_words("level_below_zero") == "a level crossed zero"
    assert why_not.stage_words("level_below_zero") != why_not.stage_words("levels")
