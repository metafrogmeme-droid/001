"""A position that vanished from the venue is not a break-even.

`exchange_sync` reconciles local state against the exchange. A "ghost" is a
position the portfolio still tracks and the venue no longer shows: it closed,
and the job is to find out at what price. Three real sources are tried —
`fetchMyTrades` by SL/TP order id, `fetchClosedOrders` by the same, then a
ticker with SL/TP proximity — and step 4 ended the chain:

    # ── 4. Last resort: entry price ──
    logger.warning("Ghost close for %s: no fill data available — using entry
                    price (PnL=0)", trade.asset)
    return trade.entry_price, "no matching exchange position", "fallback"

`_calc_pnl(trade, trade.entry_price)` is `(entry - entry) * qty` — exactly
0.00 — so a position that was liquidated, or stopped out, or closed in profit
entered the permanent record as a MEASURED break-even. 0.00 is the one answer
that can be ruled out: it is the only price the trade demonstrably did not
close at, since a close at the entry is what "no data" was standing in for.

NOTE WHEN STEP 4 IS REACHED. Steps 1 and 2 found no fill AND step 3 could not
read a ticker (`current_price <= 0`). So the substitute was a price from an
arbitrary earlier moment standing in for one nobody could read at all — not a
near-miss estimate, a fabrication.

IT DID NOT STOP AT THE RECORD. `portfolio.close_position` computes the P&L and
hands it to `_on_trade_close`, which appends to `_realized_pnl_window`. Two
tighten-only size controls read that window, and a fabricated 0.0 pushes them
in OPPOSITE wrong directions:

  * the live-performance governor counts `sum(1 for p in recent if p > 0)`
    over `len(recent)`, so 0.0 is not a win but IS in the denominator — it
    drags the win rate down and over-tightens;
  * the equity throttle's `rolling_profit_factor` sees a value that adds to
    neither gross profit nor gross loss, yet it still counts toward
    `equity_throttle_min_samples` — an evidence floor satisfied by a
    non-measurement.

The consecutive-loss streak is genuinely unaffected: `_record_trade_result_locked`
handles the break-even case explicitly (`# C2-09 FIX: pnl == 0.0 (breakeven) —
no change to streak`), which is the one consumer already written for it.

THE FIX IS TO DEFER, NOT TO GUESS. Step 4 answers `None` and the caller leaves
the position tracked. "Open" is not true either — it did close — but it is the
RECOVERABLE falsehood: the sweep runs again, a readable ticker books it at a
real price, and local SL/TP monitoring keeps running in the meantime. A
break-even written into the trade record and the risk windows is permanent.
Same asymmetry `live_executor` states for the flatten verdict, pointed the
other way.

This whole chain had no test of any kind before this file.
"""

import asyncio
import types

import pytest

import bot.core.exchange_sync as xs
from bot.utils.models import Direction


def _trade(entry=100.0, qty=2.0, direction=Direction.LONG, sl=0.0, tp=0.0):
    return types.SimpleNamespace(
        asset="BTC/USDT", direction=direction, entry_price=entry,
        quantity=qty, stop_loss=sl, take_profit=tp)


class _Exchange:
    """A venue that answers however the test needs it to."""

    def __init__(self, my_trades=None, closed=None, ticker=None, raises=()):
        self._my_trades = my_trades or []
        self._closed = closed or []
        self._ticker = ticker
        self._raises = raises

    async def fetch_my_trades(self, symbol, limit=50):
        if "my_trades" in self._raises:
            raise RuntimeError("venue down")
        return self._my_trades

    async def fetch_closed_orders(self, symbol, limit=20):
        if "closed" in self._raises:
            raise RuntimeError("venue down")
        return self._closed

    async def fetch_ticker(self, symbol):
        if "ticker" in self._raises or self._ticker is None:
            raise RuntimeError("no ticker")
        return self._ticker


def _engine(positions=None):
    return types.SimpleNamespace(
        live_executor=types.SimpleNamespace(_positions=positions or {}))


def _price(exchange, trade=None, engine=None):
    return asyncio.run(xs._get_actual_close_price(
        engine or _engine(), exchange, trade or _trade(), "TI-1"))


class TestNothingReadableIsNotBreakEven:
    def test_no_fill_and_no_ticker_answers_none(self):
        price, reason, source = _price(_Exchange(raises=("ticker",)))
        assert price is None, (
            "the entry price is back — `(entry - entry) * qty` is exactly 0.00, "
            "so a liquidation books as a measured break-even")
        assert source == "unpriced"

    def test_the_reason_says_it_closed_and_the_price_is_unknown(self):
        _p, reason, _s = _price(_Exchange(raises=("ticker",)))
        # Both halves matter: it IS closed on the venue (so "still open" would
        # be wrong), and the exit price is what could not be read.
        assert "closed on the venue" in reason
        assert "unreadable" in reason

    def test_it_does_not_answer_zero_either(self):
        # A caller doing `if not price` would treat 0.0 and None alike, and
        # 0.0 would flow into _calc_pnl as a real exit at zero — a total loss
        # rather than a break-even, which is worse, not better.
        price, _r, _s = _price(_Exchange(raises=("ticker",)))
        assert price is not 0.0  # noqa: F632 - identity is the point
        assert price is None

    def test_a_venue_that_raises_everywhere_still_answers_unpriced(self):
        price, _r, source = _price(
            _Exchange(raises=("my_trades", "closed", "ticker")))
        assert price is None and source == "unpriced"


class TestTheRealSourcesStillWin:
    """The fix must not cost a reading that was working."""

    def test_a_matched_fill_is_used(self):
        pos = types.SimpleNamespace(symbol="BTC/USDT", sl_order_id="SL1",
                                    tp_order_id="TP1", stop_loss=90.0,
                                    take_profit=110.0)
        ex = _Exchange(my_trades=[{"order": "TP1", "price": "110.5"}])
        price, reason, source = _price(ex, engine=_engine({"p": pos}))
        assert price == 110.5
        assert source == "exchange_fill" and "TP HIT" in reason

    def test_a_closed_order_is_used_when_the_fill_list_is_empty(self):
        pos = types.SimpleNamespace(symbol="BTC/USDT", sl_order_id="SL1",
                                    tp_order_id="TP1", stop_loss=90.0,
                                    take_profit=110.0)
        ex = _Exchange(my_trades=[], closed=[{"id": "TP1", "average": 111.0}])
        price, _reason, source = _price(ex, engine=_engine({"p": pos}))
        assert price == 111.0 and source == "closed_order"

    def test_a_readable_ticker_is_used_when_no_order_matches(self):
        ex = _Exchange(ticker={"last": 97.25})
        price, reason, source = _price(ex)
        assert price == 97.25
        assert source == "ticker" and reason == "manually closed"

    def test_a_ticker_near_the_stop_is_attributed_to_it(self):
        ex = _Exchange(ticker={"last": 90.1})
        price, reason, source = _price(ex, trade=_trade(sl=90.0))
        assert price == 90.0 and source == "estimated"

    def test_a_zero_ticker_is_unreadable_not_a_price_of_zero(self):
        # `float(ticker.get("last", 0) or 0)` makes a null `last` a 0, and a
        # close at 0 is a total loss. It must reach step 4, not book that.
        price, _r, source = _price(_Exchange(ticker={"last": None}))
        assert price is None and source == "unpriced"


class TestTheSweepKeepsWhatItCannotPrice:
    """Driven end to end: what the reconciler does with an unpriced ghost."""

    @staticmethod
    def _run(monkeypatch, close_price_answer):
        closed: list = []
        trade = _trade()

        class _Portfolio:
            _positions = {"TI-1": trade}
            # Phase 2 re-reads this after the ghost sweep. An unpriced ghost
            # is deliberately still here; a booked one would be gone.
            open_positions: list = []

            def close_position(self, trade_id, exit_price):
                closed.append((trade_id, exit_price))
                return None

        engine = types.SimpleNamespace(
            portfolio=_Portfolio(),
            live_executor=types.SimpleNamespace(
                _positions={},
                open_positions=[],
                _get_exchange=_none_exchange))

        async def _fake_price(*a, **k):
            return close_price_answer

        monkeypatch.setattr(xs, "_get_actual_close_price", _fake_price)
        # No positions on the venue: the local one is a ghost.
        async def _no_positions(_engine):
            return []
        monkeypatch.setattr(xs, "_fetch_exchange_positions", _no_positions)

        msgs = asyncio.run(xs.sync_portfolio_with_exchange(engine))
        return closed, msgs

    def test_an_unpriced_ghost_is_not_booked(self, monkeypatch):
        closed, msgs = self._run(
            monkeypatch, (None, "closed on the venue, exit price unreadable",
                          "unpriced"))
        assert closed == [], (
            "the portfolio booked a close for a price nobody could read — "
            "that P&L reaches the permanent record and _realized_pnl_window")
        assert any("UNPRICED" in m for m in msgs)
        assert any("kept open" in m for m in msgs)

    def test_a_priced_ghost_is_still_booked(self, monkeypatch):
        closed, msgs = self._run(
            monkeypatch, (97.5, "manually closed", "ticker"))
        assert closed == [("TI-1", 97.5)], (
            "a ghost with a real price must still close — the fix defers only "
            "what it cannot price")
        assert any("Ghost closed" in m for m in msgs)

    def test_the_operator_is_told_rather_than_it_being_silent(self, monkeypatch):
        _closed, msgs = self._run(
            monkeypatch, (None, "closed on the venue, exit price unreadable",
                          "unpriced"))
        assert msgs, "an unpriced ghost produced no message at all"
        # It must not read as a completed close.
        assert not any("PnL=$" in m for m in msgs)


async def _none_exchange():
    return _Exchange(raises=("my_trades", "closed", "ticker"))


def test_the_calc_would_have_produced_exactly_zero():
    """The arithmetic behind the whole file, pinned so the claim is not prose.

    This is why the old fallback was worse than an estimate: it did not
    approximate the exit, it guaranteed a P&L of zero.
    """
    t = _trade(entry=100.0, qty=2.0)
    assert xs._calc_pnl(t, t.entry_price) == 0.0
    assert xs._calc_pnl(t, 110.0) == pytest.approx(20.0)
    short = _trade(entry=100.0, qty=2.0, direction=Direction.SHORT)
    assert xs._calc_pnl(short, short.entry_price) == 0.0
