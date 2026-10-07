"""A trailing stop the price is already at or past closes the position.

The live trail computed its stop, and when the venue refused the move the
looser stop stayed in force. Once the move at the peak did not land, every
later tick proposed the same stop, now above the price for a long, and sent
it again to be refused again: the trail had stopped working and nothing said
so, while the backtest (which sets the stop and exits at the next bar's open
when the bar opens through it) booked the exit. Driven before the fix: a long
from 100 with its stop at 98, prices 106.5 then 104.0 then 103.2, the trail
proposing 105.5 each time, the stop still 98 and the position open.

``stop_rests`` is the one reading of whether a stop can rest at a price, and
the partial-TP ladder asks it too.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
import time
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

import bot.core.live_executor as le
from bot.config import CONFIG
from bot.core.live_executor import LiveExecutor, LivePosition, stop_rests
from bot.utils.close_reason import stop_exit_label
from bot.utils.trailing import make_trailing_state


def _executor(monkeypatch, *, refuse: bool = True, stale: bool = False):
    """A live executor whose venue answers one price at a time and refuses
    (or accepts) every stop move. Returns the executor, the price box, the
    stop-move calls and the closes."""
    monkeypatch.setattr(le.asyncio, "sleep", AsyncMock())
    ex = LiveExecutor()
    venue = AsyncMock()
    box = {"p": None}
    stamp = (time.time() - 3600) * 1000 if stale else None

    def _ticker(*_a, **_k):
        return {"last": box["p"], "timestamp": stamp or time.time() * 1000}

    venue.fetch_ticker = AsyncMock(side_effect=_ticker)
    ex._exchange = venue
    ex.reconcile_positions = AsyncMock(return_value=[])
    ex.adopt_exchange_positions = AsyncMock(return_value=[])
    ex.adopt_exchange_limit_orders = AsyncMock(return_value=[])
    ex._last_exchange_sync = time.time()
    ex._save_positions = lambda *a, **k: None
    ex._record_warning = lambda *a, **k: None
    ex.sync_positions_from_exchange = AsyncMock(return_value=None)
    ex._struct_candles = AsyncMock(return_value=None)
    moves: list = []
    closes: list = []

    async def _move(_exch, _pos, new_sl):
        moves.append((box["p"], round(new_sl, 4)))
        return not refuse

    async def _close(tid, reason, price=None, *a, **k):
        closes.append((reason, price))
        return f"CLOSED {reason}"

    ex._update_exchange_sl = _move
    ex.close_position = _close
    return ex, box, moves, closes


def _position(ex, direction: str, entry: float, sl: float, tp: float) -> LivePosition:
    p = LivePosition(
        trade_id="TI-trail", symbol="ETH/USDT", direction=direction,
        entry_price=entry, quantity=1.0, cost_usd=20.0, stop_loss=sl,
        take_profit=tp, leverage=5,
        opened_at=datetime.now(UTC) - timedelta(minutes=30),
        status="open", atr_at_entry=1.0)
    p.strategy_type = "swing"
    p.signal_type = "momentum_confluence"
    p.filled_at = p.opened_at
    p.sl_order_id = "OLD-SL"
    p.tp_order_id = "OLD-TP"
    p.trailing_state = make_trailing_state(entry, direction, abs(entry - sl), 1.0)
    p.partial_tp_state = {"ladder": "off", "reason": "isolate the trail"}
    ex._positions[p.trade_id] = p
    return p


async def _ticks(ex, box, prices):
    out = []
    for px in prices:
        box["p"] = px
        out.append(await ex.check_positions())
    return out


class TestTheReading:
    @pytest.mark.parametrize("direction,level,price,rests", [
        ("LONG", 99.0, 100.0, True),
        ("LONG", 100.0, 100.0, False),   # at the price: hit, not resting
        ("LONG", 101.0, 100.0, False),
        ("SHORT", 101.0, 100.0, True),
        ("SHORT", 100.0, 100.0, False),
        ("SHORT", 99.0, 100.0, False),
    ])
    def test_a_stop_rests_only_on_the_right_side_of_the_price(
            self, direction, level, price, rests):
        assert stop_rests(direction, level, price) is rests

    def test_the_ladder_asks_the_same_reading(self):
        """The ladder's closure is nested in a 400-line coroutine behind the
        venue, so this is a scan: its `_rests` answers ``stop_rests`` and
        spells no comparison of its own."""
        src = textwrap.dedent(inspect.getsource(LiveExecutor._run_partial_tp))
        tree = ast.parse(src)
        rests = [n for n in ast.walk(tree)
                 if isinstance(n, ast.FunctionDef) and n.name == "_rests"]
        assert len(rests) == 1
        body = rests[0].body
        returns = [n for n in ast.walk(rests[0]) if isinstance(n, ast.Return)]
        assert len(returns) == 1
        call = returns[0].value
        assert isinstance(call, ast.Call) and getattr(call.func, "id", "") == "stop_rests"
        assert not any(isinstance(n, ast.Compare) for s in body for n in ast.walk(s))


class TestACrossedTrailCloses:
    @pytest.mark.asyncio
    async def test_a_long_whose_trail_is_above_the_price_is_closed_at_market(self, monkeypatch):
        ex, box, moves, closes = _executor(monkeypatch)
        pos = _position(ex, "LONG", 100.0, 98.0, 120.0)
        await _ticks(ex, box, [106.5])
        # At the peak the trail stop rests below the price: sent, refused.
        assert moves == [(106.5, 105.5)]
        assert closes == []
        out = await _ticks(ex, box, [104.0])
        # The same stop is now above the price: never sent again, closed.
        assert moves == [(106.5, 105.5)]
        want = stop_exit_label(True, 100.0, 105.5, exit_price=104.0,
                               trailing_active=True)
        assert want == "TRAILING SL HIT"
        assert closes == [(want, 104.0)]
        # The close's message is handed on, so the engine announces it.
        assert out == [[f"CLOSED {want}"]]
        assert pos.stop_loss == 98.0   # the record never claimed a stop that did not rest

    @pytest.mark.asyncio
    async def test_a_short_mirrors_it(self, monkeypatch):
        ex, box, moves, closes = _executor(monkeypatch)
        _position(ex, "SHORT", 100.0, 102.0, 80.0)
        await _ticks(ex, box, [93.5, 96.0])
        assert moves == [(93.5, 94.5)]
        want = stop_exit_label(False, 100.0, 94.5, exit_price=96.0,
                               trailing_active=True)
        assert closes == [(want, 96.0)]

    @pytest.mark.asyncio
    async def test_a_trail_stop_exactly_at_the_price_is_crossed(self, monkeypatch):
        ex, box, moves, closes = _executor(monkeypatch)
        _position(ex, "LONG", 100.0, 98.0, 120.0)
        await _ticks(ex, box, [106.5, 105.5])
        assert moves == [(106.5, 105.5)]
        assert [c[1] for c in closes] == [105.5]

    @pytest.mark.asyncio
    async def test_a_trail_stop_that_rests_is_still_sent_and_nothing_is_closed(self, monkeypatch):
        ex, box, moves, closes = _executor(monkeypatch, refuse=False)
        pos = _position(ex, "LONG", 100.0, 98.0, 120.0)
        await _ticks(ex, box, [106.5])
        assert moves == [(106.5, 105.5)]
        assert closes == []
        assert pos.stop_loss == 105.5

    @pytest.mark.asyncio
    async def test_the_move_gate_is_not_asked_about_a_crossed_stop(self, monkeypatch):
        """``TRAILING_MIN_SL_UPDATE_PCT`` stops the bot spamming the venue
        with small moves; it does not decide whether the price has crossed
        the trail's stop. With the gate set so high that no move is sent,
        the crossed stop still closes."""
        old = CONFIG.trailing.min_sl_update_pct
        object.__setattr__(CONFIG.trailing, "min_sl_update_pct", 1e9)
        try:
            ex, box, moves, closes = _executor(monkeypatch)
            _position(ex, "LONG", 100.0, 98.0, 120.0)
            await _ticks(ex, box, [106.5, 104.0])
        finally:
            object.__setattr__(CONFIG.trailing, "min_sl_update_pct", old)
        assert moves == []          # the gate held back the move at the peak
        assert [c[1] for c in closes] == [104.0]

    @pytest.mark.asyncio
    async def test_a_wave_pivot_the_price_has_broken_closes_on_its_first_proposal(
            self, monkeypatch):
        """The wave ratchet reads confirmed pivots off closed candles; a
        pivot low above the live price is a structure the price has already
        broken, and its stop cannot rest from the first time it is read."""
        import bot.utils.trailing as trailing_mod
        ex, box, moves, closes = _executor(monkeypatch)
        ex._struct_candles = AsyncMock(return_value=([1.0], [1.0], [1.0]))
        monkeypatch.setattr(trailing_mod, "wave_ratchet",
                            lambda *a, **k: 106.8)
        _position(ex, "LONG", 100.0, 98.0, 120.0)
        await _ticks(ex, box, [106.5])
        assert moves == []
        assert [c[1] for c in closes] == [106.5]

    @pytest.mark.asyncio
    async def test_the_crossing_is_audited_at_warning(self, monkeypatch):
        seen = []
        real = le.audit

        def _audit(logger, message, **kw):
            seen.append((message, kw))
            return real(logger, message, **kw)

        monkeypatch.setattr(le, "audit", _audit)
        ex, box, moves, closes = _executor(monkeypatch)
        _position(ex, "LONG", 100.0, 98.0, 120.0)
        await _ticks(ex, box, [106.5, 104.0])
        crossed = [kw for _m, kw in seen if kw.get("result") == "CROSSED"]
        assert len(crossed) == 1
        kw = crossed[0]
        assert kw["action"] == "trailing_sl"
        assert kw["level"] == le.logging.WARNING
        assert kw["data"]["trail_sl"] == 105.5
        assert kw["data"]["old_sl"] == 98.0
        assert kw["data"]["price"] == 104.0

    @pytest.mark.asyncio
    async def test_a_sub_cent_crossing_states_both_levels(self, monkeypatch):
        # The same crossing at a PEPE-class price. `.4f` printed "the trail's
        # stop $0.0000 is at or past the price $0.0000": two zeros for two
        # measured levels. The structured data always held them; the line
        # the operator reads now does too.
        from bot.formatters.price_text import fmt_price
        seen = []
        real = le.audit

        def _audit(logger, message, **kw):
            seen.append((message, kw))
            return real(logger, message, **kw)

        monkeypatch.setattr(le, "audit", _audit)
        ex, box, moves, closes = _executor(monkeypatch)
        k = 1e-7
        _position(ex, "LONG", 100.0 * k, 98.0 * k, 120.0 * k)
        # The trail rests near 103e-7 at this scale; 101e-7 is under it.
        await _ticks(ex, box, [106.5 * k, 101.0 * k])
        crossed = [(m, kw) for m, kw in seen if kw.get("result") == "CROSSED"]
        assert len(crossed) == 1, [m for m, _ in seen]
        message, kw = crossed[0]
        assert not __import__("re").search(r"\$0\.0000(?!\d)", message), message
        assert fmt_price(kw["data"]["trail_sl"]) in message
        assert fmt_price(kw["data"]["price"]) in message
        # The refused move's line on the way there says it the same way.
        zero = __import__("re").compile(r"\$0\.0000(?!\d)")
        assert not [m for m, _ in seen if zero.search(m)]
        # And an accepted move's line.
        seen.clear()
        ex2, box2, _m2, _c2 = _executor(monkeypatch, refuse=False)
        _position(ex2, "LONG", 100.0 * k, 98.0 * k, 120.0 * k)
        await _ticks(ex2, box2, [106.5 * k])
        updated = [m for m, kw in seen if kw.get("result") == "UPDATED"]
        assert len(updated) == 1 and not zero.search(updated[0]), updated
        assert fmt_price(98.0 * k) in updated[0]

    @pytest.mark.asyncio
    async def test_the_trail_asks_the_one_reading(self, monkeypatch):
        """Plant a reading that says every stop rests: the crossed stop is
        sent again and nothing is closed, so the trail's decision is the
        module's reading and not a comparison of its own."""
        monkeypatch.setattr(le, "stop_rests", lambda *a, **k: True)
        ex, box, moves, closes = _executor(monkeypatch)
        _position(ex, "LONG", 100.0, 98.0, 120.0)
        await _ticks(ex, box, [106.5, 104.0])
        assert moves == [(106.5, 105.5), (104.0, 105.5)]
        assert closes == []

    @pytest.mark.asyncio
    async def test_a_stale_price_on_a_protected_position_closes_nothing(self, monkeypatch):
        """The staleness guard runs first: a frozen price must not drive a
        trail, and the venue's stop still protects the position."""
        ex, box, moves, closes = _executor(monkeypatch, stale=True)
        _position(ex, "LONG", 100.0, 98.0, 120.0)
        await _ticks(ex, box, [106.5, 104.0])
        assert moves == []
        assert closes == []


class TestTheLabel:
    @pytest.mark.parametrize("rule", ["multistage", "playbook", "legacy"])
    @pytest.mark.parametrize("direction", ["LONG", "SHORT"])
    def test_the_trail_reports_itself_active_whenever_its_stop_moved(self, rule, direction):
        """Why the crossing's label reads the same from the old stop and the
        new one: the trail answers ``trailing_active`` True on every tick
        whose stop moved, under every rule, and ``stop_exit_label`` answers
        TRAILING SL HIT for an active trail whatever the level. The day a
        rule moves the stop while saying it is inactive, this fails, and the
        label would then depend on which stop it was handed."""
        from bot.utils.trailing import update_trailing_stop
        entry, risk = 100.0, 2.0
        sl0 = entry - risk if direction == "LONG" else entry + risk
        state = make_trailing_state(entry, direction, risk, 1.0)
        if rule == "legacy":
            state.pop("stage", None)
        sign = 1 if direction == "LONG" else -1
        path = [entry + sign * d for d in
                (0.5, 1.5, 2.5, 4.0, 3.0, 6.5, 5.0, 9.0, 7.5, 12.0, 10.0, 4.0)]
        sl = sl0
        moved = 0
        for px in path:
            new, active = update_trailing_stop(state, px, sl, direction,
                                               trail_atr_mult=1.0, rule=rule,
                                               playbook_atr_mult=1.0)
            if new != sl:
                moved += 1
                assert active is True, (rule, direction, px, sl, new)
            sl = new
        assert moved > 0   # the path really moves the stop under this rule
