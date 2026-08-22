"""Three cards a tick is not a rate limit.

The anomaly path already suppressed repeats well: same key, unchanged severity
tier, inside a window — 30 minutes mild, 15 severe. It also capped how many
severe cards one pass could emit, at three, and named the rest in a trailing
overflow line rather than dropping them.

None of that bounds a MARKET-WIDE EVENT, because such an event does not repeat
a key. It mints new ones. Every fresh `(type, symbol)` pair is a first sighting,
correctly exempt from every filter in the file, and pages at once — so with
`CHECK_INTERVAL` at 30 seconds and three cards a tick the ceiling was **360
severe cards an hour**, each individually justified.

That is the shape worth naming: a suppression rule can be completely correct
about the thing it suppresses and still leave the flood untouched, because the
flood is made of the case it deliberately exempts. The operator who stops
reading severe alerts is the failure every one of those rules exists to prevent.

WHAT THIS FILE PINS

  * The budget bounds the hour, not just the tick.
  * Nothing is silently swallowed. A card over budget becomes a NAME in the
    overflow line — trading a flood for a quiet channel would be the worse
    defect, since an operator reads quiet as calm.
  * The worst condition of a pass is never the one dropped.
  * The window actually rolls, so an hour of quiet restores the budget.
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from bot.core.proactive_monitor import (  # noqa: E402
    apply_hourly_budget, select_severe_cards,
)


class _A:
    """Minimal stand-in for an anomaly row: severity and symbol are all the
    selection code reads."""

    def __init__(self, symbol: str, severity: float) -> None:
        self.symbol = symbol
        self.severity = severity


def _groups(*pairs) -> dict:
    return {f"bs_K_{sym}": [_A(sym, sev)] for sym, sev in pairs}


class TestTheBudgetBoundsTheHour:
    def test_within_budget_everything_passes(self):
        shown, spill = select_severe_cards(_groups(("BTC", 0.9), ("ETH", 0.8)), 3)
        kept, spilled, times = apply_hourly_budget(
            shown, spill, [], now=1000.0, per_hour=8, window=3600)
        assert len(kept) == 2
        assert spilled == []
        assert len(times) == 2

    def test_a_full_budget_demotes_rather_than_sends(self):
        shown, spill = select_severe_cards(_groups(("BTC", 0.9), ("ETH", 0.8)), 3)
        spent = [1000.0] * 8                      # eight already sent this hour
        kept, spilled, times = apply_hourly_budget(
            shown, spill, spent, now=1001.0, per_hour=8, window=3600)
        assert kept == [], "over budget, nothing new pages"
        assert spilled == ["BTC", "ETH"], "and both are still NAMED"
        assert len(times) == 8, "a demoted card does not spend budget"

    def test_a_partial_budget_sends_the_worst_and_names_the_rest(self):
        # THE PROPERTY THAT MATTERS MOST: the cap must never be able to drop
        # the loudest condition of the pass.
        shown, spill = select_severe_cards(
            _groups(("QUIET", 0.51), ("LOUDEST", 0.99), ("MID", 0.7)), 3)
        kept, spilled, _ = apply_hourly_budget(
            shown, spill, [500.0] * 7, now=600.0, per_hour=8, window=3600)
        assert len(kept) == 1
        assert kept[0][1][0].symbol == "LOUDEST"
        assert spilled == ["MID", "QUIET"]

    def test_the_window_rolls(self):
        shown, spill = select_severe_cards(_groups(("BTC", 0.9),), 3)
        stale = [100.0] * 8                       # spent, but over an hour ago
        kept, spilled, times = apply_hourly_budget(
            shown, spill, stale, now=100.0 + 3601, per_hour=8, window=3600)
        assert len(kept) == 1, "an hour of quiet restores the budget"
        assert spilled == []
        assert times == [3701.0], "the expired sends are pruned, not kept"

    def test_a_send_exactly_on_the_boundary_has_expired(self):
        shown, spill = select_severe_cards(_groups(("BTC", 0.9),), 3)
        kept, _s, times = apply_hourly_budget(
            shown, spill, [0.0] * 8, now=3600.0, per_hour=8, window=3600)
        assert len(kept) == 1
        assert len(times) == 1


class TestNothingIsSwallowed:
    def test_every_demoted_symbol_appears_in_the_spill(self):
        groups = _groups(*[(f"SYM{i}", 0.9 - i / 100) for i in range(12)])
        shown, spill = select_severe_cards(groups, 3)
        kept, spilled, _ = apply_hourly_budget(
            shown, spill, [0.0] * 8, now=1.0, per_hour=8, window=3600)
        assert kept == []
        # All twelve are accounted for: nine spilled by the tick cap, three
        # demoted by the budget, none lost between the two.
        assert len(spilled) == 12
        assert set(spilled) == {f"SYM{i}" for i in range(12)}

    def test_the_spill_never_loses_what_the_tick_cap_already_put_there(self):
        groups = _groups(*[(f"S{i}", 0.9) for i in range(6)])
        shown, spill = select_severe_cards(groups, 3)
        assert len(spill) == 3
        kept, spilled, _ = apply_hourly_budget(
            shown, spill, [0.0] * 8, now=1.0, per_hour=8, window=3600)
        assert set(spill).issubset(set(spilled)), (
            "the budget must union with the tick cap's spill, not replace it")


class TestTheCeilingIsActuallyLower:
    def test_a_sustained_event_costs_a_handful_of_cards_not_hundreds(self):
        """The regression this file exists for, simulated end to end.

        Two hours of 30-second ticks, every tick surfacing three brand-new
        severe conditions — the exact input the per-key filters exempt.
        """
        sent, times, now = 0, [], 0.0
        for tick in range(240):                   # 240 ticks x 30s = 2 hours
            groups = _groups(*[(f"T{tick}_{i}", 0.9) for i in range(3)])
            shown, spill = select_severe_cards(groups, 3)
            kept, _spilled, times = apply_hourly_budget(
                shown, spill, times, now, per_hour=8, window=3600)
            sent += len(kept)
            now += 30.0

        assert sent <= 17, f"{sent} cards in two hours is still a flood"
        # And it is not zero: the budget throttles, it does not mute.
        assert sent >= 8, "an event this severe must still page the operator"


class TestTheBudgetIsActuallyWiredIn:
    """THE HALF THE UNIT TESTS ABOVE CANNOT REACH.

    Everything above drives `apply_hourly_budget` with `per_hour=8` passed by
    hand. Two mutations proved that is not enough: setting the class constant
    to 100000, and unhooking the call so `_check_black_swan` discards the
    budgeted result, BOTH left the suite green. A pure function tested only
    through its parameters says nothing about the constant the product runs on
    or about whether anyone calls it — which is #999 exactly, committed while
    writing the fix for a flood.

    So these drive the real method, with the real constants.
    """

    @staticmethod
    def _monitor(alerts):
        from bot.core.proactive_monitor import ProactiveMonitor

        class _Det:
            active_alerts = alerts

        class _Eng:
            black_swan = _Det()

        mon = ProactiveMonitor.__new__(ProactiveMonitor)
        mon.engine = _Eng()
        return mon

    @staticmethod
    def _severe(n, tag):
        from bot.core.black_swan import AnomalyAlert, AnomalyType
        return [AnomalyAlert(anomaly_type=AnomalyType.PRICE_ACCELERATION,
                             severity=0.9, symbol=f"{tag}{i}/USDT",
                             description="d", metric_value=9.0, threshold=3.0,
                             recommended_action="REDUCE_POSITION_SIZE")
                for i in range(n)]

    def test_a_sustained_stream_of_new_conditions_stops_paging(self):
        """Every pass carries brand-new symbols, so no per-key filter applies —
        the case the budget exists for. It must throttle anyway."""
        mon = self._monitor([])
        cards = 0
        for tick in range(60):                    # 60 passes, all novel
            mon.engine.black_swan.active_alerts = self._severe(3, f"P{tick}_")
            out = mon._check_black_swan()
            cards += sum(1 for a in out if a.title.startswith("Anomaly:")
                         and "more severe" not in a.title)
        from bot.core.proactive_monitor import ProactiveMonitor as P
        assert cards <= P._SEVERE_CARDS_PER_HOUR, (
            f"{cards} severe cards from 60 passes of novel conditions — the "
            "budget is not reached from _check_black_swan")
        assert cards > 0, "throttling must not become muting"

    def test_the_shipped_constant_is_a_real_bound(self):
        """The constant, not a parameter. A budget of 100000 is not a budget,
        and nothing else in the suite would notice."""
        from bot.core.proactive_monitor import ProactiveMonitor as P
        ceiling = (3600 / P.CHECK_INTERVAL) * P._SEVERE_CARDS_PER_TICK
        assert 0 < P._SEVERE_CARDS_PER_HOUR < ceiling, (
            f"_SEVERE_CARDS_PER_HOUR={P._SEVERE_CARDS_PER_HOUR} does not bound "
            f"the per-tick ceiling of {ceiling:.0f}/hour, so it bounds nothing")
        assert P._SEVERE_CARDS_PER_HOUR <= 24, (
            "more than one severe card every few minutes is the flood again")
