"""An adopted limit order records its leverage and margin as UNREAD, not guessed.

The venue states no leverage for an unfilled order (Bitget UTA has no
GET-leverage for one), and `adopt_exchange_limit_orders` wrote
``CONFIG.exchange.default_leverage`` in anyway, then ``margin = notional /
leverage`` -- under a docstring saying "Uses real exchange data only". Driven
on the unfixed tree, an external SOL limit buy of 10 @ 140 was recorded
``leverage=5 cost_usd=280.0``, and `committed_margin` scored it 1 of 1: a
config guess summed by the exposure cap as the order's committed margin.

It records ``leverage=0``, ``cost_usd=0.0`` and names both in
``adoption_unread``, the way position adoption records what the venue did not
state. Two readers had to learn the spelling:

  * the three fill paths computed ``raw_cost / pos.leverage if pos.leverage > 1
    else raw_cost``, so a leverage of 0 would have put the whole NOTIONAL into
    ``cost_usd`` -- `margin_at_fill` keeps it unread until the post-fill sync
    reads the venue's;
  * the fill guards read the recorded leverage as the APPROVED one.
    `_intended_fill_leverage` is one reading for all three: an adopted order
    has none (the guard's verdict is "unknown" and the position is kept), and
    a reclaimed order -- the bot's own, whose approved leverage a restart lost
    -- is checked against the standard leverage the executor sets for the
    symbol, the ceiling every placement starts from.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.core import live_executor as le
from bot.core.live_executor import (
    LiveExecutor,
    committed_margin,
    margin_at_fill,
    position_size_basis,
)
from bot.core.venues import get_venue

UTC = timezone.utc


def _order(client_oid="app-1", **kw):
    o = {"id": "9981234567", "symbol": "SOL/USDT:USDT", "type": "limit", "side": "buy",
         "price": 140.0, "amount": 10.0,
         "timestamp": int((datetime.now(UTC) - timedelta(hours=5)).timestamp() * 1000),
         "datetime": "", "info": {"clientOid": client_oid, "marginMode": "crossed"}}
    o.update(kw)
    return o


@pytest.fixture
def live(monkeypatch):
    from bot.config import CONFIG
    monkeypatch.setattr(type(CONFIG), "is_live", lambda self: True)


def _adopt(orders, audit_log=None, monkeypatch=None):
    ex = LiveExecutor()
    ex._venue = get_venue("bitget")
    ex._save_positions = MagicMock()
    x = AsyncMock()
    x.fetch_open_orders = AsyncMock(return_value=list(orders))
    ex._get_exchange = AsyncMock(return_value=x)
    if audit_log is not None:
        monkeypatch.setattr(le, "audit", lambda log, msg, **kw: audit_log.append((msg, kw)))
    out = asyncio.run(ex.adopt_exchange_limit_orders())
    return ex, x, out


class TestTheRecord:
    @pytest.mark.parametrize("client_oid,origin", [("app-1", "adopted"),
                                                   ("rcTIf6798581", "reclaimed")])
    def test_leverage_and_margin_are_unread_not_the_config_default(self, live, client_oid, origin):
        ex, _x, _out = _adopt([_order(client_oid=client_oid)])
        (pos,) = ex._positions.values()
        assert pos.origin == origin and pos.status == "pending_fill"
        assert pos.leverage == 0 and pos.cost_usd == 0.0, (
            f"a guessed {pos.leverage}x and ${pos.cost_usd} margin went on the record")
        assert pos.adoption_unread == ("margin", "leverage")
        assert position_size_basis(pos) == (None, 1400.0)

    def test_the_exposure_reading_counts_it_as_unread(self, live):
        ex, _x, _out = _adopt([_order()])
        cm = committed_margin(list(ex._positions.values()))
        assert cm.total is None and (cm.scored, cm.counted) == (0, 1)

    def test_the_marker_survives_a_restart(self, live, tmp_path):
        ex, _x, _out = _adopt([_order()])
        (pos,) = ex._positions.values()
        saver = LiveExecutor(state_dir=str(tmp_path))
        saver._positions = {pos.trade_id: pos}
        saver._save_positions()
        again = LiveExecutor(state_dir=str(tmp_path))
        (back,) = again._positions.values()
        assert back.leverage == 0 and back.cost_usd == 0.0
        assert tuple(back.adoption_unread) == ("margin", "leverage")

    def test_the_audit_says_unread_and_quotes_no_figure(self, live, monkeypatch):
        said: list = []
        _adopt([_order()], audit_log=said, monkeypatch=monkeypatch)
        rows = [(m, kw) for m, kw in said if kw.get("action") == "adopt_limit_order"]
        assert len(rows) == 1
        msg, kw = rows[0]
        assert "leverage and margin unread" in msg
        assert "lev=" not in msg and "margin=$" not in msg
        assert kw["data"]["leverage"] is None and kw["data"]["margin"] is None
        assert kw["data"]["unread"] == ["margin", "leverage"]


class TestTheMarginAtAFill:
    @pytest.mark.parametrize("lev,expected", [(10, 140.0), (1, 1400.0), (0, 0.0),
                                              (None, 0.0), ("junk", 0.0), (-3, 0.0),
                                              (float("nan"), 0.0)])
    def test_a_leverage_not_on_record_leaves_the_margin_unread(self, lev, expected):
        assert margin_at_fill(1400.0, lev) == pytest.approx(expected)

    def test_a_fill_of_an_adopted_order_does_not_record_the_notional_as_margin(self, live):
        ex, x, _out = _adopt([_order()])
        (pos,) = ex._positions.values()
        x.fetch_order = AsyncMock(return_value={"id": "9981234567", "status": "closed",
                                                "filled": 10.0, "average": 140.0})
        ex._place_sl_tp = AsyncMock(return_value=("sl", "tp"))
        ex._reattempt_post_fill_sl = AsyncMock(return_value=("sl", "tp", None))
        ex.sync_positions_from_exchange = AsyncMock(side_effect=RuntimeError("unread"))
        ex._guard_fill_leverage = AsyncMock(return_value=None)
        asyncio.run(ex._check_pending_limit(x, pos.trade_id, pos))
        assert pos.status == "open"
        assert pos.cost_usd == 0.0, (
            f"the notional ${pos.cost_usd} was written as the margin")
        assert position_size_basis(pos)[0] is None
        # ...and the guard was handed no approved leverage for it.
        assert ex._guard_fill_leverage.await_args.args[3] == 0

    def test_a_reclaimed_fill_is_checked_against_the_bots_ceiling(self, live):
        """The limit-fill path's own guard, for the bot's OWN order: an
        adopted order and a raw read of the record both answer 0 here, so only
        a reclaimed fill tells the one reading from the field."""
        ex, x, _out = _adopt([_order(client_oid="rcTIf6798581")])
        (pos,) = ex._positions.values()
        ex._standard_leverage = lambda symbol: 7
        x.fetch_order = AsyncMock(return_value={"id": "9981234567", "status": "closed",
                                                "filled": 10.0, "average": 140.0})
        ex._place_sl_tp = AsyncMock(return_value=("sl", "tp"))
        ex._reattempt_post_fill_sl = AsyncMock(return_value=("sl", "tp", None))
        ex.sync_positions_from_exchange = AsyncMock(side_effect=RuntimeError("unread"))
        ex._guard_fill_leverage = AsyncMock(return_value=None)
        asyncio.run(ex._check_pending_limit(x, pos.trade_id, pos))
        assert ex._guard_fill_leverage.await_args.args[3] == 7


class TestTheIntendedLeverage:
    def _ex(self, standard=7):
        ex = LiveExecutor()
        ex._standard_leverage = lambda symbol: standard
        return ex

    def _pos(self, origin, leverage):
        return le.LivePosition(trade_id="T", symbol="SOL/USDT:USDT", direction="LONG",
                               entry_price=140.0, quantity=10.0, cost_usd=0.0,
                               stop_loss=130.0, take_profit=160.0, leverage=leverage,
                               origin=origin, status="pending_fill")

    @pytest.mark.parametrize("origin,lev,expected", [
        ("adopted", 0, 0), ("adopted", 5, 0),
        ("reclaimed", 0, 7), ("reclaimed", 3, 3),
        ("executed", 5, 5), ("executed", 0, 0)])
    def test_one_reading(self, origin, lev, expected):
        assert self._ex()._intended_fill_leverage(self._pos(origin, lev)) == expected

    def test_a_reclaimed_fill_that_overshoots_the_bots_ceiling_is_still_flattened(self):
        """The guard keeps its teeth for the bot's own orders: the verdict
        against the ceiling is `close`, not `unknown`."""
        from bot.core.live_executor import leverage_overshoot_verdict
        ceiling = self._ex(standard=5)._intended_fill_leverage(self._pos("reclaimed", 0))
        assert leverage_overshoot_verdict(ceiling, 20, 1.5)["decision"] == "close"

    @pytest.mark.parametrize("origin,expected", [("adopted", 0), ("reclaimed", 7)])
    def test_the_partial_fill_guard_reads_it(self, origin, expected):
        ex = self._ex()
        ex._venue = get_venue("bitget")
        ex._save_positions = MagicMock()
        ex._place_sl_tp = AsyncMock(return_value=("sl", "tp"))
        ex._reattempt_post_fill_sl = AsyncMock(return_value=("sl", "tp", None))
        ex._guard_fill_leverage = AsyncMock(return_value="stop here")
        pos = self._pos(origin, 0)
        asyncio.run(ex._adopt_partial_fill(AsyncMock(), "T", pos, 4.0, 140.0, "cancel"))
        assert ex._guard_fill_leverage.await_args.args[3] == expected
        assert pos.cost_usd == 0.0

    @pytest.mark.parametrize("origin,expected", [("adopted", 0), ("reclaimed", 7)])
    def test_the_drift_fallback_guard_reads_it(self, origin, expected):
        """The third fill guard, driven the way the partial-fill suite drives
        the fallback: a market order for the remainder, then the guard."""
        ex = LiveExecutor.__new__(LiveExecutor)
        ex._standard_leverage = lambda symbol: 7
        pos = self._pos(origin, 0)
        pos.limit_order_id = "OID1"
        ex._positions = {"T": pos}
        # The real venue, as the partial-fill case above uses. A stand-in that
        # listed two attributes lost the drift fallback's pre-fill read when
        # that read began asking the venue for its order-read params, and the
        # fallback then refused the market order before the guard was reached.
        ex._venue = get_venue("bitget")
        ex._save_positions = lambda: None
        ex._place_sl_tp = AsyncMock(return_value=("SL1", "TP1"))
        ex._reattempt_post_fill_sl = AsyncMock(return_value=("SL1", "TP1", None))
        ex._guard_fill_leverage = AsyncMock(return_value="stop here")
        exchange = MagicMock()
        exchange.cancel_order = AsyncMock()
        exchange.fetch_order = AsyncMock(return_value={"status": "canceled", "filled": 0.0})
        exchange.create_order = AsyncMock(return_value={"average": 141.0, "filled": 10.0})
        asyncio.run(ex._execute_drift_market_fallback(exchange, "T", pos, 141.0))
        assert ex._guard_fill_leverage.await_args.args[3] == expected
        assert pos.cost_usd == 0.0
