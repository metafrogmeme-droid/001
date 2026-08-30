"""Live scan + signal for RUNECLAW Confluence (multi-symbol).

Each scheduled pass fetches recent 15m klines for the BTC leader and every
symbol in the configured universe, computes the exact same features / regime /
confluence as the replay via ``indicators.py``, ranks the qualifying
candidates, and emits one managed signal for the best of them (or a WATCH
with the ranked board when nothing qualifies). In follow-trade mode the
sanctioned ``emit_signal_or_follow`` callback places a limit entry with a
tick-aligned stop and target, guarded by: data freshness, a per-symbol
duplicate check, a best-effort portfolio cap across the universe, risk-exact
sizing, and a margin cap. Anything unreadable becomes a stand-aside WATCH,
never a default trade.
"""
import math
from typing import Any, Optional

from getagent import data, runtime, trade

from .indicators import classify_regime, compute_features, confluence_score

_GATE = "BTCUSDT"
_BARS = 430
_INTERVAL_MS = {"5m": 300_000, "15m": 900_000, "1h": 3_600_000, "4h": 14_400_000}


def _cfg() -> dict:
    return runtime.manifest.get("strategy_config", {}) or {}


def _sanitize(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {k: _sanitize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize(v) for v in value]
    return value


def _fetch(symbol: str, interval: str, exchange: str):
    """Return (features, last_open_ms) or (None, None)."""
    try:
        raw = data.crypto.futures.kline(symbol=symbol, interval=interval,
                                        exchange=exchange, limit=min(_BARS, 1000))
        frame = data.to_dataframe(raw)
    except Exception:
        return None, None
    if frame is None or len(frame) == 0:
        return None, None
    cols = {c.lower(): c for c in frame.columns}
    try:
        highs = [float(x) for x in frame[cols["high"]]]
        lows = [float(x) for x in frame[cols["low"]]]
        closes = [float(x) for x in frame[cols["close"]]]
        volumes = [float(x) for x in frame[cols["volume"]]]
    except Exception:
        return None, None
    last_open_ms = None
    for name in ("time", "date", "timestamp"):
        if name in cols:
            try:
                last_open_ms = int(frame[cols[name]].iloc[-1].timestamp() * 1000)
            except Exception:
                last_open_ms = None
            break
    if last_open_ms is None:
        try:
            last_open_ms = int(frame.index[-1].timestamp() * 1000)
        except Exception:
            last_open_ms = None
    return compute_features(highs, lows, closes, volumes, 96), last_open_ms


def _fresh(last_open_ms: Optional[int], interval: str) -> bool:
    if last_open_ms is None:
        return False
    step = _INTERVAL_MS.get(interval, 900_000)
    import datetime as _dt

    now_ms = int(_dt.datetime.now(_dt.timezone.utc).timestamp() * 1000)
    return (now_ms - (last_open_ms + step)) <= 2 * step


def _symbol_exposed(symbol: str) -> Optional[bool]:
    """True when the symbol has an open position or resting entry; None when
    the check itself failed (unreadable is never treated as clear)."""
    try:
        pos = trade.contract.current_position(symbol=symbol)
        text = str(getattr(pos, "raw", None) or pos)
        if '"total"' in text or "holdSide" in text or "hold_side" in text:
            for token in ('"total": "0"', "'total': '0'", '"total": 0'):
                if token in text:
                    break
            else:
                return True
    except Exception:
        return None
    try:
        pending = trade.contract.pending_orders(symbol=symbol)
        text = str(getattr(pending, "raw", None) or pending)
        if "orderId" in text or "order_id" in text:
            return True
    except Exception:
        return None
    return False


def _candidate(feats, symbol: str, state: str, cfg: dict, allow_short: bool,
               min_score: float) -> Optional[dict]:
    """Best qualifying (score, plan) for one symbol under the current regime,
    same routing order as the replay: breakout > momentum > reversion."""
    vol_ok = (feats.vol_ratio is not None
              and feats.vol_ratio >= float(cfg.get("bo_vol_ratio", "2.5")))
    if vol_ok:
        sides = (["long"] if state == "TREND_UP" else
                 (["short"] if state == "TREND_DOWN" and allow_short else
                  (["long"] + (["short"] if allow_short else []) if state == "RANGE" else [])))
        for side in sides:
            broke = feats.close > feats.hi * 0.9999 if side == "long" \
                else feats.close < feats.lo * 1.0001
            if not broke:
                continue
            score, dims = confluence_score(feats, side, "momentum")
            if score >= min_score:
                plan = _build_plan(feats, side, "breakout", cfg)
                if plan:
                    return {"symbol": symbol, "score": score, "dims": dims, "plan": plan}
    if state in ("TREND_UP", "TREND_DOWN"):
        side = "long" if state == "TREND_UP" else "short"
        if side == "long" or allow_short:
            score, dims = confluence_score(feats, side, "momentum")
            if score >= min_score:
                plan = _build_plan(feats, side, "momentum", cfg)
                if plan:
                    return {"symbol": symbol, "score": score, "dims": dims, "plan": plan}
    if state == "RANGE" and feats.atr and feats.vwap:
        stretch = (feats.close - feats.vwap) / feats.atr
        lim = float(cfg.get("mr_stretch_atr", "1.5"))
        side = "long" if stretch <= -lim else (
            "short" if (stretch >= lim and allow_short) else None)
        if side is not None:
            score, dims = confluence_score(feats, side, "mr")
            if score >= min_score:
                plan = _build_plan(feats, side, "mr", cfg)
                if plan:
                    return {"symbol": symbol, "score": score, "dims": dims, "plan": plan}
    return None


def _build_plan(feats, side: str, kind: str, cfg: dict) -> Optional[dict]:
    close = feats.close
    atr_v = feats.atr or 0.0
    vwap = feats.vwap or close
    long_side = side == "long"
    sl_min = float(cfg.get("sl_min_pct", "1.2")) / 100.0

    if kind == "momentum":
        entry = vwap - float(cfg.get("atr_limit_mult", "0.5")) * atr_v if long_side \
            else vwap + float(cfg.get("atr_limit_mult", "0.5")) * atr_v
        guard = feats.lo if long_side else feats.hi
        raw = (entry - guard) / entry if long_side else (guard - entry) / entry
        sl_pct = max(raw, sl_min)
        tp_pct = float(cfg.get("tp1_pct", "3.5")) / 100.0
        if tp_pct < 0.9 * sl_pct:
            return None
    elif kind == "breakout":
        entry = close
        sl_pct = max(float(cfg.get("bo_stop_atr", "1.0")) * atr_v / entry, sl_min)
        tp_pct = max(float(cfg.get("bo_tp_atr", "2.0")) * atr_v / entry, sl_pct)
    else:
        entry = close - 0.15 * atr_v if long_side else close + 0.15 * atr_v
        sl_pct = max(float(cfg.get("mr_stop_atr", "1.2")) * atr_v / entry, sl_min)
        tp_dist = (vwap - entry) if long_side else (entry - vwap)
        if tp_dist < sl_pct * entry:
            return None
        tp_pct = tp_dist / entry
    if entry <= 0 or sl_pct <= 0:
        return None
    stop = entry * (1.0 - sl_pct) if long_side else entry * (1.0 + sl_pct)
    target = entry * (1.0 + tp_pct) if long_side else entry * (1.0 - tp_pct)

    max_loss = float(cfg.get("max_loss_usdt", "15"))
    leverage = max(int(cfg.get("leverage", 10)), 1)
    budget = float(cfg.get("margin_budget", "100"))
    notional = max_loss / sl_pct
    note = ""
    if budget > 0 and (notional / leverage) > budget:
        notional = budget * leverage
        note = "capped_by_margin_budget"
    qty = notional / entry
    return {"side": side, "kind": kind, "entry": entry, "stop": stop, "target": target,
            "sl_pct": sl_pct, "tp_pct": tp_pct, "qty": qty, "notional": notional,
            "margin": notional / leverage, "leverage": leverage,
            "sizing_ok": notional >= 5.0, "note": note}


def _decide(cfg: dict) -> dict:
    interval = str(cfg.get("interval", "15m"))
    exchange = str(cfg.get("data_exchange", "bitget"))
    min_score = float(cfg.get("min_score", 70))
    allow_short = bool(cfg.get("allow_short", True))
    max_scan = max(int(cfg.get("max_scan_symbols", 12)), 1)
    universe = [str(s).upper() for s in (cfg.get("trading_symbols") or [])
                if str(s).upper() != _GATE][:max_scan]

    btc, btc_ts = _fetch(_GATE, interval, exchange)

    def watch(reason: str, symbol: str, extra: Optional[dict] = None,
              board: Optional[list] = None) -> dict:
        metrics = {"tradable_candidates": 0, "min_score": min_score, "scanned": len(universe)}
        if extra:
            metrics.update(extra)
        meta = {"reason": reason, "run_id": runtime.run_id}
        if board is not None:
            meta["board"] = board
        return {"action": "watch", "symbol": symbol, "confidence": 0.0,
                "metrics": metrics, "meta": meta, "plan": None}

    anchor = universe[0] if universe else _GATE
    if not universe:
        return watch("empty_universe", anchor)
    if btc is None or not btc.ok:
        return watch("leader_data_unreadable", anchor)
    if not _fresh(btc_ts, interval):
        return watch("stale_leader_data", anchor)

    regime = classify_regime(btc, cfg)
    state = regime["state"]
    base = {"regime": state, "leader_chg_pct": round((btc.chg or 0.0) * 100.0, 3),
            "min_score": min_score, "scanned": len(universe)}
    if state == "CHOP":
        return watch("leader_regime_chop", anchor, base)

    candidates: list = []
    unreadable: list = []
    for symbol in universe:
        feats, ts = _fetch(symbol, interval, exchange)
        if feats is None or not feats.ok or not _fresh(ts, interval):
            unreadable.append(symbol)
            continue
        cand = _candidate(feats, symbol, state, cfg, allow_short, min_score)
        if cand is not None:
            candidates.append(cand)
    candidates.sort(key=lambda c: c["score"], reverse=True)
    board = [{"symbol": c["symbol"], "side": c["plan"]["side"], "score": round(c["score"], 1),
              "kind": c["plan"]["kind"]} for c in candidates[:5]]

    best = next((c for c in candidates if c["plan"]["sizing_ok"]), None)
    if best is None:
        extra = dict(base)
        extra["unreadable_symbols"] = len(unreadable)
        return watch("no_setup_at_or_above_min_score", anchor, extra, board)

    plan = best["plan"]
    metrics = dict(base)
    metrics.update({
        "tradable_candidates": len(candidates), "side": plan["side"],
        "entry_kind": plan["kind"], "score": round(best["score"], 2),
        "limit_price": plan["entry"], "sl_price": plan["stop"],
        "sl_pct": round(plan["sl_pct"] * 100.0, 3), "tp_price": plan["target"],
        "notional_usdt": round(plan["notional"], 2), "margin_usdt": round(plan["margin"], 2),
        "leverage": plan["leverage"], "sizing_ok": plan["sizing_ok"],
    })
    meta = {"score_dims": best["dims"], "regime_detail": regime, "board": board,
            "unreadable_symbols": unreadable,
            "sizing_note": plan["note"] or "sized_from_max_loss_usdt",
            "run_id": runtime.run_id}
    confidence = max(0.0, min(1.0, best["score"] / 100.0))
    return {"action": plan["side"], "symbol": best["symbol"], "confidence": confidence,
            "metrics": metrics, "meta": meta, "plan": plan}


def run() -> None:
    cfg = _cfg()
    decision = _decide(cfg)
    universe = [str(s).upper() for s in (cfg.get("trading_symbols") or [])
                if str(s).upper() != _GATE]
    max_concurrent = max(int(cfg.get("max_concurrent", 3)), 1)

    def _execute():
        plan = decision.get("plan")
        symbol = decision.get("symbol")
        if plan is None or not symbol:
            return {"placed": False, "reason": "no_plan"}
        # Per-symbol duplicate guard; unreadable exchange state is a stand-aside.
        exposed = _symbol_exposed(symbol)
        if exposed is None:
            return {"placed": False, "reason": "exposure_unreadable"}
        if exposed:
            return {"placed": False, "reason": "symbol_already_exposed"}
        # Best-effort portfolio cap across the universe.
        open_count = 0
        for other in universe:
            if other == symbol:
                continue
            state = _symbol_exposed(other)
            if state is None:
                return {"placed": False, "reason": "portfolio_state_unreadable"}
            if state:
                open_count += 1
                if open_count >= max_concurrent:
                    return {"placed": False, "reason": "max_concurrent_reached"}
        try:
            tpsl = trade.helpers.resolve_contract_tpsl(
                symbol=symbol, side=plan["side"], leverage=plan["leverage"],
                tp_trigger_price=str(plan["target"]), sl_trigger_price=str(plan["stop"]),
                reference_price=str(plan["entry"]))
            tp_px = getattr(tpsl, "tp_trigger_price", "") or str(plan["target"])
            sl_px = getattr(tpsl, "sl_trigger_price", "") or str(plan["stop"])
        except Exception:
            tp_px, sl_px = str(plan["target"]), str(plan["stop"])
        try:
            opener = (trade.contract.open_long_limit if plan["side"] == "long"
                      else trade.contract.open_short_limit)
            result = opener(symbol=symbol, qty=plan["qty"], price=str(plan["entry"]),
                            leverage=plan["leverage"], tp_trigger_price=tp_px,
                            sl_trigger_price=sl_px)
            ok = bool(getattr(result, "success", True))
            return {"placed": ok, "reason": "" if ok else str(result)[:80]}
        except Exception as exc:
            return {"placed": False, "reason": "exc_" + type(exc).__name__}

    runtime.emit_signal_or_follow(
        action=decision["action"], symbol=decision["symbol"],
        confidence=decision["confidence"], metrics=_sanitize(decision["metrics"]),
        meta=_sanitize(decision["meta"]), execute_trade=_execute,
    )


if __name__ == "__main__":
    run()
