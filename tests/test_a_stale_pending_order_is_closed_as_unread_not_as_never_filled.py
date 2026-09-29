"""A pending order past its hard timeout is closed as UNREAD, never as never-filled.

`_check_pending_limit` force-closes a pending_fill row that has sat for twice
the normal expiry (8h by default), on the clock alone: best-effort cancel,
then `closed, pnl 0.0, close_reason stale_pending`, without reading the order.
Its own comment names the case it exists for -- "fetch_order keeps failing" --
which is exactly the case in which the fill is unknown, and a limit that
filled while its reads were failing was booked as an order that never filled,
its position open on the venue with no stop under a record saying no money
ever moved. And the branch fired on a READABLE order too: an order the
venue answered for at 8h, resting because its cancel kept being refused,
was closed off the record while it still rested.

Driven against the unfixed tree:

    reads raise for 9h, venue holds the fill  -> "STALE PENDING CLOSED", pnl 0.0,
                                                 nothing says the fill was unread
    order READS at 9h, open, cancel refused   -> force-closed; the order rests untracked
    order READS at 9h, open, cancel lands,
      final fill 0.03                         -> force-closed as stale_pending; the
                                                 0.03 never adopted

The read comes first now. The hard timeout closes only what cannot be read
(no order id, or a read that raised), books `stale_pending` with
`fill_source = FINAL_FILL_UNREAD`, and says so; an order that reads goes
through the normal expiry flow whatever its age.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import ccxt
import pytest

from bot.config import CONFIG
from bot.core import live_executor as le
from bot.core.live_executor import FINAL_FILL_UNREAD, LiveExecutor, LivePosition
from bot.core.venues import get_venue
from bot.utils.close_reason import NON_FILL_CLOSE_REASONS, is_filled_close

UTC = timezone.utc
LIMIT = 3990.0
SECRET = "apiKey=SECRETVALUE"


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    async def _now(*a, **k):
        return None
    monkeypatch.setattr(asyncio, "sleep", _now)


@pytest.fixture
def limit_cfg():
    """A 4h expiry (8h hard timeout), no drift cancel, no market fallback."""
    old = CONFIG.limit_orders
    cfg = dataclasses.replace(old, expire_seconds=14400,
                              price_drift_cancel_pct=0.0, drift_market_fallback=False)
    # A frozen config, so this write is outside monkeypatch's bookkeeping.
    # The first draft never handed the object back, and a drift band of ZERO
    # then cancelled every resting limit the backtest suites placed for the
    # rest of the session -- five tests forgiven as "flaky" by the full gate
    # until the leak probe named this fixture. Restored in a finally.
    object.__setattr__(CONFIG, "limit_orders", cfg)
    try:
        yield cfg
    finally:
        object.__setattr__(CONFIG, "limit_orders", old)


class _Venue:
    """Order reads are a PLANTED sequence; an exception in it is raised."""

    def __init__(self, reads, cancel_raises=None):
        self.reads = list(reads)
        self.cancel_raises = cancel_raises
        self.calls: list[str] = []
        self.cancelled: list[str] = []

    async def fetch_order(self, oid, symbol=None, params=None, **k):
        self.calls.append("fetch_order")
        answer = self.reads.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return dict(answer) if answer is not None else None

    async def cancel_order(self, oid, symbol=None, params=None, **k):
        self.calls.append("cancel_order")
        if self.cancel_raises is not None:
            raise self.cancel_raises
        self.cancelled.append(oid)
        return {"id": oid}


def _resting(filled=0.0):
    return {"id": "o9", "status": "open", "filled": filled, "average": None,
            "price": LIMIT, "amount": 0.05, "info": {}}


def _cancelled(filled=0.03, average=3989.0):
    return {"id": "o9", "status": "canceled", "filled": filled, "average": average,
            "price": LIMIT, "amount": 0.05, "info": {}}


def _pending(age_h=9.0, oid="o9"):
    return LivePosition(
        trade_id="TI-L", symbol="ETH/USDT", direction="LONG", entry_price=LIMIT,
        quantity=0.05, cost_usd=40.0, stop_loss=LIMIT * 0.98, take_profit=LIMIT * 1.06,
        leverage=5, is_spot=False, order_type="limit", limit_order_id=oid,
        opened_at=datetime.now(UTC) - timedelta(hours=age_h), status="pending_fill")


class _Risk:
    def __init__(self):
        self.warnings: list[str] = []

    def record_warning(self, key):
        self.warnings.append(key)


class _Drive:
    def __init__(self, tmp_path, venue, age_h=9.0, oid="o9"):
        self.venue = venue
        self.audits: list[dict] = []
        self.ex = LiveExecutor(state_dir=str(tmp_path))
        self.ex._venue = get_venue("bitget")
        self.ex._hedge_mode = False
        self.ex._exchange = venue
        self.risk = _Risk()
        self.ex._risk_engine = self.risk
        self.pos = _pending(age_h, oid)
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

    def rows(self, action):
        return [a for a in self.audits if a.get("action") == action]


# ── what cannot be read past the hard timeout is closed as UNREAD ──────────

class TestAnUnreadableOrderIsClosedAsUnread:

    def test_a_read_that_raises_past_the_timeout_closes_the_row_and_says_the_fill_was_never_read(
            self, tmp_path, limit_cfg, caplog):
        d = _Drive(tmp_path, _Venue([ccxt.NetworkError(SECRET)]))
        with caplog.at_level(logging.WARNING, logger=le.logger.name):
            msg = d.one_pass()
        assert msg and msg.startswith("STALE PENDING CLOSED: LONG ETH/USDT")
        assert "NEVER READ" in msg and "NetworkError" in msg
        assert "may still hold a resting order or a filled position" in msg
        assert d.pos.status == "closed" and d.pos.close_reason == "stale_pending"
        assert d.pos.pnl_usd == 0.0, "nothing is booked as a fill"
        assert d.pos.fill_source == FINAL_FILL_UNREAD
        assert d.venue.cancelled == ["o9"], "the best-effort cancel is still sent"
        [row] = d.rows("stale_pending_close")
        assert row["result"] == "FORCE_CLOSED"
        assert row["data"]["fill_read"] is False
        assert row["data"]["cause"] == "order_unreadable"
        assert row["data"]["error"] == "NetworkError"
        assert row.get("level") == logging.WARNING
        assert d.risk.warnings == ["stale_pending_unread"]

    def test_the_venues_text_reaches_no_log_and_no_card(self, tmp_path, limit_cfg, caplog):
        d = _Drive(tmp_path, _Venue([ccxt.ExchangeError(SECRET)], cancel_raises=ccxt.ExchangeError(SECRET)))
        with caplog.at_level(logging.WARNING, logger=le.logger.name):
            msg = d.one_pass()
        assert "SECRETVALUE" not in msg
        assert "SECRETVALUE" not in caplog.text
        assert "ExchangeError" in caplog.text, "the class is said"
        assert d.pos.status == "closed", "a refused cancel does not keep the row"

    def test_no_order_id_past_the_timeout_names_that_cause_and_cancels_nothing(self, tmp_path, limit_cfg):
        d = _Drive(tmp_path, _Venue([]), oid=None)
        msg = d.one_pass()
        assert msg and "NEVER READ" in msg and "no order id is on record" in msg
        assert d.venue.calls == [], "nothing to read, nothing to cancel"
        assert d.pos.close_reason == "stale_pending" and d.pos.fill_source == FINAL_FILL_UNREAD
        [row] = d.rows("stale_pending_close")
        assert row["data"]["cause"] == "no_order_id" and row["data"]["error"] is None

    def test_the_closed_row_on_disk_carries_the_word_and_is_a_non_fill(self, tmp_path, limit_cfg):
        d = _Drive(tmp_path, _Venue([ccxt.NetworkError("x")]))
        d.one_pass()
        [rec] = json.loads((tmp_path / "closed_trades.json").read_text())
        assert rec["close_reason"] == "stale_pending" and rec["fill_source"] == FINAL_FILL_UNREAD
        assert "stale_pending" in NON_FILL_CLOSE_REASONS
        assert not is_filled_close(rec["close_reason"], rec["pnl_usd"]), (
            "the record's readers count it as no trade")

    def test_a_read_that_raises_before_the_timeout_keeps_the_row_and_says_the_class(
            self, tmp_path, limit_cfg, caplog):
        d = _Drive(tmp_path, _Venue([ccxt.NetworkError(SECRET)]), age_h=5.0)
        with caplog.at_level(logging.WARNING, logger=le.logger.name):
            msg = d.one_pass()
        assert msg is None
        assert d.pos.status == "pending_fill" and d.pos.close_reason is None
        assert d.venue.cancelled == [], "nothing cancelled on a read that failed"
        assert d.rows("stale_pending_close") == []
        [said] = [r for r in caplog.records if "could not be read" in r.getMessage()]
        assert "NetworkError" in said.getMessage() and "SECRETVALUE" not in said.getMessage()

    def test_a_read_that_answers_nothing_is_unread_too(self, tmp_path, limit_cfg):
        """A venue stub answering None is not an order; past the timeout it is
        the unreadable case with no exception class to name."""
        d = _Drive(tmp_path, _Venue([None]))
        msg = d.one_pass()
        assert msg and "NEVER READ" in msg
        [row] = d.rows("stale_pending_close")
        assert row["data"]["cause"] == "order_unreadable" and row["data"]["error"] is None


# ── an order that READS is never force-closed, whatever its age ───────────

class TestAReadableOrderTakesTheNormalFlow:

    def test_a_resting_order_at_9h_is_expired_and_its_final_fill_adopted(self, tmp_path, limit_cfg):
        d = _Drive(tmp_path, _Venue([_resting(0.0), _cancelled(0.03, 3989.0)]))
        msg = d.one_pass()
        assert msg and msg.startswith("LIMIT EXPIRED — PARTIAL FILL ADOPTED")
        assert d.pos.status == "open" and d.pos.quantity == 0.03
        assert d.pos.entry_price == 3989.0
        assert (d.pos.stop_loss, d.pos.take_profit) == (LIMIT * 0.98, LIMIT * 1.06)
        assert d.venue.cancelled == ["o9"]
        assert d.rows("stale_pending_close") == []
        assert d.pos.fill_source != FINAL_FILL_UNREAD

    def test_a_resting_order_at_9h_that_filled_nothing_is_closed_as_expired(self, tmp_path, limit_cfg):
        d = _Drive(tmp_path, _Venue([_resting(0.0), _cancelled(0.0, None)]))
        msg = d.one_pass()
        assert msg and msg.startswith("LIMIT EXPIRED:")
        assert d.pos.close_reason == "expired"
        assert d.rows("stale_pending_close") == []

    def test_a_cancelled_order_at_9h_is_the_venues_cancel_not_a_stale_close(self, tmp_path, limit_cfg):
        d = _Drive(tmp_path, _Venue([_cancelled(0.0, None)]))
        msg = d.one_pass()
        assert msg and msg.startswith("LIMIT CANCELED:")
        assert d.pos.close_reason == "canceled"
        assert d.venue.cancelled == []

    def test_a_filled_order_at_9h_is_a_fill(self, tmp_path, limit_cfg):
        filled = {"id": "o9", "status": "closed", "filled": 0.05, "average": 3989.0,
                  "price": LIMIT, "amount": 0.05, "info": {}}
        d = _Drive(tmp_path, _Venue([filled]))
        with patch.object(LiveExecutor, "sync_positions_from_exchange", AsyncMock(return_value=None)):
            msg = d.one_pass()
        assert msg and msg.startswith("LIMIT FILLED")
        assert d.pos.status == "open" and d.rows("stale_pending_close") == []

    def test_a_cancel_the_venue_keeps_refusing_keeps_the_row_tracked(self, tmp_path, limit_cfg, caplog):
        """The behaviour that changed: the old branch closed this record at 8h
        over an order the venue still held. It rests, so the record stays."""
        d = _Drive(tmp_path, _Venue([_resting(0.0), _resting(0.0)] * 3,
                                    cancel_raises=ccxt.ExchangeError("cancel refused")))
        with caplog.at_level(logging.WARNING, logger=le.logger.name):
            first = d.one_pass()
            second = d.one_pass()
        assert first is None and second is None
        assert d.pos.status == "pending_fill"
        assert d.ex._closed_trades == []
        assert d.rows("stale_pending_close") == []
        assert sum("still open after cancel attempt" in r.getMessage()
                   for r in caplog.records) == 2, "said on every pass"


# ── the reading is one, and it is before the clock ────────────────────────

def test_the_hard_timeout_is_decided_after_the_read_not_before_it():
    """A scan, stated as one: the drives above prove a readable order is not
    force-closed, and this pins the SHAPE that makes it so -- the one read
    sits above the hard-timeout branch, and no clock check sits above the
    read."""
    import inspect

    from tests.source_scan import code_only
    src = code_only(inspect.getsource(LiveExecutor._check_pending_limit))
    read = src.index("order = await self._fetch_order(exchange, pos.limit_order_id, pos.symbol)")
    branch = src.index("_force_close_stale_pending(")
    assert read < branch
    assert "stale_age > hard_timeout" not in src[:read]
    assert src.count("_force_close_stale_pending(") == 1


def test_the_fixture_hands_the_limit_config_back():
    """The fixture writes a frozen config outside monkeypatch's bookkeeping,
    so its restore is asserted rather than assumed: driven as the generator
    pytest drives, through its finally. `gen.close()` throws GeneratorExit at
    the yield, which is exactly what skips a restore written without one."""
    before = CONFIG.limit_orders
    gen = limit_cfg.__wrapped__()
    next(gen)
    assert CONFIG.limit_orders is not before
    gen.close()
    assert CONFIG.limit_orders is before
