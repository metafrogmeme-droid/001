"""/portfolio said 104 trades. /journal said none. Both were "right".

On 2026-07-30, minutes apart on the same account:

    /portfolio → Trades: 104 | Win rate: 59%   (+ five recent closes listed)
    /journal   → ⚠️ No trades in the last 7 days.

The journal was written from exactly ONE place: the paper portfolio's
check_stops loop in the tick. In pure-live mode the paper portfolio is never
updated — the exchange is the source of truth — so that loop's `closed` list
is always empty and the journal received nothing, ever.

The comment beside that call already asserted the missing half:

    # Live closes already record via _on_live_position_closed

_on_live_position_closed did no such thing. A claim the code did not enforce,
written directly beside the code that would have had to enforce it.

This is the same shape as the 2026-07-14 audit CRITICAL on the daily-loss
breaker ("in pure-live mode the paper snapshot's daily_pnl is ~0 because live
fills never touch the paper portfolio") and the same fix: route the live close
into the recorder, on the path live closes actually take.
"""
from __future__ import annotations

import inspect
from datetime import datetime, timedelta

from bot.compat import UTC
from bot.core.engine import RuneClawEngine
from bot.core.trade_journal import TradeJournal

from tests.source_scan import code_only

SRC = code_only(inspect.getsource(RuneClawEngine._on_live_position_closed))


class _Pos:
    """A closed live position, as the executor hands it over."""
    def __init__(self, **over):
        self.trade_id = "TI-live-1"
        self.symbol = "INJ/USDT"
        self.direction = "LONG"
        self.strategy_type = "swing"
        self.entry_price = 10.0
        self.exit_price = 10.5
        self.stop_loss = 9.5
        self.take_profit = 11.0
        self.pnl_usd = 5.0
        self.close_reason = "take_profit"
        self.opened_at = datetime.now(UTC) - timedelta(hours=3)
        self.closed_at = datetime.now(UTC)
        self.__dict__.update(over)


def _journal(tmp_path) -> TradeJournal:
    return TradeJournal(journal_file=str(tmp_path / "journal.json"))


def _engine_stub():
    """The minimum surface _on_live_position_closed touches."""
    eng = type("E", (), {})()
    eng._invalidate_live_balance_cache = lambda: None
    eng.learning = type("L", (), {"record_closed_outcome": lambda *a, **k: None})()
    eng._outcome_regime = lambda sym: "trending"
    eng.risk_for = lambda uid: type(
        "R", (), {"record_live_trade_result": lambda self, p: None})()
    return eng


class TestTheLivePathRecords:
    def test_a_live_close_reaches_the_journal(self, tmp_path):
        j = _journal(tmp_path)
        eng = _engine_stub()
        eng.journal = j
        RuneClawEngine._on_live_position_closed(eng, _Pos())
        review = j.get_weekly_review()
        assert review["trades"] == 1, (
            "a live close must appear in the journal — this is the defect: "
            "/portfolio counted it and /journal did not"
        )

    def test_the_recorded_entry_carries_the_real_numbers(self, tmp_path):
        j = _journal(tmp_path)
        eng = _engine_stub()
        eng.journal = j
        RuneClawEngine._on_live_position_closed(eng, _Pos())
        e = j._entries[-1]
        assert e.symbol == "INJ/USDT"
        assert e.pnl == 5.0
        assert e.entry_price == 10.0 and e.exit_price == 10.5
        # Hold time comes from the real timestamps, not a placeholder.
        assert 2.9 < e.holding_hours < 3.1


class TestItStaysFailOpen:
    """A journal write must never cost a close its breaker feed."""

    def test_a_broken_journal_does_not_stop_the_breaker_feed(self, tmp_path):
        fed = []
        eng = _engine_stub()
        eng.journal = type("J", (), {
            "record_trade": lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk"))})()
        eng.learning = type("L", (), {"record_closed_outcome": lambda *a, **k: None})()
        eng._outcome_regime = lambda sym: ""
        eng.risk_for = lambda uid: type(
            "R", (), {"record_live_trade_result": lambda self, p: fed.append(p)})()
        RuneClawEngine._on_live_position_closed(eng, _Pos())
        assert fed == [5.0], "the loss breakers must still be fed"

    def test_a_position_without_pnl_records_nothing(self, tmp_path):
        # Unrealized/unknown PnL is not a zero-PnL trade.
        j = _journal(tmp_path)
        eng = _engine_stub()
        eng.journal = j
        RuneClawEngine._on_live_position_closed(eng, _Pos(pnl_usd=None))
        assert j.get_weekly_review()["trades"] == 0


class TestTheClaimIsNowEnforced:
    def test_the_live_path_calls_the_journal(self):
        assert "journal.record_trade(" in SRC, (
            "the comment at the paper site promises this; it must be true"
        )

    def test_the_journal_write_is_guarded(self):
        # Fail-open, like every other recorder on this path.
        assert "Journal record skipped for live close" in inspect.getsource(
            RuneClawEngine._on_live_position_closed)

    def test_the_stale_claim_is_gone(self):
        tick = code_only(inspect.getsource(RuneClawEngine))
        assert "Live closes already record via" not in tick, (
            "the old wording asserted behaviour that did not exist"
        )
