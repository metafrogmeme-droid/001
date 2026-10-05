"""A practice confirm is sized, gated and named off the practice book, everywhere.

PR 498 made a self-admitted (role ``paper``) account's confirm a PRACTICE
row on their own paper book, and skipped the wrapper's duplicate read of the
live book for it. Two surfaces one step over still read the operator's
money and the live flags:

* ``_confirm_trade_inner`` sized and gated the practice confirm through
  ``_live_recheck_context``: with per-user live off (the default) that is
  the OPERATOR's cached live equity, exchange position count and held rows.
  Driven: an operator balance that could not be read refused the practice
  confirm ("Live equity could not be read"), an operator book at the
  position cap refused it ("MAX_POSITIONS"), and a $50,000 operator book
  sized a $100 practice fill (the live execution ceiling) on a $10,000
  practice book. The compliance gate then authorized a LIVE_TRADE against
  the operator's profile, minted a human-approval token for it, and wrote
  the grant to the consent ledger — for a fill that reached no venue.
* ``_trade_mode`` (``GET /trade/live_mode``, the browser's 2FA step-up key)
  read the web live gate or the Telegram key-holder policy alone, and
  ``_can_trade_live`` answered True for a self-admitted key holder under
  LIVE_OPEN_TO_KEY_HOLDERS, so the mode card said "🔥 Live" over a confirm
  that landed on the practice book. ``placed: true`` rode back with no word
  that it was practice.

One reading now, ``RuneClawEngine.confirm_is_practice`` (a self-admitted
account, or a practice-mode opt-in), asked by the confirm branch, the
re-check, the compliance gate, the Telegram and web doors and the mode
surfaces. The re-check runs on ``practice_risk_for``: the per-user engine
bound to the practice book, so the practice book's own equity, open count
and breakers decide, and the operator's live peak and breaker are never
written by a practice evaluation. A practice confirm authorizes a PAPER
trade with no live token. Every mode surface reads PAPER with the practice
reason, and the web confirm answer carries ``practice``.
"""

from __future__ import annotations

import re
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from bot.compliance.compliance_engine import Permission
from bot.config import CONFIG
from bot.core.practice_fill import PRACTICE_FILL, PRACTICE_MODE_REASON
from bot.skills import telegram_handler as th
from bot.web import user_gateway as ug
from tests.test_a_paper_users_first_confirm_is_practice import (
    UID,
    _armed,
    _confirm_tap,
    _Roles,
)
from tests.test_web_gateway import (
    AUTHED,
    HDRS,
    SECRET,
    FakeEngine,
    FakeHandler,
    _propose,
    gateway_client,
)


def _uid() -> str:
    """A fresh person per test. The practice books and the person peaks
    persist under data/, which plain pytest keeps between tests, so a shared
    id would carry one test's practice fills into the next test's peak."""
    return str(424242000 + uuid.uuid4().int % 10**6)


def _confirm(host, engine, idea, *, uid: str, operator_count: int = 0):
    """One confirm through the real path, live mode on, sim opt-in off."""
    was = CONFIG.paper_sim_opt_in_enabled
    object.__setattr__(CONFIG, "paper_sim_opt_in_enabled", False)
    try:
        with patch.object(type(CONFIG), "is_live", return_value=True), \
             patch("bot.core.engine.get_exchange_position_count",
                   new=AsyncMock(return_value=operator_count)), \
             patch("bot.core.engine.invalidate_position_count_cache"):
            return host._run(engine.confirm_trade(idea.id, user_id=uid))
    finally:
        object.__setattr__(CONFIG, "paper_sim_opt_in_enabled", was)


def _size(result: str) -> float:
    m = re.search(r"Size <code>\$([\d,]+\.\d+)</code>", result)
    assert m, result
    return float(m.group(1).replace(",", ""))


# ── the re-check reads the practice book ──────────────────────────────────

def test_a_practice_confirm_fills_when_the_operators_balance_is_unread():
    """The operator's live cache is empty. A live confirm is refused on that;
    a practice confirm has nothing to read there."""
    host, engine, idea, _ = _armed("paper")
    uid = _uid()
    engine._live_balance_cache = {}
    result = _confirm(host, engine, idea, uid=uid)
    assert "PRACTICE" in result, result
    assert "Live equity could not be read" not in result
    book = engine.user_portfolios.get(uid)
    assert len(book.open_positions) == 1
    assert book.open_positions[0].fill_label == PRACTICE_FILL
    engine.live_executor.execute.assert_not_awaited()


def test_a_practice_confirm_fills_when_the_operators_book_is_at_the_cap():
    host, engine, idea, _ = _armed("paper")
    result = _confirm(host, engine, idea, uid=_uid(),
                      operator_count=CONFIG.risk.max_open_positions)
    assert "PRACTICE" in result, result
    assert "MAX_POSITIONS" not in result


def test_a_practice_confirm_is_sized_on_the_practice_book_not_the_operators():
    """Both arms: twice the practice balance, twice the fill, with the
    operator's $50,000 unchanged beside both."""
    sizes = {}
    for balance in (10_000.0, 20_000.0):
        host, engine, idea, _ = _armed("paper")
        uid = _uid()
        engine.user_portfolios.get(uid).balance = balance
        result = _confirm(host, engine, idea, uid=uid)
        assert "PRACTICE" in result, result
        sizes[balance] = _size(result)
    assert sizes[20_000.0] == pytest.approx(2 * sizes[10_000.0], rel=1e-6)
    # And not the live execution ceiling the operator's book used to cut
    # the fill to: a $10,000 practice book sizes a practice-book figure.
    assert sizes[10_000.0] != pytest.approx(CONFIG.execution.max_live_position_usd)


def test_a_practice_evaluation_never_writes_the_operators_live_peak():
    host, engine, idea, _ = _armed("paper")
    uid = _uid()
    engine.risk._live_equity_peak = 135.10
    engine.risk._last_live_equity = 135.10
    result = _confirm(host, engine, idea, uid=uid)
    assert "PRACTICE" in result, result
    assert engine.risk._live_equity_peak == 135.10
    assert engine.risk._last_live_equity == 135.10
    practice_engine = engine.practice_risk_for(uid)
    assert practice_engine is not engine.risk
    # The same engine every time: one set of breakers for this person's book.
    assert engine.practice_risk_for(uid) is practice_engine


def test_the_practice_engine_is_its_own_engine_under_its_own_person():
    """Separate from the person's live engine, under a practice person key
    and with no cross-venue halt: a practice peak must never meet a later
    live account's equity, and a practice breach must halt nothing live."""
    host, engine, idea, _ = _armed("paper")
    uid = _uid()
    practice_engine = engine.practice_risk_for(uid)
    assert practice_engine._person_user_id == f"practice:{uid}"
    assert practice_engine._person_halt_fn is None
    was = CONFIG.per_user_live_enabled
    object.__setattr__(CONFIG, "per_user_live_enabled", True)
    try:
        engine._is_operator_user = lambda u: False
        live_engine = engine.risk_for(uid)
    finally:
        object.__setattr__(CONFIG, "per_user_live_enabled", was)
    assert live_engine is not practice_engine
    assert live_engine._person_user_id == uid
    assert live_engine._person_halt_fn is not None
    assert engine.practice_risk_for("") is engine.risk
    assert engine.practice_risk_for("auto") is engine.risk


def test_a_live_bots_critique_counts_the_practice_book_for_a_practice_confirm():
    """In live mode the critique counts the re-check's live count; a
    practice confirm has none, and it counts the practice book instead of
    an unread None."""
    from tests.test_the_critique_counts_the_book_the_trade_opens_on import _spy
    host, engine, idea, _ = _armed("paper")
    seen: list = []
    with _spy(seen):
        result = _confirm(host, engine, idea, uid=_uid())
    assert "PRACTICE" in result, result
    assert seen == [0]


def test_a_practice_confirm_needs_no_executor():
    """A caller with no account this bot can place on is refused a LIVE
    confirm in the venue's words. A practice confirm places nothing and is
    not asked for one."""
    host, engine, idea, _ = _armed("paper")
    engine._executor_for = lambda user_id="", venue="": None
    result = _confirm(host, engine, idea, uid=_uid())
    assert "PRACTICE" in result, result
    assert "no account of yours" not in result


def test_a_practice_confirm_authorizes_a_paper_trade_and_mints_no_live_token():
    host, engine, idea, _ = _armed("paper")
    result = _confirm(host, engine, idea, uid=_uid())
    assert "PRACTICE" in result, result
    engine.compliance.issue_approval_token.assert_not_called()
    kw = engine.compliance.authorize.call_args.kwargs
    assert kw["action"] == Permission.PAPER_TRADE
    assert kw["live_mode"] is False


def test_a_trader_confirm_still_authorizes_a_live_trade_with_a_token():
    host, engine, idea, _ = _armed("trader")
    result = _confirm(host, engine, idea, uid=_uid())
    assert result == "✅ LIVE order placed: BTC/USDT LONG"
    engine.compliance.issue_approval_token.assert_called_once()
    kw = engine.compliance.authorize.call_args.kwargs
    assert kw["action"] == Permission.LIVE_TRADE
    assert kw["live_mode"] is True
    engine.live_executor.execute.assert_awaited_once()


def test_a_practice_confirm_on_a_breached_practice_book_is_refused_by_its_own_breaker():
    """The practice book's own drawdown, not the operator's, gates the
    practice confirm: a practice book 50% under its peak is refused while
    the operator's account is untouched."""
    host, engine, idea, _ = _armed("paper")
    uid = _uid()
    book = engine.user_portfolios.get(uid)
    book.balance = 5_000.0
    book._peak_equity = 10_000.0
    result = _confirm(host, engine, idea, uid=uid)
    assert "REJECTED" in result, result
    assert "DRAWDOWN" in result
    assert engine.user_portfolios.get(uid).open_positions == []
    assert not engine.risk.circuit_breaker_active


# ── the one reading ───────────────────────────────────────────────────────

class _OptIn(_Roles):
    def __init__(self, role, opted, raises=False):
        super().__init__(role)
        self.opted = opted
        self.raises = raises

    def sim_opt_in(self, uid):
        if self.raises:
            raise OSError("store unreadable")
        return self.opted


def test_confirm_is_practice_is_one_reading():
    host, engine, idea, _ = _armed("trader")
    assert engine.confirm_is_practice(UID) is False
    engine._user_store = _Roles("paper")
    assert engine.confirm_is_practice(UID) is True
    assert engine.confirm_is_practice("") is False
    assert engine.confirm_is_practice("auto") is False
    was = CONFIG.paper_sim_opt_in_enabled
    try:
        engine._user_store = _OptIn("trader", True)
        object.__setattr__(CONFIG, "paper_sim_opt_in_enabled", False)
        assert engine.confirm_is_practice(UID) is False
        object.__setattr__(CONFIG, "paper_sim_opt_in_enabled", True)
        assert engine.confirm_is_practice(UID) is True
        engine._user_store = _OptIn("trader", False)
        assert engine.confirm_is_practice(UID) is False
        # Unreadable is not practice: the live doors still refuse.
        engine._user_store = _OptIn("trader", True, raises=True)
        assert engine.confirm_is_practice(UID) is False
    finally:
        object.__setattr__(CONFIG, "paper_sim_opt_in_enabled", was)


# ── the web surfaces ──────────────────────────────────────────────────────

def _web_cfg(monkeypatch):
    monkeypatch.setattr(ug, "_GATEWAY_SECRET", SECRET)
    monkeypatch.setattr(ug, "CONFIG", SimpleNamespace(
        telegram=SimpleNamespace(admin_ids=""), is_live=lambda: True))
    monkeypatch.setattr(ug, "_web_live_decision",
                        lambda app, h, tid: SimpleNamespace(
                            allowed=True, reason="every precondition met", checklist={}))


PAPER_WEB = {"web:9": {"authorized": True, "role": "paper", "can_trade_live": False}}
TRADER_WEB = {"web:9": {"authorized": True, "role": "trader", "can_trade_live": False}}


@pytest.mark.asyncio
async def test_live_mode_reads_paper_for_a_practice_caller_the_live_gate_would_admit(monkeypatch):
    _web_cfg(monkeypatch)
    async with gateway_client(FakeEngine(), FakeHandler(users=PAPER_WEB)) as c:
        r = await c.get("/trade/live_mode?telegram_id=web:9", headers=HDRS)
        body = await r.json()
        assert r.status == 200, body
        assert body == {"mode": "PAPER", "live_allowed": False, "practice": True,
                        "reason": PRACTICE_MODE_REASON}
    # The other arm: a caller the practice reading does not cover follows
    # the web live gate exactly as before.
    async with gateway_client(FakeEngine(), FakeHandler(users=TRADER_WEB)) as c:
        r = await c.get("/trade/live_mode?telegram_id=web:9", headers=HDRS)
        body = await r.json()
        assert (body["mode"], body["live_allowed"], body["practice"]) == ("LIVE", True, False)


@pytest.mark.asyncio
async def test_the_mode_asks_the_engines_reading_when_it_has_one(monkeypatch):
    """A trader whose engine says practice (an opt-in) reads PAPER too: the
    store's role alone is not the reading."""
    _web_cfg(monkeypatch)
    engine = FakeEngine()
    engine.confirm_is_practice = lambda uid: uid == "web:9"
    async with gateway_client(engine, FakeHandler(users=TRADER_WEB)) as c:
        r = await c.get("/trade/live_mode?telegram_id=web:9", headers=HDRS)
        body = await r.json()
        assert (body["mode"], body["practice"]) == ("PAPER", True)


@pytest.mark.asyncio
async def test_a_web_practice_confirm_says_it_is_practice(monkeypatch):
    _web_cfg(monkeypatch)
    engine = FakeEngine()
    async with gateway_client(engine, FakeHandler(users=PAPER_WEB)) as c:
        tid = await _propose(c, tg="web:9")
        r = await c.post("/trade/confirm",
                         json={"telegram_id": "web:9", "trade_id": tid}, headers=HDRS)
        body = await r.json()
        assert r.status == 200, body
        assert body["practice"] is True
        assert engine.confirm_calls == [(tid, "web:9")]
    trader = FakeEngine()
    async with gateway_client(trader, FakeHandler(users=AUTHED, live=True)) as c:
        tid = await _propose(c)
        r = await c.post("/trade/confirm",
                         json={"telegram_id": "7", "trade_id": tid}, headers=HDRS)
        body = await r.json()
        assert r.status == 200, body
        assert body["practice"] is False


# ── the Telegram surfaces ─────────────────────────────────────────────────

def _handler(role, *, engine_practice=None):
    h = th.TelegramHandler.__new__(th.TelegramHandler)
    h.users = _Roles(role)
    h.engine = SimpleNamespace(_is_operator_user=lambda tid: False)
    if engine_practice is not None:
        h.engine.confirm_is_practice = lambda uid, _p=engine_practice: _p
    h._has_own_exchange_keys = lambda tid: True
    h._allowlist_ids = lambda: set()
    return h


def test_a_self_admitted_key_holder_may_not_trade_live_under_the_open_policy(monkeypatch):
    monkeypatch.setattr(th, "CONFIG", SimpleNamespace(
        live_open_to_key_holders=True, per_user_live_enabled=True))
    assert _handler("paper")._can_trade_live(UID) is False
    assert _handler("trader")._can_trade_live(UID) is True
    # The engine's reading outranks the role: an opted-in trader is practice.
    assert _handler("trader", engine_practice=True)._can_trade_live(UID) is False


def test_a_granted_store_flag_does_not_make_a_practice_caller_live(monkeypatch):
    """Staged rollout (the open policy off): the store flag used to be the
    whole policy. A practice caller's confirm is practice whatever it says."""
    monkeypatch.setattr(th, "CONFIG", SimpleNamespace(
        live_open_to_key_holders=False, per_user_live_enabled=False))
    h = _handler("paper")
    h.users.can_trade_live = lambda tid: True
    assert h._can_trade_live(UID) is False
    h2 = _handler("trader")
    h2.users.can_trade_live = lambda tid: True
    assert h2._can_trade_live(UID) is True


def test_the_door_lets_a_practice_caller_through_on_the_engines_reading():
    """A trader the engine calls practice (an opt-in) reaches confirm_trade
    although it may not trade live; one it calls not-practice stops."""
    engine, replies = _confirm_tap("trader", live_ok=False, engine_practice=True)
    engine.confirm_trade.assert_awaited_once()
    engine, replies = _confirm_tap("trader", live_ok=False, engine_practice=False)
    engine.confirm_trade.assert_not_awaited()
    assert "Live trading not enabled" in replies
