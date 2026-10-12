"""A market question the router misses is answered from the engine's own read.

"is SOL a buy right now", "whats your read on HYPE" and "deep dive into ETH
structure" go to the model, not to a card. The model had no reading of the
market to answer from, a prompt asking it to judge "RSI stuck in no-man's land
(40-60)" and "conflicting signals across timeframes" (no block and no tool
supplies either, and the engine has no such gate), and a full-scan answer
shape demanding "Momentum — RSI" and "Setup quality, 1-10".

`market_read` wraps `Analyzer.scan_read`: the analyzer's indicators, regime
and confluence electorate with its rule thesis, no LLM, nothing written. The
prompt's RSI and timeframe list is gone, and the full-scan shape asks only for
what a reading supplies.
"""
from __future__ import annotations

import asyncio
import math

import pytest

from bot.core.analyzer import Analyzer
from bot.core.why_not import WhyNotLog
from bot.nlp import chat_market_read
from bot.nlp.chat_market_read import MarketReadSkill, confluence_words
from bot.nlp.tool_reading import parse_reading, prose_of


def _run(coro):
    return asyncio.run(coro)


def _candles(n: int = 150, step: float = 0.15) -> list:
    out, p = [], 100.0
    for i in range(n):
        o = p
        p = p + step + 0.4 * math.sin(i / 3.0)
        out.append([1_700_000_000_000 + i * 3_600_000, o, max(o, p) + 0.3,
                    min(o, p) - 0.3, p, 1000.0 + 10 * i])
    return out


# ── the claim scan_read was wired on, driven ────────────────────────────────

def test_scan_read_asks_no_model_and_leaves_nothing_behind():
    from datetime import datetime

    from bot.compat import UTC
    from bot.utils.models import MarketSignal

    a = Analyzer()
    a.why_not = WhyNotLog()

    async def _no_model(*_a, **_k):
        raise AssertionError("scan_read asked the LLM")

    a._llm_thesis = _no_model
    a._regime_history = [("ETH/USDT", "RANGE")]
    a._current_regimes = {}
    c = _candles()
    sig = MarketSignal(symbol="BTC/USDT", price=c[-1][4], change_pct_24h=1.0,
                       volume_usd_24h=1e6, timestamp=datetime.now(UTC))
    read = _run(a.scan_read(sig, c))
    assert read is not None and {"direction", "score", "regime", "confluence"} <= set(read)
    assert a._regime_history == [("ETH/USDT", "RANGE")] and a._current_regimes == {}
    assert a._no_trade_reasons == {} and a._last_rejection_diag is None
    assert a.why_not.reading()["analyses"] == 0
    # Deterministic: the same candles give the same read.
    assert _run(a.scan_read(sig, c)) == read


# ── the tool, through run_tool, on the real engine's candle cache ──────────

class _Exchange:
    def __init__(self, rows=None, raises=None, delay=0.0):
        self.rows, self.raises, self.delay, self.asked = rows, raises, delay, []

    async def fetch_ohlcv(self, symbol, timeframe, limit=100):
        self.asked.append((symbol, timeframe, limit))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.raises:
            raise self.raises
        return self.rows if self.rows is not None else _candles()


def _engine(spot: _Exchange, perp: _Exchange | None = None):
    from bot.core.engine import RuneClawEngine
    eng = RuneClawEngine()

    async def _spot():
        return spot

    async def _perp():
        return perp or _Exchange(raises=RuntimeError("no perp"))

    eng.scanner._get_exchange = _spot
    eng.scanner._get_futures_exchange = _perp
    return eng


def _tool(engine, symbol="SOL"):
    from types import SimpleNamespace

    from bot.nlp import chat_tools
    h = SimpleNamespace(registry=SimpleNamespace(get=lambda n: MarketReadSkill()),
                        conversations=None, engine=engine)
    out = _run(chat_tools.run_tool(h, "u1", "market_read", {"symbol": symbol},
                                   {"market_read"}))
    return parse_reading(out)["read_state"], prose_of(out)


def test_a_read_is_the_engines_read_and_says_what_it_is_not():
    spot = _Exchange()
    eng = _engine(spot)
    pending = dict(eng._pending_ideas)
    state, prose = _tool(eng)
    assert state == "read"
    assert spot.asked == [("SOL/USDT", "1h", 100)], "the analyzer's own leg"
    assert "SOL/USDT:1h:100" in eng._ohlcv_cache, "the sweep's cache key"
    for part in ("Direction:", "Regime:", "Confluence:", "rule engine (no LLM)",
                 "not a trade idea", "Not read here: RSI"):
        assert part in prose, (part, prose)
    assert eng._pending_ideas == pending, "nothing was queued"


def test_a_symbol_the_engine_just_read_costs_no_fetch():
    spot = _Exchange()
    eng = _engine(spot)
    _tool(eng)
    _tool(eng)
    assert len(spot.asked) == 1


def test_a_perp_only_listing_is_read_on_the_perpetual():
    perp = _Exchange()
    eng = _engine(_Exchange(rows=[]), perp)
    state, prose = _tool(eng, "HYPE")
    assert state == "read"
    assert perp.asked[0][0] == "HYPE/USDT:USDT"
    assert "Engine read on HYPE/USDT:USDT" in prose


def test_a_failed_read_is_unread_and_names_only_the_class():
    secret = RuntimeError("GET https://key=abc123@venue.example failed")
    eng = _engine(_Exchange(raises=secret), _Exchange(raises=secret))
    state, prose = _tool(eng)
    assert state == "unread"
    assert "RuntimeError" in prose and "abc123" not in prose
    assert "Direction:" not in prose


def test_a_read_that_does_not_arrive_in_time_is_unread(monkeypatch):
    monkeypatch.setattr(chat_market_read, "FETCH_BUDGET_S", 0.05)
    eng = _engine(_Exchange(delay=1.0))
    state, prose = _tool(eng)
    assert state == "unread" and "did not arrive within" in prose


def test_too_few_bars_is_unread_not_a_neutral_read():
    eng = _engine(_Exchange(rows=_candles(10)), _Exchange(rows=_candles(10)))
    state, prose = _tool(eng)
    assert state == "unread" and "could not score them" in prose
    assert "Direction: none" not in prose


def test_no_analyzer_is_unread():
    from types import SimpleNamespace
    state, prose = _tool(SimpleNamespace())
    assert state == "unread" and "no analyzer" in prose


def test_the_tool_needs_a_symbol_and_is_offered_with_analyze():
    from bot.nlp.chat_tools import CHAT_TOOLS
    from bot.skills.skill_permissions import SKILL_PERMISSION, WEB_CHAT_SKILLS
    tool = {t.name: t for t in CHAT_TOOLS}["market_read"]
    assert tool.parameters["required"] == ["symbol"]
    assert SKILL_PERMISSION["market_read"] == "analyze"
    assert "market_read" in WEB_CHAT_SKILLS
    from bot.skills.skill_registry import build_default_registry
    assert isinstance(build_default_registry().get("market_read"), MarketReadSkill)


# ── confluence is bullishness, and a strong short must not read as weak ────

@pytest.mark.parametrize("c,direction,want", [
    (0.2, "SHORT", "80% for a SHORT (bullishness 20%; 50% is neutral)"),
    (0.8, "LONG", "80% for a LONG (bullishness 80%; 50% is neutral)"),
    (0.5, None, "bullishness 50% (50% is neutral)"),
])
def test_confluence_is_printed_for_the_direction_it_is_read_against(c, direction, want):
    assert confluence_words(c, direction) == want


# ── the prompt asks for nothing no reading supplies ────────────────────────

def test_the_prompt_no_longer_asks_for_an_rsi_or_a_timeframe_read():
    from bot.skills.telegram_handler import TelegramHandler
    prompt = TelegramHandler._CHAT_SYSTEM_PROMPT
    assert "40-60" not in prompt and "across timeframes" not in prompt
    assert "Never cite an RSI value, a timeframe's trend or any indicator" in prompt
    assert "market_read" in prompt


def test_the_full_scan_shape_asks_only_for_what_a_reading_supplies():
    from bot.skills.chat_runtime import reply_contract
    contract = reply_contract("full_scan")
    assert "Setup quality" not in contract and "1-10" not in contract
    assert "Momentum" not in contract
    assert "call it for the symbol first" in contract
    assert "Print no RSI, no timeframe-by-timeframe read" in contract
    assert "say so first and give no verdict" in contract
