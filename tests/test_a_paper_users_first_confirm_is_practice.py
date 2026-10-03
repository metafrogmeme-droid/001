"""A self-admitted paper user's Confirm is a labelled practice fill.

Plan F8. With the bot live and PAPER_SIM_OPT_IN_ENABLED off, that person's
Confirm used to stop at "live trading is not enabled" (or, if it reached the
engine, place on the operator account). It now opens a PRACTICE row on their
own paper book. A vouched trader who may not trade live still gets the live
refusal, and a trader who may still reaches the executor.

A close of the PRACTICE row does not enter the engine journal or learners.
A close that is not labelled PRACTICE still does. An unreadable practice book
is a refusal that names the next step, and places nothing.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import bot.config as bot_config
from bot.config import CONFIG
from bot.core.practice_fill import (
    PRACTICE_FILL,
    is_self_admitted_paper,
    practice_book_refusal,
)
from bot.utils.models import Direction, TradeIdea
from bot.utils.user_store import ROLE_PERMISSIONS
from bot.web import user_gateway as ug
from tests.test_web_gateway import (
    AUTHED,
    HDRS,
    SECRET,
    FakeEngine,
    FakeHandler,
    _propose,
    gateway_client,
)

UID = "424242"


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _Roles:
    """The door's user store. ``get`` is the only read the practice check makes."""

    def __init__(self, role):
        self.role = role

    def get(self, tid):
        return {"role": self.role, "authorized": True}

    def has_permission(self, tid, perm):
        return perm in ROLE_PERMISSIONS.get(self.role, set())

    def is_authorized(self, *a, **k):
        return True

    def is_admitted(self, *a, **k):
        return True

    def permission_denial(self, *a, **k):
        return None

    def get_tier(self, *a, **k):
        return "basic"

    def register(self, *a, **k):
        return None

    def live_trading_revoked(self, *a, **k):
        return False


def _confirm_tap(role, *, live_ok):
    """One Confirm tap through the real dispatcher. ``confirm_trade`` is planted."""
    from bot.skills.manual_trade import build_manual_idea
    from bot.skills.telegram_handler import TelegramHandler

    replies: list = []
    idea = build_manual_idea("LONG", "BTC", 60000.0, 59000.0, 63000.0)
    engine = SimpleNamespace(
        live_executor=SimpleNamespace(_positions={}),
        _pending_ideas={idea.id: idea},
    )
    engine.confirm_trade = AsyncMock(return_value="📝 [PAPER] simulated")

    async def _noop(*a, **k):
        return None

    h = TelegramHandler.__new__(TelegramHandler)
    h.engine = engine
    h.users = _Roles(role)
    h.forwarder = SimpleNamespace(post_trade_opened=_noop)
    h._limiter = SimpleNamespace(allow=lambda uid: True)
    h._check_auth = lambda update: True
    h._is_admin = lambda update: False
    h._can_trade_live = lambda tg_id: live_ok
    h._lang = lambda update: "en"
    h._live_refusal_key = lambda: "live_not_enabled"

    async def _send(update, text, **kw):
        replies.append(text)

    h._send = _send
    query = SimpleNamespace(
        data=f"confirm:{idea.id}:{UID}",
        message=SimpleNamespace(edit_reply_markup=_noop, chat_id=int(UID)),
        answer=_noop,
    )
    update = SimpleNamespace(
        callback_query=query,
        effective_user=SimpleNamespace(id=int(UID), first_name="X"),
        effective_chat=SimpleNamespace(id=int(UID)),
    )
    orig = type(bot_config.CONFIG).is_live
    type(bot_config.CONFIG).is_live = lambda self: True
    try:
        _run(h._handle_callback(update, SimpleNamespace()))
    finally:
        type(bot_config.CONFIG).is_live = orig
    return engine, "\n".join(replies)


def test_a_trader_who_may_not_trade_live_still_stops_at_the_door():
    engine, said = _confirm_tap("trader", live_ok=False)
    assert engine.confirm_trade.await_count == 0
    assert "\U0001f512" in said


def test_a_paper_user_reaches_confirm_instead_of_the_live_refusal():
    engine, said = _confirm_tap("paper", live_ok=False)
    assert engine.confirm_trade.await_count == 1
    assert "\U0001f512" not in said


def test_a_missing_role_is_not_paper_and_a_paper_role_is():
    assert is_self_admitted_paper(None) is False
    assert is_self_admitted_paper({}) is False
    assert is_self_admitted_paper({"role": None}) is False
    assert is_self_admitted_paper({"role": "trader"}) is False
    assert is_self_admitted_paper({"role": "paper"}) is True


def _armed(role: str):
    """The live-confirm scaffolding ``test_confirm_trade_success`` drives, plus
    a store whose role is ``role``. Paper sim stays off."""
    import time
    from unittest.mock import patch

    from tests.test_core import TestEngineFSM

    host = TestEngineFSM()
    engine = host._make_engine()
    idea = host._make_pending_idea()
    engine._pending_ideas[idea.id] = idea
    engine._pending_atr[idea.id] = 500.0
    engine._cooldown_until = 0.0
    engine.risk._cooldown_until = 0.0
    engine.risk._last_loss_time = None
    engine.portfolio.balance = 50000.0
    engine.portfolio._peak_equity = 50000.0
    engine._live_balance_cache = {"total": engine.portfolio.balance,
                                  "free": engine.portfolio.balance}
    engine._live_balance_cache_ts = time.monotonic()
    mock_exchange = AsyncMock()
    mock_exchange.fetch_ticker = AsyncMock(return_value={"last": idea.entry_price})
    engine.scanner._get_exchange = AsyncMock(return_value=mock_exchange)
    engine.scanner._get_futures_exchange = AsyncMock(return_value=mock_exchange)
    engine.live_executor._positions = {}
    engine.live_executor.execute = AsyncMock(
        return_value="✅ LIVE order placed: BTC/USDT LONG")
    engine.compliance.issue_approval_token = MagicMock(return_value="tok-123")
    engine.compliance.authorize = MagicMock(return_value=SimpleNamespace(
        granted=True, reasons=[], locks_failed=[], locks_passed=["L1", "L5"],
    ))
    engine._live_execution_vetoed_by_simulation = lambda: False
    engine._user_store = _Roles(role)
    # Telemetry only. A configured website URL would open a socket from the
    # flight-record sync the live path fires, and this test is about which
    # book the confirm lands on.
    engine._sync_flight_records = lambda: None
    return host, engine, idea, patch


def test_a_paper_confirm_opens_practice_and_does_not_call_the_executor():
    host, engine, idea, patch = _armed("paper")
    was = CONFIG.paper_sim_opt_in_enabled
    object.__setattr__(CONFIG, "paper_sim_opt_in_enabled", False)
    try:
        with patch.object(type(CONFIG), "is_live", return_value=True), \
             patch("bot.core.engine.get_exchange_position_count",
                   new=AsyncMock(return_value=0)), \
             patch("bot.core.engine.invalidate_position_count_cache"):
            result = host._run(engine.confirm_trade(idea.id, user_id=UID))
    finally:
        object.__setattr__(CONFIG, "paper_sim_opt_in_enabled", was)
    engine.live_executor.execute.assert_not_awaited()
    assert "PRACTICE" in result
    assert "[PAPER]" in result
    book = engine.user_portfolios.get(UID)
    assert len(book.open_positions) == 1
    assert book.open_positions[0].fill_label == PRACTICE_FILL


def test_a_trader_confirm_still_reaches_the_executor_when_sim_is_off():
    host, engine, idea, patch = _armed("trader")
    was = CONFIG.paper_sim_opt_in_enabled
    object.__setattr__(CONFIG, "paper_sim_opt_in_enabled", False)
    try:
        with patch.object(type(CONFIG), "is_live", return_value=True), \
             patch("bot.core.engine.get_exchange_position_count",
                   new=AsyncMock(return_value=0)), \
             patch("bot.core.engine.invalidate_position_count_cache"):
            result = host._run(engine.confirm_trade(idea.id, user_id=UID))
    finally:
        object.__setattr__(CONFIG, "paper_sim_opt_in_enabled", was)
    assert result == "✅ LIVE order placed: BTC/USDT LONG"
    engine.live_executor.execute.assert_awaited_once()
    assert engine.user_portfolios.get(UID).open_positions == []


def test_an_unreadable_practice_book_is_a_next_step_and_places_nothing():
    from bot.core.engine import RuneClawEngine
    from bot.utils.models import RiskCheck, RiskVerdict

    engine = RuneClawEngine()
    engine.live_executor.execute = AsyncMock(
        side_effect=AssertionError("an unread book must not reach the venue"))
    engine._user_store = _Roles("paper")

    class _Books:
        def get(self, uid):
            raise RuntimeError("portfolio store down")

    engine.user_portfolios = _Books()
    idea = TradeIdea(
        asset="SOL/USDT", direction=Direction.LONG, entry_price=100.0,
        stop_loss=98.0, take_profit=106.0, confidence=0.8,
        reasoning="practice", source="scan")
    engine._pending_ideas[idea.id] = idea
    recheck = RiskCheck(trade_id=idea.id, verdict=RiskVerdict.APPROVED,
                        position_size_usd=50.0, position_pct=1.0)
    msg = _run(engine._simulate_paper_fill(idea, recheck, UID, idea.id))
    engine.live_executor.execute.assert_not_called()
    assert msg == practice_book_refusal(RuntimeError("portfolio store down"))
    assert "RuntimeError" in msg
    assert "/connect" in msg
    assert "$" not in msg


def test_a_practice_close_does_not_train_and_another_paper_close_does():
    from bot.core.engine import RuneClawEngine
    from bot.utils.models import RiskCheck, RiskVerdict

    engine = RuneClawEngine()
    engine._user_store = _Roles("paper")
    engine.ws_feed.is_connected = lambda: False

    async def _prices(positions):
        return {p.asset: 90.0 for p in positions}

    engine._fetch_prices_by_category = _prices
    recorded: list = []
    real = engine.learning.record_closed_outcome

    def _record(**kw):
        recorded.append(kw)
        return real(**kw)

    engine.learning.record_closed_outcome = _record

    def _idea(asset, tid):
        idea = TradeIdea(
            id=tid, asset=asset, direction=Direction.LONG, entry_price=100.0,
            stop_loss=98.0, take_profit=106.0, confidence=0.8,
            reasoning="practice", source="scan")
        engine._pending_ideas[tid] = idea
        return idea

    recheck = RiskCheck(trade_id="T", verdict=RiskVerdict.APPROVED,
                        position_size_usd=50.0, position_pct=1.0)
    was_flag = CONFIG.learning.learn_from_paper_closes_enabled
    was_sim = CONFIG.paper_sim_opt_in_enabled
    object.__setattr__(CONFIG.learning, "learn_from_paper_closes_enabled", True)
    object.__setattr__(CONFIG, "paper_sim_opt_in_enabled", False)
    try:
        paper_msg = _run(engine._simulate_paper_fill(
            _idea("SOL/USDT", "TI-PAPER1"), recheck, "paper-user", "TI-PAPER1"))
        engine._user_store = _Roles("trader")
        plain_msg = _run(engine._simulate_paper_fill(
            _idea("ETH/USDT", "TI-PLAIN1"), recheck, "trader-user", "TI-PLAIN1"))
        assert "PRACTICE" in paper_msg
        assert "PRACTICE" not in plain_msg
        orig = type(CONFIG).is_live
        type(CONFIG).is_live = lambda self: True
        try:
            _run(engine._check_paper_positions([]))
        finally:
            type(CONFIG).is_live = orig
    finally:
        object.__setattr__(CONFIG.learning, "learn_from_paper_closes_enabled", was_flag)
        object.__setattr__(CONFIG, "paper_sim_opt_in_enabled", was_sim)
    symbols = [row.get("symbol") for row in recorded]
    assert "SOL/USDT" not in symbols
    assert "ETH/USDT" in symbols
    closed_practice = [
        t for t in engine.user_portfolios.get("paper-user").trade_history
        if t.asset == "SOL/USDT"]
    assert closed_practice and closed_practice[0].fill_label == PRACTICE_FILL


@pytest.mark.asyncio
async def test_a_web_paper_user_confirms_and_a_web_trader_does_not(monkeypatch):
    monkeypatch.setattr(ug, "_GATEWAY_SECRET", SECRET)
    monkeypatch.setattr(ug, "CONFIG", SimpleNamespace(
        telegram=SimpleNamespace(admin_ids=""),
        is_live=lambda: True))
    paper_engine = FakeEngine()
    paper_users = {"web:9": {"authorized": True, "role": "paper",
                             "can_trade_live": False}}
    async with gateway_client(paper_engine, FakeHandler(users=paper_users)) as c:
        tid = await _propose(c, tg="web:9")
        r = await c.post("/trade/confirm",
                         json={"telegram_id": "web:9", "trade_id": tid},
                         headers=HDRS)
        body = await r.json()
        assert r.status == 200, body
        assert paper_engine.confirm_calls == [(tid, "web:9")]

    trader_engine = FakeEngine()
    async with gateway_client(trader_engine, FakeHandler(users=AUTHED, live=False)) as c:
        tid = await _propose(c)
        r = await c.post("/trade/confirm",
                         json={"telegram_id": "7", "trade_id": tid},
                         headers=HDRS)
        assert r.status == 403
        assert (await r.json())["error"] == "live_not_enabled"
        assert trader_engine.confirm_calls == []
