"""After a fill, an unreadable settings document is not "the venue stated none".

#526 made a found fill with no symbol-row leverage audit and say so. It said
the same sentence for two facts: the settings document read with no row for
this symbol in the order's mode, and no document read at all (a timeout, a
429). The audit claimed "the settings document stated no leverage for it"
and the card "the venue stated none" when nobody had read the document. The
reason is now carried from the verification and each fact has its words.

Driven through the real `execute()` and the real `_read_uta_symbol_leverage`,
with the settings transport stubbed one layer down.
"""
import asyncio
from unittest.mock import AsyncMock, patch

import bot.core.live_executor as le
from bot.config import CONFIG, RUNTIME
from bot.core import bounds_shadow
from bot.core.live_executor import LiveExecutor
from tests.test_an_estimated_entry_says_so import Venue, _idea
from tests.test_the_sync_reads_the_row_the_guard_reads import (
    STATED_ENTRY,
    _leverage_line,
)


def _fill(tmp_path, document):
    venue = Venue(row_entry=STATED_ENTRY)
    ex = LiveExecutor(state_dir=str(tmp_path))
    ex._exchange = venue
    ex._is_uta = True
    ex._hedge_mode = False
    ex._slippage_tracker = type("S", (), {"record": staticmethod(lambda **kw: None)})()
    ex._record_warning = lambda key: None
    RUNTIME.leverage_override = None
    audits: list[dict] = []
    ex._read_uta_settings = AsyncMock(return_value=document)

    async def _sl_tp(*a, **k):
        return ("sl1", "tp1")

    try:
        with patch.object(le, "audit", lambda log, msg, **kw: audits.append({"msg": msg, **kw})), \
             patch.object(bounds_shadow.BOUNDS_LEDGER, "record", lambda *a, **k: None), \
             patch.object(type(CONFIG), "is_live", return_value=True), \
             patch.object(LiveExecutor, "_place_sl_tp", _sl_tp), \
             patch.object(LiveExecutor, "sync_positions_from_exchange", AsyncMock(return_value=None)), \
             patch.object(LiveExecutor, "_post_fill_slippage_guard", AsyncMock(return_value=(None, ""))):
            card = asyncio.run(ex.execute(_idea(), size_usd=200.0, order_type="market"))
    finally:
        RUNTIME.leverage_override = None
    unread = [a for a in audits if a.get("action") == "leverage_unverified_on_fill"]
    _fill.position = ex._positions["TI-EST"]
    return card, unread


def test_a_settings_document_nobody_read_is_named_as_unread(tmp_path):
    card, unread = _fill(tmp_path, None)
    assert len(unread) == 1
    assert unread[0]["data"]["reason"] == "settings_unread"
    assert "could not be read" in unread[0]["msg"]
    assert "stated no leverage" not in unread[0]["msg"]
    line = _leverage_line(card)
    assert "settings could not be read, NOT verified" in line
    assert "stated none" not in line


def test_a_document_with_no_row_is_the_venue_stating_none(tmp_path):
    card, unread = _fill(tmp_path, {"data": {"symbolConfigList": []}})
    assert len(unread) == 1
    assert unread[0]["data"]["reason"] == "no_symbol_row"
    assert "stated no leverage for it in" in unread[0]["msg"]
    assert "the venue stated none, NOT verified" in _leverage_line(card)


# ── the three fill paths that are not execute() ──────────────────────────

def _guard(tmp_path, verify):
    from types import SimpleNamespace
    ex = LiveExecutor(state_dir=str(tmp_path))
    ex._verify_position_exists = AsyncMock(return_value=verify)
    ex.close_position = AsyncMock(return_value="closed")
    audits: list[dict] = []
    pos = SimpleNamespace(symbol="APT/USDT:USDT", direction="SHORT", leverage=5,
                          sl_order_id="sl-1", tp_order_id="tp-1")
    with patch.object(le, "audit", lambda log, msg, **kw: audits.append({"msg": msg, **kw})):
        msg = asyncio.run(ex._guard_fill_leverage(object(), "t1", pos, 5, "limit fill"))
    return msg, [a for a in audits if a.get("action") == "leverage_unverified_on_fill"], ex


def test_a_limit_fill_with_no_leverage_read_is_on_the_record(tmp_path):
    msg, unread, ex = _guard(tmp_path, {"confirmed": True, "state": "found", "leverage": 0,
                                        "leverage_unread": "settings_unread", "attempts": 3})
    assert msg is None
    ex.close_position.assert_not_awaited()
    assert len(unread) == 1
    assert unread[0]["result"] == "UNREAD"
    assert unread[0]["data"]["path"] == "limit fill"
    assert unread[0]["data"]["reason"] == "settings_unread"
    assert "after 3 read(s)" in unread[0]["msg"]


def test_a_limit_fill_at_the_approved_leverage_records_nothing_unread(tmp_path):
    msg, unread, ex = _guard(tmp_path, {"confirmed": True, "state": "found", "leverage": 5})
    assert msg is None and unread == []


def test_the_record_names_the_leverage_unverified_and_the_chat_row_says_so(tmp_path):
    from bot.skills.telegram_handler import _live_position_row
    _fill(tmp_path, None)
    pos = _fill.position
    assert pos.leverage == 5, "the record keeps the requested figure"
    assert "leverage" in pos.adoption_unread
    assert "margin" not in pos.adoption_unread, "the sized margin stays on record for the cap"
    row = _live_position_row(pos, None)
    assert "the venue did not state leverage for this fill" in row
    assert "at adoption" not in row


def test_a_fill_whose_leverage_was_read_names_nothing_unread(tmp_path):
    from tests.test_the_sync_reads_the_row_the_guard_reads import ORDER_MODE, _row, _settings
    pos_card = _fill(tmp_path, _settings(_row("5", ORDER_MODE, "ETHUSDT")))
    assert pos_card[1] == [], pos_card[1]
    assert "leverage" not in tuple(getattr(_fill.position, "adoption_unread", ()) or ())
