"""The learning panel reports the record, and a close reaches the scorer.

Strategies scored stayed 0 because nothing on a close called the strategy
scorer, and a closed outcome was written without a strategy name, which is
the only field the scorer keeps. Feedback entries stayed 0 because that
number was the human-comment file, and a close never wrote it. The eight
modules then shared the decision-log length and the current time, so a log
of rejected ideas read as 10/10.

A close is now stored for scoring. That store is not decision memory: paper
closes still do not train the confidence nudge while LEARN_FROM_PAPER_CLOSES
is off, and nothing here turns a learner on.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from bot.config import CONFIG
from bot.learning.orchestrator import LearningOrchestrator
from bot.learning.panel import (
    book_line,
    collect_facts,
    evidence_score,
    render_learning_panel,
)
from bot.learning.trade_outcomes import record_scored_outcome

# 11 wins of 1 and 29 losses of 1.6: win 11/40 = 27.5% (renders 28%), profit
# factor 11/46.4 = 0.237 (renders 0.24). The book the card was contradicting.
_WINS = [{"pnl": 1.0} for _ in range(11)]
_LOSSES = [{"pnl": -1.6} for _ in range(29)]
_BOOK = _WINS + _LOSSES


def _zero_facts() -> dict:
    detail = "insufficient data · 0 scored closes · updated never"
    names = [
        ("PatternMemory", "Groups of closed trades by the condition recorded on each row."),
        ("RegimeAdaptation", "Win rate of closes that recorded a market regime."),
        ("IndicatorWeightEvolution", "Voter-weight multipliers fitted from closed trades."),
        ("FeedbackLoop", "Closed trades recorded for scoring."),
        ("VolatilityProfiler", "Closes whose row recorded a volatility state."),
        ("CorrelationTracker", "Prices kept in this process for cross-asset context."),
        ("DrawdownRecovery", "No recovery model is fitted."),
        ("TimingOptimizer", "Hold-time and hour-of-day counts kept in this process."),
    ]
    return {
        "score": evidence_score(scored_closes=0, losses=0, profit_factor_value=None,
                                ready_lower_bound=None),
        "books": [
            "Paper book: no closed trades",
            "Testnet book: no closed trades",
        ],
        "modules": [
            {"name": name, "measures": measures, "status": "insufficient data", "detail": detail}
            for name, measures in names
        ],
        "strategy_lines": [],
        "trade_outcomes": 0,
        "strategies_scored": 0,
        "human_feedback": 0,
        "pending": 0,
        "blocked": 0,
        "voter_on": False,
        "expectancy_on": False,
        "calibration_line": "Confidence calibration: ACCUMULATING, 0 samples, flag off",
    }


class TestEvidenceScore:
    def test_no_closes_is_not_a_grade(self):
        score = evidence_score(scored_closes=0, losses=0, profit_factor_value=None,
                               ready_lower_bound=None)
        assert score["grade"] is None
        assert score["label"] == "insufficient data"

    def test_closes_without_a_ready_learner_are_not_a_grade(self):
        score = evidence_score(scored_closes=40, losses=29, profit_factor_value=0.24,
                               ready_lower_bound=None)
        assert score["grade"] is None
        assert score["label"] == "not validated"
        assert "10" in score["reason"]

    def test_a_ready_learner_on_a_losing_book_is_capped(self):
        score = evidence_score(scored_closes=40, losses=29, profit_factor_value=0.24,
                               ready_lower_bound=0.9)
        assert score["grade"] == 4.0
        assert score["label"] == "validated"
        assert "capped at 4" in score["reason"]

    def test_a_ready_learner_on_a_winning_book_uses_the_lower_bound(self):
        score = evidence_score(scored_closes=40, losses=10, profit_factor_value=1.5,
                               ready_lower_bound=0.7)
        assert score["grade"] == 7.0

    def test_a_bound_that_is_not_a_rate_is_not_a_grade(self):
        score = evidence_score(scored_closes=40, losses=10, profit_factor_value=1.5,
                               ready_lower_bound=1.4)
        assert score["grade"] is None
        assert score["label"] == "not validated"

    def test_no_losing_close_does_not_invent_a_profit_factor(self):
        score = evidence_score(scored_closes=10, losses=0, profit_factor_value=None,
                               ready_lower_bound=0.8)
        assert score["grade"] == 8.0
        assert "not computed" in score["reason"]

    def test_a_zero_profit_factor_is_a_measurement(self):
        # Every scored close lost: profit factor is 0, which is not "unread".
        score = evidence_score(scored_closes=5, losses=5, profit_factor_value=0.0,
                               ready_lower_bound=0.8)
        assert score["grade"] == 4.0
        assert "0.00" in score["reason"]


class TestPanelText:
    def test_the_zero_feedback_card_does_not_grade_itself(self):
        text = render_learning_panel(_zero_facts())
        assert "AI LEARNING SYSTEM" in text
        assert "No score." in text
        assert "insufficient data" in text
        assert "Human feedback: <code>0</code>" in text
        assert "Trade outcomes recorded: <code>0</code>" in text
        assert "Strategies scored: <code>0</code>" in text
        assert "Testnet book: no closed trades" in text
        assert "/10" not in text
        assert "100.0%" not in text
        assert "100%" not in text
        assert "ACTIVE" not in text
        assert "learns aggressively" not in text
        assert "Safety sandbox active" in text
        assert "VOTER_WEIGHT_LEARNING_ENABLED is off" in text
        assert "SETUP_EXPECTANCY_ENABLED is off" in text
        assert "do not override the risk engine" in text
        assert "not applied without approval" in text

    def test_the_book_line_is_the_closes_the_card_used_to_ignore(self):
        line = book_line("Paper book", _BOOK, unread=False)
        assert line == "Paper book: 40 closes, win 28%, profit factor 0.24"
        facts = _zero_facts()
        facts["books"] = [line, "Testnet book: no closed trades"]
        facts["score"] = evidence_score(
            scored_closes=40, losses=29, profit_factor_value=0.24, ready_lower_bound=None)
        facts["modules"][2] = {
            "name": "IndicatorWeightEvolution",
            "measures": "Voter-weight multipliers fitted from closed trades.",
            "status": "VALIDATING",
            "detail": "VALIDATING · 40 samples · 95% lower bound 42% · updated never · flag off",
        }
        facts["modules"][3] = {
            "name": "FeedbackLoop",
            "measures": "Closed trades recorded for scoring.",
            "status": "recorded",
            "detail": "recorded · 40 closes · updated 25 Oct 06:19 · win 28% · profit factor 0.24",
        }
        facts["trade_outcomes"] = 40
        text = render_learning_panel(facts)
        assert "Paper book: 40 closes, win 28%, profit factor 0.24" in text
        assert "VALIDATING" in text
        assert "95% lower bound 42%" in text
        assert "No score." in text
        assert "/10" not in text
        assert "learns aggressively" not in text

    def test_an_unread_book_is_not_an_empty_one(self):
        assert book_line("Testnet book", [], unread=True) == "Testnet book: not read"
        assert book_line("Paper book", [], unread=False) == "Paper book: no closed trades"

    def test_a_flat_close_is_not_a_loss_on_the_book_line(self):
        line = book_line("Paper book", [{"pnl": 0.0}], unread=False)
        assert "1 closes" in line
        assert "win 0%" in line
        assert "profit factor not computed" in line


class TestScoringRecord:
    def test_a_close_is_scored_and_does_not_enter_decision_memory(self, tmp_path):
        orch = LearningOrchestrator(str(tmp_path))
        assert record_scored_outcome(
            orch.store, orch.strategy,
            symbol="BTC/USDT", direction="LONG", pnl_result=-1.6,
            strategy="swing", trade_id="T1", mode="paper", source="paper_outcome",
        )
        assert record_scored_outcome(
            orch.store, orch.strategy,
            symbol="BTC/USDT", direction="LONG", pnl_result=-1.6,
            strategy="swing", trade_id="T1", mode="paper", source="paper_outcome",
        ) is False
        assert orch.store.get_decisions() == []
        assert orch.store.get_feedback() == []
        rows = orch.store.get_trade_outcomes()
        assert len(rows) == 1
        assert rows[0].pnl_result == -1.6
        assert rows[0].strategy == "swing"
        cards = orch.store.get_scorecards()
        assert list(cards) == ["swing"]
        assert cards["swing"].total_trades == 1
        # The nudge reads decision memory. This close is not in it.
        n, avg = orch.setup_record(symbol="BTC/USDT", market_regime="", direction="LONG")
        assert n == 0 and avg is None

    def test_an_unpriced_close_is_not_stored(self, tmp_path):
        orch = LearningOrchestrator(str(tmp_path))
        assert record_scored_outcome(
            orch.store, orch.strategy,
            symbol="BTC/USDT", direction="LONG", pnl_result=None,
            strategy="", trade_id="T0", mode="paper",
        ) is False
        assert orch.store.get_trade_outcomes() == []
        assert orch.store.get_scorecards() == {}

    def test_a_break_even_is_stored(self, tmp_path):
        orch = LearningOrchestrator(str(tmp_path))
        assert record_scored_outcome(
            orch.store, orch.strategy,
            symbol="ETH/USDT", direction="SHORT", pnl_result=0.0,
            strategy="", trade_id="T2", mode="demo", source="demo_outcome",
        )
        row = orch.store.get_trade_outcomes()[0]
        assert row.pnl_result == 0.0
        assert row.strategy == "not recorded"
        assert row.mode == "demo"


def _paper_engine(tmp_path):
    from bot.core.engine import RuneClawEngine
    engine = RuneClawEngine()
    engine.learning = LearningOrchestrator(str(tmp_path))
    engine._fetch_prices_by_category = AsyncMock(return_value={"BTC/USDT": 94.0})
    engine._last_known_prices = {}
    return engine


class TestTheCloseReachesTheScorer:
    def test_a_paper_close_is_scored_while_the_paper_flag_is_off(self, tmp_path, monkeypatch):
        monkeypatch.setattr("bot.utils.website_sync.sync_in_background", lambda *a, **k: None)
        monkeypatch.setattr(type(CONFIG), "is_live", lambda self: False)
        assert CONFIG.learning.learn_from_paper_closes_enabled is False
        assert CONFIG.analyzer.voter_weight_learning_enabled is False
        assert CONFIG.analyzer.setup_expectancy_enabled is False
        from bot.utils.models import Direction, TradeIdea
        engine = _paper_engine(tmp_path)
        idea = TradeIdea(
            asset="BTC/USDT", direction=Direction.LONG, entry_price=100.0,
            stop_loss=95.0, take_profit=120.0, confidence=0.7,
            reasoning="shared", source="scan")
        engine.portfolio.open_position(idea, 50.0, leverage=1)
        asyncio.run(engine._check_paper_positions(engine.portfolio.open_positions))
        assert engine.portfolio.trade_history
        assert engine.learning.store.get_decisions() == []
        rows = engine.learning.store.get_trade_outcomes()
        assert len(rows) == 1
        assert rows[0].mode == "paper"
        assert rows[0].source == "paper_outcome"
        assert rows[0].pnl_result is not None and rows[0].pnl_result < 0
        assert engine.learning.store.get_scorecards()
        n, _avg = engine.learning.setup_record(
            symbol="BTC/USDT", market_regime="", direction="LONG")
        assert n == 0

    def test_a_practice_close_is_not_scored(self, tmp_path, monkeypatch):
        monkeypatch.setattr("bot.utils.website_sync.sync_in_background", lambda *a, **k: None)
        monkeypatch.setattr(type(CONFIG), "is_live", lambda self: False)
        from bot.core.practice_fill import PRACTICE_FILL
        from bot.utils.models import Direction, TradeIdea
        engine = _paper_engine(tmp_path)
        book = engine.user_portfolios.get("owner")
        idea = TradeIdea(
            asset="BTC/USDT", direction=Direction.LONG, entry_price=100.0,
            stop_loss=95.0, take_profit=120.0, confidence=0.7,
            reasoning="practice", source="scan")
        book.open_position(idea, 50.0, leverage=1, fill_label=PRACTICE_FILL)
        asyncio.run(engine._check_paper_positions([]))
        assert book.trade_history
        assert engine.learning.store.get_trade_outcomes() == []
        assert engine.learning.store.get_decisions() == []

    def test_a_live_close_is_scored_and_keeps_its_mode(self, tmp_path, monkeypatch):
        from bot.core.engine import RuneClawEngine, decision_mode
        monkeypatch.setattr("bot.core.exchange_credentials.venue_sandbox", lambda venue: True)
        eng = RuneClawEngine.__new__(RuneClawEngine)
        eng._invalidate_live_balance_cache = lambda: None
        eng.learning = LearningOrchestrator(str(tmp_path))
        eng._outcome_regime = lambda _s: "RANGE"
        eng._auto_refit = MagicMock()
        eng._symbol_cooldowns = {}
        eng._symbol_cooldown_seconds = 60
        eng._symbol_loss_streaks = {}
        eng.risk = MagicMock()
        eng.risk_for = lambda _uid="": eng.risk
        eng.journal = MagicMock()
        eng.audit_chain = MagicMock()
        eng._sync_flight_records = lambda: None
        executor = SimpleNamespace(_venue=SimpleNamespace(id="bybit"))
        pos = SimpleNamespace(
            pnl_usd=-4.0, symbol="BTC/USDT", direction="LONG",
            close_reason="stop_loss", trade_id="T-LIVE", strategy_type="intraday")
        assert decision_mode(executor, live=True) == "demo"
        eng._on_live_position_closed(pos, "alice", executor=executor)
        decisions = eng.learning.store.get_decisions()
        assert len(decisions) == 1
        assert decisions[0].mode == "demo"
        assert decisions[0].strategy_signal == ""
        rows = eng.learning.store.get_trade_outcomes()
        assert len(rows) == 1
        assert rows[0].mode == "demo"
        assert rows[0].source == "demo_outcome"
        assert rows[0].strategy == "intraday"
        assert rows[0].pnl_result == -4.0
        assert rows[0].market_regime == "RANGE"
        assert "intraday" in eng.learning.store.get_scorecards()
        # The sandbox was asked for the mode, and the scored row reused it.
        assert eng.learning.store.get_feedback() == []


class TestCollectingAnEmptyRecord:
    def test_an_empty_engine_renders_insufficient_data(self, tmp_path):
        orch = LearningOrchestrator(str(tmp_path))

        class _Book:
            trade_history = []

            def snapshot(self):
                return SimpleNamespace(max_drawdown_pct=0.0)

        engine = SimpleNamespace(
            learning=orch,
            portfolio=_Book(),
            live_executor=SimpleNamespace(
                closed_positions=[], closed_trades_read_failed=False, _venue=None),
            hold_analytics=SimpleNamespace(_records={}),
            time_of_day=SimpleNamespace(_stats={}),
            cross_asset=SimpleNamespace(_price_history={}),
        )
        facts = collect_facts(engine, readiness={"components": {
            "voter_weights": {"state": "ACCUMULATING", "samples": 0, "applied": False},
            "setup_expectancy": {"state": "ACCUMULATING", "applied": False},
            "calibration": {"state": "ACCUMULATING", "samples": 0, "applied": False},
        }}, ingest=True)
        text = render_learning_panel(facts)
        assert "No score." in text
        assert "insufficient data" in text
        assert "/10" not in text
        assert "learns aggressively" not in text
        assert "Human feedback: <code>0</code>" in text
        assert facts["trade_outcomes"] == 0
        assert facts["strategies_scored"] == 0
        # Ingest of an empty book writes nothing.
        assert orch.store.get_decisions() == []
        assert orch.store.get_trade_outcomes() == []
