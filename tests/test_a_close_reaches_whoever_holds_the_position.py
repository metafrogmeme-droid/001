"""A per-user account's close was published as the agent's own trade.

The engine's monitoring loops sweep `_all_live_executors()` -- the operator's
book AND every per-user one under PER_USER_LIVE_ENABLED -- and every close,
limit fill and sync notice they produced went to the same three callbacks,
which take a string and nothing else. Those callbacks were written when there
was one account. The close one:

  * attached the OPERATOR executor's last-close card (a per-user close of a
    symbol the operator had also just closed wore the operator's figures);
  * sent it to the operator's chats with no account named, and recorded it in
    the operator's transcript as a trade they had closed;
  * forwarded it to the PUBLIC marketing channels as the agent's own trade --
    `public_close_line` of the operator's slot when the symbols matched, or
    the user's private close text through the scrubber when they did not;
  * and told the person whose money it was nothing at all.

**The fix is at the boundary, not in the hook.** `_announce_executor_message`
routes each executor's message by WHOSE BOOK IT IS: the operator's goes to the
three callbacks exactly as before (so a single-account deploy is unchanged,
and the public channel still carries the agent's own trades), and a per-user
book's goes to its owner with ITS OWN last-close slot and to nothing else --
the rule `test_an_alert_about_my_position_reaches_only_me.py` records for
person-scoped alerts: *a position belongs to a person, the person is told,
and platform oversight has its own door in `/accounts`*.

**The operator's book is decided by IDENTITY, never by `user_id is None`.**
`None` meaning both "nobody in particular" and "the operator" is the
two-meanings-under-one-name defect this repo records for `size_usd`; a
per-user executor built without an id reaches the owner door with an empty
owner, which reaches no chat and says so, rather than quietly becoming the
operator's and being published.

**The renderer is shared, not copied.** The owner's close card is the same
`_deliver_close` the operator's is, with `public=False`, because a second copy
of a close card is a second answer about what a close looks like.
"""

from __future__ import annotations

import asyncio
import logging
import time
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

import bot.core.engine as engine_mod
from bot.core.engine import RuneClawEngine
from bot.skills.alerts_monitor import owner_chat_id
from bot.utils.logger import system_log
from tests.test_an_alert_is_in_the_transcript import _started, _store, _Users

OPERATOR_CHAT = "999"
OWNER = "111"
CLOSE = "BTC/USDT LONG closed\nPnL: +$5.00 (+1.20%)"
FILL = "LIMIT FILLED: ETH/USDT LONG @ $3,000"
SYNC = "SYNC: Adopted untracked position SOL/USDT from exchange"


# ── the engine: whose book produced the message ─────────────────────────

class _Ex:
    """A live executor that reports what one monitoring pass found."""

    def __init__(self, user_id, *, checked=(), reconciled=(), slot=None):
        self.user_id = user_id
        self._checked = list(checked)
        self._reconciled = list(reconciled)
        self._last_close_data = slot
        self._closed_trades: list = []
        self.open_positions: list = []

    async def check_positions(self):
        return list(self._checked)

    async def reconcile_positions(self):
        return list(self._reconciled)

    async def verify_and_fix_sltp(self):
        return None

    async def sync_positions_from_exchange(self):
        return None


def _engine():
    eng = RuneClawEngine.__new__(RuneClawEngine)
    eng.heard: list = []
    for name in ("close", "fill", "sync"):
        async def _cb(msg, _n=name):
            eng.heard.append(("operator", _n, msg))
        setattr(eng, f"_{name}_notify_callback", _cb)

    async def _owner(owner, kind, msg, slot):
        eng.heard.append((owner, kind, msg, slot))

    eng._owner_notify_callback = _owner
    return eng


def _announce(eng, ex, kind, msg):
    asyncio.run(eng._announce_executor_message(ex, kind, msg))


class TestTheEngineRoutesByBook:
    def test_the_operators_close_reaches_the_operators_hook(self):
        eng = _engine()
        eng.live_executor = _Ex(None)
        _announce(eng, eng.live_executor, "close", CLOSE)
        assert eng.heard == [("operator", "close", CLOSE)]

    def test_a_users_close_reaches_its_owner_and_nothing_else(self):
        eng = _engine()
        eng.live_executor = _Ex(None, slot={"symbol": "BTC/USDT", "pnl_usd": 99.0})
        mine = _Ex(OWNER, slot={"symbol": "BTC/USDT", "pnl_usd": 5.0})
        _announce(eng, mine, "close", CLOSE)
        assert eng.heard == [(OWNER, "close", CLOSE, mine._last_close_data)]

    @pytest.mark.parametrize("kind,msg", [("fill", FILL), ("sync", SYNC)])
    def test_a_users_fill_and_sync_reach_its_owner(self, kind, msg):
        eng = _engine()
        eng.live_executor = _Ex(None)
        _announce(eng, _Ex(OWNER), kind, msg)
        assert eng.heard == [(OWNER, kind, msg, None)]

    def test_an_owner_is_decided_by_identity_not_by_none(self):
        # A per-user executor with no id is NOT the operator's book. It goes
        # to the owner door with an empty owner, which reaches no chat.
        eng = _engine()
        eng.live_executor = _Ex(None)
        _announce(eng, _Ex(None), "close", CLOSE)
        assert eng.heard == [("", "close", CLOSE, None)]

    def test_an_unknown_kind_is_refused_on_either_book(self):
        eng = _engine()
        eng.live_executor = _Ex(None)
        for ex in (eng.live_executor, _Ex(OWNER)):
            with pytest.raises(ValueError):
                _announce(eng, ex, "bogus", CLOSE)

    def test_no_owner_door_installed_sends_nothing_anywhere(self):
        eng = _engine()
        eng._owner_notify_callback = None
        eng.live_executor = _Ex(None)
        _announce(eng, _Ex(OWNER), "close", CLOSE)
        assert eng.heard == []


@pytest.fixture
def live_multi_user():
    """A real engine, LIVE, with an operator book and one per-user book."""
    orig_live = type(engine_mod.CONFIG).is_live
    orig_pul = engine_mod.CONFIG.per_user_live_enabled
    orig_sync = engine_mod.sync_portfolio_with_exchange
    type(engine_mod.CONFIG).is_live = lambda self: True
    object.__setattr__(engine_mod.CONFIG, "per_user_live_enabled", True)

    async def _no_sync(_eng):
        return []

    engine_mod.sync_portfolio_with_exchange = _no_sync
    try:
        yield
    finally:
        type(engine_mod.CONFIG).is_live = orig_live
        object.__setattr__(engine_mod.CONFIG, "per_user_live_enabled", orig_pul)
        engine_mod.sync_portfolio_with_exchange = orig_sync


class TestEveryMonitoringSiteRoutes:
    """Driven through the real `_check_open_positions`: a scan of the call
    sites cannot see whether each one reaches the seam, which is the claim."""

    def test_each_book_is_heard_by_its_own_door(self, live_multi_user):
        real = RuneClawEngine()
        heard: list = []
        for name in ("close", "fill", "sync"):
            async def _cb(msg, _n=name):
                heard.append(("operator", _n, msg))
            setattr(real, f"_{name}_notify_callback", _cb)

        async def _owner(owner, kind, msg, slot):
            heard.append((owner, kind, msg))

        real._owner_notify_callback = _owner
        op_close = "ETH/USDT SHORT closed\nPnL: -$2.00 (-0.50%)"
        real.live_executor = _Ex(None, checked=[op_close])
        real._user_executors = {OWNER: _Ex(
            OWNER, checked=[FILL, SYNC, CLOSE],
            reconciled=["XRP/USDT reconciled closed"])}
        real._last_sltp_verify_ts = time.monotonic()
        asyncio.run(real._check_open_positions())
        assert heard == [
            ("operator", "close", op_close),
            (OWNER, "fill", FILL),
            (OWNER, "sync", SYNC),
            (OWNER, "close", CLOSE),
            (OWNER, "close", "XRP/USDT reconciled closed"),
        ], heard

    def test_a_single_account_deploy_is_unchanged(self, live_multi_user):
        object.__setattr__(engine_mod.CONFIG, "per_user_live_enabled", False)
        real = RuneClawEngine()
        heard: list = []
        for name in ("close", "fill", "sync"):
            async def _cb(msg, _n=name):
                heard.append((_n, msg))
            setattr(real, f"_{name}_notify_callback", _cb)
        real.live_executor = _Ex(None, checked=[FILL, CLOSE],
                                 reconciled=["XRP/USDT reconciled closed"])
        real._last_sltp_verify_ts = time.monotonic()
        asyncio.run(real._check_open_positions())
        assert heard == [("fill", FILL), ("close", CLOSE),
                         ("close", "XRP/USDT reconciled closed")]


class TestTheSmartExitRoutesByBook:
    """The fifth site. The smart-exit suite drives the OPERATOR's book only,
    so a mutation sending a per-user smart exit's note to the operator's hook
    survived the first round; it is driven on a per-user book here."""

    def test_a_users_smart_exit_note_reaches_its_owner(self):
        from tests.test_live_smart_exit_autoclose import _cfg, _Executor, _stale_time_pos
        from tests.test_live_smart_exit_autoclose import _engine as _se_engine

        mine = _Executor([_stale_time_pos()])
        mine.user_id = OWNER
        eng = _se_engine(mine, {"BTC/USDT": 100.5})
        eng.live_executor = _Executor([])      # the operator's, a different book
        operator_heard: list = []
        owner_heard: list = []

        async def _operator(msg):
            operator_heard.append(msg)

        async def _owner(owner, kind, msg, slot):
            owner_heard.append((owner, kind, msg))

        eng._close_notify_callback = _operator
        eng._owner_notify_callback = _owner
        p, _ = _cfg()
        try:
            asyncio.run(eng._evaluate_live_smart_exits(mine))
        finally:
            p.stop()
        assert operator_heard == [], operator_heard
        assert [(o, k) for o, k, _ in owner_heard] == [(OWNER, "close")], owner_heard


# ── the monitor: what each door sends, and where ────────────────────────

def _monitor(forwarder, admitted=(OPERATOR_CHAT, OWNER)):
    return _started(_store(), _Users(admitted), OPERATOR_CHAT,
                    forwarder=forwarder)


def _forwarder():
    return NS(set_bot=lambda b: None, post_signal=AsyncMock(),
              post_trade_closed=AsyncMock())


def _chats(w) -> list:
    return [str(kw.get("chat_id")) for kw in w.sent]


def _fire(w, name, *args):
    try:
        asyncio.run(w.engine.cbs[name](*args))
    finally:
        w.restore()


class TestTheOwnerDoor:
    def test_a_users_close_is_never_published(self):
        fw = _forwarder()
        w = _monitor(fw)
        _fire(w, "owner", OWNER, "close", CLOSE,
              {"symbol": "BTC/USDT", "pnl_usd": 5.0, "pnl_pct": 1.2})
        fw.post_trade_closed.assert_not_awaited()

    def test_a_users_close_reaches_the_owner_and_not_the_operator(self):
        w = _monitor(_forwarder())
        _fire(w, "owner", OWNER, "close", CLOSE, None)
        assert _chats(w) == [OWNER], w.sent

    def test_the_operators_own_close_is_still_published(self):
        # The public channel carries the agent's own trades; the door that
        # stopped publishing a user's must not stop publishing the agent's.
        fw = _forwarder()
        w = _monitor(fw)
        _fire(w, "close", CLOSE)
        fw.post_trade_closed.assert_awaited_once()
        assert _chats(w) == [OPERATOR_CHAT]

    def test_the_owners_card_is_read_from_the_owners_slot(self):
        # The operator's slot holds a close of the SAME symbol with a
        # different figure: a card read from the operator's slot would print
        # the operator's P&L on the owner's close.
        w = _monitor(_forwarder())
        w.engine.live_executor = NS(_last_close_data={
            "symbol": "BTC/USDT", "direction": "LONG", "pnl_usd": 987.65,
            "reason": "tp_hit"})
        mine = {"symbol": "BTC/USDT", "direction": "LONG", "pnl_usd": 5.0,
                "reason": "tp_hit"}
        _fire(w, "owner", OWNER, "close", CLOSE, mine)
        text = " ".join(str(kw.get("caption") or kw.get("text") or "")
                        for kw in w.sent)
        assert "987.65" not in text, text
        assert "+5.00" in text, text
        # The CARD path has its own send, and a recipient check on the
        # text-only path cannot see it: the first round sent this card to the
        # operator's chats and survived.
        assert any("photo" in kw for kw in w.sent), w.sent
        assert _chats(w) == [OWNER], w.sent

    @pytest.mark.parametrize("kind,msg,marker", [
        ("fill", FILL, "TRADE OPENED"), ("sync", SYNC, "EXCHANGE SYNC")])
    def test_a_users_fill_and_sync_reach_the_owner(self, kind, msg, marker):
        w = _monitor(_forwarder())
        _fire(w, "owner", OWNER, kind, msg, None)
        assert _chats(w) == [OWNER], w.sent
        assert marker in w.sent[0]["text"]

    def test_an_owner_with_no_chat_reaches_nobody_and_it_is_said(self):
        # `caplog` cannot see this: `bot.utils.logger` sets `propagate =
        # False`, so a handler goes on `system_log` itself -- the reason
        # `test_an_alert_about_my_position_reaches_only_me.py` records.
        seen: list = []

        class _Catch(logging.Handler):
            def emit(self, record):
                seen.append(record.getMessage())

        h = _Catch(level=logging.WARNING)
        system_log.addHandler(h)
        fw = _forwarder()
        w = _monitor(fw)
        try:
            _fire(w, "owner", "web:5", "close", CLOSE, None)
        finally:
            system_log.removeHandler(h)
        assert w.sent == []
        fw.post_trade_closed.assert_not_awaited()
        assert any("reached no chat" in m for m in seen), seen

    def test_the_close_is_in_the_owners_transcript_not_the_operators(self):
        s = _store()
        w = _started(s, _Users([OPERATOR_CHAT, OWNER]), OPERATOR_CHAT,
                     forwarder=_forwarder())
        _fire(w, "owner", OWNER, "close", CLOSE, None)
        assert [r["kind"] for r in s.recent_alerts(OWNER)] == ["TRADE_CLOSED"]
        assert s.recent_alerts(OPERATOR_CHAT) == []


class TestTheChatReading:
    @pytest.mark.parametrize("owner,chat", [
        ("111", 111), (" 222 ", 222), (333, 333),
        ("web:5", None), ("", None), (None, None), ("12a", None),
    ])
    def test_only_a_telegram_id_is_a_chat(self, owner, chat):
        assert owner_chat_id(owner) == chat
