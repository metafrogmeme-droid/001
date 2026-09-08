"""The setup learner could not reach its own threshold, and said ACCUMULATING.

A live readiness card read:

    ⏳ setup_expectancy: ACCUMULATING
       setup-expectancy: 105 setups, 168 trades, 0 setup(s) at/above 10-trade
       threshold

105 keys over 168 trades is a mean of 1.6 and a median of 1. The key is
``(symbol, regime, direction)`` and the floor is ten, so a key only qualifies
after TEN COMPLETED TRADES ON THE SAME SYMBOL in the same regime in the same
direction — and this bot scans a broad universe. It was not accumulating toward
readiness; it could not get there. "ACCUMULATING" tells an operator that waiting
fixes it, which is the third thing this repo's honesty table warns about: a
state that reads as progress toward an outcome that will not arrive.

The lookup backs off — setup → (regime, direction) → (direction) — dropping
SYMBOL first, because symbol is what explodes the key space and regime is the
dimension that plausibly generalises.

TWO THINGS THIS FILE IS CAREFUL ABOUT.

Applying a coarse tier is a WIDER claim than the module's own name makes, so it
has its own switch (`SETUP_EXPECTANCY_BACKOFF_ENABLED`, default off) and until
that is on the coarse nudge is computed and SHADOW-logged. Merging the backoff
therefore changes nothing about live trading — which matters, because
`SETUP_EXPECTANCY_ENABLED` has been ON by default the whole time, while both
the module docstring and `analyzer.py`'s comment beside the call said "default
OFF … applies nothing". The nudge was inert because no setup ever qualified,
not because a flag was holding it.

And `may_apply` is a FUNCTION rather than an expression inline in the analyzer,
because a four-input decision that includes the tier can otherwise only be
tested by scanning for it — and two source scans in this same session survived
mutations that kept the literal and inverted the branch.
"""
from __future__ import annotations

import pytest

from bot.learning.setup_expectancy import (
    TIERS,
    Nudge,
    SetupExpectancy,
    may_apply,
)


def _samples(sym, regime, direction, wins, losses):
    return ([(sym, regime, direction, True)] * wins
            + [(sym, regime, direction, False)] * losses)


def _production_shaped(win_every=2):
    """What the live table looked like: many symbols, one or two trades each,
    all longs in RANGE. No setup key can reach ten; the regime tier can.

    `win_every` SKEWS THE RATE ON PURPOSE. The first version was 1 win and 1
    loss per symbol — a 50% rate, so every nudge computed to exactly 0.0 and
    the tier-weight comparison below passed through its `== 0.0` escape hatch
    no matter what the weights were. A mutation flattening all three weights to
    1.0 survived it. A fixture that cannot produce the quantity under test is
    the assertion-cannot-see-its-subject failure, in the setup rather than the
    assert.
    """
    out = []
    for i in range(40):
        out += _samples(f"SYM{i}", "RANGE", "LONG",
                        wins=win_every, losses=2 - win_every + 1)
    return out


# ── the case the whole change is for ──────────────────────────────────────

def test_a_universe_of_thin_setups_can_still_learn_something():
    exp = SetupExpectancy(min_samples=10).ingest(_production_shaped())
    assert exp.learned_setups() == 0, "the fixture stopped reproducing the card"
    tiers = exp.learned_at_tier()
    assert tiers["setup"] == 0
    assert tiers["regime"] == 1, "the longs-in-RANGE trades learned nothing"
    assert exp.is_ready(), (
        "inert on 80 completed trades because no single SYMBOL reached ten — "
        "the learner could not have become ready by waiting")


def test_the_card_names_the_tier_that_answered():
    """"0 setup(s) at/above threshold" beside a READY verdict reads as a
    contradiction unless the line says where the evidence came from."""
    exp = SetupExpectancy(min_samples=10).ingest(_production_shaped())
    out = exp.summary()
    assert "0 setup(s) at/above" in out
    assert "backing off to regime=1" in out, out


def test_a_thin_table_with_no_tier_at_all_stays_not_ready():
    """The control. Backoff must not turn "no evidence" into readiness — it
    lowers the specificity of the evidence, never the amount."""
    exp = SetupExpectancy(min_samples=10).ingest(
        _samples("SOL", "RANGE", "LONG", wins=2, losses=1))
    assert exp.learned_at_tier() == {"setup": 0, "regime": 0, "direction": 0}
    assert not exp.is_ready()
    assert exp.nudge_for("SOL", "RANGE", "LONG").value == 0.0
    assert "backing off" not in exp.summary()


# ── which tier answers, and in what order ─────────────────────────────────

def test_the_symbols_own_record_beats_the_regime_tier():
    """Backoff is a fallback, not a blend: a symbol with its own history must
    be judged on it, or the specific evidence is diluted by the general."""
    exp = SetupExpectancy(min_samples=10).ingest(
        _samples("SOL", "RANGE", "LONG", wins=18, losses=2)      # 90% on SOL
        + _samples("BTC", "RANGE", "LONG", wins=2, losses=18)    # 10% on BTC
    )
    wr, n, tier = exp.lookup_best("SOL", "RANGE", "LONG")
    assert tier == "setup" and n == 20 and wr == pytest.approx(0.9)
    assert exp.nudge_for("SOL", "RANGE", "LONG").value > 0
    assert exp.nudge_for("BTC", "RANGE", "LONG").value < 0


def test_a_symbol_with_no_record_falls_back_to_its_regime():
    exp = SetupExpectancy(min_samples=10).ingest(_production_shaped())
    wr, n, tier = exp.lookup_best("NEVERSEEN", "RANGE", "LONG")
    assert tier == "regime" and n >= exp.min_samples
    assert exp.nudge_for("NEVERSEEN", "RANGE", "LONG").tier == "regime"


def test_an_unseen_regime_falls_all_the_way_to_direction():
    exp = SetupExpectancy(min_samples=10).ingest(_production_shaped())
    _, n, tier = exp.lookup_best("NEVERSEEN", "TREND_UP", "LONG")
    assert tier == "direction" and n >= exp.min_samples


def test_nothing_at_any_tier_is_none_not_a_coin_flip_applied():
    exp = SetupExpectancy(min_samples=10).ingest(_production_shaped())
    wr, n, tier = exp.lookup_best("NEVERSEEN", "TREND_UP", "SHORT")
    assert tier == "none" and n == 0
    # 0.5 is the placeholder, and n == 0 beside it is what says so — the nudge
    # reads the count, never the rate alone.
    assert exp.nudge_for("NEVERSEEN", "TREND_UP", "SHORT") == Nudge(0.0, "none", 0)


def test_lookup_still_answers_the_per_setup_question_honestly():
    """`lookup` is NOT backed off on purpose: "what has this symbol done here"
    has to keep being answerable, or the card can no longer tell a symbol with
    a record from one riding on its regime."""
    exp = SetupExpectancy(min_samples=10).ingest(_production_shaped())
    assert exp.lookup("NEVERSEEN", "RANGE", "LONG") == (0.5, 0)
    assert exp.lookup_best("NEVERSEEN", "RANGE", "LONG")[2] == "regime"


# ── how much a coarse tier is allowed to move ─────────────────────────────

def test_a_coarse_tier_nudges_less_than_the_same_evidence_at_setup_level():
    """Identical win rate and sample count; only the specificity differs."""
    # 80 trades at 75% either way — only the SPECIFICITY differs.
    fine = SetupExpectancy(min_samples=10).ingest(
        _samples("SOL", "RANGE", "LONG", wins=60, losses=20))
    coarse = SetupExpectancy(min_samples=10)
    coarse.ingest([s for i in range(20)
                   for s in _samples(f"SYM{i}", "RANGE", "LONG", 3, 1)])

    at_setup = fine.nudge_for("SOL", "RANGE", "LONG")
    at_regime = coarse.nudge_for("NEVERSEEN", "RANGE", "LONG")
    assert at_setup.tier == "setup" and at_regime.tier == "regime"
    assert at_setup.n == at_regime.n == 80
    assert at_setup.value > 0 and at_regime.value > 0, (
        "a 75% win rate must move confidence UP at both tiers, or this "
        "comparison is between two zeros")
    assert at_regime.value < at_setup.value, (
        "identical evidence about a wider population moved confidence just as "
        "hard as the symbol's own record")


@pytest.mark.parametrize("tier_case", ["setup", "regime", "direction"])
def test_the_bound_holds_at_every_tier(tier_case):
    """±max_nudge is the promise the whole module rests on, and a tier weight
    that was ever > 1.0 would break it silently."""
    exp = SetupExpectancy(min_samples=10, max_nudge=0.05)
    if tier_case == "setup":
        exp.ingest(_samples("X", "RANGE", "LONG", wins=500, losses=0))
        got = exp.nudge_for("X", "RANGE", "LONG")
    else:
        exp.ingest([(f"S{i}", "RANGE", "LONG", True) for i in range(500)])
        regime = "RANGE" if tier_case == "regime" else "TREND_UP"
        got = exp.nudge_for("UNSEEN", regime, "LONG")
    assert got.tier == tier_case, got
    assert -0.05 - 1e-9 <= got.value <= 0.05 + 1e-9


def test_is_coarse_is_what_the_caller_gates_on():
    assert not Nudge(0.03, "setup", 40).is_coarse
    assert Nudge(0.03, "regime", 40).is_coarse
    assert Nudge(0.03, "direction", 40).is_coarse
    assert not Nudge(0.0, "none", 0).is_coarse
    assert set(TIERS) == {"setup", "regime", "direction"}


# ── the gate: what may touch a live confidence ────────────────────────────

@pytest.mark.parametrize("tier, enabled, backoff, expected", [
    # The symbol's own record: the backoff switch is irrelevant to it.
    ("setup", True, False, True),
    ("setup", True, True, True),
    # A coarse tier waits for its own switch — shadow until then.
    ("regime", True, False, False),
    ("regime", True, True, True),
    ("direction", True, False, False),
    ("direction", True, True, True),
    # The feature switch still governs everything above it.
    ("setup", False, True, False),
    ("regime", False, True, False),
])
def test_only_the_permitted_tiers_may_move_a_confidence(tier, enabled, backoff,
                                                        expected):
    got = may_apply(Nudge(0.03, tier, 40), enabled=enabled,
                    backoff_enabled=backoff)
    assert got is expected, (tier, enabled, backoff)


def test_a_zero_nudge_is_never_applied_whatever_the_flags():
    """Applying zero is harmless arithmetically and dishonest in the audit
    log: it records an APPLIED adjustment that adjusted nothing."""
    assert not may_apply(Nudge(0.0, "setup", 40), enabled=True,
                         backoff_enabled=True)
    assert not may_apply(Nudge(0.0, "none", 0), enabled=True,
                         backoff_enabled=True)


def test_the_default_deployment_applies_no_coarse_nudge():
    """Merging the backoff must not change live trading. `may_apply` is driven
    with the SHIPPED defaults rather than literals, so a change to either
    default fails here instead of in production."""
    from bot.config import CONFIG

    enabled = CONFIG.analyzer.setup_expectancy_enabled
    backoff = getattr(CONFIG.analyzer, "setup_expectancy_backoff_enabled", None)
    assert backoff is False, (
        "SETUP_EXPECTANCY_BACKOFF_ENABLED defaults on — coarse-tier nudges "
        "would start moving live confidences the moment this merges")
    assert not may_apply(Nudge(0.03, "regime", 80), enabled=enabled,
                         backoff_enabled=backoff)
    assert may_apply(Nudge(0.03, "setup", 80), enabled=enabled,
                     backoff_enabled=backoff) is bool(enabled)


# ── what actually protects the ±max_nudge promise ─────────────────────────

def test_every_tier_has_a_weight_and_none_exceeds_one():
    """THE REAL BOUND. `nudge_for` clamps to ±max_nudge, but that clamp is
    unreachable while every weight is a fraction — `shrink` and the weight are
    both ≤ 1, so the product never exceeds it, and a mutation deleting the
    clamp SURVIVED. What keeps the promise is this: weights in [0, 1], and one
    for every tier.

    The coverage half matters too — `_TIER_WEIGHT.get(tier, 0.0)` means a tier
    added to TIERS without a weight would silently contribute nothing rather
    than raise, which is the safe runtime direction and the wrong thing to
    discover in production.
    """
    from bot.learning.setup_expectancy import _TIER_WEIGHT

    assert set(_TIER_WEIGHT) == set(TIERS), (
        "a tier without a weight nudges by zero and looks like 'no evidence'")
    for tier, w in _TIER_WEIGHT.items():
        assert 0.0 <= w <= 1.0, f"{tier} weight {w} can exceed max_nudge"
    assert _TIER_WEIGHT["setup"] == 1.0, (
        "the symbol's own record is the reference the others are scaled against")
    assert _TIER_WEIGHT["regime"] < _TIER_WEIGHT["setup"]
    assert _TIER_WEIGHT["direction"] < _TIER_WEIGHT["regime"]


def test_a_tier_under_the_floor_is_not_the_tier_that_answered():
    """`lookup_best` must apply `min_samples` ITSELF, not lean on `nudge_for`
    zeroing a thin result afterwards. A mutation dropping the floor from the
    walk survived, because the only caller happened to re-check it — so the
    function was free to answer "setup, n=2" to "which tier has enough".
    """
    exp = SetupExpectancy(min_samples=10).ingest(
        _samples("SOL", "RANGE", "LONG", wins=2, losses=1))
    wr, n, tier = exp.lookup_best("SOL", "RANGE", "LONG")
    assert tier == "none" and n == 0, (
        f"a 3-trade cell answered as tier {tier!r} with n={n}")


# ── the readiness card: applied is a FLAG, not a readiness ────────────────

def test_the_card_reports_applied_from_the_flags_not_from_readiness():
    """`applied=se.is_ready()` made two of the four (state, applied) pairs
    unreachable for this component — READY implied applied, so "validated but
    not applied — consider enabling" could never fire and "applied and
    validated ✓" always did. With a coarse tier ready and backoff off, the
    analyzer shadow-logs and the card would have claimed it applied.
    """
    from bot.config import CONFIG
    from bot.learning import readiness as rd
    from bot.learning import setup_expectancy as sx

    class _EmptyStore:
        def get_decisions(self, symbol=None, limit=100):
            return []

    coarse_ready = SetupExpectancy(min_samples=10).ingest(_production_shaped())
    assert coarse_ready.is_ready() and coarse_ready.learned_setups() == 0

    def _fake(reload=False):
        return coarse_ready

    _real = sx.get_setup_expectancy
    sx.get_setup_expectancy = _fake
    _before = getattr(CONFIG.analyzer, "setup_expectancy_backoff_enabled", False)
    try:
        object.__setattr__(CONFIG.analyzer, "setup_expectancy_backoff_enabled", False)
        comp = rd.assess_readiness(store=_EmptyStore())["components"]["setup_expectancy"]
        assert comp["state"] == "READY"
        assert comp["applied"] is False, (
            "the coarse tier is ready and its switch is off — the analyzer "
            "shadow-logs this nudge and the card said it was applied")
        assert comp["flag"] == "SETUP_EXPECTANCY_BACKOFF_ENABLED", (
            "the card names the switch that is actually holding it back")

        object.__setattr__(CONFIG.analyzer, "setup_expectancy_backoff_enabled", True)
        comp = rd.assess_readiness(store=_EmptyStore())["components"]["setup_expectancy"]
        assert comp["applied"] is True
    finally:
        sx.get_setup_expectancy = _real
        object.__setattr__(CONFIG.analyzer,
                           "setup_expectancy_backoff_enabled", _before)
