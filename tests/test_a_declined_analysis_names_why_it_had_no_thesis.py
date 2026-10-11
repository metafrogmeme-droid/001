"""A declined analysis names why it had no thesis, and its confluence faces the bias.

When the thesis came back None the analyzer always wrote "Ambiguous signal —
confluence in neutral zone, no strong directional bias from RSI or MACD". That
is the rule engine's sentence, and it was also written when the MODEL's reply
could not be read, so the /analyze card printed "Ambiguous signal" beside
"Confluence: 72%". The same card printed the raw confluence, which is a
bullishness score, beside "Bias: SHORT", so a strongly confirmed short read
"30%".

`_llm_thesis` now says which of the two it was (`why`), the analyzer records
`thesis_unread` or `no_thesis` with the route that sent the call to the rules,
and the card prints the confluence for the direction it is read against.
"""
from __future__ import annotations

import asyncio
import math
import time
from datetime import datetime
from types import SimpleNamespace

import pytest

import bot.core.analyzer as am
from bot.compat import UTC
from bot.core.analyzer import Analyzer, no_thesis_reason
from bot.utils.models import MarketSignal


def _run(coro):
    return asyncio.run(coro)


def _signal() -> MarketSignal:
    return MarketSignal(symbol="BTC/USDT", price=100.0, change_pct_24h=1.0,
                        volume_usd_24h=1e8, timestamp=datetime.now(UTC))


# ── _llm_thesis says which None it is ──────────────────────────────────────

class _Reply:
    def __init__(self, text):
        self.choices = [SimpleNamespace(message=SimpleNamespace(content=text))]
        self.usage = None


class _Client:
    """An OpenAI-shaped client that answers one fixed reply."""

    def __init__(self, text):
        async def create(**_kw):
            return _Reply(text)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))


def _to_the_model(monkeypatch, reply: str) -> Analyzer:
    """A real analyzer routed straight to its primary model, which answers
    ``reply``: every valve that would send the call to the rules is open."""
    a = Analyzer()
    a._llm = _Client(reply)
    a._scan_client = a._thesis_client = None
    a._admin_scan_client = a._admin_thesis_client = None
    a._cost = None
    monkeypatch.setattr(a, "_on_this_loop", lambda client, cfg: client)
    monkeypatch.setattr(a, "_maybe_user_client", lambda uid: (None, None))
    monkeypatch.setattr(a, "_maybe_tier_client", lambda tier, full: (None, None, None))
    monkeypatch.setattr(am.AdaptiveFrequency, "should_use_llm",
                        staticmethod(lambda sig, ind: True))
    monkeypatch.setattr(am.TieredPipeline, "effective_tier",
                        staticmethod(lambda ind, sig: 2))
    return a


@pytest.mark.parametrize("reply,field", [
    ("I think it might go up from here.", "direction"),
    ('{"direction": "LONG", "confidence": "high", "reasoning": "x"}', "confidence"),
])
def test_an_unreadable_model_reply_is_unread_and_names_the_field(monkeypatch, reply, field):
    a = _to_the_model(monkeypatch, reply)
    why: dict = {}
    assert _run(a._llm_thesis(_signal(), {}, why=why)) is None
    assert why == {"kind": "unread", "field": field}


def test_a_readable_model_reply_says_nothing_in_why(monkeypatch):
    a = _to_the_model(monkeypatch, '{"direction": "LONG", "confidence": 0.7, "reasoning": "x"}')
    why: dict = {}
    assert _run(a._llm_thesis(_signal(), {}, why=why))["direction"] == "LONG"
    assert why == {}


def test_a_neutral_rule_thesis_names_the_route_that_sent_it_there(monkeypatch):
    a = Analyzer()
    a._llm = None          # no LLM configured: the rule engine answers
    monkeypatch.setattr(a, "_rule_based_thesis", lambda sig, ind: None)
    why: dict = {}
    assert _run(a._llm_thesis(_signal(), {}, why=why)) is None
    assert why == {"kind": "neutral", "source": "RULE_ENGINE"}


def _route_adaptive(monkeypatch, a):
    monkeypatch.setattr(am.AdaptiveFrequency, "should_use_llm",
                        staticmethod(lambda sig, ind: False))


def _route_tier1(monkeypatch, a):
    monkeypatch.setattr(am.TieredPipeline, "effective_tier",
                        staticmethod(lambda ind, sig: 1))


def _route_budget(monkeypatch, a):
    a._llm_day = datetime.now(UTC).strftime("%Y-%m-%d")
    a._llm_calls_today = 10 ** 9


def _route_dollars(monkeypatch, a):
    a._cost = SimpleNamespace(snapshot=lambda: SimpleNamespace(llm_cost_usd=9.0))
    monkeypatch.setattr(am, "thesis_dollar_exhausted", lambda snap, cfg: True)


def _route_fallback(monkeypatch, a):
    async def _raise(**_kw):
        raise RuntimeError("provider down")

    async def _none(*_a, **_k):
        return None

    a._llm = SimpleNamespace(chat=SimpleNamespace(
        completions=SimpleNamespace(create=_raise)))
    monkeypatch.setattr(a, "_try_llm_fallback", _none)
    monkeypatch.setattr(a, "_note_llm_degraded", lambda *a_, **k_: None)


def _route_background_env(monkeypatch, a):
    monkeypatch.setenv("LLM_BACKGROUND_SCANS", "off")


def _route_offline(monkeypatch, a):
    a._offline_thesis_fn = lambda sig, ind, as_of: None


@pytest.mark.parametrize("route,source", [
    (_route_adaptive, "RULE_ENGINE_ADAPTIVE"),
    (_route_tier1, "RULE_ENGINE_TIER1"),
    (_route_budget, "RULE_ENGINE_BUDGET"),
    (_route_dollars, "RULE_ENGINE_BUDGET"),
    (_route_fallback, "RULE_ENGINE_FALLBACK"),
    (_route_background_env, "RULE_ENGINE_BG_THROTTLE"),
    (_route_offline, "RULE_ENGINE_NO_RECORD"),
])
def test_every_route_to_the_rules_says_it_was_the_rules(monkeypatch, route, source):
    a = _to_the_model(monkeypatch, "unused")
    monkeypatch.setattr(a, "_rule_based_thesis", lambda sig, ind: None)
    route(monkeypatch, a)
    why: dict = {}
    assert _run(a._llm_thesis(_signal(), {}, why=why)) is None
    assert why == {"kind": "neutral", "source": source}


def test_a_rule_thesis_that_answered_is_stamped_as_before(monkeypatch):
    a = Analyzer()
    a._llm = None
    monkeypatch.setattr(a, "_rule_based_thesis",
                        lambda sig, ind: {"direction": "SHORT", "confidence": 0.6})
    why: dict = {}
    out = _run(a._llm_thesis(_signal(), {}, why=why))
    assert out["source"] == "RULE_ENGINE" and why == {}


# ── the analyzer records the reason it was given ───────────────────────────

def _candles(n: int = 150) -> list:
    out, p = [], 100.0
    for i in range(n):
        o = p
        p = p + 0.15 + 0.4 * math.sin(i / 3.0)
        out.append([1_700_000_000_000 + i * 3_600_000, o, max(o, p) + 0.3,
                    min(o, p) - 0.3, p, 1000.0 + 10 * i])
    return out


async def _no_fetch(*_a, **_k):
    return None


def _declined(why_filled: dict) -> dict:
    a = Analyzer()
    a._sentiment.refresh_fear_greed = _no_fetch

    async def _thesis(*_a, why=None, **_k):
        if why is not None:
            why.update(why_filled)
        return None

    a._llm_thesis = _thesis
    c = _candles()
    sig = MarketSignal(symbol="BTC/USDT", price=c[-1][4], change_pct_24h=1.0,
                       volume_usd_24h=1e8, timestamp=datetime.now(UTC))
    assert _run(a.analyze(sig, c)) is None
    return a.get_no_trade_reason("BTC/USDT")


def test_an_unread_reply_is_recorded_as_unread_not_as_an_ambiguous_market():
    rec = _declined({"kind": "unread", "field": "confidence"})
    assert rec["stage"] == "thesis_unread"
    assert rec["reason"] == ("The model's reply could not be read (no readable "
                             "confidence), so no trade is taken on it")
    assert "Ambiguous" not in rec["reason"]


def test_a_neutral_rule_thesis_is_recorded_as_ambiguous_with_its_route():
    rec = _declined({"kind": "neutral", "source": "RULE_ENGINE_FALLBACK"})
    assert rec["stage"] == "no_thesis"
    assert rec["reason"].startswith("Ambiguous signal — the rule engine "
                                    "(every LLM provider failed)")


def test_a_none_nothing_explained_is_neither():
    stage, reason = no_thesis_reason({})
    assert stage == "no_thesis"
    assert reason == "No thesis was produced, and nothing recorded why"


# ── the /analyze card: the real reason, and confluence facing the bias ─────

class _Exchange:
    async def fetch_ticker(self, sym):
        return {"last": 100.0, "percentage": 1.0, "quoteVolume": 5e6}


def _card(diag: dict) -> str:
    from bot.skills.skill_registry import AnalyzeAssetSkill

    async def _get_exchange():
        return _Exchange()

    eng = SimpleNamespace(
        scanner=SimpleNamespace(_get_exchange=_get_exchange,
                                _get_futures_exchange=_get_exchange),
        analyzer=SimpleNamespace(_last_rejection_diag=None),
        _last_rejections={}, _pending_ideas={}, _pending_atr={})

    async def _analyze_signal(sig, **_kw):
        eng.analyzer._last_rejection_diag = {"symbol": sig.symbol, "at": time.time(), **diag}
        return None

    eng._analyze_signal = _analyze_signal
    return _run(AnalyzeAssetSkill().execute(eng, symbol="BTC/USDT"))


def test_a_strong_short_reads_strong():
    out = _card({"direction": "SHORT", "confluence": 0.2, "reason": "Order-flow veto"})
    assert "Bias: <code>SHORT</code>" in out
    assert "Confluence: <code>80% for a SHORT (bullishness 20%; 50% is neutral)</code>" in out


def test_a_long_reads_its_own_bullishness():
    out = _card({"direction": "LONG", "confluence": 0.72, "reason": "x"})
    assert "Confluence: <code>72% for a LONG (bullishness 72%; 50% is neutral)</code>" in out


def test_with_no_bias_the_figure_is_named_as_bullishness():
    out = _card({"confluence": 0.72, "reason": "x"})
    assert "Confluence: <code>bullishness 72% (50% is neutral)</code>" in out


def test_the_most_bearish_reading_is_printed_not_hidden():
    out = _card({"direction": "SHORT", "confluence": 0.0, "reason": "x"})
    assert "100% for a SHORT (bullishness 0%" in out


def test_the_card_prints_the_unread_reason_beside_the_confluence():
    stage, reason = no_thesis_reason({"kind": "unread", "field": "direction"})
    out = _card({"stage": stage, "confluence": 0.72, "reason": reason})
    assert "could not be read (no LONG or SHORT direction)" in out
    assert "Ambiguous signal" not in out
