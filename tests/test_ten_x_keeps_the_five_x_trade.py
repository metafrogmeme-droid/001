"""A 10x venue keeps the 5x trade: the same notional, the same loss at the stop,
half the margin.

The operator's decision of 11 October: the venue standard moves from 5x to
10x, every trade keeps the notional and the dollar risk it had at 5x, and the
stops stay where they were. Every sizing figure in this repo is a margin
written at 5x, so the order is SIZED at `SIZING_LEVERAGE` (5x) exactly as
before and placed at the venue's leverage with the margin that notional needs
there (`bot.core.leverage`). Each test drives one surface in both worlds --
the one-leverage world of 5x (`tests/leverage_worlds.py`), and the shipped
standards (sized at 5x, the venue at 10x) -- and asserts what must match and
what must differ.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from bot.config import CONFIG, RUNTIME
from bot.core import leverage as lv
from bot.core import live_executor as le
from bot.core.engine import RuneClawEngine
from bot.core.leverage import LeveragePair
from bot.core.live_executor import LiveExecutor, LivePosition
from bot.core.margin_clamp import clamp_at_venue
from bot.core.size_trace import reset_size_trace
from bot.risk.portfolio import PortfolioTracker
from bot.risk.risk_engine import RiskEngine
from bot.utils.models import Direction, TradeIdea
from tests.leverage_worlds import one_leverage
from tests.test_an_estimated_entry_says_so import Venue
from tests.test_an_estimated_entry_says_so import _drive as _execute

SPLIT = LeveragePair(5, 10)


@pytest.fixture(autouse=True)
def _no_override():
    old = RUNTIME.leverage_override
    RUNTIME.leverage_override = None
    try:
        assert lv.order_standards(CONFIG.exchange, None) == SPLIT, "the shipped standards"
        yield
    finally:
        RUNTIME.leverage_override = old


# ── the arithmetic ────────────────────────────────────────────────────────

class TestTheVenueFollowsTheSizing:

    def test_the_venue_is_the_same_share_of_its_standard(self):
        assert [lv.venue_leverage(x, SPLIT) for x in (5, 4, 3, 2, 1)] == [10, 8, 6, 4, 2]
        assert lv.venue_leverage(5, LeveragePair(5, 5)) == 5, "one standard: the identity"
        assert lv.venue_leverage(5, LeveragePair(5, 7)) == 7
        assert lv.venue_leverage(4, LeveragePair(5, 7)) == 5, "rounded down: 5.6 -> 5"
        assert lv.venue_leverage(9, SPLIT) == 10, "never above the venue standard"

    @pytest.mark.parametrize("pair", [SPLIT, LeveragePair(5, 7), LeveragePair(3, 10),
                                      LeveragePair(5, 5)], ids=str)
    def test_a_cap_measured_at_the_venue_holds_there_on_the_way_back(self, pair):
        for u in range(1, 21):
            back = lv.sized_for_venue(u, pair)
            assert back >= 1
            assert lv.venue_leverage(back, pair) <= max(u, lv.venue_leverage(1, pair)), (u, back)

    def test_the_venue_never_locks_more_than_the_sized_margin(self):
        for x in range(1, 6):
            assert lv.venue_leverage(x, SPLIT) >= x
        assert lv.venue_margin(100.0, 5, 10) == 50.0
        assert lv.venue_margin(100.0, 4, 8) == 50.0
        assert lv.venue_margin(100.0, 5, 5) == 100.0

    def test_the_sized_standard_is_never_above_the_venues(self):
        with one_leverage(5):
            object.__setattr__(CONFIG.exchange, "default_leverage", 3)
            assert lv.order_standards(CONFIG.exchange, None) == (3, 3)
        assert lv.order_standards(CONFIG.exchange, 3) == (3, 3), "an override under the sizing"
        assert lv.order_standards(CONFIG.exchange, 7) == (5, 7)

    def test_the_stop_cap_unset_is_a_six_percent_stop_at_any_venue(self):
        for pair in (SPLIT, LeveragePair(5, 5), LeveragePair(5, 7)):
            cap = lv.margin_risk_cap_pct(None, pair)
            assert cap / pair.venue == pytest.approx(6.0), (pair, cap)
        assert lv.margin_risk_cap_pct(None, SPLIT) == 60.0
        assert lv.margin_risk_cap_pct(30.0, SPLIT) == 30.0, "set, it is read as written"


# ── the risk gate: one size in both worlds ───────────────────────────────

def _engine(tmp_path):
    return RiskEngine(PortfolioTracker(initial_balance=10_000.0),
                      state_file=str(tmp_path / "risk.json"))


def _idea(stop_pct, entry=100.0):
    return TradeIdea(asset="BTC/USDT", direction=Direction.LONG, entry_price=entry,
                     stop_loss=entry * (1 - stop_pct / 100),
                     take_profit=entry * (1 + 2 * stop_pct / 100),
                     confidence=0.72, reasoning="x", source="scan")


def _mr(check):
    return [ln for ln in check.checks_passed + check.checks_failed
            if ln.startswith("MARGIN_RISK")]


class TestTheGateSizesTheSameTrade:

    @pytest.mark.parametrize("stop_pct", [1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
    def test_the_same_margin_at_the_same_sizing_and_the_same_verdict(self, tmp_path, stop_pct):
        with one_leverage(5):
            at5 = _engine(tmp_path).evaluate(_idea(stop_pct), atr=2.0)
        at10 = _engine(tmp_path).evaluate(_idea(stop_pct), atr=2.0)
        assert at10.verdict == at5.verdict, (at5.checks_failed, at10.checks_failed)
        assert at10.position_size_usd == pytest.approx(at5.position_size_usd)
        assert at10.size_path == at5.size_path
        # The notional and the loss at the stop: the sized margin x 5 in both.
        loss = at10.position_size_usd * 5 * stop_pct / 100
        assert loss <= 10_000 * 0.02 + 0.5

    def test_the_cap_reads_the_venue_and_keeps_the_stops(self, tmp_path):
        """6% is admitted and 7% refused in both worlds; the sentence names
        the venue's leverage and the cap it is measured against."""
        with one_leverage(5):
            ok5 = _engine(tmp_path).evaluate(_idea(6.0), atr=2.0)
            wide5 = _engine(tmp_path).evaluate(_idea(7.0), atr=2.0)
        ok10 = _engine(tmp_path).evaluate(_idea(6.0), atr=2.0)
        wide10 = _engine(tmp_path).evaluate(_idea(7.0), atr=2.0)
        assert _mr(ok5) == ["MARGIN_RISK: 30.0% OK (SL 6.0% × 5x) ≤ 30.0% cap"]
        assert _mr(ok10) == ["MARGIN_RISK: 60.0% OK (SL 6.0% × 10x) ≤ 60.0% cap"]
        assert any(ln.startswith("MARGIN_RISK") for ln in wide5.checks_failed)
        assert any(ln.startswith("MARGIN_RISK") for ln in wide10.checks_failed)

    def test_a_cap_set_by_hand_is_read_at_the_venue(self, tmp_path):
        """MAX_MARGIN_RISK_PCT=30 under a 10x venue is a 3% stop: the operator
        is told so on the card, and the gate does it."""
        object.__setattr__(CONFIG.risk, "max_margin_risk_pct", 30.0)
        try:
            check = _engine(tmp_path).evaluate(_idea(4.0), atr=2.0)
        finally:
            object.__setattr__(CONFIG.risk, "max_margin_risk_pct", None)
        assert any("SL 4.0% × 10x" in ln and "30.0% cap" in ln for ln in check.checks_failed), _mr(check)


# ── the executor: the same order, half the margin ────────────────────────

class TestTheExecutorPlacesTheSizedNotional:

    def test_the_same_quantity_at_the_venues_leverage(self, tmp_path):
        with one_leverage(5):
            v5 = Venue()
            _, pos5, *_ = _execute(v5, tmp_path / "a")
        v10 = Venue()
        _, pos10, *_ = _execute(v10, tmp_path / "b")
        assert v5.orders == v10.orders, "the same quantity in both worlds"
        assert v5.lev == 5 and v10.lev == 10, "set at the venue's leverage"
        assert pos5.leverage == 5 and pos10.leverage == 10
        assert pos10.cost_usd == pytest.approx(pos5.cost_usd / 2), "half the margin"
        assert le.sized_leverage_on_record(pos5) == 5 == le.sized_leverage_on_record(pos10)
        # Read at the sizing, the two books are one book.
        assert le.sized_margin(pos10) == pytest.approx(le.sized_margin(pos5))
        assert le.sized_margin(pos10) == pytest.approx(pos5.cost_usd)

    def test_the_sized_leverage_survives_a_restart(self, tmp_path):
        ex, pos, *_ = _execute(Venue(), tmp_path)
        ex._save_positions()
        fresh = LiveExecutor(state_dir=str(tmp_path))
        restored = fresh._positions["TI-EST"]
        assert le.sized_leverage_on_record(restored) == 5
        assert le.sized_margin(restored) == pytest.approx(le.sized_margin(pos))

    def test_a_users_lower_preference_is_sized_and_placed_at_it(self, tmp_path):
        ex = LiveExecutor(state_dir=str(tmp_path))
        ex._user_leverage_pref = 3
        assert ex._order_leverages("BTC/USDT:USDT") == (3, 3)
        ex._user_leverage_pref = 8
        assert ex._order_leverages("BTC/USDT:USDT") == (5, 8), "sized at 5, the venue at their 8"
        ex._user_leverage_pref = None
        assert ex._order_leverages("BTC/USDT:USDT") == (5, 10)


# ── the book: caps read the sized margin ─────────────────────────────────

def _pos(cost, entry, qty, lev, sized=None, symbol="ETH/USDT"):
    p = LivePosition(trade_id=f"P{cost}", symbol=symbol, direction="LONG",
                     entry_price=entry, quantity=qty, cost_usd=cost,
                     stop_loss=entry * 0.95, take_profit=entry * 1.1, leverage=lev)
    if sized is not None:
        le.note_sized_leverage(p, sized)
    return p


class TestTheBookIsReadAtTheSizing:

    def test_a_ten_x_position_reads_its_notional_over_five(self):
        p = _pos(cost=50.0, entry=4000.0, qty=0.125, lev=10, sized=5)
        assert le.position_size_basis(p) == (50.0, 500.0), "the venue's margin, the notional"
        assert le.sized_margin(p) == 100.0
        (row,) = le.held_rows([p])
        assert row.margin_usd == 100.0 and row.notional_usd == 500.0

    def test_a_position_with_no_sized_leverage_reads_as_it_always_did(self):
        legacy = _pos(cost=100.0, entry=4000.0, qty=0.125, lev=5)
        assert le.sized_margin(legacy) == 100.0
        assert le.committed_sized_margin([legacy]) == le.committed_margin([legacy])

    def test_a_sized_position_whose_notional_is_unread_is_unread(self):
        p = _pos(cost=50.0, entry=4000.0, qty=0.125, lev=10, sized=5)
        p.quantity = 0.0
        assert le.sized_margin(p) is None, "never the venue's smaller figure"
        reading = le.committed_sized_margin([p])
        assert reading.total is None and reading.unread == (le.display_symbol("ETH/USDT"),)

    def test_the_cards_print_the_venues_margin_and_the_caps_read_the_sized(self):
        book = [_pos(cost=50.0, entry=4000.0, qty=0.125, lev=10, sized=5),
                _pos(cost=100.0, entry=4000.0, qty=0.125, lev=5)]
        assert le.committed_margin(book).total == 150.0
        assert le.committed_sized_margin(book).total == 200.0

    def test_the_preflight_cap_counts_the_sized_book(self, tmp_path):
        """$210 held at 10x is $420 at the sizing: a $100 order then breaks
        the $500 total, which the venue's $210 + $100 would have passed. The
        same $210 with no sized leverage on record is a $310 book, and
        passes."""
        assert le.MICRO_MAX_TOTAL_EXPOSURE == 500.0 and le.MICRO_MAX_POSITION_USD >= 100.0
        ex = LiveExecutor(state_dir=str(tmp_path))
        held = _pos(cost=210.0, entry=4000.0, qty=0.525, lev=10, sized=5)
        ex._positions[held.trade_id] = held
        err = ex._preflight_check(100.0, symbol="BTC/USDT")
        assert err is not None and "$520.00" in err, err
        legacy = _pos(cost=210.0, entry=4000.0, qty=0.2625, lev=5)
        ex2 = LiveExecutor(state_dir=str(tmp_path / "b"))
        ex2._positions[legacy.trade_id] = legacy
        assert ex2._preflight_check(100.0, symbol="BTC/USDT") is None


# ── the free margin is compared with what the venue locks ─────────────────

class TestTheClampReadsTheVenuesMargin:

    def test_a_sized_margin_that_fits_at_the_venue_passes_untouched(self):
        assert clamp_at_venue(100.0, {"free": 60.0}, 5, 10) == (100.0, None)

    def test_a_clamp_comes_back_in_the_sized_unit(self):
        assert clamp_at_venue(100.0, {"free": 30.0}, 5, 10) == (60.0, "clamped")

    def test_one_standard_is_the_plain_clamp(self):
        assert clamp_at_venue(100.0, {"free": 60.0}, 5, 5) == (60.0, "clamped")
        assert clamp_at_venue(100.0, {"total": 500.0}, 5, 10) == (None, "unreadable")


# ── the practice book is held at the sizing ──────────────────────────────

class _PaperEng:
    _simulate_paper_fill = RuneClawEngine._simulate_paper_fill
    _drop_pending_idea = RuneClawEngine._drop_pending_idea
    _high_conviction_margin = RuneClawEngine._high_conviction_margin
    _high_conviction_ceiling = RuneClawEngine._high_conviction_ceiling

    def __init__(self):
        self._pending_ideas = {}
        self._book = PortfolioTracker(initial_balance=10_000.0)
        self.user_portfolios = SimpleNamespace(get=lambda uid: self._book)
        self.learning = SimpleNamespace(log_decision=lambda **k: None)

    def _per_user_margin_cap(self, uid):
        return None

    def _self_admitted_practice(self, user_id):
        return False

    def _transition(self, *a, **k):
        pass


def test_a_practice_fill_is_the_live_notional_at_the_sizing_leverage():
    eng = _PaperEng()
    idea = TradeIdea(asset="BTC/USDT", direction=Direction.LONG, entry_price=100.0,
                     stop_loss=97.0, take_profit=106.0, confidence=0.8, reasoning="x")
    reset_size_trace(idea)
    card = asyncio.run(eng._simulate_paper_fill(
        idea, SimpleNamespace(position_size_usd=500.0, checks_passed=[]), "u1", idea.id))
    assert "@ 5x" in card and "@ 10x" not in card, card
    (trade,) = eng._book.open_positions
    assert trade.leverage == 5
    assert trade.entry_price * trade.quantity == pytest.approx(2_500.0, rel=1e-3), \
        "the $500 sized margin's notional, not twice it"


# ── the analyzer's stop cap, the round-up, the reduction ─────────────────

def test_the_analyzer_allows_the_same_stops_at_ten_x():
    """6% stands and 8% is pulled in to 6% in both worlds, with the target
    keeping the reward:risk; at a hand-set 30% under the 10x venue, 3%."""
    from bot.core.analyzer import leverage_capped_levels
    for world in (lambda: one_leverage(5), None):
        ctx = world() if world else None
        if ctx:
            ctx.__enter__()
        try:
            assert leverage_capped_levels(Direction.LONG, 100.0, 94.0, 112.0)[:2] == (94.0, 112.0)
            sl, tp, line = leverage_capped_levels(Direction.LONG, 100.0, 92.0, 116.0)
            assert (sl, tp) == pytest.approx((94.0, 112.0)) and line, line
            sl, tp, _ = leverage_capped_levels(Direction.SHORT, 100.0, 108.0, 84.0)
            assert (sl, tp) == pytest.approx((106.0, 88.0))
        finally:
            if ctx:
                ctx.__exit__(None, None, None)
    object.__setattr__(CONFIG.risk, "max_margin_risk_pct", 30.0)
    try:
        sl, _, _ = leverage_capped_levels(Direction.LONG, 100.0, 94.0, 112.0)
    finally:
        object.__setattr__(CONFIG.risk, "max_margin_risk_pct", None)
    assert sl == pytest.approx(97.0), "30% of a 10x venue's margin is a 3% stop"


def test_a_round_up_is_asked_against_the_sized_book(tmp_path):
    """$240 held at 10x is $480 at the sizing. $20 sized rounds up to $25:
    $505 against the $500 total, refused, and said in the sizing unit with
    the venue's figure beside it. Read at the venue's margin it was $265."""
    from unittest.mock import patch

    from bot.core import bounds_shadow
    from tests.test_the_placed_order_is_the_checked_order import _idea as _placed_idea
    from tests.test_the_placed_order_is_the_checked_order import _market, _Sent
    from tests.test_the_placed_order_is_the_checked_order import _Venue as _PlacedVenue

    def drive(held):
        venue = _PlacedVenue(_market(amount_step=0.05, amount_min=0.05), price=2500.0)
        ex = LiveExecutor(state_dir=str(tmp_path / str(len(held.__dict__))))
        ex._exchange = venue
        ex._positions[held.trade_id] = held
        with patch.object(le, "audit", lambda *a, **k: None), \
             patch.object(bounds_shadow.BOUNDS_LEDGER, "record", lambda *a, **k: None), \
             patch.object(type(CONFIG), "is_live", return_value=True):
            try:
                result = asyncio.run(ex.execute(_placed_idea(2500.0), size_usd=20.0,
                                                order_type="market"))
            except _Sent:
                result = "<sent>"
        return result, venue.orders

    result, orders = drive(_pos(cost=240.0, entry=100_000.0, qty=0.024, lev=10, sized=5,
                                symbol="BTC/USDT"))
    assert orders == [], result
    assert ("rounded up to the venue minimum is $25.00 of margin (approved $20.00; "
            "$12.50 at the 10x venue)") in result, result
    assert "$505.00" in result, result
    # The same venue margin with no sized leverage on record is a $240 book.
    result, orders = drive(_pos(cost=240.0, entry=100_000.0, qty=0.012, lev=5,
                                symbol="BTC/USDT"))
    assert result == "<sent>" and orders == [{"amount": 0.05, "leverage": 10}], result


def test_a_reduction_measured_at_the_venue_is_carried_as_a_sized_leverage(tmp_path):
    """Dynamic leverage on, a 7% stop: the venue's 10x takes 70% of its
    margin, over the 60% cap. The cap allows 8x at the venue; the idea
    carries the SIZED leverage whose venue leverage is at most that, 4x, so
    the executor sizes at 4 and sets the venue to 8 -- 56% of its margin."""
    object.__setattr__(CONFIG.exchange, "dynamic_leverage_enabled", True)
    try:
        idea = _idea(7.0)
        check = _engine(tmp_path).evaluate(idea, atr=2.0)
    finally:
        object.__setattr__(CONFIG.exchange, "dynamic_leverage_enabled", False)
    assert _mr(check) == ["MARGIN_RISK: reduced leverage 10x→8x (SL 7.0% × 8x = 56.0% ≤ 60.0%)"]
    assert getattr(idea, lv.RISK_CAP_ATTR) == 4
    ex = LiveExecutor(state_dir=str(tmp_path))
    assert ex._order_leverages("BTC/USDT:USDT", idea) == (4, 8)
