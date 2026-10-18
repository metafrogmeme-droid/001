"""Everyone but the operator sees their own book.

With PER_USER_LIVE_ENABLED off (the default) `viewer_executor` answered the
operator's executor for every caller ("single-account behaviour"), and
PAPER_AUTO_ACCEPT admits anyone who sends a command. So a stranger's
/positions, /start, the portfolio tool and the risk cards described the
operator's live account. The operator decided on 2026-10-15 that everyone but
the operator sees only their own book: on a live bot, a caller with no live
account to view is shown their PRACTICE book, under one line saying so.

The resolver itself is driven in tests/test_live_stats_consistency.py and
tests/test_chat_prompt_describes_only_the_callers_book.py; the record cards in
tests/test_the_record_cards_read_the_callers_book.py. These drive the surfaces
those files do not reach. In every one the operator's book RAISES if anything
reads it, so a surface that takes the wrong book fails loudly instead of
agreeing with a symmetric stub.
"""
from __future__ import annotations

import asyncio
import dataclasses
import threading
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock

import bot.config
from bot.config import CONFIG
from bot.core import trade_gate
from bot.formatters import signal_card as sc
from bot.risk.portfolio import PortfolioTracker
from bot.skills import chat_runtime
from bot.utils.models import Direction


class OperatorBook:
    """The book no caller but the operator may be shown."""

    @property
    def open_positions(self):
        raise AssertionError("read the OPERATOR's open positions")

    @property
    def closed_positions(self):
        raise AssertionError("read the OPERATOR's closed trades")

    @property
    def _positions(self):
        raise AssertionError("read the OPERATOR's tracked positions")


def _per_user(monkeypatch, on: bool):
    monkeypatch.setattr(bot.config, "CONFIG",
                        dataclasses.replace(bot.config.CONFIG, per_user_live_enabled=on))


# ── /positions ──────────────────────────────────────────────────────────────

def _practice_pos(asset="DOGE/USDT"):
    return NS(asset=asset, direction=Direction.SHORT, entry_price=0.2,
              stop_loss=0.22, take_profit=0.15, quantity=50.0, leverage=2,
              opened_at=datetime.now(timezone.utc) - timedelta(hours=1))


def _positions(monkeypatch, *, executor):
    from bot.skills import telegram_handler as th
    from bot.skills import trading_commands as tc
    monkeypatch.setattr(type(CONFIG), "is_live", lambda self: True)
    monkeypatch.setattr(tc, "live_account_absence", lambda _uid: "absent")
    cards: list = []
    monkeypatch.setattr(sc, "render_position_card", lambda d: (cards.append(d), b"PNG")[1])
    pos = _practice_pos()
    portfolio = NS(open_positions=[pos], _positions={"P1": pos}, _lock=threading.RLock(),
                   _last_prices={"DOGE/USDT": 0.19}, mark_to_market=lambda fresh: None)

    async def _no_exchange():
        raise RuntimeError("venue down")

    h = object.__new__(th.TelegramHandler)
    h.engine = NS(user_portfolios={7: portfolio}, scanner=NS(_get_exchange=_no_exchange),
                  pending_ideas=[], position_watch=lambda: None,
                  live_executor=OperatorBook())
    h._get_tg_id = lambda update: 7
    h._lang = lambda update: "en"
    h._caller_executor = lambda update: executor

    async def _allow(update, command="", ctx=None):
        return True
    h._guard = _allow
    sent: list = []

    async def _send(update, text, **kw):
        sent.append(text)
    h._send = _send

    async def _send_photo(update, png, caption, reply_markup=None):
        sent.append(caption)
        return True
    h._send_photo = _send_photo
    asyncio.run(h._cmd_open_positions(None, None))
    return cards, sent


def test_positions_on_a_live_bot_shows_a_stranger_their_practice_book(monkeypatch):
    cards, sent = _positions(monkeypatch, executor=None)
    said = "\n".join(str(s) for s in sent)
    assert "Your practice book" in said, said[:300]
    assert cards and any("DOGE" in str(c) for c in cards), (cards, said[:300])


def test_positions_for_the_operator_reads_the_live_book_and_no_practice_note(monkeypatch):
    # The other arm: a caller with a live book to view (the operator) is
    # shown that book, flat here, and neither the note nor the practice rows.
    cards, sent = _positions(monkeypatch, executor=NS(open_positions=[], _positions={},
                                                      closed_positions=[]))
    said = "\n".join(str(s) for s in sent)
    assert "practice book" not in said.lower(), said[:300]
    assert not any("DOGE" in str(c) for c in cards) and "DOGE" not in said, (cards, said[:300])


# ── the portfolio tool ──────────────────────────────────────────────────────

def _tool_engine(viewer):
    return NS(user_portfolios=NS(get=lambda uid, venue="": PortfolioTracker(initial_balance=4321.0)),
              portfolio=PortfolioTracker(initial_balance=10_000.0),
              viewer_executor=lambda uid="": viewer(uid),
              live_executor=OperatorBook(),
              get_effective_equity_async=AsyncMock(return_value=9999.0),
              live_view=lambda uid="": {"scope": "none", "balance": None},
              resolve_display_equity=AsyncMock(return_value=(None, "no_account")))


def test_the_portfolio_tool_shows_a_stranger_their_practice_book(monkeypatch):
    import bot.skills.skill_registry as reg
    monkeypatch.setattr(type(CONFIG), "is_live", lambda self: True)
    monkeypatch.setattr("bot.core.exchange_credentials.get_credential_store",
                        lambda: NS(credential_state=lambda u: "absent"))
    _per_user(monkeypatch, False)
    engine = _tool_engine(lambda uid: None)
    out = asyncio.run(reg.build_default_registry().get("get_portfolio").execute(engine, user_id="555"))
    assert "Your practice book" in out, out[:300]
    assert "4,321" in out, out[:400]
    assert "9,999" not in out and "/connect" not in out, out[:400]
    # The other arm: the operator's view is the live book, with no note.
    own = NS(open_positions=[], closed_positions=[], closed_trades_read_failed=False)
    engine = _tool_engine(lambda uid: own if uid == "777" else None)
    out = asyncio.run(reg.build_default_registry().get("get_portfolio").execute(engine, user_id="777"))
    assert "practice book" not in out.lower() and "4,321" not in out, out[:400]


# ── the practice note's door ────────────────────────────────────────────────

def test_the_link_door_is_named_only_where_it_opens(monkeypatch):
    """With per-user live off, linking keys gives nobody but the operator a
    live account to see, so the practice note names no door; on, it names
    the surface's own."""
    _per_user(monkeypatch, False)
    off = chat_runtime.practice_view_note("absent")
    assert "/connect" not in off and "dashboard" not in off, off
    _per_user(monkeypatch, True)
    on = chat_runtime.practice_view_note("absent")
    web = chat_runtime.practice_view_note("absent", "web")
    assert "/connect links exchange keys" in on, on
    assert "/connect" not in web and "Connect an exchange step" in web, web
    # The other absences keep their remedy whatever the mode: a key that will
    # not decrypt is fixed by re-linking it.
    for flag in (False, True):
        _per_user(monkeypatch, flag)
        assert "re-link" in chat_runtime.practice_view_note("unreadable").lower()


# ── the risk engine a practice caller's cards read ──────────────────────────

def _gate_engine(*, practice: object):
    shared, own, prac = NS(name="shared"), NS(name="own"), NS(name="practice")
    return NS(risk=shared, risk_for=lambda uid: shared,
              confirm_is_practice=lambda uid: practice,
              practice_risk_for=lambda uid: prac), shared, own, prac


def test_a_practice_caller_reads_their_practice_risk_engine():
    eng, shared, _own, prac = _gate_engine(practice=True)
    assert trade_gate.caller_risk(eng, "555") is prac
    assert list(trade_gate.risk_engines(eng, "555")) == [shared, prac]


def test_a_caller_who_is_not_practice_keeps_risk_for():
    # Both arms, and a stand-in that answers a truthy non-bool is not practice.
    for answer in (False, MagicMock()):
        eng, shared, _own, _prac = _gate_engine(practice=answer)
        assert trade_gate.caller_risk(eng, "555") is shared
        assert list(trade_gate.risk_engines(eng, "555")) == [shared]
    # The system context is nobody's practice book.
    eng, shared, _own, _prac = _gate_engine(practice=True)
    assert trade_gate.caller_risk(eng, "") is shared


# ── /start ──────────────────────────────────────────────────────────────────

def _start(monkeypatch, *, executor):
    import bot.core.live_readiness as lr
    from bot.skills import telegram_handler as th
    monkeypatch.setattr(lr, "mode_label", lambda: "LIVE")
    monkeypatch.setattr(chat_runtime, "live_account_absence", lambda _uid: "absent")
    book = PortfolioTracker(initial_balance=4321.0)
    users = NS(get=lambda uid: {"role": "paper", "authorized": True},
               register=lambda uid, name="": {"role": "paper", "authorized": True},
               tier_label=lambda uid: "Free")
    h = object.__new__(th.TelegramHandler)
    h.engine = NS(user_portfolios=NS(get=lambda uid: book), live_executor=OperatorBook(),
                  risk=NS(circuit_breaker_active=False), risk_for=lambda uid: NS(),
                  resolve_display_equity=AsyncMock(return_value=(9999.0, "live")))
    h.users = users
    h._limiter = NS(allow=lambda uid: True)
    h._get_tg_id = lambda update: "555"
    h._access_state = lambda tg_id: "ok"
    h._caller_executor = lambda update: executor
    h._can_trade_live = lambda tg_id: False
    sent: list = []

    async def _send(update, text, **kw):
        sent.append(text)
    h._send = _send
    update = NS(effective_user=NS(id=555, first_name="Sam"))
    asyncio.run(h._cmd_start(update, NS(args=[])))
    return h, "\n".join(sent)


def test_start_on_a_live_bot_shows_a_stranger_their_practice_book(monkeypatch):
    h, said = _start(monkeypatch, executor=None)
    assert "Your practice book" in said, said[:400]
    assert "4,321.00" in said and "9,999" not in said, said[:600]
    h.engine.resolve_display_equity.assert_not_awaited()


def test_start_for_the_operator_reads_the_live_account(monkeypatch):
    live = NS(open_positions=[], _positions={}, closed_positions=[],
              closed_trades_read_failed=False)
    h, said = _start(monkeypatch, executor=live)
    # The account the card is about is the live one: its equity is the live
    # balance, never the paper baseline. The paper book is named on a line of
    # its own (`paper_book_line`, 2026-10-18), and only there.
    equity_line = next(ln for ln in said.splitlines() if ln.startswith("Equity:"))
    assert "9,999.00" in equity_line and "4,321" not in equity_line, said[:600]
    assert any(ln.startswith("\U0001f4dd Paper book: <code>$4,321.00</code>") for ln in said.splitlines()), said[:600]
    assert [ln for ln in said.splitlines() if "4,321" in ln] == \
        [ln for ln in said.splitlines() if ln.startswith("\U0001f4dd Paper book:")], said[:600]
    assert "practice book" not in said.lower(), said[:400]
    h.engine.resolve_display_equity.assert_awaited_once()
