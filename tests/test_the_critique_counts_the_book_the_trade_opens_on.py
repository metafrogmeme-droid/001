"""The self-critique's heat check counts the book the trade opens on.

Before every confirm, `TradeCritique` argues the bear case, and one of its
concerns is heat: "N open positions — portfolio is hot" at four or more, which
takes 0.03 off the idea's confidence and counts toward a HALT. The count came
from `user_portfolios.combined_snapshot()`: every user's PRACTICE book,
summed, in live mode too. Driven through the real confirm path:

- a live trade on a FLAT live book, while some user's practice book held
  seven positions, was critiqued as hot, and the engine's own auto-confirm at
  0.62 fell to 0.59 under the 0.60 floor and was REJECTED;
- a live book holding four positions, with no practice books, was critiqued
  as holding none.

The critique now counts what the risk re-check just read: live, the open
count of the account this order executes on (`_LiveRecheck.open_count`);
paper, the book the re-check engine's gates read (`RiskEngine.book_snapshot`).
"""
from __future__ import annotations

from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

import pytest

from bot.config import CONFIG
from bot.core import critique as _crit
from bot.core.engine import RuneClawEngine, _LiveRecheck
from tests.test_a_seal_failure_does_not_unplace_a_trade import _engine, _run


@pytest.fixture(autouse=True)
def _no_website_sync(monkeypatch):
    monkeypatch.setattr("bot.utils.website_sync.sync_in_background",
                        lambda *a, **k: None)


def _spy(seen):
    real = _crit.TradeCritique.evaluate

    def spy(self, idea, risk_check, snapshot, macro=None):
        seen.append(getattr(snapshot, "open_positions", "?"))
        return real(self, idea, risk_check, snapshot, macro)
    return patch.object(_crit.TradeCritique, "evaluate", spy)


def _practice_books_hold(engine, n):
    """Some other user's practice book holds `n` open positions."""
    engine.user_portfolios.all_portfolios = lambda: {"999": object()}
    engine.user_portfolios.combined_snapshot = lambda: NS(open_positions=n)


def _live_confirm(engine, idea, user_id="123456", live_count=0):
    with patch.object(type(CONFIG), "is_live", return_value=True), \
         patch("bot.core.engine.get_exchange_position_count",
               new=AsyncMock(return_value=live_count)), \
         patch("bot.core.engine.invalidate_position_count_cache"):
        return _run(engine.confirm_trade(idea.id, user_id=user_id))


class TestLive:
    def test_other_peoples_practice_positions_are_not_live_heat(self, tmp_path):
        engine, idea = _engine(tmp_path)
        _practice_books_hold(engine, 7)
        seen: list = []
        with _spy(seen):
            result = _live_confirm(engine, idea, live_count=0)
        assert seen == [0]
        assert "LIVE LONG" in result

    def test_live_positions_are_live_heat(self, tmp_path):
        engine, idea = _engine(tmp_path)
        _practice_books_hold(engine, 0)
        seen: list = []
        with _spy(seen):
            _live_confirm(engine, idea, live_count=4)
        assert seen == [4]

    def test_an_auto_confirm_is_not_rejected_for_practice_positions(self, tmp_path):
        engine, idea = _engine(tmp_path)
        idea.confidence = CONFIG.risk.min_confidence + 0.02
        idea.source = "unknown"
        _practice_books_hold(engine, 7)
        result = _live_confirm(engine, idea, user_id="auto", live_count=0)
        assert "LIVE LONG" in result, result
        assert "post-critique confidence" not in result

    def test_the_count_is_the_rechecks_own(self, tmp_path):
        """Whichever account the re-check read (the operator's, or a user's
        own under per-user live), the critique counts that reading."""
        engine, idea = _engine(tmp_path)
        _practice_books_hold(engine, 0)

        async def _rc(user_id=""):
            return _LiveRecheck(50000.0, 4, 50000.0, (), account="bybit")

        engine._live_recheck_context = _rc
        seen: list = []
        with _spy(seen):
            _live_confirm(engine, idea, live_count=0)
        assert seen == [4]


class TestPaper:
    def _practice_confirm(self, engine, idea):
        engine._simulate_paper_fill = AsyncMock(return_value="✅ [PAPER] filled")
        engine._user_store = NS(sim_opt_in=lambda _uid: True)
        was = CONFIG.paper_sim_opt_in_enabled
        object.__setattr__(CONFIG, "paper_sim_opt_in_enabled", True)
        try:
            with patch.object(type(CONFIG), "is_live", return_value=False):
                return _run(engine.confirm_trade(idea.id, user_id="123456"))
        finally:
            object.__setattr__(CONFIG, "paper_sim_opt_in_enabled", was)

    def test_a_practice_fill_counts_the_book_its_gates_read(self, tmp_path):
        engine, idea = _engine(tmp_path)
        _practice_books_hold(engine, 7)
        engine.risk.book_snapshot = lambda: NS(open_positions=2)
        seen: list = []
        with _spy(seen):
            result = self._practice_confirm(engine, idea)
        assert seen == [2], "counted another user's practice book"
        assert result == "✅ [PAPER] filled"

    def test_under_per_user_live_it_is_the_callers_own_practice_book(
            self, tmp_path):
        engine, idea = _engine(tmp_path)
        engine._is_operator_user = lambda uid: False
        _practice_books_hold(engine, 7)
        # The operator engine's book holds positions too, so reading the
        # shared engine instead of the caller's is visible.
        engine.risk.book_snapshot = lambda: NS(open_positions=3)
        was = CONFIG.per_user_live_enabled
        object.__setattr__(CONFIG, "per_user_live_enabled", True)
        try:
            seen: list = []
            with _spy(seen):
                self._practice_confirm(engine, idea)
        finally:
            object.__setattr__(CONFIG, "per_user_live_enabled", was)
        own = engine.user_portfolios.get("123456").snapshot().open_positions
        assert seen == [own] == [0]


class TestTheSeam:
    def test_live_reads_the_recheck_count(self, monkeypatch):
        monkeypatch.setattr(type(CONFIG), "is_live", lambda self: True)
        snap = RuneClawEngine._critique_book(
            NS(book_snapshot=lambda: pytest.fail("read the paper book live")),
            _LiveRecheck(100.0, 3, 100.0))
        assert snap.open_positions == 3

    def test_paper_reads_the_recheck_engines_book(self, monkeypatch):
        monkeypatch.setattr(type(CONFIG), "is_live", lambda self: False)
        snap = RuneClawEngine._critique_book(
            NS(book_snapshot=lambda: NS(open_positions=5)),
            _LiveRecheck(None, None, None))
        assert snap.open_positions == 5

    def test_book_snapshot_is_the_engines_own_tracker(self):
        from bot.risk.risk_engine import RiskEngine
        tracker = NS(snapshot=lambda: "the tracker's snapshot")
        eng = RiskEngine.__new__(RiskEngine)
        eng._portfolio = tracker
        assert eng.book_snapshot() == "the tracker's snapshot"
