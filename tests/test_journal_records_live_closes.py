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

from types import SimpleNamespace as NS

from bot.compat import UTC
from bot.core.engine import RuneClawEngine
from bot.core.trade_journal import TradeJournal
from bot.skills.callback_handler import book_untracked_exchange_close
from bot.skills.engine_ops_commands import (
    _journal_gap_closes,
    _window_coverage_line,
)

from tests.source_scan import code_only

SRC = code_only(inspect.getsource(RuneClawEngine.journal_live_close))
HOOK = code_only(inspect.getsource(RuneClawEngine._on_live_position_closed))


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
        "R", (), {"record_live_trade_result": lambda self, p, **kw: None})()
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


class TestAnUnrecordedConfidenceTeachesNothing:
    """#515: a live close passes no confidence (a live position carries none)
    and the journal defaulted it to 0.0, so every losing live close earned
    "Low confidence trade lost" on the weekly card. Absent is None now, and
    the confidence lessons speak only about a recorded figure."""

    def test_a_losing_live_close_is_not_called_low_confidence(self, tmp_path):
        j = _journal(tmp_path)
        eng = _engine_stub()
        eng.journal = j
        RuneClawEngine._on_live_position_closed(eng, _Pos(pnl_usd=-5.0, close_price=9.5))
        e = j._entries[-1]
        assert e.pnl < 0
        assert e.confidence is None
        assert not any("confidence" in lesson.lower() for lesson in e.lessons), e.lessons

    def test_a_recorded_confidence_still_teaches_both_lessons(self, tmp_path):
        j = _journal(tmp_path)
        base = dict(symbol="BTC/USDT", direction="LONG", strategy_type="swing",
                    entry_price=100.0, stop_loss=95.0, take_profit=110.0)
        lost = j.record_trade(trade_id="t1", exit_price=96.0, pnl=-4.0, confidence=0.5, **base)
        won = j.record_trade(trade_id="t2", exit_price=108.0, pnl=8.0, confidence=0.9, **base)
        assert any(lesson.startswith("Low confidence") for lesson in lost.lessons)
        assert any(lesson.startswith("High confidence") for lesson in won.lessons)
        # A measured zero is a reading, and a loss at it is low confidence.
        zero = j.record_trade(trade_id="t3", exit_price=96.0, pnl=-4.0, confidence=0.0, **base)
        assert any(lesson.startswith("Low confidence") for lesson in zero.lessons)

    def test_an_absent_confidence_survives_a_restart_as_absent(self, tmp_path):
        j = _journal(tmp_path)
        j.record_trade(trade_id="t1", symbol="BTC/USDT", direction="LONG", strategy_type="swing",
                       entry_price=100.0, exit_price=96.0, stop_loss=95.0, take_profit=110.0, pnl=-4.0)
        again = TradeJournal(journal_file=str(tmp_path / "journal.json"))
        assert again._entries[-1].confidence is None


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
            "R", (), {"record_live_trade_result": lambda self, p, **kw: fed.append(p)})()
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
        assert "RuneClawEngine.journal_live_close(" in HOOK, (
            "the close hook must journal through the one writer"
        )
        assert "journal.record_trade(" in SRC, (
            "the comment at the paper site promises this; it must be true"
        )

    def test_the_journal_write_is_guarded(self):
        # Fail-open, like every other recorder on this path.
        assert "Journal record skipped for live close" in inspect.getsource(
            RuneClawEngine.journal_live_close)

    def test_the_stale_claim_is_gone(self):
        tick = code_only(inspect.getsource(RuneClawEngine))
        assert "Live closes already record via" not in tick, (
            "the old wording asserted behaviour that did not exist"
        )


class _Ledger:
    """The executor surface the exchange-direct close writes."""

    def __init__(self, *, keep: bool = True, store: bool = True) -> None:
        self.user_id = "alice"
        self._closed_trades: list = []
        self._keep = keep
        self._store = store

    def _append_closed_trade(self, pos) -> bool:
        if self._store:
            self._closed_trades.append(pos)
        return self._keep

    @property
    def closed_positions(self):
        return list(self._closed_trades)


def _live(**over):
    base = dict(
        trade_id="TI-manual-ICP-1",
        symbol="ICP/USDT",
        direction="LONG",
        strategy_type="swing",
        entry_price=10.0,
        close_price=9.0,
        stop_loss=0.0,
        take_profit=0.0,
        quantity=1.0,
        pnl_usd=-2.92,
        close_reason="manual_exchange",
        opened_at=datetime.now(UTC) - timedelta(hours=1),
        closed_at=datetime.now(UTC),
    )
    base.update(over)
    return NS(**base)


def _wired(tmp_path, ledger):
    """A stub engine whose breaker hook raises if this path calls it."""
    eng = _engine_stub()
    eng.journal = _journal(tmp_path)
    eng.live_executor = ledger
    eng._user_executors = {}

    def _breaker(*_a, **_k):
        raise AssertionError("the loss-breaker path must not run from this close")

    eng._on_live_position_closed = _breaker
    return eng


class TestTheUntrackedCloseIsJournaledWhenPriced:
    """The Close button's exchange fallback used to ledger and stop.

    The bot is up — the operator tapped Close — and the venue returned a
    price. That close belongs in the journal. An unreadable fill stays out
    of it and stays in the weekly gap. A measured 0.00 stays a flat.
    """

    def test_a_priced_close_is_journaled_in_dollars(self, tmp_path):
        ledger = _Ledger()
        eng = _wired(tmp_path, ledger)
        book_untracked_exchange_close(eng, ledger, _live())
        rev = eng.journal.get_weekly_review()
        assert rev["trades"] == 1
        assert rev["losses"] == 1
        assert rev["wins"] == 0
        assert rev["flat"] == 0
        assert rev["total_pnl"] == -2.92
        assert eng.journal._entries[-1].pnl == -2.92
        assert eng.journal._entries[-1].user_id == "alice"
        # The executor and the journal each hold the close, so the gap is 0.
        assert _journal_gap_closes(eng, days=7) == 1
        assert _window_coverage_line(_journal_gap_closes(eng, days=7), rev["trades"]) == ""

    def test_a_measured_flat_stays_flat(self, tmp_path):
        ledger = _Ledger()
        eng = _wired(tmp_path, ledger)
        book_untracked_exchange_close(eng, ledger, _live(pnl_usd=0.0, trade_id="TI-flat"))
        rev = eng.journal.get_weekly_review()
        assert rev["trades"] == 1
        assert rev["flat"] == 1
        assert rev["losses"] == 0
        assert rev["wins"] == 0
        # The flat is in the denominator: 0 wins / 1 scored, not "no trades".
        assert rev["win_rate"] == 0.0
        assert rev["losses"] != rev["trades"] - rev["wins"]

    def test_an_unpriced_close_is_omitted_and_still_a_gap(self, tmp_path):
        ledger = _Ledger()
        eng = _wired(tmp_path, ledger)
        book_untracked_exchange_close(
            eng, ledger, _live(pnl_usd=None, close_price=None, trade_id="TI-unread"))
        rev = eng.journal.get_weekly_review()
        assert rev["trades"] == 0
        assert ledger.closed_positions, "the executor still holds the row"
        gap = _journal_gap_closes(eng, days=7)
        assert gap == 1
        line = _window_coverage_line(gap, rev["trades"])
        assert "1 more position(s) closed" in line
        assert "recording gap" in line

    def test_a_duplicate_the_ledger_refuses_is_not_journaled(self, tmp_path):
        ledger = _Ledger(keep=False, store=False)
        eng = _wired(tmp_path, ledger)
        book_untracked_exchange_close(eng, ledger, _live())
        assert eng.journal.get_weekly_review()["trades"] == 0
        assert ledger.closed_positions == []

    def test_a_journal_fault_does_not_raise_into_the_close(self, tmp_path):
        ledger = _Ledger()
        eng = _wired(tmp_path, ledger)
        eng.journal = type("J", (), {
            "record_trade": lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk")),
        })()
        book_untracked_exchange_close(eng, ledger, _live())
        assert len(ledger.closed_positions) == 1
