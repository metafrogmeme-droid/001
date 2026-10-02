"""A resting entry order outlived every halt, and the venue filled it.

The executor's own comment said it: "Neither /halt nor any breaker cancels
resting limits; only /emergency_stop does." The drift market fallback was
guarded against the halt, and the resting order itself was not. It is an
entry placed before the refusal, and the venue matches it with no further
word from the bot. Driven with the kill switch engaged on the unfixed tree:
pass one left the order resting (no cancel), and when the venue filled it,
pass two booked "LIMIT FILLED", placed its stops and opened a position on an
account somebody had stopped. The kill switch clears the pending IDEAS in
memory; the ORDERS on the venue were never asked about. A typed ticket now
rests 24h, so the window is a day wide.

The monitor pass hands each executor a reading of the entry gate for its
account (`RuneClawEngine._entry_halt_reason`, which reads the raw fields the
pre-execute gate reads and is required to agree with `trade_gate.entry_gate`
on every planted state, since the money path may not ask the display helper), and a
resting order under a positive reading goes through the cancel flow an expiry
takes: the cancel is confirmed, the final fill is read, and a partial fill is
adopted with the idea's levels because that part is already a position. Only
a POSITIVE reading cancels. A gate that could not read one of its conditions
is not a halt, and cancelling a person's order on a reading nobody took would
be a guess dressed as caution.
"""
from __future__ import annotations

import asyncio
import tempfile
import time
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

import pytest

import bot.core.engine as engine_mod
from bot.core import live_executor as le
from bot.core.engine import RuneClawEngine
from bot.core.order_state import unfilled_order_heading
from bot.formatters.signal_card import _CLOSE_REASON_LABELS
from bot.utils.close_reason import NON_FILL_CLOSE_REASONS, is_filled_close
from tests.test_a_close_reaches_whoever_holds_the_position import _Ex
from tests.test_the_drift_fallback_places_what_was_approved import (
    _executor,
    _resting,
    _Venue,
)

HALT = "kill switch engaged"
OPEN = {"status": "open", "filled": 0}
CANCELLED = {"status": "canceled", "filled": 0, "average": None}
PART = {"status": "canceled", "filled": 0.4, "average": 100.0}


def _pending(pos, reads, *, entry_halt=HALT, momentum=True, venue=None):
    ex = _executor(tempfile.mkdtemp())
    ex._positions[pos.trade_id] = pos
    venue = venue or _Venue(100.2)
    ex._fetch_order = AsyncMock(side_effect=list(reads))
    ex._check_drift_market_fallback = AsyncMock(return_value=momentum)
    ex._execute_drift_market_fallback = AsyncMock(return_value="FALLBACK RAN")
    ex.sync_positions_from_exchange = AsyncMock(return_value=None)
    audits: list = []
    with patch.object(le, "audit", lambda log, msg, **kw: audits.append({"message": msg, **kw})):
        msg = asyncio.run(ex._check_pending_limit(
            venue, pos.trade_id, pos, entry_halt=entry_halt))
    return ex, venue, msg, audits


# ── the executor ─────────────────────────────────────────────────────────

class TestTheRestingOrder:

    @pytest.mark.parametrize("source", ["manual", "unknown"], ids=["typed", "engine"])
    def test_it_is_cancelled_under_a_halt(self, source):
        pos = _resting(idea_source=source)
        _, venue, msg, _ = _pending(pos, [OPEN, CANCELLED])
        assert venue.cancels == ["L1"]
        assert pos.status == "closed" and pos.close_reason == "entry_halted"
        assert pos.pnl_usd == 0.0
        assert msg.startswith("LIMIT CANCELLED (new entries refused): LONG SOL/USDT")
        assert HALT in msg and msg.endswith("Nothing was placed.")

    def test_the_message_is_read_as_an_unfilled_order(self):
        _, _, msg, _ = _pending(_resting(), [OPEN, CANCELLED])
        assert unfilled_order_heading(msg) is not None
        assert not RuneClawEngine._is_fill_message(msg)

    def test_a_partial_fill_at_the_cancel_is_adopted(self):
        pos = _resting()
        ex, venue, msg, _ = _pending(pos, [OPEN, PART])
        assert venue.cancels == ["L1"]
        assert pos.status == "open" and pos.quantity == 0.4
        assert ex._place_sl_tp.await_count == 1
        assert RuneClawEngine._is_fill_message(msg), msg

    def test_without_a_halt_it_keeps_resting(self):
        pos = _resting(idea_source="manual")
        _, venue, msg, _ = _pending(pos, [OPEN], entry_halt=None)
        assert msg is None and venue.cancels == [] and pos.status == "pending_fill"

    def test_the_drift_fallback_is_never_asked(self):
        """An engine limit the market ran from, with momentum behind it: the
        fallback would market it. Under a halt the cancel comes first."""
        pos = _resting(idea_source="unknown")
        ex, venue, msg, _ = _pending(pos, [OPEN, CANCELLED], venue=_Venue(104.9))
        assert ex._check_drift_market_fallback.await_count == 0
        assert ex._execute_drift_market_fallback.await_count == 0
        assert venue.orders == []
        assert msg.startswith("LIMIT CANCELLED (new entries refused)")

    def test_the_audit_names_what_refused_it(self):
        _, _, _, audits = _pending(_resting(idea_source="manual"), [OPEN, CANCELLED])
        (row,) = [a for a in audits if a.get("action") == "limit_halt_cancel"]
        assert row["result"] == "CANCELLED"
        assert row["data"]["refused_by"] == HALT and row["data"]["typed"] is True

    def test_a_cancel_the_venue_refused_keeps_it_tracked(self):
        """The cancel flow's own rule: an unconfirmed cancel is retried, never
        booked as cancelled over an order that may still rest."""
        pos = _resting()
        ex = _executor(tempfile.mkdtemp())
        ex._positions[pos.trade_id] = pos
        venue = _Venue(100.2)
        venue.cancel_order = AsyncMock(side_effect=RuntimeError("venue said no"))
        ex._fetch_order = AsyncMock(side_effect=[OPEN, OPEN])
        with patch.object(le, "audit", lambda *a, **k: None):
            msg = asyncio.run(ex._check_pending_limit(venue, pos.trade_id, pos, entry_halt=HALT))
        assert msg is None and pos.status == "pending_fill"


class TestAFillThatBeatTheCancel:

    def test_it_is_booked_and_says_it_was_resting_before_the_refusal(self):
        pos = _resting()
        ex, venue, msg, _ = _pending(
            pos, [{"status": "closed", "filled": 1.0, "average": 100.0}])
        assert pos.status == "open" and venue.cancels == []
        assert ex._place_sl_tp.await_count == 1
        assert msg.startswith("LIMIT FILLED:")
        assert f"New entries are refused ({HALT})" in msg
        assert "already resting" in msg

    def test_an_ordinary_fill_says_nothing_about_a_refusal(self):
        pos = _resting()
        _, _, msg, _ = _pending(
            pos, [{"status": "closed", "filled": 1.0, "average": 100.0}], entry_halt=None)
        assert "refused" not in msg


class TestThePositionsPassHandsItOn:

    def test_check_positions_passes_the_reading_to_every_resting_order(self):
        ex = _executor(tempfile.mkdtemp())
        pos = _resting()
        ex._positions[pos.trade_id] = pos
        venue = _Venue(100.2)
        ex._get_exchange = AsyncMock(return_value=venue)
        ex._probe_hold_mode_if_unknown = AsyncMock(return_value=None)
        ex._reconcile_unverified_submissions = AsyncMock(return_value=[])
        seen: list = []

        async def _rec(exchange, tid, p, entry_halt=None):
            seen.append(entry_halt)
            return None

        ex._check_pending_limit = _rec
        asyncio.run(ex.check_positions(entry_halt=HALT))
        asyncio.run(ex.check_positions())
        assert seen == [HALT, None]


class TestTheRecord:

    def test_an_order_cancelled_under_a_halt_is_not_a_trade(self):
        assert "entry_halted" in NON_FILL_CLOSE_REASONS
        assert is_filled_close("entry_halted", 0.0) is False

    def test_its_card_label_is_an_order_event(self):
        _icon, text = _CLOSE_REASON_LABELS["entry_halted"]
        assert text.startswith("Order")


# ── the engine's reading ─────────────────────────────────────────────────

class _Risk:
    def __init__(self, blocked=None, readable=True):
        self._blocked = blocked
        self._readable = readable

    @property
    def trading_blocked_by(self):
        if not self._readable:
            raise RuntimeError("unreadable")
        return self._blocked

    @property
    def circuit_breaker_active(self):
        if not self._readable:
            raise RuntimeError("unreadable")
        return bool(self._blocked)


def _engine(*, halted=False, shared=None, own=None):
    eng = RuneClawEngine.__new__(RuneClawEngine)
    eng._halted = halted
    eng.risk = shared or _Risk()
    own_engines = own or {}
    eng.risk_for = lambda uid: own_engines.get(uid, eng.risk)
    eng.live_executor = NS(user_id=None)
    return eng


class TestTheEnginesReading:

    def test_the_kill_switch_is_a_halt_for_every_account(self):
        eng = _engine(halted=True)
        assert eng._entry_halt_reason(eng.live_executor) == HALT
        assert eng._entry_halt_reason(NS(user_id="111")) == HALT

    def test_a_clear_gate_is_no_halt(self):
        eng = _engine()
        assert eng._entry_halt_reason(eng.live_executor) is None

    def test_a_users_own_breaker_halts_that_user_only(self):
        eng = _engine(own={"111": _Risk("daily_loss")})
        assert eng._entry_halt_reason(NS(user_id="111")) == "daily_loss"
        assert eng._entry_halt_reason(eng.live_executor) is None
        assert eng._entry_halt_reason(NS(user_id="222")) is None

    def test_the_operators_book_is_decided_by_identity(self):
        """The operator's executor asks for the empty id even when it carries
        a user id (an operator who linked an account), and a per-user executor
        asks for its own."""
        asked: list = []
        eng = _engine()
        eng.live_executor = NS(user_id="999")
        eng.risk_for = lambda uid: (asked.append(uid), eng.risk)[1]
        eng._entry_halt_reason(eng.live_executor)
        eng._entry_halt_reason(NS(user_id="111"))
        assert asked == ["", "111"]

    def test_an_unread_gate_is_not_a_halt(self):
        """Only a POSITIVE reading cancels a person's order."""
        eng = _engine(shared=_Risk(readable=False))
        assert eng._entry_halt_reason(eng.live_executor) is None

    def test_a_per_user_engine_that_cannot_be_read_leaves_the_shared_one(self):
        """``risk_for`` raising is an unread account, not a clear one, and not
        a halt; the shared engine it sits beside is still read."""
        eng = _engine(shared=_Risk("daily_loss"))

        def _boom(uid):
            raise RuntimeError("secret-bearing text")

        eng.risk_for = _boom
        assert eng._entry_halt_reason(NS(user_id="111")) == "daily_loss"
        eng.risk = _Risk()
        assert eng._entry_halt_reason(NS(user_id="111")) is None

    def test_a_breaker_read_through_the_narrow_flag_is_a_halt(self):
        class _Narrow:
            @property
            def trading_blocked_by(self):
                raise RuntimeError("probe failed")

            circuit_breaker_active = True

        eng = _engine(shared=_Narrow())
        assert eng._entry_halt_reason(eng.live_executor) == "circuit breaker open"

    def test_the_venue_auth_halt_carries_no_venue_text(self, monkeypatch):
        """The auth halt is a halt in live, and its reason is the category:
        the venue's own error text never reaches the owner's card."""
        monkeypatch.setattr(type(engine_mod.CONFIG), "is_live",
                            lambda self: True)
        eng = _engine()
        eng.live_auth_healthy = lambda uid: uid != "111"
        eng._live_auth_detail = {"111": "SECRETVALUE 40012 sign mismatch"}
        why = eng._entry_halt_reason(NS(user_id="111"))
        assert why and "venue auth" in why
        assert "SECRETVALUE" not in why and "40012" not in why
        assert eng._entry_halt_reason(eng.live_executor) is None

    def test_the_auth_halt_is_not_read_in_paper(self, monkeypatch):
        monkeypatch.setattr(type(engine_mod.CONFIG), "is_live",
                            lambda self: False)
        eng = _engine()
        eng.live_auth_healthy = lambda uid: False
        assert eng._entry_halt_reason(eng.live_executor) is None

    def test_an_auth_read_that_raises_is_not_a_halt(self, monkeypatch):
        monkeypatch.setattr(type(engine_mod.CONFIG), "is_live",
                            lambda self: True)
        eng = _engine()

        def _boom(uid):
            raise RuntimeError("unread")

        eng.live_auth_healthy = _boom
        assert eng._entry_halt_reason(eng.live_executor) is None

    def test_a_kill_switch_that_cannot_be_read_is_not_a_halt(self):
        class _Eng(RuneClawEngine):
            @property
            def _halted(self):
                raise RuntimeError("unread")

        eng = _Eng.__new__(_Eng)
        eng.risk = _Risk()
        eng.risk_for = lambda uid: eng.risk
        eng.live_executor = NS(user_id=None)
        assert eng._entry_halt_reason(eng.live_executor) is None

    def test_one_cause_on_one_engine_is_said_once(self):
        """The operator's own engine IS the shared one, so it is read twice;
        a reason said twice reads like two problems."""
        eng = _engine(shared=_Risk("daily_loss"))
        assert eng._entry_halt_reason(eng.live_executor) == "daily_loss"
        eng._halted = True
        assert eng._entry_halt_reason(eng.live_executor) == (
            "kill switch engaged; daily_loss")

    def test_the_money_path_does_not_ask_the_display_helper(self):
        """The parity guard's rule, held here too: the monitor's reading is
        the raw fields, so a display bug cannot become a trading bug."""
        import inspect

        from tests.source_scan import code_only

        src = code_only(inspect.getsource(RuneClawEngine._entry_halt_reason))
        assert "trade_gate" not in src and "entry_gate" not in src

    @pytest.mark.parametrize("state", [
        dict(),
        dict(halted=True),
        dict(shared="daily_loss"),
        dict(own="loss_streak:4"),
        dict(shared_unread=True),
        dict(own_raises=True),
        dict(own_raises=True, shared="warning_rate:x"),
        dict(auth_down=True, live=True),
        dict(auth_down=True, live=False),
        dict(halted=True, own="daily_loss", auth_down=True, live=True),
    ])
    def test_it_agrees_with_the_display_gate_on_blocked(self, state,
                                                       monkeypatch):
        """Two readings of one gate. The monitor may not ask the display
        helper, so this is where they are required to agree."""
        from bot.core.trade_gate import entry_gate

        live = bool(state.get("live"))
        monkeypatch.setattr(type(engine_mod.CONFIG), "is_live",
                            lambda self: live)
        shared = (_Risk(readable=False) if state.get("shared_unread")
                  else _Risk(state.get("shared")))
        own = _Risk(state.get("own")) if state.get("own") else None
        eng = _engine(halted=bool(state.get("halted")), shared=shared,
                      own={"111": own} if own else None)
        if state.get("own_raises"):
            def _boom(uid):
                raise RuntimeError("unread")
            eng.risk_for = _boom
        down = bool(state.get("auth_down"))
        eng.live_auth_healthy = lambda uid: not down
        eng._live_auth_detail = {}
        for ex, uid in ((eng.live_executor, ""), (NS(user_id="111"), "111")):
            mine = eng._entry_halt_reason(ex)
            gate = entry_gate(eng, uid, live=live, include_detail=False)
            assert (mine is not None) == bool(gate["blocked"]), (state, uid)


@pytest.fixture
def live_operator():
    orig_live = type(engine_mod.CONFIG).is_live
    orig_sync = engine_mod.sync_portfolio_with_exchange
    type(engine_mod.CONFIG).is_live = lambda self: True

    async def _no_sync(_eng):
        return []

    engine_mod.sync_portfolio_with_exchange = _no_sync
    try:
        yield
    finally:
        type(engine_mod.CONFIG).is_live = orig_live
        engine_mod.sync_portfolio_with_exchange = orig_sync


class TestTheMonitorPass:

    def test_each_executor_is_handed_its_accounts_reading(self, live_operator):
        real = RuneClawEngine()
        for name in ("close", "fill", "sync"):
            async def _cb(msg):
                return None
            setattr(real, f"_{name}_notify_callback", _cb)
        real.live_executor = _Ex(None)
        real._last_sltp_verify_ts = time.monotonic()
        real._halted = True
        real.live_auth_healthy = lambda uid: True
        asyncio.run(real._check_open_positions())
        assert real.live_executor.entry_halt == HALT
        real._halted = False
        asyncio.run(real._check_open_positions())
        assert real.live_executor.entry_halt is None


def _auth_shell():
    """Operator book with venue auth marked down and a balance cache."""
    eng = RuneClawEngine.__new__(RuneClawEngine)
    eng._halted = False
    eng.risk = _Risk()
    eng.risk_for = lambda uid: eng.risk
    eng._live_auth_ok = {}
    eng._live_auth_detail = {}
    eng._live_balance_cache = {}
    eng._live_balance_cache_ts = 0.0
    eng._LIVE_BALANCE_TTL = 30.0
    eng._user_live_balance_cache = {}
    eng._user_live_balance_cache_ts = {}
    eng.live_executor = NS(user_id=None, fetch_balance=AsyncMock())
    eng.set_live_auth_status(False, "40012 rejected")
    return eng


class TestVenueAuthCancelsOnlyWhileItIsDown:
    """Both arms. A real auth-down still cancels the resting order and places
    nothing. A balance the venue just authenticated does not cancel for
    "venue auth marked down"."""

    def test_a_failed_read_still_cancels_and_places_nothing(self, monkeypatch):
        monkeypatch.setattr(type(engine_mod.CONFIG), "is_live", lambda self: True)
        eng = _auth_shell()
        eng.live_executor.fetch_balance = AsyncMock(return_value={
            "error": "40012 rejected", "total": 0, "free": 0, "used": 0,
            "holdings": []})
        asyncio.run(eng.get_live_equity())
        assert eng.live_auth_healthy("") is False
        why = eng._entry_halt_reason(eng.live_executor)
        assert why and "venue auth marked down" in why
        pos = _resting()
        _, venue, msg, _ = _pending(pos, [OPEN, CANCELLED], entry_halt=why)
        assert venue.cancels == ["L1"]
        assert pos.status == "closed" and pos.close_reason == "entry_halted"
        assert msg.endswith("Nothing was placed.")
        assert "venue auth marked down" in msg

    def test_a_recovered_read_does_not_cancel_for_auth(self, monkeypatch):
        monkeypatch.setattr(type(engine_mod.CONFIG), "is_live", lambda self: True)
        eng = _auth_shell()
        eng.live_executor.fetch_balance = AsyncMock(return_value={
            "total": 388.33, "free": 120.0, "used": 0.0, "holdings": []})
        bal = asyncio.run(eng.get_live_equity())
        assert bal["total"] == 388.33
        assert eng.live_auth_healthy("") is True
        why = eng._entry_halt_reason(eng.live_executor)
        assert not why or "venue auth" not in why
        pos = _resting()
        _, venue, msg, _ = _pending(pos, [OPEN], entry_halt=why)
        assert venue.cancels == []
        assert pos.status == "pending_fill"
        assert msg is None

    def test_auto_sees_the_operators_down_flag(self, monkeypatch):
        """user_id "auto" is the operator's book. Asking live_auth_healthy
        ("auto") used to read a key nobody probes and default to healthy, so
        the limit was placed and the monitor cancelled it."""
        monkeypatch.setattr(type(engine_mod.CONFIG), "is_live", lambda self: True)
        eng = _auth_shell()
        assert eng.auth_account_id(eng.live_executor, "auto") == ""
        assert eng.live_auth_healthy(
            eng.auth_account_id(eng.live_executor, "auto")) is False
        from bot.core.trade_gate import entry_gate
        eng._executor_for = lambda uid="": eng.live_executor
        gate = entry_gate(eng, "auto", live=True, include_detail=False)
        assert gate["blocked"]
        assert any("venue auth marked down" in r for r in gate["reasons"])
