"""The Telegram "close X" answer is read, then rendered — and never guessed.

`CallbackHandler._report_manual_close` is the seam: the NLP close used to
render inline, pinned by a source window only, so a mutation that kept the
literals and still rendered the closed card passed. Driven here with the
handler's sends captured.

Three things it must never do: render a card for a close that did not happen
(a kept-open answer beside a STALE close slot of the same symbol); render an
earlier close's card as this one (the slot is matched on the trade id, not the
symbol); or rebuild a "closed" card from the position record with
``pnl_usd or 0`` — a break-even nobody measured, under a green emoji.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from bot.skills.callback_handler import CallbackHandler


class _Handler(CallbackHandler):
    def __init__(self):
        self.sent: list[str] = []
        self.photos: list[tuple[bytes, str]] = []

    async def _send(self, update, text, reply_markup=None, edit=False):
        self.sent.append(text)

    async def _send_photo(self, update, png, caption, reply_markup=None):
        self.photos.append((png, caption))
        return True


def _lp(trade_id="T1"):
    return SimpleNamespace(trade_id=trade_id, symbol="BTC/USDT:USDT", entry_price=100.0,
                           quantity=1.0, cost_usd=100.0, close_price=None, pnl_usd=None,
                           opened_at=None)


def _executor(slot):
    return SimpleNamespace(_last_close_data=slot)


def _slot(trade_id, pnl=12.5):
    return {"trade_id": trade_id, "symbol": "BTC/USDT:USDT", "direction": "LONG",
            "reason": "manual_nlp", "entry": 100.0, "exit": 101.0, "pnl_pct": 1.0,
            "pnl_pct_margin": 1.0, "pnl_usd": pnl, "fees": 0.1, "size_usd": 100.0,
            "leverage": 1, "hold_time": "1.0h"}


@pytest.fixture
def card(monkeypatch):
    """A renderer that returns bytes for any payload, so a photo means the
    handler DECIDED to render, not that the renderer happened to work."""
    import bot.formatters.signal_card as sc
    monkeypatch.setattr(sc, "render_close_card", lambda data: b"png")


async def _report(handler, slot, answer, trade_id="T1"):
    await handler._report_manual_close(object(), _executor(slot), _lp(trade_id), "BTCUSDT", answer)


@pytest.mark.asyncio
async def test_a_rejected_close_says_failed_and_shows_no_card(card):
    h = _Handler()
    await _report(h, _slot("T1"), "CLOSE FAILED for T1: venue 5xx")
    assert h.photos == []
    assert len(h.sent) == 1 and "Close failed" in h.sent[0] and "still open" in h.sent[0]


@pytest.mark.asyncio
async def test_a_kept_open_answer_beside_a_stale_slot_of_the_same_symbol_shows_no_card(card):
    """The slot holds an EARLIER close of this symbol; the position this
    request asked about is still there. The old symbol-only match rendered
    that earlier close's green card as this one."""
    h = _Handler()
    answer = "⚠️ CLOSE NOT CONFIRMED: LONG BTC/USDT:USDT\nkept OPEN, NOT re-protected"
    await _report(h, _slot("T1"), answer)
    assert h.photos == []
    assert len(h.sent) == 1
    assert "was NOT closed by this request" in h.sent[0]
    assert "CLOSE NOT CONFIRMED" in h.sent[0] and "NOT re-protected" in h.sent[0], (
        "the answer is quoted, whole enough to carry its protection line")
    assert "$+0.00" not in h.sent[0]


@pytest.mark.asyncio
async def test_a_position_another_path_closed_first_is_not_called_kept_open(card):
    """A second tap while the first close holds the per-trade lock lands on
    'not found or already closed/closing' — the kept-open bucket's 'not this
    caller's to close' member. The card must not assert close_position kept
    it open; it quotes the answer."""
    h = _Handler()
    await _report(h, _slot("T1"), "Position T1 not found or already closed/closing.")
    assert h.photos == []
    assert "not found or already closed/closing" in h.sent[0]
    assert "kept it open" not in h.sent[0]


@pytest.mark.asyncio
async def test_this_closes_own_card_is_rendered(card):
    h = _Handler()
    await _report(h, _slot("T1", pnl=12.5), "✅ CLOSED LONG BTC/USDT:USDT @ $101.00 (manual_nlp)")
    assert len(h.photos) == 1 and "CLOSED" in h.photos[0][1] and "$+12.50" in h.photos[0][1]
    assert h.sent == []


@pytest.mark.asyncio
async def test_an_earlier_close_of_the_same_symbol_is_not_this_closes_card(card):
    """Same symbol, different trade: the slot is not this close's. The answer
    is printed — it carries the numbers that were measured — and nothing is
    rebuilt from the record."""
    h = _Handler()
    answer = "✅ CLOSED LONG BTC/USDT:USDT @ $101.00 (manual_nlp)\nPnL: unread"
    await _report(h, _slot("T0", pnl=99.0), answer)
    assert h.photos == [], "the slot's trade id is not this close's"
    assert len(h.sent) == 1 and "PnL: unread" in h.sent[0]
    assert "$+0.00" not in h.sent[0] and "$+99.00" not in h.sent[0]


@pytest.mark.asyncio
async def test_a_close_booked_without_a_card_prints_the_answer(card):
    """The booked-but-unreported answer says no card was built. Even a slot
    stamped with this trade id (it cannot be — the branch clears it) is
    refused on the answer's own word."""
    from bot.core.order_state import CLOSE_CARD_NOT_RENDERED

    h = _Handler()
    answer = f"✅ CLOSED LONG BTC/USDT:USDT — booked; {CLOSE_CARD_NOT_RENDERED} (KeyError). The ledger row is written."
    await _report(h, _slot("T1"), answer)
    assert h.photos == []
    assert len(h.sent) == 1 and "could not be rendered" in h.sent[0]


@pytest.mark.asyncio
async def test_a_cancelled_pending_order_is_not_a_closed_card(card):
    """'CANCELLED pending … limit order' reads as closed (nothing to protect),
    and used to print '<b>X closed</b> … Exit <entry> … 🟢 PnL: $+0.00' for an
    order that never filled."""
    h = _Handler()
    await _report(h, None, "✅ CANCELLED pending LONG BTC/USDT:USDT limit order (verified)")
    assert h.photos == []
    assert len(h.sent) == 1 and "CANCELLED pending" in h.sent[0]
    assert "closed</b>" not in h.sent[0] and "$+0.00" not in h.sent[0]


@pytest.mark.asyncio
async def test_an_unreadable_answer_is_a_failed_close(card):
    h = _Handler()
    await _report(h, _slot("T1"), None)
    assert h.photos == [] and "Close failed" in h.sent[0]
