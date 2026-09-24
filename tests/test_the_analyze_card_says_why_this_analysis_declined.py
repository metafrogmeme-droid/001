"""The analyze card explains a declined analysis with what happened in THAT call.

When `engine._analyze_signal` returns None the card used to read
`engine.analyzer._last_rejection_diag` bare. That is ONE slot for the last
rejection of ANY symbol, and a background scan writes it constantly, so
"analyze BTC" was explained with another symbol's regime, bias and score. When
the RISK GATE refused the idea (the analyzer had produced one), the card showed
the analyzer's older note anyway, and with none it printed "regime filter or
low confluence", a cause nobody measured.

Enforcing the minimum reward:risk on limits makes a risk refusal the ordinary
way this card declines, so the card has to be able to say so.
`declined_analysis_reason` takes a record only when it names this symbol AND
was made at or after the call started, asks the gate first, and otherwise says
nothing was recorded.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta
from types import SimpleNamespace

from bot.compat import UTC
from bot.skills.skill_registry import AnalyzeAssetSkill, declined_analysis_reason

T0 = 1_800_000_000.0


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, UTC).isoformat()


def _rej(at: float, failed=("RISK_REWARD: 1.30 < 1.5 minimum (swing)",)):
    return {"symbol": "BTC/USDT:USDT", "reason": "REJECTED: " + "; ".join(failed),
            "checks_failed": list(failed), "timestamp": _iso(at)}


def _diag(symbol: str, at, **kw):
    d = {"symbol": symbol, "regime": "RANGE", "direction": "SHORT",
         "reason": "Score 41% < 55% threshold", **kw}
    if at is not None:
        d["at"] = at
    return d


def test_a_refusal_made_during_this_call_is_the_answer():
    got = declined_analysis_reason("BTC/USDT", T0, None, {"BTC": _rej(T0 + 1)})
    assert got["source"] == "risk"
    assert got["failed"] == ["RISK_REWARD: 1.30 < 1.5 minimum (swing)"]


def test_an_older_refusal_is_not_this_calls():
    got = declined_analysis_reason("BTC/USDT", T0, None, {"BTC": _rej(T0 - 1)})
    assert got == {"source": "unrecorded"}


def test_the_gate_is_asked_before_the_analyzer():
    got = declined_analysis_reason("BTC/USDT", T0, _diag("BTC/USDT", T0 + 2),
                                   {"BTC": _rej(T0 + 1)})
    assert got["source"] == "risk"


def test_another_symbols_diagnostic_is_never_this_ones():
    got = declined_analysis_reason("BTC/USDT", T0, _diag("DOGE/USDT", T0 + 1), {})
    assert got == {"source": "unrecorded"}


def test_this_symbols_fresh_diagnostic_is_the_answer():
    d = _diag("BTC/USDT:USDT", T0 + 1)
    got = declined_analysis_reason("BTC/USDT", T0, d, {})
    assert got == {"source": "analyzer", "diag": d}


def test_this_symbols_older_diagnostic_is_not_this_calls():
    got = declined_analysis_reason("BTC/USDT", T0, _diag("BTC/USDT", T0 - 1), {})
    assert got == {"source": "unrecorded"}


def test_an_undated_diagnostic_is_not_trusted():
    got = declined_analysis_reason("BTC/USDT", T0, _diag("BTC/USDT", None), {})
    assert got == {"source": "unrecorded"}


def test_a_boolean_is_not_a_time():
    got = declined_analysis_reason("BTC/USDT", 0.5, _diag("BTC/USDT", True), {})
    assert got == {"source": "unrecorded"}


def test_the_record_stamp_falls_back_to_its_iso_time():
    d = {"symbol": "BTC/USDT", "reason": "x", "ts": _iso(T0 + 1)}
    assert declined_analysis_reason("BTC/USDT", T0, d, {})["source"] == "analyzer"


def test_the_analyzer_stamps_every_rejection_it_records():
    # The reading trusts only a dated diagnostic, so every writer must date it.
    import inspect
    import re

    from bot.core.analyzer import Analyzer
    from tests.source_scan import code_only
    src = code_only(inspect.getsource(Analyzer))
    writes = re.findall(r"self\._last_rejection_diag\s*=\s*([^\n]*)", src)
    assert writes, "no writer found"
    for rhs in writes:
        assert rhs.strip() == "None" or '"at": time.time()' in rhs, rhs


# ── The card itself, driven ────────────────────────────────────────────────


class _Exchange:
    async def fetch_ticker(self, sym):
        return {"last": 100.0, "percentage": 1.0, "quoteVolume": 5e6}


def _engine(on_analyze):
    async def _get_exchange():
        return _Exchange()

    eng = SimpleNamespace(
        scanner=SimpleNamespace(_get_exchange=_get_exchange,
                                _get_futures_exchange=_get_exchange),
        analyzer=SimpleNamespace(_last_rejection_diag=None),
        _last_rejections={},
        _pending_ideas={}, _pending_atr={},
    )

    async def _analyze_signal(sig, **kw):
        on_analyze(eng, sig)
        return None

    eng._analyze_signal = _analyze_signal
    return eng


def _card(on_analyze) -> str:
    return asyncio.run(AnalyzeAssetSkill().execute(_engine(on_analyze), symbol="BTC/USDT"))


def test_the_card_names_the_gates_refusal():
    def refuse(eng, sig):
        # A stale note about ANOTHER symbol sits in the analyzer's one slot,
        # as a background scan leaves it; the gate refuses THIS idea now.
        eng.analyzer._last_rejection_diag = _diag("DOGE/USDT", time.time())
        eng._last_rejections["BTC"] = _rej(time.time())

    out = _card(refuse)
    assert "Refused by the risk gate" in out
    assert "RISK_REWARD: 1.30 &lt; 1.5 minimum (swing)" in out
    assert "RANGE" not in out and "SHORT" not in out, out


def test_the_card_does_not_borrow_another_symbols_diagnostic():
    def other(eng, sig):
        eng.analyzer._last_rejection_diag = _diag("DOGE/USDT", time.time())

    out = _card(other)
    assert "RANGE" not in out and "41%" not in out, out
    assert "nothing recorded why during this analysis" in out
    assert "regime filter or low confluence" not in out


def test_the_card_shows_this_symbols_fresh_diagnostic():
    def own(eng, sig):
        eng.analyzer._last_rejection_diag = _diag(sig.symbol, time.time())

    out = _card(own)
    assert "Score 41% &lt; 55% threshold" in out
    assert "Regime: <code>RANGE</code>" in out


def test_the_cards_clock_starts_before_the_analysis():
    # A refusal written a moment BEFORE the call is an older one.
    def old(eng, sig):
        eng._last_rejections["BTC"] = _rej(time.time() - timedelta(seconds=5).total_seconds())

    out = _card(old)
    assert "Refused by the risk gate" not in out
    assert "nothing recorded why during this analysis" in out
