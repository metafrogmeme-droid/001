""""Why didn't we trade?" counts every analysis by the stage that ended it.

`/whynot` and its chat tool read only `engine._last_rejections`, which the
RISK GATE fills. Most analyses never reach the gate: the analyzer skips them
below the bar, at the regime gate or at the direction guard, and it recorded
each skip per symbol where nothing read it. So a day of skips answered
"No trades rejected", under a tool description promising "the gate that
rejected or skipped it".

`bot/core/why_not.py` keeps one row per analysis. The analyzer writes it where
it decides, the engine amends the row of an idea it drops itself, and a
confirm that placed nothing is kept with whose confirm it was. Each stage is
driven here through the code that decides it.
"""
from __future__ import annotations

import asyncio
import math
from datetime import datetime
from types import SimpleNamespace

import pytest

from bot.compat import UTC
from bot.core import why_not
from bot.core.analyzer import Analyzer
from bot.core.why_not import WhyNotLog
from bot.utils.models import MarketSignal, RiskCheck, RiskVerdict


def _run(coro):
    return asyncio.run(coro)


def _candles(n: int = 150) -> list:
    out, p = [], 100.0
    for i in range(n):
        o = p
        p = p + 0.15 + 0.4 * math.sin(i / 3.0)
        out.append([1_700_000_000_000 + i * 3_600_000, o, max(o, p) + 0.3,
                    min(o, p) - 0.3, p, 1000.0 + 10 * i])
    return out


def _signal(symbol: str = "BTC/USDT") -> MarketSignal:
    c = _candles()
    return MarketSignal(symbol=symbol, price=c[-1][4], change_pct_24h=1.0,
                        volume_usd_24h=1e8, timestamp=datetime.now(UTC))


def _thesis(direction="LONG", confidence=0.95):
    async def thesis(*_a, **_k):
        if direction is None:
            return None
        return {"direction": direction, "confidence": confidence,
                "reasoning": "planted", "source": "LLM_TEST"}
    return thesis


async def _no_fetch(*_a, **_k):
    return None


def _offline(a: Analyzer) -> Analyzer:
    # The external Fear & Greed read is the analyzer's one network call.
    a._sentiment.refresh_fear_greed = _no_fetch
    return a


def _analyzer(*, thesis=None, gate=None, guard=None, log=True) -> Analyzer:
    a = _offline(Analyzer())
    a.why_not = WhyNotLog() if log else None
    a._llm_thesis = thesis or _thesis()
    a._regime_hard_gate_reason = lambda *_x: gate
    a._direction_guard_action = lambda *_x: guard
    return a


# ── the analyzer writes one row per analysis, at the stage that ended it ────

@pytest.mark.parametrize("stage,kw", [
    ("no_thesis", {"thesis": _thesis(direction=None)}),
    ("regime_hard_gate", {"gate": "CHOP regime has no directional edge"}),
    ("llm_direction_guard", {"guard": "veto"}),
    ("confidence", {"thesis": _thesis(confidence=0.0)}),
])
def test_each_analyzer_skip_is_one_row_at_its_own_stage(stage, kw):
    a = _analyzer(**kw)
    assert _run(a.analyze(_signal(), _candles())) is None
    got = a.why_not.reading(symbol="BTC/USDT")
    assert got["analyses"] == 1
    assert got["by_stage"] == [(stage, 1)], got["by_stage"]
    assert got["latest"][0]["reason"], "a skip carries its reason"


def test_an_analysis_that_produced_an_idea_is_counted_as_one():
    a = _analyzer()
    idea = _run(a.analyze(_signal(), _candles()))
    assert idea is not None
    assert a.why_not.reading()["by_stage"] == [("idea", 1)]
    # ...under the idea's id, which is what lets the engine amend it.
    assert a.why_not.amend(idea.id, "risk", "DAILY_LOSS") is True
    assert a.why_not.reading()["by_stage"] == [("risk", 1)]


def test_an_analyzer_with_no_record_attached_keeps_none_and_still_decides():
    # A backtest's or a script's analyzer: the engine attaches the record.
    a = _analyzer(gate="CHOP regime has no directional edge", log=False)
    assert _run(a.analyze(_signal(), _candles())) is None
    assert a.why_not is None
    assert a.get_no_trade_reason("BTC/USDT")["stage"] == "regime_hard_gate"


def test_the_bots_engine_attaches_a_record_to_its_analyzer():
    from bot.core.engine import RuneClawEngine
    assert isinstance(RuneClawEngine().analyzer.why_not, WhyNotLog)


# ── the engine amends an idea it drops, at the stage that dropped it ────────

class _Exchange:
    async def fetch_ohlcv(self, *_a, **_k):
        return _candles()


def _engine_with_gate(verdict: RiskVerdict):
    from bot.core.engine import RuneClawEngine
    eng = RuneClawEngine()

    async def _ex():
        return _Exchange()

    eng.scanner._get_exchange = _ex
    _offline(eng.analyzer)
    eng.analyzer._llm_thesis = _thesis()
    eng.analyzer._regime_hard_gate_reason = lambda *_x: None
    eng.analyzer._direction_guard_action = lambda *_x: None

    def _evaluate(idea, **_kw):
        failed = ([] if verdict == RiskVerdict.APPROVED
                  else ["DAILY_LOSS: 3.1% >= 3%", "MAX_POSITIONS: 5 >= 5"])
        return RiskCheck(trade_id=idea.id, verdict=verdict, checks_failed=failed,
                         reason="REJECTED" if failed else "")

    eng.risk.evaluate = _evaluate
    return eng


def test_a_risk_refusal_amends_the_analysis_to_the_risk_gate_by_check_name():
    eng = _engine_with_gate(RiskVerdict.REJECTED)
    assert _run(eng._analyze_signal(_signal(), lightweight=True)) is None
    got = eng.analyzer.why_not.reading(symbol="BTC")
    assert got["by_stage"] == [("risk", 1)], "one analysis, counted once"
    # The names, never the figures: those are the operator's account.
    assert got["latest"][0]["reason"] == "DAILY_LOSS, MAX_POSITIONS"


def test_an_idea_the_gate_passes_stays_an_idea():
    eng = _engine_with_gate(RiskVerdict.APPROVED)
    idea = _run(eng._analyze_signal(_signal(), lightweight=True))
    assert idea is not None
    assert eng.analyzer.why_not.reading()["by_stage"] == [("idea", 1)]


def _engine_with_a_planted_idea():
    """The real engine, with the analyzer's answer planted: an idea it wrote
    a row for, as the real analyzer does. What happens next is the engine's."""
    eng = _engine_with_gate(RiskVerdict.APPROVED)
    idea = _run(_analyzer().analyze(_signal(), _candles()))
    assert idea is not None

    async def _analyze(sig, *_a, **_k):
        eng.analyzer.why_not.analysed(sig.symbol, "idea", ref=idea.id)
        return idea

    eng.analyzer.analyze = _analyze
    return eng


def test_an_idea_on_a_symbol_already_held_is_counted_there():
    eng = _engine_with_a_planted_idea()
    eng._same_symbol_verdict = lambda _idea, _key: "skip"
    assert _run(eng._analyze_signal(_signal(), lightweight=True)) is None
    assert eng.analyzer.why_not.reading()["by_stage"] == [("held", 1)]


def test_the_liquidity_gate_is_counted_by_its_stage_and_not_its_sentence():
    eng = _engine_with_a_planted_idea()

    async def _flow(*_a, **_k):
        return SimpleNamespace(bid_depth_usd=1.0, ask_depth_usd=1.0, spread_bps=40.0)

    async def _none(*_a, **_k):
        return None

    eng.order_flow.analyze = _flow
    eng.order_flow.liquidity_guard = (
        lambda *_a, **_k: "LIQUIDITY: size $1,234 is 9% of the book")
    eng.basis.get_basis = _none
    eng.market_cap.get_market_cap = _none
    assert _run(eng._analyze_signal(_signal())) is None
    got = eng.analyzer.why_not.reading()
    assert got["by_stage"] == [("liquidity", 1)]
    assert got["latest"][0]["reason"] == "", "the sentence carries the operator's size"


# ── confirm-time refusals are kept with whose confirm it was ────────────────

def _confirming_engine(answer: str):
    from bot.core.engine import RuneClawEngine
    eng = RuneClawEngine()
    idea = SimpleNamespace(id="TI-1", asset="SOL/USDT")
    eng._pending_ideas = {"TI-1": idea}

    async def _inner(trade_id, user_id=""):
        return answer

    eng._confirm_trade_inner = _inner
    eng.confirm_is_practice = lambda _uid: False
    return eng


def test_a_confirm_that_placed_nothing_is_kept_for_the_one_who_confirmed():
    eng = _confirming_engine("Trade REJECTED: re-check failed (DAILY_LOSS 3.1%)")
    _run(eng.confirm_trade("TI-1", user_id="111"))
    log = eng.analyzer.why_not
    mine = log.reading(viewer="111")
    assert mine["confirm_count"] == 1
    assert mine["confirms"][0]["symbol"] == "SOL"
    assert mine["confirms"][0]["reason"] == "the re-check at confirm rejected it"
    assert "3.1" not in mine["confirms"][0]["reason"], "the words, not the answer"
    assert log.reading(viewer="222")["confirm_count"] == 0, "nobody else's"


def test_a_confirm_that_placed_something_is_not_a_refusal():
    eng = _confirming_engine("\U0001f7e2 LONG SOL/USDT opened at 150.0")
    _run(eng.confirm_trade("TI-1", user_id="111"))
    assert eng.analyzer.why_not.reading(viewer="111")["confirm_count"] == 0


def test_a_confirm_of_an_idea_that_is_gone_is_kept_too():
    eng = _confirming_engine("unused")
    assert _run(eng.confirm_trade("TI-gone", user_id="111")) == "Trade not found or expired."
    got = eng.analyzer.why_not.reading(viewer="111")
    assert got["confirms"][0]["reason"] == "the idea had expired or was not found"


def test_a_duplicate_suppressed_at_confirm_is_kept_for_the_one_who_confirmed():
    eng = _confirming_engine("unused")
    eng.confirm_is_practice = lambda _uid: True
    eng._practice_book_symbols = lambda _uid: ["SOL/USDT"]
    assert _run(eng.confirm_trade("TI-1", user_id="111")).startswith("\u23ed")
    got = eng.analyzer.why_not.reading(viewer="111")
    assert got["confirms"][0]["reason"] == (
        "a position or order on this symbol already existed")


def test_the_engines_own_confirms_are_the_operators_to_see():
    log = WhyNotLog()
    log.refused_at_confirm("SOL/USDT", "auto", "the re-check at confirm rejected it")
    assert log.reading(viewer="111")["confirm_count"] == 0
    assert log.reading(viewer="111", operator=True)["confirm_count"] == 1
    eng = SimpleNamespace(analyzer=SimpleNamespace(why_not=log),
                          _is_operator_user=lambda uid: uid == "999")
    assert why_not.reading_for(eng, user_id="999")["confirm_count"] == 1
    assert why_not.reading_for(eng, user_id="111")["confirm_count"] == 0


# ── the record is bounded, and says how far back it reaches ─────────────────

class _Clock:
    def __init__(self, t: float = 1_800_000_000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


def test_a_window_longer_than_the_record_says_where_the_record_starts():
    clock = _Clock()
    log = WhyNotLog(clock=clock)
    clock.t += 3600
    log.analysed("BTC/USDT", "confidence", "Score 41% < 55% threshold")
    got = log.reading(6 * 3600)
    assert got["whole"] is False and got["from"] == log.started_at
    card = why_not.render(got)
    assert "since " in card and "asked for the last 6h" in card
    clock.t += 6 * 3600
    assert log.reading(6 * 3600)["whole"] is True


def test_rows_past_the_cap_cut_the_window_and_the_reading_says_so():
    clock = _Clock()
    log = WhyNotLog(clock=clock, max_rows=3)
    clock.t += 7 * 3600
    for i in range(5):
        clock.t += 60
        log.analysed("BTC/USDT", "confidence", f"#{i}")
    got = log.reading(6 * 3600)
    assert got["analyses"] == 3 and got["whole"] is False
    assert got["from"] > clock.t - 6 * 3600


def test_rows_older_than_a_day_are_dropped():
    clock = _Clock()
    log = WhyNotLog(clock=clock)
    log.analysed("BTC/USDT", "confidence")
    clock.t += WhyNotLog.HORIZON_S + 1
    log.analysed("ETH/USDT", "risk")
    assert len(log._rows) == 1
    assert log.reading(48 * 3600)["by_stage"] == [("risk", 1)]


# ── the card: read, absent and unread are three sentences ──────────────────

def _aged_log() -> WhyNotLog:
    """A record whose process started seven hours ago, so a six-hour window
    is whole."""
    clock = _Clock()
    log = WhyNotLog(clock=clock)
    clock.t += 7 * 3600
    return log


def _engine_with(log):
    return SimpleNamespace(analyzer=SimpleNamespace(why_not=log), _last_rejections={},
                           _is_operator_user=lambda _uid: False)


def _card(engine, **kw) -> str:
    from bot.skills.skill_registry import WhyNotSkill
    return _run(WhyNotSkill().execute(engine, **kw))


def test_the_card_counts_the_analyzers_skips_beside_the_gates_refusals():
    log = _aged_log()
    for stage, n in (("confidence", 3), ("regime_hard_gate", 2),
                     ("llm_direction_guard", 1), ("risk", 1)):
        for _ in range(n):
            log.analysed("ETH/USDT", stage, "why")
    out = _card(_engine_with(log))
    assert ("Of 7 analyses in the last 6h: 3 below the bar, 2 regime gate, "
            "1 direction guard, 1 risk gate.") in out, out
    assert "NO RISK-GATE REJECTIONS" in out, "the gate's own store, named as such"
    assert "No trades rejected" not in out


def test_one_symbols_card_counts_only_that_symbol():
    log = _aged_log()
    log.analysed("ETH/USDT", "confidence")
    log.analysed("SOL/USDT:USDT", "regime_hard_gate", "CHOP")
    out = _card(_engine_with(log), symbol="SOL")
    assert "Of 1 analysis on SOL in the last 6h: 1 regime gate." in out, out


def test_an_empty_window_is_absent_and_not_a_refusal():
    out = _card(_engine_with(_aged_log()))
    assert "No analysis is on record in the last 6h" in out
    assert "That is not a refusal" in out
    assert "Of 0" not in out


def test_an_engine_with_no_record_is_unread_and_not_empty():
    out = _card(SimpleNamespace(analyzer=SimpleNamespace(), _last_rejections={}))
    assert why_not.UNREAD_NO_RECORD in out
    assert "No analysis is on record" not in out


def test_a_process_that_is_not_the_bot_reads_neither_record():
    log = WhyNotLog()
    log.analysed("BTC/USDT", "confidence")
    eng = _engine_with(log)
    eng._state_persistence_detached = True
    eng._last_rejections = {"BTC": {"symbol": "BTC/USDT"}}
    out = _card(eng)
    assert why_not.UNREAD_NOT_THE_BOT in out
    assert "Of 1" not in out and "REJECTED" not in out


def test_the_chat_tool_is_footed_with_the_records_own_state():
    from bot.nlp import chat_tools
    from bot.nlp.tool_reading import parse_reading, prose_of
    from bot.skills.skill_registry import WhyNotSkill

    def handler(engine):
        return SimpleNamespace(registry=SimpleNamespace(get=lambda n: WhyNotSkill()),
                               conversations=None, engine=engine)

    log = WhyNotLog()
    log.analysed("BTC/USDT", "regime_hard_gate", "CHOP")
    states = {}
    for label, eng in (("read", _engine_with(log)),
                       ("absent", _engine_with(WhyNotLog())),
                       ("unread", SimpleNamespace(analyzer=None, _last_rejections={}))):
        out = _run(chat_tools.run_tool(handler(eng), "u1", "whynot", {}, {"whynot"}))
        states[label] = parse_reading(out)["read_state"]
        assert not prose_of(out).startswith(("READ", "ABSENT", "UNREAD"))
    assert states == {"read": "read", "absent": "absent", "unread": "unread"}
    # The slash command carries no tag.
    assert not _card(_engine_with(log)).startswith("READ")
