"""Daily trend sleeve, long-only, on closed daily bars.

The one rule in this project's line of pre-registered studies that passed its
bar (the engineering log's "THE DAILY TREND RULE PASSED ON KRAKEN'S HISTORY"):
per market, the trend score is the mean of the signs of its returns over the
lookbacks (20, 60 and 120 days), floored at zero because the sleeve is long
only. A market with a positive score is held at ``score × (target vol / n)
/ its own 30-day realised vol``, capped at one times notional, where ``n`` is
the number of markets readable that day; the book is rebalanced only when a
target moves by more than the band. No stop and no target: the score falls to
zero and the market is sold.

This module is one reading, ``read_book``. It opens nothing, sizes no fill and
exits nothing; the scan door and `/run` state what it would hold. Its numbers
live under the preset's own ``daily_trend`` key. None is unread, never a zero
score and never a zero weight.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Optional, Sequence

from bot.core.equity_record import ALREADY, RECORDED, UNREADABLE, last_recorded_day
from bot.core.ma_trend import closed_ohlcv
from bot.core.strategy_gate import _base
from bot.utils.paths import env_state_path

#: The preset key that holds this rule's numbers.
RULE_KEY = "daily_trend"
LONG = "LONG"
#: The engine's word for a tick whose scan phase opened no venue: the reading
#: rides that connection and opens none of its own, so nothing was asked.
NOT_ASKED = "not_asked"
#: Days in a year the vol is annualised over: crypto trades every day.
_DAYS = 365


@dataclass(frozen=True)
class SleeveParams:
    lookbacks: tuple[int, ...] = (20, 60, 120)
    vol_window: int = 30
    target_vol: float = 0.20
    band: float = 0.10
    cap: float = 1.0

    @property
    def days_needed(self) -> int:
        """Closed daily bars one reading needs: the longest lookback's prior
        close, or the vol window's returns, whichever is further back."""
        return max(max(self.lookbacks) + 1, self.vol_window + 1)


def preset_is_daily_trend(cfg: Any) -> bool:
    """True when this preset carries the daily trend sleeve."""
    return isinstance(cfg, dict) and isinstance(cfg.get(RULE_KEY), dict)


def _pos_int(val: Any) -> Optional[int]:
    if isinstance(val, bool) or not isinstance(val, int) or val < 1:
        return None
    return int(val)


def _unit(val: Any, *, closed_top: bool) -> Optional[float]:
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        return None
    f = float(val)
    if not math.isfinite(f) or f < 0 or (f > 1 if closed_top else f >= 1):
        return None
    return f


def params_from_preset(cfg: Any) -> Optional[SleeveParams]:
    """The sleeve's numbers, or None when any of them does not read.

    Lookbacks are strictly increasing positive ints, the vol window a positive
    int, the target vol in (0, 1], the band in [0, 1) and the cap in (0, 1].
    A key the rule does not read is refused rather than carried.
    """
    if not preset_is_daily_trend(cfg):
        return None
    raw = cfg[RULE_KEY]
    known = {"lookbacks", "vol_window", "target_vol", "band", "cap"}
    if set(raw) - known:
        return None
    kw: dict[str, Any] = {}
    if "lookbacks" in raw:
        lbs = raw["lookbacks"]
        if not isinstance(lbs, (list, tuple)) or not lbs:
            return None
        ints: list[int] = []
        for v in lbs:
            i = _pos_int(v)
            if i is None:
                return None
            ints.append(i)
        if any(b <= a for a, b in zip(ints, ints[1:])):
            return None
        kw["lookbacks"] = tuple(ints)
    if "vol_window" in raw:
        w = _pos_int(raw["vol_window"])
        if w is None or w < 2:
            return None
        kw["vol_window"] = w
    for key, closed_top in (("target_vol", True), ("band", False), ("cap", True)):
        if key in raw:
            f = _unit(raw[key], closed_top=closed_top)
            if f is None or (key != "band" and f == 0.0):
                return None
            kw[key] = f
    return SleeveParams(**kw)


def _close(raw: Any) -> Optional[float]:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    value = float(raw)
    if not math.isfinite(value) or value <= 0:
        return None
    return value


def trend_score(closes: Sequence[Any], lookbacks: Sequence[int]) -> Optional[float]:
    """Mean of the signs of the returns over ``lookbacks``, floored at zero
    (long only), or None when a close the rule needs does not read."""
    seq = list(closes or [])
    last = _close(seq[-1]) if seq else None
    if last is None:
        return None
    signs = []
    for lb in lookbacks:
        if len(seq) < lb + 1:
            return None
        prior = _close(seq[-1 - lb])
        if prior is None:
            return None
        r = last / prior - 1.0
        signs.append(0.0 if r == 0 else (1.0 if r > 0 else -1.0))
    return max(0.0, sum(signs) / len(signs))


def realised_vol(closes: Sequence[Any], window: int) -> Optional[float]:
    """Annualised standard deviation of the last ``window`` daily returns, or
    None when a close does not read or the returns are fewer than two."""
    seq = list(closes or [])
    if len(seq) < window + 1:
        return None
    vals = [_close(v) for v in seq[-(window + 1):]]
    if any(v is None for v in vals):
        return None
    prices = [v for v in vals if v is not None]
    rets = [b / a - 1.0 for a, b in zip(prices, prices[1:])]
    if len(rets) < 2:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    sd = math.sqrt(var) * math.sqrt(_DAYS)
    return sd if sd > 0 else None


def target_weight(score: Optional[float], vol: Optional[float], n_book: int,
                  p: SleeveParams) -> Optional[float]:
    """The share of notional the sleeve would hold in one market, or None when
    its score or vol is unread. A zero score is a zero weight: a measurement."""
    if score is None or vol is None or n_book < 1:
        return None
    return min(p.cap, score * (p.target_vol / n_book) / vol)


@dataclass(frozen=True)
class Reading:
    """One market at one closed daily bar. None is unread."""

    symbol: str
    score: Optional[float]
    vol: Optional[float]
    weight: Optional[float]
    daily_bars: Optional[int]
    reason: str


def _closes(rows: Any) -> Optional[list]:
    try:
        return [r[4] for r in rows]
    except (TypeError, IndexError, KeyError):
        return None


def read_book(bars_by_symbol: dict, cfg: dict) -> dict:
    """Read every listed market and the book the sleeve would hold.

    ``n`` for the vol split is the number of listed markets whose score and
    vol read today, the universe "that day" of the pre-registration. Does not
    open, size a fill or exit.
    """
    p = params_from_preset(cfg)
    source = str(cfg.get("bar_source_timeframe") or "1h")
    target = str(cfg.get("bar_timeframe") or "1d")
    listed = [_base(s) for s in (cfg.get("symbols") or []) if _base(s)]
    reads: dict[str, Reading] = {}
    if p is None:
        return {"params": None, "reads": reads, "holds": [], "gross": None, "n_book": 0,
                "opens": False, "sizing_applied": False, "exits_applied": False}
    pre: dict[str, tuple[float, float, int]] = {}
    for sym in listed:
        rows = bars_by_symbol.get(sym) if isinstance(bars_by_symbol, dict) else None
        if rows is None:
            continue
        daily = closed_ohlcv(rows, source, target)
        closes = _closes(daily) if daily else None
        if daily is None or closes is None:
            reads[sym] = Reading(sym, None, None, None, None, "unread: the bars do not read")
            continue
        if len(daily) < p.days_needed:
            reads[sym] = Reading(sym, None, None, None, len(daily),
                                 f"unread: {len(daily)} closed daily bars, {p.days_needed} needed")
            continue
        score = trend_score(closes, p.lookbacks)
        vol = realised_vol(closes, p.vol_window)
        if score is None or vol is None:
            reads[sym] = Reading(sym, score, vol, None, len(daily),
                                 "unread: a close in the window does not read")
            continue
        pre[sym] = (score, vol, len(daily))
    n_book = len(pre)
    weights: dict[str, float] = {}
    for sym, (score, vol, nd) in pre.items():
        w = target_weight(score, vol, n_book, p)
        if w is None:
            reads[sym] = Reading(sym, score, vol, None, nd, "unread: no weight")
            continue
        weights[sym] = w
        if score == 0.0:
            reason = "trend score 0: not held"
        else:
            reason = (f"trend score {score:.2f}, vol {vol * 100:.0f}% a year: "
                      f"weight {w * 100:.1f}%")
        reads[sym] = Reading(sym, score, vol, w, nd, reason)
    holds = sorted((s for s, w in weights.items() if w > 0), key=lambda s: -weights[s])
    gross: Optional[float] = sum(weights[s] for s in holds) if n_book else None
    return {"params": p, "reads": reads, "holds": holds, "gross": gross, "n_book": n_book,
            "opens": False, "sizing_applied": False, "exits_applied": False}


def how_line(cfg: dict) -> str:
    """What the sleeve does, read off the preset's own numbers."""
    p = params_from_preset(cfg)
    if p is None:
        return "Its numbers could not be read, so no scan runs it."
    syms = cfg.get("symbols")
    n = len(syms) if isinstance(syms, (list, tuple)) else 0
    lbs = ", ".join(str(lb) for lb in p.lookbacks[:-1]) + f" and {p.lookbacks[-1]}"
    return (
        f"The sleeve reads {n} USDT perpetuals on closed daily bars, long only: a "
        f"market's trend score is the mean sign of its returns over {lbs} days, floored "
        f"at zero. A market with a positive score is held at that score times "
        f"{p.target_vol * 100:.0f}% a year split across the markets readable that day, divided "
        f"by its own {p.vol_window}-day realised vol, at most {p.cap:g}× notional; the book "
        f"is rebalanced when a target moves by more than {p.band * 100:.0f}% of the "
        f"position. No stop and no target: a score that falls to zero sells the market. "
        f"The engine applies none of this sizing; the reading is recorded and not applied."
    )


def book_log_path() -> Path:
    """Where the sleeve's daily book rows live; `DAILY_TREND_BOOK_PATH`
    overrides it. The record `docs/research/daily_trend_sleeve_prereg.md`
    pre-registers the reading of."""
    return env_state_path("DAILY_TREND_BOOK_PATH", "data/learning/daily_trend_book.jsonl")


def record_book(board: Any, *, when: Optional[datetime] = None,
                path: Optional[Path] = None) -> str:
    """Append today's board unless today already has one.

    A board whose rows all failed to read, or that is not a board, records
    nothing (UNREADABLE), never an empty book. Returns RECORDED, ALREADY or
    UNREADABLE, the equity record's words.
    """
    now = when or datetime.now(UTC)
    day = now.date().isoformat()
    p = path or book_log_path()
    if not isinstance(board, dict) or not isinstance(board.get("rows"), list):
        return UNREADABLE
    read = [r for r in board["rows"] if isinstance(r, dict) and r.get("score") is not None]
    if not read:
        return UNREADABLE
    if last_recorded_day(p) == day:
        return ALREADY
    holds = [{"symbol": r["symbol"], "score": r["score"], "weight_pct": r["weight_pct"]}
             for r in read if isinstance(r.get("weight_pct"), (int, float)) and r["weight_pct"] > 0]
    gross = sum(h["weight_pct"] for h in holds)
    row = {
        "day": day,
        "read_at": now.isoformat(timespec="seconds"),
        "bar_close": board.get("bar_close"),
        "n_book": len(read),
        "gross_pct": round(gross, 3),
        "holds": sorted(holds, key=lambda h: -h["weight_pct"]),
        "unread": [u.get("symbol") for u in board.get("unread") or [] if isinstance(u, dict)],
    }
    # Present only when the scan named a book. A board that did not is the
    # sleeve as it has always been recorded, and an absent name is not "hourly".
    book_id = board.get("book")
    if isinstance(book_id, str) and book_id.strip():
        row["book"] = book_id.strip()
    vol_window = board.get("vol_window")
    if isinstance(vol_window, int) and not isinstance(vol_window, bool) and vol_window > 0:
        row["vol_window"] = vol_window
    lev_cap = board.get("leverage_cap")
    if isinstance(lev_cap, int) and not isinstance(lev_cap, bool) and lev_cap > 0:
        row["leverage_cap"] = lev_cap
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, separators=(",", ":")) + "\n")
    return RECORDED


__all__ = ["LONG", "NOT_ASKED", "RULE_KEY", "SleeveParams", "Reading", "preset_is_daily_trend",
           "params_from_preset", "trend_score", "realised_vol", "target_weight",
           "read_book", "how_line", "book_log_path", "record_book"]
