"""Spread-gate consolidation — one source of truth across two layers.

The audit found two unrelated spread knobs in two units: the analysis-layer
liquidity guard (OF_MAX_SPREAD_BPS, 50bps) and the executor's last-line gate
(ENTRY_MAX_SPREAD_PCT, 1.0%). The executor ceiling now DERIVES from the
guard at 2x unless explicitly overridden — behavior-preserving at defaults
(2 x 50bps = 1.0%) and coherent when the operator moves the OF knob. The
liquidity-guard knobs also join the self-audit allowlist so the nightly
audit can propose tuning them within bounds.
"""

from pathlib import Path

from bot.core.order_flow import entry_max_spread_pct
from bot.core.self_audit import ALLOWED_FLAGS, validate_proposals


def test_derivation_and_override(monkeypatch):
    monkeypatch.delenv("ENTRY_MAX_SPREAD_PCT", raising=False)
    monkeypatch.delenv("OF_MAX_SPREAD_BPS", raising=False)
    # Defaults preserve today's behavior exactly: 2 x 50bps = 1.0%.
    assert entry_max_spread_pct() == 1.0
    # Moving the analysis guard moves the executor ceiling with it.
    monkeypatch.setenv("OF_MAX_SPREAD_BPS", "80")
    assert entry_max_spread_pct() == 1.6
    # An explicit executor override wins...
    monkeypatch.setenv("ENTRY_MAX_SPREAD_PCT", "0.4")
    assert entry_max_spread_pct() == 0.4
    # ...including 0 = disabled (the executor treats <=0 as off).
    monkeypatch.setenv("ENTRY_MAX_SPREAD_PCT", "0")
    assert entry_max_spread_pct() == 0.0
    # Junk in either knob degrades to the safe default, never raises.
    monkeypatch.setenv("ENTRY_MAX_SPREAD_PCT", "not-a-number")
    monkeypatch.setenv("OF_MAX_SPREAD_BPS", "junk")
    assert entry_max_spread_pct() == 1.0


def test_liquidity_knobs_join_the_self_audit_menu():
    assert ALLOWED_FLAGS["OF_MAX_SPREAD_BPS"] == {"type": "float", "min": 10, "max": 200}
    assert ALLOWED_FLAGS["OF_MIN_DEPTH_USD"] == {"type": "float", "min": 500, "max": 50_000}
    ok = validate_proposals([
        {"flag": "OF_MAX_SPREAD_BPS", "value": 80, "rationale": "spreads widened"},
        {"flag": "OF_MAX_SPREAD_BPS", "value": 500, "rationale": "out of bounds"},
        {"flag": "OF_MIN_DEPTH_USD", "value": 10_000, "rationale": "thin books"},
    ], current_env={}, max_proposals=3)
    # First in-bounds proposal per flag survives; the out-of-bounds one dies.
    # (validate_proposals emits env-style STRING values.)
    assert [(p["flag"], float(p["value"])) for p in ok] == [
        ("OF_MAX_SPREAD_BPS", 80.0), ("OF_MIN_DEPTH_USD", 10_000.0)]


def test_executor_consumes_the_derived_ceiling():
    src = Path("bot/core/live_executor.py").read_text(encoding="utf-8")
    assert "_max_spread = entry_max_spread_pct()" in src
    assert 'float(os.environ.get("ENTRY_MAX_SPREAD_PCT", "1.0"))' not in src, \
        "the old free-standing knob read must be gone"


def test_large_cap_depth_floor_is_a_real_knob_matching_the_docs():
    """The audit found the docstring promising a $50K large-cap floor the
    code never enforced ($10K hardcoded). Now the floor is a knob whose
    default IS the enforced value, and the docs tell the truth."""
    import dataclasses
    from bot.core.order_flow import OrderFlowAnalyzer, OrderFlowConfig, OrderFlowSignal

    cfg = OrderFlowConfig()
    assert cfg.large_cap_depth_floor_usd == 10_000.0
    assert "$50K floor is enforced" not in (OrderFlowAnalyzer.liquidity_guard.__doc__ or "")

    # Raising the knob raises the enforced large-cap floor — and ONLY for
    # large caps: 20K depth passes an altcoin but fails BTC at a 25K floor.
    an = OrderFlowAnalyzer(dataclasses.replace(cfg, large_cap_depth_floor_usd=25_000.0))
    sig = OrderFlowSignal(symbol="BTC/USDT", spread_bps=5.0,
                          bid_depth_usd=20_000.0, ask_depth_usd=20_000.0,
                          components_ok=["book"])
    verdict = an.liquidity_guard(sig, position_size_usd=0.0, symbol="BTC/USDT")
    assert verdict is not None and "thin book" in verdict
    alt = OrderFlowSignal(symbol="DOGE/USDT", spread_bps=5.0,
                          bid_depth_usd=20_000.0, ask_depth_usd=20_000.0,
                          components_ok=["book"])
    assert an.liquidity_guard(alt, position_size_usd=0.0, symbol="DOGE/USDT") is None
