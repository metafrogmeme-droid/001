"""
Tests for risk_manifest.yaml validation against actual code,
and for the /whynot rejection explainer skill.
"""

import os
import yaml
import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock

# Paths
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MANIFEST_PATH = os.path.join(_PROJECT_ROOT, "config", "risk_manifest.yaml")


# ── helpers ──────────────────────────────────────────────────────

def _load_manifest() -> dict:
    with open(_MANIFEST_PATH) as f:
        return yaml.safe_load(f)


def _make_idea(
    asset="BTC/USDT",
    direction=None,
    entry=65000.0,
    sl=58500.0,
    tp=72800.0,
    confidence=0.72,
    idea_id="TI-test001",
):
    from bot.utils.models import Direction, TradeIdea
    return TradeIdea(
        id=idea_id,
        asset=asset,
        direction=direction or Direction.LONG,
        entry_price=entry,
        stop_loss=sl,
        take_profit=tp,
        confidence=confidence,
        reasoning="test idea",
        signals_used=["rsi", "macd"],
    )


def _make_portfolio(balance=10000.0):
    from bot.risk.portfolio import PortfolioTracker
    return PortfolioTracker(initial_balance=balance)


def _make_risk(portfolio):
    import tempfile

    from bot.risk.risk_engine import RiskEngine

    # Isolated temp state file (never "/dev/null": RiskEngine._save_state does
    # os.replace(tmp, state_file), which as root clobbers the /dev/null device
    # and leaks circuit-breaker state into later tests).
    state = os.path.join(tempfile.mkdtemp(prefix="rc-risk-"), "risk_state.json")
    return RiskEngine(portfolio, state_file=state)


# ══════════════════════════════════════════════════════════════════
# MANIFEST TESTS
# ══════════════════════════════════════════════════════════════════

class TestRiskManifest:
    """Validate risk_manifest.yaml matches actual code."""

    def test_manifest_loads_valid_yaml(self):
        """1. manifest_loads_valid_yaml - file parses without error."""
        data = _load_manifest()
        assert isinstance(data, dict)
        assert "checks" in data

    def test_manifest_has_21_checks(self):
        """2. manifest_has_21_checks - exactly 21 check entries."""
        data = _load_manifest()
        assert len(data["checks"]) == 21

    def test_manifest_check_ids_sequential(self):
        """3. manifest_check_ids_sequential - IDs 1-21 in order."""
        data = _load_manifest()
        ids = [c["id"] for c in data["checks"]]
        assert ids == list(range(1, 22))

    def test_manifest_check_names_match_code(self):
        """4. manifest_check_names_match_code - names match risk_engine.py check names."""
        data = _load_manifest()
        manifest_names = [c["name"] for c in data["checks"]]

        # Expected names derived from the risk_engine.py check labels
        expected_names = [
            "CIRCUIT_BREAKER",
            "POSITION_SIZE",
            "DAILY_LOSS",
            "DRAWDOWN",
            "OPEN_POSITIONS",
            "RISK_REWARD",
            "CONFIDENCE",
            "CORRELATION",
            "LOSS_STREAK",
            "ENTRY_PRICE",
            "STOP_LOSS",
            "STALE_DATA",
            "COOLDOWN",
            "PORTFOLIO_EXPOSURE",
            "SYMBOL_EXPOSURE",
            "VOLATILITY",
            "LIQUIDITY",
            "MACRO_EVENT",
            "MTF_ALIGNMENT",
            "CONCENTRATION_PCA",
            "PORTFOLIO_VAR",
        ]
        assert manifest_names == expected_names

    def test_manifest_has_defaults_section(self):
        """5. manifest_has_defaults_section - defaults section exists with all RiskLimits fields."""
        from bot.config import RiskLimits
        import dataclasses

        data = _load_manifest()
        assert "defaults" in data

        defaults = data["defaults"]
        risk_fields = {f.name for f in dataclasses.fields(RiskLimits)}

        # Every RiskLimits field should appear in manifest defaults
        for field_name in risk_fields:
            assert field_name in defaults, (
                f"RiskLimits field '{field_name}' missing from manifest defaults"
            )

    def test_manifest_has_correlation_groups(self):
        """6. manifest_has_correlation_groups - correlation groups section matches code."""
        from bot.risk.risk_engine import _CORRELATION_GROUPS

        data = _load_manifest()
        assert "correlation_groups" in data

        manifest_groups = data["correlation_groups"]

        # Build reverse map from code: group_name -> set of symbols
        code_groups: dict[str, set[str]] = {}
        for symbol, group in _CORRELATION_GROUPS.items():
            code_groups.setdefault(group, set()).add(symbol)

        # Every group in code must be in manifest
        for group_name, symbols in code_groups.items():
            assert group_name in manifest_groups, (
                f"Correlation group '{group_name}' in code but missing from manifest"
            )
            manifest_symbols = set(manifest_groups[group_name]["symbols"])
            assert symbols == manifest_symbols, (
                f"Symbols mismatch for group '{group_name}': "
                f"code={sorted(symbols)}, manifest={sorted(manifest_symbols)}"
            )

    def test_manifest_defaults_match_config(self):
        """7. manifest_defaults_match_config - threshold values match RiskLimits defaults."""
        from bot.config import RiskLimits
        import dataclasses

        data = _load_manifest()
        defaults = data["defaults"]
        risk = RiskLimits()

        for f in dataclasses.fields(RiskLimits):
            manifest_entry = defaults[f.name]
            manifest_val = manifest_entry["value"]
            code_val = getattr(risk, f.name)

            # Handle bool comparison (YAML booleans are native Python bools)
            if isinstance(code_val, bool):
                assert manifest_val == code_val, (
                    f"Default mismatch for '{f.name}': "
                    f"manifest={manifest_val}, code={code_val}"
                )
            elif code_val is None:
                # A knob whose default is DERIVED, not a number (the margin-risk
                # cap: 30% at the sizing leverage, scaled to the venue's). The
                # manifest states the absence, never a figure the code does
                # not hold.
                assert manifest_val is None, (
                    f"Default mismatch for '{f.name}': "
                    f"manifest={manifest_val!r}, code=None (derived)"
                )
            elif isinstance(code_val, str):
                # A string-valued knob — the mode ladders (off/shadow/enforce).
                # `validation_gate_mode` was the first, and this branch did not
                # exist: every non-bool field went through float(), so the check
                # died with "could not convert string to float: 'shadow'"
                # instead of comparing anything. Exact match, not a skip — the
                # default a manifest STATES is exactly the promise this test is
                # here to keep, and a mode is as much a default as a threshold.
                assert manifest_val == code_val, (
                    f"Default mismatch for '{f.name}': "
                    f"manifest={manifest_val!r}, code={code_val!r}"
                )
            else:
                assert float(manifest_val) == pytest.approx(float(code_val), abs=0.01), (
                    f"Default mismatch for '{f.name}': "
                    f"manifest={manifest_val}, code={code_val}"
                )

    def test_manifest_all_checks_have_fail_behavior(self):
        """8. manifest_all_checks_have_fail_behavior - every check has fail_behavior field."""
        data = _load_manifest()
        valid_behaviors = {"closed", "open", "skip"}
        for check in data["checks"]:
            assert "fail_behavior" in check, (
                f"Check {check['id']} ({check['name']}) missing fail_behavior"
            )
            assert check["fail_behavior"] in valid_behaviors, (
                f"Check {check['id']} ({check['name']}) has invalid fail_behavior: "
                f"{check['fail_behavior']}"
            )

    def test_manifest_version_present(self):
        """9. manifest_version_present - version field exists."""
        data = _load_manifest()
        assert "version" in data
        assert data["version"] is not None
        assert str(data["version"]).strip() != ""

    def test_manifest_env_vars_match_config(self):
        """10. manifest_env_vars_match_config - env var names in manifest match config.py."""
        data = _load_manifest()
        defaults = data["defaults"]

        # Map of manifest default key -> expected env_var from config.py source
        # These are the env var names that config.py actually reads
        expected_env_vars = {
            "max_position_pct": "MAX_POSITION_PCT",
            "max_daily_loss_pct": "MAX_DAILY_LOSS_PCT",
            "max_drawdown_pct": "MAX_DRAWDOWN_PCT",
            "max_open_positions": "MAX_OPEN_POSITIONS",
            "max_correlation": "MAX_CORRELATION",
            "min_risk_reward": "MIN_RISK_REWARD",
            "min_confidence": "MIN_CONFIDENCE",
            "max_consecutive_losses": "MAX_CONSECUTIVE_LOSSES",
            "cooldown_after_loss_seconds": "COOLDOWN_AFTER_LOSS_SEC",
            "max_portfolio_exposure_pct": "MAX_PORTFOLIO_EXPOSURE_PCT",
            "max_symbol_exposure_pct": "MAX_SYMBOL_EXPOSURE_PCT",
            "max_correlation_per_group": "MAX_CORRELATION_PER_GROUP",
            "volatility_guard_atr_pct": "VOLATILITY_GUARD_ATR_PCT",
            "stale_data_max_age_seconds": "STALE_DATA_MAX_AGE_SEC",
            "require_stop_loss": "REQUIRE_STOP_LOSS",
            "commission_pct": "COMMISSION_PCT",
        }

        for key, expected_var in expected_env_vars.items():
            manifest_var = defaults[key]["env_var"]
            assert manifest_var == expected_var, (
                f"Env var mismatch for '{key}': "
                f"manifest={manifest_var}, expected={expected_var}"
            )


# ══════════════════════════════════════════════════════════════════
# PER-CHECK THRESHOLDS
# ══════════════════════════════════════════════════════════════════
#
# The `defaults:` block was held to RiskLimits; the per-check `threshold:`
# blocks were held by nothing, and nothing loads the manifest, so they drifted
# from the code they describe: COOLDOWN said 300 s where the code waits 120,
# VOLATILITY said 6% with meme coins "capped at 4%" where the code says 7%
# and gives meme coins a WIDER 10%, LIQUIDITY named a dead knob
# (MIN_BOOK_DEPTH_USD) and a 50K floor the code never enforced, RISK_REWARD
# named one global 1.2 where the gate reads a figure per strategy type, and
# MTF_ALIGNMENT described a 2-of-3 vote the gate does not take.
#
# The rule now: a threshold key that names a config field (RiskLimits,
# StrategyTypeConfig, OrderFlowConfig) is compared with that field's code
# default. Every other key is listed in `_derived_thresholds()` with the code
# it was read from and a verifier that asks that code, driving it where the
# figure is a literal inside a function. The list is two-way: a listed key the
# manifest no longer has fails too.

def _config_fields():
    """field name -> (owner, code default), plus any name two classes share."""
    import dataclasses

    from bot.config import RiskLimits, StrategyTypeConfig
    from bot.core.order_flow import OrderFlowConfig

    fields: dict[str, tuple[str, object]] = {}
    clashes: list[str] = []
    for cls in (RiskLimits, StrategyTypeConfig, OrderFlowConfig):
        inst = cls()
        for f in dataclasses.fields(cls):
            owner = f"{cls.__name__}.{f.name}"
            if f.name in fields:
                clashes.append(f"config field '{f.name}' is both {fields[f.name][0]} "
                               f"and {owner}: a threshold key cannot say which it means")
            fields[f.name] = (owner, getattr(inst, f.name))
    return fields, clashes


def _differs(manifest_value, code_value) -> bool:
    """Exact for bools, None and strings; float-equal for numbers.

    Not the defaults test's abs=0.01: a funding rate of 0.0005 against 0.001
    is a factor of two, and that tolerance would wave it through.
    """
    if isinstance(code_value, bool) or isinstance(manifest_value, bool):
        return manifest_value is not code_value
    if code_value is None or manifest_value is None:
        return manifest_value is not code_value
    if isinstance(code_value, str) or isinstance(manifest_value, str):
        return manifest_value != code_value
    try:
        return float(manifest_value) != pytest.approx(float(code_value), rel=1e-9, abs=1e-12)
    except (TypeError, ValueError):
        return True


def _reads(read):
    """Verifier for a figure the code holds as a value: compare with it."""
    def verify(value):
        code = read()
        return f"the code holds {code!r}" if _differs(value, code) else None
    return verify


def _line(check, gate: str):
    """("passed" | "failed", line) for one gate on a RiskCheck, or None."""
    for bucket, lines in (("passed", check.checks_passed), ("failed", check.checks_failed)):
        for line in lines:
            if line.startswith(f"{gate}:"):
                return bucket, line
    return None


def _evaluate(idea, atr):
    return _make_risk(_make_portfolio()).evaluate(idea, atr=atr)


def _book(closed: int, open_assets=()):
    """A RiskEngine over a real paper book: `closed` round trips, then opens."""
    portfolio = _make_portfolio()
    for i in range(closed):
        idea = _make_idea(asset="SOL/USDT", entry=100.0, sl=90.0, tp=120.0,
                          idea_id=f"TI-closed{i}")
        portfolio.open_position(idea, size_usd=100.0)
        portfolio.close_position(idea.id, 105.0)
    for i, asset in enumerate(open_assets):
        portfolio.open_position(
            _make_idea(asset=asset, entry=100.0, sl=90.0, tp=120.0, idea_id=f"TI-open{i}"),
            size_usd=100.0)
    return _make_risk(portfolio)


def _verify_rr_tolerance(tol):
    """At floor - tol the swing idea passes; one cent further below, it fails."""
    from bot.config import StrategyTypeConfig
    floor = StrategyTypeConfig().get_min_rr("swing")

    def verdict(rr):
        idea = _make_idea(entry=100.0, sl=90.0, tp=100.0 + 10.0 * rr)
        assert idea.risk_reward_ratio == pytest.approx(rr), "fixture R:R drifted"
        return _line(_evaluate(idea, atr=1.0), "RISK_REWARD")

    at, below = round(floor - tol, 2), round(floor - tol - 0.01, 2)
    if verdict(at)[0] != "passed":
        return f"a swing idea at R:R {at} (floor {floor} - {tol}) is refused"
    if verdict(below)[0] != "failed":
        return f"a swing idea at R:R {below}, below that tolerance, is allowed"
    return None


def _stale_verdict(seconds_old: float, order_type: str = "market"):
    from datetime import timedelta
    idea = _make_idea().model_copy(update={
        "timestamp": datetime.now(timezone.utc) - timedelta(seconds=seconds_old),
        "order_type": order_type,
    })
    return _line(_evaluate(idea, atr=1300.0), "STALE_DATA")[0]


def _verify_limit_multiplier(mult):
    from bot.config import RiskLimits
    limit_age = RiskLimits().stale_data_max_age_seconds * mult
    if _stale_verdict(limit_age - 15, "limit") != "passed":
        return f"a limit idea {limit_age - 15:.0f}s old is refused"
    if _stale_verdict(limit_age + 15, "limit") != "failed":
        return f"a limit idea {limit_age + 15:.0f}s old is allowed"
    # The control: the allowance is the LIMIT order's, not everybody's.
    if (limit_age - 15 > RiskLimits().stale_data_max_age_seconds
            and _stale_verdict(limit_age - 15, "market") != "failed"):
        return f"a market idea {limit_age - 15:.0f}s old is allowed too"
    return None


def _verify_future_skew(skew):
    if _stale_verdict(-(skew - 10)) != "passed":
        return f"an idea stamped {skew - 10}s in the future is refused"
    if _stale_verdict(-(skew + 10)) != "failed":
        return f"an idea stamped {skew + 10}s in the future is allowed"
    return None


def _verify_meme_floor(floor_pct):
    """DOGE (MEME group) rides max(guard, floor); BTC at the same ATR% does not."""
    from bot.config import RiskLimits
    guard = RiskLimits().volatility_guard_atr_pct
    meme_limit = max(guard, floor_pct)

    def verdict(asset, entry, atr_pct):
        idea = _make_idea(asset=asset, entry=entry, sl=entry * 0.9, tp=entry * 1.2)
        return _line(_evaluate(idea, atr=entry * atr_pct / 100.0), "VOLATILITY")[0]

    if verdict("DOGE/USDT", 1.0, meme_limit - 0.1) != "passed":
        return f"DOGE at ATR {meme_limit - 0.1:.1f}% is refused"
    if verdict("DOGE/USDT", 1.0, meme_limit + 0.1) != "failed":
        return f"DOGE at ATR {meme_limit + 0.1:.1f}% is allowed"
    if meme_limit - 0.1 > guard and verdict("BTC/USDT", 65000.0, meme_limit - 0.1) != "failed":
        return f"BTC at ATR {meme_limit - 0.1:.1f}% is allowed: the meme limit is not wider"
    return None


def _verify_depth_multiple(mult):
    from bot.core.order_flow import OrderFlowAnalyzer, OrderFlowSignal
    guard = OrderFlowAnalyzer()
    position = guard.config.min_top_depth_usd  # x mult clears the floor for any mult > 1
    need = max(position * mult, guard.config.min_top_depth_usd)

    def reason(depth):
        sig = OrderFlowSignal(symbol="XYZ/USDT", components_ok=["book"], spread_bps=1.0,
                              bid_depth_usd=depth, ask_depth_usd=depth,
                              bid_depth_top_usd=depth, ask_depth_top_usd=depth)
        return guard.liquidity_guard(sig, position_size_usd=position, symbol="XYZ/USDT")

    if reason(need + 1.0) is not None:
        return f"a {need + 1:,.0f} book under a {position:,.0f} position is refused"
    if reason(need - 1.0) is None:
        return f"a {need - 1:,.0f} book under a {position:,.0f} position is allowed"
    return None


def _verify_mtf_tags(n):
    """With htf_trend unset, n-1 bearish tags are no opinion; n refuse a LONG."""
    risk = _make_risk(_make_portfolio())

    def verdict(k):
        idea = _make_idea().model_copy(update={
            "htf_trend": "", "signals_used": [f"MTF:tf{i}=DOWN" for i in range(k)]})
        return risk._check_mtf_alignment(idea)

    if n >= 2 and verdict(n - 1) is not None:
        return f"{n - 1} bearish tags already refuse a LONG"
    if verdict(n) is None:
        return f"{n} bearish tags do not refuse a LONG"
    return None


def _macro_context(event_type: str, minutes_from_event: float):
    from datetime import timedelta

    from bot.core.macro_events import MacroEventProvider
    t0 = datetime(2030, 1, 15, 12, 0, tzinfo=timezone.utc)
    provider = MacroEventProvider(
        live_feed=lambda: [{"event_type": event_type, "label": "driven",
                            "scheduled_utc": t0.isoformat()}],
        failsafe=True)
    return provider.get_context(now=t0 + timedelta(minutes=minutes_from_event))


def _blackout_minutes(severity: str, attr: str):
    from bot.core.macro_events import _BLACKOUT, MacroSeverity
    return lambda: getattr(_BLACKOUT[MacroSeverity[severity]], attr).total_seconds() / 60.0


def _verify_post_event(minutes):
    """A MEDIUM event: still reduced `minutes`-1 after its block, clear at +1."""
    block_after = _blackout_minutes("MEDIUM", "block_after")()
    inside = _macro_context("PPI", block_after + minutes - 1)
    outside = _macro_context("PPI", block_after + minutes + 1)
    if inside.risk_state != "REDUCE":
        return f"{minutes - 1} min after the block ends the state is {inside.risk_state}"
    if outside.risk_state != "CLEAR":
        return f"{minutes + 1} min after the block ends the state is {outside.risk_state}"
    return None


def _verify_reduce_multiplier(mult):
    before = _blackout_minutes("CRITICAL", "block_before")()
    ctx = _macro_context("FOMC_DECISION", -(before + 1))
    if ctx.risk_state != "REDUCE":
        return f"a minute before an FOMC block the state is {ctx.risk_state}"
    return _reads(lambda: ctx.size_multiplier)(mult)


def _two_series_pc1(pc1: float) -> bool:
    """check_portfolio_concentration's verdict on two assets whose PC1 is `pc1`.

    For two assets the correlation matrix [[1, r], [r, 1]] has top eigenvalue
    1 + r, so PC1 explains (1 + r) / 2. x and z are orthogonal, zero-mean and
    equal-norm, so y = r*x + sqrt(1 - r^2)*z correlates with x at exactly r.
    """
    import math

    from bot.risk.risk_engine import RiskEngine
    r = 2.0 * pc1 - 1.0
    x, z = [1.0, -1.0, 1.0, -1.0], [1.0, 1.0, -1.0, -1.0]
    y = [r * a + math.sqrt(1.0 - r * r) * b for a, b in zip(x, z)]
    return RiskEngine.check_portfolio_concentration([x, y])[0]


def _verify_pc1(limit):
    if not 0.51 <= limit <= 0.98:
        return f"{limit} cannot be driven with two assets (PC1 lies in [0.5, 1])"
    if _two_series_pc1(limit - 0.01) is not True:
        return f"a book whose PC1 explains {limit - 0.01:.0%} is refused"
    if _two_series_pc1(limit + 0.01) is not False:
        return f"a book whose PC1 explains {limit + 0.01:.0%} is allowed"
    return None


def _verify_min_assets(n):
    from bot.risk.risk_engine import RiskEngine
    s = [1.0, -1.0, 1.0, -1.0]  # identical series: PC1 explains 100%
    if n >= 2 and RiskEngine.check_portfolio_concentration([s] * (n - 1))[0] is not True:
        return f"{n - 1} perfectly correlated assets are already judged"
    if RiskEngine.check_portfolio_concentration([s] * n)[0] is not False:
        return f"{n} perfectly correlated assets are not judged"
    return None


def _verify_min_periods(n):
    from bot.risk.risk_engine import RiskEngine
    s = [1.0, -1.0] * 10
    if n >= 2 and RiskEngine.check_portfolio_concentration([s[:n - 1]] * 2)[0] is not True:
        return f"{n - 1} return periods are already judged"
    if RiskEngine.check_portfolio_concentration([s[:n]] * 2)[0] is not False:
        return f"{n} return periods are not judged"
    return None


def _verify_min_trade_history(n):
    """Two open positions, no price history: the closed-trade fallback's gate."""
    held = ("BTC/USDT", "ETH/USDT")
    short = _book(n - 1, held)._check_concentration()[1]
    enough = _book(n, held)._check_concentration()[1]
    if "fewer than" not in short:
        return f"with {n - 1} closed trades the fallback reads them: {short!r}"
    if "closed trades per asset" not in enough:
        return f"with {n} closed trades the fallback still refuses to read them: {enough!r}"
    return None


def _verify_var_min_trades(n):
    from bot.risk.risk_engine import VarStatus
    if _book(n - 1)._compute_portfolio_var(100.0).status != VarStatus.SKIP:
        return f"{n - 1} closed trades already produce a VaR"
    if _book(n)._compute_portfolio_var(100.0).status != VarStatus.OK:
        return f"{n} closed trades still skip the VaR"
    return None


def _var_confidence_default():
    import inspect

    from bot.risk.risk_engine import RiskEngine
    return inspect.signature(RiskEngine._compute_portfolio_var).parameters["confidence_level"].default


def _derived_thresholds():
    """(check, key) -> (where the code holds it, verifier), for every threshold
    key that is NOT a config field. A verifier returns None when the code
    agrees with the manifest's figure, else the disagreement in words."""
    import inspect

    from bot.config import RiskLimits, RuntimeState, StrategyTypeConfig
    from bot.core.macro_events import MacroEventProvider
    from bot.risk.risk_engine import RiskEngine

    win = "bot/core/macro_events.py::_BLACKOUT"
    return {
        ("DRAWDOWN", "live_override_min_pct"): (
            "bot/config.py::RuntimeState.LIVE_DRAWDOWN_OVERRIDE_MIN",
            _reads(lambda: RuntimeState.LIVE_DRAWDOWN_OVERRIDE_MIN)),
        ("DRAWDOWN", "live_override_max_pct"): (
            "bot/config.py::RuntimeState.LIVE_DRAWDOWN_OVERRIDE_MAX",
            _reads(lambda: RuntimeState.LIVE_DRAWDOWN_OVERRIDE_MAX)),
        ("RISK_REWARD", "unmapped_strategy_min_rr"): (
            "bot/config.py::StrategyTypeConfig.get_min_rr (its getattr fallback)",
            _reads(lambda: StrategyTypeConfig().get_min_rr("no_such_strategy_type"))),
        ("RISK_REWARD", "boundary_tolerance"): (
            "bot/risk/risk_engine.py::RiskEngine._evaluate_locked (`rr < min_rr - 0.01`), driven",
            _verify_rr_tolerance),
        ("LOSS_STREAK", "soft_reject_at"): (
            "bot/risk/risk_engine.py::RiskEngine._soft_loss_streak_limit(max_consecutive_losses)",
            _reads(lambda: RiskEngine._soft_loss_streak_limit(
                RiskLimits().max_consecutive_losses))),
        ("STALE_DATA", "limit_order_age_multiplier"): (
            "bot/risk/risk_engine.py::RiskEngine._evaluate_locked (`2 if is_limit_order`), driven",
            _verify_limit_multiplier),
        ("STALE_DATA", "future_skew_tolerance_seconds"): (
            "bot/risk/risk_engine.py::RiskEngine._evaluate_locked (_CLOCK_SKEW_TOLERANCE_S), driven",
            _verify_future_skew),
        ("VOLATILITY", "meme_coin_threshold_floor_pct"): (
            "bot/risk/risk_engine.py::RiskEngine._evaluate_locked "
            "(`max(volatility_guard_atr_pct, 10.0) if is_meme`), driven",
            _verify_meme_floor),
        ("LIQUIDITY", "depth_position_multiple"): (
            "bot/core/order_flow.py::OrderFlowAnalyzer.liquidity_guard "
            "(`position_size_usd * 10.0`), driven",
            _verify_depth_multiple),
        ("MACRO_EVENT", "critical_block_before_min"): (
            win, _reads(_blackout_minutes("CRITICAL", "block_before"))),
        ("MACRO_EVENT", "critical_block_after_min"): (
            win, _reads(_blackout_minutes("CRITICAL", "block_after"))),
        ("MACRO_EVENT", "critical_reduce_before_min"): (
            win, _reads(_blackout_minutes("CRITICAL", "reduce_before"))),
        ("MACRO_EVENT", "high_block_before_min"): (
            win, _reads(_blackout_minutes("HIGH", "block_before"))),
        ("MACRO_EVENT", "high_block_after_min"): (
            win, _reads(_blackout_minutes("HIGH", "block_after"))),
        ("MACRO_EVENT", "high_reduce_before_min"): (
            win, _reads(_blackout_minutes("HIGH", "reduce_before"))),
        ("MACRO_EVENT", "medium_block_before_min"): (
            win, _reads(_blackout_minutes("MEDIUM", "block_before"))),
        ("MACRO_EVENT", "medium_block_after_min"): (
            win, _reads(_blackout_minutes("MEDIUM", "block_after"))),
        ("MACRO_EVENT", "post_event_reduce_min"): (
            "bot/core/macro_events.py::MacroEventProvider._evaluate_windows "
            "(`timedelta(minutes=30)`), driven",
            _verify_post_event),
        ("MACRO_EVENT", "reduce_size_multiplier"): (
            "bot/core/macro_events.py::MacroEventProvider._evaluate_windows "
            "(`best_mult = 0.5`), driven",
            _verify_reduce_multiplier),
        ("MACRO_EVENT", "funding_block_rate_per_8h"): (
            "bot/core/macro_events.py::MacroEventProvider.FUNDING_CRITICAL_THRESHOLD",
            _reads(lambda: MacroEventProvider.FUNDING_CRITICAL_THRESHOLD)),
        ("MACRO_EVENT", "calendar_stale_after_hours"): (
            "bot/core/macro_events.py::MacroEventProvider.__init__ (max_stale_hours "
            "default; the engine passes none)",
            _reads(lambda: inspect.signature(MacroEventProvider.__init__)
                   .parameters["max_stale_hours"].default)),
        ("MTF_ALIGNMENT", "legacy_min_mtf_tags"): (
            "bot/risk/risk_engine.py::RiskEngine._check_mtf_alignment "
            "(`len(mtf_trends) >= 2`), driven",
            _verify_mtf_tags),
        ("CONCENTRATION_PCA", "pc1_max_explained_variance"): (
            "bot/risk/risk_engine.py::RiskEngine.check_portfolio_concentration "
            "(`pc1_explained > 0.70`), driven",
            _verify_pc1),
        ("CONCENTRATION_PCA", "min_assets_required"): (
            "bot/risk/risk_engine.py::RiskEngine.check_portfolio_concentration "
            "(`n_assets < 2`), driven",
            _verify_min_assets),
        ("CONCENTRATION_PCA", "min_return_periods"): (
            "bot/risk/risk_engine.py::RiskEngine.check_portfolio_concentration "
            "(`n_periods < 3`), driven",
            _verify_min_periods),
        ("CONCENTRATION_PCA", "min_trade_history"): (
            "bot/risk/risk_engine.py::RiskEngine._check_concentration "
            "(`len(history) < 5`), driven",
            _verify_min_trade_history),
        ("PORTFOLIO_VAR", "confidence_level"): (
            "bot/risk/risk_engine.py::RiskEngine._compute_portfolio_var "
            "(confidence_level default; the gate passes none)",
            _reads(_var_confidence_default)),
        ("PORTFOLIO_VAR", "z_score"): (
            "bot/risk/risk_engine.py::RiskEngine._var_z_score(confidence_level)",
            _reads(lambda: RiskEngine._var_z_score(_var_confidence_default()))),
        ("PORTFOLIO_VAR", "min_trades_required"): (
            "bot/risk/risk_engine.py::RiskEngine._compute_portfolio_var "
            "(`len(closed) < 5`), driven",
            _verify_var_min_trades),
    }


#: Config fields a check's threshold must NOT name, because that check does not
#: read them. Each one did name it, which is how this list came to exist; a
#: comparison with the field's default would pass and say nothing.
_NOT_READ_BY_ITS_CHECK = {
    "min_book_depth_usd": "bot/config.py records it as a dead knob: #17 reads "
                          "OrderFlowConfig.min_top_depth_usd",
    "min_risk_reward": "#6 reads StrategyTypeConfig.get_min_rr(strategy_type), "
                       "not RiskLimits.min_risk_reward",
}


def _threshold_disagreements(checks, only=None) -> list[str]:
    """Every way the manifest's per-check thresholds disagree with the code.

    `only` restricts the walk (and the two-way listing check) to those check
    names, so a control can drive one block without driving all of them.
    """
    fields, clashes = _config_fields()
    derived = _derived_thresholds()
    out = list(clashes)
    seen: set[tuple[str, str]] = set()
    for check in checks:
        name = check["name"]
        if only is not None and name not in only:
            continue
        block = check.get("threshold")
        if block is None:
            continue
        if not isinstance(block, dict):
            out.append(f"{name}: threshold is neither null nor a mapping ({block!r})")
            continue
        for key, value in block.items():
            seen.add((name, key))
            if key in _NOT_READ_BY_ITS_CHECK:
                out.append(f"{name}.{key}: the check does not read this field "
                           f"({_NOT_READ_BY_ITS_CHECK[key]})")
            elif (name, key) in derived:
                where, verify = derived[(name, key)]
                if key in fields:
                    out.append(f"{name}.{key} is listed as derived but is the config "
                               f"field {fields[key][0]}; compare it as the field")
                    continue
                try:
                    why = verify(value)
                except Exception as exc:  # a figure the drive cannot even use
                    why = f"driving it raised {type(exc).__name__}: {exc}"
                if why:
                    out.append(f"{name}.{key} = {value!r} disagrees with {where}: {why}")
            elif key in fields:
                owner, code = fields[key]
                if _differs(value, code):
                    out.append(f"{name}.{key} = {value!r}, but {owner} defaults to {code!r}")
            else:
                out.append(f"{name}.{key} names no config field and is not listed in "
                           f"_derived_thresholds() with the code it was read from")
    for name, key in derived:
        if (only is None or name in only) and (name, key) not in seen:
            out.append(f"{name}.{key} is listed in _derived_thresholds() but the "
                       f"manifest no longer has it")
    return out


def _check_named(data: dict, name: str) -> dict:
    return next(c for c in data["checks"] if c["name"] == name)


class TestPerCheckThresholds:
    """Every `threshold:` figure in the manifest is the code's figure."""

    def test_every_threshold_figure_is_the_codes(self):
        bad = _threshold_disagreements(_load_manifest()["checks"])
        assert not bad, "risk_manifest.yaml thresholds disagree with the code:\n  " + \
            "\n  ".join(bad)

    def test_the_comparison_finds_a_config_figure_that_drifted(self):
        """THE CONTROL for the config-field arm: COOLDOWN's old figure, 300 s."""
        from bot.config import RiskLimits
        code = RiskLimits().cooldown_after_loss_seconds
        data = _load_manifest()
        assert _threshold_disagreements(data["checks"], only={"COOLDOWN"}) == []
        drifted = 300 if code != 300 else 301
        _check_named(data, "COOLDOWN")["threshold"]["cooldown_after_loss_seconds"] = drifted
        bad = _threshold_disagreements(data["checks"], only={"COOLDOWN"})
        assert bad == [f"COOLDOWN.cooldown_after_loss_seconds = {drifted!r}, but "
                       f"RiskLimits.cooldown_after_loss_seconds defaults to {code!r}"], bad

    def test_the_comparison_finds_a_derived_figure_that_drifted(self):
        """THE CONTROL for the derived arm: VOLATILITY's old meme figure, 4%."""
        data = _load_manifest()
        assert _threshold_disagreements(data["checks"], only={"VOLATILITY"}) == []
        _check_named(data, "VOLATILITY")["threshold"]["meme_coin_threshold_floor_pct"] = 4.0
        bad = _threshold_disagreements(data["checks"], only={"VOLATILITY"})
        assert len(bad) == 1 and "VOLATILITY.meme_coin_threshold_floor_pct" in bad[0], bad

    def test_an_unlisted_key_and_a_dead_knob_are_refused(self):
        """A key nobody can trace, and a field the check does not read.

        `min_book_depth_usd: 2000.0` EQUALS its own RiskLimits default, so a
        default comparison alone would pass it; that is the drift this file had.
        """
        data = _load_manifest()
        assert _threshold_disagreements(data["checks"], only={"LIQUIDITY"}) == []
        block = _check_named(data, "LIQUIDITY")["threshold"]
        block["min_book_depth_usd"] = 2000.0
        block["large_cap_floor_usd"] = 50000.0
        bad = _threshold_disagreements(data["checks"], only={"LIQUIDITY"})
        assert len(bad) == 2, bad
        assert any("LIQUIDITY.min_book_depth_usd" in b and "does not read" in b for b in bad)
        assert any("LIQUIDITY.large_cap_floor_usd names no config field" in b for b in bad)

    def test_a_listed_key_the_manifest_dropped_fails(self):
        """The list is two-way: an entry for a key that is gone is stale."""
        data = _load_manifest()
        del _check_named(data, "STALE_DATA")["threshold"]["future_skew_tolerance_seconds"]
        bad = _threshold_disagreements(data["checks"], only={"STALE_DATA"})
        assert bad == ["STALE_DATA.future_skew_tolerance_seconds is listed in "
                       "_derived_thresholds() but the manifest no longer has it"], bad

    def test_every_dead_knob_listed_is_still_a_config_field(self):
        """Two-way: a refusal for a field that no longer exists guards nothing."""
        fields, _ = _config_fields()
        gone = [k for k in _NOT_READ_BY_ITS_CHECK if k not in fields]
        assert not gone, f"no longer config fields, drop them from the list: {gone}"


# ══════════════════════════════════════════════════════════════════
# WHYNOT TESTS
# ══════════════════════════════════════════════════════════════════

class TestWhyNot:
    """Test /whynot rejection explainer."""

    def _make_engine_with_rejections(self, rejections=None):
        """Create a minimal mock engine with _last_rejections."""
        engine = MagicMock()
        engine._last_rejections = rejections if rejections is not None else {}
        return engine

    def _sample_rejection(self, symbol="BTC/USDT", direction="LONG",
                          confidence=0.55, checks_failed=None,
                          checks_passed=None):
        return {
            "symbol": symbol,
            "direction": direction,
            "confidence": confidence,
            "entry_price": 65000.0,
            "stop_loss": 58500.0,
            "take_profit": 72800.0,
            "checks_passed": checks_passed or [
                "CIRCUIT_BREAKER: OK",
                "POSITION_SIZE: notional 15.0% <= 20%",
                "DAILY_LOSS: 0.0% OK",
            ],
            "checks_failed": checks_failed or [
                "CONFIDENCE: 0.55 < 0.6 minimum",
            ],
            "reason": "CONFIDENCE: 0.55 < 0.6 minimum",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    @pytest.mark.asyncio
    async def test_whynot_no_rejections_returns_message(self):
        """11. whynot_no_rejections_returns_message - empty _last_rejections gives no-rejections msg."""
        from bot.skills.skill_registry import WhyNotSkill
        skill = WhyNotSkill()
        engine = self._make_engine_with_rejections({})
        result = await skill.execute(engine)
        assert "no" in result.lower() or "No" in result
        assert "reject" in result.lower()

    @pytest.mark.asyncio
    async def test_whynot_stores_rejection_on_risk_fail(self):
        """12. whynot_stores_rejection_on_risk_fail - mock a rejection, verify it's stored."""
        portfolio = _make_portfolio()
        risk = _make_risk(portfolio)
        idea = _make_idea(confidence=0.3)  # below threshold
        atr = 2600.0
        check = risk.evaluate(idea, atr=atr)
        assert check.verdict.value == "REJECTED"
        # Simulate what engine.py does: store rejection
        rejections: dict[str, dict] = {}
        symbol_key = idea.asset.replace("/USDT", "").upper()
        rejections[symbol_key] = {
            "symbol": idea.asset,
            "checks_failed": check.checks_failed,
            "checks_passed": check.checks_passed,
        }
        assert symbol_key in rejections
        assert len(rejections[symbol_key]["checks_failed"]) > 0

    @pytest.mark.asyncio
    async def test_whynot_retrieves_by_symbol(self):
        """13. whynot_retrieves_by_symbol - store rejection for BTC, query 'BTC' returns it."""
        from bot.skills.skill_registry import WhyNotSkill
        skill = WhyNotSkill()
        rej = self._sample_rejection(symbol="BTC/USDT")
        engine = self._make_engine_with_rejections({"BTC": rej})
        result = await skill.execute(engine, symbol="BTC")
        assert "BTC" in result
        assert "REJECTED" in result or "CONFIDENCE" in result

    @pytest.mark.asyncio
    async def test_whynot_shows_failed_checks(self):
        """14. whynot_shows_failed_checks - output includes failed check names."""
        from bot.skills.skill_registry import WhyNotSkill
        skill = WhyNotSkill()
        rej = self._sample_rejection(
            checks_failed=[
                "CONFIDENCE: 0.55 < 0.6 minimum",
                "LOSS_STREAK: 3 consecutive losses (>= 3)",
            ]
        )
        engine = self._make_engine_with_rejections({"BTC": rej})
        result = await skill.execute(engine, symbol="BTC")
        assert "CONFIDENCE" in result
        assert "LOSS_STREAK" in result
        assert "Failed" in result or "failed" in result.lower()

    @pytest.mark.asyncio
    async def test_whynot_shows_passed_checks(self):
        """15. whynot_shows_passed_checks - output includes passed check count."""
        from bot.skills.skill_registry import WhyNotSkill
        skill = WhyNotSkill()
        rej = self._sample_rejection(
            checks_passed=[
                "CIRCUIT_BREAKER: OK",
                "POSITION_SIZE: notional 15.0% <= 20%",
                "DAILY_LOSS: 0.0% OK",
            ]
        )
        engine = self._make_engine_with_rejections({"BTC": rej})
        result = await skill.execute(engine, symbol="BTC")
        assert "Passed" in result or "passed" in result.lower()
        # Should mention the count (3)
        assert "3" in result

    @pytest.mark.asyncio
    async def test_whynot_latest_when_no_symbol(self):
        """16. whynot_latest_when_no_symbol - no symbol arg returns most recent."""
        from bot.skills.skill_registry import WhyNotSkill
        skill = WhyNotSkill()
        rej_btc = self._sample_rejection(symbol="BTC/USDT")
        rej_eth = self._sample_rejection(symbol="ETH/USDT")
        # dict ordering: ETH is last inserted
        engine = self._make_engine_with_rejections({
            "BTC": rej_btc,
            "ETH": rej_eth,
        })
        result = await skill.execute(engine)
        # Should show ETH (the most recent / last key)
        assert "ETH" in result

    @pytest.mark.asyncio
    async def test_whynot_unknown_symbol_returns_message(self):
        """17. whynot_unknown_symbol_returns_message - query 'XYZ' returns not-found msg."""
        from bot.skills.skill_registry import WhyNotSkill
        skill = WhyNotSkill()
        rej = self._sample_rejection(symbol="BTC/USDT")
        engine = self._make_engine_with_rejections({"BTC": rej})
        result = await skill.execute(engine, symbol="XYZ")
        assert "no rejection" in result.lower() or "No rejection" in result

    @pytest.mark.asyncio
    async def test_whynot_cap_at_100(self):
        """18. whynot_cap_at_100 - store 110 rejections, verify capped at ~100."""
        # This tests the capping logic from engine.py _analyze_signal
        rejections: dict[str, dict] = {}
        for i in range(110):
            symbol_key = f"SYM{i}"
            rejections[symbol_key] = self._sample_rejection(symbol=f"SYM{i}/USDT")

        # Apply the same capping logic from engine.py
        if len(rejections) > 100:
            oldest_keys = list(rejections.keys())[:-50]
            for k in oldest_keys:
                rejections.pop(k, None)

        # After capping, should have 50 entries (kept the last 50)
        assert len(rejections) <= 100
        assert len(rejections) == 50
        # The remaining keys should be the last 50 (SYM60..SYM109)
        assert "SYM109" in rejections
        assert "SYM0" not in rejections
