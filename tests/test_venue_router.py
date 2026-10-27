"""The venue router asks every venue at once and says which ones answered.

Driven end to end with fake ccxt clients and fake adapters: nothing here
reaches a venue (`tests/test_no_test_reaches_a_venue.py`). Each behaviour is
asserted on both arms, because a refusal-only assertion passes against code
that does nothing.

What is pinned:

* UNREAD IS NEVER FLAT. A venue that timed out, raised a ccxt error, or
  returned a list with a row nobody could read has `flat is None` and the
  verdict `unread`; only a venue that answered with an empty, fully read list
  is `flat`.
* ABSENT IS NOT ZERO. A balance the venue did not state is None; a position row
  with no size is counted as unreadable, not dropped; a size of 0 is not a
  position.
* CONCURRENT, BOUNDED. N slow venues cost one wait, and one venue past its
  timeout does not delay or blank the others.
* IDENTITY. An adapter is built from ONE executor's account, venue and
  credentials; a venue with no adapter is reported `not_routed` and is never
  given another venue's reading, and an account with no executor has no row.
* THE ORDER PATH. A close is a reduce-only market order in the venue's own
  unit; OKX is read-only (its order shape has not been driven) and Hyperliquid
  mainnet is never signed by the server.
* THE WIRING. `flatten_all_positions` and `account_risk_overview` carry the
  router's answer when `VENUE_ROUTER_ENABLED` is on, and are byte-identical
  when it is off.
"""

from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace
from unittest import mock
from unittest.mock import AsyncMock

import ccxt
import pytest

import bot.core.venue_router as vr
from bot.config import CONFIG
from bot.core.engine import RuneClawEngine
from bot.core.venue_router import (
    STATUS_FAULTED,
    STATUS_OK,
    STATUS_UNREAD,
    BalanceReading,
    BybitAdapter,
    HyperliquidAdapter,
    OkxAdapter,
    PositionRead,
    PositionRow,
    ProductionVenueAdapter,
    VenueOrderRefused,
    VenueOutcome,
    VenueReadError,
    VenueRouter,
    adapters_for_executors,
    executor_key,
    parse_balance,
    parse_positions,
)
from bot.core.venues import PER_USER_EXECUTION_VENUES, get_venue
from bot.formatters.drift_offer import flatten_headline

# ── fakes ─────────────────────────────────────────────────────────────

def _row(symbol="BTC/USDT:USDT", side="long", contracts=1.0, mark=100.0):
    return {"symbol": symbol, "side": side, "contracts": contracts,
            "markPrice": mark}


class FakeClient:
    """A synchronous ccxt-shaped client. `positions` is a list of reads: each
    `fetch_positions` consumes one (the last repeats), so a test can say what
    the venue held before and after a close."""

    def __init__(self, positions=None, balance=None, fail=None,
                 sandbox=False, delay=0.0):
        self._reads = list(positions if positions is not None else [[]])
        self.balance = balance if balance is not None else {
            "USDT": {"free": 10.0, "total": 12.0}, "USDC": {"free": 10.0, "total": 12.0}}
        self.fail = dict(fail or {})
        self.isSandboxModeEnabled = sandbox
        self.delay = delay
        self.calls: list[tuple] = []
        self.threads: set[int] = set()

    def _enter(self, name, *args):
        self.calls.append((name, *args))
        self.threads.add(threading.get_ident())
        if self.delay:
            time.sleep(self.delay)
        exc = self.fail.get(name)
        if exc is not None:
            raise exc

    def fetch_positions(self, symbols=None, params=None):
        self._enter("fetch_positions", symbols, params)
        read = self._reads[0] if len(self._reads) == 1 else self._reads.pop(0)
        return read

    def fetch_balance(self, params=None):
        self._enter("fetch_balance", params)
        return self.balance

    def fetch_time(self):
        self._enter("fetch_time")
        return 1_700_000_000_000

    def create_order(self, symbol, type, side, amount, price, params):
        self._enter("create_order", symbol, type, side, amount, price, params)
        return {"id": "ord-1"}

    def orders(self):
        return [c for c in self.calls if c[0] == "create_order"]


class FakeAdapter(ProductionVenueAdapter):
    """An adapter whose four operations are scripted."""

    def __init__(self, account, venue_id, *, positions=None, balance=None,
                 delay=0.0, fail=None, close=None, ping=3.0):
        super().__init__(account)
        self.venue_id = venue_id
        self._positions = positions if positions is not None else PositionRead((), 0)
        self._balance = balance
        self._delay = delay
        self._fail = dict(fail or {})
        self._close = close
        self._ping = ping
        self.closed_with: list[str] = []

    async def _gate(self, name):
        if self._delay:
            await asyncio.sleep(self._delay)
        exc = self._fail.get(name)
        if exc is not None:
            raise exc

    async def fetch_positions(self) -> PositionRead:
        await self._gate("fetch_positions")
        if isinstance(self._positions, list):          # a script of reads
            return self._positions.pop(0) if len(self._positions) > 1 else self._positions[0]
        return self._positions

    async def fetch_balance(self) -> BalanceReading:
        await self._gate("fetch_balance")
        return self._balance or BalanceReading("USDT", None, None)

    async def close_all(self, reason: str = "venue_router") -> tuple[str, ...]:
        self.closed_with.append(reason)
        await self._gate("close_all")
        return tuple(self._close or ())

    async def ping(self) -> float:
        await self._gate("ping")
        return self._ping


def _open(n=1):
    return PositionRead(tuple(PositionRow("BTC/USDT:USDT", "long", 1.0, 100.0)
                              for _ in range(n)), 0)


# ── parsing: unreadable is never zero ─────────────────────────────────

class TestParsing:
    def test_a_zero_size_row_is_not_a_position(self):
        read = parse_positions([_row(contracts=0.0)])
        assert read.rows == () and read.unreadable_rows == 0

    def test_an_absent_size_is_counted_not_dropped_and_not_zero(self):
        read = parse_positions([_row(contracts=None), {"symbol": "ETH"}, "junk"])
        assert read.rows == ()
        assert read.unreadable_rows == 3

    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), "abc", True])
    def test_a_size_that_is_not_a_finite_number_is_unreadable(self, bad):
        assert parse_positions([_row(contracts=bad)]).unreadable_rows == 1

    def test_a_readable_row_keeps_the_venues_own_unit_and_side(self):
        read = parse_positions([_row(side="SHORT", contracts=-2.5, mark=None)])
        (p,) = read.rows
        assert (p.side, p.size, p.mark_price) == ("short", 2.5, None)

    def test_a_row_with_no_side_is_kept_with_side_none(self):
        (p,) = parse_positions([_row(side=None)]).rows
        assert p.side is None

    @pytest.mark.parametrize("raw", [None, {}, "x", 3])
    def test_a_position_list_that_is_not_a_list_is_not_a_reading(self, raw):
        with pytest.raises(VenueReadError):
            parse_positions(raw)

    def test_a_balance_with_no_entry_for_the_coin_is_none_not_zero(self):
        b = parse_balance({"USDT": {"free": 5.0, "total": 7.0}}, "USDC")
        assert (b.free, b.total) == (None, None)

    def test_a_stated_zero_free_is_a_zero(self):
        b = parse_balance({"USDT": {"free": 0.0, "total": 7.0}}, "USDT")
        assert (b.free, b.total) == (0.0, 7.0)

    def test_a_balance_that_is_not_a_mapping_is_not_a_reading(self):
        with pytest.raises(VenueReadError):
            parse_balance([], "USDT")


# ── the outcome's verdict ─────────────────────────────────────────────

class TestVerdict:
    def test_flat_is_said_only_for_a_fully_read_empty_book(self):
        o = VenueOutcome("a", "bybit", STATUS_OK, positions=())
        assert o.flat is True and o.verdict == "flat"

    def test_an_open_book_is_not_flat(self):
        o = VenueOutcome("a", "bybit", STATUS_OK, positions=_open().rows)
        assert o.flat is False and o.verdict == "open" and o.open_count == 1

    def test_an_empty_list_beside_an_unreadable_row_is_not_flat(self):
        o = VenueOutcome("a", "bybit", STATUS_OK, positions=(), unreadable_rows=1)
        assert o.flat is None and o.verdict == "unread"
        assert "NOT verified" in o.describe()

    def test_an_unread_venue_has_no_flat_and_no_open_count(self):
        o = VenueOutcome("a", "bybit", STATUS_UNREAD, error="RequestTimeout")
        assert o.flat is None and o.open_count is None and o.verdict == "unread"
        assert "flat NOT verified" in o.describe() and "RequestTimeout" in o.describe()

    def test_a_faulted_venue_is_never_flat(self):
        o = VenueOutcome("a", "okx", STATUS_FAULTED, error="X", note="refused")
        assert o.flat is None and o.verdict == "faulted"
        assert "refused" in o.describe()

    def test_the_row_shape_carries_no_balance_and_names_the_state(self):
        o = VenueOutcome("a", "bybit", STATUS_OK, positions=(),
                         balance=BalanceReading("USDT", 5.0, 6.0), latency_ms=12.0)
        d = o.venue_row()
        assert d["venue_state"] == "flat" and d["venue_latency_ms"] == 12.0
        assert not any("free" in k or "usd" in k for k in d)


# ── the router: concurrent, bounded, three-valued ─────────────────────

class TestRouterOverview:
    async def test_each_outcome_is_ok_unread_or_faulted_and_order_is_kept(self):
        router = VenueRouter([
            FakeAdapter("op", "bybit", positions=_open(2)),
            FakeAdapter("op2", "hyperliquid",
                        fail={"fetch_positions": ccxt.RequestTimeout("slow")}),
            FakeAdapter("op3", "okx", fail={"fetch_positions": KeyError("boom")}),
            FakeAdapter("op4", "bybit",
                        fail={"fetch_positions": VenueReadError("shape")}),
        ])
        out = await router.account_overview()
        assert [o.account for o in out] == ["op", "op2", "op3", "op4"]
        assert [o.status for o in out] == [STATUS_OK, STATUS_UNREAD, STATUS_FAULTED, STATUS_UNREAD]
        assert out[0].open_count == 2
        assert out[1].flat is None and out[1].error == "RequestTimeout"
        assert out[2].error == "KeyError"          # the class, never the message
        assert "boom" not in out[2].describe()

    async def test_a_ccxt_error_message_never_reaches_the_outcome(self):
        secret = "apiKey=SUPERSECRET123"
        router = VenueRouter([FakeAdapter(
            "op", "bybit", fail={"fetch_positions": ccxt.AuthenticationError(secret)})])
        (o,) = await router.account_overview()
        assert o.status == STATUS_UNREAD and secret not in repr(o) + o.describe()

    async def test_venues_are_read_concurrently_not_one_after_another(self):
        adapters = [FakeAdapter(f"a{i}", "bybit", delay=0.3) for i in range(4)]
        router = VenueRouter(adapters, timeout_s=5.0)
        started = time.perf_counter()
        out = await router.account_overview()
        elapsed = time.perf_counter() - started
        assert all(o.status == STATUS_OK for o in out)
        assert elapsed < 0.9, f"four 0.3s venues took {elapsed:.2f}s: sequential"

    async def test_a_venue_past_its_timeout_is_unread_and_costs_the_others_nothing(self):
        router = VenueRouter([
            FakeAdapter("slow", "hyperliquid", delay=30.0),
            FakeAdapter("fast", "bybit", positions=_open()),
        ], timeout_s=0.1)
        started = time.perf_counter()
        slow, fast = await router.account_overview()
        assert time.perf_counter() - started < 2.0
        assert slow.status == STATUS_UNREAD and slow.flat is None
        assert slow.error in ("TimeoutError",)
        assert fast.status == STATUS_OK and fast.verdict == "open"

    async def test_a_failed_balance_or_ping_does_not_unread_a_read_book(self):
        router = VenueRouter([FakeAdapter(
            "op", "bybit", fail={"fetch_balance": RuntimeError("x"), "ping": RuntimeError("y")})])
        (o,) = await router.account_overview()
        assert o.status == STATUS_OK and o.verdict == "flat"
        assert o.balance is None and o.latency_ms is None      # absent, not 0

    async def test_balance_and_latency_are_carried_when_read(self):
        router = VenueRouter([FakeAdapter(
            "op", "bybit", balance=BalanceReading("USDT", 0.0, 4.0), ping=7.5)])
        (o,) = await router.account_overview()
        assert o.balance.free == 0.0 and o.latency_ms == 7.5

    async def test_verify_flat_asks_for_neither_balance_nor_ping(self):
        a = FakeAdapter("op", "bybit")
        a.fetch_balance = AsyncMock(side_effect=AssertionError("balance asked"))
        a.ping = AsyncMock(side_effect=AssertionError("ping asked"))
        (o,) = await VenueRouter([a]).verify_flat()
        assert o.verdict == "flat" and o.balance is None and o.latency_ms is None
        a.fetch_balance.assert_not_called()
        a.ping.assert_not_called()

    async def test_a_child_that_escapes_its_guard_is_faulted_not_lost(self):
        router = VenueRouter([FakeAdapter("a", "bybit"), FakeAdapter("b", "bybit")])
        real = router._overview_one

        async def flaky(adapter, extras=True):
            if adapter.account == "a":
                raise RuntimeError("module bug")
            return await real(adapter, extras)

        router._overview_one = flaky
        a, b = await router.account_overview()
        assert a.status == STATUS_FAULTED and a.error == "RuntimeError"
        assert b.status == STATUS_OK

    async def test_no_adapters_is_an_empty_answer(self):
        assert await VenueRouter([]).account_overview() == []

    def test_one_account_one_venue_one_adapter(self):
        with pytest.raises(ValueError):
            VenueRouter([FakeAdapter("a", "bybit"), FakeAdapter("a", "bybit")])
        VenueRouter([FakeAdapter("a", "bybit"), FakeAdapter("a", "hyperliquid"),
                     FakeAdapter("b", "bybit")])

    @pytest.mark.parametrize("bad", [0, -1, float("nan"), float("inf"), True, "5"])
    def test_a_timeout_that_is_not_a_positive_number_is_refused(self, bad):
        with pytest.raises(ValueError):
            VenueRouter([], timeout_s=bad)


class TestRouterCloseAll:
    async def test_a_close_is_read_back_and_the_outcome_is_what_the_venue_holds_after(self):
        # the fake's close_all reads nothing: the router's read-back is the
        # first (and only) read, and it finds the book empty
        a = FakeAdapter("op", "bybit", close=["long BTC: close order sent"],
                        positions=PositionRead((), 0))
        (o,) = await VenueRouter([a]).close_all("why")
        assert a.closed_with == ["why"]
        assert o.verdict == "flat" and o.messages == ("long BTC: close order sent",)

    async def test_an_accepted_order_over_a_book_that_still_holds_is_open_not_flat(self):
        a = FakeAdapter("op", "bybit", close=["long BTC: close order sent"],
                        positions=_open())
        (o,) = await VenueRouter([a]).close_all()
        assert o.verdict == "open" and o.flat is False

    async def test_a_read_back_that_fails_is_unread_and_keeps_the_messages(self):
        # the fake's own close_all reads nothing, so the router's read-back is
        # the first fetch_positions call
        a = FakeAdapter("op", "bybit", close=["sent"],
                        fail={"fetch_positions": ccxt.NetworkError("down")})
        (o,) = await VenueRouter([a]).close_all()
        assert o.status == STATUS_UNREAD and o.flat is None
        assert o.messages == ("sent",) and o.error == "NetworkError"

    async def test_a_refused_order_path_is_faulted_with_the_reason_and_never_flat(self):
        a = FakeAdapter("op", "okx", fail={"close_all": VenueOrderRefused("not driven")})
        (o,) = await VenueRouter([a]).close_all()
        assert o.status == STATUS_FAULTED and o.note == "not driven" and o.flat is None

    async def test_a_close_that_cannot_list_the_book_is_unread(self):
        a = FakeAdapter("op", "bybit", fail={"close_all": ccxt.RequestTimeout("t")})
        (o,) = await VenueRouter([a]).close_all()
        assert o.status == STATUS_UNREAD

    async def test_only_the_named_accounts_are_closed(self):
        a = FakeAdapter("a", "bybit", close=["x"])
        b = FakeAdapter("b", "bybit", close=["y"])
        out = await VenueRouter([a, b]).close_all("r", only={("b", "bybit")})
        assert [o.account for o in out] == ["b"]
        assert a.closed_with == [] and b.closed_with == ["r"]


# ── the concrete adapters over a fake ccxt client ─────────────────────

def _adapter(cls, venue_id, client):
    return cls("acct", get_venue(venue_id), client=client)


class TestCcxtAdapters:
    async def test_calls_run_off_the_event_loop_thread(self):
        client = FakeClient(positions=[[_row()]])
        a = _adapter(BybitAdapter, "bybit", client)
        await a.fetch_positions()
        await a.fetch_balance()
        await a.ping()
        assert client.threads and threading.get_ident() not in client.threads

    async def test_the_factory_builds_the_client_once_off_the_loop(self):
        seen: list[int] = []

        def factory():
            seen.append(threading.get_ident())
            return FakeClient()

        a = BybitAdapter("acct", get_venue("bybit"), client_factory=factory)
        await a.fetch_positions()
        await a.fetch_positions()
        assert len(seen) == 1 and seen[0] != threading.get_ident()

    def test_a_client_and_a_factory_are_not_both_given_or_both_absent(self):
        with pytest.raises(ValueError):
            BybitAdapter("a", get_venue("bybit"))
        with pytest.raises(ValueError):
            BybitAdapter("a", get_venue("bybit"), client=FakeClient(),
                         client_factory=FakeClient)

    async def test_positions_balance_and_ping_are_read_in_the_venues_terms(self):
        client = FakeClient(positions=[[_row(contracts=3.0)]],
                            balance={"USDC": {"free": 4.0, "total": 9.0}})
        a = _adapter(HyperliquidAdapter, "hyperliquid", client)
        read = await a.fetch_positions()
        assert read.rows[0].size == 3.0
        bal = await a.fetch_balance()
        assert (bal.coin, bal.free, bal.total) == ("USDC", 4.0, 9.0)
        assert await a.ping() >= 0.0

    async def test_a_balance_the_venue_did_not_state_is_none(self):
        a = _adapter(BybitAdapter, "bybit", FakeClient(balance={"info": {}}))
        bal = await a.fetch_balance()
        assert bal.free is None and bal.total is None

    async def test_bybit_closes_with_a_reduce_only_market_order_on_the_perp(self):
        client = FakeClient(positions=[[_row("BTC/USDT:USDT", "long", 2.0)]])
        msgs = await _adapter(BybitAdapter, "bybit", client).close_all("r")
        (order,) = client.orders()
        _n, symbol, otype, side, amount, price, params = order
        assert (symbol, otype, side, amount) == ("BTC/USDT:USDT", "market", "sell", 2.0)
        assert price is None and params == {"reduceOnly": True}
        assert any("close order sent" in m for m in msgs)

    async def test_a_short_is_closed_by_a_buy(self):
        client = FakeClient(positions=[[_row(side="short", contracts=1.5)]])
        await _adapter(BybitAdapter, "bybit", client).close_all()
        assert client.orders()[0][3] == "buy"

    async def test_the_spot_form_symbol_is_sent_as_the_perp(self):
        client = FakeClient(positions=[[_row(symbol="ETH/USDT")]])
        await _adapter(BybitAdapter, "bybit", client).close_all()
        assert client.orders()[0][1] == "ETH/USDT:USDT"

    async def test_nothing_open_sends_no_order_and_says_so(self):
        client = FakeClient(positions=[[]])
        msgs = await _adapter(BybitAdapter, "bybit", client).close_all()
        assert client.orders() == [] and "nothing open" in msgs[0]

    async def test_a_row_nobody_could_read_is_not_closed_and_is_said(self):
        client = FakeClient(positions=[[_row(contracts=None), _row(contracts=1.0)]])
        msgs = await _adapter(BybitAdapter, "bybit", client).close_all()
        assert len(client.orders()) == 1
        assert any("could not be read and were not closed" in m for m in msgs)

    async def test_a_position_with_no_stated_side_is_not_guessed_at(self):
        client = FakeClient(positions=[[_row(side=None)]])
        msgs = await _adapter(BybitAdapter, "bybit", client).close_all()
        assert client.orders() == [] and "side not stated" in msgs[0]

    async def test_a_refused_or_timed_out_order_is_not_reported_as_closed(self):
        for exc in (ccxt.InsufficientFunds("x"), ccxt.RequestTimeout("t")):
            client = FakeClient(positions=[[_row()]], fail={"create_order": exc})
            (msg,) = await _adapter(BybitAdapter, "bybit", client).close_all()
            assert "close not confirmed" in msg and type(exc).__name__ in msg
            assert "order sent" not in msg

    async def test_the_book_that_cannot_be_listed_sends_nothing(self):
        client = FakeClient(fail={"fetch_positions": ccxt.NetworkError("n")})
        with pytest.raises(ccxt.NetworkError):
            await _adapter(BybitAdapter, "bybit", client).close_all()
        assert client.orders() == []

    async def test_hyperliquid_testnet_close_carries_the_mark_as_its_slippage_bound(self):
        client = FakeClient(positions=[[_row("BTC/USDC:USDC", "long", 0.5, mark=64000.0)]],
                            sandbox=True)
        await _adapter(HyperliquidAdapter, "hyperliquid", client).close_all()
        (order,) = client.orders()
        assert order[1] == "BTC/USDC:USDC" and order[3] == "sell"
        assert order[5] == 64000.0 and order[6] == {"reduceOnly": True}

    async def test_hyperliquid_without_a_mark_is_not_closed_at_a_guessed_price(self):
        client = FakeClient(positions=[[_row("BTC/USDC:USDC", mark=None)]], sandbox=True)
        msgs = await _adapter(HyperliquidAdapter, "hyperliquid", client).close_all()
        assert client.orders() == [] and "no mark price" in msgs[0]

    async def test_hyperliquid_mainnet_is_read_but_never_signed(self):
        client = FakeClient(positions=[[_row("BTC/USDC:USDC", mark=64000.0)]],
                            sandbox=False)
        a = _adapter(HyperliquidAdapter, "hyperliquid", client)
        # reads are open on mainnet ...
        assert (await a.fetch_positions()).rows
        # ... and the order is refused before anything is sent
        with pytest.raises(VenueOrderRefused) as info:
            await a.close_all()
        assert "mainnet" in info.value.reason and client.orders() == []

    async def test_hyperliquid_with_no_sandbox_flag_at_all_is_treated_as_mainnet(self):
        client = FakeClient(positions=[[_row("BTC/USDC:USDC", mark=1.0)]])
        del client.isSandboxModeEnabled
        with pytest.raises(VenueOrderRefused):
            await _adapter(HyperliquidAdapter, "hyperliquid", client).close_all()
        assert client.orders() == []

    async def test_okx_reads_and_refuses_to_close(self):
        assert "okx" not in PER_USER_EXECUTION_VENUES       # the allow-list rule
        client = FakeClient(positions=[[_row("BTC/USDT:USDT", contracts=3.0)]])
        a = _adapter(OkxAdapter, "okx", client)
        assert (await a.fetch_positions()).rows[0].size == 3.0
        with pytest.raises(VenueOrderRefused) as info:
            await a.close_all()
        assert "balances only" in info.value.reason and client.orders() == []

    async def test_the_router_reports_an_okx_close_as_faulted_with_the_reason(self):
        client = FakeClient(positions=[[_row()]])
        (o,) = await VenueRouter([_adapter(OkxAdapter, "okx", client)]).close_all()
        assert o.status == STATUS_FAULTED and "balances only" in (o.note or "")
        assert client.orders() == []

    async def test_the_ping_is_the_call_not_the_wait_behind_other_calls(self):
        class SlowBook(FakeClient):
            def fetch_positions(self, symbols=None, params=None):
                time.sleep(0.3)
                return []

        a = _adapter(BybitAdapter, "bybit", SlowBook())
        (o,) = await VenueRouter([a], timeout_s=5.0).account_overview()
        # the three reads share one client, so the ping queues behind the slow
        # book; the latency is what the CALL took, not that queue
        assert o.status == STATUS_OK and o.latency_ms is not None
        assert o.latency_ms < 100.0, o.latency_ms

    async def test_calls_on_one_client_never_overlap(self):
        active = {"n": 0, "max": 0}
        guard = threading.Lock()

        class Counting(FakeClient):
            def fetch_positions(self, symbols=None, params=None):
                with guard:
                    active["n"] += 1
                    active["max"] = max(active["max"], active["n"])
                time.sleep(0.05)
                with guard:
                    active["n"] -= 1
                return []

        a = _adapter(BybitAdapter, "bybit", Counting())
        await asyncio.gather(*(a.fetch_positions() for _ in range(5)))
        assert active["max"] == 1


# ── identity: one account, its own venue and keys ─────────────────────

def _ex(user_id, venue_id, creds=None):
    return SimpleNamespace(user_id=user_id, _venue=get_venue(venue_id),
                           _credentials=creds, open_positions=[],
                           close_all_positions=AsyncMock(
                               return_value=["LONG BTC/USDT: closed at 100"]))


class TestIdentity:
    def test_the_key_is_the_executors_own_account_and_venue(self):
        assert executor_key(_ex(None, "bybit")) == ("operator", "bybit")
        assert executor_key(_ex("alice", "hyperliquid")) == ("alice", "hyperliquid")
        assert executor_key(SimpleNamespace(user_id="x")) == ("x", "?")

    def test_each_adapter_is_built_with_its_own_executors_credentials(self):
        built: list[tuple] = []

        def fake_sync(venue, cfg, creds):
            built.append((venue.id, creds))
            return FakeClient()

        alice = {"api_key": "alice-key", "api_secret": "s"}
        bob = {"wallet_address": "0xb", "agent_private_key": "0x1"}
        with mock.patch.object(vr, "sync_client_for", fake_sync):
            adapters, unrouted = adapters_for_executors(
                [_ex(None, "bybit"), _ex("alice", "bybit", alice),
                 _ex("bob", "hyperliquid", bob)], cfg=object())
            assert unrouted == []
            assert [(a.account, a.venue_id) for a in adapters] == [
                ("operator", "bybit"), ("alice", "bybit"), ("bob", "hyperliquid")]
            for a in adapters:
                asyncio.run(a.fetch_positions())
        # the operator's keys are never handed to a user's client, and the
        # reverse: each factory saw exactly one executor's credentials
        assert sorted(built, key=str) == sorted(
            [("bybit", None), ("bybit", alice), ("hyperliquid", bob)], key=str)

    def test_a_venue_with_no_adapter_is_unrouted_not_borrowed(self):
        adapters, unrouted = adapters_for_executors(
            [_ex("carol", "bitget"), _ex(None, "bingx"), _ex("dan", "bybit", {"a": 1})],
            cfg=object())
        assert [(a.account, a.venue_id) for a in adapters] == [("dan", "bybit")]
        assert unrouted == [("carol", "bitget"), ("operator", "bingx")]

    def test_the_sync_client_is_the_same_exchange_with_the_same_keys_and_sandbox(self):
        venue = get_venue("bybit")
        client = vr.sync_client_for(
            venue, object(), {"api_key": "K1", "api_secret": "S1"})
        assert isinstance(client, ccxt.bybit)
        assert (client.apiKey, client.secret) == ("K1", "S1")
        assert client.options.get("defaultType") == "swap"
        assert client.isSandboxModeEnabled is False

    def test_the_sync_client_follows_the_venues_sandbox_switch(self):
        key = "0x" + "11" * 32
        wallet = "0x" + "ab" * 20
        creds = {"wallet_address": wallet, "agent_private_key": key}
        venue = get_venue("hyperliquid")
        test = vr.sync_client_for(venue, SimpleNamespace(hyperliquid_testnet=True), creds)
        main = vr.sync_client_for(venue, SimpleNamespace(hyperliquid_testnet=False), creds)
        assert isinstance(test, ccxt.hyperliquid)
        assert test.isSandboxModeEnabled is True and main.isSandboxModeEnabled is False
        assert test.walletAddress == wallet

    def test_a_venue_refusing_its_credentials_refuses_the_client(self):
        with pytest.raises(RuntimeError):
            vr.sync_client_for(get_venue("bybit"), object(), {"api_key": "", "api_secret": ""})


# ── the wiring: flatten and /accounts ─────────────────────────────────

@pytest.fixture
def router_on():
    prev = {k: getattr(CONFIG, k) for k in
            ("venue_router_enabled", "venue_router_close_residual", "venue_router_timeout_s")}
    object.__setattr__(CONFIG, "venue_router_enabled", True)
    object.__setattr__(CONFIG, "venue_router_close_residual", False)
    object.__setattr__(CONFIG, "venue_router_timeout_s", 2.0)
    try:
        yield
    finally:
        for k, v in prev.items():
            object.__setattr__(CONFIG, k, v)


@pytest.fixture
def live():
    with mock.patch.object(type(CONFIG), "is_live", lambda self: True):
        yield


def _engine(operator, users=()):
    eng = RuneClawEngine.__new__(RuneClawEngine)
    eng.live_executor = operator
    eng._user_executors = {(u.user_id or "x"): u for u in users}
    eng.risk = SimpleNamespace(circuit_breaker_active=False, consecutive_losses=0,
                               live_performance_state=lambda: None,
                               equity_throttle_state=lambda: None)
    eng._user_risk = {}
    eng._user_store = None
    eng.get_live_equity = AsyncMock(return_value={"total": 1000.0})
    eng.get_user_live_equity = AsyncMock(return_value={"total": 100.0})
    return eng


def _clients(mapping):
    """Patch `sync_client_for` so a client is chosen by (venue, user marker)."""
    def fake(venue, cfg, creds):
        marker = (creds or {}).get("marker") if isinstance(creds, dict) else None
        return mapping[(venue.id, marker)]
    return mock.patch.object(vr, "sync_client_for", fake)


class TestFlattenWiring:
    async def test_router_off_adds_no_key_and_builds_nothing(self, live):
        assert CONFIG.venue_router_enabled is False          # the shipped default
        eng = _engine(_ex(None, "bybit"), [_ex("alice", "bybit", {"marker": "a"})])
        boom = AssertionError("router built while off")
        with mock.patch.object(vr, "build_router", side_effect=boom):
            rows = await eng.flatten_all_positions(reason="t")
        assert [set(r) for r in rows] == [{"account", "messages", "ok"}] * 2
        assert all(r["ok"] for r in rows)

    async def test_router_on_marks_a_flat_venue_flat_and_leaves_ok_alone(self, router_on, live):
        eng = _engine(_ex(None, "bybit"))
        with _clients({("bybit", None): FakeClient(positions=[[]])}):
            (row,) = await eng.flatten_all_positions(reason="t")
        assert row["venue_state"] == "flat" and row["venue_open"] == 0
        assert row["ok"] is True and "flat at the venue" in row["venue_note"]

    async def test_a_position_the_venue_still_holds_makes_the_account_not_ok(self, router_on, live):
        eng = _engine(_ex(None, "bybit"))
        with _clients({("bybit", None): FakeClient(positions=[[_row()]])}):
            (row,) = await eng.flatten_all_positions(reason="t")
        assert row["venue_state"] == "open" and row["venue_open"] == 1
        assert row["ok"] is False
        assert "closes FAILED" in flatten_headline([row])

    async def test_a_venue_that_did_not_answer_is_unread_never_flat(self, router_on, live):
        eng = _engine(_ex(None, "bybit"))
        dead = FakeClient(fail={"fetch_positions": ccxt.RequestTimeout("t")})
        with _clients({("bybit", None): dead}):
            (row,) = await eng.flatten_all_positions(reason="t")
        assert row["venue_state"] == "unread" and row["venue_open"] is None
        assert row["ok"] is True                    # the executor's own word stands
        assert "flat" not in row["venue_state"]
        head = flatten_headline([row])
        assert "could not be read back for operator" in head and "NOT verified" in head

    async def test_the_headline_stays_plain_when_the_venue_was_read(self, router_on, live):
        eng = _engine(_ex(None, "bybit"))
        with _clients({("bybit", None): FakeClient(positions=[[]])}):
            rows = await eng.flatten_all_positions(reason="t")
        assert flatten_headline(rows) == "Accounts flattened: 1 of 1"

    async def test_each_account_is_read_on_its_own_venue_with_its_own_keys(self, router_on, live):
        operator_client = FakeClient(positions=[[]])
        alice_client = FakeClient(positions=[[_row(), _row("ETH/USDT:USDT")]])
        bob_client = FakeClient(positions=[[]], sandbox=True)
        eng = _engine(
            _ex(None, "bybit"),
            [_ex("alice", "bybit", {"marker": "alice"}),
             _ex("bob", "hyperliquid", {"marker": "bob"})])
        with _clients({("bybit", None): operator_client,
                       ("bybit", "alice"): alice_client,
                       ("hyperliquid", "bob"): bob_client}):
            rows = await eng.flatten_all_positions(reason="t")
        by = {r["account"]: r for r in rows}
        assert by["operator"]["venue_open"] == 0
        assert by["alice"]["venue_open"] == 2 and by["alice"]["ok"] is False
        assert by["bob"]["venue_open"] == 0 and by["bob"]["venue"] == "hyperliquid"
        # no client was asked twice or asked to close
        for c in (operator_client, alice_client, bob_client):
            assert c.orders() == []

    async def test_a_user_with_no_executor_has_no_row_and_borrows_no_book(self, router_on, live):
        eng = _engine(_ex(None, "bybit"))            # `dave` has no executor
        with _clients({("bybit", None): FakeClient(positions=[[_row()]])}):
            rows = await eng.flatten_all_positions(reason="t")
        assert [r["account"] for r in rows] == ["operator"]

    async def test_a_venue_with_no_adapter_is_said_not_routed_and_borrows_nothing(self, router_on, live):
        eng = _engine(_ex(None, "bybit"), [_ex("carol", "bitget", {"marker": "c"})])
        with _clients({("bybit", None): FakeClient(positions=[[_row()]])}):
            rows = await eng.flatten_all_positions(reason="t")
        carol = next(r for r in rows if r["account"] == "carol")
        assert carol["venue_state"] == "not_routed" and carol["venue_open"] is None
        assert carol["ok"] is True                    # nothing contradicts the executor
        assert "no venue adapter" in carol["venue_note"]

    async def test_a_router_that_cannot_run_leaves_every_account_unread(self, router_on, live):
        eng = _engine(_ex(None, "bybit"), [_ex("alice", "bybit", {"marker": "a"})])
        with mock.patch.object(vr, "build_router", side_effect=RuntimeError("secret detail")):
            rows = await eng.flatten_all_positions(reason="t")
        assert [r["venue_state"] for r in rows] == ["unread", "unread"]
        assert all(r["venue_error"] == "RuntimeError" for r in rows)
        assert "secret detail" not in repr(rows)

    async def test_paper_mode_flattens_nothing_and_asks_no_venue(self, router_on):
        eng = _engine(_ex(None, "bybit"))
        with mock.patch.object(type(CONFIG), "is_live", lambda self: False), \
                mock.patch.object(vr, "build_router", side_effect=AssertionError("asked")):
            assert await eng.flatten_all_positions(reason="t") == []

    async def test_a_residual_is_left_alone_unless_the_operator_opted_in(self, router_on, live):
        client = FakeClient(positions=[[_row()]], sandbox=True)
        eng = _engine(_ex(None, "hyperliquid"))
        with _clients({("hyperliquid", None): client}):
            (row,) = await eng.flatten_all_positions(reason="t")
        assert client.orders() == [] and row["venue_state"] == "open" and row["ok"] is False

    async def test_an_opted_in_residual_is_closed_reduce_only_and_read_back(self, router_on, live):
        object.__setattr__(CONFIG, "venue_router_close_residual", True)
        client = FakeClient(positions=[[_row("BTC/USDC:USDC", mark=50.0)], [_row("BTC/USDC:USDC", mark=50.0)], []],
                            sandbox=True)
        eng = _engine(_ex(None, "hyperliquid"))
        with _clients({("hyperliquid", None): client}):
            (row,) = await eng.flatten_all_positions(reason="admin_closeall")
        (order,) = client.orders()
        assert order[3] == "sell" and order[6] == {"reduceOnly": True}
        assert row["venue_state"] == "flat" and row["ok"] is True
        assert any("close order sent" in m for m in row["venue_closed"])

    async def test_a_residual_that_stays_open_after_the_close_is_still_not_ok(self, router_on, live):
        object.__setattr__(CONFIG, "venue_router_close_residual", True)
        client = FakeClient(positions=[[_row()]], sandbox=False)
        eng = _engine(_ex(None, "bybit"))
        with _clients({("bybit", None): client}):
            (row,) = await eng.flatten_all_positions(reason="t")
        assert len(client.orders()) == 1                      # it was tried
        assert row["venue_state"] == "open" and row["ok"] is False

    async def test_a_refused_residual_close_keeps_the_open_reading_and_the_reason(self, router_on, live):
        object.__setattr__(CONFIG, "venue_router_close_residual", True)
        client = FakeClient(positions=[[_row("BTC/USDC:USDC", mark=50.0)]], sandbox=False)
        eng = _engine(_ex(None, "hyperliquid"))
        with _clients({("hyperliquid", None): client}):
            (row,) = await eng.flatten_all_positions(reason="t")
        assert client.orders() == []                           # mainnet: never signed
        assert row["venue_state"] == "open" and row["ok"] is False
        assert "Close not settled" in row["venue_note"] and "mainnet" in row["venue_note"]

    async def test_a_residual_is_never_closed_on_a_venue_that_read_flat(self, router_on, live):
        object.__setattr__(CONFIG, "venue_router_close_residual", True)
        client = FakeClient(positions=[[]], sandbox=True)
        eng = _engine(_ex(None, "hyperliquid"))
        with _clients({("hyperliquid", None): client}):
            (row,) = await eng.flatten_all_positions(reason="t")
        assert client.orders() == [] and row["venue_state"] == "flat"

    async def test_the_kill_switch_carries_the_same_reading(self, router_on, live):
        eng = _engine(_ex(None, "bybit"))
        eng.risk = SimpleNamespace(emergency_halt=lambda reason: None)
        eng._pending_ideas = {}
        for name in ("_pending_atr", "_pending_timing", "_pending_turn", "_pending_pyramid"):
            setattr(eng, name, {})
        with _clients({("bybit", None): FakeClient(positions=[[_row()]])}):
            out = await eng.emergency_halt_all("t")
        (row,) = out["accounts"]
        assert row["venue_state"] == "open" and row["ok"] is False


class TestAccountsWiring:
    async def test_router_off_adds_no_venue_key(self):
        eng = _engine(_ex(None, "bybit"))
        with mock.patch.object(vr, "build_router", side_effect=AssertionError("built")):
            rows = await eng.account_risk_overview()
        assert not any(k.startswith("venue") for k in rows[0])

    async def test_router_on_puts_each_accounts_own_venue_read_on_its_row(self, router_on):
        eng = _engine(_ex(None, "bybit"), [_ex("alice", "hyperliquid", {"marker": "a"})])
        with _clients({
                ("bybit", None): FakeClient(positions=[[]]),
                ("hyperliquid", "a"): FakeClient(positions=[[_row()]], sandbox=True)}):
            rows = {r["account"]: r for r in await eng.account_risk_overview()}
        assert rows["operator"]["venue_state"] == "flat"
        assert rows["alice"]["venue_state"] == "open" and rows["alice"]["venue_open"] == 1
        assert isinstance(rows["alice"]["venue_latency_ms"], float)
        # the account's other fields are as before
        assert rows["alice"]["equity_usd"] == 100.0 and rows["operator"]["equity_usd"] == 1000.0

    async def test_an_unread_venue_keeps_the_rest_of_the_row(self, router_on):
        eng = _engine(_ex(None, "bybit"))
        dead = FakeClient(fail={"fetch_positions": ccxt.NetworkError("n"),
                                "fetch_balance": ccxt.NetworkError("n"),
                                "fetch_time": ccxt.NetworkError("n")})
        with _clients({("bybit", None): dead}):
            (row,) = await eng.account_risk_overview()
        assert row["venue_state"] == "unread" and row["venue_open"] is None
        assert row["venue_latency_ms"] is None             # absent, never 0
        assert row["equity_usd"] == 1000.0 and row["error"] is None


# ── review findings: the audit, the budget, the log line ──────────────

def _kill_switch_engine(operator, users=()):
    eng = _engine(operator, users)
    eng.risk = SimpleNamespace(emergency_halt=lambda reason: None)
    eng._pending_ideas = {}
    for name in ("_pending_atr", "_pending_timing", "_pending_turn", "_pending_pyramid"):
        setattr(eng, name, {})
    return eng


async def _audited_kill_switch(eng):
    """The ``data`` of the kill-switch's audit row."""
    rows: list[dict] = []
    with mock.patch("bot.core.engine.audit",
                    lambda _log, _msg, **kw: rows.append(kw)):
        await eng.emergency_halt_all("t")
    (row,) = [r for r in rows if r.get("action") == "emergency_halt_all"]
    return row["data"]


class TestKillSwitchAudit:
    async def test_a_flat_the_venue_confirmed_is_counted_flattened(self, router_on, live):
        eng = _kill_switch_engine(_ex(None, "bybit"))
        with _clients({("bybit", None): FakeClient(positions=[[]])}):
            data = await _audited_kill_switch(eng)
        assert data["accounts_flattened"] == 1 and data["accounts_unverified"] == []
        assert data["accounts_failed"] == []

    async def test_a_venue_that_was_never_read_is_not_counted_flattened(self, router_on, live):
        eng = _kill_switch_engine(_ex(None, "bybit"))
        dead = FakeClient(fail={"fetch_positions": ccxt.RequestTimeout("t")})
        with _clients({("bybit", None): dead}):
            data = await _audited_kill_switch(eng)
        assert data["accounts_flattened"] == 0
        assert data["accounts_unverified"] == ["operator"]
        assert data["accounts_failed"] == []              # the executor did say ok

    async def test_a_faulted_read_and_a_venue_with_no_adapter_are_unverified_too(self, router_on, live):
        eng = _kill_switch_engine(
            _ex(None, "bybit"), [_ex("carol", "bitget", {"marker": "c"})])
        broken = FakeClient(fail={"fetch_positions": ValueError("bad")})
        with _clients({("bybit", None): broken}):
            data = await _audited_kill_switch(eng)
        assert data["accounts_flattened"] == 0
        assert sorted(data["accounts_unverified"]) == ["carol", "operator"]

    async def test_with_the_router_off_the_executors_word_is_counted_as_before(self, live):
        assert CONFIG.venue_router_enabled is False
        eng = _kill_switch_engine(_ex(None, "bybit"))
        data = await _audited_kill_switch(eng)
        assert data["accounts_flattened"] == 1 and data["accounts_unverified"] == []


class _SlowOrderClient(FakeClient):
    """Reads at once; a residual close's order hangs."""

    def create_order(self, symbol, type, side, amount, price, params):
        self.calls.append(("create_order", symbol, type, side, amount, price, params))
        time.sleep(1.5)
        return {"id": "late"}


class TestFlattenBudget:
    async def test_a_hung_residual_close_cannot_hold_the_flatten_past_its_budget(self, router_on, live):
        object.__setattr__(CONFIG, "venue_router_close_residual", True)
        object.__setattr__(CONFIG, "venue_router_timeout_s", 0.4)   # budget 0.8s, close 1.2s
        client = _SlowOrderClient(positions=[[_row("BTC/USDC:USDC", mark=50.0)]], sandbox=True)
        eng = _engine(_ex(None, "hyperliquid"))
        started = time.monotonic()
        with _clients({("hyperliquid", None): client}):
            (row,) = await eng.flatten_all_positions(reason="t")
        elapsed = time.monotonic() - started
        assert elapsed < 1.05, elapsed            # unbudgeted, the close alone waits 1.2s
        # The read that said the venue holds a position stands; the close is not claimed.
        assert row["venue_state"] == "open" and row["ok"] is False
        assert "flatten budget" in row["venue_note"]

    async def test_a_close_inside_the_budget_still_settles(self, router_on, live):
        object.__setattr__(CONFIG, "venue_router_close_residual", True)
        object.__setattr__(CONFIG, "venue_router_timeout_s", 0.4)
        client = FakeClient(positions=[[_row("BTC/USDC:USDC", mark=50.0)],
                                       [_row("BTC/USDC:USDC", mark=50.0)], []], sandbox=True)
        eng = _engine(_ex(None, "hyperliquid"))
        with _clients({("hyperliquid", None): client}):
            (row,) = await eng.flatten_all_positions(reason="t")
        assert row["venue_state"] == "flat" and row["ok"] is True
        assert "flatten budget" not in (row.get("venue_note") or "")


class TestLogsCarryNoAccountId:
    async def test_a_users_id_never_reaches_the_router_log_lines(self, router_on, live, caplog):
        import logging
        object.__setattr__(CONFIG, "venue_router_close_residual", True)
        uid = "135792468"
        client = FakeClient(positions=[[_row("BTC/USDC:USDC", mark=50.0)],
                                       [_row("BTC/USDC:USDC", mark=50.0)], []], sandbox=True)
        eng = _engine(_ex(None, "bybit"), [_ex(uid, "hyperliquid", {"marker": "u"})])
        caplog.set_level(logging.INFO, logger="bot.core.venue_router")
        with _clients({("bybit", None): FakeClient(positions=[[]]),
                       ("hyperliquid", "u"): client}):
            await eng.flatten_all_positions(reason="t")
        text = caplog.text
        assert "venue router close sent" in text           # the line exists...
        assert uid not in text                              # ...and carries no id
        assert "user-" in text

    def test_the_tag_is_stable_per_account_distinct_between_accounts_and_keeps_the_role(self):
        assert vr.account_tag("operator") == "operator"
        assert vr.account_tag("135792468") == vr.account_tag("135792468")
        assert vr.account_tag("135792468") != vr.account_tag("135792469")
        assert "135792468" not in vr.account_tag("135792468")
