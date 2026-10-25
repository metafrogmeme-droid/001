"""Per-symbol funding grids, and the clock's cost-only mode.

Bitget settles some USDT perps every 1h or 4h. One 8h grid undercharges
those and the funding clock misses their settlements. The interval is read
off the venue payload, remembered, and used by the clock and the backtest
charge. Unreadable stays the 8h fallback and says so.

FUNDING_CLOCK_GATE_MODE defaults to block. cost_only does not refuse the
entry; it subtracts one settlement from expectancy. Every check records
both decisions.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from bot.config import CONFIG
from bot.risk.funding_clock import (
    SETTLEMENT_INTERVAL_SEC,
    funding_clock_gate_mode,
    funding_clock_pair,
    funding_clock_verdict,
    seconds_to_settlement,
)
from bot.risk.funding_interval import (
    FALLBACK_HOURS,
    IntervalBook,
    canonical_symbol,
    install_book,
    interval_seconds,
    parse_interval_hours,
    read_interval_hours,
)

# 2026-07-14 00:00:00 UTC — an 8h boundary, so also a 4h and a 1h boundary.
T_SETTLE = 1_784_073_600.0
assert T_SETTLE % (8 * 3600) == 0


@pytest.fixture
def intervals(tmp_path, monkeypatch):
    monkeypatch.delenv("FUNDING_INTERVAL_HOURS", raising=False)
    monkeypatch.delenv("FUNDING_CLOCK_SHADOW_PATH", raising=False)
    book = IntervalBook(path=str(tmp_path / "funding_intervals.json"))
    install_book(book)
    yield book
    install_book(IntervalBook(path=None))


class TestParse:
    @pytest.mark.parametrize("raw,hours", [
        ("1", 1), ("4", 4), ("8", 8), (1, 1), (4, 4), (8, 8),
        ("1h", 1), ("4h", 4), ("8h", 8), ("2", 2),
    ])
    def test_the_grids_bitget_names(self, raw, hours):
        assert parse_interval_hours(raw) == hours

    @pytest.mark.parametrize("raw", [None, "", "0", "3", "16", "1.5", "abc", True])
    def test_unreadable_is_not_eight(self, raw):
        assert parse_interval_hours(raw) is None

    def test_a_payload_prefers_the_venue_field(self):
        assert read_interval_hours(
            {"interval": "8h", "info": {"fundingRateInterval": "4"}}) == 4
        assert read_interval_hours({"interval": "1h"}) == 1
        assert read_interval_hours({"fundingRate": "0.0001"}) is None
        assert read_interval_hours(None) is None


class TestTheBook:
    def test_one_symbol_keeps_its_own_grid(self, intervals):
        assert intervals.remember("BTC/USDT", {"fundingRateInterval": "8"}).hours == 8
        assert intervals.remember("PEPE/USDT:USDT", {"info": {"fundingRateInterval": "4"}}).hours == 4
        assert intervals.remember("1000BONKUSDT", {"interval": "1h"}).hours == 1
        assert intervals.get("BTC/USDT").hours == 8
        assert intervals.get("PEPEUSDT").hours == 4
        assert intervals.get("1000BONK/USDT").hours == 1
        for sym in ("BTC/USDT", "PEPE/USDT", "1000BONKUSDT"):
            assert intervals.get(sym).measured is True
            assert intervals.get(sym).source == "cache"

    def test_an_unreadable_payload_is_the_fallback_and_is_not_stored(self, intervals):
        assert intervals.remember("SOL/USDT", {"fundingRate": "0.0001"}) is None
        read = intervals.get("SOL/USDT")
        assert read.hours == FALLBACK_HOURS == 8
        assert read.measured is False
        assert read.source == "fallback"
        assert interval_seconds("SOL/USDT") == pytest.approx(SETTLEMENT_INTERVAL_SEC)

    def test_a_reading_survives_a_new_process(self, intervals, tmp_path):
        intervals.remember("SUI/USDT", {"fundingRateInterval": "1"})
        again = IntervalBook(path=str(tmp_path / "funding_intervals.json"))
        read = again.get("SUI/USDT")
        assert read.hours == 1 and read.measured is True and read.source == "cache"
        # The file does not record the fallback as if the venue had said 8.
        stored = json.loads((tmp_path / "funding_intervals.json").read_text())
        assert list(stored) == [canonical_symbol("SUI/USDT")]

    def test_the_override_forces_one_grid(self, intervals, monkeypatch):
        intervals.remember("ETH/USDT", {"fundingRateInterval": "1"})
        monkeypatch.setenv("FUNDING_INTERVAL_HOURS", "4")
        read = intervals.get("ETH/USDT")
        assert read.hours == 4 and read.source == "override"
        monkeypatch.setenv("FUNDING_INTERVAL_HOURS", "nope")
        assert intervals.get("ETH/USDT").hours == 1


class TestGridsChangeTheClockAndTheCharge:
    def test_seconds_to_the_next_boundary_on_each_grid(self):
        # 10 minutes before the next hour, 50 minutes after an 8h settle.
        now = T_SETTLE + 3600 - 600
        assert seconds_to_settlement(now, 1 * 3600) == pytest.approx(600)
        assert seconds_to_settlement(now, 4 * 3600) == pytest.approx(3 * 3600 + 600)
        assert seconds_to_settlement(now, 8 * 3600) == pytest.approx(7 * 3600 + 600)
        # No interval is the 8h fallback, including exactly on a boundary.
        assert seconds_to_settlement(T_SETTLE) == pytest.approx(SETTLEMENT_INTERVAL_SEC)

    def test_a_1h_symbol_is_inside_the_window_when_an_8h_symbol_is_not(self):
        now = T_SETTLE + 3600 - 600
        blocked_1h, _ = funding_clock_verdict(
            "LONG", 0.001, now, interval_sec=3600)
        blocked_8h, reason_8h = funding_clock_verdict(
            "LONG", 0.001, now, interval_sec=8 * 3600)
        assert blocked_1h is True
        assert blocked_8h is False
        assert "away" in reason_8h

    def test_a_4h_symbol_blocks_fifteen_minutes_before_its_own_settle(self):
        now = T_SETTLE + 4 * 3600 - 900
        assert funding_clock_verdict(
            "LONG", 0.001, now, interval_sec=4 * 3600)[0] is True
        assert funding_clock_verdict(
            "LONG", 0.001, now, interval_sec=8 * 3600)[0] is False

    def test_the_same_hold_pays_more_often_on_a_shorter_grid(self):
        from bot.backtest.funding import funding_for_position, settlements_spanned
        entry, exit_ = 0.1 * 3600, 24.1 * 3600
        n8 = settlements_spanned(entry, exit_)
        n4 = settlements_spanned(entry, exit_, 4 * 3600)
        n1 = settlements_spanned(entry, exit_, 1 * 3600)
        assert n8 == settlements_spanned(entry, exit_, 8 * 3600)
        assert n1 > n4 > n8 > 0
        rate, notional = 0.0001, 10_000.0
        charges = []
        for n, grid in ((n8, None), (n4, 4 * 3600), (n1, 3600)):
            read = funding_for_position(
                "LONG", notional, entry, exit_, rate, True, interval_sec=grid)
            assert read.intervals == n
            assert read.usd == pytest.approx(-rate * notional * n)
            charges.append(read.usd)
        assert charges[2] < charges[1] < charges[0] < 0


class TestCostOnlyLeavesTheBlockDefault:
    def test_the_mode_ships_as_block(self, monkeypatch):
        monkeypatch.delenv("FUNDING_CLOCK_GATE_MODE", raising=False)
        from bot.config import RiskLimits
        assert RiskLimits().funding_clock_gate_mode == "block"
        assert CONFIG.risk.funding_clock_gate_enabled is True
        assert funding_clock_gate_mode("block") == "block"
        assert funding_clock_gate_mode("cost_only") == "cost_only"
        assert funding_clock_gate_mode("yes") == "block"

    def test_both_decisions_are_in_the_pair(self):
        pair = funding_clock_pair(
            "LONG", 0.001, T_SETTLE - 900,
            confidence=0.7, reward_r=3.0, stop_distance_pct=0.02)
        assert pair["block"]["would_block"] is True
        cost = pair["cost_only"]
        assert cost["would_block"] is False
        assert cost["charge_state"] == "charged"
        assert cost["cost_fraction"] == pytest.approx(0.001)
        # 0.7 * 3 - 0.3 = 1.8R, minus 0.001/0.02 = 0.05R.
        assert cost["expectancy_r_before"] == pytest.approx(1.8)
        assert cost["expectancy_r_after"] == pytest.approx(1.75)

    def test_a_pass_charges_nothing(self):
        pair = funding_clock_pair(
            "SHORT", 0.001, T_SETTLE - 900,
            confidence=0.7, reward_r=3.0, stop_distance_pct=0.02)
        assert pair["block"]["would_block"] is False
        assert pair["cost_only"]["charge_state"] == "not_charged"
        assert pair["cost_only"]["expectancy_r_after"] == pytest.approx(
            pair["cost_only"]["expectancy_r_before"])

    def test_an_unreadable_stop_is_not_a_zero_charge(self):
        pair = funding_clock_pair(
            "LONG", 0.001, T_SETTLE - 900,
            confidence=0.7, reward_r=3.0, stop_distance_pct=None)
        assert pair["block"]["would_block"] is True
        assert pair["cost_only"]["would_block"] is False
        assert pair["cost_only"]["charge_state"] == "unpriced"
        assert pair["cost_only"]["expectancy_r_after"] is None

    def test_the_engine_blocks_by_default_and_records_both(self, intervals, tmp_path, monkeypatch):
        import bot.risk.risk_engine as risk_engine_mod
        from bot.risk.portfolio import PortfolioTracker
        from bot.risk.risk_engine import RiskEngine
        from bot.utils.models import Direction, TradeIdea

        shadow = tmp_path / "shadow.jsonl"
        monkeypatch.setenv("FUNDING_CLOCK_SHADOW_PATH", str(shadow))
        state = str(tmp_path / "risk_state.json")
        eng = RiskEngine(PortfolioTracker(initial_balance=10_000.0), state_file=state)
        eng._last_of_signal = SimpleNamespace(symbol="BTC/USDT", funding_rate=0.001)
        eng.set_sim_time(datetime.fromtimestamp(T_SETTLE - 900, tz=timezone.utc))
        idea = TradeIdea(
            asset="BTC/USDT", direction=Direction.LONG, entry_price=100.0,
            stop_loss=98.0, take_profit=106.0, confidence=0.7,
            reasoning="clock", source="scan",
            timestamp=datetime.fromtimestamp(T_SETTLE - 900, tz=timezone.utc))

        blocked = eng.evaluate(idea)
        assert any(line.startswith("FUNDING_CLOCK:") and "would pay extreme" in line
                   for line in blocked.checks_failed)
        assert not any("cost-only" in line for line in blocked.checks_passed)

        new_risk = dataclasses.replace(CONFIG.risk, funding_clock_gate_mode="cost_only")
        monkeypatch.setattr(risk_engine_mod, "CONFIG", dataclasses.replace(CONFIG, risk=new_risk))
        charged = eng.evaluate(idea)
        assert not any(line.startswith("FUNDING_CLOCK:") for line in charged.checks_failed)
        assert any("cost-only, charged" in line and "expectancy" in line
                   for line in charged.checks_passed)

        rows = [json.loads(line) for line in shadow.read_text().splitlines() if line.strip()]
        assert len(rows) >= 2
        assert {row["applied_mode"] for row in rows} >= {"block", "cost_only"}
        for row in rows:
            assert row["block"]["would_block"] is True
            assert row["cost_only"]["would_block"] is False
            assert "expectancy_r_before" in row["cost_only"]
            assert "expectancy_r_after" in row["cost_only"]

    def test_a_1h_contract_blocks_when_the_8h_fallback_does_not(self, intervals, tmp_path):
        from bot.risk.portfolio import PortfolioTracker
        from bot.risk.risk_engine import RiskEngine
        from bot.utils.models import Direction, TradeIdea

        now = T_SETTLE + 3600 - 600
        state = str(tmp_path / "risk_state.json")
        eng = RiskEngine(PortfolioTracker(initial_balance=10_000.0), state_file=state)
        eng._last_of_signal = SimpleNamespace(symbol="PEPE/USDT", funding_rate=0.001)
        eng.set_sim_time(datetime.fromtimestamp(now, tz=timezone.utc))
        idea = TradeIdea(
            asset="PEPE/USDT", direction=Direction.LONG, entry_price=0.00001,
            stop_loss=0.000009, take_profit=0.000012, confidence=0.7,
            reasoning="clock", source="scan",
            timestamp=datetime.fromtimestamp(now, tz=timezone.utc))

        fallback = eng.evaluate(idea)
        assert any("FUNDING_CLOCK:" in line and "away" in line
                   for line in fallback.checks_passed)

        intervals.remember("PEPE/USDT", {"fundingRateInterval": "1"})
        hourly = eng.evaluate(idea)
        assert any("would pay extreme" in line for line in hourly.checks_failed)
