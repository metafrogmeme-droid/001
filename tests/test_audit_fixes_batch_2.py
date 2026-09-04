"""Audit batch 2: four places that ACTED on a value that was never read.

Batch 1 fixed surfaces that RENDERED an unread value as a number. These four
spent money on one:

EXEC-05  the drift->market fallback read the limit's pre-fill; when the read
         raised it "assumed no partial fill" and marketed the FULL size on top
         of whatever had filled -- up to 2x the approved exposure.
EXEC-04  adoption read the venue's plan (stop) channel; when the read failed
         it looked like "no stops", and the default-placement path then
         cancelled the real stops it never saw and replaced them with 3%/6%.
ERS-01   live sizing read the raw balance dict, whose timestamp can stop
         advancing for hours while the number stays; the per-user path handed
         back a cache that was past the TTL by construction.
ERS-07   a failed spot read returned {}, which the stake path read as "spot
         holds nothing" and moved the whole amount out of futures margin.

Behaviour first; source scans only for the wiring a unit test cannot reach.
"""

from __future__ import annotations

import inspect
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import bot.core.live_executor as live_executor_mod
from bot.core.engine import RuneClawEngine
from bot.core.live_executor import LiveExecutor, LivePosition
from tests.source_scan import code_only

UTC = timezone.utc


# ── ERS-07: yield radar ──────────────────────────────────────────────────

class _Client:
    """Records every request; a response that is an Exception is raised."""

    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def request(self, method, path, body_dict=None, timeout=10):
        self.calls.append((method, path, body_dict))
        for prefix, resp in self.responses.items():
            if path.startswith(prefix):
                if isinstance(resp, Exception):
                    raise resp
                return resp
        return {"code": "40404", "msg": "no fixture"}


_CATALOG = {"code": "00000", "data": [
    {"coin": "USDT", "periodType": "flexible", "productId": "7001",
     "apyList": [{"currentApy": "8.5"}]},
    {"coin": "USDT", "periodType": "fixed", "productId": "8090", "period": "90",
     "apyList": [{"currentApy": "12.0"}]},
]}
_SPOT_OK = {"code": "00000", "data": [{"coin": "USDT", "available": "40"}]}
_OK = {"code": "00000", "data": {"orderId": "1"}}


def _yield_client(spot):
    return _Client({
        "/api/v2/earn/savings/product": _CATALOG,
        "/api/v2/spot/account/assets": spot,
        "/api/v2/spot/wallet/transfer": _OK,
        "/api/v2/earn/savings/subscribe": _OK,
    })


def test_spot_read_that_raises_is_none_not_an_empty_wallet():
    from bot.core.yield_radar import fetch_spot_idle
    assert fetch_spot_idle(_yield_client(RuntimeError("502"))) is None


def test_spot_read_the_venue_refuses_is_none_not_an_empty_wallet():
    from bot.core.yield_radar import fetch_spot_idle
    assert fetch_spot_idle(_yield_client({"code": "40001", "msg": "nope"})) is None


def test_a_spot_wallet_that_really_is_empty_is_still_a_dict():
    from bot.core.yield_radar import fetch_spot_idle
    assert fetch_spot_idle(_yield_client(_SPOT_OK)) == {"USDT": 40.0}
    assert fetch_spot_idle(_yield_client({"code": "00000", "data": []})) == {}


def test_report_names_an_unread_spot_leg_instead_of_counting_it_as_nothing():
    from bot.core.yield_radar import build_report
    rep = build_report(_yield_client(RuntimeError("502")), futures_free_usdt=100.0)
    assert rep.error == ""                      # the report RAN
    assert "Spot balances could not be read" in rep.incomplete
    assert "Spot holdings are complete" not in rep.incomplete
    # The futures leg it DID read is still there, reserve applied.
    usdt = next(r for r in rep.rows if r.coin == "USDT")
    assert usdt.stakeable_usd == pytest.approx(70.0)


def test_report_with_both_legs_unread_says_so_without_contradicting_itself():
    from bot.core.yield_radar import build_report
    rep = build_report(_yield_client(RuntimeError("502")), futures_free_usdt=None)
    assert "Free futures margin could not be read" in rep.incomplete
    assert "Spot balances could not be read" in rep.incomplete
    assert "Spot holdings are complete" not in rep.incomplete
    assert rep.rows == []


def test_report_with_only_futures_unread_keeps_its_exact_wording():
    # Two other suites pin this sentence; the restructure must not move it.
    from bot.core.yield_radar import build_report
    rep = build_report(_yield_client(_SPOT_OK), futures_free_usdt=None)
    assert rep.incomplete == ("Free futures margin could not be read, so it is "
                              "not counted below. Spot holdings are complete.")


def test_stake_moves_nothing_when_spot_is_unread():
    # 100 futures free -> 70 stakeable; spot unread. Before: shortfall = 70 - 0
    # (the {} read as "empty"), so 70 left futures margin on a read that never
    # happened. Now: refused, and no POST reaches the venue.
    from bot.core.yield_radar import execute_stake
    c = _yield_client(RuntimeError("502"))
    res = execute_stake(c, "USDT", futures_free_usdt=100.0)
    assert not res.ok
    assert "could not be read" in res.message
    assert "nothing was moved" in res.message
    assert not any(m == "POST" for m, _p, _b in c.calls), \
        "a refused stake must not touch the account"


def test_fixed_stake_moves_nothing_when_spot_is_unread():
    from bot.core.yield_radar import execute_stake_fixed
    c = _yield_client(RuntimeError("502"))
    res = execute_stake_fixed(c, "USDT", "8090", 90, futures_free_usdt=100.0)
    assert not res.ok
    assert "could not be read" in res.message
    assert not any(m == "POST" for m, _p, _b in c.calls)


def test_stake_still_tops_up_only_the_shortfall_when_spot_is_read():
    # The control: a READ spot balance keeps the pre-existing arithmetic.
    from bot.core.yield_radar import execute_stake
    c = _yield_client(_SPOT_OK)
    res = execute_stake(c, "USDT", futures_free_usdt=100.0)
    assert res.ok, res.message
    transfer = next(b for m, p, b in c.calls if p == "/api/v2/spot/wallet/transfer")
    # 100 + 40 = 140 idle -> 98 stakeable; spot holds 40 -> 58 moves.
    assert transfer["amount"] == "58.00"


# ── EXEC-05: drift -> market fallback ───────────────────────────────────

def _pending_limit(qty=1.0, entry=100.0):
    return LivePosition(
        trade_id="T1", symbol="BTC/USDT:USDT", direction="LONG",
        entry_price=entry, quantity=qty, cost_usd=entry * qty / 5,
        stop_loss=95.0, take_profit=110.0, leverage=5,
        status="pending_fill", limit_order_id="OID1", atr_at_entry=0.0,
        opened_at=datetime.now(UTC) - timedelta(minutes=6))


def _bare_executor(pos):
    ex = LiveExecutor.__new__(LiveExecutor)
    ex._positions = {"T1": pos}
    ex._venue = SimpleNamespace(order_symbol=lambda s: s, futures_params=lambda: {})
    ex._save_positions = lambda: None
    ex._append_closed_trade = lambda p: None
    ex._is_duplicate_fill = lambda p, price: False
    ex._fmt_fill_protection = lambda *a, **k: ""
    ex._place_sl_tp = AsyncMock(return_value=("SL1", "TP1"))
    ex._reattempt_post_fill_sl = AsyncMock(return_value=("SL1", "TP1", None))
    return ex


@pytest.mark.asyncio
async def test_market_fallback_refuses_when_the_pre_fill_cannot_be_read():
    pos = _pending_limit(qty=1.0)
    ex = _bare_executor(pos)
    exchange = MagicMock()
    exchange.cancel_order = AsyncMock()
    exchange.fetch_order = AsyncMock(side_effect=RuntimeError("venue 502"))
    exchange.create_order = AsyncMock(return_value={"average": 101.0, "filled": 1.0})
    with patch.object(live_executor_mod, "audit") as rec:
        msg = await ex._execute_drift_market_fallback(exchange, "T1", pos, 101.0)
    assert msg is None
    exchange.create_order.assert_not_awaited()          # nothing irreversible
    assert pos.status == "pending_fill" and pos.quantity == 1.0
    refused = [c for c in rec.call_args_list
               if c.kwargs.get("action") == "market_fallback"
               and c.kwargs.get("result") == "REFUSED"]
    assert refused, "the refusal must be on the audit trail"
    assert refused[0].kwargs["data"]["reason"] == "pre_fill_unread"


@pytest.mark.asyncio
async def test_market_fallback_with_a_read_pre_fill_still_markets_the_remainder():
    # The control from the QC-1 round, unchanged by the refusal.
    pos = _pending_limit(qty=1.0, entry=100.0)
    ex = _bare_executor(pos)
    exchange = MagicMock()
    exchange.cancel_order = AsyncMock()
    exchange.fetch_order = AsyncMock(return_value={
        "status": "canceled", "filled": 0.4, "average": 99.0})
    exchange.create_order = AsyncMock(return_value={"average": 101.0, "filled": 0.6})
    msg = await ex._execute_drift_market_fallback(exchange, "T1", pos, 101.0)
    assert msg is not None
    assert exchange.create_order.await_args.args[3] == pytest.approx(0.6)


# ── EXEC-04: adoption defers when the stop legs are unread ──────────────

@pytest.fixture
def _isolated_state_files(tmp_path):
    with patch.object(live_executor_mod, "_POSITIONS_FILE",
                      str(tmp_path / "live_positions.json")), \
            patch.object(live_executor_mod, "_CLOSED_TRADES_FILE",
                         str(tmp_path / "closed_trades.json")):
        yield


def _orphan_position(symbol="AMD/USDT:USDT", side="long", contracts=0.74) -> dict:
    # No stopLoss/takeProfit in the position payload, so adoption has to READ
    # the order channels to find out whether the venue holds stops.
    return {
        "symbol": symbol, "side": side, "contracts": contracts,
        "entryPrice": 575.22, "leverage": 10, "initialMargin": 42.57,
        "timestamp": None,
        "info": {"openPriceAvg": "575.22", "totalQty": str(contracts),
                 "margin": "42.57", "leverage": "10"},
    }


def _plan_channel_down():
    async def _fetch_open_orders(symbol, params=None):
        if params:                       # the plan-order query
            raise RuntimeError("plan channel 502")
        return []
    return _fetch_open_orders


@pytest.mark.asyncio
async def test_adoption_is_deferred_when_the_venue_stop_legs_cannot_be_read(
        _isolated_state_files):
    executor = LiveExecutor()
    executor._exchange = AsyncMock()
    executor._exchange.fetch_positions = AsyncMock(return_value=[_orphan_position()])
    executor._exchange.fetch_open_orders = _plan_channel_down()
    executor._place_sl_tp = AsyncMock(return_value=("SL-1", "TP-1"))

    with patch.object(type(live_executor_mod.CONFIG), "is_live", return_value=True), \
            patch.object(live_executor_mod, "audit") as rec:
        adopted = await executor.adopt_exchange_positions()

    assert adopted == []
    assert executor._positions == {}
    # The whole point: no 3%/6% defaults placed over stops nobody looked at.
    executor._place_sl_tp.assert_not_awaited()
    deferred = [c for c in rec.call_args_list
                if c.kwargs.get("action") == "adopt_position"
                and c.kwargs.get("result") == "DEFERRED"]
    assert deferred and deferred[0].kwargs["data"]["reason"] == "plan_orders_unread"
    assert executor._adoption_deferrals == {"AMD": 1}


@pytest.mark.asyncio
async def test_repeated_deferrals_are_counted_and_not_audited_every_tick(
        _isolated_state_files):
    executor = LiveExecutor()
    executor._exchange = AsyncMock()
    executor._exchange.fetch_positions = AsyncMock(return_value=[_orphan_position()])
    executor._exchange.fetch_open_orders = _plan_channel_down()
    executor._place_sl_tp = AsyncMock(return_value=("SL-1", "TP-1"))

    with patch.object(type(live_executor_mod.CONFIG), "is_live", return_value=True), \
            patch.object(live_executor_mod, "audit") as rec:
        for _ in range(12):
            await executor.adopt_exchange_positions()

    assert executor._adoption_deferrals == {"AMD": 12}
    deferred = [c for c in rec.call_args_list if c.kwargs.get("result") == "DEFERRED"]
    assert len(deferred) == 2                 # the 1st and the 10th
    assert [c.kwargs["data"]["consecutive"] for c in deferred] == [1, 10]


@pytest.mark.asyncio
async def test_adoption_proceeds_with_defaults_once_the_stop_legs_are_read(
        _isolated_state_files):
    # The control: both channels answer (empty), so the venue really holds no
    # stops, and the safety defaults are placed exactly as before.
    executor = LiveExecutor()
    executor._exchange = AsyncMock()
    executor._exchange.fetch_positions = AsyncMock(return_value=[_orphan_position()])
    executor._exchange.fetch_open_orders = AsyncMock(return_value=[])
    executor._place_sl_tp = AsyncMock(return_value=("SL-1", "TP-1"))
    executor._adoption_deferrals["AMD"] = 3       # deferred on earlier sweeps

    with patch.object(type(live_executor_mod.CONFIG), "is_live", return_value=True):
        adopted = await executor.adopt_exchange_positions()

    assert adopted == ["AMD"]
    executor._place_sl_tp.assert_awaited_once()
    assert "AMD" not in executor._adoption_deferrals   # streak cleared


def test_the_deferral_sits_between_the_read_and_the_default_placement():
    # Wiring a unit test cannot see: the flag is cleared BEFORE the read, set
    # only AFTER the plan channel answered, and the `continue` fires before
    # need_sl/need_tp compute the defaults.
    code = code_only(inspect.getsource(LiveExecutor.adopt_exchange_positions))
    cleared = code.index("plans_read_ok = False")
    first_read = code.index("fetch_open_orders(raw_sym)")
    plan_read = code.index("plan_order_query_params()")
    set_ok = code.index("plans_read_ok = True")
    gate = code.index("if not plans_read_ok:")
    cont = code.index("continue", gate)
    defaults = code.index("need_sl = lp.stop_loss <= 0")
    assert cleared < first_read < plan_read < set_ok < gate < cont < defaults
    assert 'result="DEFERRED"' in code[gate:defaults]


# ── ERS-01: sizing reads a FRESH balance or none ────────────────────────

class _FakeExec:
    def __init__(self, balance=None, positions=(), raises=False):
        self._balance = balance or {}
        self._raises = raises
        self.open_positions = list(positions)

    async def fetch_balance(self):
        if self._raises:
            raise RuntimeError("balance boom")
        return self._balance


def _bare_engine(cache_age_s):
    """An engine whose shared balance dict holds $10k. `cache_age_s` None means
    the cache was never stamped -- the just-booted shape."""
    eng = RuneClawEngine.__new__(RuneClawEngine)
    eng.live_executor = _FakeExec(balance={"total": 10_000.0})
    eng._live_balance_cache = {"total": 10_000.0}
    eng._live_balance_cache_ts = (0.0 if cache_age_s is None
                                  else time.monotonic() - cache_age_s)
    eng._user_live_balance_cache = {}
    eng._user_live_balance_cache_ts = {}
    eng._LIVE_BALANCE_TTL = 30.0
    eng._is_operator_user = lambda uid: False
    eng._executor_for = lambda uid: eng.live_executor
    return eng


def _live_cfg(per_user=False):
    p = patch("bot.core.engine.CONFIG")
    m = p.start()
    m.is_live.return_value = True
    m.per_user_live_enabled = per_user
    return p


@pytest.mark.asyncio
@pytest.mark.parametrize("age", [None, 3 * 3600.0])
async def test_recheck_sizes_on_none_when_the_shared_balance_is_stale(age):
    # The dict still says $10,000; the venue stopped confirming it hours ago
    # (or never did). Sizing on it is sizing on a number nobody read.
    p = _live_cfg()
    eng = _bare_engine(age)
    with patch("bot.core.engine.get_exchange_position_count",
               new=AsyncMock(return_value=1)):
        try:
            eq, cnt = await eng._live_recheck_context("")
        finally:
            p.stop()
    assert eq is None
    assert cnt == 1


@pytest.mark.asyncio
async def test_recheck_sizes_on_a_fresh_shared_balance():
    p = _live_cfg()
    eng = _bare_engine(5.0)
    with patch("bot.core.engine.get_exchange_position_count",
               new=AsyncMock(return_value=1)):
        try:
            eq, _cnt = await eng._live_recheck_context("")
        finally:
            p.stop()
    assert eq == 10_000.0


def test_the_two_sizing_sites_read_through_the_age_gate():
    # Wiring: the risk-gate call in _analyze_signal and the operator branch of
    # _live_recheck_context go through live_balance_cached(), never the dict.
    for fn in (RuneClawEngine._analyze_signal, RuneClawEngine._live_recheck_context):
        code = code_only(inspect.getsource(fn))
        assert "live_balance_cached()" in code, fn.__name__
        assert '_live_balance_cache.get("total"' not in code, fn.__name__


def test_the_per_user_path_no_longer_hands_back_a_cache_past_its_ttl():
    code = code_only(inspect.getsource(RuneClawEngine.get_user_live_equity))
    assert "return cached if cached else None" not in code
