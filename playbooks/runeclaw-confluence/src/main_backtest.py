"""Historical replay entry for RUNECLAW Confluence (multi-symbol).

Fetches 15m klines for a configurable slice of the traded universe plus the
BTC leader context in batches that respect the per-request bar cap, filters
the Nautilus spec down to the instruments that actually loaded (the engine
requires data for every declared instrument), runs the self-contained replay,
writes the output contract files, and emits a summary signal. A missing
leader feed aborts the run as a watch — regime is never fabricated from
absent data, and skipped symbols are reported, never silently dropped.
"""
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import pandas as pd
from getagent import backtest, data, runtime

_GATE = "BTCUSDT"
_VENUE = "BITGET"
_OUT = Path("/workspace/output")
_INTERVAL_MS = {"5m": 300_000, "15m": 900_000, "1h": 3_600_000, "4h": 14_400_000}
_WARMUP_BARS = 420


def _f(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def _emit_watch(reason: str, extra: Optional[dict] = None) -> None:
    metrics = {"total_trades": 0}
    if extra:
        metrics.update(extra)
    runtime.emit_signal(action="watch", symbol="BNBUSDT", confidence=0.0,
                        metrics=metrics, meta={"reason": reason, "run_id": runtime.run_id})


def _fetch_frame(symbol: str, interval: str, exchange: str, days: int) -> Optional[pd.DataFrame]:
    """Chunked kline fetch: each request stays under the bar cap and the
    per-request day cap; chunks are concatenated, de-duplicated, and sorted."""
    step_ms = _INTERVAL_MS.get(interval, 900_000)
    total_bars = days * (86_400_000 // step_ms) + _WARMUP_BARS
    end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = end_ms - total_bars * step_ms
    frames = []
    cursor = start_ms
    for _ in range(24):  # hard bound on requests per symbol
        if cursor >= end_ms:
            break
        chunk_end = min(cursor + 990 * step_ms, end_ms)
        try:
            raw = data.crypto.futures.kline(symbol=symbol, interval=interval,
                                            exchange=exchange, limit=1000,
                                            start_time=cursor, end_time=chunk_end)
            frame = backtest.prepare_frame(raw, datetime_index="date")
        except Exception as exc:
            print(f"[runeclaw-confluence] fetch {symbol} chunk failed: "
                  f"{type(exc).__name__}: {exc}")
            frame = None
        if frame is None or frame.empty:
            cursor = chunk_end + step_ms
            continue
        frames.append(frame)
        last_ts = int(frame.index[-1].timestamp() * 1000)
        cursor = max(last_ts + step_ms, chunk_end + step_ms)
    if not frames:
        return None
    out = pd.concat(frames)
    out = out[~out.index.duplicated(keep="last")].sort_index()
    return out.tail(total_bars)


def _filtered_spec(spec: Any, loaded_keys: set) -> Any:
    """Keep only instruments whose data loaded — the engine expects OHLCV for
    every declared instrument. On any surprise in the spec shape, return it
    unchanged and let the engine report the real error."""
    try:
        out = dict(spec)
        instruments = out.get("instruments")
        if isinstance(instruments, list):
            kept = [i for i in instruments if str(i.get("id")) in loaded_keys]
            if kept:
                out["instruments"] = kept
                return out
    except Exception as exc:
        print(f"[runeclaw-confluence] spec filter skipped: {type(exc).__name__}: {exc}")
    return spec


def run() -> None:
    cfg = runtime.manifest.get("strategy_config", {}) or {}
    interval = str(cfg.get("interval", "15m"))
    days = int(cfg.get("backtest_days", 30))
    exchange = str(cfg.get("data_exchange", "bitget"))
    max_symbols = max(int(cfg.get("max_backtest_symbols", 5)), 1)
    batch_size = max(int(cfg.get("backtest_batch_size", 4)), 1)

    universe = [str(s).upper() for s in (cfg.get("trading_symbols") or [])
                if str(s).upper() != _GATE]
    requested = universe[:max_symbols]
    if not requested:
        _emit_watch("empty_universe")
        return

    btc = _fetch_frame(_GATE, interval, exchange, days)
    if btc is None or btc.empty:
        _emit_watch("no_historical_bars_btc_leader")
        return

    loaded: dict = {}
    skipped: list = []
    for start in range(0, len(requested), batch_size):
        for symbol in requested[start:start + batch_size]:
            frame = _fetch_frame(symbol, interval, exchange, days)
            if frame is None or frame.empty:
                skipped.append(symbol)
                print(f"[runeclaw-confluence] no replay data for {symbol}; skipped")
                continue
            loaded[symbol] = frame
    if not loaded:
        _emit_watch("no_historical_bars_universe",
                    {"symbols_requested": len(requested)})
        return

    ohlcv = {f"{sym}.{_VENUE}": frame for sym, frame in loaded.items()}
    ohlcv[f"{_GATE}.{_VENUE}"] = btc
    loaded_keys = set(ohlcv.keys())
    anchor = next(iter(loaded))
    print(f"[runeclaw-confluence] symbols_loaded={len(loaded)}/{len(requested)} "
          f"skipped={skipped} rows_each≈{len(next(iter(loaded.values())))} "
          f"btc_rows={len(btc)} exchange={exchange}")

    spec = _filtered_spec(runtime.backtest_spec, loaded_keys)
    result = backtest.run(ohlcv_data=ohlcv, spec=spec)
    summary = result.summary or {}
    raw = result.raw if isinstance(result.raw, dict) else {}
    print(f"[runeclaw-confluence] trades={int(_f(summary.get('total_trades'), 0))} "
          f"positions={int(_f(summary.get('position_count'), 0))} "
          f"pf={summary.get('profit_factor')} sharpe={summary.get('sharpe_ratio')}")

    starting_balance = _f(summary.get("starting_balance"), 10000.0)

    # Reconcile from the positions ledger itself — never from summary fields.
    ledger = _position_ledger(raw)
    if ledger["count"] > 0:
        net_pnl = ledger["net_pnl"]
    else:
        stats_pnls = (raw.get("stats") or {}).get("pnls") or {}
        net_pnl = _f(stats_pnls.get("PnL (total)"), _f(summary.get("net_pnl"), 0.0))
    account_return_pct = (net_pnl / starting_balance * 100.0) if starting_balance else 0.0

    # Engine summary fields flatten into raw top-level and the backend merge
    # treats the report as BASE — override with correct absolute values.
    raw["net_pnl"] = round(net_pnl, 6)
    raw["total_return_pct"] = round(account_return_pct, 6)
    raw["starting_balance"] = starting_balance

    _OUT.mkdir(parents=True, exist_ok=True)
    report = {k: v for k, v in raw.items() if k != "reports"}
    report["reports"] = {k: v for k, v in (raw.get("reports") or {}).items()
                         if k != "equity_curve"}
    try:
        (_OUT / "backtest_report.json").write_text(json.dumps(report, default=str),
                                                   encoding="utf-8")
    except Exception as exc:
        print(f"[runeclaw-confluence] report write failed: {type(exc).__name__}: {exc}")

    _write_equity_curve(btc, starting_balance, net_pnl, raw)

    chart_path = ""
    try:
        chart_path = backtest.generate_chart(result)
    except Exception as exc:
        print(f"[runeclaw-confluence] chart failed: {type(exc).__name__}: {exc}")

    if ledger["count"] > 0:
        positions = ledger["count"]
        win_rate = ledger["win_rate"]
        profit_factor = ledger["profit_factor"]
    else:
        positions = int(_f(summary.get("position_count"), 0.0))
        win_rate = _f(summary.get("win_rate"), 0.0)
        profit_factor = _f(summary.get("profit_factor"), 0.0)
    metrics = {
        "total_return_pct": round(account_return_pct, 4),
        "account_return_pct": round(account_return_pct, 4),
        "net_pnl": round(net_pnl, 4),
        "starting_balance": starting_balance,
        "sharpe_ratio": _f(summary.get("sharpe_ratio"), 0.0),
        "max_drawdown_pct": _f(summary.get("max_drawdown_pct"), 0.0),
        "win_rate": win_rate,
        "total_trades": positions * 2,
        "position_count": positions,
        "profit_factor": profit_factor,
        "metrics_source": "positions_ledger" if ledger["count"] > 0 else "engine_summary",
        "symbols_requested": len(requested),
        "symbols_loaded": len(loaded),
        "leader_rows": len(btc),
    }
    action = "long" if net_pnl > 0 and positions > 0 else "watch"
    runtime.emit_signal(
        action=action, symbol=anchor,
        confidence=win_rate,
        metrics=metrics,
        meta={"chart_path": chart_path, "interval": interval, "exchange": exchange,
              "gate_symbol": _GATE, "backtest_days": days,
              "symbols_loaded": sorted(loaded.keys()), "symbols_skipped": skipped,
              "batch_size": batch_size, "run_id": runtime.run_id},
    )


def _position_ledger(raw: dict) -> dict:
    """Wins/losses/net from the closed-positions report. A row whose realized
    PnL cannot be read is counted as unreadable, never as zero."""
    out = {"count": 0, "net_pnl": 0.0, "win_rate": 0.0, "profit_factor": 0.0,
           "unreadable": 0}
    try:
        rows = (raw.get("reports") or {}).get("positions") or []
    except Exception:
        return out
    wins = 0.0
    losses = 0.0
    win_count = 0
    count = 0
    for row in rows:
        value = row.get("realized_pnl", row.get("realized_return"))
        try:
            pnl = float(value)
        except (TypeError, ValueError):
            try:
                pnl = float(str(value).split()[0].replace(",", ""))
            except (TypeError, ValueError, IndexError):
                out["unreadable"] += 1
                continue
        if not math.isfinite(pnl):
            out["unreadable"] += 1
            continue
        count += 1
        out["net_pnl"] += pnl
        if pnl > 0:
            wins += pnl
            win_count += 1
        else:
            losses += -pnl
    out["count"] = count
    out["net_pnl"] = round(out["net_pnl"], 6)
    if count:
        out["win_rate"] = round(win_count / count, 6)
    if losses > 0:
        out["profit_factor"] = round(wins / losses, 4)
    elif wins > 0:
        out["profit_factor"] = 0.0
    return out


def _write_equity_curve(frame: pd.DataFrame, starting_balance: float,
                        net_pnl: float, raw: dict) -> None:
    lines = ["timestamp,value,nav"]
    points: list = []
    try:
        positions = (raw.get("reports") or {}).get("positions") or []
        rows = []
        for pos in positions:
            ts = pos.get("ts_closed") or pos.get("closing_time") or pos.get("ts_last")
            pnl = _f(pos.get("realized_pnl") or pos.get("realized_return") or 0.0)
            if ts is not None:
                rows.append((str(ts), pnl))
        rows.sort(key=lambda r: r[0])
        running = starting_balance
        for ts, pnl in rows:
            running += pnl
            points.append((ts, running))
    except Exception:
        points = []
    if not points:
        points = [(str(frame.index[0]), starting_balance),
                  (str(frame.index[-1]), starting_balance + net_pnl)]
    for ts, value in points:
        nav = value / starting_balance if starting_balance else 1.0
        lines.append(f"{ts},{round(value, 6)},{round(nav, 8)}")
    try:
        (_OUT / "equity_curve.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception as exc:
        print(f"[runeclaw-confluence] equity curve write failed: {type(exc).__name__}: {exc}")
