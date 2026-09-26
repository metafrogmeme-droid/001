"""A field the venue has since stated is not "not stated" any more.

Adoption records each field the venue did not state in ``adoption_unread``,
and the chat model's evidence row reads that list back as "the venue did not
state margin, leverage at adoption -- do not estimate them". The leverage sync
reads the venue's leverage onto every open Bitget position at boot and every
five minutes, and derives the margin from it when the entry is on record. It
never took either name off the list, so one row told the model:

    margin $30.00, lev 20x, ... the venue did not state margin, leverage
    at adoption -- do not estimate them

Two claims about one field, and the one telling the model not to use the
figure was the false one.

Beside it, the filled-during-cancel path divided by ``pos.leverage or 1``. An
adopted limit order records leverage 0, so the NOTIONAL was written as its
margin, under a marker still saying the margin was never stated.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.core import live_executor as le
from bot.core.live_executor import LiveExecutor, LivePosition, clear_unread
from bot.skills.telegram_handler import _live_position_row


def _executor(tmp_path) -> LiveExecutor:
    return LiveExecutor(user_id="u1",
                        credentials={"api_key": "k", "api_secret": "s", "passphrase": "p"},
                        venue="bitget", state_dir=str(tmp_path))


def _adopted(*, entry=60000.0, leverage=0, cost=0.0, unread=("margin", "leverage"),
             status="open", tid="T1") -> LivePosition:
    pos = LivePosition(trade_id=tid, symbol="BTC/USDT", direction="LONG",
                       entry_price=entry, quantity=0.01, cost_usd=cost, stop_loss=0,
                       take_profit=0, leverage=leverage, opened_at=datetime.now(UTC),
                       status=status, origin="adopted")
    setattr(pos, "adoption_unread", tuple(unread))
    return pos


def _sync(ex: LiveExecutor, leverage: str) -> None:
    rows = [{"symbol": "BTCUSDT", "leverage": leverage, "total": "0.01"}]
    with patch.object(LiveExecutor, "_fetch_v3_positions_raw",
                      staticmethod(lambda creds, venue: rows)):
        asyncio.run(ex.sync_positions_from_exchange())


NOT_STATED = "did not state"


# ── the helper ────────────────────────────────────────────────────────────

def test_it_drops_only_the_names_it_is_given_and_keeps_the_order():
    # Not alphabetical, so a sort would show: the row prints the list as is.
    pos = _adopted(unread=("opened_at", "margin", "leverage", "entry_price"))
    clear_unread(pos, "leverage", "margin")
    assert pos.adoption_unread == ("opened_at", "entry_price")


def test_a_name_that_is_not_on_the_list_changes_nothing():
    pos = _adopted(unread=("margin",))
    clear_unread(pos, "leverage")
    assert pos.adoption_unread == ("margin",)


def test_a_position_nobody_adopted_is_not_given_a_marker():
    pos = LivePosition(trade_id="B1", symbol="BTC/USDT", direction="LONG",
                       entry_price=60000.0, quantity=0.01, cost_usd=30.0, stop_loss=59000,
                       take_profit=62000, leverage=20, opened_at=datetime.now(UTC),
                       status="open")
    clear_unread(pos, "leverage")
    assert not hasattr(pos, "adoption_unread")


# ── the leverage sync ─────────────────────────────────────────────────────

def test_the_sync_clears_the_leverage_and_the_margin_it_wrote(tmp_path):
    ex = _executor(tmp_path)
    pos = _adopted()
    ex._positions[pos.trade_id] = pos
    before = _live_position_row(pos, 61000.0)
    assert NOT_STATED in before and "margin, leverage" in before
    _sync(ex, "20")
    assert (pos.leverage, pos.cost_usd) == (20, 30.0)
    assert pos.adoption_unread == ()
    row = _live_position_row(pos, 61000.0)
    assert "margin $30.00" in row and "lev 20x" in row
    assert NOT_STATED not in row


def test_an_entry_the_venue_never_stated_stays_named_and_no_margin_is_derived(tmp_path):
    ex = _executor(tmp_path)
    pos = _adopted(entry=0.0, unread=("entry_price", "margin", "leverage"))
    ex._positions[pos.trade_id] = pos
    _sync(ex, "20")
    assert pos.leverage == 20 and pos.cost_usd == 0.0
    # The leverage was read; the margin could not be derived from it.
    assert pos.adoption_unread == ("entry_price", "margin")
    row = _live_position_row(pos, 61000.0)
    assert "did not state entry_price, margin at adoption" in row


def test_an_unparseable_leverage_clears_nothing(tmp_path):
    ex = _executor(tmp_path)
    pos = _adopted()
    ex._positions[pos.trade_id] = pos
    _sync(ex, "n/a")
    assert pos.leverage == 0
    assert pos.adoption_unread == ("margin", "leverage")


def test_a_leverage_already_on_record_leaves_the_unread_margin_named(tmp_path):
    # Read at adoption; the sync sees no change and writes nothing, so the
    # margin is still one nobody stated.
    ex = _executor(tmp_path)
    pos = _adopted(leverage=20, unread=("margin",))
    ex._positions[pos.trade_id] = pos
    _sync(ex, "20")
    assert pos.cost_usd == 0.0
    assert pos.adoption_unread == ("margin",)


def test_the_cleared_marker_survives_a_restart(tmp_path):
    ex = _executor(tmp_path)
    pos = _adopted(entry=0.0, unread=("entry_price", "margin", "leverage"))
    ex._positions[pos.trade_id] = pos
    _sync(ex, "20")
    again = _executor(tmp_path)
    restored = again._positions[pos.trade_id]
    assert restored.leverage == 20
    assert restored.adoption_unread == ("entry_price", "margin")


def test_a_marker_emptied_by_the_sync_restores_as_none(tmp_path):
    ex = _executor(tmp_path)
    pos = _adopted()
    ex._positions[pos.trade_id] = pos
    _sync(ex, "20")
    restored = _executor(tmp_path)._positions[pos.trade_id]
    assert tuple(getattr(restored, "adoption_unread", ()) or ()) == ()
    assert NOT_STATED not in _live_position_row(restored, 61000.0)


# ── the close reconcile ───────────────────────────────────────────────────

def test_the_close_reconcile_takes_leverage_off_the_list(tmp_path):
    ex = _executor(tmp_path)
    pos = _adopted(entry=0.0, unread=("entry_price", "margin", "leverage"))
    ex._positions[pos.trade_id] = pos
    close_data = {"close_price": 61000.0, "reason": "SL HIT", "source": "history",
                  "pnl": None, "leverage": 20}
    seen: list = []
    real_audit = le.audit

    def spy(logger, msg, **kw):
        seen.append(kw)
        return real_audit(logger, msg, **kw)

    ex._get_exchange = AsyncMock(return_value=MagicMock())
    ex._fetch_bitget_close_data = AsyncMock(return_value=close_data)
    with patch.object(le, "audit", spy):
        asyncio.run(ex._handle_already_closed_position(pos))
    assert pos.leverage == 20
    unpriced = [kw["data"] for kw in seen
                if kw.get("result") == "UNPRICED" and "adoption_unread" in kw.get("data", {})]
    assert unpriced, seen
    assert unpriced[0]["adoption_unread"] == ["entry_price", "margin"]


# ── the fill during a cancel ──────────────────────────────────────────────

@pytest.mark.parametrize("leverage,cost,unread", [
    (0, 0.0, ("margin", "leverage")),     # adopted: nothing to divide by
    (20, 30.0, ("margin",)),              # the leverage is on record
])
def test_a_fill_during_cancel_writes_a_margin_and_never_the_notional(
        tmp_path, leverage, cost, unread):
    ex = _executor(tmp_path)
    pos = _adopted(leverage=leverage, unread=unread, status="pending_fill")
    pos.limit_order_id = "L1"
    pos.order_type = "limit"
    ex._positions[pos.trade_id] = pos
    exch = MagicMock()
    exch.cancel_order = AsyncMock()
    ex._get_exchange = AsyncMock(return_value=exch)
    ex._fetch_order = AsyncMock(return_value={"id": "L1", "status": "closed",
                                              "filled": 0.01, "amount": 0.01,
                                              "average": 60000.0})
    out = asyncio.run(ex.close_position(pos.trade_id))
    assert "filled while cancelling" in out, out
    assert pos.status == "open"
    # Before: `60000 * 0.01 / (0 or 1)` = $600, the notional, as the margin.
    assert pos.cost_usd == cost
    assert pos.adoption_unread == unread
