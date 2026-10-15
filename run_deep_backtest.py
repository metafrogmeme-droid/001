"""
RUNECLAW Deep Backtest -- Top 20 Crypto Symbols
Runs comprehensive backtests across multiple market conditions.
"""
import asyncio
import json
import sys
import os
import subprocess
import time
from datetime import datetime, timedelta, timezone

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from bot.backtest.engine import BacktestEngine
from bot.backtest.models import BacktestConfig, BacktestBar
from bot.backtest.data_loader import DataLoader
from bot.backtest.metrics import profit_factor_summary, share_profitable


# Top 20 symbols with realistic starting prices and volatility profiles
SYMBOLS = [
    # ── Original 10 ──
    {"symbol": "BTC/USDT",  "price": 108000.0, "vol": 0.012, "name": "Bitcoin"},
    {"symbol": "ETH/USDT",  "price": 2550.0,   "vol": 0.018, "name": "Ethereum"},
    {"symbol": "SOL/USDT",  "price": 180.0,     "vol": 0.025, "name": "Solana"},
    {"symbol": "BNB/USDT",  "price": 640.0,     "vol": 0.015, "name": "BNB"},
    {"symbol": "XRP/USDT",  "price": 2.35,      "vol": 0.022, "name": "XRP"},
    {"symbol": "ADA/USDT",  "price": 0.78,      "vol": 0.025, "name": "Cardano"},
    {"symbol": "DOGE/USDT", "price": 0.23,      "vol": 0.030, "name": "Dogecoin"},
    {"symbol": "AVAX/USDT", "price": 24.0,      "vol": 0.028, "name": "Avalanche"},
    {"symbol": "LINK/USDT", "price": 16.5,      "vol": 0.022, "name": "Chainlink"},
    {"symbol": "SUI/USDT",  "price": 3.80,      "vol": 0.030, "name": "Sui"},
    # ── New 10 (diverse sectors, price ranges, volatility) ──
    {"symbol": "DOT/USDT",  "price": 7.00,      "vol": 0.025, "name": "Polkadot"},
    {"symbol": "TON/USDT",  "price": 3.50,      "vol": 0.022, "name": "Toncoin"},
    {"symbol": "NEAR/USDT", "price": 5.00,      "vol": 0.028, "name": "NEAR"},
    {"symbol": "APT/USDT",  "price": 8.00,      "vol": 0.028, "name": "Aptos"},
    {"symbol": "UNI/USDT",  "price": 7.00,      "vol": 0.025, "name": "Uniswap"},
    {"symbol": "LTC/USDT",  "price": 90.0,      "vol": 0.015, "name": "Litecoin"},
    {"symbol": "PEPE/USDT", "price": 0.000013,  "vol": 0.040, "name": "Pepe"},
    {"symbol": "TRX/USDT",  "price": 0.27,      "vol": 0.015, "name": "TRON"},
    {"symbol": "ATOM/USDT", "price": 8.00,      "vol": 0.022, "name": "Cosmos"},
    {"symbol": "FIL/USDT",  "price": 3.00,      "vol": 0.030, "name": "Filecoin"},
]

# Market regimes to test
REGIMES = [
    {"trend": 0.0003,  "label": "Bull Trend",      "vol_mult": 1.0},
    {"trend": -0.0003, "label": "Bear Trend",       "vol_mult": 1.0},
    {"trend": 0.0,     "label": "Range/Chop",       "vol_mult": 1.0},
    {"trend": 0.0,     "label": "High Volatility",  "vol_mult": 1.8},
    {"trend": None,    "label": "Crash Recovery",    "vol_mult": 1.0},  # special handling
]

BARS = 1500        # ~62 days of 1H data per run
SEEDS = [42, 137, 256, 512, 1024]  # 5 seeds per regime for statistical robustness

# Crash Recovery: a crash for the first half of the bars, then a recovery.
CRASH_TREND = -0.0006
RECOVERY_TREND = 0.0004
RECOVERY_SEED_OFFSET = 10000  # the recovery half draws from its own seed

COMMAND = "python3 run_deep_backtest.py"

OUTPUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backtest_deep_results.json")


def generate_bars_for_regime(sym_info, regime, seed):
    """Generate synthetic bars, with special handling for Crash Recovery."""
    vol = sym_info["vol"] * regime["vol_mult"]

    if regime["label"] == "Crash Recovery":
        # First half: crash (CRASH_TREND), second half: recovery (RECOVERY_TREND)
        half = BARS // 2
        second_half_bars = BARS - half

        # Generate first half (crash)
        bars_first = DataLoader.generate_synthetic(
            bars=half,
            start_price=sym_info["price"],
            volatility=vol,
            trend=CRASH_TREND,
            seed=seed,
        )

        # Second half starts where first half ended
        last_close = bars_first[-1].close
        last_ts = bars_first[-1].timestamp

        bars_second_raw = DataLoader.generate_synthetic(
            bars=second_half_bars,
            start_price=last_close,
            volatility=vol,
            trend=RECOVERY_TREND,
            seed=seed + RECOVERY_SEED_OFFSET,  # different seed for second half
        )

        # Adjust timestamps on second half to continue from first half
        bars_second = []
        for j, bar in enumerate(bars_second_raw):
            bars_second.append(BacktestBar(
                timestamp=last_ts + timedelta(hours=j + 1),
                open=bar.open,
                high=bar.high,
                low=bar.low,
                close=bar.close,
                volume=bar.volume,
            ))

        return bars_first + bars_second
    else:
        return DataLoader.generate_synthetic(
            bars=BARS,
            start_price=sym_info["price"],
            volatility=vol,
            trend=regime["trend"],
            seed=seed,
        )


def generator_params(sym_info, regime, seed):
    """The parameters `generate_bars_for_regime` drew this run's bars with."""
    vol = sym_info["vol"] * regime["vol_mult"]
    out = {"generator": "DataLoader.generate_synthetic (GBM + GARCH)",
           "start_price": sym_info["price"], "volatility": vol, "bars": BARS}
    if regime["label"] == "Crash Recovery":
        half = BARS // 2
        out["phases"] = [
            {"bars": half, "trend": CRASH_TREND, "seed": seed},
            {"bars": BARS - half, "trend": RECOVERY_TREND, "seed": seed + RECOVERY_SEED_OFFSET},
        ]
    else:
        out["trend"] = regime["trend"]
        out["seed"] = seed
    return out


def _round(x, places):
    return None if x is None else round(x, places)


def summarise(valid):
    """The global summary of the runs that completed.

    MEANS ARE OVER THE RUNS THAT TRADED (audit B4-09). A run that opened no
    position returns 0%, a 0.0 win rate and no drawdown; folded into a mean
    those are non-measurements that drag every average toward zero, and the
    2026-10 audit found the published win rate 13 points off the win rate of
    the runs that traded. Idle runs are counted beside the means instead, as
    `profit_factor_summary` and `share_profitable` already do.
    """
    traded = [r for r in valid if r["total_trades"] > 0]

    def mean(key):
        return sum(r[key] for r in traded) / len(traded) if traded else None

    # THE LINE THAT REPORTED 19.17 WHILE THE MEDIAN RUN SCORED 0.45.
    pf = profit_factor_summary((r["profit_factor"], r["total_trades"]) for r in valid)
    shr = share_profitable((r["total_return_pct"], r["total_trades"]) for r in valid)
    return {
        "total_trades": sum(r["total_trades"] for r in valid),
        # Both denominators: an idle run returns 0% and so is "not
        # profitable", but it declined to trade rather than losing.
        # Publishing one alone would be choosing the framing.
        "runs_that_traded": shr.traded,
        "runs_idle": shr.idle,
        "means_over_traded_runs": {
            "return_pct": _round(mean("total_return_pct"), 2),
            "max_drawdown_pct": _round(mean("max_drawdown_pct"), 2),
            "win_rate": _round(mean("win_rate"), 4),
            "sharpe": _round(mean("sharpe_ratio"), 2),
            "sortino": _round(mean("sortino_ratio"), 2),
        },
        "best_return_pct": round(max(r["total_return_pct"] for r in valid), 2),
        "worst_return_pct": round(min(r["total_return_pct"] for r in valid), 2),
        "worst_drawdown_pct": round(max(r["max_drawdown_pct"] for r in valid), 2),
        # Was `avg_profit_factor` — a mean poisoned by the undefined
        # sentinel. Renamed as well as fixed: a consumer reading the
        # old key would get a different number under the same name,
        # and silently changing what a key means is worse than
        # breaking it.
        "median_profit_factor": _round(pf.median, 2),
        "mean_profit_factor_defined": _round(pf.mean, 2),
        "runs_profit_factor_undefined": pf.n_undefined,
        "runs_no_trades": pf.n_no_trades,
        "profitable_runs": shr.profitable,
        "profitable_runs_total": shr.total,
        "crashed_runs": sum(1 for r in valid if r["max_drawdown_pct"] > 20),
        "total_commission": round(sum(r["total_commission"] for r in valid), 2),
        "total_slippage": round(sum(r["total_slippage"] for r in valid), 2),
    }


def provenance():
    """Where and when this record was produced. An answer git could not give is None, not a guess."""
    here = os.path.dirname(os.path.abspath(__file__))

    def git(*args):
        try:
            return subprocess.run(["git", *args], cwd=here, capture_output=True, text=True,
                                  timeout=10, check=True).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return None

    status = git("status", "--porcelain", "--", "run_deep_backtest.py", "bot")
    return {
        "command": COMMAND,
        "commit": git("rev-parse", "HEAD"),
        # The engine and this script exactly as committed, or not: a run on an
        # edited tree is not the commit's result.
        "tree_clean": None if status is None else status == "",
        "run_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


async def run_single_backtest(sym_info, regime, seed):
    """Run a single backtest and return summary dict."""
    config = BacktestConfig(
        symbol=sym_info["symbol"],
        timeframe="1h",
        initial_balance=10000.0,
        commission_pct=0.1,
        slippage_pct=0.05,
        use_llm=False,
        lookback_size=100,
        scan_interval=4,
    )
    engine = BacktestEngine(config)
    bars = generate_bars_for_regime(sym_info, regime, seed)

    try:
        result = await engine.run(bars)
    except Exception as e:
        # Catch validation errors (e.g. DOGE/low-price rounding issues) and surface them
        raise RuntimeError(f"engine.run failed: {e}") from e

    avg_pnl = result.net_pnl / result.total_trades if result.total_trades > 0 else 0.0

    return {
        "symbol": sym_info["symbol"],
        "name": sym_info["name"],
        "regime": regime["label"],
        "seed": seed,
        "total_trades": result.total_trades,
        "win_rate": result.win_rate,
        "total_return_pct": result.total_return_pct,
        "max_drawdown_pct": result.max_drawdown_pct,
        "sharpe_ratio": result.sharpe_ratio,
        "sortino_ratio": result.sortino_ratio,
        "profit_factor": result.profit_factor,
        "calmar_ratio": result.calmar_ratio,
        "risk_reward_avg": result.risk_reward_avg,
        "avg_trade_pnl": round(avg_pnl, 2),
        "largest_win_usd": result.largest_win_usd,
        "largest_loss_usd": result.largest_loss_usd,
        "avg_win_usd": result.avg_win_usd,
        "avg_loss_usd": result.avg_loss_usd,
        "winning_trades": result.winning_trades,
        "losing_trades": result.losing_trades,
        "max_consecutive_losses": result.max_consecutive_losses,
        "total_commission": result.total_commission,
        "total_slippage": result.total_slippage,
        "net_pnl": result.net_pnl,
        "final_equity": round(result.final_equity, 2),
        "avg_trade_duration_hours": result.avg_trade_duration_hours,
        "total_signals_generated": result.total_signals_generated,
        "total_ideas_generated": result.total_ideas_generated,
        "total_ideas_rejected_risk": result.total_ideas_rejected_risk,
        "total_ideas_rejected_confidence": result.total_ideas_rejected_confidence,
        "bars_processed": BARS,
        # Every bar of this run was generated, not read from a market; the
        # ticker and starting price only set its scale (audit B4-10).
        "data_source": "synthetic",
        "generator": generator_params(sym_info, regime, seed),
    }


async def main():
    total_runs = len(SYMBOLS) * len(REGIMES) * len(SEEDS)
    print(f"RUNECLAW Deep Backtest: {len(SYMBOLS)} symbols x {len(REGIMES)} regimes x {len(SEEDS)} seeds = {total_runs} runs")
    print(f"Bars per run: {BARS} (1H candles, ~{BARS // 24} days)")
    print("=" * 100)

    all_results = []
    start = time.time()
    completed = 0
    error_count = 0

    for sym in SYMBOLS:
        for regime in REGIMES:
            for seed in SEEDS:
                try:
                    r = await run_single_backtest(sym, regime, seed)
                    all_results.append(r)
                    completed += 1
                    pct = completed / total_runs * 100
                    print(f"  [{completed:3d}/{total_runs}] {pct:5.1f}% | {sym['symbol']:12s} | {regime['label']:16s} | seed={seed:4d} | "
                          f"trades={r['total_trades']:3d} | ret={r['total_return_pct']:+7.2f}% | DD={r['max_drawdown_pct']:5.2f}% | "
                          f"WR={r['win_rate'] * 100:5.1f}% | sharpe={r['sharpe_ratio']:+6.2f}")
                except Exception as e:
                    completed += 1
                    error_count += 1
                    print(f"  [{completed:3d}/{total_runs}] ERROR: {sym['symbol']} {regime['label']} seed={seed}: {e}")
                    all_results.append({
                        "symbol": sym["symbol"], "name": sym["name"],
                        "regime": regime["label"], "seed": seed,
                        "error": str(e),
                    })

    elapsed = time.time() - start
    print("=" * 100)
    print(f"Completed {completed} runs in {elapsed:.1f}s ({elapsed/completed:.2f}s/run)")

    # ── Aggregate Statistics ──
    valid = [r for r in all_results if "error" not in r]
    errors = [r for r in all_results if "error" in r]

    if not valid:
        print("No valid results!")
        # Still save the error results
        with open(OUTPUT_PATH, "w") as f:
            json.dump({"meta": {"total_runs": total_runs, "errors": len(errors)}, "results": all_results}, f, indent=2, default=str)
        print(f"\nResults saved to {OUTPUT_PATH}")
        return

    # Per-symbol aggregates
    print("\n" + "=" * 100)
    print("PER-SYMBOL AGGREGATE (across all regimes and seeds)")
    print("=" * 100)
    print(f"{'Symbol':12s} {'Runs':>5s} {'Trades':>7s} {'Avg Ret%':>9s} {'Avg DD%':>8s} {'Avg WR%':>8s} {'Avg Sharpe':>11s} {'Med PF':>8s} {'Worst DD%':>10s} {'Avg Sortino':>12s}")
    print("-" * 100)

    sym_agg = {}
    for r in valid:
        s = r["symbol"]
        if s not in sym_agg:
            sym_agg[s] = []
        sym_agg[s].append(r)

    for s in [x["symbol"] for x in SYMBOLS]:
        if s not in sym_agg:
            continue
        runs = sym_agg[s]
        n = len(runs)
        avg_trades = sum(r["total_trades"] for r in runs) / n
        avg_ret = sum(r["total_return_pct"] for r in runs) / n
        avg_dd = sum(r["max_drawdown_pct"] for r in runs) / n
        avg_wr = sum(r["win_rate"] for r in runs) / n
        avg_sharpe = sum(r["sharpe_ratio"] for r in runs) / n
        # MEDIAN: profit factor is an unbounded ratio carrying an "undefined"
        # sentinel, so its mean flatters twice over. See backtest/metrics.py.
        pf = profit_factor_summary((r["profit_factor"], r["total_trades"]) for r in runs)
        med_pf = pf.median if pf.median is not None else float("nan")
        avg_sortino = sum(r["sortino_ratio"] for r in runs) / n
        worst_dd = max(r["max_drawdown_pct"] for r in runs)
        print(f"{s:12s} {n:5d} {avg_trades:7.0f} {avg_ret:+9.2f} {avg_dd:8.2f} {avg_wr * 100:8.1f} {avg_sharpe:+11.2f} {med_pf:8.2f} {worst_dd:10.2f} {avg_sortino:+12.2f}")

    # Per-regime aggregates
    print("\n" + "=" * 100)
    print("PER-REGIME AGGREGATE (across all symbols and seeds)")
    print("=" * 100)
    print(f"{'Regime':16s} {'Runs':>5s} {'Trades':>7s} {'Avg Ret%':>9s} {'Avg DD%':>8s} {'Avg WR%':>8s} {'Avg Sharpe':>11s} {'Med PF':>8s} {'Avg Sortino':>12s}")
    print("-" * 100)

    for regime in REGIMES:
        runs = [r for r in valid if r["regime"] == regime["label"]]
        if not runs:
            continue
        n = len(runs)
        avg_trades = sum(r["total_trades"] for r in runs) / n
        avg_ret = sum(r["total_return_pct"] for r in runs) / n
        avg_dd = sum(r["max_drawdown_pct"] for r in runs) / n
        avg_wr = sum(r["win_rate"] for r in runs) / n
        avg_sharpe = sum(r["sharpe_ratio"] for r in runs) / n
        pf = profit_factor_summary((r["profit_factor"], r["total_trades"]) for r in runs)
        med_pf = pf.median if pf.median is not None else float("nan")
        avg_sortino = sum(r["sortino_ratio"] for r in runs) / n
        print(f"{regime['label']:16s} {n:5d} {avg_trades:7.0f} {avg_ret:+9.2f} {avg_dd:8.2f} {avg_wr * 100:8.1f} {avg_sharpe:+11.2f} {med_pf:8.2f} {avg_sortino:+12.2f}")

    # Global summary
    summary = summarise(valid)
    means = summary["means_over_traded_runs"]

    def show(x, fmt):
        return "n/a (no run traded)" if x is None else format(x, fmt)

    print("\n" + "=" * 100)
    print("GLOBAL SUMMARY")
    print("=" * 100)
    print(f"  Total runs:            {len(valid)} valid, {len(errors)} errors (of {total_runs} planned)")
    print(f"  Total trades:          {summary['total_trades']}")
    print(f"  Runs that traded:      {summary['runs_that_traded']} "
          f"({summary['runs_idle']} never traded; the means below are over the runs that traded)")
    print(f"  Avg return:            {show(means['return_pct'], '+.2f')}%")
    print(f"  Best return:           {summary['best_return_pct']:+.2f}%")
    print(f"  Worst return:          {summary['worst_return_pct']:+.2f}%")
    print(f"  Avg max drawdown:      {show(means['max_drawdown_pct'], '.2f')}%")
    print(f"  Worst drawdown:        {summary['worst_drawdown_pct']:.2f}%")
    print(f"  Avg win rate:          {show(None if means['win_rate'] is None else means['win_rate'] * 100, '.1f')}%")
    print(f"  Avg Sharpe:            {show(means['sharpe'], '+.2f')}")
    print(f"  Avg Sortino:           {show(means['sortino'], '+.2f')}")
    pf = profit_factor_summary((r["profit_factor"], r["total_trades"]) for r in valid)
    shr = share_profitable((r["total_return_pct"], r["total_trades"]) for r in valid)
    print(f"  Profit factor:         {pf.render()}")
    # The number that settles the argument fastest. "Avg return -0.46%"
    # invites "so it is roughly flat"; "113 of 485 profitable" does not.
    print(f"  Profitable runs:       {shr.render()}")
    print(f"  Crashed runs (DD>20%): {summary['crashed_runs']}")
    print(f"  Total commission:      ${summary['total_commission']:,.2f}")
    print(f"  Total slippage:        ${summary['total_slippage']:,.2f}")
    print(f"  Runtime:               {elapsed:.1f}s")

    # Save full results
    with open(OUTPUT_PATH, "w") as f:
        json.dump({
            "meta": {
                "symbols": len(SYMBOLS),
                "regimes": len(REGIMES),
                "seeds": len(SEEDS),
                "total_runs": total_runs,
                "valid_runs": len(valid),
                "error_runs": len(errors),
                "bars_per_run": BARS,
                "runtime_seconds": round(elapsed, 1),
                # Read alone, this file is 500 named-crypto runs; say inside it
                # that every price in it was generated (audit B4-10).
                "data_source": "synthetic",
                "used_synthetic": True,
                "provenance": provenance(),
            },
            "summary": summary,
            "results": all_results,
        }, f, indent=2, default=str)
    print(f"\nFull results saved to {OUTPUT_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
