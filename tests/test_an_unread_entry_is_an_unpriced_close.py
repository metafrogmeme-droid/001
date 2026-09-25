"""A close whose ENTRY is not on record is an unpriced close, on every path.

Adoption writes ``entry_price = 0.0`` for an entry the venue did not state and
names it in ``adoption_unread``; the restore path reads that 0.0 back. All
three close paths then did ``(exit - pos.entry_price) * pos.quantity`` as if
0.0 were a price. Driven on the unfixed tree, an adopted SOL position of 10
contracts closed at 150:

    25227 path       LONG  gross=+1500.0  net=+1499.1   card "(+0.00%)"
                     SHORT gross=-1500.0  net=-1500.9
    reconcile        LONG  gross=+1500.0  net=+1499.1   (a fill priced the exit)
                     SHORT gross=-1500.0  net=-1500.9
    the bot's close  LONG  gross=+1500.0  net=+1499.1   ("Verified: CONFIRMED")
                     SHORT gross=-1500.0  net=-1500.9

-- the whole exit notional as profit for a long and as loss for a short, on
the record, the loss-streak feed and the governor's window, under a card
reading ``Entry: $0.0000``. The 25227 path also stored every figure
unconditionally and printed ``pnl_pct`` as a measured 0%.

Each path now asks `entry_on_record` (the ``price_on_record`` reading) and,
with no venue P&L, books the close UNPRICED: the None triple, ``+entry_unread``
on the fill source after the word for where the exit came from, a WARNING, an
``UNPRICED`` audit row and a ``close_entry_unread`` warning-rate event. A venue
P&L still prices the close, because the venue knows the entry even when this
record does not.

And the position-history stage read a row with no profit field as a gross of
0.0: a stop-out of -$50 was booked as a $0.00 gross (and, with fees on the
row, a net of exactly minus the fees). An absent figure answers ``pnl: None``
with a ``*_local_pnl`` source now; a present "0" is still a measurement.

Driven through the real methods with an ``AsyncMock`` venue.
"""

from __future__ import annotations

import asyncio
import logging
import math
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.core import live_executor as le
from bot.core.live_executor import (
    ENTRY_UNREAD,
    LiveExecutor,
    LivePosition,
    closed_trade_row,
    entry_fee_notional,
    entry_on_record,
)
from bot.core.venues import get_venue

UTC = timezone.utc
OPENED = datetime.now(UTC) - timedelta(hours=3)
OPENED_MS = int(OPENED.timestamp() * 1000)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    async def _now(*a, **k):
        return None
    monkeypatch.setattr(asyncio, "sleep", _now)


def _executor(venue_rows=(), history=(), fills=(), ticker=150.0):
    ex = LiveExecutor()
    ex._venue = get_venue("bitget")
    ex._hedge_mode = False
    ex._save_positions = MagicMock()
    ex._save_closed_trades = MagicMock()
    ex._fire_position_closed = MagicMock()
    ex._risk_engine = MagicMock()
    x = AsyncMock()
    x.privateMixGetV2MixPositionHistoryPosition = AsyncMock(
        return_value={"data": {"list": list(history)}})
    x.fetch_my_trades = AsyncMock(return_value=list(fills))
    x.fetch_closed_orders = AsyncMock(return_value=[])
    x.fetch_ticker = AsyncMock(return_value={"last": ticker})
    x.fetch_positions = AsyncMock(return_value=list(venue_rows))
    x.create_order = AsyncMock(return_value={"id": "CL-1"})
    x.cancel_order = AsyncMock(return_value={"status": "canceled"})
    x.fetch_open_orders = AsyncMock(return_value=[])
    ex._get_exchange = AsyncMock(return_value=x)
    return ex, x


def _adopted(direction="LONG", entry=0.0, **kw):
    p = LivePosition(trade_id="TI-adopted-SOL", symbol="SOL/USDT:USDT",
                     direction=direction, entry_price=entry, quantity=10.0,
                     cost_usd=0.0, stop_loss=0, take_profit=0, leverage=0,
                     opened_at=OPENED, status="open", origin="adopted")
    setattr(p, "adoption_unread", ("entry_price", "margin", "leverage"))
    for k, v in kw.items():
        setattr(p, k, v)
    return p


def _close_fill(direction, price=150.0, profit="0", **info):
    i = {"profit": profit, "tradeSide": "close"}
    i.update(info)
    return {"order": "app-close", "price": price,
            "side": "sell" if direction == "LONG" else "buy",
            "timestamp": OPENED_MS + 60_000, "info": i}


def _assert_unpriced(ex, p, msg, action):
    assert (p.gross_pnl, p.commission, p.pnl_usd) == (None, None, None), (
        "an entry of 0.0 priced the close: "
        f"gross={p.gross_pnl} net={p.pnl_usd} fees={p.commission}")
    assert p.status == "closed" and p.close_price == 150.0, (
        "the position IS gone from the venue: it is booked, only not priced")
    assert p.fill_source.endswith(ENTRY_UNREAD), p.fill_source
    assert "Entry: unread" in msg and "Entry: $0.0000" not in msg, msg
    assert "UNPRICED" in msg
    assert "1500" not in msg, msg
    ex._risk_engine.record_warning.assert_any_call("close_entry_unread")
    row = closed_trade_row(p)
    assert row["pnl_usd"] is None and row["gross_pnl"] is None
    assert ex._last_close_data["entry"] is None
    assert ex._last_close_data["pnl_usd"] is None
    ex._fire_position_closed.assert_called_once_with(p)


# ── the reading ──────────────────────────────────────────────────────────────

class TestTheReading:
    @pytest.mark.parametrize("v", [0.0, 0, None, -1.0, float("nan"), float("inf"), "x"])
    def test_what_is_not_a_price_is_not_an_entry(self, v):
        assert entry_on_record(_adopted(entry=0.0, entry_price=v)) is None

    def test_a_price_is_an_entry(self):
        assert entry_on_record(_adopted(entry=142.5)) == 142.5

    def test_the_value_decides_not_the_adoption_marker(self):
        # A later read filled the entry in: it is a price, whatever adoption
        # once recorded about it.
        p = _adopted(entry=142.5)
        assert "entry_price" in p.adoption_unread
        assert entry_on_record(p) == 142.5


class TestTheEntryFeeBasis:
    def test_the_recorded_entry_is_the_basis(self):
        assert entry_fee_notional(_adopted(entry=140.0), 150.0, 99.0) == pytest.approx(1400.0)

    @pytest.mark.parametrize("direction,gross", [("LONG", 100.0), ("SHORT", -100.0)])
    def test_an_unread_entry_is_derived_from_the_venues_gross_and_exit(self, direction, gross):
        # entry 140 -> exit 150 on 10 contracts: +100 for a long, -100 for a
        # short. Either way the entry notional the venue's figures imply is 1400.
        assert entry_fee_notional(_adopted(direction), 150.0, gross) == pytest.approx(1400.0)

    @pytest.mark.parametrize("exit_px,gross", [(None, 100.0), (0.0, 100.0), (150.0, None)])
    def test_with_either_venue_figure_missing_there_is_no_basis(self, exit_px, gross):
        assert entry_fee_notional(_adopted(), exit_px, gross) is None

    def test_a_derived_notional_that_is_not_positive_is_no_basis(self):
        assert entry_fee_notional(_adopted("LONG"), 150.0, 1500.0) is None

    def test_no_quantity_no_basis(self):
        assert entry_fee_notional(_adopted(entry=140.0, quantity=0.0), 150.0, 1.0) is None

    def test_a_gross_venue_pnl_with_no_basis_leaves_net_and_fees_unknown(self):
        gross, net, comm = LiveExecutor._reconcile_exchange_close_pnl(
            12.0, 0.3, False, entry_notional=None, entry_fee_pct=0.06)
        assert (gross, net, comm) == (12.0, None, None)

    def test_a_net_venue_pnl_needs_no_basis(self):
        gross, net, comm = LiveExecutor._reconcile_exchange_close_pnl(
            11.4, 0.6, True, entry_notional=None, entry_fee_pct=0.06)
        assert net == 11.4 and comm == 0.6 and gross == pytest.approx(12.0)


# ── the three close paths ────────────────────────────────────────────────────

@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
class TestEveryPathBooksItUnpriced:
    def test_the_25227_path(self, direction, caplog):
        ex, _x = _executor()
        p = _adopted(direction)
        with caplog.at_level(logging.WARNING):
            msg = asyncio.run(ex._handle_already_closed_position(p))
        _assert_unpriced(ex, p, msg, "live_close_25227")
        assert p.fill_source == "ticker_fallback" + ENTRY_UNREAD
        # The card's percentage is a word, not a measured 0%.
        assert "+0.00%" not in msg
        assert any("entry price is not on record" in r.getMessage() for r in caplog.records)

    def test_the_reconcile_path(self, direction):
        ex, _x = _executor(fills=[_close_fill(direction)])
        p = _adopted(direction)
        ex._positions = {p.trade_id: p}
        msgs = asyncio.run(ex.reconcile_positions())
        assert len(msgs) == 1
        _assert_unpriced(ex, p, msgs[0], "reconcile_close")
        assert p.fill_source == "exchange_fill_recent_local_pnl" + ENTRY_UNREAD

    def test_the_bots_own_close(self, direction):
        ex, _x = _executor()
        p = _adopted(direction)
        ex._positions = {p.trade_id: p}
        ex._verify_position_closed = AsyncMock(return_value={
            "confirmed": True, "fill_price": 150.0, "fill_qty": 10.0, "fees": 0.0,
            "remaining_qty": 0.0, "failure_stage": ""})
        ex._fetch_bitget_close_data = AsyncMock(return_value=None)
        msg = asyncio.run(ex.close_position(p.trade_id, reason="manual"))
        _assert_unpriced(ex, p, msg, "live_close")
        assert p.fill_source == "exchange_fill" + ENTRY_UNREAD


class TestTheAuditSaysWhichEndWasMissing:
    def test_an_unpriced_row_names_the_entry(self, monkeypatch):
        said = []
        monkeypatch.setattr(le, "audit", lambda log, msg, **kw: said.append((msg, kw)))
        ex, _x = _executor()
        asyncio.run(ex._handle_already_closed_position(_adopted("LONG")))
        rows = [kw for _m, kw in said if kw.get("result") == "UNPRICED"]
        assert len(rows) == 1 and rows[0]["data"]["unread"] == "entry_price"
        closes = [kw for _m, kw in said if kw.get("action") == "live_close_25227"
                  and kw.get("result") != "UNPRICED"]
        assert closes and closes[0]["result"] == "CLOSED_UNPRICED"
        assert closes[0]["data"]["entry"] is None and closes[0]["data"]["pnl_usd"] is None


# ── the venue still prices it when it says ───────────────────────────────────

class TestAVenuePnlStillPricesIt:
    def test_a_net_history_figure_prices_an_adopted_close(self):
        row = {"openAvgPrice": "140", "closeAvgPrice": "150", "pnl": "100",
               "netProfit": "98.2", "openFee": "0.9", "closeFee": "0.9",
               "closeType": "close", "holdSide": "long", "utime": str(OPENED_MS + 60_000)}
        ex, _x = _executor(history=[row])
        p = _adopted("LONG")
        msg = asyncio.run(ex._handle_already_closed_position(p))
        assert p.pnl_usd == pytest.approx(98.2)
        assert p.fill_source == "bitget_position_history"
        assert not p.fill_source.endswith(ENTRY_UNREAD)
        ex._risk_engine.record_warning.assert_not_called()
        assert "Entry: unread" in msg          # the record still has no entry
        assert "UNPRICED" not in msg

    def test_a_gross_fill_profit_prices_it_with_the_entry_fee_from_the_venues_figures(self):
        fill = _close_fill("LONG", price=150.0, profit="100", feeDetail={"totalFee": "-0.9"})
        ex, _x = _executor(fills=[fill])
        p = _adopted("LONG")
        ex._positions = {p.trade_id: p}
        asyncio.run(ex.reconcile_positions())
        rate = le.entry_rate_pct(p.order_type)
        # entry notional derived: 150*10 - 100 = 1400; the entry fee is a
        # fraction of THAT, never of the 0.0 the record holds.
        assert p.gross_pnl == pytest.approx(100.0)
        assert p.commission == pytest.approx(0.9 + 1400.0 * rate / 100.0)
        assert p.pnl_usd == pytest.approx(100.0 - p.commission)
        assert p.fill_source == "exchange_fill_recent+exchange_pnl"


class TestARecordedEntryIsPricedAsBefore:
    @pytest.mark.parametrize("direction,gross", [("LONG", 100.0), ("SHORT", -100.0)])
    def test_the_25227_arithmetic_is_unchanged(self, direction, gross):
        ex, _x = _executor()
        p = _adopted(direction, entry=140.0)
        msg = asyncio.run(ex._handle_already_closed_position(p))
        assert p.gross_pnl == pytest.approx(gross)
        assert p.pnl_usd is not None and p.pnl_usd < p.gross_pnl
        assert p.fill_source == "ticker_fallback"
        assert "Entry: $140.0000" in msg
        ex._risk_engine.record_warning.assert_not_called()


# ── the history stage: an absent figure is not a zero ────────────────────────

def _hist_row(**kw):
    r = {"openAvgPrice": "100", "closeAvgPrice": "95", "closeType": "sl"}
    r.update(kw)
    return r


def _pos100():
    return LivePosition(trade_id="T-1", symbol="XYZ/USDT:USDT", direction="LONG",
                        entry_price=100.0, quantity=10.0, cost_usd=100.0,
                        stop_loss=95.0, take_profit=110.0, leverage=10,
                        opened_at=OPENED, status="open")


class TestTheHistoryStage:
    @pytest.mark.parametrize("fees", [{}, {"openFee": "0.3", "closeFee": "0.3"}])
    def test_a_row_with_no_profit_field_answers_none(self, fees):
        ex, _x = _executor(history=[_hist_row(**fees)])
        out = asyncio.run(ex._fetch_bitget_close_data(_pos100()))
        assert out["close_price"] == 95.0
        assert out["pnl"] is None, f"an absent profit was read as {out['pnl']}"
        assert out["source"] == "bitget_position_history_local_pnl"
        assert out["pnl_is_net"] is False

    def test_the_close_is_then_priced_from_the_price_move(self):
        ex, _x = _executor(history=[_hist_row(openFee="0.3", closeFee="0.3")])
        p = _pos100()
        asyncio.run(ex._handle_already_closed_position(p))
        assert p.gross_pnl == pytest.approx(-50.0), (
            "a stop-out booked as a break-even off an absent profit field")
        assert p.pnl_usd < -50.0
        assert p.fill_source == "bitget_position_history_local_pnl"

    def test_a_stated_zero_stays_a_measurement(self):
        ex, _x = _executor(history=[_hist_row(pnl="0")])
        out = asyncio.run(ex._fetch_bitget_close_data(_pos100()))
        assert out["pnl"] == 0.0 and out["pnl_is_net"] is False
        assert out["source"] == "bitget_position_history"

    def test_a_stated_zero_net_alone_is_a_measurement(self):
        ex, _x = _executor(history=[_hist_row(netProfit="0")])
        out = asyncio.run(ex._fetch_bitget_close_data(_pos100()))
        assert out["pnl"] == 0.0 and out["pnl_is_net"] is True

    def test_a_non_zero_net_wins(self):
        ex, _x = _executor(history=[_hist_row(pnl="-50", netProfit="-50.6",
                                              openFee="0.3", closeFee="0.3")])
        out = asyncio.run(ex._fetch_bitget_close_data(_pos100()))
        assert out["pnl"] == pytest.approx(-50.6) and out["pnl_is_net"] is True

    def test_a_gross_with_fees_is_netted_locally(self):
        ex, _x = _executor(history=[_hist_row(pnl="-50", openFee="0.3", closeFee="0.3")])
        out = asyncio.run(ex._fetch_bitget_close_data(_pos100()))
        assert out["pnl"] == pytest.approx(-50.6) and out["pnl_is_net"] is True

    def test_the_v1_gross_name_is_still_read(self):
        ex, _x = _executor(history=[_hist_row(achievedProfits="-50")])
        out = asyncio.run(ex._fetch_bitget_close_data(_pos100()))
        assert out["pnl"] == pytest.approx(-50.0) and out["pnl_is_net"] is False

    def test_an_adopted_close_with_no_profit_field_is_unpriced_not_the_exit(self):
        row = _hist_row(openAvgPrice="140", closeAvgPrice="150", holdSide="long",
                        utime=str(OPENED_MS + 60_000))
        ex, _x = _executor(history=[row])
        p = _adopted("LONG")
        msg = asyncio.run(ex._handle_already_closed_position(p))
        _assert_unpriced(ex, p, msg, "live_close_25227")
        assert p.fill_source == "bitget_position_history_local_pnl" + ENTRY_UNREAD

    def test_the_bots_own_close_derives_it_from_its_own_fill(self):
        """History priced nothing: the bot's close falls through to its own
        fill and the local arithmetic, never to a 0 from the history row."""
        ex, _x = _executor(history=[_hist_row(openFee="0.3", closeFee="0.3")])
        p = _pos100()
        ex._positions = {p.trade_id: p}
        ex._verify_position_closed = AsyncMock(return_value={
            "confirmed": True, "fill_price": 95.0, "fill_qty": 10.0, "fees": 0.0,
            "remaining_qty": 0.0, "failure_stage": ""})
        asyncio.run(ex.close_position(p.trade_id, reason="SL"))
        assert p.gross_pnl == pytest.approx(-50.0)
        assert not math.isclose(p.pnl_usd, -0.6)
