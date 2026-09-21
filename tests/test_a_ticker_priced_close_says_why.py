"""A ticker-priced close says WHY, keeps a fill the venue priced, and reads
every venue -- the 2026-09-21 parity card's "175 close(s) inferred".

Driven, not read. The venue lookup (``LiveExecutor._fetch_bitget_close_data``)
is planted stage by stage through an ``AsyncMock`` exchange and its answers are
read off the position, the record row, the log and the parity card:

  * every stage that did not price the close is CLASSIFIED -- raised (the
    exception's CLASS, never its text), answered rows that matched nothing
    (with the nearest entry gap), answered no rows, skipped and why -- said
    ONCE per position, and stamped on the record so the card can count causes;
  * a matched fill's PRICE is kept when the venue's per-fill profit reads 0,
    with the P&L derived locally and a source word that says so; the ticker
    never beats a read fill;
  * the two generic stages run for every ccxt venue -- Bitget's history
    endpoint is the only stage that is Bitget's;
  * an adopted position whose entry price was never stated matches its
    history row by side and time instead of dividing by zero;
  * the three ticker words are one reading, so the sweep's retry-suffixed rows
    count as inferred like the others.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock

import ccxt
import pytest

from bot.backtest import parity
from bot.core.close_lookup import (
    UNRECORDED,
    is_ticker_priced,
    lookup_class,
    lookup_no_rows,
    lookup_raised,
    lookup_sentence,
    lookup_skipped,
    lookup_unmatched,
    nearest_entry_gap_pct,
    stage_summary,
)
from bot.core.live_executor import LiveExecutor, LivePosition, closed_trade_row
from bot.core.venues import get_venue

UTC = timezone.utc
OPENED = datetime(2026, 9, 21, 6, 0, tzinfo=UTC)
OPENED_MS = int(OPENED.timestamp() * 1000)
SECRET = "sign=SECRETSIGNATURE0123"


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
        quantity=0.01, cost_usd=100.0, stop_loss=95_000.0, take_profit=110_000.0,
        leverage=10, sl_order_id="sl-1", tp_order_id="tp-1", opened_at=OPENED,
        status="open",
    )
    base.update(kw)
    return LivePosition(**base)


def _history(*rows):
    return {"data": {"list": list(rows)}}


def _row(open_px, close_px, **kw):
    r = {"openAvgPrice": str(open_px), "closeAvgPrice": str(close_px), "pnl": "5.0",
         "netProfit": "4.4", "openFee": "0.3", "closeFee": "0.3", "closeType": "sl"}
    r.update(kw)
    return r


def _fill(order="sl-1", price=95_000.0, side="sell", profit="0", ts=None, **info):
    i = {"profit": profit}
    i.update(info)
    return {"order": order, "price": price, "side": side,
            "timestamp": OPENED_MS + 60_000 if ts is None else ts, "info": i}


def _plant(ex, *, history=None, history_raises=None, fills=None, fills_raises=None,
           orders=None, orders_raises=None):
    x = ex._exchange
    if history_raises is not None:
        x.privateMixGetV2MixPositionHistoryPosition = AsyncMock(side_effect=history_raises)
    else:
        x.privateMixGetV2MixPositionHistoryPosition = AsyncMock(
            return_value=_history() if history is None else history)
    if fills_raises is not None:
        x.fetch_my_trades = AsyncMock(side_effect=fills_raises)
    else:
        x.fetch_my_trades = AsyncMock(return_value=list(fills or []))
    if orders_raises is not None:
        x.fetch_closed_orders = AsyncMock(side_effect=orders_raises)
    else:
        x.fetch_closed_orders = AsyncMock(return_value=list(orders or []))
    x.fetch_ticker = AsyncMock(return_value={"last": 96_000.0})
    return x


def _warnings(caplog):
    return [r for r in caplog.records
            if r.levelno == logging.WARNING and "No venue close data" in r.getMessage()]


# ── the leaf ─────────────────────────────────────────────────────────────────

class TestTheLeaf:
    def test_the_class_is_the_first_stage_that_was_asked(self):
        o = [lookup_raised("history", ccxt.NetworkError("x")),
             lookup_unmatched("fills", 12, 1.83), lookup_skipped("orders", "no ids")]
        assert lookup_class(o) == "history raised NetworkError"
        o = [lookup_skipped("history", "bybit has no channel"),
             lookup_unmatched("fills", 3), lookup_no_rows("orders")]
        assert lookup_class(o) == "fills unmatched"
        assert lookup_class([lookup_skipped("history", "a"), lookup_no_rows("orders")]) == "orders no_rows"

    def test_no_stage_asked_is_skipped_and_nothing_recorded_is_unrecorded(self):
        assert lookup_class([]) == UNRECORDED
        assert lookup_class([lookup_skipped("history", "a"), lookup_skipped("fills", "b"),
                             lookup_skipped("orders", "c")]) == "skipped"

    def test_a_raise_in_any_attempt_outranks_the_attempts_that_answered(self):
        o = [lookup_no_rows("history"), lookup_no_rows("history"),
             lookup_raised("history", TimeoutError("t"))]
        s = stage_summary("history", o)
        assert s.kind == "raised" and s.detail.startswith("TimeoutError")
        assert "1 of 3 attempts" in s.detail

    def test_the_sentence_names_every_stage_and_never_the_exception_text(self):
        o = [lookup_raised("history", ccxt.NetworkError(SECRET)),
             lookup_unmatched("fills", 12, 1.83, "no fill carries the stop/target order id"),
             lookup_skipped("orders", "no stop/target order ids on record")]
        s = lookup_sentence("BTC/USDT", o)
        assert s.startswith("No venue close data for BTC/USDT")
        assert "history: raised NetworkError" in s
        assert "fills: answered 12 rows, none matched; nearest entry gap 1.83%" in s
        assert "orders: skipped (no stop/target order ids on record)" in s
        assert "SECRET" not in s and "sign=" not in s

    def test_a_stage_never_attempted_is_said_as_such(self):
        assert "orders: not attempted" in lookup_sentence("X", [lookup_no_rows("history")])

    def test_the_nearest_gap_is_a_percent_of_our_entry_and_none_without_one(self):
        assert nearest_entry_gap_pct([100.6, 99.0], 100.0) == pytest.approx(0.6)
        assert nearest_entry_gap_pct([], 100.0) is None
        assert nearest_entry_gap_pct([5.0], 0.0) is None
        assert nearest_entry_gap_pct(["junk", None, 101.0], 100.0) == pytest.approx(1.0)

    def test_every_ticker_word_is_ticker_priced_and_no_venue_word_is(self):
        for w in ("ticker_fallback", "ticker_fallback_after_3_retries", "ticker_after_bot_close"):
            assert is_ticker_priced(w), w
        for w in ("bitget_position_history", "exchange_fill_sltp", "exchange_fill_sltp_local_pnl",
                  "exchange_fill_recent", "closed_order", "unread", None, ""):
            assert not is_ticker_priced(w), w


# ── the lookup says why ──────────────────────────────────────────────────────

class TestTheLookupSaysWhy:
    @pytest.mark.asyncio
    async def test_a_raising_endpoint_is_named_by_class_never_by_text(self, caplog):
        ex = _executor()
        _plant(ex, history_raises=ccxt.NetworkError(f"GET https://api.bitget.com/x?{SECRET}"))
        pos = _pos()
        with caplog.at_level(logging.DEBUG):
            assert await ex._fetch_bitget_close_data(pos) is None
        assert pos.close_lookup == "history raised NetworkError"
        w = _warnings(caplog)
        assert len(w) == 1
        msg = w[0].getMessage()
        assert "history: raised NetworkError (3 of 3 attempts)" in msg
        assert "fills: answered no rows" in msg and "orders: answered no rows" in msg
        assert "SECRETSIGNATURE" not in msg and "sign=" not in msg

    @pytest.mark.asyncio
    async def test_rows_that_match_nothing_name_the_nearest_gap(self, caplog):
        ex = _executor()
        _plant(ex, history=_history(_row(100_600.0, 101_000.0), _row(90_000.0, 91_000.0)))
        pos = _pos()
        with caplog.at_level(logging.WARNING):
            assert await ex._fetch_bitget_close_data(pos) is None
        assert pos.close_lookup == "history unmatched"
        msg = _warnings(caplog)[0].getMessage()
        assert "history: answered 2 rows, none matched; nearest entry gap 0.60%" in msg

    @pytest.mark.asyncio
    async def test_the_warning_is_said_once_per_position(self, caplog):
        ex = _executor()
        _plant(ex)
        pos = _pos()
        with caplog.at_level(logging.DEBUG):
            for _ in range(3):
                assert await ex._fetch_bitget_close_data(pos) is None
        assert len(_warnings(caplog)) == 1
        repeats = [r for r in caplog.records if r.levelno == logging.DEBUG
                   and "No venue close data" in r.getMessage() and "repeat" in r.getMessage()]
        assert len(repeats) == 2
        # A different position is a different warning.
        other = _pos(trade_id="T-2")
        with caplog.at_level(logging.WARNING):
            assert await ex._fetch_bitget_close_data(other) is None
        assert len(_warnings(caplog)) == 2

    @pytest.mark.asyncio
    async def test_a_priced_close_stamps_no_cause(self):
        ex = _executor()
        _plant(ex, history=_history(_row(100_000.0, 103_000.0)))
        pos = _pos()
        out = await ex._fetch_bitget_close_data(pos)
        assert out["source"] == "bitget_position_history" and out["close_price"] == 103_000.0
        assert pos.close_lookup is None

    @pytest.mark.asyncio
    async def test_the_25227_path_books_the_ticker_with_the_cause_on_the_record(self, caplog):
        ex = _executor()
        _plant(ex, history_raises=ccxt.AuthenticationError("nope"))
        pos = _pos()
        with caplog.at_level(logging.WARNING):
            msg = await ex._handle_already_closed_position(pos)
        assert msg and pos.fill_source == "ticker_fallback"
        assert pos.close_price == 96_000.0
        assert pos.close_lookup == "history raised AuthenticationError"
        assert closed_trade_row(pos)["close_lookup"] == "history raised AuthenticationError"
        ticker_line = [r.getMessage() for r in caplog.records
                       if "Using ticker price" in r.getMessage()]
        assert ticker_line and "history raised AuthenticationError" in ticker_line[0]


# ── a matched fill is kept when its profit is unread ─────────────────────────

class TestAMatchedFillIsKeptWhenProfitIsUnread:
    @pytest.mark.asyncio
    async def test_the_stop_fill_prices_the_close_and_the_pnl_is_derived(self):
        ex = _executor()
        x = _plant(ex, fills=[_fill(order="sl-1", price=95_000.0, profit="0",
                                    feeDetail={"totalFee": "-0.57"})])
        pos = _pos()
        out = await ex._fetch_bitget_close_data(pos)
        assert out["close_price"] == 95_000.0
        assert out["pnl"] is None and out["pnl_is_net"] is False
        assert out["source"] == "exchange_fill_sltp_local_pnl"
        assert out["fees"] == pytest.approx(0.57)
        assert out["reason"] == "SL HIT (exchange)"
        # Booked through the 25227 handler: the venue's price, a local net,
        # and the ticker never asked.
        msg = await ex._handle_already_closed_position(pos)
        assert msg and pos.close_price == 95_000.0
        assert pos.fill_source == "exchange_fill_sltp_local_pnl"
        assert pos.gross_pnl == pytest.approx((95_000.0 - 100_000.0) * 0.01)
        assert pos.pnl_usd is not None and pos.pnl_usd < pos.gross_pnl
        x.fetch_ticker.assert_not_called()
        assert pos.close_lookup is None

    @pytest.mark.asyncio
    async def test_a_stated_profit_still_travels_as_the_venues(self):
        ex = _executor()
        _plant(ex, fills=[_fill(order="tp-1", price=110_000.0, profit="98.5")])
        out = await ex._fetch_bitget_close_data(_pos())
        assert out["pnl"] == pytest.approx(98.5)
        assert out["source"] == "exchange_fill_sltp" and out["reason"] == "TP HIT (exchange)"

    @pytest.mark.asyncio
    async def test_a_close_side_fill_in_one_way_mode_is_kept_without_a_profit(self):
        ex = _executor(hedge=False)
        _plant(ex, fills=[_fill(order="someone-elses-app-order", price=97_000.0, profit="0")])
        pos = _pos(sl_order_id=None, tp_order_id=None)
        out = await ex._fetch_bitget_close_data(pos)
        assert out["close_price"] == 97_000.0 and out["pnl"] is None
        assert out["source"] == "exchange_fill_recent_local_pnl"

    @pytest.mark.asyncio
    async def test_in_hedge_mode_an_unmarked_close_side_fill_is_not_taken(self, caplog):
        for hedge in (True, None):
            ex = _executor(hedge=hedge)
            _plant(ex, fills=[_fill(order="x", price=97_000.0, profit="0")])
            pos = _pos(sl_order_id=None, tp_order_id=None)
            with caplog.at_level(logging.WARNING):
                assert await ex._fetch_bitget_close_data(pos) is None, hedge
            # The class is the first ASKED stage's outcome -- history answered
            # no rows -- and the fills stage's reason is in the sentence.
            assert pos.close_lookup == "history no_rows"
        msg = _warnings(caplog)[0].getMessage()
        assert "fills: answered 1 row, none matched; no stop/target order ids on record; " in msg
        assert "a close-side fill with no stated profit and no close marker" in msg
        assert "not known to be one-way" in msg

    @pytest.mark.asyncio
    async def test_in_hedge_mode_the_venues_own_close_marker_is_enough(self):
        ex = _executor(hedge=True)
        _plant(ex, fills=[_fill(order="x", price=97_000.0, profit="0", tradeSide="close_long")])
        pos = _pos(sl_order_id=None, tp_order_id=None)
        out = await ex._fetch_bitget_close_data(pos)
        assert out["source"] == "exchange_fill_recent_local_pnl"

    @pytest.mark.asyncio
    async def test_a_fill_from_before_the_position_opened_is_never_its_close(self, caplog):
        # The previous position's exit on the same symbol, with a realized
        # profit on it: the last close-side fill used to match with no time
        # check, so it priced THIS close.
        ex = _executor(hedge=False)
        _plant(ex, fills=[_fill(order="old", price=80_000.0, profit="12.0",
                                ts=OPENED_MS - 3_600_000)])
        pos = _pos(sl_order_id=None, tp_order_id=None)
        with caplog.at_level(logging.WARNING):
            assert await ex._fetch_bitget_close_data(pos) is None
        assert pos.close_lookup == "history no_rows"
        msg = _warnings(caplog)[0].getMessage()
        assert "fills: answered 1 row, none matched; " in msg
        assert "no sell fill after the position opened" in msg

    @pytest.mark.asyncio
    async def test_a_fill_with_no_price_does_not_price_anything(self, caplog):
        ex = _executor()
        _plant(ex, fills=[_fill(order="sl-1", price=0, profit="3.0")])
        pos = _pos()
        with caplog.at_level(logging.WARNING):
            assert await ex._fetch_bitget_close_data(pos) is None
        assert pos.close_lookup == "history no_rows"
        assert "the stop/target fill carries no price" in _warnings(caplog)[0].getMessage()


# ── every venue runs the generic stages ──────────────────────────────────────

class TestEveryVenueRunsTheGenericStages:
    @pytest.mark.asyncio
    async def test_bybit_skips_history_and_still_reads_its_fills(self):
        ex = _executor(venue="bybit")
        x = _plant(ex, fills=[_fill(order="sl-1", price=95_000.0, profit="0",
                                    fee={"cost": 0.42, "currency": "USDT"})])
        # ccxt's unified fee, since a non-Bitget fill carries no feeDetail.
        x.fetch_my_trades.return_value[0]["fee"] = {"cost": 0.42, "currency": "USDT"}
        out = await ex._fetch_bitget_close_data(_pos())
        assert out["close_price"] == 95_000.0 and out["source"] == "exchange_fill_sltp_local_pnl"
        assert out["fees"] == pytest.approx(0.42)
        x.privateMixGetV2MixPositionHistoryPosition.assert_not_called()

    @pytest.mark.asyncio
    async def test_bybit_with_nothing_readable_says_history_was_skipped(self, caplog):
        ex = _executor(venue="bybit")
        x = _plant(ex)
        pos = _pos()
        with caplog.at_level(logging.WARNING):
            assert await ex._fetch_bitget_close_data(pos) is None
        msg = _warnings(caplog)[0].getMessage()
        assert "history: skipped (bybit has no position-history channel here)" in msg
        assert pos.close_lookup == "fills no_rows"
        x.privateMixGetV2MixPositionHistoryPosition.assert_not_called()
        x.fetch_my_trades.assert_called()
        x.fetch_closed_orders.assert_called()

    @pytest.mark.asyncio
    async def test_the_closed_order_stage_runs_for_bybit_too(self):
        ex = _executor(venue="bybit")
        _plant(ex, orders=[{"id": "tp-1", "filled": 0.01, "status": "closed",
                            "average": 110_000.0}])
        out = await ex._fetch_bitget_close_data(_pos())
        assert out["source"] == "closed_order" and out["close_price"] == 110_000.0


# ── an adopted position matches on side and time ─────────────────────────────

class TestAnAdoptedPositionMatchesOnSideAndTime:
    @pytest.mark.asyncio
    async def test_no_entry_price_matches_the_earliest_long_closed_after_tracking(self):
        ex = _executor()
        earlier = _row(99_000.0, 99_500.0, holdSide="long", utime=str(OPENED_MS - 7_200_000))
        ours = _row(101_000.0, 103_000.0, holdSide="long", utime=str(OPENED_MS + 3_600_000))
        later = _row(104_000.0, 105_000.0, holdSide="long", utime=str(OPENED_MS + 9_000_000))
        short = _row(101_000.0, 100_000.0, holdSide="short", utime=str(OPENED_MS + 1_800_000))
        _plant(ex, history=_history(later, short, ours, earlier))
        pos = _pos(entry_price=0.0, origin="adopted")
        out = await ex._fetch_bitget_close_data(pos)   # no ZeroDivisionError
        assert out["close_price"] == 103_000.0 and out["source"] == "bitget_position_history"

    @pytest.mark.asyncio
    async def test_rows_with_no_side_cannot_be_matched_and_say_so(self, caplog):
        ex = _executor()
        _plant(ex, history=_history(_row(101_000.0, 103_000.0)))
        pos = _pos(entry_price=0.0)
        with caplog.at_level(logging.WARNING):
            assert await ex._fetch_bitget_close_data(pos) is None
        assert "entry price unread and the rows carry no holdSide" in _warnings(caplog)[0].getMessage()

    @pytest.mark.asyncio
    async def test_no_row_closed_after_tracking_is_unmatched_not_a_guess(self, caplog):
        ex = _executor()
        _plant(ex, history=_history(
            _row(99_000.0, 99_500.0, holdSide="long", utime=str(OPENED_MS - 7_200_000))))
        pos = _pos(entry_price=0.0)
        with caplog.at_level(logging.WARNING):
            assert await ex._fetch_bitget_close_data(pos) is None
        assert "no long row closed after the position was tracked" in _warnings(caplog)[0].getMessage()


# ── the ticker word says which path ──────────────────────────────────────────

class TestTheTickerWordSaysWhichPath:
    @pytest.mark.asyncio
    async def test_the_bots_own_close_with_no_readable_fill_says_so(self, tmp_path):
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
            "confirmed": True, "fill_price": 0.0, "fill_qty": 0.01, "fees": 0.0,
            "remaining_qty": 0.0, "failure_stage": ""})
        ex._fetch_bitget_close_data = AsyncMock(return_value=None)
        msg = await ex.close_position("T-1", reason="manual")
        assert msg and pos.status == "closed"
        assert pos.fill_source == "ticker_after_bot_close"
        assert pos.close_price == 97_000.0

    def test_parity_counts_every_ticker_word_as_inferred(self):
        rows = [dict(symbol="BTC/USDT", pnl_usd=-1.0, close_reason="SL HIT (inferred)",
                     fill_source=w, close_lookup=c)
                for w, c in (("ticker_fallback", "history raised NetworkError"),
                             ("ticker_fallback_after_3_retries", "history raised NetworkError"),
                             ("ticker_after_bot_close", "fills unmatched"),
                             ("exchange_fill_sltp", None))]
        assert sum(1 for r in rows if parity._ticker_priced(r)) == 3
        assert parity.inferred_causes(rows) == {"history raised NetworkError": 2, "fills unmatched": 1}

    def test_a_record_with_no_cause_is_counted_as_unrecorded_not_folded(self):
        rows = [dict(symbol="X", pnl_usd=-1.0, close_reason="r", fill_source="ticker_fallback")]
        assert parity.inferred_causes(rows) == {UNRECORDED: 1}


# ── the record carries the cause and the card counts it ──────────────────────

class TestTheRecordCarriesTheCause:
    def test_the_row_round_trips_the_cause(self, tmp_path):
        ex = LiveExecutor(state_dir=str(tmp_path))
        pos = _pos(status="closed", close_price=96_000.0, pnl_usd=-40.0,
                   fill_source="ticker_fallback", close_lookup="fills unmatched",
                   closed_at=OPENED + timedelta(hours=2))
        assert closed_trade_row(pos)["close_lookup"] == "fills unmatched"
        ex._closed_trades = [pos]
        assert ex._save_closed_trades()
        ex2 = LiveExecutor(state_dir=str(tmp_path))
        ex2._load_closed_trades()
        assert ex2._closed_trades[-1].close_lookup == "fills unmatched"
        # An older row, written before the reading, loads as None -- not "".
        rows = json.loads((tmp_path / "closed_trades.json").read_text())
        del rows[0]["close_lookup"]
        (tmp_path / "closed_trades.json").write_text(json.dumps(rows))
        ex3 = LiveExecutor(state_dir=str(tmp_path))
        ex3._load_closed_trades()
        assert ex3._closed_trades[-1].close_lookup is None

    def _rows(self):
        def t(net, fill, cause=None, reason="SL HIT (inferred)"):
            return {"symbol": "BTC/USDT", "pnl_usd": net, "gross_pnl": net + 0.3,
                    "commission": 0.3, "close_reason": reason, "fill_source": fill,
                    "close_lookup": cause, "strategy_type": "swing",
                    "signal_type": "momentum_confluence", "entry_price": 100.0,
                    "quantity": 1.0, "leverage": 5, "opened_at": OPENED.isoformat(),
                    "closed_at": (OPENED + timedelta(hours=1)).isoformat()}
        # Fresh dicts per row: `[d] * 3` is three references to ONE dict, and
        # the digest test below edits a single row's cause.
        return ([t(-1.0, "ticker_fallback", "history raised NetworkError") for _ in range(4)]
                + [t(2.0, "ticker_fallback_after_3_retries", "fills unmatched") for _ in range(2)]
                + [t(1.5, "ticker_after_bot_close")]
                + [t(3.0, "exchange_fill_sltp") for _ in range(6)])

    def test_the_card_names_the_causes_most_common_first(self):
        s = parity.parity_summary(self._rows(), 0.06)
        v = s["verdict"]
        assert v["inferred"] == 7
        assert v["inferred_causes"] == {"history raised NetworkError": 4,
                                         "fills unmatched": 2, UNRECORDED: 1}
        card = parity.format_report(s)
        assert "7 of 13 strategy exits are ticker-priced (fill_source=ticker_*" in card
        assert ("why the venue lookup priced none of them: history raised NetworkError ×4 · "
                "fills unmatched ×2 · unrecorded ×1") in card

    def test_no_cause_line_over_a_record_with_no_ticker_priced_row(self):
        rows = [r for r in self._rows() if not is_ticker_priced(r["fill_source"])]
        s = parity.parity_summary(rows, 0.06)
        assert "inferred_cause_sentence" not in s["verdict"]
        assert "why the venue lookup" not in parity.format_report(s)

    def test_the_digest_names_the_most_common_cause(self, monkeypatch, tmp_path):
        import bot.core.proactive_monitor as pm
        now = datetime.now(pm.UTC)
        monkeypatch.setenv("PARITY_DIGEST_DOW", str(now.weekday()))
        monkeypatch.setenv("PARITY_DIGEST_HOUR_UTC", "0")
        rows = self._rows()
        # The most common cause carries an exception class name read off a
        # venue driver -- text this process did not write -- so the digest
        # escapes it: planted as markup, it must arrive as text.
        for r in rows:
            if r["close_lookup"] == "history raised NetworkError":
                r["close_lookup"] = "history raised <Fake>Error"
        f = tmp_path / "closed.json"
        f.write_text(json.dumps(rows))
        alerts = pm.ProactiveMonitor(NS(live_executor=NS(_closed_trades_file=str(f))))._check_parity_digest()
        body = alerts[0].body
        assert "⚠ 7 of 13 strategy exits are ticker-priced" in body
        assert "(most often: history raised &lt;Fake&gt;Error ×4)" in body
        assert "<Fake>" not in body
