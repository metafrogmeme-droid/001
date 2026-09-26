"""The /risk card shows the leverage in use as read, and an unread book as unread.

Two claims on one card, neither measured:

- **The leverage gauge was drawn from a literal ``1.0``.** It printed, in
  green, "Leverage 1 / 5" whatever the open positions ran at. The figure
  beside it was ``CONFIG.exchange.default_leverage``, the standard every order
  is set to, not a ceiling. So even a real reading drawn as a bar against it
  would paint the ordinary state (5x at a 5x standard) full and red. The line
  is the highest leverage read across the open positions now, beside the
  standard, with no colour claim, and a dash with its reason otherwise.
- **A caller with no executor was shown 0 open positions.** A book nobody read
  counted as a flat one, on the gauge, in "Open Now" and on the picture's tile.
"""
from __future__ import annotations

import asyncio
import re
import types

import pytest

from bot.core.live_executor import LivePosition
from bot.skills.portfolio_commands import PortfolioCommands, leverage_in_use
from bot.warroom.warroom_bot import render_risk


def _live(lev, cost, entry=100.0, qty=1.0, tid="T"):
    return LivePosition(trade_id=tid, symbol="BTC/USDT:USDT", direction="LONG",
                        entry_price=entry, quantity=qty, cost_usd=cost,
                        stop_loss=90.0, take_profit=120.0, leverage=lev, status="open")


def _paper(lev):
    return types.SimpleNamespace(leverage=lev, entry_price=100.0, quantity=1.0)


# ── the reading ─────────────────────────────────────────────────────────

class TestTheReading:
    def test_the_highest_stated_leverage(self):
        assert leverage_in_use([_live(5, 20.0, tid="a"), _live(10, 10.0, tid="b")]) == (10.0, 0)

    def test_an_adopted_position_with_nothing_stated_is_unread(self):
        # leverage 0 and margin 0: nothing to read and nothing to derive from.
        assert leverage_in_use([_live(5, 20.0, tid="a"), _live(0, 0.0, tid="b")]) == (5.0, 1)

    def test_an_unstated_leverage_is_derived_from_margin_and_notional(self):
        # 1000 of notional on 100 of margin is 10x.
        assert leverage_in_use([_live(0, 100.0, entry=100.0, qty=10.0)]) == (10.0, 0)

    def test_a_paper_position_at_1x_is_a_reading(self):
        assert leverage_in_use([_paper(1.0)]) == (1.0, 0)

    @pytest.mark.parametrize("bad", [None, "x", 0.0, float("inf"), float("nan")])
    def test_a_paper_leverage_that_is_not_one_is_unread(self, bad):
        assert leverage_in_use([_paper(bad)]) == (None, 1)

    @pytest.mark.parametrize("rows", [None, []])
    def test_no_rows_reads_nothing(self, rows):
        assert leverage_in_use(rows) == (None, 0)


# ── the renderer ────────────────────────────────────────────────────────

def _card(**kw):
    base = {"current_drawdown": 2.0, "drawdown_limit": 7.0, "max_open_trades": 5,
            "leverage_cap": 5}
    base.update(kw)
    return re.sub(r"<[^>]+>", "", render_risk(base)["text"])


def _line(card, label):
    return next(x for x in card.splitlines() if x.strip().startswith(label)
                or f" {label} " in x or x.strip().startswith(f"⚪ {label}")
                or x.strip().startswith(f"🟢 {label}") or x.strip().startswith(f"🟡 {label}")
                or x.strip().startswith(f"🔴 {label}"))


def test_the_leverage_line_is_the_reading_beside_the_standard():
    line = _line(_card(open_trades=3, leverage_in_use=10.0), "Leverage")
    assert "10x in use" in line and "standard 5x" in line, line
    # No colour claim: the figure beside it is not a ceiling.
    assert "🔴" not in line and "🟢" not in line and "🟡" not in line, line


def test_the_old_literal_is_gone():
    line = _line(_card(open_trades=3, leverage_in_use=5.0), "Leverage")
    assert "1 / 5" not in line and "1 / 5" not in line, line


def test_an_unread_leverage_says_so():
    assert "— (unread)" in _line(_card(open_trades=2, leverage_in_use=None, leverage_unread=2),
                                 "Leverage")


def test_nothing_open_is_not_an_unread_leverage():
    assert "— (nothing open)" in _line(_card(open_trades=0), "Leverage")


def test_a_partly_read_leverage_says_how_many_did_not_read():
    assert "(1 unread)" in _line(_card(open_trades=2, leverage_in_use=5.0, leverage_unread=1),
                                 "Leverage")


def test_an_unread_book_is_not_a_flat_one():
    card = _card(open_trades=None)
    pos = _line(card, "Positions")
    assert "book not read" in pos and "0" not in pos.split("│")[-1], pos
    assert "Open Now ··············· —" in card, card
    assert "— (unread)" in _line(card, "Leverage")


def test_a_flat_book_still_reads_zero():
    card = _card(open_trades=0)
    assert "Open Now ··············· 0" in card


# ── the handler ─────────────────────────────────────────────────────────

class _Stand:
    def __init__(self, executor):
        risk = types.SimpleNamespace(
            drawdown_status=lambda: {"drawdown_pct": 1.0, "effective_limit_pct": 7.0},
            slot_status=lambda n: {"cap": 5}, trading_blocked_by="",
            circuit_breaker_active=False, consecutive_losses=0)
        state = types.SimpleNamespace(open_positions=0)
        self.engine = types.SimpleNamespace(
            risk=risk, risk_for=lambda uid: risk, _halted=False,
            live_executor=object(),
            user_portfolios=types.SimpleNamespace(get=lambda uid: types.SimpleNamespace(
                snapshot=lambda: state, open_positions=[])))
        self._executor = executor
        self.sent: list[str] = []

    def _get_tg_id(self, update):
        return "7"

    def _lang(self, update):
        return "en"

    def _caller_executor(self, update):
        return self._executor

    async def _guard(self, *a, **kw):
        return True

    async def _send(self, update, text, **kw):
        self.sent.append(text)


def _risk(executor):
    from unittest import mock

    from bot.config import CONFIG
    me = _Stand(executor)
    with mock.patch.object(type(CONFIG), "is_live", lambda self: True):
        asyncio.run(PortfolioCommands._cmd_risk(me, object(), object()))
    return re.sub(r"<[^>]+>", "", me.sent[-1])


def test_the_command_shows_an_unlinked_callers_book_as_unread():
    card = _risk(None)
    assert "book not read" in card and "Open Now ··············· —" in card, card


def test_the_command_shows_the_leverage_the_positions_run_at():
    book = types.SimpleNamespace(open_positions=[_live(7, 10.0)])
    card = _risk(book)
    assert "7x in use" in card and "Open Now ··············· 1" in card, card


def test_the_picture_tile_shows_an_unread_book_as_a_dash():
    from unittest import mock

    from bot.config import CONFIG
    me = _Stand(None)
    specs: list = []

    async def _photo(update, png, caption, reply_markup=None):
        return True
    me._send_photo = _photo

    def _render(spec):
        specs.append(spec)
        return b"png"
    with mock.patch.object(type(CONFIG), "is_live", lambda self: True), \
            mock.patch("bot.formatters.signal_card.render_stats_card", _render):
        asyncio.run(PortfolioCommands._cmd_risk(me, object(), object()))
    tile = next(x for x in specs[-1]["tiles"] if "/" in str(x["value"]) and x["value"].endswith("/5"))
    assert tile["value"] == "—/5" and tile["color"] == "gray", tile


def test_the_command_says_how_many_leverages_it_could_not_read():
    book = types.SimpleNamespace(open_positions=[_live(7, 10.0, tid="a"), _live(0, 0.0, tid="b")])
    card = _risk(book)
    assert "7x in use" in card and "(1 unread)" in card, card
