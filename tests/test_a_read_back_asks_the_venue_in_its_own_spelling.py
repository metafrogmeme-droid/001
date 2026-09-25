"""A read-back asks the venue about the market the order was placed on.

THE VENUE SYMBOL MAPPING REACHED THE ORDER CALLS AND NOT THE READ-BACKS. The
recorded symbol is the bot's spot spelling ("BTC/USDT"); every order and cancel
went out on ``self._venue.order_symbol(pos.symbol)``, and the reads that decide
what those orders DID asked about ``pos.symbol``. Driven with real ccxt 4.5.56
objects, fabricated markets and the transport stubbed:

  * Bybit lists BOTH the spot market and the perp, and "BTC/USDT" is the spot
    one. The close verification asked ``/v5/position/list?category=spot`` —
    which holds no perp — so a close booked CONFIRMED while the venue still
    held LONG 0.01; the residual-exposure branch could never fire. The close
    was then priced off the SPOT ticker.
  * Hyperliquid lists no "BTC/USDT" at all (BadSymbol), so every read raised:
    a close the venue CONFIRMED came back "CLOSE UNVERIFIED ... keeping the
    position OPEN and re-protecting", and placed two NEW reduce-only trigger
    orders on a flat book; the per-tick ticker read raised every tick.
  * ccxt refuses every Bybit ``fetch_order`` on a unified account without
    ``acknowledged`` — before sending anything. A limit entry that FILLED was
    never seen filling, got no stop, and after the hard timeout was booked
    ``stale_pending`` at ``$0.00`` over a live filled position.
  * The Bitget v3 sync asked the MODULE's venue: under a Bitget operator a
    per-user Hyperliquid executor read the operator's Bitget book (no api_key,
    so `for_account` fell back to the operator's keys) and rewrote its own
    position's leverage from it.

The fix is one seam per question — ``_fetch_order`` for an order read, the
venue mapping at every other read — and a structural rule
(``tests/venue_symbol_reads.py``) so the site added tomorrow cannot read the
recorded spelling unannounced. The rule's branches are driven on planted
trees, because on the real tree every site is mapped or recorded and a
mutation of the RULE would change no verdict there.
"""
from __future__ import annotations

import asyncio
import json
import textwrap
from collections import Counter
from datetime import datetime, timezone

import ccxt.async_support as ca
import pytest
from ccxt.base.errors import ArgumentsRequired, BadSymbol

import bot.core.live_executor as le
from bot.core.live_executor import LiveExecutor, LivePosition
from bot.core.order_state import rows_for_side
from bot.core.venues import get_venue
from tests import venue_symbol_reads as rule

UTC = timezone.utc


# ── fixtures: real ccxt, fabricated markets, the transport stubbed ─────────
def _mk(ex, **kw):
    return ex.safe_market_structure(kw)


def _bybit():
    ex = ca.bybit({"apiKey": "k", "secret": "s", "options": {"defaultType": "swap"}})
    spot = _mk(ex, id="BTCUSDT", symbol="BTC/USDT", base="BTC", quote="USDT", baseId="BTC",
               quoteId="USDT", type="spot", spot=True, swap=False, contract=False, linear=None,
               inverse=None, active=True, precision={"amount": 0.000001, "price": 0.01},
               limits={"amount": {"min": 0.000048}}, info={})
    lin = _mk(ex, id="BTCUSDT", symbol="BTC/USDT:USDT", base="BTC", quote="USDT", settle="USDT",
              baseId="BTC", quoteId="USDT", settleId="USDT", type="swap", spot=False, swap=True,
              contract=True, linear=True, inverse=False, contractSize=1, active=True,
              precision={"amount": 0.001, "price": 0.1}, limits={"amount": {"min": 0.001}}, info={})
    ex.set_markets([spot, lin])
    return ex


def _bitget():
    ex = ca.bitget({"apiKey": "k", "secret": "s", "password": "p",
                    "options": {"defaultType": "swap", "uta": True}})
    spot = _mk(ex, id="BTCUSDT", symbol="BTC/USDT", base="BTC", quote="USDT", baseId="BTC",
               quoteId="USDT", type="spot", spot=True, swap=False, contract=False, linear=None,
               inverse=None, active=True, precision={"amount": 0.000001, "price": 0.01}, info={})
    lin = _mk(ex, id="BTCUSDT", symbol="BTC/USDT:USDT", base="BTC", quote="USDT", settle="USDT",
              baseId="BTC", quoteId="USDT", settleId="USDT", type="swap", spot=False, swap=True,
              contract=True, linear=True, inverse=False, contractSize=1, active=True,
              precision={"amount": 0.001, "price": 0.1}, info={})
    ex.set_markets([spot, lin])
    return ex


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


def _bybit_router(holds_long=0.01, limit_filled=False):
    """A unified Bybit account. The venue holds a LINEAR long; its spot book
    holds nothing — so which category a read asks is the whole answer."""
    def router(url, method, body):
        if "/v5/user/query-api" in url:
            return {"retCode": 0, "result": {"unified": 1, "uta": 1, "userID": 1, "isMaster": True}}
        if "/v5/account/info" in url:
            return {"retCode": 0, "result": {"unifiedMarginStatus": 6, "marginMode": "REGULAR_MARGIN"}}
        if "/v5/position/list" in url:
            if "category=linear" in url and holds_long:
                return {"retCode": 0, "result": {"category": "linear", "list": [{
                    "symbol": "BTCUSDT", "side": "Buy", "size": str(holds_long), "avgPrice": "60000",
                    "leverage": "5", "positionValue": "600", "unrealisedPnl": "0",
                    "markPrice": "60000", "liqPrice": "", "positionIdx": 0, "tradeMode": 0,
                    "createdTime": "1", "updatedTime": "1"}]}}
            return {"retCode": 0, "result": {"list": []}}
        if "/v5/order/create" in url:
            return {"retCode": 0, "result": {"orderId": "close1", "orderLinkId": ""}}
        if "/v5/order/cancel" in url:
            b = json.loads(body or "{}")
            return {"retCode": 0, "result": {"orderId": b.get("orderId", ""), "orderLinkId": ""}}
        if "/v5/order/realtime" in url or "/v5/order/history" in url:
            if "orderId=close1" in url or (limit_filled and "orderId=lim1" in url):
                oid = "close1" if "orderId=close1" in url else "lim1"
                return {"retCode": 0, "result": {"list": [{
                    "orderId": oid, "symbol": "BTCUSDT", "side": "Sell" if oid == "close1" else "Buy",
                    "orderType": "Market", "orderStatus": "Filled", "qty": "0.01",
                    "cumExecQty": "0.01", "avgPrice": "60000", "cumExecFee": "0.36",
                    "createdTime": "1", "updatedTime": "1", "price": "60000"}]}}
            return {"retCode": 0, "result": {"list": []}}
        if "/v5/market/tickers" in url:
            return {"retCode": 0, "result": {"list": [{"symbol": "BTCUSDT", "lastPrice": "60000",
                                                         "bid1Price": "59999", "ask1Price": "60001"}]}}
        return {"retCode": 0, "result": {"list": []}}
    return router


def _category(url):
    return url.split("category=")[1].split("&")[0] if "category=" in url else None


@pytest.fixture
def fast(monkeypatch):
    real_sleep = asyncio.sleep

    async def _nosleep(*_a, **_k):
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", _nosleep)
    monkeypatch.setattr(le, "_VENUE_SETTLE_SECONDS", 0)


def _pos(**kw):
    base = dict(trade_id="T1", symbol="BTC/USDT", direction="LONG", entry_price=60000.0,
                quantity=0.01, stop_loss=59000.0, take_profit=62000.0, leverage=5,
                cost_usd=120.0, status="open", opened_at=datetime.now(UTC),
                sl_order_id="sl1", tp_order_id="tp1")
    base.update(kw)
    return LivePosition(**base)


def _executor(tmp_path, venue, creds=None):
    creds = creds or ({"wallet_address": "0x" + "ab" * 20, "agent_private_key": "0x" + "01" * 32}
                      if venue == "hyperliquid" else {"api_key": "k", "api_secret": "s"})
    return LiveExecutor(user_id="u1", credentials=creds, venue=venue, state_dir=str(tmp_path))


class _HyperliquidVenue:
    """Answers the way Hyperliquid does, resolving every symbol through ccxt's
    OWN ``market()`` over its USDC perp list — so "BTC/USDT" raises BadSymbol
    exactly as the real client does, and a confirmed fill reads confirmed."""

    def __init__(self):
        self.real = ca.hyperliquid({"walletAddress": "0x" + "ab" * 20,
                                    "privateKey": "0x" + "01" * 32,
                                    "options": {"defaultType": "swap"}})
        self.real.set_markets([_mk(
            self.real, id="0", symbol="BTC/USDC:USDC", base="BTC", quote="USDC", settle="USDC",
            baseId="0", quoteId="USDC", settleId="USDC", type="swap", spot=False, swap=True,
            contract=True, linear=True, inverse=False, contractSize=1, active=True,
            precision={"amount": 0.00001, "price": 0.1}, limits={"amount": {"min": 0.00001}},
            info={})])
        self.markets = self.real.markets
        self.sent: list = []
        self.tickers: list = []

    def market(self, s):
        return self.real.market(s)

    async def load_markets(self, *a, **k):
        return self.markets

    def price_to_precision(self, s, p):
        return self.real.price_to_precision(s, p)

    def amount_to_precision(self, s, a):
        return self.real.amount_to_precision(s, a)

    async def fetch_ticker(self, s, params=None):
        self.market(s)
        self.tickers.append(s)
        return {"symbol": s, "last": 60000.0, "bid": 59999.0, "ask": 60001.0}

    async def fetch_open_orders(self, s=None, since=None, limit=None, params=None):
        if s:
            self.market(s)
        return []

    async def cancel_order(self, oid, s=None, params=None):
        self.market(s)
        self.sent.append(("cancel", oid, s))
        return {"id": oid, "status": "canceled"}

    async def fetch_order(self, oid, s=None, params=None):
        self.market(s)
        if oid in ("sl1", "tp1"):
            return {"id": oid, "status": "canceled", "filled": 0.0}
        return {"id": oid, "status": "closed", "filled": 0.01, "average": 60000.0,
                "fee": {"cost": 0.1}}

    async def fetch_positions(self, syms=None, params=None):
        for s in (syms or []):
            self.market(s)
        return []

    async def create_order(self, symbol, type, side, amount, price=None, params=None):
        self.market(symbol)
        self.sent.append(("create", symbol, dict(params or {})))
        return {"id": f"o{len(self.sent)}", "status": "closed", "filled": amount,
                "average": 60000.0}

    async def fetch_my_trades(self, s=None, since=None, limit=None, params=None):
        if s:
            self.market(s)
        return []

    async def fetch_closed_orders(self, s=None, since=None, limit=None, params=None):
        if s:
            self.market(s)
        return []

    async def close(self):
        await self.real.close()


# ── the rule, on the real tree ─────────────────────────────────────────────
class TestTheRule:
    def test_every_symbol_read_is_mapped_or_recorded(self):
        found = rule.tree_findings()
        recorded, _ = rule.baseline()
        unrecorded, stale = rule.compare(found, recorded)
        assert not unrecorded, (
            "a ccxt call hands the venue the recorded (spot-form) symbol. Map it "
            "with self._venue.order_symbol(...) (an order read goes through "
            "_fetch_order), or record it in tests/venue_symbol_read_baseline.txt "
            f"WITH the reason it is right: {unrecorded}")
        assert not stale, (
            "a recorded site is gone or its count moved — delete or correct the "
            f"row in the same commit: {stale}")

    def test_every_row_says_why(self):
        _, reasonless = rule.baseline()
        assert not reasonless, f"a baseline row with no reason: {reasonless}"

    def test_the_only_raw_order_read_in_the_executor_is_the_seam(self):
        src = (rule.ROOT / "bot/core/live_executor.py").read_text()
        keys = [f.key for f in rule.findings_for(src) if "fetch_order(" in f.key]
        assert keys == [], keys


# ── the rule's own branches, on planted trees ──────────────────────────────
def _keys(src):
    return Counter(f.key for f in rule.findings_for(textwrap.dedent(src)))


class TestTheRuleOnPlantedTrees:
    def test_a_recorded_symbol_attribute_is_bare(self):
        assert _keys("""
            async def f(self, exchange, pos):
                await exchange.fetch_ticker(pos.symbol)
        """) == Counter({"f.fetch_ticker(pos.symbol)": 1})

    def test_a_mapped_call_is_not_reported(self):
        assert _keys("""
            async def f(self, exchange, pos):
                await exchange.fetch_ticker(self._venue.order_symbol(pos.symbol))
                await exchange.fetch_positions([self._venue.swap_symbol(pos.symbol)])
        """) == Counter()

    def test_a_name_bound_only_to_a_mapped_value_is_mapped(self):
        assert _keys("""
            async def f(self, exchange, pos):
                sym = self._venue.order_symbol(pos.symbol)
                await exchange.fetch_open_orders(sym)
        """) == Counter()

    def test_a_name_with_one_unmapped_binding_is_bare(self):
        assert _keys("""
            async def f(self, exchange, pos, fut):
                sym = self._venue.order_symbol(pos.symbol)
                if not fut:
                    sym = pos.symbol
                await exchange.fetch_open_orders(sym)
        """) == Counter({"f.fetch_open_orders(sym)": 1})

    def test_a_loop_variable_is_bare(self):
        assert _keys("""
            async def f(self, exchange, positions):
                for sym in [p.symbol for p in positions]:
                    await exchange.fetch_ticker(sym)
        """) == Counter({"f.fetch_ticker(sym)": 1})

    def test_a_list_element_counts_for_fetch_positions(self):
        assert _keys("""
            async def f(self, exchange, pos):
                await exchange.fetch_positions([pos.symbol])
        """) == Counter({"f.fetch_positions([pos.symbol])": 1})

    def test_either_branch_of_a_conditional_decides(self):
        assert _keys("""
            async def f(self, exchange, pos, v):
                await exchange.fetch_ticker(self._venue.order_symbol(pos.symbol) if v else pos.symbol)
        """) == Counter({"f.fetch_ticker(self._venue.order_symbol(pos.symbol) if v else pos.symbol)": 1})

    def test_a_parameter_is_resolved_through_its_callers(self):
        # One caller maps, one hands the recorded spelling: only the second
        # is reported, and it is named.
        assert _keys("""
            async def reader(self, exchange, symbol):
                await exchange.fetch_positions([symbol])

            async def good(self, exchange, pos):
                await self.reader(exchange, self._venue.order_symbol(pos.symbol))

            async def bad(self, exchange, pos):
                await self.reader(exchange, pos.symbol)
        """) == Counter({"reader.fetch_positions([symbol]) <- bad(pos.symbol)": 1})

    def test_a_parameter_carried_twice_is_resolved_to_the_outer_caller(self):
        assert _keys("""
            async def inner(self, exchange, symbol):
                await exchange.fetch_ticker(symbol)

            async def middle(self, exchange, symbol):
                await self.inner(exchange, symbol)

            async def outer(self, exchange, pos):
                await self.middle(exchange, pos.symbol)
        """) == Counter({"inner.fetch_ticker(symbol) <- outer(pos.symbol)": 1})

    def test_a_splat_hand_off_is_reported_not_trusted(self):
        assert _keys("""
            async def inner(self, exchange, symbol):
                await exchange.create_order(symbol, "market", "buy", 1)

            async def outer(self, exchange, kw):
                await self.inner(exchange, **kw)
        """) == Counter({"inner.create_order(symbol) <- outer(**)": 1})

    def test_a_parameter_nobody_passes_here_is_reported(self):
        assert _keys("""
            async def lonely(self, exchange, symbol):
                await exchange.fetch_ticker(symbol)
        """) == Counter({"lonely.fetch_ticker(symbol) <- lonely(symbol (no caller in this file))": 1})

    def test_a_raw_order_read_outside_the_seam_is_reported_even_mapped(self):
        # The venue's READ PARAMS are the second half: a mapped symbol without
        # them is still refused by Bybit on a unified account.
        assert _keys("""
            async def f(self, exchange, pos):
                await exchange.fetch_order(pos.oid, self._venue.order_symbol(pos.symbol))

            async def _fetch_order(self, exchange, order_id, symbol):
                return await exchange.fetch_order(order_id, self._venue.order_symbol(symbol))
        """) == Counter({"f.fetch_order(self._venue.order_symbol(pos.symbol)) outside _fetch_order": 1})

    def test_the_executor_and_its_venue_are_not_exchanges(self):
        assert _keys("""
            async def f(self, pos):
                await self.fetch_ticker(pos.symbol)
                self._venue.market(pos.symbol)
        """) == Counter()

    def test_a_nested_def_does_not_bind_the_outer_name(self):
        # A binding inside a nested function is that function's; the outer
        # name's only binding is the mapped one.
        assert _keys("""
            async def f(self, exchange, pos):
                sym = self._venue.order_symbol(pos.symbol)
                def later():
                    sym = pos.symbol
                    return sym
                await exchange.fetch_ticker(sym)
        """) == Counter()


class TestTheBaselineIsTwoWay:
    def test_an_unrecorded_site_is_named(self):
        un, stale = rule.compare(Counter({"a": 2}), Counter({"a": 1}))
        assert un == {"a": 1} and stale == {}

    def test_a_row_whose_site_is_gone_is_stale(self):
        un, stale = rule.compare(Counter(), Counter({"gone": 1}))
        assert un == {} and stale == {"gone": 1}

    def test_a_row_with_no_reason_is_refused(self):
        counts, reasonless = rule.parse_baseline("k | 1 | \nj | 1\nm | x | why\nok | 2 | fine\n")
        assert counts == Counter({"ok": 2})
        assert len(reasonless) == 3


# ── rows_for_side: a spelling is not a different market ────────────────────
class TestTheBookReadMatchesTheVenuesRow:
    def test_a_perp_row_is_the_recorded_symbols_row(self):
        row = {"symbol": "BTC/USDT:USDT", "side": "long", "contracts": 0.01}
        assert rows_for_side([row], "BTC/USDT", "long") == [row]
        usdc = {"symbol": "BTC/USDC:USDC", "side": "long", "contracts": 0.01}
        assert rows_for_side([usdc], "BTC/USDT", "long") == [usdc]

    def test_a_different_market_is_still_dropped(self):
        row = {"symbol": "ETH/USDT:USDT", "side": "long", "contracts": 0.01}
        assert rows_for_side([row], "BTC/USDT", "long") == []


# ── B1: the close verification reads the book the order was placed on ─────
@pytest.mark.asyncio
async def test_bybit_close_verification_reads_the_perp_book(tmp_path, fast):
    ex = _bybit()
    calls = _stub(ex, _bybit_router(holds_long=0.01))
    exr = _executor(tmp_path, "bybit")
    try:
        res = await exr._verify_position_closed(ex, "BTC/USDT", "LONG", "close1")
    finally:
        await ex.close()
    assert res["confirmed"] is False
    assert res["failure_stage"] == "position_still_open"
    assert res["remaining_qty"] == pytest.approx(0.01)
    books = [_category(u) for _, u, _ in calls if "/v5/position/list" in u]
    assert books and set(books) == {"linear"}, books


@pytest.mark.asyncio
async def test_bybit_close_keeps_a_position_the_venue_still_holds(tmp_path, fast):
    """Through `close_position` itself: the residual branch, never CLOSED."""
    ex = _bybit()
    calls = _stub(ex, _bybit_router(holds_long=0.01))
    exr = _executor(tmp_path, "bybit")
    exr._exchange = ex
    exr._positions["T1"] = _pos()
    exr._save_positions()
    try:
        out = await exr.close_position("T1", reason="drive")
    finally:
        await ex.close()
    assert out.startswith("⚠️ PARTIAL CLOSE"), out
    assert exr._positions["T1"].status == "open"
    assert exr._closed_trades == []
    # No read reached the SPOT market: every categorised request is linear.
    cats = {_category(u) for _, u, _ in calls if _category(u)}
    assert cats == {"linear"}, cats


@pytest.mark.asyncio
async def test_hyperliquid_close_the_venue_confirms_is_booked_and_protects_nothing(tmp_path, fast):
    venue = _HyperliquidVenue()
    exr = _executor(tmp_path, "hyperliquid")
    exr._exchange = venue
    exr._positions["T1"] = _pos()
    exr._save_positions()
    with pytest.raises(BadSymbol):
        venue.market("BTC/USDT")
    try:
        out = await exr.close_position("T1", reason="drive")
    finally:
        await venue.close()
    assert out.startswith("CLOSED LONG BTC/USDT"), out
    triggers = [s for s in venue.sent if s[0] == "create"
                and (s[2].get("triggerPrice") or s[2].get("takeProfitPrice"))]
    assert triggers == [], "a close the venue confirmed re-armed stops on a flat book"


# ── B1: the per-tick monitor reads the venue's market ──────────────────────
@pytest.mark.asyncio
async def test_the_monitor_reads_the_bybit_perp_ticker(tmp_path, fast):
    ex = _bybit()
    calls = _stub(ex, _bybit_router())
    exr = _executor(tmp_path, "bybit")
    exr._exchange = ex
    exr._positions["T1"] = _pos(stop_loss=1.0, take_profit=1_000_000.0)
    try:
        await exr.check_positions()
    finally:
        await ex.close()
    tick = [_category(u) for _, u, _ in calls if "/v5/market/tickers" in u]
    assert tick and tick[0] == "linear", tick
    assert "BTC/USDT" not in exr._ticker_failure_count


@pytest.mark.asyncio
async def test_the_monitor_reads_the_hyperliquid_ticker(tmp_path, fast):
    venue = _HyperliquidVenue()
    exr = _executor(tmp_path, "hyperliquid")
    exr._exchange = venue
    exr._positions["T1"] = _pos(stop_loss=1.0, take_profit=1_000_000.0)
    try:
        await exr.check_positions()
    finally:
        await venue.close()
    assert venue.tickers and venue.tickers[0] == "BTC/USDC:USDC"
    assert exr._ticker_failure_count.get("BTC/USDT", 0) == 0


@pytest.mark.asyncio
async def test_the_drift_fallback_buys_the_perp_not_spot(tmp_path, fast, monkeypatch):
    """The one ORDER in the file on the recorded spelling."""
    base = _bybit_router()

    def router(url, method, body):
        if "orderId=lim1" in url:
            # The drifted limit, cancelled with nothing filled.
            return {"retCode": 0, "result": {"list": [{
                "orderId": "lim1", "symbol": "BTCUSDT", "side": "Buy", "orderType": "Limit",
                "orderStatus": "Cancelled", "qty": "0.01", "cumExecQty": "0", "avgPrice": "0",
                "price": "59000", "cumExecFee": "0", "createdTime": "1", "updatedTime": "1"}]}}
        return base(url, method, body)

    ex = _bybit()
    calls = _stub(ex, router)
    exr = _executor(tmp_path, "bybit")
    exr._exchange = ex
    pos = _pos(status="pending_fill", limit_order_id="lim1", sl_order_id=None,
               tp_order_id=None, order_type="limit")
    exr._positions["T1"] = pos
    monkeypatch.setattr(le, "trading_halted", lambda: False)
    try:
        await exr._execute_drift_market_fallback(ex, "T1", pos, 60000.0)
    finally:
        await ex.close()
    creates = [json.loads(b or "{}") for _, u, b in calls if "/v5/order/create" in u]
    assert creates, "the fallback placed no market order"
    assert creates[0].get("category") == "linear", creates[0]
    assert creates[0].get("orderType") == "Market"


# ── B2: an order read carries the venue's read params ──────────────────────
@pytest.mark.asyncio
async def test_a_bare_bybit_order_read_is_refused_before_any_request():
    ex = _bybit()
    calls = _stub(ex, _bybit_router())
    try:
        with pytest.raises(ArgumentsRequired):
            await ex.fetch_order("close1", "BTC/USDT:USDT")
    finally:
        await ex.close()
    assert not [u for _, u, _ in calls if "/v5/order/" in u]


@pytest.mark.asyncio
async def test_the_seam_reads_a_bybit_order_in_one_request(tmp_path):
    ex = _bybit()
    calls = _stub(ex, _bybit_router())
    exr = _executor(tmp_path, "bybit")
    try:
        order = await exr._fetch_order(ex, "close1", "BTC/USDT")
    finally:
        await ex.close()
    reads = [u for _, u, _ in calls if "/v5/order/" in u]
    assert len(reads) == 1 and "/v5/order/realtime" in reads[0]
    assert _category(reads[0]) == "linear"
    assert order["status"] == "closed" and order["filled"] == pytest.approx(0.01)


@pytest.mark.asyncio
async def test_a_bitget_order_read_is_the_call_it_always_was(tmp_path):
    seen: list = []

    class _Ex:
        async def fetch_order(self, *args, **kwargs):
            seen.append((args, kwargs))
            return {"id": args[0], "status": "open"}

    exr = LiveExecutor(state_dir=str(tmp_path))
    exr._venue = get_venue("bitget")
    await exr._fetch_order(_Ex(), "oid", "BTC/USDT")
    await exr._fetch_order(_Ex(), "oid", "BTC/USDT", params={"productType": "USDT-FUTURES"})
    assert seen == [(("oid", "BTC/USDT"), {}),
                    (("oid", "BTC/USDT"), {"params": {"productType": "USDT-FUTURES"}})]


def test_only_bybit_needs_read_params():
    assert get_venue("bybit").order_read_params() == {"acknowledged": True}
    for vid in ("bitget", "hyperliquid", "bingx", "okx", "gate", "kucoin", "paradex"):
        assert get_venue(vid).order_read_params() == {}, vid


@pytest.mark.asyncio
async def test_a_filled_bybit_limit_is_seen_filling(tmp_path, fast):
    ex = _bybit()
    calls = _stub(ex, _bybit_router(holds_long=0.01, limit_filled=True))
    exr = _executor(tmp_path, "bybit")
    exr._exchange = ex
    pos = _pos(status="pending_fill", limit_order_id="lim1", sl_order_id=None,
               tp_order_id=None, order_type="limit")
    exr._positions["T1"] = pos
    exr._save_positions()
    try:
        msg = await exr._check_pending_limit(ex, "T1", pos)
    finally:
        await ex.close()
    assert msg and msg.startswith("LIMIT FILLED"), msg
    assert pos.status == "open"
    stops = [u for _, u, _ in calls if "/v5/order/create" in u]
    assert len(stops) == 2, "the filled entry got no stop and target"


# ── B5: the v3 position read asks about the executor's own venue ──────────
class _V3Client:
    has_credentials = True
    asked: list = []

    def __init__(self, creds):
        self.creds = creds or {}

    def get(self, path):
        whose = ("user" if self.creds.get("api_key") and self.creds.get("api_secret")
                 else "operator")
        _V3Client.asked.append(whose)
        return {"code": "00000", "data": {"list": [
            {"symbol": "BTCUSDT", "leverage": "20", "total": "0.5", "marginMode": "crossed"}]}}


@pytest.fixture
def v3(monkeypatch):
    import bot.core.bitget_v3_client as v3mod
    _V3Client.asked = []
    monkeypatch.setattr(v3mod.BitgetV3Client, "for_account",
                        classmethod(lambda cls, creds: _V3Client(creds)))
    return _V3Client


def test_a_per_user_bitget_read_does_not_ask_the_operators_venue(monkeypatch, v3):
    monkeypatch.setattr(le, "get_venue",
                        lambda vid=None: get_venue(vid if vid is not None else "hyperliquid"))
    rows = LiveExecutor._fetch_v3_positions_raw(
        {"api_key": "k", "api_secret": "s", "passphrase": "p"}, "bitget")
    assert rows and v3.asked == ["user"]


def test_a_non_bitget_venue_reads_nothing_from_bitget(monkeypatch, v3):
    monkeypatch.setattr(le, "get_venue",
                        lambda vid=None: get_venue(vid if vid is not None else "bitget"))
    rows = LiveExecutor._fetch_v3_positions_raw(
        {"wallet_address": "0x" + "ab" * 20, "agent_private_key": "0x" + "01" * 32},
        "hyperliquid")
    assert rows == [] and v3.asked == []


@pytest.mark.asyncio
async def test_a_hyperliquid_sync_leaves_its_position_alone(tmp_path, monkeypatch, v3):
    monkeypatch.setattr(le, "get_venue",
                        lambda vid=None: get_venue(vid if vid is not None else "bitget"))
    audits: list = []
    monkeypatch.setattr(le, "audit", lambda log, msg, **kw: audits.append((msg, kw)))
    exr = _executor(tmp_path, "hyperliquid")
    pos = _pos(leverage=3, cost_usd=200.0, sl_order_id=None, tp_order_id=None)
    exr._positions["T1"] = pos
    await exr.sync_positions_from_exchange()
    assert (pos.leverage, pos.cost_usd) == (3, 200.0)
    assert v3.asked == []
    assert not [m for m, _ in audits if "NO positions" in m]


@pytest.mark.asyncio
async def test_a_per_user_bitget_sync_under_a_hyperliquid_operator_reads_its_book(
        tmp_path, monkeypatch, v3):
    monkeypatch.setattr(le, "get_venue",
                        lambda vid=None: get_venue(vid if vid is not None else "hyperliquid"))
    audits: list = []
    monkeypatch.setattr(le, "audit", lambda log, msg, **kw: audits.append((msg, kw)))
    exr = _executor(tmp_path, "bitget", {"api_key": "k", "api_secret": "s", "passphrase": "p"})
    pos = _pos(symbol="BTC/USDT", quantity=0.5, leverage=5, sl_order_id=None, tp_order_id=None)
    exr._positions["T1"] = pos
    await exr.sync_positions_from_exchange()
    assert v3.asked == ["user"]
    assert pos.leverage == 20
    assert not [m for m, _ in audits if "NO positions" in m]


# ── /orders reads through the executor's seam ──────────────────────────────
@pytest.mark.asyncio
async def test_the_orders_card_reads_bybit_orders_in_the_venues_spelling(tmp_path):
    from bot.core import open_orders as oo
    ex = _bybit()
    calls = _stub(ex, _bybit_router(limit_filled=True))
    exr = _executor(tmp_path, "bybit")
    exr._exchange = ex
    exr._positions["T1"] = _pos(status="pending_fill", limit_order_id="lim1")
    try:
        reading = await oo.read_open_orders(exr, now=datetime.now(UTC), expire_sec=4 * 3600)
    finally:
        await ex.close()
    per_symbol = [_category(u) for _, u, _ in calls
                  if "/v5/order/realtime" in u and "symbol=BTCUSDT" in u and "orderId" not in u]
    by_id = [_category(u) for _, u, _ in calls if "orderId=lim1" in u]
    assert per_symbol == ["linear"], per_symbol
    assert by_id == ["linear"], by_id
    assert any("FILLED" in n for n in reading.notes), reading.notes


def test_bybit_precision_is_the_perp_grid():
    """What the partial close rounds to: the recorded spelling is Bybit's SPOT grid."""
    ex = _bybit()
    try:
        assert ex.amount_to_precision("BTC/USDT", 0.0123456) == "0.012345"
        assert ex.amount_to_precision(get_venue("bybit").order_symbol("BTC/USDT"),
                                      0.0123456) == "0.012"
    finally:
        asyncio.run(ex.close())


@pytest.mark.asyncio
async def test_bitget_reads_reach_the_same_market_as_before(tmp_path):
    """The mapping is the identity on Bitget, so no Bitget read moved."""
    assert get_venue("bitget").order_symbol("BTC/USDT") == "BTC/USDT"
    ex = _bitget()
    calls = _stub(ex, lambda url, method, body: {"code": "00000", "data": {"list": []}})
    exr = LiveExecutor(state_dir=str(tmp_path))
    exr._venue = get_venue("bitget")
    try:
        await ex.fetch_positions(["BTC/USDT"])
        direct = [u for _, u, _ in calls]
        calls.clear()
        await ex.fetch_positions([exr._venue.order_symbol("BTC/USDT")])
        mapped = [u for _, u, _ in calls]
    finally:
        await ex.close()
    assert mapped == direct
