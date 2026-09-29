"""An empty book, an empty trade window and "no whale traded" are not readings.

The order-flow snapshot counted each fetch that ANSWERED as a component that
RESOLVED. An empty order book left the imbalance at its 0.0 default and
still counted as "book". An empty trade window left aggressor 0.5, CVD "flat"
and whale "neutral" and still counted as "trades". A window in which no trade
reached the whale floor left the whale bias at "neutral" and voted it at the
heaviest order-flow weight. Each of those defaults then:

- voted 0.0 in the confluence scorer,
- counted toward the snapshot's confidence, which scales every order-flow
  vote's weight, and
- (the book) halved the opposition the order-flow veto reads.

Driven on the survey's inputs, an empty book beside a falling CVD read as
opposition 0.5 at confidence 0.87 for a LONG. The one reading taken says 1.0,
over 39% of the evidence weight.

An EMPTY book is still a book the venue answered, and the liquidity guard and
the dominance rule refuse it on its zero depth. That half is kept, and pinned
here, because making the book "unread" would turn the fail-OPEN liquidity
guard into a pass for a market with no orders on it.

The smart-money engine built on the snapshot had the same shape one level
up. Its cascade and squeeze detectors answer (0.0, "none") for a funding rate
that was never read, which is what a measured mild rate answers. Its whale
tracker answers 0.0 below three windows, and counted windows whose trades
were never read. All four components counted as resolved whatever they read,
so its confidence was 1.0 over a composite built mostly from defaults.
"""
from __future__ import annotations

import asyncio
import logging

import pytest

from bot.core.analyzer import Analyzer
from bot.core.order_flow import (
    OrderFlowAnalyzer,
    OrderFlowConfig,
    OrderFlowSignal,
    book_imbalance_read,
    whale_flow_read,
)
from bot.core.smart_money import SmartMoneyEngine, WhaleFlowTracker
from bot.utils.models import Direction


class _Venue:
    """A ccxt-shaped stand-in: the four calls analyze() makes."""

    def __init__(self, book, trades, funding=None):
        self._book = book
        self._trades = trades
        self._funding = funding

    async def fetch_order_book(self, symbol, limit=None):
        return self._book

    async def fetch_trades(self, symbol, limit=None):
        return self._trades

    async def fetch_funding_rate(self, symbol):
        if self._funding is None:
            raise RuntimeError("spot symbol")
        return {"fundingRate": self._funding}

    async def fetch_open_interest(self, symbol):
        raise RuntimeError("no oi")


BOOK = {"bids": [[99.9, 50.0], [99.8, 40.0]],
        "asks": [[100.1, 45.0], [100.2, 30.0]]}
EMPTY = {"bids": [], "asks": []}


def _trades(n=40, side="sell", amount=5.0, price=100.0, t0=0):
    """Retail-sized trades: $500 each, far under the $25k whale floor."""
    return [{"timestamp": t0 + i, "price": price, "amount": amount,
             "cost": price * amount, "side": side} for i in range(n)]


def _whale_trades():
    """Retail tape plus whales that traded both sides evenly."""
    tape = _trades(30, side="buy", t0=0)
    tape += [{"timestamp": 100, "price": 100.0, "amount": 400.0,
              "cost": 40_000.0, "side": "buy"},
             {"timestamp": 101, "price": 100.0, "amount": 400.0,
              "cost": 40_000.0, "side": "sell"}]
    return tape


def _analyzer():
    # The cross-venue enrichment asks other venues over the network; nothing
    # here is about it.
    return OrderFlowAnalyzer(OrderFlowConfig(cross_venue_funding=False))


def _read(venue, symbol="ALT/USDT"):
    logging.disable(logging.CRITICAL)
    try:
        return asyncio.run(_analyzer().analyze(venue, symbol))
    finally:
        logging.disable(logging.NOTSET)


def _labels(sig):
    return OrderFlowAnalyzer.to_confluence_votes(sig)[2]


# ── The book ────────────────────────────────────────────────────────────────


class TestAnEmptyBookHasNoImbalance:
    def test_the_venue_answered_so_the_book_is_in_the_components(self):
        sig = _read(_Venue(EMPTY, _trades()))
        assert "book" in sig.components_ok

    def test_but_its_imbalance_is_not_a_reading(self):
        sig = _read(_Venue(EMPTY, _trades()))
        assert not book_imbalance_read(sig)
        assert "of_book_imbalance" not in _labels(sig)

    def test_a_book_with_both_sides_is_a_reading(self):
        sig = _read(_Venue(BOOK, _trades()))
        assert book_imbalance_read(sig)
        assert "of_book_imbalance" in _labels(sig)

    @pytest.mark.parametrize("book", [
        {"bids": [[99.9, 50.0]], "asks": []},
        {"bids": [], "asks": [[100.1, 45.0]]},
    ], ids=["no asks", "no bids"])
    def test_a_one_sided_book_has_no_imbalance_either(self, book):
        sig = _read(_Venue(book, _trades()))
        assert not book_imbalance_read(sig)
        assert "of_book_imbalance" not in _labels(sig)

    def test_a_side_quoted_at_zero_size_is_an_empty_side(self):
        # Both sides answered, so the fill helper measured the book: the bid
        # side holds $0 and the ask side holds $4,500. The -1.0 it computes
        # is a book with no bids, not a reading of sellers outweighing
        # buyers, which is the one-sided case above in another spelling.
        book = {"bids": [[99.9, 0.0]], "asks": [[100.1, 45.0]]}
        sig = _read(_Venue(book, _trades()))
        assert sig.bid_depth_usd == 0 and sig.ask_depth_usd > 0
        assert not book_imbalance_read(sig)
        assert "of_book_imbalance" not in _labels(sig)

    def test_the_liquidity_guard_still_refuses_an_empty_book(self):
        # The guard is fail-OPEN when "book" is missing. An empty book is a
        # book with zero depth, and it must stay a refusal.
        of = _analyzer()
        sig = _read(_Venue(EMPTY, _trades()))
        reason = of.liquidity_guard(sig, position_size_usd=50.0,
                                    symbol="ALT/USDT")
        assert reason and reason.startswith("LIQUIDITY")

    def test_the_dominance_rule_still_fails_an_empty_book(self):
        of = _analyzer()
        sig = _read(_Venue(EMPTY, _trades()))
        assert of.check_bid_dominance(sig, "LONG")["passed"] is False

    def test_the_composite_leaves_the_empty_book_out(self):
        with_book = _read(_Venue(BOOK, _trades()))
        empty = _read(_Venue(EMPTY, _trades()))
        # Weight of every resolved component over the most there can be:
        # the book's weight is in the first and not in the second.
        assert empty.confidence < with_book.confidence

    def test_the_opposition_is_not_halved_by_an_empty_book(self):
        # 40 sells: the one-window CVD reads falling, the aggressor reads all
        # sellers. Only the CVD and the book feed the opposition.
        sig = _read(_Venue(EMPTY, _trades(side="sell")))
        assert sig.cvd_trend == "falling"
        opposition, conf, bias = Analyzer._order_flow_opposition(
            sig, Direction.LONG)
        assert opposition == pytest.approx(1.0)
        assert bias == pytest.approx(-1.0)

    def test_a_read_book_still_enters_the_opposition(self):
        sell_heavy = {"bids": [[99.9, 1.0]], "asks": [[100.1, 90.0]]}
        sig = _read(_Venue(sell_heavy, _trades(side="buy")))
        assert sig.cvd_trend == "rising"
        opposition, _, bias = Analyzer._order_flow_opposition(
            sig, Direction.LONG)
        # book ~ -0.98 and CVD +1 average to a small positive bias.
        assert 0.0 < bias < 0.05
        assert opposition == 0.0


# ── The trade window ────────────────────────────────────────────────────────


class TestAnEmptyTradeWindowIsNotAReading:
    def test_no_trades_leave_trades_out_of_the_components(self):
        sig = _read(_Venue(BOOK, []))
        assert "trades" not in sig.components_ok
        labels = _labels(sig)
        assert "of_cvd_trend" not in labels
        assert "of_whale_bias" not in labels

    def test_trades_with_no_readable_side_or_size_are_not_a_reading(self):
        # No side, one price throughout: the tick rule cannot infer a side.
        flat = [{"timestamp": i, "price": 100.0, "amount": 5.0,
                 "cost": 500.0, "side": None} for i in range(20)]
        sig = _read(_Venue(BOOK, flat))
        assert "trades" not in sig.components_ok
        assert "no trade with a readable side and size" in sig.notes

    def test_an_unread_window_records_nothing_in_the_histories(self):
        of = _analyzer()
        flat = [{"timestamp": i, "price": 100.0, "amount": 5.0,
                 "cost": 500.0, "side": None} for i in range(20)]
        sig = OrderFlowSignal(symbol="ALT/USDT")
        assert of._fill_trade_metrics(sig, flat, "ALT/USDT") is False
        assert of._fill_trade_metrics(sig, [], "ALT/USDT") is False
        # A 1.0 taker bar, a 0 CVD delta and a $0 spot volume would be three
        # readings nobody took.
        assert not of._taker_bar_ratios.get("ALT/USDT")
        assert not of._cvd_history.get("ALT/USDT")
        assert not of._spot_vol_history.get("ALT/USDT")

    def test_a_read_window_says_so(self):
        of = _analyzer()
        sig = OrderFlowSignal(symbol="ALT/USDT")
        assert of._fill_trade_metrics(sig, _trades(), "ALT/USDT") is True
        assert len(of._cvd_history["ALT/USDT"]) == 1

    def test_the_composite_leaves_the_empty_window_out(self):
        read = _read(_Venue(BOOK, _trades()))
        empty = _read(_Venue(BOOK, []))
        assert empty.confidence < read.confidence


# ── The whales ──────────────────────────────────────────────────────────────


class TestNoWhaleTradedIsNotAWhaleReading:
    def test_a_retail_tape_has_no_whale_vote(self):
        sig = _read(_Venue(BOOK, _trades()))
        assert sig.whale_trade_count == 0
        assert "trades" in sig.components_ok
        assert not whale_flow_read(sig)
        assert "of_whale_bias" not in _labels(sig)

    def test_whales_that_traded_both_sides_evenly_are_a_neutral_reading(self):
        sig = _read(_Venue(BOOK, _whale_trades()))
        assert sig.whale_trade_count >= 2
        assert sig.whale_bias == "neutral"
        assert whale_flow_read(sig)
        votes, _, labels = OrderFlowAnalyzer.to_confluence_votes(sig)
        assert votes[labels.index("of_whale_bias")] == 0.0

    def test_the_composite_leaves_no_whale_out(self):
        quiet = _read(_Venue(BOOK, _trades(side="buy")))
        whales = _read(_Venue(BOOK, _whale_trades()))
        # Same book, same side: the whale weight is in the second only.
        assert quiet.confidence < whales.confidence


class TestNothingResolvedCastsNoVote:
    def test_a_snapshot_with_no_resolved_component_votes_nothing(self):
        sig = OrderFlowSignal(symbol="ALT/USDT", confidence=0.0)
        assert OrderFlowAnalyzer.to_confluence_votes(sig) == ([], [], [])

    def test_a_snapshot_that_read_everything_still_votes(self):
        sig = _read(_Venue(BOOK, _whale_trades(), funding=0.0001))
        labels = _labels(sig)
        for lab in ("of_book_imbalance", "of_cvd_trend", "of_whale_bias",
                    "of_funding"):
            assert lab in labels


class TestARecordedSnapshotKeepsItsReading:
    def test_the_readings_ask_fields_a_recorded_snapshot_carries(self):
        # The backtest replays recorded snapshots (recorded_order_flow). The
        # readings key on model fields, so a replayed empty book is still
        # unread and a replayed whale is still read.
        empty = _read(_Venue(EMPTY, _trades()))
        whales = _read(_Venue(BOOK, _whale_trades()))
        again_empty = OrderFlowSignal(**empty.model_dump(mode="json"))
        again_whales = OrderFlowSignal(**whales.model_dump(mode="json"))
        assert not book_imbalance_read(again_empty)
        assert whale_flow_read(again_whales)


# ── Smart money ─────────────────────────────────────────────────────────────


def _of(symbol="SOL/USDT", funding=None, buy=0.0, sell=0.0,
        comps=("trades",)):
    return OrderFlowSignal(symbol=symbol, smart_money_score=0.8,
                           confidence=0.6, funding_rate=funding,
                           whale_buy_usd=buy, whale_sell_usd=sell,
                           components_ok=list(comps))


class TestSmartMoneyCountsOnlyWhatItRead:
    def test_an_unread_funding_rate_resolves_neither_funding_component(self):
        score = SmartMoneyEngine().analyze(_of(funding=None))
        # Institutional read; cascade and squeeze had no funding to read;
        # whales have one window.
        assert score.components_resolved == 1
        assert score.confidence == 0.25

    def test_a_measured_mild_rate_resolves_both(self):
        score = SmartMoneyEngine().analyze(_of(funding=0.0001))
        assert score.cascade_direction == "none"
        assert score.components_resolved == 3
        assert score.confidence == 0.75

    def test_the_two_are_no_longer_the_same_answer(self):
        unread = SmartMoneyEngine().analyze(_of(funding=None))
        mild = SmartMoneyEngine().analyze(_of(funding=0.0001))
        assert unread.composite_score == mild.composite_score
        assert unread.confidence < mild.confidence
        # The composite vote is weighted by the confidence.
        v_unread = SmartMoneyEngine.to_confluence_votes(unread)
        v_mild = SmartMoneyEngine.to_confluence_votes(mild)
        assert v_unread[1][0] < v_mild[1][0]

    def test_whale_flow_resolves_once_three_windows_hold_a_whale(self):
        eng = SmartMoneyEngine()
        for _ in range(3):
            score = eng.analyze(_of(funding=0.0001, buy=80_000, sell=20_000))
        assert score.components_resolved == 4
        assert score.whale_accumulation > 0

    def test_nothing_read_says_so(self):
        sig = OrderFlowSignal(symbol="SOL/USDT", confidence=0.0)
        score = SmartMoneyEngine().analyze(sig)
        assert score.components_resolved == 0
        assert "could be read" in score.narrative
        assert "No significant" not in score.narrative

    def test_a_read_that_found_nothing_keeps_its_old_sentence(self):
        sig = _of(funding=0.0001)
        sig.smart_money_score = 0.1   # read, and under the narrative's bar
        score = SmartMoneyEngine().analyze(sig)
        assert score.components_resolved > 0
        assert "No significant smart money signals detected" in score.narrative


class TestTheWhaleTrackerReadsOnlyReadWindows:
    def test_below_three_windows_is_no_reading(self):
        tr = WhaleFlowTracker()
        assert tr.evaluate(_of(buy=80_000, sell=20_000)) is None
        assert tr.evaluate(_of(buy=80_000, sell=20_000)) is None
        assert tr.evaluate(_of(buy=80_000, sell=20_000)) is not None

    def test_a_window_whose_trades_were_never_read_is_not_recorded(self):
        tr = WhaleFlowTracker()
        for _ in range(5):
            tr.evaluate(_of(comps=()))
        assert not tr._whale_history.get("SOL/USDT")

    def test_a_read_window_with_no_whale_is_recorded(self):
        tr = WhaleFlowTracker()
        for _ in range(3):
            tr.evaluate(_of(comps=("trades",)))
        assert len(tr._whale_history["SOL/USDT"]) == 3

    def test_read_windows_with_no_whale_are_no_reading(self):
        tr = WhaleFlowTracker()
        out = [tr.evaluate(_of(comps=("trades",))) for _ in range(4)]
        assert out[-1] is None

    def test_a_whale_figure_is_a_read_by_itself(self):
        # Callers that plant whale figures without the component list (older
        # tests, recorded rows written before components were listed) still
        # count: a figure above zero cannot be a default.
        tr = WhaleFlowTracker()
        for _ in range(3):
            tr.evaluate(_of(buy=50_000, sell=10_000, comps=()))
        assert len(tr._whale_history["SOL/USDT"]) == 3

    def test_quiet_windows_are_not_consistent_selling(self):
        # Two buy sessions, two sell sessions, six quiet read sessions. The
        # net bias is +0.2. Counting the quiet ones as "not buying" read a
        # 2-in-10 buy rate as consistent SELLING and amplified the bias by
        # 1.3. Among the sessions where a whale traded it is 50/50.
        tr = WhaleFlowTracker()
        for _ in range(6):
            tr.evaluate(_of(comps=("trades",)))
        for buy, sell in ((80_000, 20_000), (80_000, 20_000),
                          (40_000, 60_000)):
            tr.evaluate(_of(buy=buy, sell=sell))
        out = tr.evaluate(_of(buy=40_000, sell=60_000))
        assert out == pytest.approx(0.2)

    def test_consistent_buying_among_quiet_windows_is_still_amplified(self):
        # Seven quiet read windows, then three buy sessions. Among the
        # sessions where a whale traded, all three bought: that is the
        # consistency the amplifier exists for, and the quiet windows must
        # not dilute it away either.
        tr = WhaleFlowTracker()
        for _ in range(7):
            tr.evaluate(_of(comps=("trades",)))
        out = None
        for _ in range(3):
            out = tr.evaluate(_of(buy=60_000, sell=40_000))
        assert out == pytest.approx(0.26)

    def test_a_tied_session_is_neither_buying_nor_selling(self):
        # Four sessions where whales bought and sold the same, one selling
        # session. The net bias is -0.04. Counting the ties as selling reads
        # five of five as consistent selling and amplifies it.
        tr = WhaleFlowTracker()
        for _ in range(4):
            tr.evaluate(_of(buy=50_000, sell=50_000))
        out = tr.evaluate(_of(buy=40_000, sell=60_000))
        assert out == pytest.approx(-0.04)

    def test_consistent_buying_is_still_amplified(self):
        tr = WhaleFlowTracker()
        out = None
        for _ in range(5):
            out = tr.evaluate(_of(buy=60_000, sell=40_000))
        assert out == pytest.approx(0.26)

    def test_consistent_selling_is_still_amplified(self):
        tr = WhaleFlowTracker()
        out = None
        for _ in range(5):
            out = tr.evaluate(_of(buy=40_000, sell=60_000))
        assert out == pytest.approx(-0.26)

    def test_no_symbol_is_no_reading(self):
        assert WhaleFlowTracker().evaluate(_of(symbol="")) is None
