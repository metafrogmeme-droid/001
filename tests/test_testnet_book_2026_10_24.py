"""Testnet book from internal review 2026-10-24.

On a venue testnet, or a paper process that opts in, B1v is the primary book
and the hourly book is sized at 2x, with a 24 hour streak reset and a
per-symbol cooldown trial. Live mainnet keeps the configured values. The
trial's arm is a stable hash and it is written to the trade log.
"""
from __future__ import annotations

import inspect
import logging
import os
import tempfile
from datetime import UTC, datetime, timedelta

import pytest

from bot.config import CONFIG
from bot.core import daily_trend as dt
from bot.core import strategy_scan as ss
from bot.core import testnet_book as tb
from bot.core.leverage import order_standards, sizing_leverage
from bot.core.market_scanner import MarketScanner
from bot.risk.portfolio import PortfolioTracker
from bot.risk.risk_engine import RiskEngine
from bot.skills.skill_registry import PlaybookSkill
from bot.utils.models import Direction, TradeIdea

_HOUR = 3_600_000
_START = int(datetime(2024, 1, 1, tzinfo=UTC).timestamp() * 1000)
T0 = datetime(2026, 1, 1, 12, tzinfo=UTC)

LIVE = {
    "LIVE_TRADING_ENABLED": "true",
    "SIMULATION_MODE": "false",
    "BITGET_SANDBOX": "false",
    "HYPERLIQUID_TESTNET": "false",
    "TESTNET_BOOK_PROFILE": "1",
    "PRIMARY_BOOK": "b1v",
}
SANDBOX = {
    "BITGET_SANDBOX": "true",
    "SIMULATION_MODE": "true",
    "LIVE_TRADING_ENABLED": "false",
}
PAPER_ON = {
    "SIMULATION_MODE": "true",
    "LIVE_TRADING_ENABLED": "false",
    "TESTNET_BOOK_PROFILE": "1",
}


def _sleeve():
    return {
        "symbols": ["BTCUSDT"],
        "bar_timeframe": "1d",
        "bar_source_timeframe": "1h",
        "daily_trend": {
            "lookbacks": [20, 60, 120],
            "vol_window": 30,
            "target_vol": 0.20,
            "band": 0.10,
            "cap": 1.0,
        },
    }


def _ramp(n, start, step):
    return [start + i * step for i in range(n)]


def _hours(values):
    rows = []
    i = 0
    for v in values:
        for _ in range(24):
            rows.append([_START + i * _HOUR, float(v), float(v) + 1.0,
                         float(v) - 1.0, float(v), 1.0])
            i += 1
    return rows


def _closes_that_split_the_windows():
    """The last 30 daily returns are small and not flat, and the 30 before
    them are not small, so a 30-day vol and a 60-day vol both read and differ.
    A constant return is a zero deviation, which the sleeve leaves unread."""
    price = 100.0
    out = []
    for i in range(140):
        if 80 <= i < 110:
            price *= 1.08 if i % 2 == 0 else 0.93
        else:
            price *= 1.002 if i % 2 == 0 else 0.999
        out.append(price)
    return out


def _idea(asset):
    return TradeIdea(
        asset=asset, direction=Direction.LONG, entry_price=100.0,
        stop_loss=95.0, take_profit=110.0, confidence=0.9,
        reasoning="x", source="scan", timestamp=datetime.now(UTC))


def _engine():
    state = os.path.join(tempfile.mkdtemp(prefix="rc-tb-"), "risk_state.json")
    return RiskEngine(PortfolioTracker(initial_balance=10_000.0), state_file=state)


def _arms(env):
    found = {"cooldown_14400": None, "control": None}
    for i in range(400):
        name = f"S{i}USDT"
        arm = tb.cooldown_arm_for(name, env)
        if found[arm] is None:
            found[arm] = name
        if all(found.values()):
            return found
    raise AssertionError(found)


class TestTheProfile:
    def test_an_empty_environment_is_todays_book(self):
        assert tb.deployment_kind({}) == tb.PAPER
        assert tb.profile_active({}) is False
        assert tb.primary_book({}) == tb.HOURLY_BOOK
        assert tb.effective_sizing_leverage(5, {}) == 5
        assert tb.effective_sizing_leverage(7, {}) == 7
        assert tb.effective_streak_hours(0.0, {}) == 0.0
        assert tb.effective_streak_hours(6.0, {}) == 6.0
        decision = tb.cooldown_for("ETH/USDT", 90, {})
        assert decision.seconds == 90 and decision.arm == tb.ARM_OFF
        assert tb.b1v_parameters() == tb.B1vSpec((20, 60, 120), 60, 0.20, 0.10, 1.0, 2)

    def test_live_mainnet_keeps_every_configured_value(self):
        assert tb.deployment_kind(LIVE) == tb.LIVE
        assert tb.profile_active(LIVE) is False
        assert tb.primary_book(LIVE) == tb.HOURLY_BOOK
        assert tb.effective_sizing_leverage(5, LIVE) == 5
        assert tb.effective_streak_hours(0.0, LIVE) == 0.0
        assert tb.effective_streak_hours(3.0, LIVE) == 3.0
        decision = tb.cooldown_for("BTCUSDT", 120, LIVE)
        assert decision.seconds == 120 and decision.arm == tb.ARM_OFF

    def test_a_venue_testnet_adopts_the_book_and_zero_reverts_it(self):
        assert tb.deployment_kind(SANDBOX) == tb.TESTNET
        assert tb.profile_active(SANDBOX) is True
        assert tb.primary_book(SANDBOX) == tb.B1V_BOOK
        assert tb.effective_sizing_leverage(5, SANDBOX) == 2
        assert tb.effective_streak_hours(0.0, SANDBOX) == 24.0
        hyper = dict(SANDBOX)
        hyper.pop("BITGET_SANDBOX")
        hyper["HYPERLIQUID_TESTNET"] = "true"
        assert tb.profile_active(hyper) is True
        reverted = dict(SANDBOX)
        reverted["TESTNET_BOOK_PROFILE"] = "0"
        assert tb.profile_active(reverted) is False
        assert tb.effective_sizing_leverage(5, reverted) == 5
        assert tb.primary_book(reverted) == tb.HOURLY_BOOK

    def test_paper_opts_in_and_live_on_a_sandbox_is_still_testnet(self):
        assert tb.deployment_kind(PAPER_ON) == tb.PAPER
        assert tb.profile_active(PAPER_ON) is True
        assert tb.primary_book(PAPER_ON) == tb.B1V_BOOK
        demo = {
            "LIVE_TRADING_ENABLED": "true",
            "SIMULATION_MODE": "false",
            "BITGET_SANDBOX": "true",
        }
        assert tb.deployment_kind(demo) == tb.TESTNET
        assert tb.profile_active(demo) is True
        assert tb.effective_sizing_leverage(5, demo) == 2

    def test_primary_book_hourly_is_only_the_name(self):
        env = dict(PAPER_ON)
        env["PRIMARY_BOOK"] = "hourly"
        assert tb.primary_book(env) == tb.HOURLY_BOOK
        assert tb.effective_sizing_leverage(5, env) == 2
        assert tb.effective_streak_hours(0.0, env) == 24.0
        env["PRIMARY_BOOK"] = "not-a-book"
        assert tb.primary_book(env) == tb.B1V_BOOK


class TestTheCooldownTrial:
    def test_the_arm_is_stable_and_both_halves_exist(self):
        assert tb.cooldown_arm_for("BTC/USDT", PAPER_ON) == tb.cooldown_arm_for(
            "BTC/USDT:USDT", PAPER_ON)
        assert tb.cooldown_arm_for("BTC/USDT", PAPER_ON) == tb.cooldown_arm_for(
            "btcusdt", PAPER_ON)
        assert tb.cooldown_arm_for("BTC/USDT", PAPER_ON) == tb.cooldown_arm_for(
            "BTC/USDT", PAPER_ON)
        arms = [tb.cooldown_arm_for(f"S{i}USDT", PAPER_ON) for i in range(400)]
        treatment = arms.count(tb.ARM_TREATMENT)
        control = arms.count(tb.ARM_CONTROL)
        assert treatment + control == 400
        assert 160 <= treatment <= 240
        assert 160 <= control <= 240

    def test_an_unreadable_symbol_is_not_assigned(self):
        for symbol in ("", "   ", None, 3):
            decision = tb.cooldown_for(symbol, 120, PAPER_ON)
            assert decision.arm == tb.ARM_UNREAD
            assert decision.seconds == 120

    def test_treatment_waits_14400_and_control_keeps_the_configured_wait(self):
        arms = _arms(PAPER_ON)
        treatment = tb.cooldown_for(arms[tb.ARM_TREATMENT], 120, PAPER_ON)
        control = tb.cooldown_for(arms[tb.ARM_CONTROL], 120, PAPER_ON)
        assert treatment.arm == tb.ARM_TREATMENT and treatment.seconds == 14400
        assert control.arm == tb.ARM_CONTROL and control.seconds == 120
        other = tb.cooldown_for(arms[tb.ARM_CONTROL], 90, PAPER_ON)
        assert other.seconds == 90

    def test_an_assigned_arm_is_a_trade_log_row_and_an_unassigned_one_is_not(self):
        records = []
        handler = logging.Handler()
        handler.emit = lambda record: records.append(record)
        logger = logging.getLogger("runeclaw.trade")
        logger.addHandler(handler)
        try:
            off = tb.cooldown_for("BTCUSDT", 120, {})
            tb.log_cooldown_decision(off, where="risk_gate", env={})
            unread = tb.cooldown_for("", 120, PAPER_ON)
            tb.log_cooldown_decision(unread, where="risk_gate", env=PAPER_ON)
            assert records == []
            arms = _arms(PAPER_ON)
            decision = tb.cooldown_for(arms[tb.ARM_TREATMENT], 120, PAPER_ON)
            tb.log_cooldown_decision(decision, where="risk_gate", env=PAPER_ON)
        finally:
            logger.removeHandler(handler)
        assert len(records) == 1
        row = records[0]
        assert row.action == "cooldown_ab" and row.result == tb.ARM_TREATMENT
        assert row.data["cooldown_arm"] == tb.ARM_TREATMENT
        assert row.data["cooldown_sec"] == 14400
        assert row.data["configured_sec"] == 120
        assert row.data["primary_book"] == tb.B1V_BOOK
        assert row.data["where"] == "risk_gate"
        assert row.data["review"] == tb.REVIEW
        assert row.data["symbol"] == arms[tb.ARM_TREATMENT]


class TestTheGateUsesTheProfile:
    def _env(self, monkeypatch, env):
        for key in ("BITGET_SANDBOX", "HYPERLIQUID_TESTNET", "TESTNET_BOOK_PROFILE",
                    "PRIMARY_BOOK", "LIVE_TRADING_ENABLED", "SIMULATION_MODE"):
            monkeypatch.delenv(key, raising=False)
        for key, value in env.items():
            monkeypatch.setenv(key, value)

    def test_on_testnet_the_gate_waits_by_arm_and_logs_it(self, monkeypatch):
        self._env(monkeypatch, PAPER_ON)
        arms = _arms(PAPER_ON)
        eng = _engine()
        eng.set_sim_time(T0)
        eng._last_loss_time = T0.timestamp()
        eng.set_sim_time(T0 + timedelta(seconds=200))
        records = []
        handler = logging.Handler()
        handler.emit = lambda record: records.append(record)
        logger = logging.getLogger("runeclaw.trade")
        logger.addHandler(handler)
        try:
            treatment = eng.evaluate(_idea(arms[tb.ARM_TREATMENT]), atr=2.0)
            control = eng.evaluate(_idea(arms[tb.ARM_CONTROL]), atr=2.0)
        finally:
            logger.removeHandler(handler)
        failed = " ".join(treatment.checks_failed)
        passed = " ".join(control.checks_passed)
        assert "COOLDOWN:" in failed and f"arm={tb.ARM_TREATMENT}" in failed
        assert "COOLDOWN: cooldown period elapsed" in passed
        assert f"arm={tb.ARM_CONTROL}" in passed
        assert "arm=" not in " ".join(
            line for line in control.checks_failed if line.startswith("COOLDOWN"))
        logged = [r for r in records if getattr(r, "action", "") == "cooldown_ab"]
        assert {r.result for r in logged} == {tb.ARM_TREATMENT, tb.ARM_CONTROL}
        assert CONFIG.risk.cooldown_after_loss_seconds == 120

    def test_on_live_the_gate_sentence_is_unchanged(self, monkeypatch):
        self._env(monkeypatch, LIVE)
        eng = _engine()
        eng.set_sim_time(T0)
        eng._last_loss_time = T0.timestamp()
        eng.set_sim_time(T0 + timedelta(seconds=200))
        check = eng.evaluate(_idea("BTC/USDT"), atr=2.0)
        blob = " ".join(check.checks_passed + check.checks_failed)
        assert "COOLDOWN: cooldown period elapsed" in blob
        assert "arm=" not in blob
        assert tb.effective_sizing_leverage(CONFIG.exchange.sizing_leverage) == 5
        assert tb.effective_streak_hours(CONFIG.risk.streak_breaker_autoreset_hours) == 0.0

    def test_sizing_and_the_streak_follow_the_profile(self, monkeypatch):
        self._env(monkeypatch, SANDBOX)
        assert CONFIG.exchange.sizing_leverage == 5
        assert sizing_leverage(CONFIG.exchange) == 2
        assert order_standards(CONFIG.exchange, None).sized == 2
        eng = _engine()
        eng.set_sim_time(T0)
        for _ in range(CONFIG.risk.max_consecutive_losses):
            eng.record_trade_result(-10.0)
        assert eng.circuit_breaker_active
        eng.set_sim_time(T0 + timedelta(hours=23))
        eng.evaluate(_idea("BTC/USDT"), atr=2.0)
        assert eng.circuit_breaker_active
        eng.set_sim_time(T0 + timedelta(hours=24, seconds=1))
        eng.evaluate(_idea("BTC/USDT"), atr=2.0)
        assert not eng.circuit_breaker_active
        assert CONFIG.risk.streak_breaker_autoreset_hours == 0.0

    def test_a_live_streak_does_not_clear_itself(self, monkeypatch):
        self._env(monkeypatch, LIVE)
        eng = _engine()
        eng.set_sim_time(T0)
        for _ in range(CONFIG.risk.max_consecutive_losses):
            eng.record_trade_result(-10.0)
        eng.set_sim_time(T0 + timedelta(days=3))
        eng.evaluate(_idea("BTC/USDT"), atr=2.0)
        assert eng.circuit_breaker_active
        assert sizing_leverage(CONFIG.exchange) == 5
        assert order_standards(CONFIG.exchange, None) == (5, 10)

    def test_runtime_live_mainnet_ignores_a_paper_opt_in(self, monkeypatch):
        self._env(monkeypatch, PAPER_ON)
        monkeypatch.setattr(type(CONFIG), "is_live", lambda self: True)
        assert tb.profile_active() is False
        assert sizing_leverage(CONFIG.exchange) == 5
        assert tb.primary_book() == tb.HOURLY_BOOK


class TestTheRecordedBook:
    def test_b1v_reads_a_60_day_vol_and_does_not_rewrite_the_preset(self):
        cfg = _sleeve()
        assert tb.overlay_daily_trend(cfg, LIVE) is cfg
        over = tb.overlay_daily_trend(cfg, PAPER_ON)
        assert over is not cfg
        assert cfg["daily_trend"]["vol_window"] == 30
        params = dt.params_from_preset(over)
        assert params is not None
        assert params.vol_window == 60
        assert params.lookbacks == (20, 60, 120)
        assert params.target_vol == 0.20 and params.band == 0.10 and params.cap == 1.0
        closes = _closes_that_split_the_windows()
        rows = {"BTC": _hours(closes)}
        at_30 = dt.read_book(rows, cfg)
        at_60 = dt.read_book(rows, over)
        vol_30 = at_30["reads"]["BTC"].vol
        vol_60 = at_60["reads"]["BTC"].vol
        assert vol_30 is not None and vol_60 is not None
        assert vol_30 != pytest.approx(vol_60)
        assert at_60["opens"] is False

    def test_the_board_names_b1v_only_on_the_profile(self, monkeypatch):
        closes = _closes_that_split_the_windows()
        rows = {"BTC": _hours(closes)}
        cfg = _sleeve()
        for key in ("BITGET_SANDBOX", "HYPERLIQUID_TESTNET", "LIVE_TRADING_ENABLED",
                    "PRIMARY_BOOK", "TESTNET_BOOK_PROFILE"):
            monkeypatch.delenv(key, raising=False)
        monkeypatch.setenv("SIMULATION_MODE", "true")
        plain = ss.board_daily_trend(cfg, rows)
        assert "book" not in plain and "vol_window" not in plain
        monkeypatch.setenv("TESTNET_BOOK_PROFILE", "1")
        board = ss.board_daily_trend(cfg, rows)
        assert board["book"] == "b1v"
        assert board["vol_window"] == 60 and board["leverage_cap"] == 2
        at_60 = dt.read_book(rows, tb.overlay_daily_trend(cfg, PAPER_ON))
        at_30 = dt.read_book(rows, cfg)
        by = {r["symbol"]: r for r in board["rows"]}
        assert at_60["reads"]["BTC"].vol is not None
        assert at_30["reads"]["BTC"].vol is not None
        shown = ss._num(at_60["reads"]["BTC"].vol * 100, 3)
        other = ss._num(at_30["reads"]["BTC"].vol * 100, 3)
        assert by["BTC"]["vol_pct"] == shown
        assert shown != other
        assert cfg["daily_trend"]["vol_window"] == 30

    def test_the_daily_record_carries_the_book_when_the_board_names_one(self, tmp_path):
        path = tmp_path / "book.jsonl"
        when = datetime(2026, 10, 24, tzinfo=UTC)
        named = {
            "rows": [{"symbol": "BTC", "score": 1.0, "weight_pct": 12.5}],
            "bar_close": "24 Oct 00:00 UTC",
            "book": "b1v",
            "vol_window": 60,
            "leverage_cap": 2,
            "unread": [],
        }
        assert dt.record_book(named, when=when, path=path) == "recorded"
        import json
        row = json.loads(path.read_text().splitlines()[0])
        assert row["book"] == "b1v" and row["vol_window"] == 60 and row["leverage_cap"] == 2
        bare = tmp_path / "bare.jsonl"
        unnamed = {
            "rows": [{"symbol": "BTC", "score": 1.0, "weight_pct": 12.5}],
            "bar_close": "24 Oct 00:00 UTC",
            "unread": [],
        }
        assert dt.record_book(unnamed, when=when, path=bare) == "recorded"
        bare_row = json.loads(bare.read_text().splitlines()[0])
        assert "book" not in bare_row and "vol_window" not in bare_row


class TestTheWiring:
    def test_the_order_path_asks_the_profile(self):
        assert "effective_sizing_leverage" in inspect.getsource(sizing_leverage)
        gate = inspect.getsource(RiskEngine._evaluate_locked)
        assert "_evaluate_locked" in inspect.getsource(RiskEngine.evaluate)
        assert "effective_streak_hours" in gate
        assert "cooldown_for" in gate
        assert "log_cooldown_decision" in gate
        from bot.core.engine import RuneClawEngine
        assert "cooldown_for" in inspect.getsource(RuneClawEngine._simulate_paper_fill)
        assert "note_primary_book" in inspect.getsource(MarketScanner.scan)
        assert "cooldown_card_line" in inspect.getsource(PlaybookSkill.execute)
        assert "CONFIG.risk.cooldown_after_loss_seconds" in inspect.getsource(
            RuneClawEngine._check_paper_positions)
