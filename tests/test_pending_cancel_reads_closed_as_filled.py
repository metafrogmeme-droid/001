"""Cancelling a pending limit order could delete a LIVE position from the book.

`_close_position_inner`'s pending-cancel branch verified the cancel like this::

    status = (order_info.get("status") or "").lower()
    filled = float(order_info.get("filled") or 0)
    if status in ("canceled", "cancelled", "expired", "closed"):
        cancelled = True
    elif filled > 0:
        ...it filled while we were cancelling — reopen and place a stop...

CCXT's unified order vocabulary is ``open / closed / canceled / expired /
rejected``, where **closed means the order is done — filled**. Three other
sites in this same file read it that way::

    _verify_order_fill    status in ("closed", "filled") and filled > 0  -> FILLED
    _check_pending_limit  status in ("closed","filled","partially_filled") -> FILLED
    execute()             order_status not in ("closed","filled")         -> not filled

and so does `TelegramHandler._resolve_desync_orders` (pinned by
tests/test_pending_order_desync.py, which asserts ``{"status": "closed"}``
renders "FILLED"). The cancel branch was the one site that read it backwards,
and it was the one site that then did ``del self._positions[trade_id]``.

So the ordinary way a venue reports a fill was booked as a cancel: the record
dropped, the operator told ``CANCELLED``, and a live leveraged position left on
the exchange with no stop, no monitor and no record. The ``elif filled > 0``
branch written directly beneath — which stamps ``filled_at`` and places the
stop — could only ever be reached for a PARTIAL fill, because the terminal
status matched first.

Driving the real method across seven venue answers, the unpatched code deleted
the position on FOUR of them and reached that ``elif`` on NONE.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.core.live_executor import LiveExecutor, LivePosition
from bot.core.order_state import (
    CANCELLED_STATUSES,
    FILLED_STATUSES,
    pending_cancel_verdict,
    position_presence,
    read_amount,
)


# ══ the pure verdict ═══════════════════════════════════════════════════
def test_closed_is_a_fill_not_a_cancel():
    # The defect, stated as one assertion.
    v = pending_cancel_verdict({"status": "closed", "filled": 204.0})
    assert v["state"] == "filled"
    assert v["filled_qty"] == 204.0


def test_the_vocabulary_agrees_with_the_rest_of_the_file():
    # _verify_order_fill puts "closed" in the FILLED set and deliberately
    # leaves it out of the cancelled set. Anything else is a disagreement
    # between two readers of the same fetch_order payload.
    assert "closed" in FILLED_STATUSES
    assert "closed" not in CANCELLED_STATUSES
    for word in ("canceled", "cancelled", "expired", "rejected"):
        assert word in CANCELLED_STATUSES
        assert word not in FILLED_STATUSES


@pytest.mark.parametrize("status", ["closed", "filled", "partially_filled"])
def test_every_fill_word_reads_as_a_fill(status):
    assert pending_cancel_verdict({"status": status, "filled": 3})["state"] == "filled"


@pytest.mark.parametrize("status", ["canceled", "cancelled", "expired", "rejected"])
def test_every_cancel_word_reads_as_a_cancel(status):
    assert pending_cancel_verdict({"status": status, "filled": 0})["state"] == "cancelled"


def test_a_cancel_with_a_partial_fill_behind_it_is_a_position():
    # The cancelled remainder is not what matters. 50 of 204 already changed
    # hands, and that 50 is a live leveraged position.
    v = pending_cancel_verdict({"status": "canceled", "filled": 50.0})
    assert v["state"] == "filled"
    assert v["filled_qty"] == 50.0


def test_still_resting_is_its_own_answer():
    v = pending_cancel_verdict({"status": "open", "filled": 0})
    assert v["state"] == "still_open"


@pytest.mark.parametrize("payload", [
    None, {}, "nonsense", [], {"status": None}, {"status": ""},
])
def test_unreadable_payloads_produce_no_verdict(payload):
    assert pending_cancel_verdict(payload)["state"] == "unreadable"


def test_status_is_case_and_whitespace_tolerant():
    assert pending_cancel_verdict({"status": " CANCELED "})["state"] == "cancelled"
    assert pending_cancel_verdict({"status": "Closed"})["state"] == "filled"


def test_a_terminal_fill_with_no_size_is_still_a_fill_with_no_size():
    # Writing 0 here would zero a live position's quantity and every margin
    # and PnL number built on it.
    v = pending_cancel_verdict({"status": "closed"})
    assert v["state"] == "filled"
    assert v["filled_qty"] is None


# ══ read_amount is null-preserving ═════════════════════════════════════
@pytest.mark.parametrize("raw", [None, "", "abc", float("nan"), [1]])
def test_unmeasured_quantity_is_none_not_zero(raw):
    assert read_amount({"filled": raw}, "filled") is None


def test_a_measured_zero_survives_as_zero():
    # 0.0 is falsy and 0.0 is a real reading: nothing filled.
    assert read_amount({"filled": 0}, "filled") == 0.0
    assert read_amount({"filled": "0.0"}, "filled") == 0.0


def test_absent_key_is_none():
    assert read_amount({}, "filled") is None
    assert read_amount(None, "filled") is None


# ══ position_presence ══════════════════════════════════════════════════
def test_an_empty_book_is_a_reading_but_an_unparseable_row_is_not():
    assert position_presence([])["state"] == "flat"
    assert position_presence([{"contracts": 0}])["state"] == "flat"
    assert position_presence([{}])["state"] == "unreadable"
    assert position_presence([{"contracts": None}])["state"] == "unreadable"
    assert position_presence(None)["state"] == "unreadable"
    assert position_presence("nonsense")["state"] == "unreadable"


def test_one_readable_position_settles_it():
    # Short-circuit: a row we CAN read showing size answers the question
    # regardless of what the unreadable rows beside it say.
    assert position_presence([{"contracts": None}, {"contracts": 5}])["state"] == "present"
    assert position_presence([{"contracts": -5}])["state"] == "present"   # short


# ══ the real method, driven end to end ═════════════════════════════════
def _executor(tmp_path, *, order_info=None, order_exc=None,
              cancel_exc=None, positions=None, pos_exc=None):
    ex = LiveExecutor(state_dir=str(tmp_path))
    ex._save_positions = MagicMock()
    ex._risk_engine = MagicMock()
    pos = LivePosition(
        trade_id="T1", symbol="FIL/USDT", direction="LONG", entry_price=0.7668,
        quantity=204.0, cost_usd=15.65, stop_loss=0.70, take_profit=0.85,
        leverage=10, status="pending_fill",
    )
    pos.limit_order_id = "ORD-1"
    ex._positions = {"T1": pos}

    venue = MagicMock()
    venue.cancel_order = AsyncMock(side_effect=cancel_exc) if cancel_exc \
        else AsyncMock(return_value={})
    venue.fetch_order = AsyncMock(side_effect=order_exc) if order_exc \
        else AsyncMock(return_value=order_info if order_info is not None else {})
    venue.fetch_positions = AsyncMock(side_effect=pos_exc) if pos_exc \
        else AsyncMock(return_value=positions if positions is not None else [])
    ex._get_exchange = AsyncMock(return_value=venue)
    ex._place_sl_tp = AsyncMock(return_value=("SL-1", "TP-1"))
    return ex, pos


def _close(ex):
    return asyncio.run(ex._close_position_inner("T1"))


def test_a_filled_order_is_not_reported_as_cancelled(tmp_path):
    # THE bug: status "closed" means filled. Before the fix this returned
    # "CANCELLED pending LONG FIL/USDT limit order" and deleted the record.
    ex, pos = _executor(tmp_path, order_info={
        "status": "closed", "filled": 204.0, "average": 0.7669})
    msg = _close(ex)
    assert "T1" in ex._positions, "a filled position must stay in the book"
    assert pos.status == "open"
    assert "CANCELLED" not in msg.upper()
    assert "filled" in msg.lower()


def test_a_filled_order_gets_its_stop_placed(tmp_path):
    # The `elif filled > 0` branch was unreachable for a full fill, so the
    # SL placement inside it never ran for the common case.
    ex, pos = _executor(tmp_path, order_info={
        "status": "closed", "filled": 204.0, "average": 0.7669})
    _close(ex)
    ex._place_sl_tp.assert_awaited()
    assert pos.sl_order_id == "SL-1"
    assert not getattr(pos, "unprotected", False)


def test_a_partial_fill_behind_a_cancel_stays_tracked(tmp_path):
    ex, pos = _executor(tmp_path, order_info={"status": "canceled", "filled": 50.0})
    _close(ex)
    assert "T1" in ex._positions
    assert pos.status == "open"
    assert pos.quantity == 50.0, "must true up to the quantity that actually filled"


def test_a_real_cancel_still_deletes_the_record(tmp_path):
    # The fix must not make cancelling impossible.
    ex, pos = _executor(tmp_path, order_info={"status": "canceled", "filled": 0})
    msg = _close(ex)
    assert "T1" not in ex._positions
    assert "CANCELLED" in msg


def test_an_unverifiable_cancel_keeps_the_position(tmp_path):
    # fetch_order threw. `cancel_order` not raising is not a confirmation —
    # this used to "assume cancel worked" and delete the record.
    ex, pos = _executor(tmp_path, order_exc=RuntimeError("read timeout"))
    msg = _close(ex)
    assert "T1" in ex._positions
    assert pos.status == "pending_fill", "must stay tracked for the monitor to re-check"
    assert "CANCELLED" not in msg.upper()
    assert "not verify" in msg.lower()


def test_an_unreadable_order_payload_keeps_the_position(tmp_path):
    # The venue answered, with nothing in it.
    ex, pos = _executor(tmp_path, order_info={})
    _close(ex)
    assert "T1" in ex._positions
    assert pos.status == "pending_fill"


def test_order_gone_and_book_unreadable_keeps_the_position(tmp_path):
    # 25204 says the order is gone. It does NOT say whether it filled first,
    # and an unreadable book cannot answer that either.
    ex, pos = _executor(tmp_path,
                        cancel_exc=RuntimeError("25204 Order does not exist"),
                        pos_exc=RuntimeError("venue 500"))
    msg = _close(ex)
    assert "T1" in ex._positions
    assert pos.status == "pending_fill"
    assert "CANCELLED" not in msg.upper()


def test_order_gone_and_book_readably_flat_still_deletes(tmp_path):
    ex, pos = _executor(tmp_path,
                        cancel_exc=RuntimeError("25204 Order does not exist"),
                        positions=[])
    msg = _close(ex)
    assert "T1" not in ex._positions
    assert "CANCELLED" in msg


def test_order_gone_and_a_position_is_there_reopens_it(tmp_path):
    ex, pos = _executor(tmp_path,
                        cancel_exc=RuntimeError("25204 Order does not exist"),
                        positions=[{"contracts": 204.0}])
    _close(ex)
    assert "T1" in ex._positions
    assert pos.status == "open"


def test_a_sizeless_position_row_does_not_read_as_flat(tmp_path):
    # `any(abs(float(p.get("contracts", 0) or 0)) > 0 ...)` read a row that
    # states no size as a row with no position, and deleted on it.
    ex, pos = _executor(tmp_path,
                        cancel_exc=RuntimeError("25204 Order does not exist"),
                        positions=[{"symbol": "FIL/USDT:USDT"}])
    _close(ex)
    assert "T1" in ex._positions
    assert pos.status == "pending_fill"


def test_a_fill_with_no_stated_size_does_not_zero_the_position(tmp_path):
    # Written because the FIRST mutation run let this through: the guard on
    # `pos.quantity = filled` had no test. A venue that reports the terminal
    # status without a `filled` field is still reporting a fill, and
    # `float(x or 0)` would have written 204 contracts down to 0 — taking
    # cost_usd, margin and every PnL number built on it along with it.
    ex, pos = _executor(tmp_path, order_info={"status": "closed"})
    before_qty, before_cost = pos.quantity, pos.cost_usd
    _close(ex)
    assert "T1" in ex._positions
    assert pos.status == "open"
    assert pos.quantity == before_qty, "must not zero a size the venue did not state"
    assert pos.cost_usd == before_cost


def test_a_cancel_records_whether_the_fill_quantity_was_stated(tmp_path, monkeypatch):
    # The delete IS gated on the venue saying "canceled" — but it can say that
    # without saying whether anything filled first. Refusing the delete there
    # would make cancelling impossible on a venue that omits the field, and the
    # 8h hard timeout would then book a flat close on a position that might be
    # live. So the delete stands and the weaker reading is recorded instead.
    import bot.core.live_executor as le

    for order_info, expect in [
        ({"status": "canceled", "filled": 0}, "canceled"),
        ({"status": "canceled"}, "filled quantity not stated"),
    ]:
        seen = []
        monkeypatch.setattr(le, "audit",
                            lambda *a, **k: seen.append((a, k)))
        ex, pos = _executor(tmp_path, order_info=order_info)
        _close(ex)
        cancels = [(a, k) for a, k in seen if k.get("result") == "CANCELLED"]
        assert cancels, "a cancel must leave an audit record"
        args, kwargs = cancels[-1]
        blob = " ".join(str(x) for x in args) + " " + str(kwargs)
        assert expect in blob, f"audit must distinguish {expect!r}, got: {blob}"
