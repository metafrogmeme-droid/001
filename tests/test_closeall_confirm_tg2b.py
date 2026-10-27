"""TG-2b: /closeall requires a two-step confirm (audit-confirmed).

/emergency_stop already confirmed before flattening; /closeall market-closed
EVERY operator and per-user position IMMEDIATELY on a single admin tap — a
fat-finger with no undo. It now shows a confirm keyboard; the flatten runs only
from the closeall_confirm callback, which is permission-gated (halt) AND
re-checks admin. Verified by source assertion — the handler needs a full engine
+ Telegram Update to run.
"""

from __future__ import annotations

import inspect

from bot.skills import telegram_handler as th


def test_closeall_shows_a_confirm_and_does_not_flatten_immediately():
    src = inspect.getsource(th.TelegramHandler._cmd_close_all)
    # It renders a confirm keyboard …
    assert 'callback_data="closeall_confirm"' in src
    assert 'callback_data="closeall_cancel"' in src
    assert "Flatten ALL open positions on EVERY account?" in src
    # … and does NOT call the flatten directly anymore.
    assert "flatten_all_positions" not in src


def test_the_actual_flatten_lives_behind_the_confirm():
    src = inspect.getsource(th.TelegramHandler._flatten_all_accounts)
    assert 'flatten_all_positions(reason="admin_closeall")' in src


def test_confirm_callback_is_permission_gated_and_admin_rechecked():
    # The dispatcher, not the class body: _handle_callback lives in the
    # callback mixin since the handler split, and the class source stops at
    # the handler's own file. Reading the method follows it.
    src = inspect.getsource(th.TelegramHandler._handle_callback)
    # Registered as a destructive callback requiring the halt permission.
    # Read through the seam rather than as a literal of the dispatcher: the
    # table lives in `bot/nlp/button_actions.py` now, because a map local to
    # a 1,400-line method is a map nothing can read -- and what nobody could
    # read was that `pos_close_` had no row at all.
    from bot.nlp.button_actions import required_permission
    assert required_permission("closeall_confirm") == "halt"
    assert "required_permission(" in src
    # The confirm branch re-checks admin before flattening, and cancel is inert.
    assert 'if data == "closeall_confirm":' in src
    assert 'if data == "closeall_cancel":' in src
    confirm_at = src.index('if data == "closeall_confirm":')
    branch = src[confirm_at:confirm_at + 400]
    assert "_is_admin(update)" in branch
    assert "_flatten_all_accounts(update)" in branch


# ── the venue router's read-back reaches the cards (VENUE_ROUTER_ENABLED) ──
#
# `flatten_all_positions` rows carry `venue_note` only when the router ran. The
# two cards that print a flatten (the /closeall confirm and the emergency stop)
# show it under the account, and print nothing extra when it is absent, so the
# default (router off) card is the card it always was. Driven through the real
# `_flatten_all_accounts`, not read off its source.

import asyncio  # noqa: E402
from types import SimpleNamespace  # noqa: E402
from unittest.mock import AsyncMock  # noqa: E402


def _sent_by_flatten(rows):
    sent: list[str] = []

    async def _send(update, text, **kw):
        sent.append(text)

    host = SimpleNamespace(
        _send=_send,
        engine=SimpleNamespace(flatten_all_positions=AsyncMock(return_value=rows)))
    asyncio.run(th.TelegramHandler._flatten_all_accounts(host, None))
    return sent[-1]


def test_the_closeall_card_prints_the_venue_readback_under_its_account():
    card = _sent_by_flatten([
        {"account": "operator", "messages": ["LONG BTC/USDT: closed"], "ok": True,
         "venue_note": "bybit: flat at the venue (read back)"},
        {"account": "alice", "messages": ["LONG ETH/USDT: closed"], "ok": True,
         "venue_note": "hyperliquid: not read (TimeoutError); flat NOT verified"},
    ])
    assert "↳ bybit: flat at the venue (read back)" in card
    # a venue that did not answer is said as such, in its own account's block
    alice = card.split("<b>alice:</b>")[1]
    assert "flat NOT verified" in alice and "TimeoutError" in alice
    assert "flat NOT verified" not in card.split("<b>alice:</b>")[0]


def test_the_closeall_card_is_unchanged_when_the_router_did_not_run():
    card = _sent_by_flatten(
        [{"account": "operator", "messages": ["LONG BTC/USDT: closed"], "ok": True}])
    assert "↳" not in card and "venue" not in card.lower()
    assert "LONG BTC/USDT: closed" in card


def test_the_emergency_stop_card_prints_the_same_note():
    src = inspect.getsource(th.TelegramHandler._handle_callback)
    at = src.index('if data == "emergency_confirm":')
    branch = src[at:at + 3200]
    assert "flatten_venue_note(acct)" in branch
