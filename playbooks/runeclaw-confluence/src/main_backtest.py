"""Historical replay entry for RUNECLAW Confluence.

Fetches BNB (traded) + BTC (leader regime context) 15m klines in chunks that
respect the per-request bar cap, runs the self-contained Nautilus replay via
``backtest.run`` with both instruments, writes the output contract files, and
emits a summary signal. A missing leader feed aborts the run as a watch —
regime is never fabricated from absent data.
"""
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import pandas as pd
from getagent import backtest, data, runtime

_BNB_KEY = "BNBUSDT.BITGET"
_BTC_KEY = "BTCUSDT.BITGET"
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
    for _ in range(24):  # hard bound on requests
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


def run() -> None:
    cfg = runtime.manifest.get("strategy_config", {}) or {}
    interval = str(cfg.get("interval", "15m"))
    days = int(cfg.get("backtest_days", 45))
    exchange = str(cfg.get("data_exchange", "bitget"))

    bnb = _fetch_frame("BNBUSDT", interval, exchange, days)
    btc = _fetch_frame("BTCUSDT", interval, exchange, days)
    if (bnb is None or bnb.empty) and exchange != "binance":
        # Documented fallback: replay data only; venue fees stay as declared.
        print("[runeclaw-confluence] bitget kline path empty for BNBUSDT; "
              "falling back to binance data feed")
        exchange = "binance"
        bnb = _fetch_frame("BNBUSDT", interval, exchange, days)
        btc = _fetch_frame("BTCUSDT", interval, exchange, days)
    if bnb is None or bnb.empty:
        _emit_watch("no_historical_bars_bnb")
        return
    if btc is None or btc.empty:
        _emit_watch("no_historical_bars_btc_leader")
        return

    print(f"[runeclaw-confluence] bnb_rows={len(bnb)} btc_rows={len(btc)} "
          f"first={bnb.index[0]} last={bnb.index[-1]} exchange={exchange}")

    result = backtest.run(ohlcv_data={_BNB_KEY: bnb, _BTC_KEY: btc},
                          spec=runtime.backtest_spec)
    summary = result.summary or {}
    raw = result.raw if isinstance(result.raw, dict) else {}
    total_trades = int(getattr(result, "total_trades", 0) or 0)
    print(f"[runeclaw-confluence] trades={total_trades} "
          f"return_pct={getattr(result, 'total_return_pct', None)} "
          f"win_rate={getattr(result, 'win_rate', None)} "
          f"pf={getattr(result, 'profit_factor', None)}")

    starting_balance = _f(summary.get("starting_balance"), 10000.0)

    # Reconcile from the positions ledger itself — never from summary fields.
    # (The engine's in-run summary and the platform-normalized record can
    # disagree; the closed-positions report is the single source of truth.)
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

    _write_equity_curve(bnb, starting_balance, net_pnl, raw)

    chart_path = ""
    try:
        chart_path = backtest.generate_chart(result)
    except Exception as exc:
        print(f"[runeclaw-confluence] chart failed: {type(exc).__name__}: {exc}")

    # Source every emitted number from the closed-positions ledger.
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
        "rows": len(bnb),
        "leader_rows": len(btc),
    }
    action = "long" if net_pnl > 0 and positions > 0 else "watch"
    runtime.emit_signal(
        action=action, symbol="BNBUSDT",
        confidence=win_rate,
        metrics=metrics,
        meta={"chart_path": chart_path, "interval": interval, "exchange": exchange,
              "gate_symbol": "BTCUSDT", "backtest_days": days, "run_id": runtime.run_id},
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
        running = starting_balance
        for pos in positions:
            ts = pos.get("ts_closed") or pos.get("closing_time") or pos.get("ts_last")
            pnl = _f(pos.get("realized_pnl") or pos.get("realized_return") or 0.0)
            running += pnl
            if ts is not None:
                points.append((str(ts), running))
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
