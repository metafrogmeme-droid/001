"""On Bitget, every read asks the perp book, not the spot one.

`Venue.order_symbol` was the identity on Bitget, under a module rule that said
not to "fix" it because "Bitget resolves spot-form symbols on the swap exchange
today". A position the bot opened records the spot form ("BTC/USDT"), and
driven against the pinned ccxt 4.5.56 with UTA markets loaded, ccxt resolves
that name to Bitget's SPOT market whatever `defaultType` says:

* the close verification read `category=SPOT`, found no row, and booked the
  close CONFIRMED while the venue still held the perp;
* the limit-fill leverage guard read the same empty book, so a fill at the
  wrong leverage was never flattened;
* the ticker read asked the spot book, and on an asset listed only as a perp
  (NATGAS) raised BadSymbol on every read;
* `amount_to_precision` and `price_to_precision` rounded on the SPOT grid,
  which for a sub-cent token is finer than the perp's, the 45115 rejection the
  entry path had already been fixed for.

Orders reached the perp all along because their params carry `productType`,
and a cancel carries no category, so neither changes with the mapping. Those
two facts are pinned here too, because they are the reason the fix is safe.
All drives use real ccxt objects, fabricated markets and a stubbed transport.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

import ccxt.async_support as ca
import pytest
from ccxt.base.errors import BadSymbol

import bot.core.live_executor as le
from bot.core.live_executor import LiveExecutor, LivePosition
from bot.core.venues import get_venue

UTC = timezone.utc
BG = get_venue("bitget")


def _mk(ex, **kw):
    return ex.safe_market_structure(kw)


def _bitget():
    """UTA client with BTC listed as spot and perp, NATGAS as a perp only, and
    a sub-cent token whose spot tick (1e-6) is finer than its perp tick (1e-5)."""
    ex = ca.bitget({"apiKey": "k", "secret": "s", "password": "p",
                    "options": {"defaultType": "swap", "uta": True}})
    ex.set_markets([
        _mk(ex, id="BTCUSDT", symbol="BTC/USDT", base="BTC", quote="USDT", baseId="BTC",
            quoteId="USDT", type="spot", spot=True, swap=False, contract=False, active=True,
            precision={"amount": 0.000001, "price": 0.01}, info={}),
        _mk(ex, id="BTCUSDT", symbol="BTC/USDT:USDT", base="BTC", quote="USDT", settle="USDT",
            baseId="BTC", quoteId="USDT", settleId="USDT", type="swap", spot=False, swap=True,
            contract=True, linear=True, inverse=False, contractSize=1, active=True,
            precision={"amount": 0.001, "price": 0.1}, info={}),
        _mk(ex, id="NATGASUSDT", symbol="NATGAS/USDT:USDT", base="NATGAS", quote="USDT",
            settle="USDT", baseId="NATGAS", quoteId="USDT", settleId="USDT", type="swap",
            spot=False, swap=True, contract=True, linear=True, inverse=False, contractSize=1,
            active=True, precision={"amount": 0.01, "price": 0.001}, info={}),
        _mk(ex, id="HOMEUSDT", symbol="HOME/USDT", base="HOME", quote="USDT", baseId="HOME",
            quoteId="USDT", type="spot", spot=True, swap=False, contract=False, active=True,
            precision={"amount": 0.01, "price": 0.000001}, info={}),
        _mk(ex, id="HOMEUSDT", symbol="HOME/USDT:USDT", base="HOME", quote="USDT",
            settle="USDT", baseId="HOME", quoteId="USDT", settleId="USDT", type="swap",
            spot=False, swap=True, contract=True, linear=True, inverse=False, contractSize=1,
            active=True, precision={"amount": 1, "price": 0.00001}, info={}),
    ])
    return ex


def _q(url):
    return {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}


def _router(holds_long=0.01):
    """A UTA account holding a BTC perp long. Its spot book holds nothing, so
    which category a read asks is the whole answer."""
    def router(url, method, body):
        q = _q(url)
        if "/api/v3/position/current-position" in url:
            if q.get("category") == "USDT-FUTURES" and holds_long:
                return {"code": "00000", "data": {"list": [{
                    "symbol": q.get("symbol", "BTCUSDT"), "posSide": "long",
                    "holdSide": "long", "total": str(holds_long), "size": str(holds_long),
                    "available": str(holds_long), "avgPrice": "60000",
                    "openPriceAvg": "60000", "leverage": "5", "marginMode": "crossed",
                    "unrealisedPnl": "0", "markPrice": "60000", "liqPrice": "0",
                    "marginSize": "120", "createdTime": "1", "updatedTime": "1"}]}}
            return {"code": "00000", "data": {"list": []}}
        if "/api/v3/trade/order-info" in url:
            return {"code": "00000", "data": {
                "orderId": q.get("orderId", "close1"), "clientOid": "c", "category": "USDT-FUTURES",
                "symbol": "BTCUSDT", "orderType": "market", "side": "sell", "price": "0",
                "qty": "0.01", "cumExecQty": "0.01", "cumExecValue": "600", "avgPrice": "60000",
                "orderStatus": "filled", "createdTime": "1", "updatedTime": "1",
                "feeDetail": [{"feeCoin": "USDT", "fee": "0.36"}]}}
        if "/api/v3/market/tickers" in url:
            last = "60050" if q.get("category") == "USDT-FUTURES" else "59000"
            return {"code": "00000", "data": [{"category": q.get("category"),
                                              "symbol": q.get("symbol"), "lastPrice": last,
                                              "bid1Price": last, "ask1Price": last, "ts": "1"}]}
        if "/api/v3/trade/place-order" in url:
            return {"code": "00000", "data": {"orderId": "o9", "clientOid": "c"}}
        if "/api/v3/trade/cancel-order" in url:
            return {"code": "00000", "data": {"orderId": "o1", "clientOid": "c"}}
        return {"code": "00000", "data": {"list": []}}
    return router


def _stub(ex, router):
    calls: list = []

    async def fetch(url, method="GET", headers=None, body=None):
        calls.append((method, url, body))
        return router(url, method, body)

    async def load_markets(reload=False, params={}):
        return ex.markets

    ex.fetch = fetch
    ex.load_markets = load_markets
    return calls


def _categories(calls, path):
    return [_q(u).get("category") for _, u, _ in calls if path in u]


@pytest.fixture
def fast(monkeypatch):
    real_sleep = asyncio.sleep

    async def _nosleep(*_a, **_k):
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", _nosleep)
    monkeypatch.setattr(le, "_VENUE_SETTLE_SECONDS", 0)


def _executor(tmp_path):
    return LiveExecutor(user_id="u1",
                        credentials={"api_key": "k", "api_secret": "s", "passphrase": "p"},
                        venue="bitget", state_dir=str(tmp_path))


def _pos(**kw):
    base = dict(trade_id="T1", symbol="BTC/USDT", direction="LONG", entry_price=60000.0,
                quantity=0.01, stop_loss=59000.0, take_profit=62000.0, leverage=5,
                cost_usd=120.0, status="open", opened_at=datetime.now(UTC),
                sl_order_id="sl1", tp_order_id="tp1")
    base.update(kw)
    return LivePosition(**base)


# ── the mapping, and what the recorded spelling used to name ───────────────

def test_the_recorded_spelling_is_bitgets_spot_market():
    ex = _bitget()
    try:
        assert ex.market("BTC/USDT")["spot"] is True
        assert ex.market(BG.order_symbol("BTC/USDT"))["swap"] is True
        with pytest.raises(BadSymbol):
            ex.market("NATGAS/USDT")
        assert ex.market(BG.order_symbol("NATGAS/USDT"))["swap"] is True
    finally:
        asyncio.run(ex.close())


# ── the reads ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_close_the_venue_still_holds_is_not_confirmed(tmp_path, fast):
    ex = _bitget()
    calls = _stub(ex, _router(holds_long=0.01))
    exr = _executor(tmp_path)
    try:
        res = await exr._verify_position_closed(ex, "BTC/USDT", "LONG", "close1")
    finally:
        await ex.close()
    # Before: the book read `category=SPOT`, held nothing, and this was
    # confirmed=True over a held LONG 0.01.
    assert res["confirmed"] is False
    assert res["failure_stage"] == "position_still_open"
    assert res["remaining_qty"] == pytest.approx(0.01)
    books = _categories(calls, "/api/v3/position/current-position")
    assert books and set(books) == {"USDT-FUTURES"}, books


@pytest.mark.asyncio
async def test_a_close_the_venue_confirms_is_confirmed(tmp_path, fast):
    ex = _bitget()
    calls = _stub(ex, _router(holds_long=0))
    exr = _executor(tmp_path)
    try:
        res = await exr._verify_position_closed(ex, "BTC/USDT", "LONG", "close1")
    finally:
        await ex.close()
    assert res["confirmed"] is True, res
    assert set(_categories(calls, "/api/v3/position/current-position")) == {"USDT-FUTURES"}


@pytest.mark.asyncio
async def test_the_fill_leverage_guard_reads_the_held_position(tmp_path, fast):
    # `_guard_fill_leverage` hands the recorded spelling. On the spot book the
    # position was "absent" and its leverage unread, so an over-levered fill
    # was never flattened.
    ex = _bitget()
    calls = _stub(ex, _router(holds_long=0.01))
    exr = _executor(tmp_path)
    try:
        res = await exr._verify_position_exists(ex, "BTC/USDT", "LONG",
                                                max_attempts=1, delay=0)
    finally:
        await ex.close()
    assert res["state"] == "found" and res["confirmed"] is True, res
    assert res["leverage"] == 5
    assert set(_categories(calls, "/api/v3/position/current-position")) == {"USDT-FUTURES"}


@pytest.mark.asyncio
async def test_the_monitor_reads_the_perp_ticker(tmp_path, fast, monkeypatch):
    ex = _bitget()
    calls = _stub(ex, _router(holds_long=0.01))
    exr = _executor(tmp_path)
    exr._exchange = ex

    async def _no_v3(*a, **k):
        return None
    monkeypatch.setattr(exr, "_fetch_v3_positions_raw", _no_v3, raising=False)
    exr._positions["T1"] = _pos(stop_loss=1.0, take_profit=1_000_000.0)
    exr._positions["T2"] = _pos(trade_id="T2", symbol="NATGAS/USDT", entry_price=3.0,
                                quantity=10.0, stop_loss=0.01, take_profit=1000.0)
    try:
        await exr.check_positions()
    finally:
        await ex.close()
    ticks = _categories(calls, "/api/v3/market/tickers")
    assert ticks and set(ticks) == {"USDT-FUTURES"}, ticks
    # A perp-only asset raised BadSymbol on the spot spelling, every tick.
    assert exr._ticker_failure_count.get("NATGAS/USDT", 0) == 0
    assert exr._ticker_failure_count.get("BTC/USDT", 0) == 0


@pytest.mark.asyncio
async def test_the_orders_card_reads_the_perp_book(tmp_path):
    from bot.core import open_orders as oo
    ex = _bitget()
    calls = _stub(ex, _router())
    exr = _executor(tmp_path)
    exr._exchange = ex
    # The per-symbol read runs when the account-wide one lists nothing and a
    # limit the bot is tracking is still resting, which is what this plants.
    exr._positions["T1"] = _pos(status="pending_fill", limit_order_id="lim1")
    try:
        await oo.read_open_orders(exr, now=datetime.now(UTC), expire_sec=4 * 3600)
    finally:
        await ex.close()
    per_symbol = [_q(u).get("category") for _, u, _ in calls
                  if "/api/v3/trade/unfilled-orders" in u and "symbol=BTCUSDT" in u]
    assert per_symbol == ["USDT-FUTURES"], per_symbol


# ── the grids ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_partial_close_is_rounded_on_the_perp_grid(tmp_path, fast):
    ex = _bitget()
    calls = _stub(ex, _router())
    exr = _executor(tmp_path)
    try:
        await exr._partial_close(ex, _pos(), 0.0123456, "tp1")
    finally:
        await ex.close()
    orders = [json.loads(b) for _, u, b in calls if "/api/v3/trade/place-order" in u]
    # The spot grid is 0.000001 and sent 0.012345 to a perp whose grid is 0.001.
    assert orders and orders[0]["qty"] == "0.012", orders
    assert orders[0]["category"] == "USDT-FUTURES"


@pytest.mark.asyncio
async def test_a_remainder_under_the_perp_grid_sends_no_order(tmp_path, fast):
    # ccxt rounds an order again on the order's own symbol, so the body above
    # would have read 0.012 on either grid. What the partial close's own
    # rounding decides is whether there is anything to send: 0.0004 is nothing
    # on the perp's 0.001 grid and a real amount on the spot's 0.000001 one.
    # And ccxt RAISES InvalidOrder for "nothing" rather than answering "0", so
    # the zero branch was unreachable on either grid until that raise was read
    # as nothing to send; the order then raised the same error out of the
    # ladder on every tick.
    ex = _bitget()
    calls = _stub(ex, _router())
    exr = _executor(tmp_path)
    try:
        out = await exr._partial_close(ex, _pos(), 0.0004, "tp1")
    finally:
        await ex.close()
    assert out == (0.0, "none", "")
    assert not [u for _, u, _ in calls if "/api/v3/trade/place-order" in u]


def test_a_stop_is_rounded_on_the_perp_tick():
    ex = _bitget()
    try:
        spot = LiveExecutor._round_price_to_market(ex, "HOME/USDT", 0.0168153)
        perp = LiveExecutor._round_price_to_market(ex, BG.order_symbol("HOME/USDT"), 0.0168153)
    finally:
        asyncio.run(ex.close())
    # The stop placement and the stop move both round through this with the
    # mapped spelling. On the spot tick the trigger was off the perp's grid.
    assert spot == "0.016815" and perp == "0.01682"


# ── why the fix is safe: orders and cancels send what they always sent ────

@pytest.mark.asyncio
async def test_an_order_on_the_grid_sends_the_same_body_either_way():
    bodies = []
    for sym in ("BTC/USDT", BG.order_symbol("BTC/USDT")):
        ex = _bitget()
        calls = _stub(ex, _router())
        try:
            await ex.create_order(sym, "market", "sell", 0.01, params=BG.close_params(True))
            await ex.cancel_order("o1", sym)
        finally:
            await ex.close()
        bodies.append([(u.split("bitget.com")[-1], b) for _, u, b in calls])
    assert bodies[0] == bodies[1]
    assert json.loads(bodies[1][0][1])["category"] == "USDT-FUTURES"
