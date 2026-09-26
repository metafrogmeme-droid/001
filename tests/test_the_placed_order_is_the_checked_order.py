"""The order sent to the venue is the order the hard caps and the leverage checked.

Two ways `LiveExecutor.execute` placed something other than what it checked:

- **The minimum round-up ran after the caps.** `_preflight_check` tests the
  per-trade bound, the total margin cap and `PER_USER_MAX_FUNDS_USD` against
  the approved size. `_exchange_minimum_gate` then raises the quantity to the
  venue's minimum (up to `EXCHANGE_MIN_ROUNDUP_MAX_MULT`, on by default), and
  nothing asked the caps again. A linked account with $70 deployed and a $30
  approval sent $40 of margin: $110 against its $100 cap. The caps are asked
  again at the margin the rounded quantity places.
- **The leverage was read twice.** `_ensure_leverage` and `_size_or_block`
  each called `_compute_target_leverage`, which re-reads the `/leverage`
  override and, for an unread preference, the preference file. A change
  between the two set the venue to one leverage and sized at another: a
  `/leverage 10` landing during the ticker read sized a $100 approval at 10x
  on a venue set to 5x, $200 of margin locked. `execute` reads it once and
  hands the same number to both.
- **A Tier C limit re-sized after the minimum gate ran.** A limit that would
  fill as a taker is re-priced, and a marginal-confluence (Tier C) re-price
  scales the size by 0.7 and recomputes the quantity. That ran after the
  minimum gate, so $20 at 5x on ETH at 4000 (0.025, over the 0.02 minimum)
  went out as 0.017, under it, for the venue to refuse. The re-sized quantity
  is asked the gate again.

Driven through the real `execute` against a venue that records what it is
sent, with real ccxt precision (`ccxt.bitget` with a fabricated market).
"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import patch

import ccxt
import pytest

from bot.config import CONFIG, RUNTIME
from bot.core import bounds_shadow
from bot.core import live_executor as le
from bot.core import user_leverage_store as uls
from bot.core.limit_entry import EntryResult
from bot.core.live_executor import LiveExecutor, LivePosition
from bot.utils.models import Direction, TradeIdea


class _Sent(BaseException):
    """Raised by create_order once it has recorded what it was sent."""


def _market(amount_step=0.01, amount_min=0.01, cost_min=5.0):
    return {
        "id": "ETHUSDT", "symbol": "ETH/USDT:USDT", "base": "ETH",
        "quote": "USDT", "settle": "USDT", "baseId": "ETH", "quoteId": "USDT",
        "settleId": "USDT", "type": "swap", "spot": False, "margin": False,
        "swap": True, "future": False, "option": False, "active": True,
        "contract": True, "linear": True, "inverse": False, "contractSize": 1.0,
        "precision": {"amount": amount_step, "price": 0.01},
        "limits": {"amount": {"min": amount_min, "max": None},
                   "price": {"min": None, "max": None},
                   "cost": {"min": cost_min, "max": None},
                   "leverage": {"min": 1, "max": 125}},
        "info": {},
    }


class _Venue:
    """A Bitget stand-in that records every order and every leverage set."""

    def __init__(self, market, price, free=10_000.0):
        self.bg = ccxt.bitget()
        self.bg.set_markets([market])
        self.markets = self.bg.markets
        self.price = price
        self.free = free
        self.orders: list[dict] = []
        self.set_lev: list[int] = []
        self.lev = 1

    async def load_markets(self, *a, **k):
        return self.bg.markets

    async def fetch_ticker(self, symbol, *a, **k):
        return {"symbol": symbol, "last": self.price, "bid": self.price * 0.9999,
                "ask": self.price * 1.0001, "timestamp": int(time.time() * 1000)}

    async def fetch_order_book(self, symbol, limit=25, *a, **k):
        p = self.price
        return {"bids": [[p * (1 - 0.0001 * i), 1e6] for i in range(1, 26)],
                "asks": [[p * (1 + 0.0001 * i), 1e6] for i in range(1, 26)]}

    async def fetch_funding_rate(self, *a, **k):
        return {"fundingRate": 0.0001}

    async def set_margin_mode(self, *a, **k):
        return None

    async def set_leverage(self, leverage=None, symbol=None, params=None, **k):
        self.lev = leverage
        self.set_lev.append(leverage)
        return {}

    async def fetch_leverage(self, *a, **k):
        v = self.lev
        return {"longLeverage": v, "shortLeverage": v, "leverage": v,
                "marginMode": "isolated",
                "info": {"marginMode": "isolated", "longLeverage": str(v),
                         "shortLeverage": str(v)}}

    async def fetch_balance(self, *a, **k):
        return {"USDT": {"free": self.free, "used": 0.0, "total": self.free},
                "free": {"USDT": self.free}, "total": {"USDT": self.free}}

    async def fetch_positions(self, *a, **k):
        return []

    async def fetch_open_orders(self, *a, **k):
        return []

    async def fetch_ohlcv(self, *a, **k):
        return []

    async def fetch_account_configuration(self, *a, **k):
        return {}

    def amount_to_precision(self, symbol, amount):
        return self.bg.amount_to_precision(symbol, amount)

    def price_to_precision(self, symbol, price):
        return self.bg.price_to_precision(symbol, price)

    async def create_order(self, symbol=None, type=None, side=None, amount=None,
                           price=None, params=None, **k):
        self.orders.append({"amount": float(amount),
                            "leverage": int((params or {}).get("leverage") or 0)})
        raise _Sent()

    async def close(self):
        return None


def _idea(entry):
    return TradeIdea(id="TI-PLACED-1", asset="ETH/USDT", direction=Direction.LONG,
                     entry_price=entry, stop_loss=entry * 0.98,
                     take_profit=entry * 1.06, confidence=0.8, reasoning="fixture")


def _drive(size_usd, venue, tmp_path, *, user_id=None, pref=None, held=()):
    creds = {"api_key": "k", "api_secret": "s", "passphrase": "p"} if user_id else None
    ex = LiveExecutor(user_id=user_id, credentials=creds, state_dir=tmp_path)
    ex._exchange = venue
    ex._user_leverage_pref = pref
    for i, (cost, entry, lev) in enumerate(held):
        tid = f"OLD-{i}"
        ex._positions[tid] = LivePosition(
            trade_id=tid, symbol="BTC/USDT", direction="LONG", entry_price=entry,
            quantity=cost * lev / entry, cost_usd=cost, leverage=lev,
            stop_loss=entry * 0.9, take_profit=entry * 1.1, status="open")
    audits: list[dict] = []
    with patch.object(le, "audit", lambda log, msg, **kw: audits.append({"message": msg, **kw})), \
         patch.object(bounds_shadow.BOUNDS_LEDGER, "record", lambda *a, **k: None), \
         patch.object(type(CONFIG), "is_live", return_value=True):
        try:
            result = asyncio.run(ex.execute(_idea(venue.price), size_usd=size_usd,
                                            order_type="market"))
        except _Sent:
            result = "<sent>"
    return result, venue.orders, audits


@pytest.fixture(autouse=True)
def _no_override():
    RUNTIME.leverage_override = None
    yield
    RUNTIME.leverage_override = None


# ── the round-up is asked the caps again ────────────────────────────────

def test_a_linked_account_is_not_rounded_past_its_max_funds_cap(tmp_path, monkeypatch):
    monkeypatch.delenv("PER_USER_MAX_FUNDS_USD", raising=False)       # default $100
    venue = _Venue(_market(), price=4000.0)
    # $70 deployed, a $30 approval at 1x: 0.0075 ETH, rounded up to the 0.01
    # minimum = $40 of margin, $110 against the $100 cap.
    result, orders, _ = _drive(30.0, venue, tmp_path, user_id="u1", pref=1,
                               held=[(70.0, 100_000.0, 1)])
    assert orders == [], orders
    assert "rounded up to the venue minimum is $40.00" in result, result
    assert "max-funds cap $100.00" in result, result


def test_the_operator_book_is_not_rounded_past_its_total_cap(tmp_path):
    venue = _Venue(_market(amount_step=0.05, amount_min=0.05), price=2500.0)
    # $480 committed, $20 approved at 5x: 0.04, rounded to 0.05 = $25 margin,
    # $505 against the $500 total cap.
    result, orders, audits = _drive(20.0, venue, tmp_path, held=[(480.0, 100_000.0, 5)])
    assert orders == [], orders
    assert "rounded up to the venue minimum is $25.00" in result, result
    assert f"${le.MICRO_MAX_TOTAL_EXPOSURE:,.2f} total margin limit" in result, result
    assert any(a.get("result") == "ROUNDUP_OVER_CAP" for a in audits), audits


def test_a_round_up_past_the_per_trade_bound_is_refused(tmp_path):
    RUNTIME.leverage_override = 1
    venue = _Venue(_market(amount_step=0.03, amount_min=0.03), price=4000.0)
    # $100 at 1x is 0.025, rounded up to 0.03 = $120 against the $100 bound.
    result, orders, _ = _drive(100.0, venue, tmp_path)
    assert orders == [], orders
    assert "is $120.00 of margin" in result, result
    assert "per-trade margin limit" in result, result


def test_a_round_up_inside_every_cap_still_places(tmp_path):
    venue = _Venue(_market(amount_step=0.05, amount_min=0.05), price=2500.0)
    # $20 at 5x rounds to $25 of margin with nothing else committed.
    result, orders, _ = _drive(20.0, venue, tmp_path)
    assert result == "<sent>", result
    assert orders == [{"amount": 0.05, "leverage": 5}], orders


def test_an_order_that_needs_no_round_up_is_not_asked_again(tmp_path):
    venue = _Venue(_market(amount_step=0.001, amount_min=0.001), price=4000.0)
    with patch.object(LiveExecutor, "_hard_cap_refusal",
                      autospec=True, wraps=LiveExecutor._hard_cap_refusal) as spy:
        result, orders, _ = _drive(50.0, venue, tmp_path)
    assert result == "<sent>" and len(orders) == 1, (result, orders)
    assert spy.call_count == 1          # the preflight only


# ── one leverage per order ──────────────────────────────────────────────

def test_a_leverage_change_mid_order_does_not_split_set_from_size(tmp_path):
    class _Race(_Venue):
        async def fetch_ticker(self, symbol, *a, **k):
            RUNTIME.leverage_override = 10          # /leverage 10 lands here
            return await super().fetch_ticker(symbol)

    venue = _Race(_market(amount_step=0.001, amount_min=0.001), price=4000.0)
    result, orders, _ = _drive(100.0, venue, tmp_path)
    assert result == "<sent>", result
    (order,) = orders
    assert set(venue.set_lev) == {order["leverage"]}, (venue.set_lev, order)
    # Sized at the leverage the venue was set to, within one 0.001 step.
    assert 0 <= 100.0 * order["leverage"] / 4000.0 - order["amount"] < 0.001, order


def test_a_preference_that_reads_on_the_second_try_does_not_split_them(tmp_path):
    reads = {"n": 0}

    def flaky(uid):
        reads["n"] += 1
        if reads["n"] == 1:
            raise RuntimeError("unreadable")
        return 5

    venue = _Venue(_market(amount_step=0.001, amount_min=0.001), price=4000.0)
    with patch.object(uls, "get", flaky):
        result, orders, _ = _drive(50.0, venue, tmp_path, user_id="u1", pref=uls.UNREAD)
    assert result == "<sent>", result
    (order,) = orders
    assert set(venue.set_lev) == {order["leverage"]}, (venue.set_lev, order)
    assert 0 <= 50.0 * order["leverage"] / 4000.0 - order["amount"] < 0.001, order
    assert reads["n"] == 1, "the preference is read once per order"


def test_the_generic_venue_path_sets_the_orders_leverage(tmp_path, monkeypatch):
    # A non-Bitget venue leaves `_ensure_leverage` for the generic path, which
    # read the leverage for itself too; no Bitget drive can reach it.
    from bot.core.venues import get_venue

    pushed: list = []

    class _Ex:
        def market(self, sym):
            return {}

        async def set_margin_mode(self, mode, sym):
            return None

        async def set_leverage(self, leverage, sym, params=None):
            pushed.append(leverage)

        async def fetch_leverage(self, sym, params=None):
            return {"leverage": 3}

        async def fetch_positions(self, syms, params=None):
            return []

    async def _exchange():
        return _Ex()

    ex = LiveExecutor(state_dir=str(tmp_path))
    ex._venue = get_venue("hyperliquid")
    ex._standard_leverage = lambda symbol: 5        # a second read would say 5
    monkeypatch.setattr(ex, "_get_exchange", _exchange)
    asyncio.run(ex._ensure_leverage("BTC/USDT", "long", None, target=3))
    assert pushed and set(pushed) == {3}, pushed


# ── a Tier C re-size is asked the minimum again ─────────────────────────

def _drive_tier_c(venue, tmp_path, mult=0.7):
    ex = LiveExecutor(state_dir=tmp_path)
    ex._exchange = venue
    idea = _idea(venue.price * 1.001)            # a buy limit above the market
    tier = EntryResult(limit_price=venue.price * 0.998, tier="C",
                       confluence_count=1, size_multiplier=mult)
    audits: list[dict] = []
    with patch.object(le, "audit", lambda log, msg, **kw: audits.append({"message": msg, **kw})), \
         patch.object(bounds_shadow.BOUNDS_LEDGER, "record", lambda *a, **k: None), \
         patch.object(le, "calculate_entry", lambda **k: tier), \
         patch.object(type(CONFIG), "is_live", return_value=True), \
         patch.object(LiveExecutor, "_exchange_minimum_gate", autospec=True,
                      wraps=LiveExecutor._exchange_minimum_gate) as gate:
        try:
            result = asyncio.run(ex.execute(idea, size_usd=20.0, order_type="limit",
                                            atr_value=venue.price * 0.01))
        except _Sent:
            result = "<sent>"
    return result, venue.orders, audits, gate.call_count


def test_a_tier_c_resize_under_the_minimum_is_rounded_to_it(tmp_path):
    RUNTIME.leverage_override = 5
    venue = _Venue(_market(amount_step=0.001, amount_min=0.02), price=4000.0)
    # $20 at 5x = 0.025; Tier C x0.7 = 0.0175, under the 0.02 minimum.
    result, orders, audits, asked = _drive_tier_c(venue, tmp_path)
    assert result == "<sent>" and orders == [{"amount": 0.02, "leverage": 5}], orders
    assert asked == 2
    assert [a["result"] for a in audits].count("ROUNDED_TO_MIN") == 1


def test_a_tier_c_resize_is_rounded_once_on_a_coarse_grid(tmp_path):
    # Truncated to the 0.01 grid first, 0.0175 is 0.01, half the minimum and
    # past the 1.5x round-up cap; the gate reads it unrounded and takes 0.02.
    RUNTIME.leverage_override = 5
    venue = _Venue(_market(amount_step=0.01, amount_min=0.02), price=4000.0)
    result, orders, _, _ = _drive_tier_c(venue, tmp_path)
    assert result == "<sent>" and orders == [{"amount": 0.02, "leverage": 5}], orders


def test_a_tier_c_resize_under_the_minimum_is_refused_without_a_round_up(tmp_path):
    RUNTIME.leverage_override = 5
    venue = _Venue(_market(amount_step=0.001, amount_min=0.02), price=4000.0)
    object.__setattr__(CONFIG.exchange, "exchange_min_roundup_enabled", False)
    try:
        result, orders, audits, _ = _drive_tier_c(venue, tmp_path)
    finally:
        object.__setattr__(CONFIG.exchange, "exchange_min_roundup_enabled", True)
    assert orders == [], orders
    assert result.startswith("BLOCKED:") and "position too small for the exchange" in result
    assert "sized $70.00 notional at 5x" in result, result      # the Tier C size, not $100
    assert any(a.get("result") == "BELOW_EXCHANGE_MIN" for a in audits)


def test_a_tier_c_resize_over_the_minimum_keeps_its_smaller_size(tmp_path):
    RUNTIME.leverage_override = 5
    venue = _Venue(_market(amount_step=0.001, amount_min=0.001), price=4000.0)
    result, orders, _, asked = _drive_tier_c(venue, tmp_path)
    assert result == "<sent>" and orders == [{"amount": 0.017, "leverage": 5}], orders
    assert asked == 2


def test_an_order_the_entry_tier_did_not_resize_is_gated_once(tmp_path):
    RUNTIME.leverage_override = 5
    venue = _Venue(_market(amount_step=0.001, amount_min=0.001), price=4000.0)
    result, orders, _, asked = _drive_tier_c(venue, tmp_path, mult=1.0)
    assert result == "<sent>" and orders == [{"amount": 0.025, "leverage": 5}], orders
    assert asked == 1
