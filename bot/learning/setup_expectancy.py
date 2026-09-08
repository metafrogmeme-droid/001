"""
Per-setup expectancy — let the bot's own track record on a setup nudge confidence.

RUNECLAW already records every completed trade (`DecisionMemory`: symbol, regime,
direction, ``pnl_result``) and ``experience.get_similar_setups()`` surfaces the
matching history — but only into the *LLM prompt*, never the decision gate. This
module turns that history into a small, bounded **confidence nudge**: if longs on
SOL in a RANGE regime have historically won 30% of the time, shade that setup's
confidence down; if 70%, shade it up.

Design / safety (mirrors confidence_calibration.py):
  - **Bounded** nudge in ``[-max_nudge, +max_nudge]`` (default ±0.05) — it can
    only *shade* a confidence, never dominate the analyzer or risk gate.
  - **Shrinkage by sample count**: a setup with few trades barely moves; the nudge
    scales toward full strength only as evidence accumulates.
  - **Identity below ``min_samples``** (default 10) and when no history loaded —
    zero nudge. It can only refine once there is evidence, never fabricate one.
  - Pure read-only aggregation over completed decisions. Cached in memory; refresh
    explicitly. Applied on the decision path when SETUP_EXPECTANCY_ENABLED is on
    — which it is BY DEFAULT (bot/config.py). This docstring said "default-OFF …
    shadow-only" and so did analyzer.py's comment beside the call; the config has
    said the opposite since it was written. Two comments telling a reader the
    nudge is inert while it is live.

WHY THERE ARE TIERS, AND WHY THE FINEST ONE COULD NEVER FIRE.

The key was ``(symbol, regime, direction)`` against ``min_samples = 10``, and a
live card read: **105 setups, 168 trades, 0 setup(s) at/above the 10-trade
threshold**. Mean 1.6 trades per key, median 1. For any key to reach ten you
need ten completed trades on the same SYMBOL in the same regime in the same
direction — and this bot scans a broad universe, so that essentially never
happens. The learner was not accumulating toward readiness; it could not get
there, and the card said ACCUMULATING, which tells an operator that waiting
will fix it.

So the lookup backs off:

    setup      (symbol, regime, direction)   most specific
    regime     (regime, direction)           "longs in RANGE"
    direction  (direction,)                  "longs"

SYMBOL IS DROPPED FIRST because symbol is what explodes the key space and
regime is the dimension that plausibly generalises — a per-symbol edge is the
strongest claim and the least affordable one. Each tier carries a weight
(``_TIER_WEIGHT``) so coarser evidence nudges less; that weighting is a
deliberate simplification, not an estimate, and it is bounded by ``max_nudge``
either way.

APPLYING a coarse tier is a different claim from applying a setup's own record,
so it is gated separately: ``SETUP_EXPECTANCY_BACKOFF_ENABLED``, **default
off**. Until it is switched on the coarse nudge is computed and SHADOW-logged —
the same shadow-first path every other learner here was introduced through —
so the would-be effect can be read from the audit channel before it touches a
trade.
"""

from __future__ import annotations

import logging
from typing import NamedTuple, Optional

log = logging.getLogger("runeclaw.setup_expectancy")

_DEFAULT_MIN_SAMPLES = 10
_DEFAULT_MAX_NUDGE = 0.05
_DEFAULT_SHRINKAGE = 10.0   # pseudo-trades; the nudge reaches ~half strength at n≈shrinkage

#: Tiers from most to least specific. The ORDER is the backoff order.
TIERS = ("setup", "regime", "direction")

#: How much of the nudge each tier is allowed to contribute. Coarser evidence
#: is real but is about a wider population than the trade in hand, so it moves
#: confidence less. These are a judgement, stated rather than fitted — and the
#: result is bounded by ``max_nudge`` regardless of what they are.
_TIER_WEIGHT = {"setup": 1.0, "regime": 0.6, "direction": 0.3}


class Nudge(NamedTuple):
    """What the nudge is, and what it is a claim ABOUT.

    The tier has to travel with the number: the caller decides whether a
    regime-level nudge may be applied to a symbol with no record of its own,
    and it cannot decide that from a float.
    """

    value: float
    tier: str          # one of TIERS, or "none" when nothing qualified
    n: int             # trades behind it; 0 when nothing qualified

    @property
    def is_coarse(self) -> bool:
        """True when this came from a backed-off tier — a claim about longs in
        RANGE, say, rather than about this symbol's own history."""
        return self.tier in TIERS and self.tier != "setup"


def _norm(s) -> str:
    return str(s or "").strip().upper()


class SetupExpectancy:
    """Aggregates completed-trade win rates per (symbol, regime, direction) and
    converts the matching history into a bounded confidence nudge."""

    def __init__(self, min_samples: int = _DEFAULT_MIN_SAMPLES,
                 max_nudge: float = _DEFAULT_MAX_NUDGE,
                 shrinkage: float = _DEFAULT_SHRINKAGE) -> None:
        self.min_samples = max(1, int(min_samples))
        self.max_nudge = max(0.0, float(max_nudge))
        self.shrinkage = max(0.0, float(shrinkage))
        # (symbol, regime, direction) -> [wins, total]
        self._table: dict[tuple, list[int]] = {}
        # The backoff tiers, AGGREGATED IN `ingest` rather than derived from
        # `_table` at query time. Deriving them would mean unpacking its keys,
        # and tests plant `_table` directly with keys that are not 3-tuples —
        # a query-time derivation would crash on exactly the fixtures that pin
        # `learned_setups`. Building them where the samples arrive also keeps
        # every tier a count of real trades rather than a re-summed summary.
        self._regime_table: dict[tuple, list[int]] = {}
        self._dir_table: dict[tuple, list[int]] = {}
        self._loaded = False

    # -- building --------------------------------------------------------------

    def ingest(self, samples) -> "SetupExpectancy":
        """Ingest ``(symbol, regime, direction, won)`` tuples (completed trades)."""
        table: dict[tuple, list[int]] = {}
        regimes: dict[tuple, list[int]] = {}
        dirs: dict[tuple, list[int]] = {}
        n = 0
        for sym, regime, direction, won in samples:
            s, r, d = _norm(sym), _norm(regime), _norm(direction)
            hit = 1 if won else 0
            for tbl, key in ((table, (s, r, d)), (regimes, (r, d)), (dirs, (d,))):
                cell = tbl.setdefault(key, [0, 0])
                cell[0] += hit
                cell[1] += 1
            n += 1
        self._table = table
        self._regime_table = regimes
        self._dir_table = dirs
        self._loaded = n > 0
        return self

    @staticmethod
    def samples_from_decisions(decisions):
        """Extract ``(symbol, regime, direction, won)`` from completed
        DecisionMemory-like records (non-null ``pnl_result``)."""
        out = []
        for d in decisions:
            pnl = getattr(d, "pnl_result", None)
            if pnl is None:
                continue
            out.append((getattr(d, "symbol", ""), getattr(d, "market_regime", ""),
                        getattr(d, "direction", ""), float(pnl) > 0.0))
        return out

    def load(self, store=None) -> "SetupExpectancy":
        """Load completed-trade history from the learning store (default: a fresh
        LearningStore). Safe on an empty store — stays identity."""
        if store is None:
            from bot.learning.store import LearningStore
            store = LearningStore()
        decisions = store.get_decisions(limit=100000)
        return self.ingest(self.samples_from_decisions(decisions))

    # -- querying --------------------------------------------------------------

    def lookup(self, symbol, regime, direction) -> tuple:
        """``(win_rate, n)`` for THIS SETUP'S OWN record, or ``(0.5, 0)``.

        Deliberately not backed off: this answers "what has this symbol done in
        this regime in this direction", and the honest answer to that is often
        "nothing". `lookup_best` is the one that falls back.

        The `0.5` is a placeholder, not a measurement, and `n = 0` beside it is
        what says so — every caller here reads the count before the rate.
        """
        cell = self._table.get((_norm(symbol), _norm(regime), _norm(direction)))
        if not cell or cell[1] == 0:
            return 0.5, 0
        return cell[0] / cell[1], cell[1]

    def lookup_best(self, symbol, regime, direction) -> tuple:
        """``(win_rate, n, tier)`` — the most specific tier with enough trades.

        Walks `TIERS` in order and stops at the first cell holding at least
        ``min_samples``. Returns ``(0.5, 0, "none")`` when no tier qualifies,
        which is the same "nothing measured" the caller already handles.
        """
        s, r, d = _norm(symbol), _norm(regime), _norm(direction)
        for tier, cell in (("setup", self._table.get((s, r, d))),
                           ("regime", self._regime_table.get((r, d))),
                           ("direction", self._dir_table.get((d,)))):
            if cell and cell[1] >= self.min_samples:
                return cell[0] / cell[1], cell[1], tier
        return 0.5, 0, "none"

    def nudge_for(self, symbol, regime, direction) -> "Nudge":
        """The bounded nudge, WITH the tier it came from.

        nudge = (win_rate - 0.5) * 2 * max_nudge * shrink * tier_weight, where
        shrink = n / (n + shrinkage) pulls thin samples toward zero.

        The tier rides along because the caller has a decision to make with it:
        a regime-level nudge applied to a symbol with no record of its own is a
        wider claim than this module's name suggests, and `analyzer` gates it
        behind its own flag. A bare float cannot carry that.
        """
        win_rate, n, tier = self.lookup_best(symbol, regime, direction)
        if n < self.min_samples:
            return Nudge(0.0, "none", 0)
        shrink = n / (n + self.shrinkage) if (n + self.shrinkage) else 0.0
        nudge = ((win_rate - 0.5) * 2.0 * self.max_nudge * shrink
                 * _TIER_WEIGHT.get(tier, 0.0))
        # THE CLAMP IS UNREACHABLE WHILE EVERY WEIGHT IS <= 1, and it is kept
        # anyway: `shrink` and the weight are both fractions, so the product
        # cannot exceed max_nudge today. A mutation deleting it survived, which
        # is what "unreachable" looks like from outside. What actually protects
        # the ±max_nudge promise is the bound on the weights themselves, and
        # that is pinned in test_setup_expectancy_backoff.py rather than left
        # to this line to catch after the fact.
        return Nudge(max(-self.max_nudge, min(self.max_nudge, nudge)), tier, n)

    # `confidence_nudge()` LIVED HERE and is gone. It returned
    # `nudge_for(...).value` and its docstring said it was "what most callers
    # and tests want" -- true until this change moved the analyzer onto
    # `nudge_for` for the tier, at which point tests were its only caller and
    # the docstring's first half was false. `tests/
    # test_no_new_unreachable_functions.py` said so in the same commit, which
    # is the whole point of that ratchet: a wrapper nobody reads is
    # indistinguishable from one that does not work. Call `nudge_for(...)
    # .value`.

    def is_ready(self) -> bool:
        """Has anything actually been LEARNED — not merely loaded.

        This returned `self._loaded`, so the readiness card reported
        `setup_expectancy: READY — applied and validated` whenever a file
        opened, directly above its own summary line saying `0 setup(s)
        at/above 10-trade threshold`. Loaded is not learned, and a green badge
        over no qualifying evidence is the defect this repo spends most of its
        guard tests preventing.

        Harmless to trades either way — `expectancy_for` already returns
        identity below `min_samples`, so nothing was being adjusted. The cost
        was to the operator, who was told a component was contributing while
        it was inert, next to a recommendation to switch another one on.
        """
        return self._loaded and sum(self.learned_at_tier().values()) > 0

    def learned_setups(self) -> int:
        """How many SETUP-level keys clear ``min_samples``.

        Unchanged on purpose: it is the per-symbol number, and the readiness
        card and its guards read it as exactly that. `learned_at_tier` is where
        the backed-off counts live.
        """
        if not self._loaded:
            return 0
        return sum(1 for c in self._table.values() if c[1] >= self.min_samples)

    def learned_at_tier(self) -> dict:
        """``{tier: how many keys at that tier clear min_samples}``.

        The number that made the old card unreadable was 0 at the only tier
        that existed. Reporting all three is what turns "ACCUMULATING", which
        promises that waiting helps, into a statement an operator can act on.
        """
        if not self._loaded:
            return {t: 0 for t in TIERS}
        return {
            "setup": self.learned_setups(),
            "regime": sum(1 for c in self._regime_table.values()
                          if c[1] >= self.min_samples),
            "direction": sum(1 for c in self._dir_table.values()
                             if c[1] >= self.min_samples),
        }

    def summary(self) -> str:
        if not self._loaded:
            return "setup-expectancy: no completed-trade history loaded"
        tiers = self.learned_at_tier()
        total = sum(c[1] for c in self._table.values())
        head = (f"setup-expectancy: {len(self._table)} setups, {total} trades, "
                f"{tiers['setup']} setup(s) at/above {self.min_samples}-trade "
                "threshold")
        # NAME THE TIER THAT ACTUALLY ANSWERS. Without this the card reads
        # "0 at threshold" beside a READY verdict and the two look like a
        # contradiction rather than a backoff.
        if tiers["setup"] == 0 and (tiers["regime"] or tiers["direction"]):
            head += (f"; backing off to regime={tiers['regime']} "
                     f"direction={tiers['direction']}")
        return head


def may_apply(nudge: "Nudge", *, enabled: bool, backoff_enabled: bool) -> bool:
    """Whether this nudge may move a confidence, or only be shadow-logged.

    A FUNCTION, not an expression inline in `analyzer`. The decision has four
    inputs and one of them is the tier, so an inline version can only be tested
    by scanning the source for it — and this session has twice watched a source
    scan survive a mutation that kept the literal and inverted the branch. It
    is driven from `test_setup_expectancy_backoff.py` instead.

    Two switches, deliberately: `enabled` is the whole feature, and
    `backoff_enabled` is the narrower question of whether evidence about a
    WIDER population than this symbol may act on it.
    """
    if not enabled or nudge.value == 0.0:
        return False
    return (not nudge.is_coarse) or bool(backoff_enabled)


_INSTANCE: Optional["SetupExpectancy"] = None


def get_setup_expectancy(reload: bool = False) -> "SetupExpectancy":
    """Process-wide singleton, lazily loaded from the learning store."""
    global _INSTANCE
    if _INSTANCE is None or reload:
        inst = SetupExpectancy()
        try:
            inst.load()
        except Exception as exc:
            log.warning("setup-expectancy load failed: %s", exc)
        _INSTANCE = inst
    return _INSTANCE
