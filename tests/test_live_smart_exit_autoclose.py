"""
Gated live smart-exit auto-close (Audit dead-code resolution).

Paper positions already auto-close on smart-exit triggers (time stop,
signal-hold limit, VWAP reversion, volume decay) in _check_paper_positions, but
LIVE positions only got SL/TP — a thesis that invalidated rode all the way to
the exchange stop. _evaluate_live_smart_exits extends the SAME checks to live
positions, closing via the executor, gated behind
CONFIG.time_stop.live_auto_close_enabled (default OFF). These tests exercise the
gate, the trigger logic, and the fail-open posture in isolation.
"""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from bot.compat import UTC
from bot.core.engine import RuneClawEngine


def _pos(**kw):
    """A LivePosition-like object with the fields the evaluator reads."""
    from datetime import datetime
    base = dict(
        trade_id="t1", symbol="BTC/USDT", direction="LONG",
        entry_price=100.0, stop_loss=90.0, status="open",
        opened_at=datetime.now(UTC), signal_type="momentum_confluence",
        strategy_type="swing",
    )
    base.update(kw)
    return SimpleNamespace(**base)


class _Executor:
    def __init__(self, positions):
        self._positions = {p.trade_id: p for p in positions}
        self.closed: list[tuple[str, str]] = []
        self.raise_on_close = False

    async def close_position(self, trade_id, reason="bot_auto", close_price=0):
        if self.raise_on_close:
            raise RuntimeError("exchange down")
        self.closed.append((trade_id, reason))
        return f"CLOSED {trade_id}"


def _engine(executor, prices, vwap=None):
    eng = RuneClawEngine.__new__(RuneClawEngine)
    eng.ws_feed = SimpleNamespace(
        is_connected=lambda: True,
        get_prices=lambda max_age_sec=None: prices,
    )
    eng._last_vwap = vwap or {}
    eng._close_notify_callback = None
    return eng


def _cfg(enabled=True, live=True):
    """Patch CONFIG.time_stop with known flags."""
    p = patch("bot.core.engine.CONFIG")
    m = p.start()
    m.time_stop.enabled = enabled
    m.time_stop.live_auto_close_enabled = live
    return p, m


# A swing position held well past its time-stop window with no R progress.
def _stale_time_pos():
    from datetime import datetime
    old = datetime.now(UTC) - timedelta(hours=60)  # > 48 swing candles
    return _pos(opened_at=old, entry_price=100.0, stop_loss=90.0)


class TestGate:
    @pytest.mark.asyncio
    async def test_disabled_does_nothing(self):
        ex = _Executor([_stale_time_pos()])
        eng = _engine(ex, {"BTC/USDT": 100.5})  # flat: would time-exit
        p, _ = _cfg(live=False)
        try:
            await eng._evaluate_live_smart_exits(ex)
        finally:
            p.stop()
        assert ex.closed == []

    @pytest.mark.asyncio
    async def test_master_time_stop_off_does_nothing(self):
        ex = _Executor([_stale_time_pos()])
        eng = _engine(ex, {"BTC/USDT": 100.5})
        p, _ = _cfg(enabled=False, live=True)
        try:
            await eng._evaluate_live_smart_exits(ex)
        finally:
            p.stop()
        assert ex.closed == []


class TestTriggers:
    @pytest.mark.asyncio
    async def test_time_exit_closes(self):
        ex = _Executor([_stale_time_pos()])
        eng = _engine(ex, {"BTC/USDT": 100.5})  # ~0R, past window
        p, _ = _cfg()
        try:
            await eng._evaluate_live_smart_exits(ex)
        finally:
            p.stop()
        assert len(ex.closed) == 1
        assert ex.closed[0][0] == "t1"
        assert ex.closed[0][1].startswith("smart_exit:")

    @pytest.mark.asyncio
    async def test_healthy_position_kept(self):
        from datetime import datetime
        # Fresh, in profit → no trigger.
        pos = _pos(opened_at=datetime.now(UTC), entry_price=100.0, stop_loss=90.0)
        ex = _Executor([pos])
        eng = _engine(ex, {"BTC/USDT": 108.0})  # +0.8R, just opened
        p, _ = _cfg()
        try:
            await eng._evaluate_live_smart_exits(ex)
        finally:
            p.stop()
        assert ex.closed == []

    @pytest.mark.asyncio
    async def test_vwap_reversion_invalidation_closes(self):
        from datetime import datetime
        pos = _pos(
            opened_at=datetime.now(UTC), signal_type="vwap_reversion",
            direction="LONG", entry_price=100.0, stop_loss=99.0,
        )
        ex = _Executor([pos])
        # Price >0.3% above VWAP → LONG vwap-reversion target reached → exit.
        eng = _engine(ex, {"BTC/USDT": 100.2}, vwap={"BTC/USDT": 99.0})
        p, _ = _cfg()
        try:
            await eng._evaluate_live_smart_exits(ex)
        finally:
            p.stop()
        assert len(ex.closed) == 1

    @pytest.mark.asyncio
    async def test_non_open_status_skipped(self):
        ex = _Executor([_stale_time_pos()])
        ex._positions["t1"].status = "pending_fill"
        eng = _engine(ex, {"BTC/USDT": 100.5})
        p, _ = _cfg()
        try:
            await eng._evaluate_live_smart_exits(ex)
        finally:
            p.stop()
        assert ex.closed == []

    @pytest.mark.asyncio
    async def test_missing_price_skipped(self):
        ex = _Executor([_stale_time_pos()])
        eng = _engine(ex, {})  # no price for the symbol
        p, _ = _cfg()
        try:
            await eng._evaluate_live_smart_exits(ex)
        finally:
            p.stop()
        assert ex.closed == []


class TestTheAnswerIsRead:
    """close_position signals failure by RETURN VALUE. The smart exit used to
    discard the answer and notify "Smart-exit closed …" for a rejected close
    and for one the venue kept open."""

    @pytest.mark.asyncio
    async def test_a_rejected_close_is_not_announced_as_closed(self):
        ex = _Executor([_stale_time_pos()])
        ex.close_position = AsyncMock(return_value="CLOSE FAILED for t1: venue 5xx")
        eng = _engine(ex, {"BTC/USDT": 100.5})
        notes: list[str] = []

        async def _note(msg):
            notes.append(msg)
        eng._close_notify_callback = _note
        p, _ = _cfg()
        try:
            await eng._evaluate_live_smart_exits(ex)
        finally:
            p.stop()
        assert notes and "FAILED" in notes[0] and "still OPEN" in notes[0], notes
        assert not any(n.startswith("Smart-exit closed") for n in notes)

    @pytest.mark.asyncio
    async def test_a_close_kept_open_is_not_announced_as_closed(self):
        from bot.core.order_state import close_did_not_happen

        ex = _Executor([_stale_time_pos()])
        ex.close_position = AsyncMock(return_value="⚠️ CLOSE NOT CONFIRMED: LONG BTC/USDT\nkept OPEN")
        eng = _engine(ex, {"BTC/USDT": 100.5})
        notes: list[str] = []

        async def _note(msg):
            notes.append(msg)
        eng._close_notify_callback = _note
        p, _ = _cfg()
        try:
            await eng._evaluate_live_smart_exits(ex)
        finally:
            p.stop()
        assert notes and "DID NOT COMPLETE" in notes[0] and "CLOSE NOT CONFIRMED" in notes[0], notes
        assert not any(n.startswith("Smart-exit closed") for n in notes)
        assert close_did_not_happen(notes[0]), "the close card must not stand in for this note"

    @pytest.mark.asyncio
    async def test_a_position_another_path_closed_first_is_not_called_kept_open(self):
        """The 'not found or already closed/closing' answer sits in the
        kept-open bucket as 'not this caller's to close'. The note used to
        assert 'KEPT OPEN by close_position' over a position the monitor had
        just closed under the per-trade lock; it quotes the answer now."""
        ex = _Executor([_stale_time_pos()])
        ex.close_position = AsyncMock(return_value="Position t1 not found or already closed/closing.")
        eng = _engine(ex, {"BTC/USDT": 100.5})
        notes: list[str] = []

        async def _note(msg):
            notes.append(msg)
        eng._close_notify_callback = _note
        p, _ = _cfg()
        try:
            await eng._evaluate_live_smart_exits(ex)
        finally:
            p.stop()
        assert notes and "already closed or closing under another path" in notes[0], notes
        assert "not found or already closed/closing" in notes[0]
        assert "KEPT OPEN by close_position" not in notes[0]
        assert not any(n.startswith("Smart-exit closed") for n in notes)

    @pytest.mark.asyncio
    async def test_a_completed_close_is_announced_as_closed(self):
        ex = _Executor([_stale_time_pos()])
        eng = _engine(ex, {"BTC/USDT": 100.5})
        notes: list[str] = []

        async def _note(msg):
            notes.append(msg)
        eng._close_notify_callback = _note
        p, _ = _cfg()
        try:
            await eng._evaluate_live_smart_exits(ex)
        finally:
            p.stop()
        assert notes == [f"Smart-exit closed BTC/USDT: {notes[0].split(': ', 1)[1]}"]
        assert notes[0].startswith("Smart-exit closed BTC/USDT")


class TestFailOpen:
    @pytest.mark.asyncio
    async def test_close_error_is_swallowed(self):
        ex = _Executor([_stale_time_pos()])
        ex.raise_on_close = True
        eng = _engine(ex, {"BTC/USDT": 100.5})
        p, _ = _cfg()
        try:
            # Must not raise despite executor.close_position throwing.
            await eng._evaluate_live_smart_exits(ex)
        finally:
            p.stop()
        assert ex.closed == []
