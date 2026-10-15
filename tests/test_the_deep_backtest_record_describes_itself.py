"""`run_deep_backtest.py` writes a record that says what it measured, over what, and when.

Two audit findings, both on `backtest_deep_results.json`, the record the Live Market Validation
page cites:

- B4-09: five of the summary's means divided by every run, so each run that never traded added a
  0% return, a 0.0 win rate and no drawdown to the average. The published win rate was 13 points
  off the win rate of the runs that traded. The means are now over the runs that traded, and the
  idle runs are counted beside them.
- B4-10: the file named 500 runs after real tickers at real prices and said nowhere that every
  bar was generated. Each run now carries `data_source: "synthetic"` and the parameters its bars
  were drawn with, and the file carries the commit and time it was produced at.
"""
from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

from bot.backtest.data_loader import DataLoader

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def script():
    spec = importlib.util.spec_from_file_location("run_deep_backtest", ROOT / "run_deep_backtest.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run(trades, ret, wr, dd=1.0, pf=1.5):
    return {"total_trades": trades, "total_return_pct": ret, "win_rate": wr, "max_drawdown_pct": dd,
            "sharpe_ratio": 0.5, "sortino_ratio": 0.7, "profit_factor": pf,
            "total_commission": 1.0, "total_slippage": 0.5}


def test_means_are_over_the_runs_that_traded_and_idle_runs_are_counted(script):
    traded = [_run(10, 2.0, 0.6), _run(8, -1.0, 0.4)]
    idle = [_run(0, 0.0, 0.0, dd=0.0, pf=0.0)] * 3
    s = script.summarise(traded + idle)
    assert (s["runs_that_traded"], s["runs_idle"]) == (2, 3)
    means = s["means_over_traded_runs"]
    assert means["return_pct"] == 0.5, "idle runs pulled the mean return toward zero"
    assert means["win_rate"] == 0.5
    assert means["max_drawdown_pct"] == 1.0
    # The other arm: the same traded runs alone give the same means.
    assert script.summarise(traded)["means_over_traded_runs"] == means
    assert script.summarise(traded)["runs_idle"] == 0


def test_the_win_rate_keeps_four_places(script):
    s = script.summarise([_run(3, 1.0, 0.6513), _run(3, 1.0, 0.6513)])
    assert s["means_over_traded_runs"]["win_rate"] == 0.6513


def test_no_run_traded_is_no_mean_not_a_zero(script):
    s = script.summarise([_run(0, 0.0, 0.0, dd=0.0, pf=0.0)] * 4)
    assert set(s["means_over_traded_runs"].values()) == {None}
    assert s["runs_that_traded"] == 0 and s["runs_idle"] == 4


@pytest.mark.parametrize("regime_index", range(5))
def test_the_recorded_generator_parameters_are_the_ones_the_bars_were_drawn_with(script, regime_index):
    sym, regime, seed = script.SYMBOLS[4], script.REGIMES[regime_index], script.SEEDS[1]
    params = script.generator_params(sym, regime, seed)
    bars = script.generate_bars_for_regime(sym, regime, seed)
    assert len(bars) == params["bars"] == script.BARS
    phases = params.get("phases") or [{"bars": params["bars"], "trend": params["trend"], "seed": params["seed"]}]
    first = DataLoader.generate_synthetic(bars=phases[0]["bars"], start_price=params["start_price"],
                                          volatility=params["volatility"], trend=phases[0]["trend"],
                                          seed=phases[0]["seed"])
    assert [b.close for b in bars[:len(first)]] == [b.close for b in first]
    if len(phases) == 2:
        second = DataLoader.generate_synthetic(bars=phases[1]["bars"], start_price=first[-1].close,
                                               volatility=params["volatility"], trend=phases[1]["trend"],
                                               seed=phases[1]["seed"])
        assert [b.close for b in bars[len(first):]] == [b.close for b in second]


def test_provenance_names_the_commit_or_says_it_could_not(script, monkeypatch):
    p = script.provenance()
    assert p["command"] == "python3 run_deep_backtest.py"
    assert p["commit"] and len(p["commit"]) == 40
    assert p["tree_clean"] in (True, False)

    def no_git(*a, **k):
        raise OSError("git is not installed")
    monkeypatch.setattr(subprocess, "run", no_git)
    p = script.provenance()
    assert p["commit"] is None and p["tree_clean"] is None, "an unknown commit is None, never a guess"
