"""Flat margin for high-conviction ideas (operator directive, 2026-07-29).

    "each trade open above 70% with 100 usdt margin x10 so 1000 notional"

Normal sizing is fixed-fractional — risk_budget / stop_distance_pct — so a
tight stop buys a bigger position and a wide one buys a smaller. This
overrides that above a confidence floor: same margin every time, whatever the
stop.

The property that makes it safe to ship is that it sets a TARGET, never a
floor. Every reducer downstream still runs, and none of them can be bypassed:
stock-session multiplier, exchange free-balance clamp, per-user ceiling,
MICRO_MAX_POSITION_USD, total exposure, open-position count, drawdown breaker.

Leverage is deliberately NOT set here. _compute_target_leverage stays the one
source of truth for it (DEFAULT_LEVERAGE, the /leverage override, volatility
de-leveraging which only reduces). A previous audit found a double-leverage
bug from sizing and execution each applying it; keeping this to margin-only is
how that stays fixed.
"""

from types import SimpleNamespace

import pytest

from bot.config import CONFIG
from bot.core.engine import RuneClawEngine
from tests.source_scan import code_only

ENGINE_SRC = code_only(open("bot/core/engine.py", encoding="utf-8").read())


class _Eng:
    """Only what _high_conviction_margin touches."""
    _high_conviction_margin = RuneClawEngine._high_conviction_margin
    _high_conviction_ceiling = RuneClawEngine._high_conviction_ceiling

    def __init__(self, per_user_cap=None):
        self._cap = per_user_cap

    def _per_user_margin_cap(self, user_id):
        return self._cap


@pytest.fixture
def eng():
    return _Eng()


#: CONFIG is a FROZEN dataclass — it can only be set from env at boot, never
#: mutated at runtime. That is the right shape for a live-money knob, and it
#: means these tests have to go through object.__setattr__ exactly as the
#: other config-touching suites do.
_FIELDS = ("high_conviction_enabled", "high_conviction_min_confidence",
           "high_conviction_margin_usd")


def _set(**kw):
    for k, v in kw.items():
        object.__setattr__(CONFIG.execution, k, v)


@pytest.fixture(autouse=True)
def _restore():
    before = {f: getattr(CONFIG.execution, f) for f in _FIELDS}
    yield
    _set(**before)


def _on(floor=0.70, margin=100.0):
    _set(high_conviction_enabled=True, high_conviction_min_confidence=floor,
         high_conviction_margin_usd=margin)


def _idea(conf):
    return SimpleNamespace(asset="BTC/USDT", confidence=conf)


# ── it ships inert ────────────────────────────────────────────────────────

def test_it_is_off_by_default():
    """A live-money sizing rule must not arrive switched on. The operator
    turns it on with env, deliberately."""
    assert CONFIG.execution.high_conviction_enabled is False


def test_disabled_changes_nothing(eng):
    _set(high_conviction_enabled=False)
    assert eng._high_conviction_margin(_idea(0.95), 37.5) == 37.5


# ── the rule ──────────────────────────────────────────────────────────────

def test_at_or_above_the_floor_gets_the_flat_margin(eng):
    _on()
    assert eng._high_conviction_margin(_idea(0.70), 37.5) == 100.0
    assert eng._high_conviction_margin(_idea(0.93), 12.0) == 100.0


def test_below_the_floor_keeps_the_risk_engine_number(eng):
    _on()
    assert eng._high_conviction_margin(_idea(0.699), 37.5) == 37.5
    assert eng._high_conviction_margin(_idea(0.40), 37.5) == 37.5


def test_the_floor_is_inclusive(eng):
    """"above 70%" — an idea that lands exactly on the line takes the size.
    A strict > would make 0.70 behave differently from 0.7000001 for no
    reason a reader could predict."""
    _on(floor=0.70)
    assert eng._high_conviction_margin(_idea(0.70), 5.0) == 100.0


def test_it_can_reduce_as_well_as_raise(eng):
    """Flat means flat. If the risk engine sized larger than the target, the
    target still wins — otherwise it is a floor, and a floor on live size is
    a different and more dangerous thing."""
    _on()
    assert eng._high_conviction_margin(_idea(0.90), 400.0) == 100.0


def test_the_floor_and_margin_are_configurable(eng):
    _on(floor=0.85, margin=250.0)
    assert eng._high_conviction_margin(_idea(0.80), 20.0) == 20.0
    assert eng._high_conviction_margin(_idea(0.85), 20.0) == 250.0


# ── it never breaks a trade ───────────────────────────────────────────────

@pytest.mark.parametrize("idea", [
    SimpleNamespace(),                              # no confidence attribute
    SimpleNamespace(confidence=None),
    SimpleNamespace(confidence="not a number"),
    None,
])
def test_a_malformed_idea_leaves_sizing_alone(eng, idea):
    _on()
    assert eng._high_conviction_margin(idea, 42.0) == 42.0


# ── it is a target, not a bypass ──────────────────────────────────────────

def test_the_reducers_that_follow_it_still_follow_it():
    """Ordering is half the safety argument. From the live assignment
    onwards, the clamps must still be downstream of it."""
    block = ENGINE_SRC[ENGINE_SRC.rindex(
        "size_usd = self._high_conviction_margin(idea, recheck.position_size_usd"):]
    for later in ("stock_session_sizing", "live_size_clamp"):
        assert later in block, f"{later} no longer runs after the sizing rule"


def test_it_cannot_exceed_a_ceiling_that_already_bound(eng):
    """The other half — and the hole the ordering test caught before this
    shipped.

    confirm_trade feeds MICRO_MAX_POSITION_USD and the per-user cap INTO the
    risk recheck, so recheck.position_size_usd already respects them. A flat
    target that ignored that would RAISE past a cap that had already bound,
    and the per-user cap is only re-applied inside the manual-override
    branch, which the auto path never enters.
    """
    _on(margin=100.0)
    capped = _Eng(per_user_cap=40.0)
    assert capped._high_conviction_margin(_idea(0.90), 25.0) == 40.0


def test_a_ceiling_above_the_target_does_not_raise_it(eng):
    _on(margin=100.0)
    roomy = _Eng(per_user_cap=500.0)
    assert roomy._high_conviction_margin(_idea(0.90), 25.0) == 100.0


def test_an_unresolvable_ceiling_does_not_silently_lift_the_cap(eng):
    """If the per-user lookup raises, the ceiling is simply unknown — and the
    executor's own MICRO_MAX_POSITION_USD clamp still stands behind it."""
    class _Broken(_Eng):
        def _per_user_margin_cap(self, user_id):
            raise RuntimeError("store down")
    _on(margin=100.0)
    assert _Broken()._high_conviction_margin(_idea(0.90), 25.0) in (100.0, 25.0)


def test_the_ceiling_is_derived_from_the_same_caps_confirm_trade_uses():
    fn = ENGINE_SRC[ENGINE_SRC.index("def _high_conviction_ceiling"):]
    fn = fn[:fn.index("def _high_conviction_margin")]
    assert "max_live_position_usd" in fn
    assert "_per_user_margin_cap" in fn
    assert "min(ceiling" in fn, "two ceilings compose by taking the tighter"


def test_it_does_not_touch_leverage():
    """A previous audit found margin * leverage**2 notional from sizing and
    execution each applying leverage. Margin-only is how that stays fixed."""
    fn = ENGINE_SRC[ENGINE_SRC.index("def _high_conviction_margin"):]
    fn = fn[:fn.index("async def _phase")]
    assert "leverage" not in fn.replace("_compute_target_leverage", "")


def test_the_hard_caps_are_untouched():
    """MICRO_MAX_POSITION_USD still clamps in the executor, and this rule
    must not have raised it."""
    from bot.core import live_executor
    assert live_executor.MICRO_MAX_POSITION_USD == CONFIG.execution.max_live_position_usd
    ex = code_only(open("bot/core/live_executor.py", encoding="utf-8").read())
    assert "size_usd = min(size_usd, MICRO_MAX_POSITION_USD)" in ex


def test_both_sizing_paths_use_it():
    """Paper is the rehearsal for live; they must size the same way or the
    simulation stops predicting anything."""
    assert ENGINE_SRC.count(
        "self._high_conviction_margin(idea, recheck.position_size_usd, user_id)") == 2


def test_the_change_is_audited():
    fn = ENGINE_SRC[ENGINE_SRC.index("def _high_conviction_margin"):]
    fn = fn[:fn.index("async def _phase")]
    assert 'action="high_conviction_size"' in fn
    assert '"from"' in fn and '"to"' in fn, "an unexplained size change is a mystery later"
