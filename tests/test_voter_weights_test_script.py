"""scripts/voter_weights_test.py and the entry votes it fits on."""
from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from bot.backtest.engine import entry_votes
from bot.backtest.positions import positions
from bot.backtest.runner import trade_rows

ROOT = Path(__file__).resolve().parents[1]


def _script():
    spec = importlib.util.spec_from_file_location(
        "voter_weights_test", ROOT / "scripts" / "voter_weights_test.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_entry_votes_reads_the_breakdown_and_says_when_there_is_none():
    idea = SimpleNamespace(_confluence_votes=[("rsi", 1.0, 0.8), ("macd", -0.5, 1.2)])
    assert entry_votes(idea) == [["rsi", 1.0, 0.8], ["macd", -0.5, 1.2]]
    assert entry_votes(SimpleNamespace()) is None
    assert entry_votes(SimpleNamespace(_confluence_votes=[])) is None
    # A malformed row is dropped, not turned into a zero vote.
    assert entry_votes(SimpleNamespace(_confluence_votes=[("rsi", "x", 1.0)])) is None


def _trade(tid, votes, net=5.0):
    from bot.backtest.models import BacktestTrade
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return BacktestTrade(
        trade_id=tid, symbol="BTC/USDT", direction="LONG", entry_price=1.0,
        exit_price=1.1, entry_time=t0, exit_time=t0, quantity=1.0, size_usd=100.0,
        pnl_usd=net, pnl_pct=net, commission_usd=0.0, slippage_usd=0.0,
        net_pnl_usd=net, exit_reason="TP", confidence=0.7, risk_verdict="APPROVED",
        voter_votes=votes)


def test_votes_reach_the_trade_rows_through_a_position():
    rows = trade_rows([{"_trades": positions([_trade("a", [["rsi", 1.0, 0.8]]),
                                              _trade("b", None)])}])
    by_votes = sorted((r["voter_votes"] or [], r["net_pnl_pct"]) for r in rows)
    assert by_votes == [([], 5.0), ([["rsi", 1.0, 0.8]], 5.0)]


def test_samples_keep_the_window_and_count_what_they_leave_out():
    m = _script()
    rows = [
        {"exit_time": "2026-01-02T00:00:00+00:00", "direction": "LONG",
         "voter_votes": [["rsi", 1.0, 1.0]], "net_pnl_pct": 2.0},
        {"exit_time": "2026-01-01T00:00:00+00:00", "direction": "SHORT",
         "voter_votes": [["rsi", -1.0, 1.0]], "net_pnl_pct": -1.0},
        {"exit_time": "2026-01-03T00:00:00+00:00", "direction": "LONG",
         "voter_votes": None, "net_pnl_pct": 1.0},
        {"exit_time": "2026-01-03T00:00:00+00:00", "direction": "LONG",
         "voter_votes": [["rsi", 1.0, 1.0]], "net_pnl_pct": None},
        {"exit_time": "2026-09-01T00:00:00+00:00", "direction": "LONG",
         "voter_votes": [["rsi", 1.0, 1.0]], "net_pnl_pct": 3.0},
    ]
    kept, skipped = m.samples(rows, "2026-06-01T00:00:00+00:00")
    # Exit order, and a loss is a loss: unreadable returns are left out, not
    # scored as break-even.
    assert [(d, won) for _, d, won in kept] == [("SHORT", False), ("LONG", True)]
    assert skipped == {"after_window": 1, "no_votes": 1, "no_return": 1}


def test_holdout_runs_refuse_before_the_weights_are_fixed(tmp_path, monkeypatch):
    m = _script()
    monkeypatch.setattr("sys.argv", ["x", "test-runs", "--out", str(tmp_path)])
    assert m.main() == 2
    assert not any(tmp_path.glob("*__learned.json"))
