"""Frozen candles in, exact TradeIdea out: a golden replay of the signal engine.

Nothing pinned what the analyzer PRODUCES from a known input. Unit tests drive
one branch each; a change that moved every entry by a tick, re-weighted the
confluence or swapped the order type passed all of them. This replays two
windows of committed, hash-checked SOL 1h bars (`benchmark/majors_1h`) through
a fresh `Analyzer` with the LLM off (the rule engine decides, as it does with
no key) and the clock pinned to the newest bar's close, and compares every
decision field to the values recorded on 2026-11-21.

WHEN THIS FAILS, it is a signal-engine behaviour change. If the change is
intended, re-derive the values (the docstring of `_replay` says how) and
record them in the same commit with the reason; never loosen the comparison.
"""
from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from bot.backtest.data_loader import DataLoader
from bot.backtest.snapshot import load_manifest, load_symbol
from bot.core.analyzer import Analyzer
from bot.core.market_scanner import MarketScanner
from bot.utils.models import MarketSignal

DATASET = "benchmark/majors_1h"
SYMBOL = "SOL/USDT:USDT"
WINDOW = 250

#: Recorded 2026-11-21 from the code at this commit (LLM off, as_of pinned).
GOLDEN = {
    511: {
        "asset": "SOL/USDT", "direction": "SHORT", "entry_price": 132.524144,
        "stop_loss": 135.046603, "take_profit": 124.175244, "confidence": 0.73,
        "blended_confidence_raw": 0.73, "llm_confidence": 0.59, "confluence_score": 0.3101,
        "htf_trend": "", "source": "analyzer", "order_type": "limit",
        "strategy_type": "swing", "signal_type": "momentum_confluence", "timeframe": "1h",
        "model_provider": "RULE_ENGINE", "analysis_version": "2026.07-audit25",
        "data_bars": 250, "data_thin": False, "market_at_signal": 130.657,
        "newest_bar_close_ms": 1763416800000.0, "bar_age_at_analysis_s": 0.0,
        "analysis_depth": None,
    },
    733: {
        "asset": "SOL/USDT", "direction": "LONG", "entry_price": 140.95676,
        "stop_loss": 140.225565, "take_profit": 144.503626, "confidence": 0.62,
        "blended_confidence_raw": 0.62, "llm_confidence": 0.46, "confluence_score": 0.6053,
        "htf_trend": "", "source": "analyzer", "order_type": "limit",
        "strategy_type": "swing", "signal_type": "momentum_confluence", "timeframe": "1h",
        "model_provider": "RULE_ENGINE", "analysis_version": "2026.07-audit25",
        "data_bars": 250, "data_thin": False, "market_at_signal": 142.457,
        "newest_bar_close_ms": 1764216000000.0, "bar_age_at_analysis_s": 0.0,
        "analysis_depth": None,
    },
}
#: How many voters the rule engine recorded on each idea, and how its reasoning opens.
GOLDEN_SIGNALS_USED = {511: 114, 733: 108}
GOLDEN_REASONING_HEAD = {
    511: "[RULE_ENGINE|TREND_DOWN|TREND_CONTINUATION|swing|C=0.31]",
    733: "[RULE_ENGINE|TREND_UP|TREND_CONTINUATION|swing|C=0.61]",
}


@pytest.fixture(scope="module")
def bars():
    out = load_symbol(DATASET, SYMBOL)
    # The input is the input: a dataset that drifted would make every
    # assertion below a comparison against different candles.
    assert DataLoader.content_hash(out) == load_manifest(DATASET)["symbols"][SYMBOL]["sha256"]
    return out


async def _no_fetch(*_a, **_k):
    return None


def _replay(bars, end: int):
    """The idea for the WINDOW bars ending before ``end``, clock at their close.

    To re-derive GOLDEN after an intended change: call this and dump
    ``idea.model_dump(mode="json")`` for each key of GOLDEN.
    """
    window = bars[end - WINDOW:end]
    rows = [[int(b.timestamp.timestamp() * 1000), b.open, b.high, b.low, b.close, b.volume]
            for b in window]
    analyzer = Analyzer()
    analyzer._llm = None                                  # the rule engine decides
    analyzer._sentiment.refresh_fear_greed = _no_fetch    # its one network read
    signal = MarketSignal(symbol="SOL/USDT", price=rows[-1][4], change_pct_24h=1.0,
                          volume_usd_24h=5e7)
    as_of = window[-1].timestamp + timedelta(hours=1)
    return asyncio.run(analyzer.analyze(signal, rows, as_of=as_of, timeframe="1h")), analyzer


@pytest.mark.parametrize("end", sorted(GOLDEN))
def test_the_replay_produces_exactly_the_recorded_idea(bars, end):
    idea, analyzer = _replay(bars, end)
    assert idea is not None, analyzer.get_no_trade_reason("SOL/USDT")
    got = idea.model_dump(mode="json")
    diff = {k: (got[k], v) for k, v in GOLDEN[end].items() if got[k] != v}
    assert not diff, f"signal engine output moved (got, recorded): {diff}"
    assert len(got["signals_used"]) == GOLDEN_SIGNALS_USED[end]
    assert got["reasoning"].startswith(GOLDEN_REASONING_HEAD[end])


def test_the_replay_is_the_same_twice(bars):
    a, _ = _replay(bars, 511)
    b, _ = _replay(bars, 511)
    keys = list(GOLDEN[511])
    assert {k: getattr(a, k) for k in keys} == {k: getattr(b, k) for k in keys}


def test_a_window_with_no_setup_produces_no_idea_and_says_why(bars):
    # The other arm: the replay is not an idea-printer. The window ending at
    # bar 400 was recorded as producing none.
    idea, analyzer = _replay(bars, 400)
    assert idea is None
    assert analyzer.get_no_trade_reason("SOL/USDT")["stage"]


# ── the scanner's half: one ticker, the exact signal ─────────────────────────

def test_the_scanner_turns_a_ticker_into_exactly_this_signal():
    tick = {"last": 151.25, "quoteVolume": 48_000_000.0, "percentage": 3.456,
            "timestamp": 1_780_000_000_000, "active": True, "info": {"status": "online"}}
    sig = MarketScanner()._process_ticker("SOL/USDT:USDT", tick)
    assert sig is not None
    got = sig.model_dump(mode="json")
    got.pop("timestamp")                                   # the scan's own clock
    assert got == {
        "symbol": "SOL/USDT:USDT", "price": 151.25, "change_pct_24h": 3.46,
        "volume_usd_24h": 48_000_000.0, "volume_spike": False,
        "volume_spike_ratio": None, "momentum_score": 0.346,
        "asset_category": "Crypto", "ticker_ts": "2026-05-28T20:26:40Z",
    }
