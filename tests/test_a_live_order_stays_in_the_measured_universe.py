"""A live order stays in the markets a card measured.

Live's parity card of 17 Oct 2026: 226 of 249 strategy exits were on markets
no frozen benchmark holds, and the TradFi legs among them ran at PF 0.03 to
0.22 over 77 exits. The risk gate's LIVE_UNIVERSE check, live mode only,
refuses a live order outside the benchmark on record's universe, read from
its manifest rather than typed. The set nobody could read refuses by name.
`LIVE_UNIVERSE` replaces the set on purpose; `LIVE_UNIVERSE_GATE_ENABLED`
(on) is the switch. Paper and backtest evaluations never see the check.
"""

from __future__ import annotations

import json
import os

import pytest

import bot.config as bot_config
import bot.risk.live_universe as lu
from bot.risk.portfolio import PortfolioTracker
from bot.risk.risk_engine import RiskEngine
from bot.utils.models import Direction, TradeIdea

MEASURED = {"ADA", "AVAX", "BNB", "BTC", "DOGE", "ETH", "LINK", "LTC", "SOL", "XRP"}


class _Session:
    size_multiplier = 1.0


@pytest.fixture(autouse=True)
def _one_session(monkeypatch):
    monkeypatch.setattr("bot.core.session_aware.get_current_session", lambda now=None: _Session())


@pytest.fixture(autouse=True)
def _gate_on():
    """The harness holds the gate off for every other suite; this one measures it."""
    prev = bot_config.CONFIG.risk.live_universe_gate_enabled
    object.__setattr__(bot_config.CONFIG.risk, "live_universe_gate_enabled", True)
    try:
        yield
    finally:
        object.__setattr__(bot_config.CONFIG.risk, "live_universe_gate_enabled", prev)


@pytest.fixture
def risk_flag():
    """Set a RiskConfig field on the frozen config, restored after."""
    prev: dict = {}

    def _set(name, value):
        prev.setdefault(name, getattr(bot_config.CONFIG.risk, name))
        object.__setattr__(bot_config.CONFIG.risk, name, value)

    try:
        yield _set
    finally:
        for name, value in prev.items():
            object.__setattr__(bot_config.CONFIG.risk, name, value)


def _idea(asset):
    return TradeIdea(asset=asset, direction=Direction.LONG, entry_price=1.0,
                     stop_loss=0.98, take_profit=1.06, confidence=0.8, reasoning="x")


def _engine(tmp_path):
    return RiskEngine(PortfolioTracker(), state_file=os.path.join(str(tmp_path), "risk.json"))


def _live(tmp_path, asset):
    return _engine(tmp_path).evaluate(_idea(asset), atr=0.01, live_equity=1000.0,
                                      max_position_usd=100.0, live_open_count=0,
                                      live_mode=True, live_book=())


def _line(check):
    lines = [x for x in check.checks_passed + check.checks_failed if x.startswith("LIVE_UNIVERSE")]
    assert len(lines) <= 1, lines
    return lines[0] if lines else None


class TestTheSet:
    def test_the_measured_set_is_the_benchmark_manifest_s_universe(self):
        uni = lu.measured_universe()
        assert uni.readable and uni.source == "benchmark/majors_1h"
        manifest = json.load(open(os.path.join("benchmark", "majors_1h", "manifest.json")))
        assert uni.markets == {s.split("/")[0] for s in manifest["symbols"]}
        assert uni.markets == MEASURED

    def test_the_operator_s_list_replaces_it(self, risk_flag):
        risk_flag("live_universe", "btc, eth/usdt:usdt, PEPEUSDT ,,")
        uni = lu.measured_universe()
        assert uni.markets == {"BTC", "ETH", "PEPE"} and uni.source == "LIVE_UNIVERSE"

    def test_a_manifest_that_cannot_be_read_is_a_set_nobody_read(self, monkeypatch, tmp_path):
        monkeypatch.setattr("bot.risk.live_universe.benchmark_root", lambda: tmp_path)
        uni = lu.measured_universe()
        assert not uni.readable and uni.error == "FileNotFoundError"
        d = tmp_path / "majors_1h"
        d.mkdir()
        (d / "manifest.json").write_text("{not json")
        assert lu.measured_universe().error == "JSONDecodeError"
        (d / "manifest.json").write_text(json.dumps({"symbols": []}))
        assert lu.measured_universe().error == "no symbols in the manifest"


class TestTheGate:
    def test_a_measured_market_passes_and_says_so(self, tmp_path):
        check = _live(tmp_path, "BTC/USDT:USDT")
        assert _line(check) == "LIVE_UNIVERSE: BTC is in the measured set (10 markets, benchmark/majors_1h)"
        assert _line(check) in check.checks_passed

    @pytest.mark.parametrize("asset", ["PEPE/USDT:USDT", "AAPLUSDT", "XAU/USDT:USDT", "M/USDT:USDT"])
    def test_an_unmeasured_market_is_refused_by_name(self, tmp_path, asset):
        check = _live(tmp_path, asset)
        assert check.verdict.value == "REJECTED"
        line = _line(check)
        assert line in check.checks_failed
        assert "is not in the measured set (10 markets, benchmark/majors_1h)" in line
        assert "LIVE_UNIVERSE=BTC,ETH,... widens it" in line

    def test_paper_and_backtest_evaluations_never_see_the_check(self, tmp_path):
        check = _engine(tmp_path).evaluate(_idea("PEPE/USDT:USDT"), atr=0.01)
        assert _line(check) is None

    def test_the_operator_s_list_is_what_live_is_held_to(self, tmp_path, risk_flag):
        risk_flag("live_universe", "PEPE")
        assert _line(_live(tmp_path, "PEPE/USDT:USDT")) == \
            "LIVE_UNIVERSE: PEPE is in the measured set (1 markets, LIVE_UNIVERSE)"
        refused = _live(tmp_path, "BTC/USDT:USDT")
        assert refused.verdict.value == "REJECTED" and "LIVE_UNIVERSE" in _line(refused)

    def test_a_set_nobody_could_read_refuses_a_live_order(self, tmp_path, monkeypatch):
        monkeypatch.setattr("bot.risk.live_universe.benchmark_root", lambda: tmp_path / "none")
        check = _live(tmp_path, "BTC/USDT:USDT")
        assert check.verdict.value == "REJECTED"
        assert _line(check) == ("LIVE_UNIVERSE: the measured set could not be read "
                                "(FileNotFoundError); a live order is refused until it can.")

    def test_the_switch_off_runs_no_check(self, tmp_path, risk_flag):
        risk_flag("live_universe_gate_enabled", False)
        assert _line(_live(tmp_path, "PEPE/USDT:USDT")) is None

    def test_the_flag_ships_on_and_is_documented(self):
        fields = type(bot_config.CONFIG.risk).__dataclass_fields__
        assert fields["live_universe_gate_enabled"].default is True
        env_example = open(".env.example", encoding="utf-8").read()
        assert "LIVE_UNIVERSE_GATE_ENABLED" in env_example
