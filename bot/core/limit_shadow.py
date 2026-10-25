"""Shadow book for the Limit Entry Scanner, and the forward test's rules.

The playbook places nothing here. A pass writes the order it would have
rested and the fill the model would have given it. The forward test scores
only trades whose fill is strictly after 2026-10-23. A trade on or before
that instant refuses the score. An empty window is not a zero.

The four variants below are the pre-registered set. Their count is the N
the deflated Sharpe uses. Adding a variant after the window has been read
is a new test, not a member of this one.

Fees are basis points of notional, per side. Funding is priced only when a
rate was supplied. Unpriced funding is not zero, and a forward verdict
cannot be a pass while it is unpriced.
"""

from __future__ import annotations

import csv
import gzip
import json
import math
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping, Optional, Sequence

from bot.backtest.limit_fill import decide_resting_fill
from bot.core.limit_entry import resting_limit_drift
from bot.core.limit_scanner import (
    LONG,
    ExitState,
    ScannerParams,
    class_of,
    portfolio_cap,
    scan_classes,
    snapshot_from_bars,
    step_exit,
)

FORWARD_CUTOFF = "2026-10-23T00:00:00+00:00"
FORWARD_CUTOFF_MS = int(datetime(2026, 10, 23, tzinfo=timezone.utc).timestamp() * 1000)
# The spent window the internal review already read. A forward trade may not
# use it. Reported beside the forward test, never as its result.
HOLDOUT_START_MS = int(datetime(2026, 4, 24, tzinfo=timezone.utc).timestamp() * 1000)
N_PREREGISTERED = 4
MIN_TRADES = 100
DSR_MIN = 0.95
MAKER_BP = 2.0
# Four hours, the live limit's expiry and the engine's default.
REST_BARS = 4
DRIFT_CANCEL_PCT = 2.0

# Names fixed before any forward result. The fill is the realistic one:
# 5 bp of trade-through, a queue that fills half of mere qualifications and
# every trade-through of 10 bp, maker 2 bp a side.
PREREG_VARIANTS = ("V0", "V1", "V2", "V3")


@dataclass(frozen=True)
class FillSpec:
    """How a resting limit becomes a position. All knobs off is a touch."""

    name: str = "touch"
    through_bp: Optional[float] = None
    queue_p: Optional[float] = None
    queue_sure_bp: Optional[float] = None
    reclaim: bool = False
    entry_fee_bp: float = MAKER_BP
    exit_fee_bp: float = MAKER_BP


REALISTIC = FillSpec("realistic", through_bp=5.0, queue_p=0.5, queue_sure_bp=10.0)
CARD_TOUCH = FillSpec("card_touch", entry_fee_bp=11.0, exit_fee_bp=6.0)


def variant_params(base: ScannerParams, name: str) -> tuple[ScannerParams, FillSpec]:
    """One pre-registered variant. An unknown name is not a silent V0."""
    if name == "V0":
        return base, REALISTIC
    if name == "V1":
        return replace(base, reclaim_fill=True), replace(REALISTIC, name="reclaim", reclaim=True)
    if name == "V2":
        return replace(base, vol_entry_offset=0.25), replace(REALISTIC, name="vol_offset")
    if name == "V3":
        return replace(base, reclaim_fill=True, neutral_on_tie=True), replace(
            REALISTIC, name="reclaim_neutral", reclaim=True)
    raise KeyError(name)


class _Bar:
    def __init__(self, ts_ms: int, o: float, h: float, low: float, c: float):
        self.timestamp = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)
        self.open, self.high, self.low, self.close = o, h, low, c


class _Cfg:
    def __init__(self, spec: FillSpec):
        self.limit_through_bp = spec.through_bp
        self.limit_queue_p = spec.queue_p
        self.limit_queue_sure_bp = spec.queue_sure_bp
        self.limit_reclaim = spec.reclaim
        self.limit_maker_entry = True
        self.limit_cancel_adverse_bp = None


class _Idea:
    def __init__(self, asset: str, side: str):
        self.asset = asset
        self.direction = side


def load_bars(dataset: Path) -> tuple[dict[str, list[tuple]], list[str]]:
    """Closed 1h bars keyed by the manifest's symbol, and symbols that failed.

    A row that does not parse is skipped and the symbol is named in the
    second list if nothing readable remains. A missing file is a failed
    symbol, not an empty series treated as a flat market.
    """
    manifest = json.loads((dataset / "manifest.json").read_text(encoding="utf-8"))
    out: dict[str, list[tuple]] = {}
    failed: list[str] = []
    for sym, entry in manifest.get("symbols", {}).items():
        path = dataset / entry["file"]
        try:
            rows = _read_symbol(path)
        except (OSError, ValueError, KeyError, csv.Error):
            failed.append(sym)
            continue
        if not rows:
            failed.append(sym)
            continue
        out[sym] = rows
    return out, failed


def _read_symbol(path: Path) -> list[tuple]:
    rows = []
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for rec in csv.DictReader(handle):
            try:
                ts = datetime.fromisoformat(rec["timestamp"])
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                o, h, low, c = (float(rec["open"]), float(rec["high"]),
                                float(rec["low"]), float(rec["close"]))
                v = float(rec["volume"])
            except (KeyError, TypeError, ValueError):
                continue
            if min(o, h, low, c) <= 0 or h < low:
                continue
            rows.append((int(ts.timestamp() * 1000), o, h, low, c, v))
    rows.sort(key=lambda r: r[0])
    return rows


def simulate(bars: Mapping[str, Sequence[tuple]], params: ScannerParams,
             spec: FillSpec, *, cancel_adverse_bp: float = 0.0) -> dict:
    """Rule-only book. The house risk gate is not in this path.

    An order decided at a bar's close can fill only on a later bar. On the
    fill bar the favourable extreme is clamped to the fill, so a target
    cannot print from a wick that happened before the order existed. The
    adverse extreme is not clamped.
    """
    stamps = sorted({row[0] for rows in bars.values() for row in rows})
    index = {sym: 0 for sym in bars}
    resting: list[dict] = []
    positions: dict[str, ExitState] = {}
    meta: dict[str, dict] = {}
    orders: list[dict] = []
    trades: list[dict] = []
    cfg = _Cfg(spec)
    for ts in stamps:
        fresh = []
        window: dict[str, list] = {}
        live_bar: dict[str, tuple] = {}
        for sym, rows in bars.items():
            i = index[sym]
            while i < len(rows) and rows[i][0] <= ts:
                i += 1
            index[sym] = i
            if i == 0 or rows[i - 1][0] != ts:
                continue
            fresh.append(sym)
            window[sym] = [list(r) for r in rows[max(0, i - 64):i]]
            live_bar[sym] = rows[i - 1]
        _mark_to_market(resting, positions, meta, live_bar, params, spec, cfg, trades)
        if not fresh:
            continue
        snaps = [snapshot_from_bars(sym, window[sym], params) for sym in fresh]
        held = list(positions) + [o["symbol"] for o in resting]
        for _name, result in scan_classes(snaps, params, frozenset(held)):
            plan = result.plan
            if plan is None:
                continue
            why = portfolio_cap(params, plan.symbol, held)
            if why is not None:
                continue
            bar = live_bar.get(plan.symbol)
            snap = next((s for s in snaps if s.symbol == plan.symbol), None)
            if bar is None or snap is None:
                continue
            order = {
                "symbol": plan.symbol,
                "side": plan.side,
                "px": plan.limit_price,
                "stop": plan.stop,
                "take_profit": plan.take_profit,
                "atr": snap.atr,
                "points": plan.points,
                "placed_ts": ts / 1000.0,
                "placed_ms": ts,
                "placed_close": bar[4],
                "class": class_of(params, plan.symbol),
                "bars_waited": 0,
            }
            if params.reclaim_fill or spec.reclaim:
                order["reclaim"] = True
            adverse = params.cancel_adverse_bp if params.cancel_adverse_bp > 0 else cancel_adverse_bp
            if adverse > 0:
                order["cancel_adverse_bp"] = adverse
            resting.append(order)
            held.append(plan.symbol)
            orders.append({
                "kind": "order",
                "symbol": plan.symbol,
                "side": plan.side,
                "limit": plan.limit_price,
                "decision_ms": ts,
                "points": plan.points,
                "class": order["class"],
            })
    # A limit still resting, or a position still open, is not a trade.
    return {
        "orders": orders,
        "trades": trades,
        "resting": len(resting),
        "open": len(positions),
    }


def _mark_to_market(resting, positions, meta, live_bar, params, spec, cfg, trades) -> None:
    still = []
    for order in resting:
        bar = live_bar.get(order["symbol"])
        if bar is None:
            still.append(order)
            continue
        view = _Bar(*bar[:5])
        decision = decide_resting_fill(cfg, {
            "idea": _Idea(order["symbol"], order["side"]),
            "px": order["px"],
            "placed_ts": order["placed_ts"],
            "reclaim": order.get("reclaim"),
            "cancel_adverse_bp": order.get("cancel_adverse_bp"),
        }, view)
        filled = decision == "fill" or (
            decision == "legacy" and _touch(bar, order["px"], order["side"]))
        if decision == "cancel":
            continue
        if filled and bar[0] > order["placed_ms"]:
            _open_position(order, bar, positions, meta, params, spec, trades)
            continue
        order["bars_waited"] += 1
        if order["bars_waited"] >= REST_BARS:
            continue
        drift = resting_limit_drift(order["side"], order["px"], bar[4], order["placed_close"])
        if drift is not None and drift[0] > DRIFT_CANCEL_PCT:
            continue
        still.append(order)
    resting[:] = still
    for sym in list(positions):
        bar = live_bar.get(sym)
        if bar is None:
            continue
        state = positions[sym]
        # The fill bar was already stepped with its favourable side clamped.
        if state.opened_ms == bar[0]:
            continue
        fills = step_exit(state, params, open_=bar[1], high=bar[2], low=bar[3],
                          close=bar[4], close_ms=bar[0])
        _book_exits(sym, fills, positions, meta, spec, trades)


def _touch(bar, px: float, side: str) -> bool:
    if side == LONG:
        return bar[3] <= px
    return bar[2] >= px


def _open_position(order, bar, positions, meta, params, spec, trades) -> None:
    if order["symbol"] in positions:
        return
    px = order["px"]
    if order["side"] == LONG:
        high, low = min(bar[2], px), bar[3]
    else:
        high, low = bar[2], max(bar[3], px)
    state = ExitState(side=order["side"], entry=px, stop=order["stop"],
                      atr=order["atr"], opened_ms=bar[0])
    positions[order["symbol"]] = state
    info = {
        "symbol": order["symbol"],
        "side": order["side"],
        "entry": px,
        "fill_ms": bar[0],
        "decision_ms": order["placed_ms"],
        "points": order["points"],
        "class": order["class"],
        "gross_bp": 0.0,
        "exit_share": 0.0,
        "reasons": [],
    }
    meta[order["symbol"]] = info
    fills = step_exit(state, params, open_=bar[1], high=high, low=low,
                      close=bar[4], close_ms=bar[0])
    _book_exits(order["symbol"], fills, positions, meta, spec, trades)


def _book_exits(sym, fills, positions, meta, spec, trades) -> None:
    info = meta.get(sym)
    state = positions.get(sym)
    if info is None or state is None:
        return
    entry = info["entry"]
    for fill in fills:
        move = (fill.price - entry) / entry
        if info["side"] != LONG:
            move = -move
        info["gross_bp"] += fill.share * move * 10_000.0
        info["exit_share"] += fill.share
        info["reasons"].append(fill.reason)
    if state.remaining > 0:
        return
    gross = info["gross_bp"]
    fee = spec.entry_fee_bp + spec.exit_fee_bp * min(info["exit_share"], 1.0)
    trades.append({
        "symbol": sym,
        "side": info["side"],
        "fill_ms": info["fill_ms"],
        "decision_ms": info["decision_ms"],
        "points": info["points"],
        "class": info["class"],
        "gross_bp": gross,
        "fee_bp": fee,
        "net_bp": gross - fee,
        "funding": "unpriced",
        "reasons": info["reasons"],
    })
    positions.pop(sym, None)
    meta.pop(sym, None)


def slice_trades(trades: Sequence[dict], start_ms: int, end_ms: Optional[int]) -> list[dict]:
    out = []
    for trade in trades:
        ts = int(trade["fill_ms"])
        if ts < start_ms:
            continue
        if end_ms is not None and ts >= end_ms:
            continue
        out.append(trade)
    return out


def summarize(trades: Sequence[dict]) -> dict:
    """Counts and the mean. An empty list is not a zero mean."""
    n = len(trades)
    symbols = sorted({t["symbol"] for t in trades})
    if n == 0:
        return {
            "n": 0, "mean_net_bp": None, "lower_bp": None, "upper_bp": None,
            "gross_bp": None, "profit_factor": None, "win_rate": None,
            "symbols": symbols, "funding": "unpriced",
        }
    nets = [float(t["net_bp"]) for t in trades]
    grosses = [float(t["gross_bp"]) for t in trades]
    mu, lo, hi = _ci(nets)
    wins = [x for x in nets if x > 0]
    losses = [x for x in nets if x < 0]
    pf = None
    if losses:
        pf = sum(wins) / abs(sum(losses)) if wins else 0.0
    return {
        "n": n,
        "mean_net_bp": mu,
        "lower_bp": lo,
        "upper_bp": hi,
        "gross_bp": sum(grosses) / n,
        "profit_factor": pf,
        "win_rate": len(wins) / n,
        "symbols": symbols,
        "symbol_counts": {s: sum(1 for t in trades if t["symbol"] == s) for s in symbols},
        "funding": "unpriced" if any(t.get("funding") == "unpriced" for t in trades) else "priced",
    }


def evaluate_forward(trades: Sequence[dict], *, funding_priced: bool,
                     n_trials: int = N_PREREGISTERED) -> dict:
    """The pre-registered verdict. Old trades refuse the call.

    Empty, unreadable and unpriced are not a measured zero and not a pass.
    """
    for trade in trades:
        if int(trade["fill_ms"]) <= FORWARD_CUTOFF_MS:
            return {
                "evaluable": False,
                "verdict": "refused",
                "reason": "a trade is on or before 2026-10-23",
                "mean_net_bp": None,
                "n": None,
            }
        if int(trade["decision_ms"]) >= int(trade["fill_ms"]):
            return {
                "evaluable": False,
                "verdict": "refused",
                "reason": "a fill is not after its decision",
                "mean_net_bp": None,
                "n": None,
            }
    stats = summarize(trades)
    base = {
        "evaluable": False,
        "verdict": "inconclusive",
        "n": stats["n"],
        "mean_net_bp": stats["mean_net_bp"],
        "lower_bp": stats["lower_bp"],
        "upper_bp": stats["upper_bp"],
        "dsr": None,
        "funding": "priced" if funding_priced else "unpriced",
        "cutoff": FORWARD_CUTOFF,
        "n_trials": n_trials,
    }
    if stats["n"] == 0:
        base["reason"] = "no closed trades after 2026-10-23"
        base["mean_net_bp"] = None
        return base
    if not funding_priced:
        base["reason"] = "funding is unpriced"
        return base
    if stats["n"] < MIN_TRADES:
        base["reason"] = f"fewer than {MIN_TRADES} closed trades"
        return base
    nets = [float(t["net_bp"]) for t in trades]
    dsr = deflated_sharpe(nets, n_trials)
    base["dsr"] = dsr
    lo = stats["lower_bp"]
    hi = stats["upper_bp"]
    if lo is None or hi is None or dsr is None:
        base["reason"] = "the interval or the deflated Sharpe could not be read"
        return base
    if lo > 0 and dsr >= DSR_MIN:
        return {**base, "evaluable": True, "verdict": "survives", "reason": ""}
    if hi < 0:
        return {**base, "evaluable": True, "verdict": "refuted", "reason": "upper bound is below zero"}
    base["reason"] = "the lower bound is not above zero, or the deflated Sharpe is below the bar"
    return base


def book_overlap(symbols: Iterable[str], *, b1v: Optional[Sequence[str]],
                 stocks: Optional[Sequence[str]]) -> dict:
    """How the traded names sit on the two books this must not change.

    A book that could not be read is omitted, not counted as zero overlap.
    """
    traded = sorted({_base(s) for s in symbols})
    out: dict = {"traded": traded}
    if b1v is None:
        out["b1v"] = {"readable": False}
    else:
        known = {_base(s) for s in b1v}
        hit = [s for s in traded if s in known]
        out["b1v"] = {"readable": True, "overlap": hit, "n": len(hit), "of": len(traded)}
    if stocks is None:
        out["tokenized_equity"] = {"readable": False}
    else:
        known = {_base(s) for s in stocks}
        hit = [s for s in traded if s in known]
        out["tokenized_equity"] = {"readable": True, "overlap": hit, "n": len(hit), "of": len(traded)}
    return out


def _base(symbol: str) -> str:
    from bot.core.strategy_gate import _base as base
    return base(symbol)


def _ci(values: Sequence[float]) -> tuple[Optional[float], Optional[float], Optional[float]]:
    n = len(values)
    if n == 0:
        return None, None, None
    mu = sum(values) / n
    if n < 2:
        return mu, None, None
    var = sum((x - mu) ** 2 for x in values) / (n - 1)
    se = math.sqrt(var / n)
    return mu, mu - 1.96 * se, mu + 1.96 * se


def deflated_sharpe(values: Sequence[float], trials: int) -> Optional[float]:
    """Probability the true per-trade Sharpe exceeds 0, after ``trials`` looks.

    Bailey and López de Prado. The Sharpe is mean over sample standard
    deviation of the per-trade net basis points, not an annualised figure.
    Kurtosis is the raw fourth moment (a normal sample is about 3).
    """
    n = len(values)
    if n < 3 or trials < 2:
        return None
    mu = sum(values) / n
    var = sum((x - mu) ** 2 for x in values) / (n - 1)
    if var <= 0:
        return None
    sd = math.sqrt(var)
    sr = mu / sd
    m3 = sum((x - mu) ** 3 for x in values) / n
    m4 = sum((x - mu) ** 4 for x in values) / n
    skew = m3 / (sd ** 3)
    kurt = m4 / (sd ** 4)
    inside = 1.0 - skew * sr + ((kurt - 1.0) / 4.0) * sr * sr
    if inside <= 0:
        return None
    sr_std = math.sqrt(inside / (n - 1))
    if sr_std <= 0:
        return None
    emc = 0.5772156649015329
    z1 = _ppf(1.0 - 1.0 / trials)
    z2 = _ppf(1.0 - 1.0 / (trials * math.e))
    if z1 is None or z2 is None:
        return None
    sr0 = sr_std * ((1.0 - emc) * z1 + emc * z2)
    return _phi((sr - sr0) / sr_std)


def _phi(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def _ppf(p: float) -> Optional[float]:
    """Acklam's inverse normal. None outside (0, 1)."""
    if not 0.0 < p < 1.0:
        return None
    a = [ -3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
          1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
          6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    plow = 0.02425
    phigh = 1 - plow
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
               ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
                ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    q = p - 0.5
    r = q * q
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5]) * q / \
           (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)
