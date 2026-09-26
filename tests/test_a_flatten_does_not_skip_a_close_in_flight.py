"""A flatten counts a close that is in flight, and never reports it closed early.

`close_all_positions` is what `/closeall`, the `/emergency_stop` kill-switch
and the website's Emergency Stop all call. It closed the rows whose status was
"open" or "pending_fill", and a row the monitor (or a button) was closing at
that moment is "closing". So a book holding only that row answered
"No open positions to close.", which every flatten reader counts as flat: the
web ack went out `ok: True` and the pending row was cleared. When the other
close then failed (H-01 puts the row back to "open"), the position was still
open under an emergency stop that had been acknowledged as done.

It closes the "closing" rows too now: `close_position` waits on the in-flight
close's lock and then answers what is really there. When the in-flight close
finished the row while this one waited, the answer is the row's own, read off
the book, rather than the "not found or already closed" sentence the readers
count as still open.

And an empty book is not a close: `flatten_closed_count` does not count the
empty-book sentence, which the web ack used to report as "1 closed" for an
account that held nothing.

Driven through the real `close_position` with an AsyncMock venue, and through
the real web pump with a real `LiveExecutor` as the operator's book.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import bot.core.engine as eng_mod
import bot.core.live_executor as le
from bot.compat import UTC
from bot.core.engine import RuneClawEngine
from bot.core.live_executor import LiveExecutor, LivePosition
from bot.core.order_state import NOTHING_TO_CLOSE, flatten_outcome
from bot.formatters.drift_offer import flatten_account_ok, flatten_closed_count, flatten_failed_messages

CREDS = {"api_key": "a", "api_secret": "b", "passphrase": "c"}
OPERATOR = "111"


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNECLAW_STATE_DIR", str(tmp_path))
    return tmp_path


def _pos():
    return LivePosition(trade_id="T1", symbol="BTC/USDT", direction="LONG",
                        entry_price=100.0, quantity=1.0, cost_usd=20.0,
                        stop_loss=95.0, take_profit=110.0, leverage=5,
                        status="open", sl_order_id="SL1", tp_order_id="TP1",
                        opened_at=datetime.now(UTC) - timedelta(hours=1))


class _Venue:
    """The first close order parks until released; `fail` makes it raise."""

    def __init__(self):
        self.parked = asyncio.Event()
        self.release = asyncio.Event()
        self.fail = False
        self.orders = 0
        x = AsyncMock()
        x.fetch_ticker = AsyncMock(return_value={"last": 101.0, "bid": 101.0, "ask": 101.0})
        x.fetch_open_orders = AsyncMock(return_value=[])
        x.fetch_positions = AsyncMock(return_value=[])
        x.cancel_order = AsyncMock(return_value={"status": "canceled"})
        x.create_order = self._create
        self.x = x

    async def _create(self, *a, **k):
        self.orders += 1
        if self.orders == 1:
            self.parked.set()
            await self.release.wait()
        if self.fail:
            raise RuntimeError("venue refused the close")
        return {"id": f"C{self.orders}", "average": 101.0, "filled": 1.0,
                "status": "closed"}


def _executor(venue=None):
    ex = LiveExecutor(user_id=OPERATOR, credentials=CREDS, venue="bitget")
    ex._positions["T1"] = _pos()
    if venue is not None:
        ex._get_exchange = AsyncMock(return_value=venue.x)
    return ex


async def _flatten_during_a_close(ex, venue, *, fail: bool):
    first = asyncio.ensure_future(ex.close_position("T1", reason="SL HIT"))
    await asyncio.wait_for(venue.parked.wait(), timeout=5)
    assert ex._positions["T1"].status == "closing"
    flatten = asyncio.ensure_future(ex.close_all_positions(reason="emergency"))
    for _ in range(5):
        await asyncio.sleep(0)
    venue.fail = fail
    venue.release.set()
    await asyncio.wait_for(first, timeout=10)
    return await asyncio.wait_for(flatten, timeout=10)


class TestACloseInFlight:
    @pytest.mark.asyncio
    async def test_a_close_that_then_fails_is_not_read_as_flat(self, state):
        venue = _Venue()
        ex = _executor(venue)
        msgs = await _flatten_during_a_close(ex, venue, fail=True)

        assert msgs != [NOTHING_TO_CLOSE], (
            "the only row was mid-close and the flatten reported an empty book")
        assert flatten_account_ok(msgs) is False, (
            f"a position that is still open was read as flat: {msgs}")
        assert ex._positions["T1"].status != "closed"
        assert venue.orders >= 2, "the flatten sent its own close after waiting"

    @pytest.mark.asyncio
    async def test_a_close_that_then_succeeds_is_counted_as_closed(self, state):
        venue = _Venue()
        ex = _executor(venue)
        msgs = await _flatten_during_a_close(ex, venue, fail=False)

        assert len(msgs) == 1
        assert flatten_outcome(msgs[0]) == "closed", msgs
        assert "already in progress" in msgs[0]
        assert flatten_account_ok(msgs) is True
        assert flatten_closed_count(msgs) == 1
        assert venue.orders == 1, "no second close was sent for a closed row"


    @pytest.mark.asyncio
    async def test_a_closed_row_the_save_could_not_prune_is_counted_closed(
            self, state, monkeypatch):
        # The positions save prunes a closed row only AFTER its write lands,
        # so a write that fails leaves the row in the book as "closed". It is
        # still closed: the book's own status is the reading.
        def unwritable(*a, **k):
            raise OSError("disk full")
        monkeypatch.setattr(le, "atomic_write_json", unwritable)
        venue = _Venue()
        ex = _executor(venue)
        msgs = await _flatten_during_a_close(ex, venue, fail=False)

        assert ex._positions["T1"].status == "closed"
        assert flatten_closed_count(msgs) == 1, msgs
        assert venue.orders == 1

    @pytest.mark.asyncio
    async def test_a_limit_whose_order_was_already_gone_is_counted_closed(self, state):
        # The in-flight cancel finds the order gone and the venue flat, and
        # deletes the row without writing a status: the row is gone from the
        # book while its object still reads "closing".
        ex = LiveExecutor(user_id=OPERATOR, credentials=CREDS, venue="bitget")
        pos = _pos()
        pos.status, pos.limit_order_id = "pending_fill", "L1"
        pos.sl_order_id = pos.tp_order_id = None
        ex._positions["T1"] = pos
        parked, release = asyncio.Event(), asyncio.Event()

        async def cancel(*a, **k):
            parked.set()
            await release.wait()
            raise RuntimeError("25204 Order does not exist")

        x = AsyncMock()
        x.cancel_order = cancel
        x.fetch_positions = AsyncMock(return_value=[])
        ex._get_exchange = AsyncMock(return_value=x)

        first = asyncio.ensure_future(ex.close_position("T1", reason="cancel"))
        await asyncio.wait_for(parked.wait(), timeout=5)
        flatten = asyncio.ensure_future(ex.close_all_positions(reason="emergency"))
        for _ in range(5):
            await asyncio.sleep(0)
        release.set()
        await asyncio.wait_for(first, timeout=10)
        msgs = await asyncio.wait_for(flatten, timeout=10)

        assert "T1" not in ex._positions and pos.status == "closing"
        assert flatten_closed_count(msgs) == 1, msgs

    @pytest.mark.asyncio
    async def test_a_row_this_flatten_could_not_close_keeps_its_own_answer(self, state):
        # The rewrite is only for a row the book now holds as closed or gone.
        ex = _executor()
        ex._positions["T1"].status = "closing"

        async def refused(trade_id, reason=""):
            return f"Position {trade_id} not found or already closed/closing."
        ex.close_position = refused
        msgs = await ex.close_all_positions(reason="emergency")

        assert msgs == ["Position T1 not found or already closed/closing."]
        assert flatten_account_ok(msgs) is False


class TestAnEmptyBookIsNotAClose:
    @pytest.mark.asyncio
    async def test_the_empty_book_answers_one_sentence_counted_as_nothing(self, state):
        ex = LiveExecutor(user_id=OPERATOR, credentials=CREDS, venue="bitget")
        msgs = await ex.close_all_positions(reason="emergency")

        assert msgs == [NOTHING_TO_CLOSE]
        assert flatten_account_ok(msgs) is True
        assert flatten_closed_count(msgs) == 0

    def test_the_count_is_closes_minus_failures(self):
        msgs = ["LONG BTC/USDT closed", "CLOSE FAILED for ETH/USDT: refused",
                "SHORT SOL/USDT closed"]
        assert flatten_failed_messages(msgs) == [msgs[1]]
        assert flatten_closed_count(msgs) == 2
        assert flatten_closed_count([]) == 0
        assert flatten_closed_count(None) == 0


class _Users:
    def get(self, uid):
        return {"role": "admin"}


def _web(monkeypatch, book):
    monkeypatch.setattr("bot.utils.control_pull.fetch_flatten_pending",
                        lambda: [{"user_id": 1, "telegram_id": OPERATOR}])
    acked: dict = {}
    monkeypatch.setattr("bot.utils.control_pull.ack_flatten",
                        lambda acks: acked.update({"acks": acks}))
    monkeypatch.setattr(eng_mod, "audit", lambda *_a, **_k: None)
    eng = SimpleNamespace(live_executor=book, _user_store=_Users(),
                          _last_flatten_pull=0.0, _user_executors={})
    eng._executor_for = RuneClawEngine._executor_for.__get__(eng)
    eng._is_operator_user = lambda uid: str(uid) == OPERATOR
    return eng, acked


class TestTheWebAck:
    @pytest.mark.asyncio
    async def test_a_stop_during_a_close_that_fails_is_not_acked_done(self, state, monkeypatch):
        venue = _Venue()
        ex = _executor(venue)
        eng, acked = _web(monkeypatch, ex)

        first = asyncio.ensure_future(ex.close_position("T1", reason="SL HIT"))
        await asyncio.wait_for(venue.parked.wait(), timeout=5)
        pump = asyncio.ensure_future(RuneClawEngine._maybe_flatten_web_requests(eng))
        for _ in range(5):
            await asyncio.sleep(0)
        venue.fail = True
        venue.release.set()
        await asyncio.wait_for(first, timeout=10)
        await asyncio.wait_for(pump, timeout=10)

        (ack,) = acked["acks"]
        assert ack["ok"] is False, (
            f"the emergency stop was acknowledged over an open position: {ack}")

    @pytest.mark.asyncio
    async def test_an_empty_book_is_acked_with_nothing_closed(self, state, monkeypatch):
        ex = LiveExecutor(user_id=OPERATOR, credentials=CREDS, venue="bitget")
        eng, acked = _web(monkeypatch, ex)
        await RuneClawEngine._maybe_flatten_web_requests(eng)

        assert acked["acks"] == [{"user_id": 1, "ok": True, "closed": 0}], (
            "an account that held nothing was reported as one position closed")
