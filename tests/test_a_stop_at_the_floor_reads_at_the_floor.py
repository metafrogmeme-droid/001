"""A stop the analyzer floors to MIN_STOP_DISTANCE_PCT reads at the floor.

The analyzer widens a too-tight stop to exactly ``floor * entry`` from the
entry, and the risk gate refuses a stop whose distance is UNDER the floor.
Both read the same number, so a floored stop should always pass. It passed
about half the time. The floor is computed on the unrounded entry, the idea
is then recorded with each level rounded on its own, and the gate divides the
rounded distance by the rounded entry: float noise plus two roundings put the
ratio a hair under the floor for roughly one floored stop in two. On the
frozen majors snapshot, 202 of the 430 ideas the floor widened were refused
on STOP_DISTANCE, 23 of them on that reason alone.

``record_idea_levels`` records the three levels together and, for a stop that
sat at or beyond the floor before rounding, steps it outward by the recorded
precision until the gate's own predicate (``stop_under_floor``) reads it at
the floor. A stop that was under the floor before rounding is not stepped:
that one the gate is right to refuse.
"""
from __future__ import annotations

import ast
import asyncio
import inspect
import textwrap
from types import SimpleNamespace as NS

import pytest

import bot.core.analyzer as an
import bot.core.signal_levels as sl
from bot.core.engine import RuneClawEngine
from bot.core.signal_levels import (
    FLOOR_STEPS,
    floor_steps,
    level_places,
    record_idea_levels,
    record_level,
    stop_under_floor,
)
from bot.risk.portfolio import PortfolioTracker
from bot.risk.risk_engine import RiskEngine
from bot.utils.models import Direction, TradeIdea

F = 0.004
MAGNITUDES = [10 ** (-7 + 12 * k / 199) for k in range(200)]


def _floored(entry: float, direction: Direction) -> float:
    """A stop well inside the floor, widened by the analyzer's own floor."""
    tight = entry * (1 - 0.001) if direction == Direction.LONG else entry * (1 + 0.001)
    return an._floor_stop_distance(entry, tight, direction, F)


def _target(entry: float, direction: Direction) -> float:
    return entry * 1.03 if direction == Direction.LONG else entry * 0.97


class TestTheSeam:
    @pytest.mark.parametrize("direction", [Direction.LONG, Direction.SHORT])
    def test_a_floored_stop_reads_at_the_floor_at_every_magnitude(self, direction):
        long = direction == Direction.LONG
        old_under = new_under = 0
        for p in MAGNITUDES:
            s = _floored(p, direction)
            if stop_under_floor(record_level(p), record_level(s), F):
                old_under += 1
            e, s2, _t = record_idea_levels(p, s, _target(p, direction),
                                           is_long=long, floor=F)
            if stop_under_floor(e, s2, F):
                new_under += 1
        # The fixture reaches the defect: recorded one by one, many read under.
        assert old_under > 20
        assert new_under == 0

    def test_the_analyzers_eight_places_read_under_too(self):
        # price_decimals is 8 below $1; the seam records to at least that.
        old = new = 0
        for p in MAGNITUDES:
            if p >= 1.0:
                continue
            s = _floored(p, Direction.LONG)
            if stop_under_floor(record_level(p, 8), record_level(s, 8), F):
                old += 1
            e, s2, _ = record_idea_levels(p, s, p * 1.03, is_long=True,
                                          floor=F, min_places=8)
            if stop_under_floor(e, s2, F):
                new += 1
        assert old > 0
        assert new == 0

    def test_a_stop_under_the_floor_is_not_stepped(self):
        for p in (0.0000123, 0.0421, 1.2345, 63000.0):
            s = p * (1 - F * 0.9)
            e, s2, _ = record_idea_levels(p, s, p * 1.03, is_long=True, floor=F)
            assert s2 == record_level(s)
            assert stop_under_floor(e, s2, F)

    @pytest.mark.parametrize("direction", [Direction.LONG, Direction.SHORT])
    def test_steps_go_outward_only_and_at_most_three_units(self, direction):
        long = direction == Direction.LONG
        stepped = 0
        for p in MAGNITUDES:
            s = _floored(p, direction)
            _e, s2, _ = record_idea_levels(p, s, _target(p, direction),
                                           is_long=long, floor=F)
            base = record_level(s)
            unit = 10.0 ** -level_places(base)
            if long:
                assert s2 <= base
            else:
                assert s2 >= base
            budget = floor_steps(level_places(record_level(p)), level_places(base))
            assert abs(s2 - base) <= budget * unit * (1 + 1e-9)
            if s2 != base:
                stepped += 1
        assert stepped > 0

    def test_the_entry_and_target_are_recorded_as_before(self):
        for p in MAGNITUDES:
            s = _floored(p, Direction.LONG)
            e, _s, t = record_idea_levels(p, s, p * 1.03, is_long=True, floor=F)
            assert e == record_level(p)
            assert t == record_level(p * 1.03)

    @pytest.mark.parametrize("floor", [0.0, -0.004])
    def test_no_floor_steps_nothing(self, floor):
        for p in MAGNITUDES:
            s = _floored(p, Direction.LONG)
            got = record_idea_levels(p, s, p * 1.03, is_long=True, floor=floor)
            assert got == (record_level(p), record_level(s), record_level(p * 1.03))

    def test_an_entry_that_is_no_price_steps_nothing(self):
        for entry in (0.0, -1.0):
            got = record_idea_levels(entry, entry - 0.5, entry + 1.0,
                                     is_long=True, floor=F)
            assert got == (record_level(entry), record_level(entry - 0.5),
                           record_level(entry + 1.0))

    def test_the_predicate_is_the_gates_arithmetic(self):
        assert stop_under_floor(100.0, 99.87, F)
        assert not stop_under_floor(100.0, 99.2, F)
        assert not stop_under_floor(100.0, 99.87, 0.0)
        assert not stop_under_floor(0.0, 99.87, F)
        assert stop_under_floor(100.0, 100.13, F)
        assert not stop_under_floor(-1.0, -1.2, F)
        # 4 / 1000 is the double nearest 0.004, so this stop is AT the floor:
        # at the floor is not under it.
        assert 4.0 / 1000.0 == F
        assert not stop_under_floor(1000.0, 996.0, F)
        assert not stop_under_floor(1000.0, 1004.0, F)


def _gate(tmp_path, entry, stop, tp, direction=Direction.LONG):
    risk = RiskEngine(PortfolioTracker(initial_balance=10_000.0),
                      state_file=str(tmp_path / "risk_state.json"))
    idea = TradeIdea(
        id="TI-FLOOR", asset="TEST/USDT", direction=direction,
        entry_price=entry, stop_loss=stop, take_profit=tp,
        confidence=0.9, reasoning="floor", signals_used=["rsi"],
        strategy_type="swing", order_type="limit", source="unknown")
    check = risk.evaluate(idea, atr=abs(entry - stop))
    return ([x for x in check.checks_passed if x.startswith("STOP_DISTANCE")],
            [x for x in check.checks_failed if x.startswith("STOP_DISTANCE")])


def _a_price_the_old_recording_refused():
    for p in MAGNITUDES:
        s = _floored(p, Direction.LONG)
        if stop_under_floor(record_level(p), record_level(s), F):
            return p, s
    raise AssertionError("no magnitude reaches the defect")


class TestTheRiskGate:
    def test_the_gate_refused_the_old_recording(self, tmp_path):
        p, s = _a_price_the_old_recording_refused()
        passed, failed = _gate(tmp_path, record_level(p), record_level(s),
                               record_level(p * 1.03))
        assert passed == [] and len(failed) == 1

    def test_the_gate_passes_the_seams_recording(self, tmp_path):
        p, s = _a_price_the_old_recording_refused()
        e, s2, t = record_idea_levels(p, s, p * 1.03, is_long=True, floor=F)
        passed, failed = _gate(tmp_path, e, s2, t)
        assert failed == [] and len(passed) == 1

    def test_the_gate_still_refuses_a_stop_under_the_floor(self, tmp_path):
        e, s2, t = record_idea_levels(100.0, 99.87, 102.0, is_long=True, floor=F)
        passed, failed = _gate(tmp_path, e, s2, t)
        assert passed == [] and "0.130% < 0.40% floor" in failed[0]

    def test_the_gate_asks_the_predicate(self, tmp_path, monkeypatch):
        import bot.risk.risk_engine as re_mod
        monkeypatch.setattr(re_mod, "stop_under_floor", lambda *a: True)
        passed, failed = _gate(tmp_path, 100.0, 99.2, 102.0)
        assert passed == [] and len(failed) == 1


class TestTheSeamAsksThePredicate:
    def test_a_planted_never_under_steps_nothing(self, monkeypatch):
        p, s = _a_price_the_old_recording_refused()
        monkeypatch.setattr(sl, "stop_under_floor", lambda *a: False)
        _e, s2, _ = record_idea_levels(p, s, p * 1.03, is_long=True, floor=F)
        assert s2 == record_level(s)

    def test_a_planted_always_under_steps_the_limit(self, monkeypatch):
        p = 1.2345
        s = _floored(p, Direction.LONG)
        monkeypatch.setattr(sl, "stop_under_floor", lambda *a: True)
        _e, s2, _ = record_idea_levels(p, s, p * 1.03, is_long=True, floor=F)
        base = record_level(s)
        unit = 10.0 ** -level_places(base)
        assert s2 == pytest.approx(base - FLOOR_STEPS * unit, rel=0, abs=unit / 10)


def _refine(p: float, direction: Direction):
    long = direction == Direction.LONG
    if long:
        lows = [p] * 48
        lows[30] = p * 0.991
        lows[42] = p * 0.97
        candles = [[i, p, p, lows[i], p, 1.0] for i in range(48)]
        e = p * 0.991 + (p - p * 0.991) * 0.2
        stop, tp = e - e * F * (1 + 1e-7), p * 1.03
    else:
        highs = [p] * 48
        highs[30] = p * 1.009
        highs[42] = p * 1.03
        candles = [[i, p, highs[i], p, p, 1.0] for i in range(48)]
        e = p * 1.009 - (p * 1.009 - p) * 0.2
        stop, tp = e + e * F * (1 + 1e-7), p * 0.97
    idea = TradeIdea(
        id="TI-REFINE", asset="TEST/USDT", direction=direction,
        entry_price=p, stop_loss=stop, take_profit=tp, confidence=0.7,
        reasoning="refine", signals_used=["rsi"], strategy_type="swing",
        order_type="limit", source="unknown")

    async def cached(ex, sym, tf, limit=48, ttl=60):
        return candles

    out = asyncio.run(RuneClawEngine._refine_entry_mtf(
        NS(_cached_ohlcv=cached), idea, None))
    return out, e, stop


class TestTheRefinement:
    @pytest.mark.parametrize("direction", [Direction.LONG, Direction.SHORT])
    def test_a_refined_stop_at_the_floor_reads_at_the_floor(self, direction):
        old_under = 0
        for p in MAGNITUDES:
            out, e, stop = _refine(p, direction)
            assert out.entry_price != p  # the refinement ran
            # recorded to at least the eight places it always kept
            assert out.entry_price == record_level(e, 8)
            if stop_under_floor(round(e, 8), round(stop, 8), F):
                old_under += 1
            assert not stop_under_floor(out.entry_price, out.stop_loss, F), p
        assert old_under > 20


def _function(src: str, name: str) -> ast.AST:
    tree = ast.parse(textwrap.dedent(src))
    hits = [n for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name]
    assert len(hits) == 1
    return hits[0]


def _calls(fn: ast.AST, name: str) -> list[ast.Call]:
    return [n for n in ast.walk(fn) if isinstance(n, ast.Call)
            and getattr(n.func, "id", getattr(n.func, "attr", None)) == name]


class TestTheCallSites:
    """Scans, stated as scans: `analyze` is a 1,400-line coroutine behind a
    thesis model and the refinement's two branches are drives above; what is
    pinned here is that neither records a level on its own any more."""

    def test_analyze_records_the_idea_through_the_seam(self):
        fn = _function(inspect.getsource(an.Analyzer), "analyze")
        calls = _calls(fn, "record_idea_levels")
        assert len(calls) == 1
        kw = {k.arg: ast.unparse(k.value) for k in calls[0].keywords}
        assert kw == {"is_long": "direction == Direction.LONG",
                      "floor": "CONFIG.analyzer.min_stop_distance_pct",
                      "min_places": "price_decimals"}
        assert [ast.unparse(a) for a in calls[0].args] == [
            "entry", "stop_loss", "take_profit"]
        rounded = [c for c in _calls(fn, "round") if len(c.args) == 2
                   and isinstance(c.args[1], ast.Name)
                   and c.args[1].id == "price_decimals"]
        assert rounded == []

    def test_the_refinement_records_both_branches_through_the_seam(self):
        fn = _function(inspect.getsource(RuneClawEngine._refine_entry_mtf),
                       "_refine_entry_mtf")
        assert len(_calls(fn, "record_idea_levels")) == 2
        rounded = [c for c in _calls(fn, "round")
                   if c.args and isinstance(c.args[0], ast.Name)
                   and c.args[0].id.startswith("refined_")]
        assert rounded == []

    def test_the_gate_reads_the_floor_through_the_predicate(self):
        import bot.risk.risk_engine as re_mod
        src = inspect.getsource(re_mod.RiskEngine)
        assert "stop_under_floor(idea.entry_price, idea.stop_loss, _stop_floor)" in src
        assert "if _stop_dist < _stop_floor" not in src


# ── an entry recorded on a coarser grid than its stop ──────────────────────
# `record_level` keeps significant digits, so a long whose entry sits within
# 0.4% above a power of ten records the entry with one decimal place fewer
# than its stop. The entry's rounding is then up to five of the stop's
# units, and three steps could not clear it: driven over 20,000 such longs,
# about one in five still read under the floor and the gate refused it.

#: Entries in [10^k, 1.004 * 10^k), the band where the two grids differ,
#: with digits past the entry's grid: a round entry records exactly and has
#: no rounding for the stop to clear. Seeded, so every run reads the same.
_rng = __import__("random").Random(466)
_BAND = [10.0 ** k * (1 + _rng.random() * 0.004) for k in range(-7, -1) for _ in range(60)]


@pytest.mark.parametrize("min_places", [6, 8])
@pytest.mark.parametrize("direction", [Direction.LONG, Direction.SHORT])
def test_a_floored_stop_reads_at_the_floor_across_a_power_of_ten(direction, min_places, monkeypatch):
    long = direction == Direction.LONG
    band = _BAND

    def under():
        n = 0
        for p in band:
            s = _floored(p, direction)
            e, s2, _ = record_idea_levels(p, s, _target(p, direction), is_long=long,
                                          floor=F, min_places=min_places)
            n += stop_under_floor(e, s2, F)
        return n

    assert under() == 0
    if long:
        # The fixture can produce the state: on the one-grid budget it can't.
        monkeypatch.setattr(sl, "floor_steps", lambda e, s: FLOOR_STEPS)
        assert under() > 0


def test_the_budget_counts_the_entrys_half_unit_in_stop_units():
    assert floor_steps(6, 6) == FLOOR_STEPS
    assert floor_steps(8, 7) == FLOOR_STEPS        # a short: the stop is the coarser
    assert floor_steps(7, 8) == FLOOR_STEPS + 5


def test_the_reviews_idea_passes_the_gate(tmp_path):
    entry = 0.01000044
    stop = _floored(entry, Direction.LONG)
    e, s2, t = record_idea_levels(entry, stop, entry * 1.03, is_long=True, floor=F)
    passed, failed = _gate(tmp_path, e, s2, t)
    assert failed == [] and passed, (e, s2, failed)
