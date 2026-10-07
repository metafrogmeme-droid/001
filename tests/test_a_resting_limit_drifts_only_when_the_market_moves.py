"""A resting limit drifts when the MARKET moves away from it, not when it sits
where it was placed.

The drift rule cancels a resting limit once "the market drifts X% away" (and,
for the engine's own idea with momentum behind it, chases it at the market).
It measured that as the limit's distance from the market, and the analyzer
places a pullback limit up to one ATR from the market on purpose
(`analyzer._compute_limit_entry`). Driven on the unfixed tree, with a 3% ATR
the analyzer's SOL LONG limit sat at 97.2 under a market at 100, and with the
market NOT MOVING the first monitor pass either cancelled it (no trend) or
marketed it at 100 (a trend up): the pullback entry turned into the price the
analysis said to wait below.

`limit_entry.resting_limit_drift` is the one reading the live monitor and the
backtest's resting limits both ask: the market's move since placement, signed
so that away from the limit is positive. The market a limit was placed against
is recorded on the resting row (and saved), and a row placed before it was
recorded is measured as it always was.

The confirm half: F-05 refuses a MARKET idea the market has moved more than 2%
from since the analysis, and skipped every LIMIT idea, because a limit's entry
sits away from the market and its distance from the entry says nothing. So an
idea the market had run 4% from was placed at the old pullback level. The
analyzer now records the market the analysis was made at
(`TradeIdea.market_at_signal`), and the confirm refuses a stale limit idea
measured from that, never from the entry.
"""
from __future__ import annotations

import ast
import asyncio
import inspect
import json
from datetime import datetime
from types import SimpleNamespace

import pytest

from bot.backtest.engine import BacktestEngine
from bot.compat import UTC
from bot.config import CONFIG
from bot.core import analyzer as analyzer_mod
from bot.core.limit_entry import resting_limit_drift
from bot.core.live_executor import LiveExecutor
from bot.utils.models import Direction, TradeIdea
from tests.source_scan import code_only
from tests.test_a_resting_limit_is_sized_at_its_own_price import (
    PRICE,
    _five_x,
    _market,
    _Recording,
    _run,
    _typed,
)
from tests.test_a_scan_cards_levels_are_placed_as_shown import (
    _by,
    _engine_idea,
    _pending,
)
from tests.test_a_scan_cards_levels_are_placed_as_shown import (
    audits as _scan_audits,
)
from tests.test_a_seal_failure_does_not_unplace_a_trade import _confirm
from tests.test_a_seal_failure_does_not_unplace_a_trade import (
    _no_website_sync as _seal_no_website_sync,
)
from tests.test_an_unverified_submission_is_neither_a_fill_nor_a_failure import (
    TICKER,
    _order,
    _standing,
)
from tests.test_the_drift_fallback_places_what_was_approved import (
    _executor,
    _pending_check,
    _resting,
    _Venue,
)

_no_website_sync = _seal_no_website_sync      # autouse binds per module
_five_x_leverage = _five_x                    # the resting suite's 5x, for its `_run`
audits = _scan_audits

# The survey's own figures: an analyzer pullback limit 2.8% under the market.
LIMIT, PLACED = 97.2, 100.0


@pytest.fixture(autouse=True)
def _premises():
    assert CONFIG.limit_orders.price_drift_cancel_pct == 2.0
    assert CONFIG.limit_orders.drift_market_fallback is True


# ── the one reading ──────────────────────────────────────────────────────

class TestTheReading:

    @pytest.mark.parametrize("side,cur,expected", [
        ("LONG", 100.0, 0.0),        # standing still is not drift
        ("LONG", 102.5, 2.5),        # up and away from a buy limit
        ("LONG", 99.0, -1.0),        # toward it: the fill coming, not drift
        ("SHORT", 97.0, 3.0),        # down and away from a sell limit
        ("SHORT", 101.0, -1.0),
        ("buy", 102.5, 2.5),
        ("sell", 97.0, 3.0),
        (Direction.LONG, 102.5, 2.5),
        (Direction.SHORT, 97.0, 3.0),
    ], ids=["still", "long-away", "long-toward", "short-away", "short-toward",
            "buy", "sell", "enum-long", "enum-short"])
    def test_the_market_move_since_placement(self, side, cur, expected):
        limit = LIMIT if str(getattr(side, "value", side)).upper() in ("LONG", "BUY") else 103.0
        pct, basis = resting_limit_drift(side, limit, cur, PLACED)
        assert basis == "placement"
        assert pct == pytest.approx(expected)

    def test_standing_still_short_is_a_plain_zero(self):
        pct, _ = resting_limit_drift("SHORT", 103.0, 100.0, 100.0)
        assert str(pct) == "0.0"

    @pytest.mark.parametrize("placed", [None, 0.0, -1.0, float("nan"), float("inf"), "abc", True])
    def test_no_placement_on_record_is_measured_from_the_limit(self, placed):
        pct, basis = resting_limit_drift("LONG", LIMIT, 100.0, placed)
        assert basis == "limit"
        assert pct == pytest.approx((100.0 - LIMIT) / LIMIT * 100)

    def test_a_side_it_cannot_read_is_measured_from_the_limit(self):
        pct, basis = resting_limit_drift("SIDEWAYS", LIMIT, 100.0, PLACED)
        assert basis == "limit" and pct == pytest.approx(2.8806584)

    @pytest.mark.parametrize("cur,limit", [(None, LIMIT), (0.0, LIMIT), (100.0, None),
                                           (100.0, 0.0), ("x", LIMIT)])
    def test_an_unreadable_price_is_no_reading(self, cur, limit):
        assert resting_limit_drift("LONG", limit, cur, PLACED) is None


# ── the live monitor ─────────────────────────────────────────────────────

def _engine_row(**extra):
    return _resting(entry=LIMIT, idea_source="unknown", **extra)


class TestTheMonitorMeasuresFromPlacement:

    def test_a_pullback_limit_with_the_market_standing_still_keeps_resting(self, tmp_path):
        """The survey's drive: momentum up, the market where it was placed.
        It used to reach the fallback and market the order at 100."""
        ex = _executor(tmp_path)
        pos = _engine_row(placed_market_price=PLACED)
        venue = _Venue(PLACED)
        msg, audits_ = _pending_check(ex, pos, venue)
        assert msg is None and pos.status == "pending_fill"
        assert venue.cancels == [] and venue.orders == []
        assert ex._check_drift_market_fallback.await_count == 0
        assert ex._execute_drift_market_fallback.await_count == 0
        assert not [a for a in audits_ if a.get("action") == "limit_drift_cancel"]
        assert venue.tickers, "the drift was read, and read as none"

    def test_a_pullback_limit_the_market_ran_away_from_still_drifts(self, tmp_path):
        ex = _executor(tmp_path)
        pos = _engine_row(placed_market_price=PLACED)
        msg, audits_ = _pending_check(ex, pos, _Venue(102.5), momentum=False)
        assert pos.status == "closed" and pos.close_reason == "price_drift"
        (row,) = [a for a in audits_ if a.get("result") == "CANCELLING"]
        assert row["data"]["basis"] == "placement"
        assert row["data"]["pct_away"] == pytest.approx(2.5)
        assert "moved 2.5% away since the order was placed" in row["message"]

    def test_with_momentum_it_still_reaches_the_fallback(self, tmp_path):
        ex = _executor(tmp_path)
        pos = _engine_row(placed_market_price=PLACED)
        msg, audits_ = _pending_check(ex, pos, _Venue(102.5))
        assert msg == "FALLBACK RAN"
        (row,) = [a for a in audits_ if a.get("result") == "CONVERTING"]
        assert row["data"]["basis"] == "placement"

    def test_a_move_toward_the_limit_is_not_drift(self, tmp_path):
        ex = _executor(tmp_path)
        pos = _engine_row(placed_market_price=PLACED)
        venue = _Venue(98.0)
        msg, _ = _pending_check(ex, pos, venue)
        assert msg is None and pos.status == "pending_fill" and venue.cancels == []

    def test_a_row_placed_before_the_market_was_recorded_keeps_the_old_rule(self, tmp_path):
        """No placement on record: measured from the limit, as every row was."""
        ex = _executor(tmp_path)
        pos = _engine_row()
        msg, audits_ = _pending_check(ex, pos, _Venue(PLACED), momentum=False)
        assert pos.status == "closed" and pos.close_reason == "price_drift"
        (row,) = [a for a in audits_ if a.get("result") == "CANCELLING"]
        assert row["data"]["basis"] == "limit"
        assert row["message"].startswith("Price drifted 2.9% from limit")

    def test_the_short_side_is_signed_the_other_way(self, tmp_path):
        ex = _executor(tmp_path)
        pos = _resting(direction="SHORT", entry=103.0, idea_source="unknown",
                       placed_market_price=PLACED)
        venue = _Venue(102.0)                       # up, toward the sell limit
        msg, _ = _pending_check(ex, pos, venue, momentum=False)
        assert msg is None and pos.status == "pending_fill" and venue.cancels == []
        venue = _Venue(97.5)                        # down and away
        msg, _ = _pending_check(ex, pos, venue, momentum=False)
        assert pos.status == "closed" and pos.close_reason == "price_drift"


class TestThePlacementIsRecorded:

    def test_execute_records_the_market_a_resting_limit_was_placed_against(self, tmp_path):
        venue = _Recording(_market(amount_step=0.0001, amount_min=0.0001), PRICE,
                           answer={"id": "o-rest", "status": "open", "filled": 0})
        result, _orders, _audits, ex = _run(venue, _typed(Direction.LONG, 3600.0), 20.0, tmp_path)
        (pos,) = [p for p in ex._positions.values() if p.status == "pending_fill"]
        assert getattr(pos, "placed_market_price", None) == PRICE, result

    def test_a_market_fill_records_none(self, tmp_path):
        """Driven through the real `execute` against the placed-order suite's
        venue, filling at the market (the drift suite's own drive): the open
        position carries no placement market, because nothing rests."""
        from bot.config import RUNTIME
        from bot.core import bounds_shadow
        from bot.core import live_executor as le
        from tests.test_the_placed_order_is_the_checked_order import _idea
        from tests.test_the_placed_order_is_the_checked_order import _Venue as _PlacedVenue

        class _Filling(_PlacedVenue):
            async def create_order(self, symbol=None, type=None, side=None, amount=None,
                                   price=None, params=None, **k):
                self.orders.append({"amount": float(amount), "type": type})
                return {"id": "M1", "status": "closed", "filled": float(amount),
                        "average": self.price, "cost": float(amount) * self.price}

            async def fetch_order(self, oid, symbol=None, params=None):
                return {"id": oid, "status": "closed", "filled": self.orders[-1]["amount"],
                        "average": self.price}

            async def fetch_my_trades(self, *a, **k):
                return []

        from unittest.mock import AsyncMock, patch
        was = RUNTIME.leverage_override
        RUNTIME.leverage_override = None
        ex = LiveExecutor(state_dir=str(tmp_path))
        ex._exchange = _Filling(_market(), 4000.0)
        ex._place_sl_tp = AsyncMock(return_value=("S1", "T1"))
        ex._reattempt_post_fill_sl = AsyncMock(
            side_effect=lambda exchange, pos, direction, qty, sl, tp, tid: (sl, tp, None))
        ex.sync_positions_from_exchange = AsyncMock(return_value=None)
        ex._guard_fill_leverage = AsyncMock(return_value=None)
        try:
            with patch.object(le, "audit", lambda *a, **k: None), \
                 patch.object(bounds_shadow.BOUNDS_LEDGER, "record", lambda *a, **k: None), \
                 patch.object(type(CONFIG), "is_live", return_value=True):
                result = asyncio.run(ex.execute(_idea(4000.0), size_usd=50.0,
                                                order_type="market"))
        finally:
            RUNTIME.leverage_override = was
        assert "LIVE BUY" in result, result
        (pos,) = ex._positions.values()
        assert pos.status == "open"
        assert getattr(pos, "placed_market_price", None) is None

    def test_it_survives_a_restart(self, tmp_path):
        ex = _executor(tmp_path)
        ex._positions["T1"] = _engine_row(placed_market_price=PLACED)
        ex._save_positions()
        back = LiveExecutor(state_dir=str(tmp_path), user_id="7")._positions["T1"]
        assert getattr(back, "placed_market_price", None) == PLACED

    @pytest.mark.parametrize("saved", ["absent", None, 0, -5.0, "abc", True])
    def test_a_saved_figure_that_is_not_a_price_restores_as_absent(self, tmp_path, saved):
        ex = _executor(tmp_path)
        ex._positions["T1"] = _engine_row(placed_market_price=PLACED)
        ex._save_positions()
        (f,) = [p for p in tmp_path.iterdir() if p.name.startswith("live_positions")]
        rows = json.loads(f.read_text())
        for row in rows.values() if isinstance(rows, dict) else rows:
            if saved == "absent":
                row.pop("placed_market_price", None)
            else:
                row["placed_market_price"] = saved
        f.write_text(json.dumps(rows))
        back = LiveExecutor(state_dir=str(tmp_path), user_id="7")._positions["T1"]
        assert getattr(back, "placed_market_price", None) is None

    def test_a_recovered_resting_submission_records_the_pre_order_ticker(self, tmp_path):
        h, ex = _standing(tmp_path, open_list=[_order(status="open", filled=0.0,
                                                       average=None, price=3990.0)])
        h.reconcile(ex)
        pos = ex._positions["TI-EST"]
        assert pos.status == "pending_fill"
        assert getattr(pos, "placed_market_price", None) == TICKER


# ── the backtest's resting limits ask the same reading ────────────────────

def _bar(close, low=None, high=None, ts=1_700_000_000):
    return SimpleNamespace(close=close, low=close if low is None else low,
                           high=close if high is None else high,
                           timestamp=datetime.fromtimestamp(ts, UTC))


class _Risk:
    def __init__(self):
        self.cleared: list = []

    def clear_pending_intent(self, idea_id):
        self.cleared.append(idea_id)


def _bt(order):
    eng = BacktestEngine.__new__(BacktestEngine)
    eng._pending_limits = [order]
    eng._limits_filled = eng._limits_expired = eng._limits_cancelled_drift = 0
    eng.risk = _Risk()
    eng._execute_fill = lambda *a, **k: None
    return eng


def _order_dict(**extra):
    idea = SimpleNamespace(id="B1", direction=Direction.LONG)
    return {"idea": idea, "risk_check": None, "px": LIMIT, "placed_ts": 1_700_000_000,
            **extra}


class TestTheBacktestMeasuresFromPlacement:

    def test_an_untouched_pullback_limit_with_a_still_market_keeps_resting(self):
        eng = _bt(_order_dict(placed_close=PLACED))
        eng._drain_pending_limits(_bar(PLACED, ts=1_700_003_600))
        assert eng._limits_cancelled_drift == 0 and len(eng._pending_limits) == 1

    def test_a_market_that_ran_away_cancels_it(self):
        eng = _bt(_order_dict(placed_close=PLACED))
        eng._drain_pending_limits(_bar(102.5, ts=1_700_003_600))
        assert eng._limits_cancelled_drift == 1 and eng._pending_limits == []
        assert eng.risk.cleared == ["B1"]

    def test_an_order_placed_without_a_close_keeps_the_old_rule(self):
        eng = _bt(_order_dict())
        eng._drain_pending_limits(_bar(PLACED, ts=1_700_003_600))
        assert eng._limits_cancelled_drift == 1

    def test_the_placement_records_the_signal_bars_close(self):
        eng = BacktestEngine.__new__(BacktestEngine)
        eng._pending_limits = []
        eng._limits_filled = eng._limits_filled_same_bar = 0
        idea = SimpleNamespace(entry_price=LIMIT, order_type="limit", direction=Direction.LONG)
        eng._place_entry(idea, None, _bar(PLACED, low=98.0, high=101.0))
        (order,) = eng._pending_limits
        assert order["placed_close"] == PLACED and order["px"] == LIMIT


# ── the confirm: a limit idea is stale measured from the analysis ───────

class TestTheConfirmReadsTheMarketTheAnalysisSaw:

    def test_a_limit_idea_the_market_ran_from_is_refused(self, tmp_path, audits):
        idea = _engine_idea(entry_price=97.2, stop_loss=93.0, take_profit=105.0,
                            market_at_signal=100.0)
        engine, handed = _pending(tmp_path, idea, 104.0)
        answer = _confirm(engine, idea)
        assert answer.startswith("Trade REJECTED: the market moved 4.0% from the price at analysis")
        assert "$100.00 → $104.00, the perp this limit rests on" in answer
        assert "limit at $97.20" in answer
        assert handed == []
        (row,) = _by(audits, "price_drift")
        assert row["result"] == "REJECTED"
        assert row["data"]["market_at_signal"] == 100.0 and row["data"]["order_type"] == "limit"
        assert row["data"]["drift_pct"] == 4.0

    def test_the_same_idea_inside_the_band_is_placed(self, tmp_path, audits):
        """Placed 2.8% under the market it was analysed at: the distance
        from the entry is the design, not a drift."""
        idea = _engine_idea(entry_price=97.2, stop_loss=93.0, take_profit=105.0,
                            market_at_signal=100.0)
        engine, handed = _pending(tmp_path, idea, 101.0)
        assert _confirm(engine, idea).startswith("✅")
        assert len(handed) == 1 and _by(audits, "price_drift") == []

    def test_a_move_of_exactly_the_band_is_not_over_it(self, tmp_path, audits):
        idea = _engine_idea(entry_price=97.2, stop_loss=93.0, take_profit=105.0,
                            market_at_signal=100.0)
        engine, handed = _pending(tmp_path, idea, 102.0)
        assert _confirm(engine, idea).startswith("✅") and len(handed) == 1

    def test_a_fall_of_more_than_the_band_is_stale_too(self, tmp_path, audits):
        """Either direction, as the market-idea rule reads it: the analysis
        was made against a market that is no longer there."""
        idea = _engine_idea(entry_price=97.2, stop_loss=93.0, take_profit=105.0,
                            market_at_signal=100.0)
        engine, handed = _pending(tmp_path, idea, 97.5)
        assert _confirm(engine, idea).startswith("Trade REJECTED: the market moved 2.5%")
        assert handed == []

    @pytest.mark.parametrize("over", [
        {"source": "scan_skill"}, {"entry_typed": True}, {"source": "manual"},
        {"market_at_signal": None},
    ], ids=["scan-card", "entry-typed", "manual", "not-recorded"])
    def test_levels_a_person_chose_or_no_recorded_market_are_not_refused(self, tmp_path, audits, over):
        base = dict(entry_price=97.2, stop_loss=93.0, take_profit=105.0, market_at_signal=100.0)
        base.update(over)
        idea = _engine_idea(**base)
        engine, handed = _pending(tmp_path, idea, 104.0)
        assert not _confirm(engine, idea).startswith("Trade REJECTED: the market moved")
        assert _by(audits, "price_drift") == []

    def test_a_market_idea_keeps_its_own_rule(self, tmp_path, audits):
        idea = _engine_idea(entry_price=100.0, stop_loss=96.0, take_profit=108.0,
                            order_type="market", market_at_signal=100.0)
        engine, handed = _pending(tmp_path, idea, 104.0)
        answer = _confirm(engine, idea)
        assert answer.startswith("Trade REJECTED: price drifted 4.0% from the analysed entry")
        assert "$100.00 → $104.00, the perp this order is placed on" in answer
        assert handed == []

    def test_a_sub_cent_drift_is_said_in_its_own_digits(self, tmp_path, audits):
        """It printed two decimals: a coin at a hundred-thousandth of a dollar
        was refused as "$0.00 → $0.00", a drift between two zeros."""
        idea = _engine_idea(entry_price=0.0000125, stop_loss=0.0000120,
                            take_profit=0.0000140, order_type="market",
                            market_at_signal=0.0000125)
        engine, handed = _pending(tmp_path, idea, 0.0000130)
        answer = _confirm(engine, idea)
        assert answer.startswith("Trade REJECTED: price drifted 4.0% from the analysed entry")
        assert "$0.00001250 → $0.00001300" in answer
        assert "$0.00 " not in answer and handed == []

    def test_a_sub_cent_stop_the_perp_is_through_is_said_in_its_own_digits(self, tmp_path, audits):
        idea = _engine_idea(entry_price=0.0000125, stop_loss=0.0000124,
                            take_profit=0.0000140, order_type="market",
                            market_at_signal=0.0000125)
        engine, handed = _pending(tmp_path, idea, 0.0000123)
        answer = _confirm(engine, idea)
        assert answer == ("Trade REJECTED: price $0.00001230 already below SL "
                          "$0.00001240 — would be instantly stopped out.")
        assert handed == []

    def test_a_sub_cent_stop_the_market_ate_into_is_said_in_its_own_digits(self, tmp_path, audits):
        """Inside the drift band and short of the stop, past half its distance."""
        idea = _engine_idea(entry_price=0.0000125, stop_loss=0.0000124,
                            take_profit=0.0000140, order_type="market",
                            market_at_signal=0.0000125)
        engine, handed = _pending(tmp_path, idea, 0.00001244)
        answer = _confirm(engine, idea)
        assert answer.startswith("Trade REJECTED: price moved 60% toward SL")
        assert "($0.00001244 vs entry $0.00001250)" in answer
        assert handed == []


class TestTheAnalyzerRecordsTheMarket:

    def test_the_field_defaults_to_nothing(self):
        idea = TradeIdea(asset="SOL/USDT", direction=Direction.LONG, entry_price=100.0,
                         stop_loss=96.0, take_profit=108.0, confidence=0.7, reasoning="x")
        assert idea.market_at_signal is None

    def test_the_market_is_kept_before_the_limit_shift_moves_entry(self):
        """A scan, stated as one: `analyze` is a 1,400-line coroutine behind a
        thesis model. The claim is an ORDER -- the market is taken from
        `entry` after it is set from the signal and before the limit shift
        rebinds it -- and that the idea is built with it, read as a price."""
        tree = ast.parse(inspect.getsource(analyzer_mod))
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.AsyncFunctionDef) and n.name == "analyze")
        lines = {}
        for node in ast.walk(fn):
            if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                    and isinstance(node.targets[0], ast.Name):
                name, src = node.targets[0].id, ast.unparse(node.value)
                if name == "entry" and src == "signal.price":
                    lines.setdefault("from_signal", node.lineno)
                if name == "market_at_signal" and src == "entry":
                    lines.setdefault("kept", node.lineno)
                if name == "entry" and src == "limit_entry":
                    lines.setdefault("shifted", node.lineno)
        assert lines.keys() == {"from_signal", "kept", "shifted"}, lines
        assert lines["from_signal"] < lines["kept"] < lines["shifted"]
        kw = [k for n in ast.walk(fn) if isinstance(n, ast.Call)
              and ast.unparse(n.func) == "TradeIdea" for k in n.keywords
              if k.arg == "market_at_signal"]
        assert [ast.unparse(k.value) for k in kw] == ["price_on_record(market_at_signal)"]

    def test_the_confirm_rule_reads_the_one_price_reading(self):
        from bot.core.engine import RuneClawEngine
        src = code_only(inspect.getsource(RuneClawEngine._confirm_trade_inner))
        assert 'price_on_record(getattr(idea, "market_at_signal", None))' in src


def test_no_reader_measures_drift_from_the_limit_itself():
    """The two lifecycle readers ask the one reading; neither keeps its own
    `abs(... - limit) / limit` copy, which is the defect."""
    ex_src = code_only(inspect.getsource(LiveExecutor._check_pending_limit))
    bt_src = code_only(inspect.getsource(BacktestEngine._drain_pending_limits))
    for src in (ex_src, bt_src):
        assert "resting_limit_drift(" in src
    assert "abs(cur_price - pos.entry_price) / pos.entry_price" not in ex_src
    assert "abs(float(bar.close) - px) / px" not in bt_src

