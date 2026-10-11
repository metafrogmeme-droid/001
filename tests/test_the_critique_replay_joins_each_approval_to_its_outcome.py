"""The critique replay joins each approved idea to the position it became.

`scripts/critique_replay.py` measures what the pre-trade critique's HALT would
have removed from the frozen benchmark: it records the critique's verdict at
every approval the risk gate makes, and joins it to the outcome. Driven here on
real objects (a `RiskEngine` over a `PortfolioTracker`, `BacktestTrade` rows):
the hook records an approval and still calls the method it wraps, an idea that
never filled is not counted as a loss, a position with no approval row is
counted rather than dropped, the patched seams are handed back even when the
run raises, and the runner's validation recorder writes nothing meanwhile.
"""
from __future__ import annotations

import asyncio
import importlib.util
import pathlib
from datetime import datetime, timedelta, timezone

import pytest

from bot.backtest import portfolio_engine, runner
from bot.backtest.models import BacktestTrade
from bot.risk.portfolio import PortfolioTracker
from bot.risk.risk_engine import RiskEngine
from bot.utils.models import Direction, TradeIdea

ROOT = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "critique_replay", ROOT / "scripts" / "critique_replay.py")
replay_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(replay_mod)

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _hand_back_the_honest_env(monkeypatch):
    """`--honest` sets three backtest switches with `os.environ.setdefault`;
    cleared here so monkeypatch hands each back however the run left it."""
    for key in ("BACKTEST_PARTIAL_TP", "BACKTEST_TIME_STOP",
                "BACKTEST_SYMBOL_LOSS_STREAK"):
        monkeypatch.delenv(key, raising=False)


def _idea(tid, *, blend=0.70, sl=95.0, tp=110.0) -> TradeIdea:
    idea = TradeIdea(id=tid, asset="SOL/USDT", direction=Direction.LONG,
                     entry_price=100.0, stop_loss=sl, take_profit=tp,
                     confidence=blend, reasoning="drive")
    idea.blended_confidence_raw = blend
    return idea


def _fill(tid, net) -> BacktestTrade:
    return BacktestTrade(
        trade_id=tid, symbol="SOL/USDT", direction="LONG", entry_price=100.0,
        exit_price=101.0, entry_time=T0, exit_time=T0 + timedelta(hours=3),
        quantity=1.0, size_usd=100.0, pnl_usd=net, pnl_pct=net, commission_usd=0.0,
        slippage_usd=0.0, net_pnl_usd=net, exit_reason="TP" if net > 0 else "SL",
        confidence=0.7, risk_verdict="APPROVED")


class TestTheRow:

    def test_a_clean_idea_passes_and_a_stacked_one_halts(self):
        clean = replay_mod.critique_row(_idea("TI-a"), 0)
        assert clean["verdict"] == "PASS" and clean["rules"] == []
        stacked = replay_mod.critique_row(_idea("TI-b", blend=0.95, sl=99.5, tp=100.6), 4)
        assert stacked["verdict"] == "HALT"
        assert sorted(stacked["rules"]) == ["high_confidence", "hot_book",
                                            "marginal_rr", "tight_stop"]
        assert stacked["open_count"] == 4

    def test_an_adjustment_that_leaves_the_floor_is_named(self):
        # 0.62 clears the 0.60 floor; a 0.5% stop costs 0.05 and it does not.
        near = replay_mod.critique_row(_idea("TI-c", blend=0.62, sl=99.5, tp=101.0), 0)
        assert near["rules"] == ["tight_stop"]
        assert near["sub_floor_after_adjustment"] is True
        clear = replay_mod.critique_row(_idea("TI-d", blend=0.80, sl=99.5, tp=101.0), 0)
        assert clear["sub_floor_after_adjustment"] is False
        none = replay_mod.critique_row(_idea("TI-e", blend=0.62), 0)
        assert none["rules"] == [] and none["sub_floor_after_adjustment"] is False


class TestTheJoin:

    ROWS = [
        {"trade_id": "TI-h", "verdict": "HALT", "rules": ["a", "b", "c", "d"],
         "sub_floor_after_adjustment": True},
        {"trade_id": "TI-p", "verdict": "PASS", "rules": [],
         "sub_floor_after_adjustment": False},
        {"trade_id": "TI-w", "verdict": "WARN", "rules": ["a"],
         "sub_floor_after_adjustment": True},
        {"trade_id": "TI-n", "verdict": "HALT", "rules": ["a", "b", "c", "d"],
         "sub_floor_after_adjustment": False},
    ]
    FILLS = [_fill("TI-h", -5.0), _fill("TI-p", 8.0), _fill("TI-w", 2.0),
             _fill("TI-w", 1.0), _fill("TI-x", 3.0)]

    def test_each_group_is_the_pooled_reading_of_its_own_positions(self):
        out = replay_mod.summarize(self.ROWS, self.FILLS)
        halted = out["halted"]
        assert halted["approved"] == 2 and halted["not_filled"] == 1
        assert halted["positions"]["trades"] == 1
        assert halted["positions"]["losses"] == 1 and halted["positions"]["wins"] == 0
        rest = out["not_halted"]
        assert rest["approved"] == 2 and rest["not_filled"] == 0
        # Two fills of one position are ONE trade (the ladder's legs).
        assert rest["positions"]["trades"] == 2 and rest["positions"]["fills"] == 3
        assert rest["positions"]["wins"] == 2

    def test_the_floor_group_is_its_own_rows(self):
        out = replay_mod.summarize(self.ROWS, self.FILLS)
        sub = out["sub_floor_after_adjustment"]
        assert sub["approved"] == 2 and sub["not_filled"] == 0
        assert sub["positions"]["trades"] == 2
        assert sub["positions"]["wins"] == 1 and sub["positions"]["losses"] == 1

    def test_what_could_not_be_joined_is_counted(self):
        out = replay_mod.summarize(self.ROWS, self.FILLS)
        assert out["positions_without_a_critique_row"] == 1
        assert out["approved_ideas"] == 4
        assert out["rule_counts"] == {"a": 3, "b": 2, "c": 2, "d": 2}
        assert out["concern_count_histogram"] == {"0": 1, "1": 1, "4": 2}

    def test_an_id_approved_twice_is_named(self):
        out = replay_mod.summarize(self.ROWS + [self.ROWS[1]], self.FILLS)
        assert out["duplicated_trade_ids"] == ["TI-p"]


class TestTheRunSeam:

    def test_the_hook_records_and_hands_every_seam_back(self, monkeypatch):
        seen = {"wrapped": []}
        fills = [_fill("TI-r", -1.0)]

        def spy_register(self, idea):
            # Stands in for the method the hook wraps: the gate's own record
            # of a pending intent must still be made.
            seen["wrapped"].append(idea.id)

        async def fake_wf(data, config, n_folds=5, **k):
            return [{"fold": 0, "_trades": fills}]

        async def fake_run_portfolio(args):
            seen["symbols"] = args.symbols
            seen["fill_mode"] = args.fill_mode
            seen["recorder_is_real"] = (
                runner._record_walk_forward_validations is real_record)
            risk = RiskEngine(PortfolioTracker(initial_balance=1000.0),
                              state_file=str(tmp_state))
            risk.register_pending_intent(_idea("TI-r", blend=0.95))
            unread = RiskEngine(PortfolioTracker(initial_balance=1000.0),
                                state_file=str(tmp_state))
            unread._portfolio = None          # a book nobody can read
            unread.register_pending_intent(_idea("TI-u"))
            await portfolio_engine.portfolio_walk_forward({}, None, n_folds=1)

        tmp_state = pathlib.Path(__import__("tempfile").mkdtemp()) / "risk.json"
        real_record = runner._record_walk_forward_validations
        monkeypatch.setattr(RiskEngine, "register_pending_intent", spy_register)
        monkeypatch.setattr(portfolio_engine, "portfolio_walk_forward", fake_wf)
        monkeypatch.setattr(runner, "_run_portfolio", fake_run_portfolio)

        out = asyncio.run(replay_mod.replay("benchmark/majors_1h", 1))

        assert seen["fill_mode"] == "next_open"
        assert "BTC/USDT:USDT" in seen["symbols"]
        assert seen["recorder_is_real"] is False
        assert seen["wrapped"] == ["TI-r", "TI-u"]
        assert out["approved_ideas"] == 1
        assert out["approvals_with_an_unread_book"] == 1
        assert out["passed"]["approved"] == 0
        assert out["warned"]["approved"] + out["halted"]["approved"] == 1
        assert (out["not_halted"]["positions"]["losses"]
                + out["halted"]["positions"]["losses"]) == 1
        assert out["dataset"] == "benchmark/majors_1h"
        assert RiskEngine.register_pending_intent is spy_register
        assert runner._record_walk_forward_validations is real_record
        assert portfolio_engine.portfolio_walk_forward is fake_wf

    def test_a_run_that_raises_still_hands_the_seams_back(self, monkeypatch):
        async def boom(args):
            raise RuntimeError("run failed")

        real = (RiskEngine.register_pending_intent,
                runner._record_walk_forward_validations,
                portfolio_engine.portfolio_walk_forward)
        monkeypatch.setattr(runner, "_run_portfolio", boom)
        with pytest.raises(RuntimeError):
            asyncio.run(replay_mod.replay("benchmark/majors_1h", 1))
        assert (RiskEngine.register_pending_intent,
                runner._record_walk_forward_validations,
                portfolio_engine.portfolio_walk_forward) == real

    def test_a_run_that_never_walked_measured_nothing(self, monkeypatch):
        async def idle(args):
            return None

        monkeypatch.setattr(runner, "_run_portfolio", idle)
        with pytest.raises(SystemExit, match="nothing was measured"):
            asyncio.run(replay_mod.replay("benchmark/majors_1h", 1))
