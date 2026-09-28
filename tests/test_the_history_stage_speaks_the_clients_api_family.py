"""The history stage asks the endpoint family the client speaks, and a
venue-matched close price is never thrown away over an unstated profit.

**Every close on the 2026-09-28 parity card read "history raised
ExchangeError".** The executor builds its Bitget client with
``options["uta"] = True`` (a unified trading account), so every ccxt read it
makes is routed to the ``/api/v3`` family -- driven on the pinned ccxt with the
transport stubbed, ``fetch_my_trades`` goes to ``v3/trade/fills`` and
``fetch_closed_orders`` to ``v3/trade/history-orders`` -- while the history
stage's one RAW call went to the classic ``v2/mix/position/history-position``.
A unified account refuses that endpoint with a code the pinned ccxt maps to
nothing, so it surfaced as the bare ``ExchangeError`` the card counted. The
most authoritative stage was dead on every close, which is also why 107 of the
211 exits read ``CLOSED (unknown)``: the row that names the venue's own close
price was never read. The stage reads the family off the client in hand now
(``client_is_uta``) and asks ``GET /api/v3/position/history-position``, whose
row is spelled into the v2 vocabulary at the boundary (``uta_history_row``)
so the one reader stays one reader.

**The bot's own close threw a venue-matched price away over a secondary
field.** ``_close_position_inner`` took the lookup's find only when the row
STATED a profit; a fills-stage match whose profit Bitget left at "0" (the
ordinary case, per the DOT card) answers ``pnl: None`` beside a real
``close_price``, and the caller took nothing from it -- the ticker read before
the lookup ran stayed on the record, with no cause, because the lookup had
priced it. That is the defect the lookup was cured of, at the lookup's own
caller.

**A row with no cause on record is not a reason.** The card printed
``why the venue lookup priced none of them: unrecorded ×179``, the heaviest
row on the line and the one that names nothing to fix; the digest named it as
"most often". Both count it apart now. And each abort guard's latest firing
is dated, so ``leverage_overshoot 18`` can be read as one incident on record
or as a guard still firing.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import ccxt
import ccxt.async_support as ca
import pytest

from bot.backtest import parity
from bot.core.close_funding import NET_INCLUDES, funding_on_row
from bot.core.close_lookup import (
    UNRECORDED,
    UTA_HISTORY_FIELDS,
    StageOutcome,
    client_is_uta,
    lookup_class,
    lookup_raised,
    lookup_sentence,
    uta_history_row,
)
from bot.core.live_executor import LiveExecutor, LivePosition
from bot.core.venues import get_venue
from tests.test_an_unread_entry_is_an_unpriced_close import (  # noqa: F401
    _executor,
    _no_sleep,
    _pos100,
)
from tests.test_bitget_reads_the_perp import _mk, _q, _stub

UTC = timezone.utc
OPENED = datetime(2026, 9, 27, 6, 0, tzinfo=UTC)
OPENED_MS = int(OPENED.timestamp() * 1000)

#: The docs' own example row (docs/bitget-uta/trade.md, Get Positions History),
#: with a funding figure that is not zero so its placement is a measurement.
UTA_ROW = {
    "positionId": "1111111111111111111", "category": "USDT-FUTURES", "symbol": "BTCUSDT",
    "marginCoin": "USDT", "holdMode": "one_way_mode", "posSide": "long",
    "marginMode": "crossed", "openPriceAvg": "100000", "closePriceAvg": "105000",
    "openTotalPos": "0.01", "closeTotalPos": "0.01",
    "cumRealisedPnl": "50", "netProfit": "48.5", "totalFunding": "-0.27",
    "openFeeTotal": "-0.6", "closeFeeTotal": "-0.63",
    "createdTime": str(OPENED_MS), "updatedTime": str(OPENED_MS + 3_600_000),
}


def _pos(**kw) -> LivePosition:
    base = dict(trade_id="T-1", symbol="BTC/USDT", direction="LONG", entry_price=100_000.0,
                quantity=0.01, cost_usd=100.0, stop_loss=95_000.0, take_profit=110_000.0,
                leverage=10, sl_order_id="sl-1", tp_order_id="tp-1", opened_at=OPENED,
                status="open")
    base.update(kw)
    return LivePosition(**base)


def _mock_executor(uta: bool, rows=None, raises=None) -> tuple[LiveExecutor, AsyncMock]:
    """An executor over an AsyncMock client that says which family it speaks."""
    ex = LiveExecutor()
    ex._venue = get_venue("bitget")
    ex._hedge_mode = False
    x = AsyncMock()
    x.options = {"uta": uta}
    answer = {"data": {"list": list(rows or [])}}
    for name in ("privateUtaGetV3PositionHistoryPosition",
                 "privateMixGetV2MixPositionHistoryPosition"):
        setattr(x, name, AsyncMock(side_effect=raises) if raises is not None
                else AsyncMock(return_value=answer))
    x.fetch_my_trades = AsyncMock(return_value=[])
    x.fetch_closed_orders = AsyncMock(return_value=[])
    ex._exchange = x
    return ex, x


def _warning(caplog):
    return [r.getMessage() for r in caplog.records
            if r.levelno == logging.WARNING and "No venue close data" in r.getMessage()]


# ── the leaf ─────────────────────────────────────────────────────────────────

class TestTheLeaf:
    def test_the_family_is_read_off_the_clients_own_options(self):
        class C:
            options = {"uta": True}
        assert client_is_uta(C()) is True
        C.options = {"uta": False}
        assert not client_is_uta(C())
        C.options = {}
        assert not client_is_uta(C())
        C.options = {"uta": 1}
        assert not client_is_uta(C()), "the literal True, never truthiness"

    def test_a_stand_in_whose_options_are_not_a_dict_is_the_classic_client(self):
        assert not client_is_uta(AsyncMock()), (
            "an AsyncMock answers a Mock for every attribute, and a Mock is truthy")
        assert not client_is_uta(object())

    def test_a_v3_row_is_spelled_in_the_v2_vocabulary_and_keeps_its_own(self):
        r = uta_history_row(UTA_ROW)
        assert r["openAvgPrice"] == "100000" and r["closeAvgPrice"] == "105000"
        assert r["pnl"] == "50" and r["netProfit"] == "48.5"
        assert r["openFee"] == "-0.6" and r["closeFee"] == "-0.63"
        assert r["holdSide"] == "long"
        assert r["ctime"] == str(OPENED_MS) and r["utime"] == str(OPENED_MS + 3_600_000)
        assert r["totalFunding"] == "-0.27"
        for k in UTA_ROW:
            assert r[k] == UTA_ROW[k], f"the row's own key {k} was dropped"
        assert "closeType" not in r and "leverage" not in r

    def test_every_mapped_name_is_a_name_the_docs_row_carries(self):
        for uta_name in UTA_HISTORY_FIELDS:
            assert uta_name in UTA_ROW, uta_name

    def test_a_v2_row_is_untouched_and_a_present_v2_name_is_not_overwritten(self):
        v2 = {"openAvgPrice": "1", "closeAvgPrice": "2", "pnl": "3"}
        assert uta_history_row(v2) == v2
        both = {"openPriceAvg": "9", "openAvgPrice": "1"}
        assert uta_history_row(both)["openAvgPrice"] == "1"
        assert uta_history_row("junk") == {} and uta_history_row(None) == {}

    def test_the_funding_placement_is_measured_on_the_spelled_row(self):
        read = funding_on_row(uta_history_row(UTA_ROW))
        assert read.usd == pytest.approx(-0.27)
        assert read.in_net is True and read.basis == NET_INCLUDES, (
            "48.5 == 50 - 0.27 - (0.6 + 0.63): the venue's net carries the funding")
        assert funding_on_row(UTA_ROW).in_net is None, (
            "the raw v3 spelling cannot be placed -- the adapter is what makes it readable")

    def test_the_channel_rides_in_the_sentence_and_never_in_the_class(self):
        o = [lookup_raised("history", ccxt.ExchangeError("bitget {code:40085}"), "v3")]
        assert o[0].detail == "ExchangeError (v3)"
        assert lookup_class(o) == "history raised ExchangeError"
        assert "history: raised ExchangeError (v3)" in lookup_sentence("BTC/USDT", o)
        assert "40085" not in lookup_sentence("BTC/USDT", o)
        plain = lookup_raised("history", ccxt.NetworkError("x"))
        assert plain == StageOutcome("history", "raised", "NetworkError"), (
            "the v2 spelling on the record is byte-identical to what it was")


# ── the stage on a real client ───────────────────────────────────────────────

def _client(uta: bool):
    ex = ca.bitget({"apiKey": "k", "secret": "s", "password": "p",
                    "options": {"defaultType": "swap", "uta": uta}})
    ex.set_markets([
        _mk(ex, id="BTCUSDT", symbol="BTC/USDT:USDT", base="BTC", quote="USDT", settle="USDT",
            baseId="BTC", quoteId="USDT", settleId="USDT", type="swap", spot=False, swap=True,
            contract=True, linear=True, inverse=False, contractSize=1, active=True,
            precision={"amount": 0.001, "price": 0.1}, info={}),
    ])
    return ex


def _router(v3_rows=(), v2_rows=()):
    def router(url, method, body):
        if "/api/v3/position/history-position" in url:
            return {"code": "00000", "data": {"list": list(v3_rows)}}
        if "/api/v2/mix/position/history-position" in url:
            return {"code": "00000", "data": {"list": list(v2_rows)}}
        return {"code": "00000", "data": {"list": []}}
    return router


def _real_executor(uta: bool, router):
    client = _client(uta)
    calls = _stub(client, router)
    ex = LiveExecutor()
    ex._venue = get_venue("bitget")
    ex._hedge_mode = False
    ex._get_exchange = AsyncMock(return_value=client)
    return ex, calls


class TestTheStageOnARealClient:
    def test_a_unified_client_asks_v3_and_never_the_classic_endpoint(self):
        ex, calls = _real_executor(True, _router(v3_rows=[UTA_ROW]))
        out = asyncio.run(ex._fetch_bitget_close_data(_pos()))
        paths = [u for _m, u, _b in calls]
        assert any("/api/v3/position/history-position" in u for u in paths), paths
        assert not any("/api/v2/mix/position/history-position" in u for u in paths), paths
        q = _q(next(u for u in paths if "/api/v3/position/history-position" in u))
        assert q["category"] == "USDT-FUTURES" and q["symbol"] == "BTCUSDT"
        assert q["startTime"] == str(OPENED_MS - 300_000), "the first window, 5 min before the open"
        assert out is not None and out["close_price"] == 105_000.0
        assert out["pnl"] == pytest.approx(48.5) and out["pnl_is_net"] is True
        assert out["funding"] == pytest.approx(-0.27) and out["funding_in_pnl"] is True
        assert out["fees"] == pytest.approx(1.23) and out["fees_cover"] == "round_trip"
        assert out["source"] == "bitget_position_history"

    def test_a_classic_client_asks_v2_and_never_v3(self):
        v2 = {"openAvgPrice": "100000", "closeAvgPrice": "104000", "pnl": "40",
              "netProfit": "38.8", "openFee": "0.6", "closeFee": "0.6", "closeType": "tp"}
        ex, calls = _real_executor(False, _router(v2_rows=[v2]))
        out = asyncio.run(ex._fetch_bitget_close_data(_pos()))
        paths = [u for _m, u, _b in calls]
        assert any("/api/v2/mix/position/history-position" in u for u in paths), paths
        assert not any("/api/v3/" in u for u in paths), paths
        q = _q(next(u for u in paths if "history-position" in u))
        assert q["productType"] == "USDT-FUTURES" and q["symbol"] == "BTCUSDT"
        assert out is not None and out["close_price"] == 104_000.0
        assert out["reason"] == "TP HIT (exchange)"

    def test_the_fills_and_orders_reads_beside_it_are_v3_on_the_same_client(self):
        """The reason the family is the client's: every other read this
        executor makes through the object already goes there."""
        ex, calls = _real_executor(True, _router())
        asyncio.run(ex._fetch_bitget_close_data(_pos()))
        paths = [u for _m, u, _b in calls]
        assert any("/api/v3/trade/fills" in u for u in paths), paths
        assert any("/api/v3/trade/history-orders" in u for u in paths), paths
        assert not any("/api/v2/" in u for u in paths), paths


# ── the stage reads the spelled row ──────────────────────────────────────────

class TestTheStageReadsTheRow:
    def test_a_v3_row_prices_the_close_and_the_reason_is_inferred(self):
        ex, x = _mock_executor(True, rows=[UTA_ROW])
        out = asyncio.run(ex._fetch_bitget_close_data(_pos()))
        assert out["close_price"] == 105_000.0 and out["pnl"] == pytest.approx(48.5)
        assert out["reason_inferred"] is True, "a v3 row names no closeType"
        assert out["leverage"] == 0, "a v3 row states no leverage: absent, never a figure"
        x.privateMixGetV2MixPositionHistoryPosition.assert_not_called()

    def test_an_unread_entry_matches_by_side_and_close_time(self):
        p = _pos(entry_price=0.0, stop_loss=0.0, take_profit=0.0)
        setattr(p, "adoption_unread", ("entry_price",))
        ex, _x = _mock_executor(True, rows=[
            {**UTA_ROW, "posSide": "short", "closePriceAvg": "1"},
            {**UTA_ROW, "updatedTime": str(OPENED_MS - 10)},
            UTA_ROW,
        ])
        out = asyncio.run(ex._fetch_bitget_close_data(p))
        assert out is not None and out["close_price"] == 105_000.0, (
            "the long row closed after the open, not the short one or the earlier one")

    def test_a_v3_refusal_is_the_class_on_the_record_and_the_channel_in_the_log(self, caplog):
        ex, x = _mock_executor(True, raises=ccxt.ExchangeError("bitget {code:40085,msg:x}"))
        p = _pos()
        with caplog.at_level(logging.WARNING):
            assert asyncio.run(ex._fetch_bitget_close_data(p)) is None
        assert p.close_lookup == "history raised ExchangeError"
        msg = _warning(caplog)[0]
        assert "history: raised ExchangeError (v3) (3 of 3 attempts)" in msg
        assert "40085" not in msg
        x.privateMixGetV2MixPositionHistoryPosition.assert_not_called()

    def test_a_v2_refusal_reads_exactly_as_it_did(self, caplog):
        ex, x = _mock_executor(False, raises=ccxt.ExchangeError("x"))
        p = _pos()
        with caplog.at_level(logging.WARNING):
            assert asyncio.run(ex._fetch_bitget_close_data(p)) is None
        assert p.close_lookup == "history raised ExchangeError"
        assert "history: raised ExchangeError (3 of 3 attempts)" in _warning(caplog)[0]
        x.privateUtaGetV3PositionHistoryPosition.assert_not_called()

    def test_an_empty_v3_answer_is_no_rows(self):
        ex, _x = _mock_executor(True, rows=[])
        p = _pos()
        assert asyncio.run(ex._fetch_bitget_close_data(p)) is None
        assert p.close_lookup == "history no_rows"

    def test_a_venue_with_no_history_channel_still_skips_the_stage(self):
        ex, x = _mock_executor(True, rows=[UTA_ROW])
        ex._venue = get_venue("bybit")
        p = _pos()
        assert asyncio.run(ex._fetch_bitget_close_data(p)) is None
        assert p.close_lookup == "fills no_rows"
        x.privateUtaGetV3PositionHistoryPosition.assert_not_called()


# ── the bot's own close keeps a venue-matched price ──────────────────────────

EXIT_PX = 95.0
TICKER = 96.0


def _found(**over):
    d = {"close_price": EXIT_PX, "pnl": None, "fees": 0.2, "fees_cover": "close",
         "reason": "SL HIT (inferred)", "reason_inferred": True,
         "source": "exchange_fill_recent_local_pnl", "pnl_is_net": False}
    d.update(over)
    return d


def _own_close(found, verify_price=0.0, verify_fee=0.0):
    ex, x = _executor(ticker=TICKER)
    p = _pos100()
    ex._positions = {p.trade_id: p}
    ex._verify_position_closed = AsyncMock(return_value={
        "confirmed": True, "fill_price": verify_price, "fill_qty": 10.0, "fees": verify_fee,
        "remaining_qty": 0.0, "failure_stage": ""})
    ex._fetch_bitget_close_data = AsyncMock(return_value=found)
    asyncio.run(ex.close_position(p.trade_id, reason="SL"))
    return p


def _entry_fee(p):
    from bot.core import live_executor as le
    return 100.0 * 10.0 * le.entry_rate_pct(p.order_type) / 100.0


class TestTheOwnClose:
    def test_a_matched_price_with_no_stated_profit_beats_the_ticker(self):
        p = _own_close(_found())
        assert p.close_price == EXIT_PX, f"the ticker {p.close_price} stayed on the record"
        assert p.fill_source.startswith("exchange_fill_recent_local_pnl"), p.fill_source
        assert p.gross_pnl == pytest.approx(-50.0)
        assert p.commission == pytest.approx(0.2 + _entry_fee(p)), "the fill's own fee, stated"
        assert p.close_lookup is None, "the lookup priced it, so there is no cause to record"

    def test_a_price_the_close_order_itself_confirmed_is_left_alone(self):
        p = _own_close(_found(close_price=94.0), verify_price=EXIT_PX, verify_fee=0.15)
        assert p.close_price == EXIT_PX and p.fill_source.startswith("exchange_fill")
        assert p.commission == pytest.approx(0.15 + _entry_fee(p))

    def test_a_round_trip_the_row_stated_is_the_whole_commission(self):
        p = _own_close(_found(fees=0.55, fees_cover="round_trip",
                              source="bitget_position_history_local_pnl"))
        assert p.close_price == EXIT_PX
        assert p.commission == pytest.approx(0.55), "the venue's round trip, no entry estimate on top"

    def test_the_close_orders_own_fill_fee_wins_over_a_round_trip_the_row_stated(self):
        """The inline fills fallback runs after the lookup whenever no profit
        was stated, and the close order's own fill is the more direct statement
        of the close leg: it takes the fee AND the cover back to the close leg,
        or a 0.2 close fee would be charged as the whole round trip."""
        ex, x = _executor(ticker=TICKER)
        x.fetch_my_trades = AsyncMock(return_value=[
            {"order": "CL-1", "price": EXIT_PX, "side": "sell",
             "info": {"profit": "0", "feeDetail": {"totalFee": "-0.2"}}}])
        p = _pos100()
        ex._positions = {p.trade_id: p}
        ex._verify_position_closed = AsyncMock(return_value={
            "confirmed": True, "fill_price": 0.0, "fill_qty": 10.0, "fees": 0.0,
            "remaining_qty": 0.0, "failure_stage": ""})
        ex._fetch_bitget_close_data = AsyncMock(return_value=_found(
            fees=0.55, fees_cover="round_trip", source="bitget_position_history_local_pnl"))
        asyncio.run(ex.close_position(p.trade_id, reason="SL"))
        assert p.close_price == EXIT_PX
        assert p.commission == pytest.approx(0.2 + _entry_fee(p)), (
            "the fill's close-leg fee beside an estimated entry leg, not 0.2 as a round trip")

    def test_a_stated_profit_carries_the_stages_own_word(self):
        p = _own_close(_found(pnl=-49.0, source="exchange_fill_sltp", reason_inferred=False,
                              reason="SL HIT (exchange)"))
        assert p.close_price == EXIT_PX and p.gross_pnl == pytest.approx(-49.0)
        assert p.fill_source.startswith("exchange_fill_sltp"), (
            f"a fills-stage find used to be booked as bitget_position_history: {p.fill_source}")

    def test_nothing_found_is_still_the_ticker_with_its_cause(self):
        ex, _x = _executor(ticker=TICKER)
        p = _pos100()
        ex._positions = {p.trade_id: p}
        ex._verify_position_closed = AsyncMock(return_value={
            "confirmed": True, "fill_price": 0.0, "fill_qty": 10.0, "fees": 0.0,
            "remaining_qty": 0.0, "failure_stage": ""})

        async def nothing(pos):
            pos.close_lookup = "history raised ExchangeError"
            return None
        ex._fetch_bitget_close_data = nothing
        asyncio.run(ex.close_position(p.trade_id, reason="SL"))
        assert p.close_price == TICKER and p.fill_source.startswith("ticker_after_bot_close")
        assert p.close_lookup == "history raised ExchangeError"


# ── the card and the digest ──────────────────────────────────────────────────

class TestTheCardAndTheDigest:
    def test_no_cause_on_record_is_counted_apart_from_the_causes(self):
        causes = {UNRECORDED: 179, "history raised ExchangeError": 3}
        s = parity.cause_sentence(causes)
        assert s == ("why the venue lookup priced none of them: history raised ExchangeError ×3; "
                     "179 carry no cause on record (closed before the lookup said why)")
        assert parity.cause_clause(causes) == (
            "most often: history raised ExchangeError ×3; 179 carry no cause on record")

    def test_only_absences_name_no_cause_at_all(self):
        assert parity.cause_sentence({UNRECORDED: 4}) == (
            "4 carry no cause on record (closed before the lookup said why)")
        assert parity.cause_clause({UNRECORDED: 4}) == "4 carry no cause on record"
        # Anchored to the line's own opening: a bare "why" matches "said why)".
        assert "why the venue lookup" not in parity.cause_sentence({UNRECORDED: 4})

    def test_only_causes_and_nothing_at_all(self):
        assert parity.cause_sentence({"fills unmatched": 2}) == (
            "why the venue lookup priced none of them: fills unmatched ×2")
        assert parity.cause_clause({"fills unmatched": 2, "orders no_rows": 1}) == (
            "most often: fills unmatched ×2")
        assert parity.cause_sentence({}) == "" and parity.cause_clause({}) == ""

    def test_split_keeps_the_order_the_counts_came_in(self):
        named, n = parity.split_causes({"a": 5, UNRECORDED: 9, "b": 2})
        assert list(named) == ["a", "b"] and n == 9

    def _abort(self, reason, day):
        return {"symbol": "BTC/USDT:USDT", "pnl_usd": -0.4, "gross_pnl": -0.1, "commission": 0.3,
                "entry_price": 100.0, "quantity": 1.0, "leverage": 10, "close_reason": reason,
                "fill_source": "exchange_fill",
                "closed_at": None if day is None else f"{day}T12:00:00+00:00"}

    def test_the_aborts_line_dates_each_guards_latest_firing(self):
        rows = [self._abort("leverage_overshoot", "2026-09-15")] * 3 + [
            self._abort("leverage_overshoot", "2026-09-27"),
            self._abort("leverage_overshoot", "2026-09-20"),
            self._abort("sl_placement_failed", "2026-09-26"),
            {"symbol": "BTC/USDT:USDT", "pnl_usd": 5.0, "gross_pnl": 5.3, "commission": 0.3,
             "entry_price": 100.0, "quantity": 1.0, "leverage": 10, "close_reason": "TP HIT",
             "fill_source": "exchange_fill", "closed_at": "2026-09-28T01:00:00+00:00"},
        ]
        s = parity.parity_summary(rows, 0.06, benchmark=None)
        assert s["aborts"]["latest"] == {"leverage_overshoot": "2026-09-27",
                                         "sl_placement_failed": "2026-09-26"}
        line = parity.aborts_line(s)
        assert "leverage_overshoot 5 (latest 2026-09-27)" in line
        assert "sl_placement_failed 1 (latest 2026-09-26)" in line
        assert line in parity.format_report(s)

    def test_a_guard_whose_rows_carry_no_time_is_not_dated(self):
        rows = [self._abort("leverage_overshoot", None), self._abort("leverage_overshoot", "junk"),
                self._abort("sl_placement_failed", "2026-09-26")]
        s = parity.parity_summary(rows, 0.06, benchmark=None)
        assert s["aborts"]["latest"] == {"sl_placement_failed": "2026-09-26"}
        line = parity.aborts_line(s)
        assert "leverage_overshoot 2" in line
        assert "leverage_overshoot 2 (latest" not in line
        assert parity.latest_close_by(rows[:2], "close_reason") == {}

    def test_a_close_day_is_the_recorded_days_never_today(self):
        today = datetime.now(UTC).date().isoformat()
        assert parity._closed_day({"closed_at": None}) is None
        assert parity._closed_day({"closed_at": "not a time"}) is None
        assert parity._closed_day({"closed_at": "2026-09-27T23:59:59+00:00"}) == "2026-09-27"
        assert parity._closed_day({}) != today
