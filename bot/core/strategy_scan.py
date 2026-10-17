"""Scan a strategy now: its own rule on the last closed 1h bars, placing nothing.

The live bot runs the house presets every cycle. The others (the Limit Entry
Scanner, the Multi-Symbol Momentum Trend, the moving-average books and the
daily rotation) are rules it does not run, and until this module nothing read
them live at all: `/run` shows their text, and only a frozen backtest ever
evaluated them. A scan reads each of the preset's markets' closed 1h bars
from Bitget's USDT-M perpetuals, runs the preset's own rule on them (the
same function its backtest runs) and returns a board: what the rule sees
and what it would do now. Nothing is placed, queued, or made confirmable.

The door is the preset's own alias (`/limitscan`, `/multitrend`, `/altsweep`,
`/ethma`, `/volrotation`) and the website's "Scan now"; `scan_command` is the
one reading of which presets have one.

A market whose bars could not be read, or whose newest closed bar is not the
hour's, is named unread with the exception class or the bar it stopped at,
and the rule runs on the rest: a composite view where one dead market must
not blank the others. The limit scanner's leader is the exception: an unread
leader opens nothing for its class, and the board says so.

One board per preset per closed hour. A second ask in the same hour is
answered from the first; a board with an unread market is kept a minute.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional

from bot.core.strategy_gate import _base, perp_symbol

if TYPE_CHECKING:
    from bot.core.limit_scanner import ScannerParams

logger = logging.getLogger(__name__)

HOUR_MS = 3_600_000
#: Fetches in flight at once, and the time one market and the whole scan get.
CONCURRENCY = 8
SYMBOL_TIMEOUT_S = 20.0
SCAN_TIMEOUT_S = 45.0
#: How long a board with an unread market is kept before a re-read.
PARTIAL_TTL_S = 60.0

LIMIT_ENTRY = "limit_entry"
MOMENTUM_TREND = "momentum_trend"
VOL_ROTATION = "vol_rotation"
MA_TREND = "ma_trend"
DAILY_TREND = "daily_trend"


def rule_of(cfg: Any) -> Optional[str]:
    """Which rule a preset is, or None for a house preset the live bot runs."""
    from bot.core.daily_trend import preset_is_daily_trend
    from bot.core.limit_scanner import preset_is_limit_scanner
    from bot.core.ma_trend import preset_is_ma_trend
    from bot.core.momentum_trend import preset_is_momentum_trend
    from bot.core.vol_rotation import preset_is_vol_rotation
    if not isinstance(cfg, dict):
        return None
    if preset_is_limit_scanner(cfg):
        return LIMIT_ENTRY
    if preset_is_daily_trend(cfg):
        return DAILY_TREND
    if preset_is_momentum_trend(cfg):
        return MOMENTUM_TREND
    if preset_is_vol_rotation(cfg):
        return VOL_ROTATION
    if preset_is_ma_trend(cfg):
        return MA_TREND
    return None


def scan_command(key: str) -> Optional[str]:
    """The slash command that scans this preset, or None.

    A preset the live bot does not run has one: its own alias. A house
    preset is scanned by the live bot every cycle, and `/run` is its door.
    """
    from bot.skills.skill_registry import RunStrategySkill
    cfg = RunStrategySkill.PRESETS.get(key)
    if rule_of(cfg) is None:
        return None
    for alias, target in RunStrategySkill.ALIASES.items():
        if target == key and alias.isalnum():
            return alias
    return None


def bars_needed(cfg: dict) -> Optional[int]:
    """Closed 1h bars one reading of this preset needs, or None."""
    rule = rule_of(cfg)
    if rule == LIMIT_ENTRY:
        from bot.core.limit_scanner import SNAPSHOT_BARS
        return SNAPSHOT_BARS
    if rule == MOMENTUM_TREND:
        from bot.core.momentum_trend import params_from_preset
        p = params_from_preset(cfg)
        return None if p is None else p.bars_needed + 1
    if rule == MA_TREND:
        hours = _hours_per_bar(cfg.get("ma_timeframe"), cfg.get("ma_source_timeframe"))
        if hours is None:
            return None
        # The slow average's bars, one more, and a group to spare: a window
        # that starts mid-group would make its first group a part-period's.
        return (int(cfg["slow_period"]) + 2) * hours
    if rule == DAILY_TREND:
        from bot.core.daily_trend import params_from_preset as _sleeve_params
        hours = _hours_per_bar(cfg.get("bar_timeframe"), cfg.get("bar_source_timeframe"))
        sleeve = _sleeve_params(cfg)
        if hours is None or sleeve is None:
            return None
        # The longest lookback's prior close, two days more, and a group to
        # spare: a window that starts mid-day would make its first day partial.
        return int((sleeve.days_needed + 2) * hours)
    if rule == VOL_ROTATION:
        hours = _hours_per_bar(cfg.get("bar_timeframe"), cfg.get("bar_source_timeframe"))
        periods: list[int] = []
        for key in ("momentum_period", "atr_period", "trend_period"):
            val = cfg.get(key)
            if isinstance(val, bool) or not isinstance(val, int):
                return None
            periods.append(val)
        if hours is None:
            return None
        return (max(periods) + 3) * hours
    return None


def _hours_per_bar(target: Any, source: Any) -> Optional[int]:
    """How many 1h bars one target bar is; 1 when the target is the 1h bar."""
    from bot.utils.candles import timeframe_to_ms
    tgt = str(target or "").strip() or "1h"
    src = str(source or "").strip() or "1h"
    if src != "1h":
        return None
    try:
        ratio = timeframe_to_ms(tgt) // HOUR_MS
    except Exception:  # noqa: BLE001 -- an unknown timeframe is unread
        return None
    return int(ratio) if ratio >= 1 else None


def last_closed_open_ms(now_ms: int) -> int:
    """The open time of the newest 1h bar that has closed at ``now_ms``."""
    return (now_ms // HOUR_MS) * HOUR_MS - HOUR_MS


# ── reading the bars ─────────────────────────────────────────────────────────

async def read_bars(exchange: Any, symbols: list[str], need: int,
                    expected_open_ms: int) -> tuple[dict[str, list], dict[str, str]]:
    """Closed 1h rows per symbol, and the ones that could not be read.

    A market is unread when its fetch raises (the class is named, never the
    message), when it times out, or when its newest closed bar is not the
    hour's (named by the bar it stopped at).
    """
    from bot.backtest.data_loader import fetch_closed_ohlcv

    sem = asyncio.Semaphore(CONCURRENCY)
    rows: dict[str, list] = {}
    unread: dict[str, str] = {}

    async def one(sym: str) -> None:
        async with sem:
            try:
                got = await asyncio.wait_for(
                    fetch_closed_ohlcv(exchange, perp_symbol(sym), "1h", need),
                    SYMBOL_TIMEOUT_S)
            except Exception as exc:  # noqa: BLE001 -- named, never its message
                unread[sym] = type(exc).__name__
                return
        if not got:
            unread[sym] = "no closed bars"
            return
        newest = int(got[-1][0])
        if newest != expected_open_ms:
            unread[sym] = "its newest bar closed at " + _hhmm(newest + HOUR_MS)
            return
        rows[sym] = got

    tasks = {sym: asyncio.ensure_future(one(sym)) for sym in symbols}
    done, pending = await asyncio.wait(tasks.values(), timeout=SCAN_TIMEOUT_S)
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    for sym, task in tasks.items():
        if task in pending:
            unread[sym] = "TimeoutError"
    return rows, unread


def _hhmm(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%d %b %H:%M UTC")


def _num(v: Optional[float], digits: int = 6) -> Optional[float]:
    """A finite float rounded to ``digits`` significant figures, or None."""
    if v is None or isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return float(f"{f:.{digits}g}")


# ── one board per rule ───────────────────────────────────────────────────────

def board_ma_trend(cfg: dict, rows: dict[str, list]) -> dict:
    """Each market's fast and slow average and the side they name."""
    from bot.core.ma_trend import closed_ohlcv, ma_trend_step
    fast_p, slow_p = int(cfg["fast_period"]), int(cfg["slow_period"])
    target = str(cfg.get("ma_timeframe") or "1h")
    out = []
    for sym in _listed(cfg):
        if sym not in rows:
            continue
        bars = closed_ohlcv(rows[sym], "1h", target)
        closes = [r[4] for r in bars] if bars else []
        step = ma_trend_step(closes, fast_p, slow_p, None)
        out.append({"symbol": sym, "fast": _num(step["fast"]), "slow": _num(step["slow"]),
                    "side": step["side"]})
    longs = [r["symbol"] for r in out if r["side"] == "LONG"]
    shorts = [r["symbol"] for r in out if r["side"] == "SHORT"]
    none = [r["symbol"] for r in out if r["side"] is None]
    parts = []
    if longs:
        parts.append("long " + ", ".join(longs))
    if shorts:
        parts.append("short " + ", ".join(shorts))
    if none:
        parts.append("no side for " + ", ".join(none))
    verdict = ("The averages name: " + "; ".join(parts) + ".") if parts else \
        "No market was read, so the averages name nothing."
    return {"rows": out, "verdict": verdict}


def board_momentum_trend(cfg: dict, rows: dict[str, list]) -> dict:
    """Each market's averages, momentum and signal."""
    from bot.core.momentum_trend import momentum_signal, params_from_preset
    p = params_from_preset(cfg)
    if p is None:
        return {"rows": [], "verdict": "Its numbers could not be read, so no scan runs it."}
    out = []
    signals: list[str] = []
    for sym in _listed(cfg):
        if sym not in rows:
            continue
        r = momentum_signal(sym, [row[4] for row in rows[sym]], p)
        out.append({"symbol": sym, "fast": _num(r.fast), "slow": _num(r.slow),
                    "momentum_pct": _num(r.momentum_pct, 3), "trend": r.trend,
                    "signal": r.signal, "reason": r.reason})
        if r.signal:
            signals.append(f"{r.signal.lower()} {sym}")
    verdict = ("Signals now: " + ", ".join(signals) + ".") if signals else \
        "No market has a signal now."
    return {"rows": out, "verdict": verdict}


def board_vol_rotation(cfg: dict, rows: dict[str, list]) -> dict:
    """The rotation's reading of every market, and the one it would hold."""
    from bot.core.vol_rotation import rotate
    result = rotate(rows, cfg)
    out = []
    for sym in _listed(cfg):
        read = result["reads"].get(sym)
        if read is None:
            continue
        out.append({"symbol": sym, "momentum": _num(read.get("momentum"), 4),
                    "above_average": read.get("above_average"),
                    "qualifies": bool(read.get("qualifies"))})
    chosen = result.get("chosen")
    if chosen:
        verdict = f"It would hold {_base(chosen)}: the qualifier with the highest momentum."
    elif result.get("qualifiers"):
        verdict = "The highest momentum is shared, so it names no market."
    else:
        verdict = "No market qualifies, so it would hold nothing."
    return {"rows": out, "verdict": verdict}


def board_daily_trend(cfg: dict, rows: dict[str, list]) -> dict:
    """Each market's trend score, vol and the weight the sleeve would hold."""
    from bot.core.daily_trend import read_book
    book = read_book(rows, cfg)
    if book["params"] is None:
        return {"rows": [], "verdict": "Its numbers could not be read, so no scan runs it."}
    out = []
    for sym in _listed(cfg):
        r = book["reads"].get(sym)
        if r is None:
            continue
        out.append({"symbol": sym, "score": _num(r.score, 3),
                    "vol_pct": None if r.vol is None else _num(r.vol * 100, 3),
                    "weight_pct": None if r.weight is None else _num(r.weight * 100, 3),
                    "reason": r.reason})
    holds = book["holds"]
    if holds:
        parts = ", ".join(f"{s} {book['reads'][s].weight * 100:.1f}%" for s in holds)
        verdict = (f"It would hold {len(holds)} of {book['n_book']} markets at "
                   f"{book['gross'] * 100:.0f}% of notional: {parts}.")
    elif book["n_book"]:
        verdict = "No market has a positive trend score, so it would hold nothing."
    else:
        verdict = "No market read, so it names no book."
    return {"rows": out, "verdict": verdict}


def board_limit_entry(cfg: dict, rows: dict[str, list]) -> dict:
    """Each class's leader, its scored board and the limit it would rest."""
    from bot.core import limit_scanner as ls
    params = ls.params_from_preset(cfg)
    if params is None:
        return {"rows": [], "classes": [],
                "verdict": "Its numbers could not be read, so no scan runs it."}
    snaps = [ls.snapshot_from_bars(perp_symbol(sym), rows[sym], params)
             for sym in _listed(cfg) if sym in rows]
    classes = []
    verdicts = []
    for name, result in ls.scan_classes(snaps, params):
        board = sorted(result.board, key=lambda s: -s.points)
        plan = result.plan
        classes.append({
            "name": name,
            "leader": _leader_of(params, name),
            "side": result.regime.side,
            "size_factor": result.regime.size_factor,
            "regime": result.regime.reason,
            "board": [{"symbol": _base(s.symbol), "side": s.side,
                       "points": _num(s.points, 3),
                       "out_of": sum(ls.WEIGHTS[k] for k, v in s.dims.items() if v is not None),
                       "skip": s.skip or None} for s in board[:10]],
            "plan": None if plan is None else {
                "symbol": _base(plan.symbol), "side": plan.side,
                "limit": _num(plan.limit_price), "stop": _num(plan.stop),
                "target": _num(plan.take_profit), "points": _num(plan.points, 3),
                "size_factor": plan.size_factor},
            "reason": result.reason,
        })
        label = "" if name == "all" else f"{name}: "
        if plan is not None:
            verb = "buy" if plan.side == ls.LONG else "sell"
            verdicts.append(f"{label}it would rest a {verb} limit on {_base(plan.symbol)} at "
                            f"{_num(plan.limit_price):g}, stop {_num(plan.stop):g}, first "
                            f"target {_num(plan.take_profit):g}")
        else:
            verdicts.append(f"{label}{result.reason.split(': ', 1)[-1]}")
    return {"rows": [], "classes": classes, "verdict": "; ".join(verdicts) + "."}


def _leader_of(params: "ScannerParams", name: str) -> str:
    for c in params.classes:
        if c.name == name:
            return c.leader
    return params.leader


def _listed(cfg: dict) -> list[str]:
    syms = cfg.get("symbols")
    return [_base(s) for s in syms] if isinstance(syms, (list, tuple)) else []


BOARDS = {
    LIMIT_ENTRY: board_limit_entry,
    MOMENTUM_TREND: board_momentum_trend,
    VOL_ROTATION: board_vol_rotation,
    MA_TREND: board_ma_trend,
    DAILY_TREND: board_daily_trend,
}


# ── the scan ─────────────────────────────────────────────────────────────────

_CACHE: dict[str, tuple[int, float, dict]] = {}
_LOCKS: dict[str, asyncio.Lock] = {}


class NoScan(LookupError):
    """The preset has no scan door: it is unknown, or the live bot runs it."""


async def scan(engine: Any, key: str, *, now_ms: Optional[int] = None) -> dict:
    """The board for ``key`` at the newest closed 1h bar.

    Raises ``NoScan`` for a preset with no scan door. A failure to reach the
    venue at all propagates; a market that does not read is named on the
    board.
    """
    from bot.skills.skill_registry import RunStrategySkill
    cfg = RunStrategySkill.PRESETS.get(key)
    rule = rule_of(cfg)
    command = scan_command(key)
    need = bars_needed(cfg) if cfg else None
    if cfg is None or rule is None or command is None or need is None:
        raise NoScan(key)
    now = int(time.time() * 1000) if now_ms is None else now_ms
    expected = last_closed_open_ms(now)
    lock = _LOCKS.setdefault(key, asyncio.Lock())
    async with lock:
        hit = _CACHE.get(key)
        if hit is not None and hit[0] == expected:
            board = hit[2]
            if not board["unread"] or time.monotonic() - hit[1] < PARTIAL_TTL_S:
                return board
        exchange = await engine.scanner._get_futures_exchange()
        rows, unread = await read_bars(exchange, _listed(cfg), need, expected)
        body = BOARDS[rule](cfg, rows)
        board = {
            "preset": key,
            "label": cfg.get("label") or key,
            "command": "/" + command,
            "rule": rule,
            "bar_close": _hhmm(expected + HOUR_MS),
            "bar_close_ms": expected + HOUR_MS,
            "read": sorted(rows),
            "unread": [{"symbol": s, "why": unread[s]} for s in _listed(cfg) if s in unread],
            "places": False,
            **body,
        }
        _CACHE[key] = (expected, time.monotonic(), board)
        return board


# ── the Telegram card ────────────────────────────────────────────────────────

def _esc(text: Any) -> str:
    import html
    return html.escape(str(text), quote=False)


def render_text(board: dict) -> str:
    """The board as a Telegram HTML card."""
    lines = [f"\U0001f50e <b>{_esc(board['label'])}</b> — scan at the "
             f"{_esc(board['bar_close'])} close"]
    rule = board["rule"]
    if rule == LIMIT_ENTRY:
        for c in board.get("classes", []):
            head = "" if c["name"] == "all" else f"<b>{_esc(c['name'])}</b> — "
            side = c["side"].lower() if c["side"] else "no side"
            lines.append(f"\n{head}{_esc(c['leader'])}: {side} ({_esc(c['regime'])})")
            for s in c["board"][:5]:
                skip = f" — {_esc(s['skip'])}" if s["skip"] else ""
                lines.append(f"  {_esc(s['symbol'])} {s['side'].lower()} "
                             f"{s['points']:g}/{s['out_of']:g}{skip}")
    elif rule == MOMENTUM_TREND:
        for r in board["rows"]:
            mom = "unread" if r["momentum_pct"] is None else f"{r['momentum_pct']:+g}%"
            lines.append(f"  {_esc(r['symbol'])}: momentum {mom} — {_esc(r['reason'])}")
    elif rule == VOL_ROTATION:
        for r in board["rows"]:
            mom = "unread" if r["momentum"] is None else f"{r['momentum'] * 100:+.1f}%"
            above = {True: "above", False: "not above", None: "unread vs"}[r["above_average"]]
            q = "qualifies" if r["qualifies"] else "does not qualify"
            lines.append(f"  {_esc(r['symbol'])}: momentum {mom}, {above} its average, {q}")
    elif rule == MA_TREND:
        for r in board["rows"]:
            side = r["side"].lower() if r["side"] else "no side"
            lines.append(f"  {_esc(r['symbol'])}: {side}")
    elif rule == DAILY_TREND:
        for r in board["rows"]:
            if r["score"] is None:
                lines.append(f"  {_esc(r['symbol'])}: {_esc(r['reason'])}")
                continue
            w = "unread" if r["weight_pct"] is None else f"{r['weight_pct']:.1f}%"
            lines.append(f"  {_esc(r['symbol'])}: trend score {r['score']:.2f}, weight {w}")
    lines.append(f"\n<b>{_esc(board['verdict'])}</b>")
    if board["unread"]:
        names = ", ".join(f"{_esc(u['symbol'])} ({_esc(u['why'])})" for u in board["unread"])
        lines.append(f"Unread, so not on the board: {names}.")
    lines.append("<i>Nothing is placed or queued: the live bot does not run this rule. "
                 "Its card has the frozen backtest.</i>")
    return "\n".join(lines)


def scan_command_for(cfg: Any) -> Optional[str]:
    """``scan_command`` for a preset's config: the preset whose config this is."""
    from bot.skills.skill_registry import RunStrategySkill
    for key, preset in RunStrategySkill.PRESETS.items():
        if preset is cfg:
            return scan_command(key)
    return None
