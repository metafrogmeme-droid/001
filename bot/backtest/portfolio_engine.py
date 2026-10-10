"""
RUNECLAW Portfolio Backtester — multi-symbol, shared risk state.

The single-symbol BacktestEngine measures one pair in isolation, but RUNECLAW
is a PORTFOLIO system: live it scans a ~67-symbol universe and its risk
machinery (correlation sizing, per-strategy notional caps, covariance VaR,
daily-loss breaker) operates on combined state. One selective strategy per
symbol × many symbols is where the live trade cadence — and any statistical
power — comes from.

This orchestrator runs one BacktestEngine per symbol but injects a SINGLE
shared PortfolioTracker + RiskEngine + Analyzer into all of them, then drives
the per-symbol bar pipelines in merged timestamp order. Every idea from every
symbol flows through the same risk gate against the same equity, exposure and
breaker state — exactly like live. The equity curve, drawdown and metrics are
the SYSTEM's, with a per-symbol breakdown on the side.

Usage:
    data = {sym: [BacktestBar, ...], ...}     # e.g. DataLoader.from_bitget per symbol
    pb = PortfolioBacktester(BacktestConfig(...), symbols=list(data))
    result = await pb.run(data)               # aggregate BacktestResult
    pb.per_symbol                              # {symbol: {trades, net_pnl, win_rate}}
    pb.cleanup()
"""
from __future__ import annotations

import time
from typing import TYPE_CHECKING, Optional

from bot.backtest.engine import BacktestEngine, BreakerResetClock, _gate_token
from bot.backtest.models import BacktestConfig, BacktestResult, EquityPoint
from bot.backtest.positions import positions
from bot.config import CONFIG
from bot.utils.logger import system_log, audit

if TYPE_CHECKING:
    from bot.core.limit_scanner import ScannerParams


class PortfolioBacktester:
    """Drives N single-symbol engines over one shared portfolio/risk state."""

    def __init__(self, config: BacktestConfig, symbols: list[str]) -> None:
        if not symbols:
            raise ValueError("PortfolioBacktester needs at least one symbol")
        self.config = config
        self.symbols = list(symbols)
        self.per_symbol: dict[str, dict] = {}

        # Save the operator's learning flags ONCE here; each sub-engine ctor
        # also saves-and-forces them, but after the first ctor it would save
        # the already-forced False values — restoring those on cleanup would
        # clobber the operator's real settings. The orchestrator owns the
        # save/restore; sub-engine restores are neutralized below.
        self._saved_learning_flags = (
            CONFIG.analyzer.confidence_calibration_enabled,
            CONFIG.analyzer.setup_expectancy_enabled,
            CONFIG.analyzer.external_sentiment_enabled,
        )

        # One engine per symbol; the FIRST engine's portfolio/risk/analyzer
        # become the shared instances injected into the rest.
        self._engines: dict[str, BacktestEngine] = {}
        for i, sym in enumerate(self.symbols):
            cfg = config.model_copy(update={"symbol": sym})
            eng = BacktestEngine(cfg)
            eng._saved_learning_flags = (None, None, None)  # orchestrator restores
            if i == 0:
                self._portfolio = eng.portfolio
                self._risk = eng.risk
                self._analyzer = eng.analyzer
            else:
                eng.portfolio = self._portfolio
                eng.risk = self._risk
                eng.analyzer = self._analyzer
            self._engines[sym] = eng
        # Shared close-callback: portfolio closes feed the ONE risk streak.
        self._portfolio._on_trade_close = self._risk.record_trade_result

        self._equity_curve: list[EquityPoint] = []

        # The Limit Entry Scanner replaces the analyzer for the whole book.
        # Its numbers are read once; numbers that do not read refuse the run
        # rather than run a rule the config does not describe.
        self._scanner: Optional[ScannerParams] = None
        #: Each pass's outcome, by the sentence it ended on.
        self.scanner_passes: dict[str, int] = {}
        if config.scanner:
            from bot.core.limit_scanner import SCANNER_NAME, params_from_preset
            if config.scanner != SCANNER_NAME:
                raise ValueError(f"unknown scanner {config.scanner!r}")
            params = params_from_preset({"scanner": SCANNER_NAME, **config.scanner_config})
            if params is None:
                raise ValueError("the scanner's configured numbers could not be read")
            self._scanner = params
            for eng in self._engines.values():
                eng._scanner_exits = params

    # ── lifecycle ────────────────────────────────────────────────

    def _restore_learning_flags(self) -> None:
        """Give the operator's learning flags back. Idempotent — first call wins.

        Same contract as BacktestEngine._restore_learning_flags: the override is
        process-wide, so it has to come back on every path out — including a
        fold that raises — or the live bot keeps analysing without them.
        """
        try:
            _cal, _exp, _ext = self._saved_learning_flags
            # Idempotent (same reasoning as BacktestEngine.cleanup): never
            # re-restore after a later backtest has re-forced the flags.
            self._saved_learning_flags = (None, None, None)
            if _cal is not None:
                object.__setattr__(CONFIG.analyzer, "confidence_calibration_enabled", _cal)
                object.__setattr__(CONFIG.analyzer, "setup_expectancy_enabled", _exp)
            if _ext is not None:
                object.__setattr__(CONFIG.analyzer, "external_sentiment_enabled", _ext)
        except Exception:
            pass

    def __enter__(self) -> "PortfolioBacktester":
        return self

    def __exit__(self, *exc: object) -> bool:
        self.cleanup()
        return False

    def cleanup(self) -> None:
        for eng in self._engines.values():
            eng.cleanup()
        self._restore_learning_flags()

    # ── main loop ────────────────────────────────────────────────

    async def run(self, data: dict[str, list]) -> BacktestResult:
        """Run the merged multi-symbol backtest.

        The learning-flag override is handed back here rather than waiting for
        cleanup(), so a fold that raises cannot leave the process analysing with
        calibration, setup-expectancy and external sentiment silently off.
        """
        try:
            return await self._run(data)
        finally:
            self._restore_learning_flags()

    async def _run(self, data: dict[str, list]) -> BacktestResult:
        """``data`` maps each configured symbol to its (ascending) BacktestBar
        list; symbols absent from the map are skipped with a log line."""
        start_time = time.time()
        streams = {s: bars for s, bars in data.items()
                   if s in self._engines and bars}
        if not streams:
            raise ValueError("no bar data for any configured symbol")

        audit(system_log, f"Portfolio backtest started: {len(streams)} symbols",
              action="portfolio_backtest_start",
              data={"symbols": list(streams),
                    "bars": {s: len(b) for s, b in streams.items()},
                    "balance": self.config.initial_balance})

        lookback = self.config.lookback_size
        scan_interval = self.config.scan_interval

        # Merged ascending timeline of every timestamp any symbol has a bar at.
        timeline = sorted({b.timestamp for bars in streams.values() for b in bars})
        # Per-symbol cursors + per-symbol bar counters (scan cadence is per
        # symbol, exactly like N independent run() loops).
        idx = {s: 0 for s in streams}
        last_close: dict[str, float] = {}
        for eng in self._engines.values():
            eng._pending_entry = None
            eng._pending_ma = None
        # MTF parity (mirrors BacktestEngine.run): full raw history per symbol
        # so _process_bar can resample closed 4h/1d groups.
        for sym, bars in streams.items():
            self._engines[sym]._all_raw = [
                [int(b.timestamp.timestamp() * 1000), b.open, b.high, b.low, b.close, b.volume]
                for b in bars
            ]

        snap_counter = 0
        breaker_reset = BreakerResetClock(self.config.breaker_reset_bars)
        for step, ts in enumerate(timeline):
            # Simulated clock: cooldown-after-loss must elapse in BAR time,
            # and the daily P&L must roll over on the BAR's day.
            self._risk.set_sim_time(ts)
            self._portfolio.pin_replay_day(ts)
            # One step per timeline bar, as run() takes one per bar.
            breaker_reset.step(self._risk, step)
            fresh: list[str] = []
            for sym, bars in streams.items():
                i = idx[sym]
                if i >= len(bars) or bars[i].timestamp != ts:
                    continue
                idx[sym] = i + 1
                eng = self._engines[sym]
                bar = bars[i]
                last_close[sym] = bar.close

                if i < lookback:
                    continue  # warmup — indicators need history
                fresh.append(sym)

                # Same per-bar pipeline as BacktestEngine.run(), the queued
                # fills through the same method.
                eng._fill_queued_at_open(bar)

                # Resting limits: fill on touch, expire, or cancel on drift --
                # before the stop check, as run() does. Missing here, a limit
                # its signal bar did not reach was never read again.
                eng._drain_pending_limits(bar)

                eng._check_stops_intrabar(bar)

                if self._scanner is None and eng._wants_signal_bar(i):
                    window = bars[max(0, i - lookback):i + 1]
                    await eng._process_bar(bar, window, i)

            # The scanner reads the whole universe at once, after every
            # symbol's fills and stops for this bar, as the live pass reads
            # its closed bars.
            if self._scanner is not None and fresh:
                self._scanner_pass(streams, idx, fresh)

            # Portfolio-level equity snapshot (throttled like run()'s cadence).
            snap_counter += 1
            if snap_counter % scan_interval == 0 or ts == timeline[-1]:
                open_assets = {p.asset for p in self._portfolio._positions.values()}
                assets_px = {a: last_close[a] for a in open_assets if a in last_close}
                self._portfolio.mark_to_market(assets_px)
                snap = self._portfolio.snapshot()
                peak = self._portfolio._peak_equity
                dd = ((peak - snap.equity_usd) / peak * 100) if peak > 0 else 0
                self._equity_curve.append(EquityPoint(
                    timestamp=ts, equity=snap.equity_usd,
                    drawdown_pct=round(dd, 2),
                    open_positions=snap.open_positions))

        # Close whatever is still open at each symbol's LAST bar (its own
        # price — never another symbol's).
        for sym, bars in streams.items():
            self._engines[sym]._close_all_at_bar(bars[-1], "END_OF_DATA")

        # ── Aggregate compile ────────────────────────────────────
        # Reuse the single-engine compiler with merged inputs: the shared
        # portfolio snapshot is already system-level; inject the merged trade
        # list + portfolio equity curve + summed pipeline counters into the
        # first engine and let _compile_result do the math.
        first = self._engines[self.symbols[0]]
        merged_trades = sorted(
            (t for eng in self._engines.values() for t in eng._trades),
            key=lambda t: t.exit_time)
        first._trades = merged_trades
        first._equity_curve = self._equity_curve
        first._rr_values = [rr for eng in self._engines.values()
                            for rr in eng._rr_values]
        first._signals_generated = sum(e._signals_generated for e in self._engines.values())
        first._ideas_generated = sum(e._ideas_generated for e in self._engines.values())
        first._ideas_rejected_risk = sum(e._ideas_rejected_risk for e in self._engines.values())
        first._ideas_rejected_confidence = sum(
            e._ideas_rejected_confidence for e in self._engines.values())
        # Each symbol's engine rests its own limits, so the lifecycle counts
        # are the sum: the first engine's alone reported one symbol's limits
        # as the whole book's.
        for name in ("_limits_filled", "_limits_filled_same_bar", "_limits_expired",
                     "_limits_cancelled_drift"):
            setattr(first, name, sum(getattr(e, name) for e in self._engines.values()))

        longest = max(streams.values(), key=len)
        result = first._compile_result(longest, time.time() - start_time)
        result.symbol = "+".join(sorted(streams))

        self.per_symbol = per_symbol_table(merged_trades, streams)

        audit(system_log,
              f"Portfolio backtest complete: {result.total_trades} trades, "
              f"return={result.total_return_pct:.2f}%",
              action="portfolio_backtest_complete",
              data={"per_symbol": self.per_symbol})
        return result


    def _scanner_pass(self, streams: dict, idx: dict, fresh: list[str]) -> None:
        """One Limit Entry Scanner pass over the symbols that closed a bar.

        Each market class is passed on its own (its leader, its members, its
        volume floor), and each places at most one limit, its board's best
        at or above the minimum score, through the same risk gate as every
        other idea. It rests from the next bar (`BacktestEngine._rest_limit`),
        and a regime's half size halves the gate's size. A symbol that holds
        a position or a resting limit is scored and not placed again.
        """
        from bot.core.limit_scanner import SNAPSHOT_BARS, scan_classes, snapshot_from_bars
        from bot.core.strategy_gate import _base

        params = self._scanner
        if params is None:
            return
        snaps = []
        for sym in fresh:
            eng = self._engines[sym]
            eng._signals_generated += 1
            snaps.append(snapshot_from_bars(sym, eng._all_raw[max(0, idx[sym] - SNAPSHOT_BARS):idx[sym]],
                                            params))
        held = frozenset(
            [_base(p.asset) for p in self._portfolio._positions.values()]
            + [_base(o["idea"].asset) for e in self._engines.values() for o in e._pending_limits])
        for name, result in scan_classes(snaps, params, held):
            prefix = "" if name == "all" else f"{name}: "
            if result.plan is None:
                self._count_pass(prefix + result.reason.split(": ", 1)[-1])
                continue
            self._count_pass(prefix + self._place_scanner_plan(result, snaps, streams, idx))

    def _place_scanner_plan(self, result, snaps: list, streams: dict, idx: dict) -> str:
        """Put one pass's plan through the risk gate and rest it; the outcome."""
        from bot.core.limit_scanner import LONG, SOURCE
        from bot.utils.models import Direction, RiskVerdict, TradeIdea

        plan = result.plan
        eng = self._engines[plan.symbol]
        bar = streams[plan.symbol][idx[plan.symbol] - 1]
        snap = next(s for s in snaps if s.symbol == plan.symbol)
        idea = TradeIdea(
            asset=plan.symbol,
            direction=Direction.LONG if plan.side == LONG else Direction.SHORT,
            entry_price=plan.limit_price,
            stop_loss=plan.stop,
            take_profit=plan.take_profit,
            confidence=min(1.0, plan.points / 100.0),
            reasoning=(f"Limit Entry Scanner: scored {plan.points:.1f} of 100; "
                       f"{result.regime.reason}."),
            signals_used=[SOURCE],
            source=SOURCE,
            order_type="limit",
            strategy_type="intraday",
            signal_type="limit_scanner",
            timeframe=self.config.timeframe,
        )
        check = eng.risk.evaluate(idea, atr=snap.atr, as_of=bar.timestamp,
                                  fill_leverage=int(self.config.leverage))
        if check.verdict == RiskVerdict.REJECTED:
            eng._ideas_rejected_risk += 1
            for _chk in (check.checks_failed or []):
                _tok = _gate_token(_chk)
                if _tok:
                    eng._rejections_by_gate[_tok] = eng._rejections_by_gate.get(_tok, 0) + 1
            return "refused by the risk gate"
        if plan.size_factor < 1.0:
            check = check.model_copy(update={
                "position_size_usd": check.position_size_usd * plan.size_factor,
                "position_pct": check.position_pct * plan.size_factor})
        eng._ideas_generated += 1
        if snap.atr is not None:
            eng._preset_atr_by_idea[idea.id] = snap.atr
        eng.risk.register_pending_intent(idea)
        eng._rest_limit(idea, check, bar)
        return "placed a limit"

    def _count_pass(self, outcome: str) -> None:
        self.scanner_passes[outcome] = self.scanner_passes.get(outcome, 0) + 1


def per_symbol_table(trades, symbols) -> dict[str, dict]:
    """The per-symbol breakdown, one row per POSITION (audit B4-04): the
    partial-TP ladder's fills of one position are one trade, and its outcome
    is their sum."""
    held = positions(trades)
    out: dict[str, dict] = {}
    for sym in symbols:
        st = [t for t in held if t.symbol == sym]
        wins = sum(1 for t in st if t.net_pnl_usd > 0)
        out[sym] = {
            "trades": len(st),
            "net_pnl": round(sum(t.net_pnl_usd for t in st), 2),
            "win_rate": round(wins / len(st), 4) if st else 0.0,
        }
    return out


async def portfolio_walk_forward(
    data: dict[str, list],
    config: BacktestConfig,
    n_folds: int = 5,
    is_min_frac: float = 0.4,
) -> list[dict]:
    """Rolling OOS robustness over the merged timeline, portfolio-wide.

    Splits the merged timestamp range into ``n_folds`` out-of-sample blocks
    after an ``is_min_frac`` warmup prefix (mirroring walk_forward.make_folds)
    and runs a fresh PortfolioBacktester per block. Each block's data starts
    exactly ``lookback_size`` timestamps before its OOS start, so the engine's
    indicator warmup consumes that prefix and TRADING begins at the OOS
    boundary — no optimizer runs here, so there is nothing to embargo.
    """
    timeline = sorted({b.timestamp for bars in data.values() for b in bars})
    n = len(timeline)
    warmup = int(n * is_min_frac)
    block = max(1, (n - warmup) // n_folds)
    out = []
    for k in range(n_folds):
        oos_start = warmup + k * block
        oos_end = warmup + (k + 1) * block if k < n_folds - 1 else n
        # Exactly lookback_size warmup bars before the block: the engine skips
        # them as indicator warmup, so trading starts at the OOS boundary.
        slice_start_i = max(0, oos_start - config.lookback_size)
        t0 = timeline[slice_start_i]
        t_start = timeline[oos_start]
        t_end = timeline[oos_end - 1]
        fold_data = {
            s: [b for b in bars if t0 <= b.timestamp <= t_end]
            for s, bars in data.items()
        }
        fold_data = {s: b for s, b in fold_data.items() if len(b) > config.lookback_size}
        if not fold_data:
            continue
        pb = PortfolioBacktester(config, symbols=list(fold_data))
        try:
            res = await pb.run(fold_data)
        finally:
            pb.cleanup()
        out.append({
            "fold": k,
            "oos_start": str(t_start), "oos_end": str(t_end),
            "trades": res.total_trades,
            "return_pct": res.total_return_pct,
            "win_rate": res.win_rate,
            "max_dd_pct": res.max_drawdown_pct,
            "profit_factor": res.profit_factor,
            # Carried so the runner can record OOS evidence to the validation
            # gate without inventing a statistic. A fold that took no trade has
            # a 0.0 here that nothing measured, so the runner averages only over
            # folds whose `trades` is non-zero.
            "sharpe": res.sharpe_ratio,
            "per_symbol": dict(pb.per_symbol),
            # Full OOS trade objects for pooled attribution across folds. The
            # runner merges these; excluded from any JSON dump (leading "_").
            "_trades": list(res.trades),
        })
    return out
