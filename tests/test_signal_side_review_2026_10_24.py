"""Signal-side review 2026-10-24, fixes 1-6. Each default is reversible."""

from __future__ import annotations

import dataclasses

from bot.config import CONFIG
from bot.core.analyzer import (
    funding_contrarian_adjustment,
    leverage_capped_levels,
    market_stop_levels,
    smart_money_direct_adjustment,
)
from bot.core.market_scanner import _class_scan_enabled
from bot.utils.models import Direction


def test_live_scan_classes_ship_off_and_turn_back_on():
    assert CONFIG.scan_class_commodities is False
    assert CONFIG.scan_class_stocks is False
    assert CONFIG.scan_class_etfs is False
    assert CONFIG.scan_class_metals is False
    assert CONFIG.scan_tradfi_full_coverage is False
    # The suite turns this off for the duration of a test. The shipped default
    # is the field default.
    assert type(CONFIG.risk).__dataclass_fields__["live_universe_gate_enabled"].default is True
    assert _class_scan_enabled("Stock") is False
    assert _class_scan_enabled("Crypto") is True
    object_set = __import__("builtins").object
    object_set.__setattr__(CONFIG, "scan_class_stocks", True)
    try:
        assert _class_scan_enabled("Stock") is True
    finally:
        object_set.__setattr__(CONFIG, "scan_class_stocks", False)


def test_next_open_is_the_fill_default_and_same_bar_limits_are_off():
    from bot.backtest.models import BacktestConfig
    from bot.backtest.runner import build_parser
    cfg = BacktestConfig()
    assert cfg.fill_mode == "next_open"
    assert cfg.same_bar_limit_fill is False
    args = build_parser().parse_args([])
    assert args.fill_mode == "next_open"


def test_the_deep_backtest_names_itself_a_smoke_test():
    import run_deep_backtest as deep
    meta = deep.result_meta(1, [], [], 0.0)
    assert meta["role"] == "smoke_test"
    assert meta["fill_mode"] == "next_open"
    assert "smoke" in meta["label"].lower()
    assert "not evidence" in meta["label"].lower()


def test_the_pages_call_the_synthetic_runs_smoke_tests():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    page = (root / "docs/gitbook/live-validation.md").read_text(encoding="utf-8")
    full = (root / "docs/BACKTEST_FULL_UNIVERSE.md").read_text(encoding="utf-8")
    assert "smoke test" in page.lower()
    assert "not evidence of an edge" in page.lower()
    assert "smoke test" in full.lower()
    assert full.lower().index("smoke test") < full.lower().index("**date:**")


def test_noise_flags_ship_off_and_the_two_new_ones_stay_off():
    a = CONFIG.analyzer
    assert a.elliott_zigzag_enabled is False
    assert a.elliott_wave_action_enabled is False
    assert a.elliott_fib_targets_enabled is False
    assert a.elliott_mtf_enabled is False
    assert a.elliott_mtf_alignment_enabled is False
    assert a.candle_strength_vote_enabled is False
    assert a.candle_mtf_enabled is False
    assert a.candle_trend_context_enabled is True
    assert a.setup_expectancy_enabled is False
    assert a.external_sentiment_enabled is False
    assert a.funding_contrarian_tilt_enabled is False
    assert a.smart_money_boost_enabled is False
    assert a.funding_cost_aware_enabled is True
    assert CONFIG.learning.learn_from_paper_closes_enabled is False
    assert funding_contrarian_adjustment(-0.001, Direction.LONG) == (0.0, "")
    assert smart_money_direct_adjustment(0.8, Direction.LONG) == 0.0
    object.__setattr__(a, "funding_contrarian_tilt_enabled", True)
    object.__setattr__(a, "smart_money_boost_enabled", True)
    try:
        delta, note = funding_contrarian_adjustment(-0.001, Direction.LONG)
        assert delta == 0.03 and "LONG" in note
        assert smart_money_direct_adjustment(0.8, Direction.LONG) == 0.05 * 0.8
    finally:
        object.__setattr__(a, "funding_contrarian_tilt_enabled", False)
        object.__setattr__(a, "smart_money_boost_enabled", False)


def test_the_stop_stays_and_leverage_is_what_moves():
    assert CONFIG.risk.leverage_from_stop_enabled is True
    assert CONFIG.exchange.dynamic_leverage_enabled is False
    sl, tp, sentence = market_stop_levels(Direction.LONG, 100.0, 92.0, 116.0)
    assert (sl, tp, sentence) == (92.0, 116.0, None)
    object.__setattr__(CONFIG.risk, "leverage_from_stop_enabled", False)
    try:
        pulled = market_stop_levels(Direction.LONG, 100.0, 92.0, 116.0)
        assert pulled == leverage_capped_levels(Direction.LONG, 100.0, 92.0, 116.0)
        assert pulled[0] != 92.0
    finally:
        object.__setattr__(CONFIG.risk, "leverage_from_stop_enabled", True)


def test_kelly_on_risk_is_the_default_and_can_be_turned_off():
    assert CONFIG.risk.kelly_sizing_enabled is True
    # The suite turns this off for the duration of a test. The shipped default
    # is the field default.
    assert type(CONFIG.risk).__dataclass_fields__["kelly_on_risk_enabled"].default is True
    assert CONFIG.risk.kelly_floor_risk_pct == 0.25
    # The flag-off reading is a no-op (0), which the caller leaves as full size.
    import os
    import tempfile
    from datetime import datetime, timezone

    import bot.risk.risk_engine as mod
    from bot.risk.portfolio import PortfolioTracker
    from bot.risk.risk_engine import RiskEngine
    from bot.utils.models import TradeExecution, TradeStatus
    state = os.path.join(tempfile.mkdtemp(prefix="rc-rev-"), "risk.json")
    eng = RiskEngine(PortfolioTracker(initial_balance=10_000.0), state_file=state)
    idea = __import__("bot.utils.models", fromlist=["TradeIdea"]).TradeIdea(
        asset="BTC/USDT", direction=Direction.LONG, entry_price=100.0,
        stop_loss=98.0, take_profit=106.0, confidence=0.8,
        reasoning="kelly", source="scan", timestamp=datetime.now(timezone.utc))
    for i in range(3):
        eng._portfolio._history.append(TradeExecution(
            trade_id=f"W{i}", asset="BTC/USDT", direction=Direction.LONG,
            entry_price=100.0, stop_loss=98.0, take_profit=106.0, exit_price=105.0,
            quantity=1.0, status=TradeStatus.EXECUTED, pnl=50.0))
    object.__setattr__(CONFIG.risk, "kelly_on_risk_enabled", True)
    try:
        on = eng._kelly_size_usd(idea, 10_000.0)
    finally:
        object.__setattr__(CONFIG.risk, "kelly_on_risk_enabled", False)
    off_cfg = dataclasses.replace(CONFIG, risk=dataclasses.replace(
        CONFIG.risk, kelly_on_risk_enabled=False))
    orig = mod.CONFIG
    mod.CONFIG = off_cfg
    try:
        off = eng._kelly_size_usd(idea, 10_000.0)
    finally:
        mod.CONFIG = orig
    assert on > 0
    assert off == 0.0
    assert on < 10_000.0 * (CONFIG.risk.max_position_pct / 100.0)
