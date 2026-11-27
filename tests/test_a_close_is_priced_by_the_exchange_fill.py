"""A close is priced by the exchange's own fill, and a ticker estimate says so.

The 249-exit parity audit read 183 exits ticker-priced. Three things in the
close path made a ticker more likely than it had to be, or made the record
unable to say which kind of figure it held:

  * the path for a position that was gone before the bot's close arrived
    asked the venue ONCE and booked a ticker, and no attempt on any path had
    a time limit of its own (`LiveExecutor._close_data_bounded` is the one
    bounded ask now);
  * a close that filled in several legs was priced off its LAST leg, with
    that leg's profit and fee standing for the whole close on the close-side
    match (`close_lookup.fill_legs`);
  * the record carried `fill_source`, a dozen spellings, and no two-word
    answer to "is this figure a verified fill or an estimate"
    (`close_lookup.pnl_source`, written on every closed row and split out on
    the `/parity` card).

And Hold-Time Analytics was fed by the paper book's close loop alone, so in
live mode `/holdtime` never left "insufficient data".

Driven through the real executor against an ``AsyncMock`` venue; nothing here
reaches a network.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import ccxt
import pytest

from bot.backtest import parity
from bot.config import CONFIG
from bot.core import live_executor as live_executor_mod
from bot.core.close_lookup import (
    LOOKUP_TIMED_OUT,
    PNL_EXCHANGE_FILL,
    PNL_TICKER_INFERRED,
    fill_legs,
    pnl_source,
    row_pnl_source,
)
from bot.core.live_executor import LiveExecutor, LivePosition, closed_trade_row
from bot.core.smart_exits import HoldTimeAnalytics
from bot.core.venues import get_venue

UTC = timezone.utc
OPENED = datetime(2026, 11, 20, 6, 0, tzinfo=UTC)
OPENED_MS = int(OPENED.timestamp() * 1000)


@pytest.fixture
def lookup_bounds():
    """Three attempts, no pause between them, a short time limit: the bounds
    are the configuration's, set on the real (frozen) object and restored."""
    cfg = CONFIG.execution
    saved = (cfg.close_fill_lookup_attempts, cfg.close_fill_lookup_timeout_sec,
             cfg.close_fill_lookup_retry_delay_sec)

    def _set(attempts=3, timeout=5.0, delay=0.0):
        object.__setattr__(cfg, "close_fill_lookup_attempts", attempts)
        object.__setattr__(cfg, "close_fill_lookup_timeout_sec", timeout)
        object.__setattr__(cfg, "close_fill_lookup_retry_delay_sec", delay)

    _set()
    try:
        yield _set
    finally:
        _set(*saved)


def _executor(venue: str = "bitget", hedge=None) -> LiveExecutor:
    ex = LiveExecutor()
    ex._exchange = AsyncMock()
    ex._venue = get_venue(venue)
    ex._hedge_mode = hedge
    ex._save_positions = MagicMock()
    ex._save_closed_trades = MagicMock()
    ex._fire_position_closed = MagicMock()
    return ex


def _pos(**kw) -> LivePosition:
    base = dict(
        trade_id="T-1", symbol="BTC/USDT", direction="LONG", entry_price=100_000.0,
        quantity=0.03, cost_usd=300.0, stop_loss=95_000.0, take_profit=110_000.0,
        leverage=10, sl_order_id="sl-1", tp_order_id="tp-1", opened_at=OPENED,
        status="open",
    )
    base.update(kw)
    return LivePosition(**base)


def _history(*rows):
    return {"data": {"list": list(rows)}}


def _row(open_px, close_px, **kw):
    r = {"openAvgPrice": str(open_px), "closeAvgPrice": str(close_px), "pnl": "90.0",
         "netProfit": "86.4", "openFee": "1.8", "closeFee": "1.8", "closeType": "tp"}
    r.update(kw)
    return r


def _fill(order="sl-1", price=95_000.0, amount=0.01, side="sell", profit="0",
          ts=None, **info):
    i = {"profit": profit}
    i.update(info)
    out = {"order": order, "price": price, "side": side,
           "timestamp": OPENED_MS + 60_000 if ts is None else ts, "info": i}
    if amount is not None:
        out["amount"] = amount
    return out


def _plant(ex, *, history=None, history_raises=None, fills=None, orders=None,
           ticker=96_000.0):
    x = ex._exchange
    if history_raises is not None:
        x.privateMixGetV2MixPositionHistoryPosition = AsyncMock(side_effect=history_raises)
    else:
        x.privateMixGetV2MixPositionHistoryPosition = AsyncMock(
            return_value=_history() if history is None else history)
    x.fetch_my_trades = AsyncMock(return_value=list(fills or []))
    x.fetch_closed_orders = AsyncMock(return_value=list(orders or []))
    x.fetch_ticker = AsyncMock(return_value={"last": ticker})
    return x


# ── the two words ────────────────────────────────────────────────────────────

class TestThePnlSourceIsTwoWordsAndAnAbsence:
    @pytest.mark.parametrize("word", [
        "bitget_position_history", "bitget_position_history_local_pnl",
        "exchange_fill", "exchange_fill_sltp", "exchange_fill_sltp_local_pnl",
        "exchange_fill_recent", "exchange_fill_recent_local_pnl", "closed_order",
        "close_order", "close_order_cost", "exchange_fill_recent+exchange_pnl",
        "closed_order+entry_estimated",
    ])
    def test_a_venue_priced_exit_is_an_exchange_fill(self, word):
        assert pnl_source(word, -3.2) == PNL_EXCHANGE_FILL

    @pytest.mark.parametrize("word", [
        "ticker_fallback", "ticker_after_bot_close", "ticker_fallback_after_11_retries",
        "ticker_fallback+entry_estimated",
    ])
    def test_every_ticker_word_is_an_estimate(self, word):
        assert pnl_source(word, 4.0) == PNL_TICKER_INFERRED

    def test_a_measured_break_even_keeps_its_source(self):
        """0.0 is a P&L. Falsiness would read it as no figure at all."""
        assert pnl_source("exchange_fill_sltp", 0.0) == PNL_EXCHANGE_FILL
        assert pnl_source("ticker_fallback", 0.0) == PNL_TICKER_INFERRED

    @pytest.mark.parametrize("word,pnl", [
        ("exchange_fill_sltp", None),               # no P&L on the record
        ("ticker_fallback+entry_unread", None),     # exit read, entry unread: unpriced
        ("unpriced_after_11_retries", None),
        ("unread", None),
        (None, 5.0),                                # a row from before fill_source
        ("", 5.0),
        ("some_word_a_later_build_writes", 5.0),    # unknown is not either word
        ("exchange_fill_sltp", True),               # a bool is not a P&L
        ("exchange_fill_sltp", "5.0"),
    ])
    def test_neither_word_is_claimed_for_a_record_that_cannot_say(self, word, pnl):
        assert pnl_source(word, pnl) is None

    def test_every_source_a_lookup_stage_can_answer_is_an_exchange_fill(self):
        """The stage functions' own ``"source"`` literals, walked, so a stage
        written tomorrow under a new word fails here instead of landing in
        "(source not recorded)" on every card."""
        tree = ast.parse(inspect.getsource(live_executor_mod))
        stages = {"_close_from_history", "_fill_by_order_id", "_fill_by_close_side",
                  "_close_from_orders"}
        words: set[str] = set()
        seen: set[str] = set()
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) or fn.name not in stages:
                continue
            seen.add(fn.name)
            for node in ast.walk(fn):
                if not isinstance(node, ast.Dict):
                    continue
                for key, value in zip(node.keys, node.values):
                    if isinstance(key, ast.Constant) and key.value == "source":
                        words.update(c.value for c in ast.walk(value)
                                     if isinstance(c, ast.Constant) and isinstance(c.value, str))
        assert seen == stages, f"a stage function was renamed: {stages - seen}"
        assert len(words) >= 7, words
        for word in words:
            assert pnl_source(word, 1.0) == PNL_EXCHANGE_FILL, word

    def test_the_writer_records_the_reading_of_its_own_fill_source(self):
        for word, pnl, want in (("bitget_position_history", 86.4, PNL_EXCHANGE_FILL),
                                ("ticker_fallback", -12.0, PNL_TICKER_INFERRED),
                                ("unpriced_after_11_retries", None, None)):
            pos = _pos(status="closed")
            pos.fill_source, pos.pnl_usd = word, pnl
            row = closed_trade_row(pos)
            assert "pnl_source" in row
            assert row["pnl_source"] == want
            # One reading: a reader that derives it from the row agrees with
            # the key the writer stored.
            assert row_pnl_source(row) == row["pnl_source"]

    def test_a_row_from_before_the_key_reads_the_same_way(self):
        old = {"pnl_usd": -4.0, "fill_source": "ticker_fallback_after_11_retries"}
        assert "pnl_source" not in old
        assert row_pnl_source(old) == PNL_TICKER_INFERRED
        assert row_pnl_source({"pnl_usd": 2.0}) is None
        assert row_pnl_source("not a row") is None


# ── a verified exchange fill ─────────────────────────────────────────────────

class TestAVerifiedExchangeFill:
    @pytest.mark.asyncio
    async def test_the_venues_price_fee_and_pnl_are_the_record(self, lookup_bounds):
        ex = _executor()
        x = _plant(ex, history=_history(_row(100_000.0, 103_000.0)))
        pos = _pos()
        msg = await ex._handle_already_closed_position(pos)
        assert msg and pos.status == "closed"
        assert pos.close_price == 103_000.0          # the venue's average close
        assert pos.pnl_usd == pytest.approx(86.4)    # the venue's netProfit
        assert pos.commission == pytest.approx(3.6)  # openFee + closeFee
        row = closed_trade_row(pos)
        assert row["fill_source"] == "bitget_position_history"
        assert row["pnl_source"] == PNL_EXCHANGE_FILL
        assert row["close_lookup"] is None
        x.fetch_ticker.assert_not_called()
        # Priced on the first ask: no retry was spent.
        assert x.privateMixGetV2MixPositionHistoryPosition.await_count == 1

    @pytest.mark.asyncio
    async def test_the_venue_is_asked_again_before_a_ticker_is(self, lookup_bounds):
        """The venue's history does not carry a close the instant the position
        leaves the book. The first ask finds nothing in any stage; the second
        finds the stop's fill. The record is the fill, and no ticker is read.
        This path used to ask once."""
        ex = _executor()
        x = _plant(ex)
        x.fetch_my_trades = AsyncMock(side_effect=[
            [], [],                                    # first ask: with and without `since`
            [_fill(order="sl-1", price=95_000.0, amount=0.03, profit="-150",
                   feeDetail={"totalFee": "-1.71"})],
        ])
        pos = _pos()
        msg = await ex._handle_already_closed_position(pos)
        assert msg and pos.close_price == 95_000.0
        assert pos.fill_source == "exchange_fill_sltp"
        assert closed_trade_row(pos)["pnl_source"] == PNL_EXCHANGE_FILL
        # The cause the first, failed ask left is not carried onto a priced close.
        assert pos.close_lookup is None
        x.fetch_ticker.assert_not_called()

    @pytest.mark.asyncio
    async def test_one_attempt_with_the_bounds_at_one(self, lookup_bounds):
        """The same venue, asked once: the bound is what found the fill above,
        not the fixture. Without the retry the close is a ticker estimate."""
        lookup_bounds(attempts=1)
        ex = _executor()
        x = _plant(ex)
        x.fetch_my_trades = AsyncMock(side_effect=[
            [], [],
            [_fill(order="sl-1", price=95_000.0, amount=0.03, profit="-150")],
        ])
        pos = _pos()
        await ex._handle_already_closed_position(pos)
        assert pos.fill_source == "ticker_fallback"
        assert closed_trade_row(pos)["pnl_source"] == PNL_TICKER_INFERRED


# ── a close that filled in several legs ──────────────────────────────────────

class TestMultiLegPartialFills:
    def test_the_average_is_weighted_by_what_each_leg_filled(self):
        legs = fill_legs([{"price": 100.0, "amount": 1.0}, {"price": 99.0, "amount": 1.0},
                          {"price": 95.0, "amount": 2.0}])
        assert legs is not None and legs.weighted and legs.legs == 3
        assert legs.quantity == pytest.approx(4.0)
        assert legs.price == pytest.approx((100.0 + 99.0 + 95.0 * 2) / 4.0)
        # Not the simple mean (98.0) and not the last leg (95.0).
        assert legs.price != pytest.approx(98.0) and legs.price != pytest.approx(95.0)

    def test_a_leg_that_states_no_amount_is_not_averaged_over(self):
        """An average over the legs that happened to be readable is a partial
        figure. With one amount unread the last leg's price stands, as it did
        before, and the reading says it was not weighted."""
        legs = fill_legs([{"price": 100.0, "amount": 1.0}, {"price": 95.0}])
        assert legs is not None and not legs.weighted
        assert legs.price == 95.0 and legs.quantity is None and legs.legs == 2

    def test_a_last_leg_with_no_price_prices_nothing(self):
        assert fill_legs([{"price": 100.0, "amount": 1.0}, {"price": 0, "amount": 1.0}]) is None
        assert fill_legs([{"price": None}]) is None
        assert fill_legs([]) is None
        assert fill_legs(["junk", 3]) is None

    def test_one_fill_is_its_own_price(self):
        legs = fill_legs([{"price": "95000.5", "amount": "0.03"}])
        assert legs is not None and legs.price == 95_000.5 and legs.legs == 1

    @pytest.mark.asyncio
    async def test_a_stop_filled_in_three_legs_is_booked_at_its_average(self, lookup_bounds):
        """0.01 at 95,000, 0.01 at 94,900 and 0.01 at 94,400: the close price
        is 94,766.67, the profit and the fee are the three legs' sums. The
        last leg alone (94,400) overstated the loss by $11 on this position."""
        ex = _executor()
        x = _plant(ex, fills=[
            _fill(order="sl-1", price=95_000.0, amount=0.01, profit="-50",
                  feeDetail={"totalFee": "-0.57"}),
            _fill(order="sl-1", price=94_900.0, amount=0.01, profit="-51",
                  feeDetail={"totalFee": "-0.57"}),
            _fill(order="sl-1", price=94_400.0, amount=0.01, profit="-56",
                  feeDetail={"totalFee": "-0.57"}),
        ])
        pos = _pos()
        out = await ex._fetch_bitget_close_data(pos)
        vwap = (95_000.0 + 94_900.0 + 94_400.0) / 3.0
        assert out["close_price"] == pytest.approx(vwap)
        assert out["close_price"] != pytest.approx(94_400.0)
        assert out["pnl"] == pytest.approx(-157.0)
        assert out["fees"] == pytest.approx(1.71)
        assert out["legs"] == 3 and out["source"] == "exchange_fill_sltp"
        msg = await ex._handle_already_closed_position(pos)
        assert msg and pos.close_price == pytest.approx(vwap)
        assert closed_trade_row(pos)["pnl_source"] == PNL_EXCHANGE_FILL
        x.fetch_ticker.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_market_close_in_two_legs_sums_both_legs(self, lookup_bounds):
        """A close no order id of ours names (an app close), filled in two
        parts under ONE order id. The last part alone was read: its price as
        the close, its profit as the whole close's, its fee as the whole fee."""
        ex = _executor(hedge=False)
        _plant(ex, fills=[
            _fill(order="app-9", price=101_000.0, amount=0.02, profit="20",
                  feeDetail={"totalFee": "-1.21"}),
            _fill(order="app-9", price=100_400.0, amount=0.01, profit="4",
                  feeDetail={"totalFee": "-0.60"}),
        ])
        pos = _pos(sl_order_id=None, tp_order_id=None)
        out = await ex._fetch_bitget_close_data(pos)
        assert out["source"] == "exchange_fill_recent" and out["legs"] == 2
        assert out["close_price"] == pytest.approx((101_000.0 * 0.02 + 100_400.0 * 0.01) / 0.03)
        assert out["pnl"] == pytest.approx(24.0)      # not the last leg's 4
        assert out["fees"] == pytest.approx(1.81)     # not the last leg's 0.60

    @pytest.mark.asyncio
    async def test_another_orders_fill_is_not_a_leg_of_this_close(self, lookup_bounds):
        """Two close-side orders after the open are two closes (a scale-out
        and the final exit). Only the last order's legs price this one."""
        ex = _executor(hedge=False)
        _plant(ex, fills=[
            _fill(order="scale-out-1", price=102_000.0, amount=0.015, profit="30"),
            _fill(order="app-9", price=100_400.0, amount=0.015, profit="6"),
        ])
        pos = _pos(sl_order_id=None, tp_order_id=None)
        out = await ex._fetch_bitget_close_data(pos)
        assert out["legs"] == 1
        assert out["close_price"] == pytest.approx(100_400.0)
        assert out["pnl"] == pytest.approx(6.0)


# ── the fallback ─────────────────────────────────────────────────────────────

class TestTheTickerIsTheFallbackAndSaysSo:
    @pytest.mark.asyncio
    async def test_every_attempt_fails_then_the_ticker_prices_it(self, lookup_bounds):
        ex = _executor()
        x = _plant(ex, history_raises=ccxt.NetworkError("down"), ticker=96_000.0)
        pos = _pos()
        msg = await ex._handle_already_closed_position(pos)
        assert msg and pos.close_price == 96_000.0
        row = closed_trade_row(pos)
        assert row["fill_source"] == "ticker_fallback"
        assert row["pnl_source"] == PNL_TICKER_INFERRED
        assert row["close_lookup"] == "history raised NetworkError"
        # Bounded: three attempts, each trying the three history windows, and
        # the ticker read once after the last.
        assert x.privateMixGetV2MixPositionHistoryPosition.await_count == 3 * 3
        assert x.fetch_ticker.await_count == 1

    @pytest.mark.asyncio
    async def test_the_number_of_attempts_is_the_configured_bound(self, lookup_bounds):
        lookup_bounds(attempts=2)
        ex = _executor()
        x = _plant(ex, history_raises=ccxt.NetworkError("down"))
        await ex._handle_already_closed_position(_pos())
        assert x.privateMixGetV2MixPositionHistoryPosition.await_count == 2 * 3

    @pytest.mark.asyncio
    async def test_an_attempt_that_hangs_is_cut_off_and_named(self, lookup_bounds):
        """A venue that never answers used to hold the close for as long as
        the client waited, on every read of every stage. Each attempt has its
        own limit, the close is booked off the ticker, and the record's cause
        is the timeout rather than a stage that said nothing."""
        lookup_bounds(attempts=2, timeout=0.05)
        ex = _executor()
        x = _plant(ex)

        async def _never(*_a, **_kw):
            await asyncio.sleep(3600)

        x.privateMixGetV2MixPositionHistoryPosition = AsyncMock(side_effect=_never)
        pos = _pos()
        msg = await asyncio.wait_for(ex._handle_already_closed_position(pos), timeout=5.0)
        assert msg and pos.fill_source == "ticker_fallback"
        assert pos.close_lookup == LOOKUP_TIMED_OUT
        row = closed_trade_row(pos)
        assert row["pnl_source"] == PNL_TICKER_INFERRED
        assert row["close_lookup"] == LOOKUP_TIMED_OUT
        assert x.privateMixGetV2MixPositionHistoryPosition.await_count == 2

    @pytest.mark.asyncio
    async def test_a_dead_ticker_after_a_dead_lookup_books_nothing(self, lookup_bounds):
        """Unreadable is never zero: with no venue stage and no ticker the
        handler prices nothing and says it could not."""
        ex = _executor()
        x = _plant(ex, history_raises=ccxt.NetworkError("down"))
        x.fetch_ticker = AsyncMock(side_effect=ccxt.NetworkError("down"))
        pos = _pos()
        assert await ex._handle_already_closed_position(pos) is None
        assert pos.pnl_usd is None and pos.status != "closed"

    @pytest.mark.asyncio
    async def test_the_bots_own_close_asks_once_when_its_fill_priced_the_exit(
            self, tmp_path, lookup_bounds):
        """The close order's own fill is the exit price; the lookup can only
        add the venue's P&L and funding to it, so a miss there is not retried
        (three more waits on every ordinary close)."""
        ex, lookups = self._own_close(tmp_path, fill_price=97_500.0)
        msg = await ex.close_position("T-1", reason="manual")
        assert msg and ex._closed_trades[-1].fill_source == "exchange_fill"
        assert closed_trade_row(ex._closed_trades[-1])["pnl_source"] == PNL_EXCHANGE_FILL
        assert lookups.await_count == 1

    @pytest.mark.asyncio
    async def test_the_bots_own_close_retries_when_only_a_ticker_priced_it(
            self, tmp_path, lookup_bounds):
        ex, lookups = self._own_close(tmp_path, fill_price=0.0)
        msg = await ex.close_position("T-1", reason="manual")
        closed = ex._closed_trades[-1]
        assert msg and closed.fill_source == "ticker_after_bot_close"
        assert closed_trade_row(closed)["pnl_source"] == PNL_TICKER_INFERRED
        assert lookups.await_count == 3

    @staticmethod
    def _own_close(tmp_path, *, fill_price: float):
        ex = LiveExecutor(state_dir=str(tmp_path))
        pos = _pos()
        ex._positions = {"T-1": pos}
        venue = AsyncMock()
        venue.create_order = AsyncMock(return_value={"id": "CLOSE-1"})
        venue.cancel_order = AsyncMock(return_value={"status": "canceled"})
        venue.fetch_ticker = AsyncMock(return_value={"last": 97_000.0})
        venue.fetch_positions = AsyncMock(return_value=[])
        venue.fetch_my_trades = AsyncMock(return_value=[])
        ex._get_exchange = AsyncMock(return_value=venue)
        ex._verify_position_closed = AsyncMock(return_value={
            "confirmed": True, "fill_price": fill_price, "fill_qty": 0.03, "fees": 0.0,
            "remaining_qty": 0.0, "failure_stage": ""})
        lookups = AsyncMock(return_value=None)
        ex._fetch_bitget_close_data = lookups
        return ex, lookups


# ── the sweep asks within the same bounds ────────────────────────────────────

class TestTheSweepAsksWithinTheSameBounds:
    """The reconcile sweep: a position the record holds and the venue no
    longer does. It asks the venue through the same bounded call, and when no
    attempt prices the close it does NOT book a ticker on that tick: it waits
    for the next one, as before."""

    PRICED = {"close_price": 150.0, "pnl": 100.0, "fees": 1.5, "pnl_is_net": False,
              "fees_cover": "close", "reason": "TP HIT (exchange)",
              "reason_inferred": False, "source": "exchange_fill_sltp"}

    @staticmethod
    def _swept(lookups):
        from tests.test_an_unread_entry_is_an_unpriced_close import (
            _executor as _sweep_executor,
        )
        ex, x = _sweep_executor()
        now = datetime.now(UTC)
        pos = LivePosition(trade_id="T-SW", symbol="SOL/USDT:USDT", direction="LONG",
                           entry_price=140.0, quantity=10.0, cost_usd=280.0,
                           stop_loss=130.0, take_profit=160.0, leverage=5,
                           status="open", opened_at=now - timedelta(hours=3))
        ex._positions = {pos.trade_id: pos}
        ex._fetch_bitget_close_data = lookups
        asyncio.run(ex.reconcile_positions())
        return ex, x, pos

    def test_the_third_ask_prices_the_close_in_the_same_sweep(self, lookup_bounds):
        lookups = AsyncMock(side_effect=[None, None, dict(self.PRICED)])
        _ex, x, pos = self._swept(lookups)
        assert lookups.await_count == 3
        assert pos.status == "closed" and pos.close_price == 150.0
        row = closed_trade_row(pos)
        assert row["fill_source"].startswith("exchange_fill_sltp")
        assert row["pnl_source"] == PNL_EXCHANGE_FILL
        x.fetch_ticker.assert_not_called()

    def test_no_priced_attempt_leaves_the_close_for_the_next_sweep(self, lookup_bounds):
        lookup_bounds(attempts=2)
        lookups = AsyncMock(return_value=None)
        _ex, x, pos = self._swept(lookups)
        assert lookups.await_count == 2, "the configured bound, not the old fixed three"
        assert pos.status == "open" and pos.pnl_usd is None
        assert getattr(pos, "_reconcile_retries", 0) == 1
        x.fetch_ticker.assert_not_called()


# ── /parity keeps the two apart ──────────────────────────────────────────────

def _closed_row(n: int, pnl: float, fill_source, **kw) -> dict:
    row = dict(trade_id=f"T-{n}", symbol="BTC/USDT", direction="LONG",
               entry_price=100.0, quantity=1.0, pnl_usd=pnl, gross_pnl=pnl + 0.2,
               commission=0.2, close_reason="TP HIT (exchange)" if pnl > 0 else "SL HIT (exchange)",
               fill_source=fill_source, signal_type="momentum_confluence",
               strategy_type="swing")
    row.update(kw)
    return row


class TestParityKeepsVerifiedFillsApartFromEstimates:
    def _rows(self):
        # Verified: 3 wins, 1 loss, net +8. Estimated: 1 win, 2 losses, net -7.
        # Asymmetric on purpose: a table over the wrong rows cannot print these.
        verified = [_closed_row(i, p, "bitget_position_history")
                    for i, p in enumerate((4.0, 3.0, 2.0, -1.0))]
        estimated = [_closed_row(10 + i, p, w) for i, (p, w) in enumerate((
            (1.0, "ticker_fallback"), (-3.0, "ticker_fallback_after_11_retries"),
            (-5.0, "ticker_after_bot_close")))]
        return verified, estimated

    def test_each_source_carries_its_own_sample(self):
        verified, estimated = self._rows()
        s = parity.parity_summary(verified + estimated, 0.06)
        by = s["by_pnl_source"]
        assert list(by) == [PNL_EXCHANGE_FILL, PNL_TICKER_INFERRED]
        assert (by[PNL_EXCHANGE_FILL]["trades"], by[PNL_EXCHANGE_FILL]["wins"],
                by[PNL_EXCHANGE_FILL]["losses"]) == (4, 3, 1)
        assert by[PNL_EXCHANGE_FILL]["net"] == pytest.approx(8.0)
        assert (by[PNL_TICKER_INFERRED]["trades"], by[PNL_TICKER_INFERRED]["wins"],
                by[PNL_TICKER_INFERRED]["losses"]) == (3, 1, 2)
        assert by[PNL_TICKER_INFERRED]["net"] == pytest.approx(-7.0)
        # The two rows are the headline's own population, split, not a third count.
        assert by[PNL_EXCHANGE_FILL]["trades"] + by[PNL_TICKER_INFERRED]["trades"] == s["trades"]
        assert s["inferred_fills"] == by[PNL_TICKER_INFERRED]["trades"]

    def test_the_card_prints_both_rows_under_their_own_words(self):
        verified, estimated = self._rows()
        card = parity.format_report(parity.parity_summary(verified + estimated, 0.06))
        lines = card.splitlines()
        head = next(i for i, ln in enumerate(lines) if "By PnL source" in ln)
        assert "verified venue fill" in lines[head] and "estimate" in lines[head]
        fill_line, ticker_line = lines[head + 1], lines[head + 2]
        assert fill_line.split()[0] == PNL_EXCHANGE_FILL and "4 tr" in fill_line
        assert "3W/1L" in fill_line and "8.00" in fill_line
        assert ticker_line.split()[0] == PNL_TICKER_INFERRED and "3 tr" in ticker_line
        assert "1W/2L" in ticker_line and "7.00" in ticker_line

    def test_a_scored_row_with_no_source_on_record_is_counted_apart(self):
        verified, estimated = self._rows()
        legacy = [_closed_row(50, 2.5, None), _closed_row(51, -1.5, "a_word_from_later")]
        by = parity.parity_summary(verified + estimated + legacy, 0.06)["by_pnl_source"]
        assert list(by) == [PNL_EXCHANGE_FILL, PNL_TICKER_INFERRED,
                            parity.PNL_SOURCE_UNRECORDED]
        assert by[parity.PNL_SOURCE_UNRECORDED]["trades"] == 2
        assert by[PNL_EXCHANGE_FILL]["trades"] == 4 and by[PNL_TICKER_INFERRED]["trades"] == 3

    def test_an_execution_abort_is_in_neither_row(self):
        """The by-source split is over strategy exits, the headline's
        population: a post-fill flatten priced by a fill is not a verified
        strategy trade."""
        verified, estimated = self._rows()
        abort = _closed_row(60, -0.4, "exchange_fill", close_reason="leverage_overshoot")
        s = parity.parity_summary(verified + estimated + [abort], 0.06)
        assert s["aborts"]["trades"] == 1
        assert s["by_pnl_source"][PNL_EXCHANGE_FILL]["trades"] == 4

    def test_an_all_verified_record_prints_one_row(self):
        verified, _ = self._rows()
        s = parity.parity_summary(verified, 0.06)
        assert list(s["by_pnl_source"]) == [PNL_EXCHANGE_FILL]
        assert "inferred_sentence" not in s["verdict"]
        assert PNL_TICKER_INFERRED not in "\n".join(
            ln for ln in parity.format_report(s).splitlines() if " tr  net " in ln)


# ── Hold-Time Analytics reads live closes ────────────────────────────────────

def _closed_pos(n: int, *, pnl, hours: float, fill_source="bitget_position_history",
                strategy="swing", reason="TP HIT (exchange)", **kw) -> LivePosition:
    pos = _pos(trade_id=f"H-{n}", status="closed", strategy_type=strategy, quantity=1.0,
               entry_price=100.0, stop_loss=95.0, **kw)
    pos.closed_at = OPENED + timedelta(hours=hours)
    pos.pnl_usd, pos.fill_source, pos.close_reason = pnl, fill_source, reason
    return pos


class TestHoldTimeAnalyticsReadsLiveCloses:
    def test_a_live_close_is_recorded_with_its_hold_r_and_outcome(self):
        a = HoldTimeAnalytics()
        assert a.record_live_close(_closed_pos(1, pnl=10.0, hours=6.0)) is True
        (hold, r, outcome), = a._records["swing"]
        assert hold == pytest.approx(6.0)
        assert r == pytest.approx(2.0)          # $10 over $5 of risk on 1 unit
        assert outcome == "win"

    def test_r_is_read_against_the_stop_the_trade_was_sized_on(self):
        """The position's own stop is moved by the break-even lock and the
        trail; the record keeps the decision's stop beside it."""
        a = HoldTimeAnalytics()
        pos = _closed_pos(1, pnl=10.0, hours=6.0)
        pos.stop_loss = 100.5                                # trailed past the entry
        setattr(pos, "entry_facts", {"v": 1, "initial_stop_loss": 96.0})
        assert a.record_live_close(pos)
        assert a._records["swing"][0][1] == pytest.approx(10.0 / 4.0)

    def test_the_hold_counts_from_the_fill_not_the_placement(self):
        a = HoldTimeAnalytics()
        pos = _closed_pos(1, pnl=-5.0, hours=6.0)
        setattr(pos, "filled_at", OPENED + timedelta(hours=4))   # a limit that rested 4h
        assert a.record_live_close(pos)
        assert a._records["swing"][0][0] == pytest.approx(2.0)

    def test_an_unpriced_close_is_unscored_not_a_loss(self):
        a = HoldTimeAnalytics()
        assert a.record_live_close(_closed_pos(
            1, pnl=None, hours=3.0, fill_source="unpriced_after_11_retries",
            reason="CLOSED (unknown)"))
        hold, r, outcome = a._records["swing"][0]
        assert (r, outcome) == (None, "unscored") and hold == pytest.approx(3.0)

    @pytest.mark.parametrize("kw", [
        dict(reason="leverage_overshoot"),            # a post-fill flatten is not a hold
        dict(reason="sl_placement_failed"),
        dict(reason="expired", pnl=0.0),              # never filled
        dict(hours=0.0),                              # no measured hold
        dict(strategy=""),
    ])
    def test_what_is_not_a_strategy_exit_with_a_hold_is_left_out(self, kw):
        a = HoldTimeAnalytics()
        base = dict(pnl=-0.4, hours=5.0)
        base.update(kw)
        assert a.record_live_close(_closed_pos(1, **base)) is False
        assert not any(a._records.values())

    def test_the_card_populates_and_says_how_many_outcomes_are_estimates(self):
        a = HoldTimeAnalytics()
        closes = [_closed_pos(i, pnl=8.0, hours=5.0 + i) for i in range(7)]
        closes += [_closed_pos(20 + i, pnl=-5.0, hours=2.0, reason="SL HIT (inferred)",
                               fill_source="ticker_fallback") for i in range(4)]
        assert a.seed_from_closed(closes) == 11
        analysis = a.get_analysis("swing")
        assert analysis is not None, "eleven live closes and the card still read insufficient"
        assert (analysis["wins"], analysis["losses"], analysis["estimated"]) == (7, 4, 4)
        summary = a.summary()
        swing = summary[summary.index("SWING"):summary.index("POSITION")]
        assert "insufficient data" not in swing
        assert "4 of 11 outcomes rest on a ticker-estimated exit" in swing

    def test_a_fully_verified_record_carries_no_estimate_line(self):
        a = HoldTimeAnalytics()
        a.seed_from_closed([_closed_pos(i, pnl=8.0, hours=5.0) for i in range(6)]
                           + [_closed_pos(20 + i, pnl=-5.0, hours=2.0) for i in range(5)])
        assert a.get_analysis("swing")["estimated"] == 0
        assert "ticker-estimated" not in a.summary()

    def test_the_paper_feed_is_unchanged(self):
        """`record` keeps its four-argument call; an outcome is an estimate
        only when a caller says so."""
        a = HoldTimeAnalytics()
        a.record("scalp", 1.0, 1.2, 4.0)
        assert a._records["scalp"] == [(1.0, 1.2, "win")]
        assert a._estimated["scalp"] == [False]

    def test_the_engine_feeds_the_operators_live_closes_and_only_those(self):
        """`_on_live_position_closed` is where a live close reaches the
        collector, driven through the real method. A per-user account's close
        does not reach it: the card is the operator's."""
        from bot.core.engine import RuneClawEngine
        from tests.test_journal_records_live_closes import _engine_stub

        eng = _engine_stub()
        eng.hold_analytics = HoldTimeAnalytics()
        RuneClawEngine._on_live_position_closed(eng, _closed_pos(1, pnl=10.0, hours=6.0))
        assert len(eng.hold_analytics._records["swing"]) == 1
        RuneClawEngine._on_live_position_closed(
            eng, _closed_pos(2, pnl=-5.0, hours=2.0), "alice")
        assert len(eng.hold_analytics._records["swing"]) == 1, (
            "a per-user close reached the operator's hold-time card")
        RuneClawEngine._on_live_position_closed(eng, _closed_pos(3, pnl=-5.0, hours=2.0), "")
        assert len(eng.hold_analytics._records["swing"]) == 2
