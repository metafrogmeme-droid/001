"""A limit order's final fill is read AFTER the cancel, or it is not read at all.

`_check_pending_limit` cancels a resting limit order at expiry or on price
drift, then reads the order's FINAL fill to adopt whatever filled while it
rested. When that read raised, it fell back to the snapshot the pass took at
its top, BEFORE the cancel -- "stale by seconds at most, and strictly better
than orphaning" -- and booked the row off it. Driven against the unfixed
tree, with the venue's cancelled order holding a partial fill of 0.03 that
landed after the snapshot:

    snapshot filled 0     -> "LIMIT EXPIRED ... order not filled", closed, pnl 0.0
    snapshot filled 0.02  -> adopted at 0.02, the venue holding 0.03

A fill the venue holds and this record does not is live margin with no stop.
"Strictly better than orphaning" compared the snapshot with the wrong thing:
the order IS cancelled on the venue, so the honest move is to leave the row
pending_fill and let the next pass read the venue's final answer through the
cancelled branch, which adopts any partial with the idea's own levels. The
market fallback one function over already refuses on the same unread read,
in as many words. The read that ANSWERS is unchanged: a stated fill is
adopted, a stated zero is a cancel that filled nothing.
"""
from __future__ import annotations

import asyncio
import dataclasses
import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import ccxt
import pytest

from bot.config import CONFIG
from bot.core import live_executor as le
from bot.core.live_executor import LiveExecutor, LivePosition
from bot.core.venues import get_venue

UTC = timezone.utc
LIMIT = 3990.0


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    async def _now(*a, **k):
        return None
    monkeypatch.setattr(asyncio, "sleep", _now)


@pytest.fixture
def limit_cfg(monkeypatch):
    """A 4h expiry, drift cancel at 2% with the market fallback OFF, so a
    drifted limit is cancelled rather than re-sent as a market order."""
    cfg = dataclasses.replace(CONFIG.limit_orders, expire_seconds=14400,
                              price_drift_cancel_pct=2.0, drift_market_fallback=False)
    object.__setattr__(CONFIG, "limit_orders", cfg)
    yield cfg


class _Venue:
    """A venue whose order reads are a PLANTED sequence: the pass's snapshot
    first, then the read after the cancel, then the next pass's read."""

    def __init__(self, reads, ticker=LIMIT):
        self.reads = list(reads)
        self.ticker = ticker
        self.calls: list[str] = []
        self.cancelled: list[str] = []

    async def fetch_order(self, oid, symbol=None, params=None, **k):
        self.calls.append("fetch_order")
        answer = self.reads.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return dict(answer)

    async def cancel_order(self, oid, symbol=None, params=None, **k):
        self.calls.append("cancel_order")
        self.cancelled.append(oid)
        return {"id": oid}

    async def fetch_ticker(self, symbol, *a, **k):
        self.calls.append("fetch_ticker")
        return {"last": self.ticker}


def _resting(filled=0.0):
    return {"id": "o9", "status": "open", "filled": filled, "average": None,
            "price": LIMIT, "amount": 0.05, "info": {}}


def _cancelled(filled=0.03, average=3989.0):
    return {"id": "o9", "status": "canceled", "filled": filled, "average": average,
            "price": LIMIT, "amount": 0.05, "info": {}}


def _pending(age_h=5.0):
    return LivePosition(
        trade_id="TI-L", symbol="ETH/USDT", direction="LONG", entry_price=LIMIT,
        quantity=0.05, cost_usd=40.0, stop_loss=LIMIT * 0.98, take_profit=LIMIT * 1.06,
        leverage=5, is_spot=False, order_type="limit", limit_order_id="o9",
        opened_at=datetime.now(UTC) - timedelta(hours=age_h), status="pending_fill")


class _Drive:
    def __init__(self, tmp_path, venue):
        self.venue = venue
        self.audits: list[dict] = []
        self.ex = LiveExecutor(state_dir=str(tmp_path))
        self.ex._venue = get_venue("bitget")
        self.ex._hedge_mode = False
        self.ex._exchange = venue
        self.pos = _pending()
        self.ex._positions[self.pos.trade_id] = self.pos

    def one_pass(self):
        async def _sl_tp(*a, **k):
            return ("sl1", "tp1")

        async def _ladder(self_, exchange, pos, direction, qty, sl_id, tp_id, trade_id=None):
            return sl_id, tp_id, None

        with patch.object(le, "audit", lambda log, msg, **kw: self.audits.append(
                {"message": msg, **kw})), \
             patch.object(type(CONFIG), "is_live", return_value=True), \
             patch.object(LiveExecutor, "_place_sl_tp", _sl_tp), \
             patch.object(LiveExecutor, "_reattempt_post_fill_sl", _ladder), \
             patch.object(LiveExecutor, "_guard_fill_leverage", AsyncMock(return_value=None)):
            return asyncio.run(self.ex._check_pending_limit(self.venue, self.pos.trade_id, self.pos))

    def results(self, action):
        return [a.get("result") for a in self.audits if a.get("action") == action]


# ── the read after the cancel raises ────────────────────────────────────────

class TestARaisedReadKeepsTheRowPending:

    @pytest.mark.parametrize("snapshot_filled", [0.0, 0.02], ids=["untouched", "partly filled"])
    def test_nothing_is_booked_off_the_snapshot(self, tmp_path, limit_cfg, caplog, snapshot_filled):
        d = _Drive(tmp_path, _Venue([_resting(snapshot_filled), ccxt.NetworkError("read timed out")]))
        with caplog.at_level(logging.WARNING, logger=le.logger.name):
            msg = d.one_pass()
        assert msg is None
        assert d.venue.cancelled == ["o9"], "the order was cancelled on the venue"
        assert d.pos.status == "pending_fill", "the row is kept for the next pass"
        assert d.pos.quantity == 0.05 and d.pos.pnl_usd is None, "nothing adopted, nothing booked"
        assert d.ex._closed_trades == [], "no closed row for a fill nobody read"
        assert d.results("limit_cancel") == ["FILL_UNREAD"]
        assert d.results("limit_expire") == [] and d.results("partial_fill_adopted") == []
        said = [r for r in caplog.records if "UNREAD" in r.getMessage()]
        assert len(said) == 1 and "NetworkError" in said[0].getMessage()
        assert "read timed out" not in said[0].getMessage(), "the class, never the text"

    def test_the_next_pass_adopts_the_venues_final_fill_with_the_ideas_levels(self, tmp_path, limit_cfg):
        d = _Drive(tmp_path, _Venue([_resting(0.0), ccxt.NetworkError("x"), _cancelled(0.03, 3989.0)]))
        assert d.one_pass() is None
        msg = d.one_pass()
        # the venue's own word for the order, not the reason this bot cancelled it
        assert msg and msg.startswith("LIMIT CANCELED — PARTIAL FILL ADOPTED")
        assert d.pos.status == "open" and d.pos.quantity == 0.03
        assert d.pos.entry_price == 3989.0
        assert (d.pos.stop_loss, d.pos.take_profit) == (LIMIT * 0.98, LIMIT * 1.06)
        assert (d.pos.sl_order_id, d.pos.tp_order_id) == ("sl1", "tp1")
        assert d.venue.cancelled == ["o9"], "the second pass read the cancel; it did not cancel again"
        assert d.results("partial_fill_adopted") == ["OPEN"]

    def test_the_next_pass_closes_a_cancel_that_filled_nothing(self, tmp_path, limit_cfg):
        d = _Drive(tmp_path, _Venue([_resting(0.0), ccxt.NetworkError("x"), _cancelled(0.0, None)]))
        assert d.one_pass() is None
        msg = d.one_pass()
        assert msg and "order not filled" in msg
        assert d.pos.status == "closed" and d.pos.pnl_usd == 0.0
        assert len(d.ex._closed_trades) == 1

    def test_a_drift_cancel_takes_the_same_rule(self, tmp_path, limit_cfg, caplog):
        # price 5% away from the limit, under a 2% drift threshold, age under expiry
        d = _Drive(tmp_path, _Venue([_resting(0.0), ccxt.NetworkError("x")], ticker=LIMIT * 1.05))
        d.pos.opened_at = datetime.now(UTC) - timedelta(minutes=10)
        assert d.one_pass() is None
        assert d.venue.cancelled == ["o9"] and d.pos.status == "pending_fill"
        rows = [a for a in d.audits if a.get("result") == "FILL_UNREAD"]
        assert rows and rows[0]["data"]["cancel_reason"] == "price_drift"
        assert d.results("limit_drift_cancel") == ["CANCELLING"], "cancelled, never CANCELLED"


# ── the read after the cancel answers: unchanged ─────────────────────────────

class TestAnAnsweredReadIsBookedAsItAlwaysWas:

    def test_a_stated_partial_fill_is_adopted_at_once(self, tmp_path, limit_cfg):
        d = _Drive(tmp_path, _Venue([_resting(0.0), _cancelled(0.03, 3989.0)]))
        msg = d.one_pass()
        assert msg and "PARTIAL FILL ADOPTED" in msg
        assert d.pos.status == "open" and d.pos.quantity == 0.03
        assert d.results("partial_fill_adopted") == ["OPEN"]

    def test_a_stated_zero_is_a_cancel_that_filled_nothing(self, tmp_path, limit_cfg):
        d = _Drive(tmp_path, _Venue([_resting(0.0), _cancelled(0.0, None)]))
        msg = d.one_pass()
        assert msg and msg.startswith("LIMIT EXPIRED")
        assert d.pos.status == "closed" and d.results("limit_expire") == ["EXPIRED"]

    def test_the_read_is_made_after_the_cancel(self, tmp_path, limit_cfg):
        d = _Drive(tmp_path, _Venue([_resting(0.0), _cancelled(0.03, 3989.0)]))
        d.one_pass()
        calls = d.venue.calls
        assert calls.index("cancel_order") < len(calls) - 1 - calls[::-1].index("fetch_order"), (
            "the fill that decides the booking is read AFTER the cancel, never before it")


# ── the market fallback's own refusal is the rule this branch took ──────────

def test_the_two_cancel_paths_share_one_reading_of_an_unread_fill():
    """A scan, stated as one: the property is driven above, and this pins that
    the sibling path (`_execute_drift_market_fallback`) still refuses rather
    than reading a raised fill as zero -- the sentence this fix was written
    from, one function over."""
    import inspect

    from tests.source_scan import code_only
    src = code_only(inspect.getsource(LiveExecutor._execute_drift_market_fallback))
    assert "REFUSING the market order (fill unread)" in src
    src2 = code_only(inspect.getsource(LiveExecutor._check_pending_limit))
    assert "using pre-cancel snapshot" not in src2, "the stale-snapshot fallback is back"
    assert 'result="FILL_UNREAD"' in src2
