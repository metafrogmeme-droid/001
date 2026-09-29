"""A retry places only the protection that is missing, and the record names what rests.

Every periodic SL/TP retry fired when EITHER leg was missing and re-placed the
PAIR through `_place_sl_tp`, then kept the record's own ids over the answer
(`if sl_id and not pos.sl_order_id`). The placer places first and cancels this
side's resting plan orders only once a NEW stop rests, so, driven on the real
placer's classic path:

  * a working stop and a refused target: a new stop was placed and the working
    one cancelled every pass, while the record went on naming the CANCELLED
    one beside an untracked live one;
  * a working stop and a refused new stop: nothing was cancelled, yet the
    record cleared the stop, marked the position unprotected and audited "the
    existing one was cancelled"; a 25588-family refusal then closed it at
    market beside the stop still resting on the venue;
  * a working target and a missing stop: the sweep cancelled the target and
    the record kept its id; with the stop refused on every pass, a fresh
    full-size target was added beside the held one each time (four resting
    after three passes).

`_protect_legs` is the rule: on the classic path, with one leg held, only the
missing leg is placed; a v3 (UTA) order carries both, so there the pair is
placed and its answer read by the placer's contract. `_place_missing_sltp` is
its record form, and the periodic retry, the grace retry, the grace sub-loop,
the post-fill ladder, the two cancel-race fills and the startup fix all ask
it. The market entry's and adoption's retry-once ask `_protect_legs`.
"""
from __future__ import annotations

import ast
import asyncio
import inspect
import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

import bot.core.live_executor as le
from bot.core.live_executor import Direction, LiveExecutor, LivePosition
from bot.core.venues import get_venue

SL, TP = 95.0, 110.0
BREACH = ("25588: For long position TP/SL (close long), the stop-loss trigger "
          "price must be less than the latest price")


def _row(oid: str, leg: str) -> dict:
    return {"id": oid, "side": "sell", "triggerPrice": SL if leg == "sl" else TP,
            "info": {"planType": "loss_plan" if leg == "sl" else "profit_plan",
                     "posSide": "long"}}


class Venue:
    """A classic Bitget account: a plan table that answers listings and
    cancels, and trigger orders answered per leg (by trigger level)."""

    markets: dict = {}

    def __init__(self, rows=(), *, sl=(), tp=(), price=100.0):
        self.rows = [dict(r) for r in rows]
        self.answers = {"sl": list(sl), "tp": list(tp)}
        self.placed: list[str] = []
        self.cancelled: list[str] = []
        self.price = price

    async def fetch_open_orders(self, *a, **k):
        return [dict(r) for r in self.rows]

    async def cancel_order(self, oid, *a, **k):
        self.cancelled.append(oid)
        self.rows = [r for r in self.rows if r["id"] != oid]
        return {"status": "canceled"}

    async def create_order(self, symbol, type, side, amount, price=None, params=None):
        leg = "sl" if params["triggerPrice"] == SL else "tp"
        self.placed.append(leg)
        answer = self.answers[leg].pop(0) if self.answers[leg] else RuntimeError("refused")
        if isinstance(answer, Exception):
            raise answer
        self.rows.append(_row(answer, leg))
        return {"id": answer}

    def price_to_precision(self, symbol, price):
        return str(price)

    def market(self, symbol):
        return {}

    async def fetch_ticker(self, *a, **k):
        return {"last": self.price, "timestamp": time.time() * 1000}

    def resting(self, leg: str) -> list[str]:
        plan = "loss_plan" if leg == "sl" else "profit_plan"
        return [r["id"] for r in self.rows if r["info"]["planType"] == plan]


def _executor(tmp_path, venue, *, uta=False) -> LiveExecutor:
    e = LiveExecutor(state_dir=str(tmp_path))
    e._venue = get_venue("bitget")
    e._exchange = venue
    e._get_exchange = AsyncMock(return_value=venue)
    e._is_uta = uta
    e._hedge_mode = False
    e.reconcile_positions = AsyncMock(return_value=[])
    e.adopt_exchange_positions = AsyncMock(return_value=[])
    e.adopt_exchange_limit_orders = AsyncMock(return_value=[])
    e._last_exchange_sync = time.time()
    e.close_position = AsyncMock(return_value="CLOSED")
    return e


def _pos(sl_id=None, tp_id=None, *, age=timedelta(minutes=30)) -> LivePosition:
    p = LivePosition(
        trade_id="T1", symbol="BTC/USDT", direction="LONG", entry_price=100.0,
        quantity=1.0, cost_usd=20.0, stop_loss=SL, take_profit=TP, leverage=5,
        status="open", opened_at=datetime.now(UTC) - age)
    p.sl_order_id = sl_id
    p.tp_order_id = tp_id
    return p


def _passes(e: LiveExecutor, pos: LivePosition, n: int = 1) -> None:
    e._positions[pos.trade_id] = pos
    for _ in range(n):
        asyncio.run(e.check_positions())


# ── The periodic retry, through the real placer's classic path ──────────────


class TestAWorkingStopIsNeverReplaced:
    def test_a_missing_target_is_placed_alone(self, tmp_path):
        v = Venue([_row("OLD-SL", "sl")], tp=["NEW-TP"])
        e = _executor(tmp_path, v)
        pos = _pos("OLD-SL", None)
        _passes(e, pos)
        assert v.placed == ["tp"], "the working stop was re-placed to add a target"
        assert v.cancelled == []
        assert (pos.sl_order_id, pos.tp_order_id) == ("OLD-SL", "NEW-TP")
        assert not getattr(pos, "unprotected", False)

    def test_a_refused_target_leaves_the_stop_alone_pass_after_pass(self, tmp_path):
        v = Venue([_row("OLD-SL", "sl")])
        e = _executor(tmp_path, v)
        pos = _pos("OLD-SL", None)
        _passes(e, pos, 3)
        assert v.placed == ["tp", "tp", "tp"]
        assert v.cancelled == [] and v.resting("sl") == ["OLD-SL"]
        assert pos.sl_order_id == "OLD-SL", (
            "the record must name the stop that rests, not one a sweep cancelled")
        assert not pos.tp_order_id
        assert not getattr(pos, "unprotected", False)
        e.close_position.assert_not_awaited()

    def test_a_breach_refusal_does_not_close_beside_a_resting_stop(self, tmp_path):
        # Re-placing the pair asked for a NEW stop, the venue refused it as
        # already breached, the record was cleared and the breach block closed
        # the position at market while the old stop still rested.
        v = Venue([_row("OLD-SL", "sl")], sl=[RuntimeError(BREACH)])
        e = _executor(tmp_path, v)
        pos = _pos("OLD-SL", None)
        _passes(e, pos)
        assert "sl" not in v.placed
        assert pos.sl_order_id == "OLD-SL"
        e.close_position.assert_not_awaited()


class TestAWorkingTargetIsNeverDoubled:
    def test_a_missing_stop_is_placed_alone(self, tmp_path):
        v = Venue([_row("OLD-TP", "tp")], sl=["NEW-SL"])
        e = _executor(tmp_path, v)
        pos = _pos(None, "OLD-TP")
        _passes(e, pos)
        assert v.placed == ["sl"]
        assert v.cancelled == []
        assert (pos.sl_order_id, pos.tp_order_id) == ("NEW-SL", "OLD-TP")
        assert not getattr(pos, "unprotected", False)

    def test_a_refused_stop_adds_no_target_pass_after_pass(self, tmp_path):
        v = Venue([_row("OLD-TP", "tp")])
        e = _executor(tmp_path, v)
        pos = _pos(None, "OLD-TP")
        _passes(e, pos, 3)
        assert v.placed == ["sl", "sl", "sl"]
        assert v.resting("tp") == ["OLD-TP"], "a fresh full-size target was added"
        assert pos.tp_order_id == "OLD-TP"
        assert not pos.sl_order_id
        assert getattr(pos, "unprotected", False) is True, (
            "a position that names no stop after the retry is unprotected")


class TestThePairWhereNothingIsHeld:
    def test_the_pair_is_placed_and_stale_rows_swept(self, tmp_path):
        v = Venue([_row("STALE-SL", "sl"), _row("STALE-TP", "tp")], sl=["NEW-SL"])
        e = _executor(tmp_path, v)
        pos = _pos(None, None)
        _passes(e, pos)
        assert v.placed == ["sl", "tp"]
        assert sorted(v.cancelled) == ["STALE-SL", "STALE-TP"]
        assert (pos.sl_order_id, pos.tp_order_id) == ("NEW-SL", None)

    def test_a_refused_pair_sweeps_nothing(self, tmp_path):
        v = Venue([_row("STALE-TP", "tp")])
        e = _executor(tmp_path, v)
        pos = _pos(None, None)
        _passes(e, pos)
        assert v.cancelled == []
        assert getattr(pos, "unprotected", False) is True


class TestAUtaAccountPlacesThePair:
    """A v3 strategy order carries both legs, so the pair is placed and its
    answer read by the placer's contract. The placer is stubbed: its v3 path
    posts to Bitget's REST API directly."""

    def _run(self, tmp_path, held, answer, *, uta=True, breach=False):
        v = Venue()
        e = _executor(tmp_path, v, uta=uta)
        e._place_sl_tp = AsyncMock(return_value=answer)
        pos = _pos(*held)
        if breach:
            e._note_sltp_error(pos.symbol, BREACH)
        _passes(e, pos)
        return e, pos

    def test_a_combined_answer_names_both_legs(self, tmp_path):
        e, pos = self._run(tmp_path, ("OLD-SL", None), ("C", "C"))
        assert (pos.sl_order_id, pos.tp_order_id) == ("C", "C"), (
            "one combined order was split across two names")

    def test_a_refused_pair_keeps_the_held_stop_and_does_not_close(self, tmp_path):
        e, pos = self._run(tmp_path, ("OLD-SL", None), (None, None), breach=True)
        e._place_sl_tp.assert_awaited_once()
        assert pos.sl_order_id == "OLD-SL"
        assert not getattr(pos, "unprotected", False)
        e.close_position.assert_not_awaited()

    def test_an_unresolved_account_type_places_the_pair(self, tmp_path):
        e, pos = self._run(tmp_path, ("OLD-SL", None), ("S2", "T2"), uta=None)
        e._place_sl_tp.assert_awaited_once()
        assert (pos.sl_order_id, pos.tp_order_id) == ("S2", "T2")


# ── The rule itself ─────────────────────────────────────────────────────────


def _legs(tmp_path, *, classic, held, one=None, pair=(None, None), levels=(SL, TP)):
    e = LiveExecutor(state_dir=str(tmp_path))
    e._venue = get_venue("bitget")
    e._is_uta = False if classic else True
    single = []

    async def _one(exchange, symbol, kind, direction, qty, level):
        single.append(kind)
        return one

    e._place_classic_trigger = _one
    e._place_sl_tp = AsyncMock(return_value=pair)
    got = asyncio.run(e._protect_legs(
        object(), "BTC/USDT", Direction.LONG, 1.0, levels[0], levels[1], *held))
    return got, single, e._place_sl_tp.await_count


@pytest.mark.parametrize("classic, held, one, pair, want, single, pairs", [
    (True, ("S", None), "T2", None, ("S", "T2"), ["tp"], 0),
    (True, ("S", None), None, None, ("S", None), ["tp"], 0),
    (True, (None, "T"), "S2", None, ("S2", "T"), ["sl"], 0),
    (True, (None, "T"), None, None, (None, "T"), ["sl"], 0),
    (True, (None, None), None, ("S2", "T2"), ("S2", "T2"), [], 1),
    (True, (None, None), None, ("S2", None), ("S2", None), [], 1),
    (True, (None, None), None, (None, "T2"), (None, "T2"), [], 1),
    (False, ("S", None), None, ("C", "C"), ("C", "C"), [], 1),
    (False, ("S", None), None, (None, None), ("S", None), [], 1),
    (False, (None, "T"), None, ("C", "C"), ("C", "C"), [], 1),
    (False, (None, "T"), None, (None, "T2"), (None, "T"), [], 1),
    # A placed stop means the placer swept this side's rows, the held target
    # among them: its refused new target is None, never the swept id.
    (False, (None, "T"), None, ("S2", None), ("S2", None), [], 1),
    (False, ("S", "T"), None, ("S2", "T2"), ("S2", "T2"), [], 1),
])
def test_the_answer_rule(tmp_path, classic, held, one, pair, want, single, pairs):
    got, placed_single, placed_pairs = _legs(
        tmp_path, classic=classic, held=held, one=one, pair=pair or (None, None))
    assert got == want
    assert placed_single == single and placed_pairs == pairs


def test_inverted_levels_go_to_the_placer_which_refuses_them(tmp_path):
    # A single-leg placement skips the placer's side-sanity check, so an
    # inverted pair takes the placer's path, where it is refused and recorded.
    got, single, pairs = _legs(tmp_path, classic=True, held=("S", None),
                               levels=(TP, SL))
    assert single == [] and pairs == 1
    assert got == ("S", None)


def test_the_record_form_writes_the_answer_and_returns_only_what_was_new(tmp_path):
    e = LiveExecutor(state_dir=str(tmp_path))
    e._venue = get_venue("bitget")
    e._is_uta = False
    e._place_classic_trigger = AsyncMock(return_value="T2")
    pos = _pos("S", None)
    assert asyncio.run(e._place_missing_sltp(object(), pos)) == (None, "T2")
    assert (pos.sl_order_id, pos.tp_order_id) == ("S", "T2")
    e._place_classic_trigger.assert_awaited_once()
    assert e._place_classic_trigger.await_args.args[2] == "tp"
    # A quantity handed in is the one placed.
    pos2 = _pos(None, "T")
    placed = asyncio.run(e._place_missing_sltp(object(), pos2, quantity=0.25))
    assert e._place_classic_trigger.await_args.args[4] == 0.25
    assert placed == ("T2", None), "the held target was reported as placed by this call"
    assert (pos2.sl_order_id, pos2.tp_order_id) == ("T2", "T")


# ── The other doors ─────────────────────────────────────────────────────────


def test_the_grace_retry_places_the_missing_target_alone(tmp_path):
    v = Venue([_row("OLD-SL", "sl")], tp=["NEW-TP"])
    e = _executor(tmp_path, v)
    pos = _pos("OLD-SL", None, age=timedelta(seconds=30))
    _passes(e, pos)
    assert v.placed == ["tp"] and v.cancelled == []
    assert (pos.sl_order_id, pos.tp_order_id) == ("OLD-SL", "NEW-TP")


def test_the_grace_sub_loop_adds_no_target_while_the_stop_is_refused(tmp_path):
    v = Venue([_row("OLD-TP", "tp")])
    e = _executor(tmp_path, v)
    pos = _pos(None, "OLD-TP")
    with patch("bot.core.live_executor.asyncio.sleep", new=AsyncMock()):
        asyncio.run(e._guard_unprotected_grace(v, pos))
    n = max(1, le.CONFIG.execution.unprotected_guard_max_iterations)
    assert v.placed == ["sl"] * n
    assert v.resting("tp") == ["OLD-TP"]
    assert pos.tp_order_id == "OLD-TP"


def test_the_post_fill_ladder_retries_the_stop_beside_the_first_target(tmp_path):
    v = Venue([_row("TP1", "tp")])
    e = _executor(tmp_path, v)
    pos = _pos(None, None)
    with patch("bot.core.live_executor.asyncio.sleep", new=AsyncMock()):
        sl_id, tp_id, msg = asyncio.run(e._reattempt_post_fill_sl(
            v, pos, Direction.LONG, 1.0, None, "TP1", "T1"))
    assert "tp" not in v.placed, "the ladder placed a second target beside the first"
    assert v.placed and set(v.placed) == {"sl"}
    assert tp_id == "TP1" and pos.tp_order_id == "TP1"
    assert sl_id is None and msg is not None, "retry and grace exhausted: flattened"


def test_the_post_fill_ladder_takes_a_stop_that_lands(tmp_path):
    v = Venue([_row("TP1", "tp")], sl=["SL2"])
    e = _executor(tmp_path, v)
    pos = _pos(None, None)
    sl_id, tp_id, msg = asyncio.run(e._reattempt_post_fill_sl(
        v, pos, Direction.LONG, 1.0, None, "TP1", "T1"))
    assert (sl_id, tp_id, msg) == ("SL2", "TP1", None)
    assert v.placed == ["sl"] and v.cancelled == []


def _verify(tmp_path, monkeypatch, pos, *, live_ids=None, stop_live=None, uta=False):
    v = Venue(sl=["NEW-SL"], tp=["NEW-TP"])
    e = _executor(tmp_path, v, uta=uta)
    e._place_sl_tp = AsyncMock(return_value=(None, None))
    e._live_protective_order_ids = AsyncMock(return_value=live_ids)
    e._stop_live_on_exchange = AsyncMock(return_value=stop_live)
    e._positions = {pos.trade_id: pos}
    monkeypatch.setattr(le, "CONFIG", SimpleNamespace(
        execution=SimpleNamespace(verify_classic_sltp_on_restart=True)))
    asyncio.run(e.verify_and_fix_sltp())
    return v, e


class TestTheStartupFix:
    def test_a_lost_stop_is_placed_and_the_resting_target_kept(self, tmp_path, monkeypatch):
        pos = _pos("SL-1", "TP-2")
        v, e = _verify(tmp_path, monkeypatch, pos, live_ids={"TP-2"})
        assert v.placed == ["sl"] and v.cancelled == []
        assert (pos.sl_order_id, pos.tp_order_id) == ("NEW-SL", "TP-2")

    def test_a_lost_target_is_placed_and_the_resting_stop_kept(self, tmp_path, monkeypatch):
        pos = _pos("SL-1", "TP-2")
        v, e = _verify(tmp_path, monkeypatch, pos, live_ids={"SL-1"})
        assert v.placed == ["tp"] and v.cancelled == []
        assert (pos.sl_order_id, pos.tp_order_id) == ("SL-1", "NEW-TP")

    def test_a_confirmed_missing_combined_order_leaves_no_dead_id(self, tmp_path, monkeypatch):
        pos = _pos("C", "C")
        v, e = _verify(tmp_path, monkeypatch, pos, stop_live=False, uta=True)
        e._place_sl_tp.assert_awaited_once()
        assert (pos.sl_order_id, pos.tp_order_id) == (None, None), (
            "a re-place that failed left the record naming an order the venue "
            "confirmed gone, so the periodic retry would never act")


# ── What the mutation round asked for ───────────────────────────────────────


def test_a_retry_that_leaves_no_stop_marks_and_saves_it_on_its_own(tmp_path):
    """The escalation alert sets the same marker, so its throttle is held: the
    retry's own mark is the only writer here, and the saved row carries it."""
    v = Venue([_row("OLD-TP", "tp")])
    e = _executor(tmp_path, v)
    pos = _pos(None, "OLD-TP")
    pos._unprotected_alert_at = time.time()
    _passes(e, pos)
    assert pos.unprotected is True and pos.sl_order_id == ""
    saved = json.loads(Path(e._positions_file).read_text())["T1"]
    assert saved["unprotected"] is True, "a restart would read it as protected"


class _Recorder:
    """Records each trigger order as the venue receives it."""

    def __init__(self, *, refuse=None, tick=None):
        self.sent: list[dict] = []
        self.refuse = refuse
        self.tick = tick

    def price_to_precision(self, symbol, price):
        return f"{price:.1f}" if self.tick else str(price)

    def market(self, symbol):
        return {}

    async def create_order(self, symbol, type, side, amount, price=None, params=None):
        self.sent.append({"symbol": symbol, "type": type, "side": side,
                          "amount": amount, "price": price, "params": dict(params)})
        if self.refuse:
            raise RuntimeError(self.refuse)
        return {"id": f"O{len(self.sent)}"}


def _trigger(tmp_path, ex, kind, direction, level):
    e = LiveExecutor(state_dir=str(tmp_path))
    e._venue = get_venue("bitget")
    oid = asyncio.run(e._place_classic_trigger(ex, "BTC/USDT", kind, direction, 1.0, level))
    return e, oid


@pytest.mark.parametrize("direction, side", [(Direction.LONG, "sell"), (Direction.SHORT, "buy")])
@pytest.mark.parametrize("kind", ["sl", "tp"])
def test_a_trigger_closes_the_position_it_protects(tmp_path, direction, side, kind):
    ex = _Recorder()
    _trigger(tmp_path, ex, kind, direction, 95.0)
    assert ex.sent[0]["side"] == side
    assert ex.sent[0]["params"]["reduceOnly"] is True


def test_a_trigger_is_sent_on_the_markets_grid(tmp_path):
    ex = _Recorder(tick=True)
    _trigger(tmp_path, ex, "sl", Direction.LONG, 95.04)
    assert ex.sent[0]["params"]["triggerPrice"] == 95.0, (
        "the level was sent off the tick grid the venue refuses (45115)")


def test_a_refused_stop_records_the_venues_reason(tmp_path):
    e, oid = _trigger(tmp_path, _Recorder(refuse="25606: price precision"),
                      "sl", Direction.LONG, 95.0)
    assert oid is None
    assert "25606" in e._last_sltp_reason("BTC/USDT")


def test_a_placed_target_keeps_the_refused_stops_reason(tmp_path):
    # On the classic pair the stop is asked first; a target that lands after
    # it must not wipe the only record of why the stop did not.
    e, _ = _trigger(tmp_path, _Recorder(refuse="25606: price precision"),
                    "sl", Direction.LONG, 95.0)
    asyncio.run(e._place_classic_trigger(_Recorder(), "BTC/USDT", "tp",
                                         Direction.LONG, 1.0, 110.0))
    assert "25606" in e._last_sltp_reason("BTC/USDT")
    # And a stop that lands does clear it.
    asyncio.run(e._place_classic_trigger(_Recorder(), "BTC/USDT", "sl",
                                         Direction.LONG, 1.0, 95.0))
    assert e._last_sltp_reason("BTC/USDT") == ""


# ── Who may call the placer directly ────────────────────────────────────────


FRESH_RECORD_CALLERS = {
    # Each places the FIRST protection for a record that names none yet, so
    # there is nothing held to keep and assigning the answer is the rule.
    "_place_recovered_stops": (1, "a recovered fill: the record is built here"),
    "adopt_exchange_positions": (1, "the first attempt on a newly adopted record"),
    "_place_entry_stops": (1, "the first attempt on a new position"),
    "execute": (1, "the emergency record after a post-order crash"),
    "_check_pending_limit": (1, "a resting limit that just filled"),
    "_adopt_partial_fill": (1, "a partial fill adopted as a new position"),
    "_execute_drift_market_fallback": (1, "a new market entry"),
    "_close_position_inner": (1, "the residual after a close: its ids were "
                                 "cleared on the line above"),
    "_protect_legs": (1, "the rule itself"),
}


def _callers() -> dict[str, int]:
    tree = ast.parse(Path(inspect.getsourcefile(LiveExecutor)).read_text())
    found: dict[str, int] = {}

    def walk(node, owner):
        for ch in ast.iter_child_nodes(node):
            name = ch.name if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef)) else owner
            if (isinstance(ch, ast.Call) and isinstance(ch.func, ast.Attribute)
                    and ch.func.attr == "_place_sl_tp"):
                found[owner] = found.get(owner, 0) + 1
            walk(ch, name)

    walk(tree, "<module>")
    return found


def test_every_direct_caller_of_the_placer_places_a_first_protection():
    """A caller that re-places over a record that already names orders goes
    through `_place_missing_sltp` or `_protect_legs`. A new direct caller has
    to be one of these, with its reason, or it is the defect again."""
    got = _callers()
    want = {k: n for k, (n, _why) in FRESH_RECORD_CALLERS.items()}
    assert got == want, (got, want)
    assert all(why for _n, why in FRESH_RECORD_CALLERS.values())


def test_the_rule_counts_a_nested_caller_under_its_own_name():
    tree_src = (
        "class X:\n"
        "    async def a(self):\n"
        "        await self._place_sl_tp()\n"
        "        async def inner():\n"
        "            await self._place_sl_tp()\n")
    tree = ast.parse(tree_src)
    found: dict[str, int] = {}

    def walk(node, owner):
        for ch in ast.iter_child_nodes(node):
            name = ch.name if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef)) else owner
            if (isinstance(ch, ast.Call) and isinstance(ch.func, ast.Attribute)
                    and ch.func.attr == "_place_sl_tp"):
                found[owner] = found.get(owner, 0) + 1
            walk(ch, name)

    walk(tree, "<module>")
    assert found == {"a": 1, "inner": 1}
