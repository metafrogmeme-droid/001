"""The xsmom + BTC regime sleeve as a PAPER book, forward from its first run.

``SLEEVE_PAPER_ENABLED`` (``bot/config.py``, off by default) is the switch.
While it is off nothing is fetched and nothing is written. While it is on, once
a UTC day, the runner reads public daily candles for the study's universe,
marks a simulated book to the newest closed day, decides the target book on a
rebalance day (``bot/sleeve/xsmom_regime.py``), and writes the record to
``$RUNECLAW_STATE_DIR/sleeve/xsmom_regime_paper.json`` atomically.

IT PLACES NOTHING. It never imports the live executor, never builds a client
with a key and never calls a private endpoint. The only network call is the
venue's public candle read, through ``fetch_closed_ohlcv``, the path the
backtests and the strategy scans already use.

THE BOOK. Positions are weights of sleeve equity. Equity is an index that
starts at 100 on the first recorded close; so does BTC buy-and-hold, from the
same close, for comparison. Between rebalances the weights drift with prices
and nothing is traded. A rebalance at the close of day ``D`` pays
``fee x turnover`` (0.10% a side) out of the index and fills at that close.
The study fills at the next open; crypto trades through midnight, so the two
are the same print to within a tick, and the difference is stated, not hidden.

UNREADABLE IS NEVER ZERO. A run that cannot read BTC, a held coin, or (on a
day a rebalance is due) any coin of the universe changes nothing but its
``last_run`` line: no day is marked at a zero return and no target is decided
on part of the universe. Marking catches up on the next run that reads the
closes, because a held book earns what the prices did whether anyone looked.
A decision does not: a rebalance due on a day no run completed is recorded
under ``skipped`` with its reason, and the book keeps the weights it had. A
coin with too little history, or that the venue does not list, is excluded
from the decision and the exclusion is recorded with the rebalance.

Two ways to run it, and only one should: the engine tick calls it once a day
when the flag is on (``RuneClawEngine._record_sleeve_paper_day``), and
``python3 -m bot.sleeve.paper run-once`` does the same from cron for an
operator who runs no engine. ``python3 -m bot.sleeve.paper show`` prints the
card ``/sleeve`` sends.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import html
import math
import os
import sys
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from bot.sleeve.xsmom_regime import (
    MIN_HISTORY,
    PRIMARY,
    REGIME_ASSET,
    UNIVERSE,
    Params,
    decide,
    is_rebalance_day,
    next_rebalance_day,
    regime_at,
    turnover,
)
from bot.utils.atomic_write import atomic_write_json
from bot.utils.json_store import READ, StoreRead, read_json_store
from bot.utils.json_store import UNREADABLE as STORE_UNREADABLE
from bot.utils.paths import REPO_ROOT

FLAG = "SLEEVE_PAPER_ENABLED"
STRATEGY = "xsmom_regime_primary"
STATE_VERSION = 1
QUOTE = "USDT"
TIMEFRAME = "1d"
SOURCE = "Bitget spot, public 1d candles (UTC days), keyless"
#: Closed daily bars asked for on a run whose record is current; a longer gap
#: since the last marked day asks for more, so marking can catch up.
FETCH_BARS = 220
MAX_FETCH_BARS = 1000
CONCURRENCY = 4
SYMBOL_TIMEOUT_S = 20.0
DAY_MS = 86_400_000
STUDY = "docs/research/prereg/new_strategy_xsmom_regime/REPORT.md"

# The runner's words. ``run_once`` answers one of them.
DISABLED = "disabled"
RECORDED = "recorded"
ALREADY = "already recorded today"
SKIPPED = "skipped"
UNREADABLE = "unreadable"
NOT_ASKED = "not_asked"


def sleeve_paper_enabled() -> bool:
    """Whether ``SLEEVE_PAPER_ENABLED`` is on. Read from ``CONFIG``, the one
    reading of the environment."""
    from bot.config import CONFIG
    return bool(getattr(CONFIG, "sleeve_paper_enabled", False))


def state_file() -> Path:
    """The record's path. A relative ``RUNECLAW_STATE_DIR`` is anchored at the
    repository root, as ``bot/quarantine/book.py`` anchors it; unset is
    ``<repo>/data``."""
    raw = (os.environ.get("RUNECLAW_STATE_DIR") or "").strip()
    if not raw:
        base = REPO_ROOT / "data"
    else:
        expanded = Path(raw).expanduser()
        base = expanded if expanded.is_absolute() else REPO_ROOT / expanded
    return base / "sleeve" / "xsmom_regime_paper.json"


# ── reading the candles ──────────────────────────────────────────────────────

@dataclass
class CandleRead:
    """One run's read of the universe.

    ``closes`` maps a coin to ``{ISO day: close}``. ``unread`` names a coin
    whose read failed or does not parse: it is not known, and a decision that
    needs it is skipped. ``absent`` names a coin the venue has no market or no
    bars for: it is excluded and said so, as the study says of a coin with no
    Binance history.
    """

    closes: dict[str, dict[str, float]] = field(default_factory=dict)
    unread: dict[str, str] = field(default_factory=dict)
    absent: dict[str, str] = field(default_factory=dict)


def parse_daily_rows(rows: Sequence[Any]) -> Optional[dict[str, float]]:
    """``{ISO day: close}`` from ccxt rows, or None when any row does not read
    as a UTC-midnight bar with a finite, positive close, or one day carries
    two different closes."""
    out: dict[str, float] = {}
    for row in rows:
        try:
            ts = int(row[0])
            close = float(row[4])
        except (TypeError, ValueError, IndexError):
            return None
        if ts % DAY_MS != 0 or not math.isfinite(close) or close <= 0:
            return None
        day = datetime.fromtimestamp(ts / 1000, tz=UTC).date().isoformat()
        if day in out and out[day] != close:
            return None
        out[day] = close
    return out


def _is_bad_symbol(exc: BaseException) -> bool:
    try:
        import ccxt
    except ImportError:  # pragma: no cover - ccxt is a pinned dependency
        return False
    return isinstance(exc, ccxt.BadSymbol)


async def read_closes(exchange: Any, symbols: Sequence[str] = UNIVERSE,
                      bars: int = FETCH_BARS) -> CandleRead:
    """Closed daily closes for ``symbols`` from the venue's PUBLIC candles.

    A failed read is named by its exception class, never its message.
    """
    from bot.backtest.data_loader import fetch_closed_ohlcv

    out = CandleRead()
    sem = asyncio.Semaphore(CONCURRENCY)

    async def one(sym: str) -> None:
        async with sem:
            try:
                rows = await asyncio.wait_for(
                    fetch_closed_ohlcv(exchange, f"{sym}/{QUOTE}", TIMEFRAME, bars),
                    SYMBOL_TIMEOUT_S)
            except Exception as exc:  # noqa: BLE001 -- named, never its message
                if _is_bad_symbol(exc):
                    out.absent[sym] = f"the venue lists no {sym}/{QUOTE} spot market"
                else:
                    out.unread[sym] = f"its candle read failed ({type(exc).__name__})"
                return
        if not rows:
            out.absent[sym] = "the venue returned no closed daily bars"
            return
        parsed = parse_daily_rows(rows)
        if parsed is None:
            out.unread[sym] = "a candle row does not read as a dated, positive close"
            return
        out.closes[sym] = parsed

    await asyncio.gather(*(one(s) for s in symbols))
    return out


def bars_to_fetch(state: Optional[Mapping[str, Any]], today: date) -> int:
    """Enough closed bars for one decision, and for marking from the last
    recorded day if the record has fallen behind."""
    need = FETCH_BARS
    last = _day(state.get("last_day")) if isinstance(state, Mapping) else None
    if last is not None:
        need = max(need, (today - last).days + 2)
    return min(need, MAX_FETCH_BARS)


# ── the record ───────────────────────────────────────────────────────────────

def _day(raw: Any) -> Optional[date]:
    if not isinstance(raw, str):
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _num(raw: Any) -> Optional[float]:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    f = float(raw)
    return f if math.isfinite(f) else None


def check_state(data: Any) -> str:
    """``""`` for a record this runner wrote, a short reason otherwise."""
    if data.get("version") != STATE_VERSION or data.get("strategy") != STRATEGY:
        return "not this sleeve's record"
    if _day(data.get("started")) is None or _day(data.get("last_day")) is None:
        return "its days do not read"
    for key in ("sleeve_index", "btc_index", "start_btc_close"):
        v = _num(data.get(key))
        if v is None or v <= 0:
            return f"{key} does not read"
    weights = data.get("weights")
    if not isinstance(weights, dict) or any(_num(v) is None for v in weights.values()):
        return "its weights do not read"
    for key in ("history", "rebalances", "skipped"):
        if not isinstance(data.get(key), list):
            return f"{key} is not a list"
    return ""


def load_record(path: Optional[Path] = None) -> StoreRead:
    """The record in one of three states: fresh (none yet), read, unreadable."""
    return read_json_store(path or state_file(), check=check_state)


def record_is_current(today: Optional[date] = None, path: Optional[Path] = None) -> bool:
    """True when the record already holds the newest closed day. An
    unreadable record is not current, and ``run_once`` says why."""
    r = load_record(path)
    if r.state != READ:
        return False
    last = _day(r.data.get("last_day"))
    today = today or datetime.now(UTC).date()
    return last is not None and last >= today - timedelta(days=1)


def _series(closes: Mapping[str, float], days: Sequence[str]) -> list[Optional[float]]:
    return [closes.get(d) for d in days]


def _hole(closes: Mapping[str, float], end: date) -> Optional[str]:
    """The first missing day between a coin's first close and ``end``, or None."""
    if not closes:
        return end.isoformat()
    d = date.fromisoformat(min(closes))
    while d <= end:
        if d.isoformat() not in closes:
            return d.isoformat()
        d += timedelta(days=1)
    return None


def _days(start: date, end: date) -> list[str]:
    n = (end - start).days
    return [(start + timedelta(days=i)).isoformat() for i in range(n + 1)]


def _note_failure(state: Optional[dict], day: date, now_iso: str, outcome: str,
                  reason: str) -> tuple[Optional[dict], str, str]:
    """A run that changes nothing but its ``last_run`` line."""
    if state is None:
        return None, outcome, reason
    state["last_run"] = {"at": now_iso, "day": day.isoformat(), "outcome": outcome,
                         "reason": reason}
    return state, outcome, reason


def _regime_on(btc: Sequence[Optional[float]], p: Params) -> Optional[bool]:
    reg = regime_at(btc, p)
    return None if reg is None else reg.on


def advance(state: Optional[Mapping[str, Any]], read: CandleRead, today: date,
            now_iso: str, p: Params = PRIMARY) -> tuple[Optional[dict], str, str]:
    """One run, as a pure function: ``(new record or None, outcome, note)``.

    ``today`` is the UTC day the run happens on; the newest closed day is the
    day before. ``None`` as the record means nothing is to be written.
    """
    st: Optional[dict] = copy.deepcopy(dict(state)) if state is not None else None
    end = today - timedelta(days=1)
    end_iso = end.isoformat()
    last = _day(st.get("last_day")) if st is not None else None
    if last is not None and last >= end:
        return st, ALREADY, f"the record already holds {last.isoformat()}"

    # Every coin's closes up to the newest closed day; a later bar is a day
    # this run is not on yet (a stand-in clock in a test, or a skewed one).
    closes = {s: {d: c for d, c in rows.items() if d <= end_iso}
              for s, rows in read.closes.items()}
    unread = dict(read.unread)
    for sym, rows in closes.items():
        if sym in unread:
            continue
        if end_iso not in rows:
            newest = max(rows) if rows else "none"
            unread[sym] = f"its newest closed day is {newest}, not {end_iso}"
            continue
        hole = _hole(rows, end)
        if hole is not None:
            unread[sym] = f"its close for {hole} is missing"

    btc_why = unread.get(REGIME_ASSET) or read.absent.get(REGIME_ASSET)
    if btc_why is None and REGIME_ASSET not in closes:
        btc_why = "it was not read"
    if btc_why is not None:
        return _note_failure(st, end, now_iso, SKIPPED if st is not None else UNREADABLE,
                             f"BTC does not read: {btc_why}; nothing was marked or decided")

    btc_rows = closes[REGIME_ASSET]
    axis_start = _day(min(btc_rows)) or end
    axis = _days(axis_start, end)
    btc = _series(btc_rows, axis)

    weights: dict[str, float] = dict(st["weights"]) if st is not None else {}
    held_unread = sorted(s for s in weights if s in unread or s in read.absent)
    if held_unread:
        why = "; ".join(f"{s}: {unread.get(s) or read.absent.get(s)}" for s in held_unread)
        return _note_failure(st, end, now_iso, SKIPPED,
                             f"a held coin does not read ({why}); the book was not marked")

    # The days this run marks, in order: from the day after the last marked
    # one (none on the first run) to the newest closed day.
    first_mark = (last + timedelta(days=1)) if last is not None else end
    if last is not None and last.isoformat() < axis[0]:
        return _note_failure(st, end, now_iso, SKIPPED,
                             f"the record's last day {last} is older than the closes read; "
                             "the book was not marked")
    for sym in weights:
        if last is not None and last.isoformat() not in closes[sym]:
            return _note_failure(st, end, now_iso, SKIPPED,
                                 f"{sym}'s close for {last} was not read; the book was not marked")

    def regime_on(day: date) -> Optional[bool]:
        idx = (day - axis_start).days
        if idx < 0:
            return None
        return _regime_on(btc[: idx + 1], p)

    def due(day: date) -> tuple[bool, str]:
        cal = is_rebalance_day(day, p)
        now_on, prev_on = regime_on(day), regime_on(day - timedelta(days=1))
        flip = now_on is not None and prev_on is not None and now_on != prev_on
        why = " and ".join(w for w, on in (("schedule", cal), ("regime flip", flip)) if on)
        return cal or flip, why

    # The decision at the newest closed day needs every coin of the universe
    # read; a coin the venue does not list is excluded, not unread.
    is_due, due_why = due(end)
    reg = regime_at(btc, p)
    if is_due and reg is None:
        # A regime nobody read is not "off": cash decided on it would be a
        # verdict taken on no data.
        return _note_failure(st, end, now_iso, SKIPPED if st is not None else UNREADABLE,
                             f"the rebalance due at the {end_iso} close ({due_why}) was not "
                             f"decided: BTC's last {p.regime_ma} closes do not all read")
    universe_unread = sorted(s for s in UNIVERSE if s in unread)
    if is_due and universe_unread:
        why = "; ".join(f"{s}: {unread[s]}" for s in universe_unread)
        outcome = SKIPPED if st is not None else UNREADABLE
        return _note_failure(st, end, now_iso, outcome,
                             f"the rebalance due at the {end_iso} close ({due_why}) was not "
                             f"decided: {why}")

    if st is None:
        start_close = btc_rows[end_iso]
        st = {
            "version": STATE_VERSION, "strategy": STRATEGY,
            "params": {"lookback": p.lookback, "rebalance": p.rebalance,
                       "regime_ma": p.regime_ma, "fee": p.fee, "min_history": MIN_HISTORY},
            "source": SOURCE, "paper_only": True,
            "started": end_iso, "last_day": end_iso,
            "sleeve_index": 100.0, "btc_index": 100.0, "start_btc_close": start_close,
            "weights": {}, "history": [{"day": end_iso, "sleeve": 100.0, "btc": 100.0}],
            "rebalances": [], "skipped": [],
        }
    else:
        run_before = st.get("last_run")
        previous: dict[str, Any] = run_before if isinstance(run_before, dict) else {}
        sleeve = float(st["sleeve_index"])
        start_close = float(st["start_btc_close"])
        d = first_mark
        while d <= end:
            iso, prev_iso = d.isoformat(), (d - timedelta(days=1)).isoformat()
            gross = 0.0
            for sym, w in weights.items():
                r = closes[sym][iso] / closes[sym][prev_iso] - 1
                gross += w * r
            if weights:
                weights = {s: w * (closes[s][iso] / closes[s][prev_iso]) / (1 + gross)
                           for s, w in weights.items()}
            sleeve *= 1 + gross
            btc_idx = 100.0 * btc_rows[iso] / start_close
            st["history"].append({"day": iso, "sleeve": sleeve, "btc": btc_idx})
            if d < end:
                missed, missed_why = due(d)
                if missed:
                    reason = "no run completed at this close; the book kept its weights"
                    if previous.get("day") == iso and previous.get("reason"):
                        reason = f"{previous['reason']}; the book kept its weights"
                    st["skipped"].append({"day": iso, "due": missed_why, "reason": reason,
                                          "recorded_at": now_iso})
            d += timedelta(days=1)
        st["sleeve_index"] = sleeve
        st["btc_index"] = 100.0 * btc_rows[end_iso] / start_close
        st["weights"] = weights
        st["last_day"] = end_iso

    if reg is not None:
        st["regime"] = {"day": end_iso, "on": reg.on, "btc_close": reg.close,
                        "btc_mean": reg.mean, "ma_days": p.regime_ma}
    note = f"marked through {end_iso}"
    if is_due:
        listed = [s for s in UNIVERSE if s in closes]
        decision = decide({s: _series(closes[s], axis) for s in listed}, p)
        target = {s: w for s, w in decision.weights.items() if w > 0}
        traded = turnover(st["weights"], target)
        cost = p.fee * traded
        st["sleeve_index"] = float(st["sleeve_index"]) * (1 - cost)
        st["history"][-1]["sleeve"] = st["sleeve_index"]
        st["weights"] = target
        excluded = dict(decision.excluded)
        for sym in UNIVERSE:
            if sym in read.absent:
                excluded[sym] = read.absent[sym]
        st["rebalances"].append({
            "day": end_iso, "why": due_why, "decided_at": now_iso,
            "regime_on": None if decision.regime is None else decision.regime.on,
            "target": target, "turnover": traded, "cost_fraction": cost,
            "eligible": list(decision.eligible), "excluded": excluded,
            "reason": decision.reason, "sleeve_index_after": st["sleeve_index"],
        })
        note += f"; rebalanced ({due_why}): {decision.reason}"
    st["last_run"] = {"at": now_iso, "day": end_iso, "outcome": RECORDED, "reason": note}
    return st, RECORDED, note


@dataclass(frozen=True)
class RunResult:
    outcome: str
    note: str


async def run_once(exchange: Any, *, today: Optional[date] = None,
                   path: Optional[Path] = None, now: Optional[datetime] = None) -> RunResult:
    """One daily run against ``exchange``'s public candles. Writes only the
    record, only while the flag is on, and never over a record that does not
    read."""
    if not sleeve_paper_enabled():
        return RunResult(DISABLED, f"{FLAG} is off: nothing was read and nothing was written")
    target = path or state_file()
    r = load_record(target)
    if r.state == STORE_UNREADABLE:
        return RunResult(UNREADABLE, f"the paper record does not read ({r.detail}); "
                                     "it was left as it is")
    state = r.data if r.state == READ else None
    stamp = now or datetime.now(UTC)
    day = today or stamp.date()
    last = _day(state.get("last_day")) if state is not None else None
    if last is not None and last >= day - timedelta(days=1):
        return RunResult(ALREADY, f"the record already holds {last.isoformat()}")
    read = await read_closes(exchange, bars=bars_to_fetch(state, day))
    new, outcome, note = advance(state, read, day, stamp.isoformat(timespec="seconds"))
    if new is not None:
        atomic_write_json(target, new)
    return RunResult(outcome, note)


# ── the card ─────────────────────────────────────────────────────────────────

def max_drawdown(values: Sequence[float]) -> Optional[float]:
    """Largest fall from a running peak, as a fraction; None for no values."""
    if not values:
        return None
    peak, worst = values[0], 0.0
    for v in values:
        peak = max(peak, v)
        worst = max(worst, 1 - v / peak)
    return worst


def _pct(x: float, signed: bool = True) -> str:
    return f"{x * 100:+.2f}%" if signed else f"{x * 100:.2f}%"


def _esc(text: Any) -> str:
    return html.escape(str(text), quote=False)


def _said(text: Any) -> Optional[str]:
    """Escaped words, or None when the field is missing.

    A missing reason is not the word "None": ``str(None)`` is that word, and
    the channel post used to print it as if it were the reason.
    """
    if not isinstance(text, str):
        return None
    stripped = text.strip()
    if not stripped:
        return None
    return html.escape(stripped, quote=False)


def card_text(record: StoreRead, *, flag_on: bool, today: Optional[date] = None) -> str:
    """The ``/sleeve`` card. Percent and ratios only; no amount of money."""
    head = ("\U0001f4d2 <b>Paper sleeve: xsmom + BTC regime gate</b>\n"
            "PRIMARY config: 90-day momentum, top-k, inverse vol, 30% vol target, "
            "no leverage, cash below BTC's 150-day mean, every 14 days, 0.10% a side. "
            "Paper only: it places nothing.\n"
            f"Runner: {FLAG} is {'on' if flag_on else 'off'}.")
    if record.state == STORE_UNREADABLE:
        return (f"{head}\n\nThe paper record could not be read ({_esc(record.detail)}). "
                "Nothing is shown from it: it is not an empty book, and the runner "
                "will not write over it.")
    if record.state != READ:
        tail = ("Nothing is being recorded while it is off." if not flag_on else
                "The runner has not recorded a day yet. Its first run starts the sleeve "
                "and BTC buy-and-hold at an index of 100 from the same close.")
        return f"{head}\n\nNo paper record yet. {tail}"
    st = record.data
    today = today or datetime.now(UTC).date()
    last_day = _day(st["last_day"]) or today
    lines = [head, "",
             f"Started {_esc(st['started'])}, marked through {_esc(st['last_day'])}. "
             f"Source: {_esc(st.get('source') or 'not recorded')}."]
    reg = st.get("regime")
    if (isinstance(reg, dict) and _num(reg.get("btc_close")) is not None
            and _num(reg.get("btc_mean")) is not None):
        gap = float(reg["btc_close"]) / float(reg["btc_mean"]) - 1
        side = "above" if reg.get("on") else "not above"
        lines.append(f"Regime at the {_esc(reg.get('day'))} close: BTC {side} its "
                     f"{_esc(reg.get('ma_days'))}-day mean ({_pct(gap)}). "
                     + ("Momentum book allowed." if reg.get("on") else "Target is all cash."))
    else:
        lines.append("Regime: not read on the last run.")
    rebs = [r for r in st["rebalances"] if isinstance(r, dict)]
    if rebs:
        lr = rebs[-1]
        cost = _num(lr.get("cost_fraction"))
        paid = (f", fees {_pct(cost, signed=False)} of the index" if cost is not None
                else ", its fee does not read")
        lines.append(f"Last rebalance: the {_esc(lr.get('day'))} close ({_esc(lr.get('why'))})"
                     f"{paid}.")
    else:
        lines.append("Last rebalance: none yet. The book is cash until the first one.")
    nxt = next_rebalance_day(last_day)
    lines.append(f"Next scheduled rebalance: the {nxt.isoformat()} close, or sooner on a regime flip.")
    weights = {s: float(w) for s, w in st["weights"].items()}
    if weights:
        parts = [f"{_esc(s)} {_pct(w, signed=False)}"
                 for s, w in sorted(weights.items(), key=lambda kv: -kv[1])]
        cash = 1 - sum(weights.values())
        lines.append("Book now (weights of sleeve equity): " + ", ".join(parts)
                     + f", cash {_pct(cash, signed=False)}.")
    else:
        lines.append("Book now: all cash.")
    sleeve, btc = float(st["sleeve_index"]), float(st["btc_index"])
    lines.append(f"Since {_esc(st['started'])}: sleeve {_pct(sleeve / 100 - 1)}, "
                 f"BTC buy-and-hold {_pct(btc / 100 - 1)}.")
    hist = [h for h in st["history"] if isinstance(h, dict)]
    s_vals = [float(h["sleeve"]) for h in hist if _num(h.get("sleeve")) is not None]
    b_vals = [float(h["btc"]) for h in hist if _num(h.get("btc")) is not None]
    # Both indexes start at 100 by construction; the first row may already
    # hold the first rebalance's fee, which is a fall from that start.
    s_dd, b_dd = max_drawdown([100.0, *s_vals]), max_drawdown([100.0, *b_vals])
    if s_dd is not None and b_dd is not None and len(s_vals) == len(hist) == len(b_vals):
        closes_word = "close" if len(hist) == 1 else "closes"
        lines.append(f"Max drawdown: sleeve {_pct(s_dd, signed=False)}, "
                     f"BTC {_pct(b_dd, signed=False)} ({len(hist)} {closes_word}).")
    else:
        lines.append("Max drawdown: a row of the daily history does not read, so it is not shown.")
    skipped = [s for s in st["skipped"] if isinstance(s, dict)]
    if skipped:
        lines.append(f"Skipped rebalances: {len(skipped)}.")
        for s in skipped[-5:]:
            lines.append(f"  {_esc(s.get('day'))} ({_esc(s.get('due'))}): {_esc(s.get('reason'))}")
    else:
        lines.append("Skipped rebalances: none.")
    run = st.get("last_run")
    if isinstance(run, dict) and run.get("outcome") != RECORDED:
        lines.append(f"Last run {_esc(run.get('at'))}: {_esc(run.get('outcome'))}, "
                     f"{_esc(run.get('reason'))}")
    if rebs and isinstance(rebs[-1].get("excluded"), dict) and rebs[-1]["excluded"]:
        ex = rebs[-1]["excluded"]
        lines.append(f"Excluded at the last rebalance: {len(ex)} ("
                     + ", ".join(_esc(s) for s in sorted(ex)) + ").")
    lines.append(f"\nThe study passed weakly and the pass is mostly the BTC gate: {STUDY}")
    return "\n".join(lines)



def channel_call_text(state: Mapping[str, Any]) -> Optional[str]:
    """The channel's post for a rebalance that changed the book, or None.

    The engine queues this text; it is not posted until an admin approves
    that one item. It speaks only for a rebalance made AT the newest close
    (``last_day``) that traded something (turnover above zero), so a schedule
    day that kept the same book posts nothing. Percent only, for a public
    room, and it says what it is: a paper record that placed nothing.
    """
    rebs = [r for r in state.get("rebalances") or [] if isinstance(r, dict)]
    if not rebs or rebs[-1].get("day") != state.get("last_day"):
        return None
    lr = rebs[-1]
    traded = _num(lr.get("turnover"))
    if traded is None or traded <= 0:
        return None
    target = {str(k): float(v) for k, v in (lr.get("target") or {}).items()
              if _num(v) is not None and float(v) > 0}
    if target:
        parts = [f"{_esc(k)} {_pct(w, signed=False)}"
                 for k, w in sorted(target.items(), key=lambda kv: -kv[1])]
        cash = 1 - sum(target.values())
        book = ", ".join(parts) + (f", cash {_pct(cash, signed=False)}" if cash > 0.0005 else "")
    else:
        book = "all cash"
    when = f"At the {_esc(lr.get('day'))} close"
    why = _said(lr.get("why"))
    if why:
        when += f" ({why})"
    lines = ["\U0001f4d2 <b>RUNECLAW SLEEVE \u2014 paper call</b>",
             f"{when}: {book}."]
    reason = _said(lr.get("reason"))
    if reason:
        lines.append(f"Why: {reason}.")
    sleeve, btc = _num(state.get("sleeve_index")), _num(state.get("btc_index"))
    if sleeve is not None and btc is not None:
        lines.append(f"Paper record since {_esc(state.get('started'))}: sleeve "
                     f"{_pct(sleeve / 100 - 1)}, BTC buy-and-hold {_pct(btc / 100 - 1)}.")
    else:
        lines.append("Paper record: the index does not read, so no figure is shown.")
    lines.append("<i>Paper only: no order was placed. Daily, long-only, cash below BTC's "
                 "150-day mean. Its backtest passed weakly and did not beat holding BTC "
                 "on return. Not investment advice.</i>")
    lines.append("#RUNECLAW #Sleeve")
    return "\n".join(lines)


# ── the standalone entry ─────────────────────────────────────────────────────

async def _run_once_with_public_client() -> RunResult:
    """``run_once`` on a keyless Bitget client built here and closed here."""
    if not sleeve_paper_enabled():
        return RunResult(DISABLED, f"{FLAG} is off: nothing was read and nothing was written")
    import ccxt.async_support as ccxt_async
    exchange = ccxt_async.bitget({"aiohttp_trust_env": True, "enableRateLimit": True,
                                  "timeout": 30_000})
    try:
        return await run_once(exchange)
    finally:
        await exchange.close()


EXIT = {RECORDED: 0, ALREADY: 0, SKIPPED: 1, UNREADABLE: 1, DISABLED: 2}


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m bot.sleeve.paper",
                                 description="Paper-only xsmom + BTC regime sleeve.")
    ap.add_argument("command", choices=("run-once", "show"))
    args = ap.parse_args(argv)
    if args.command == "show":
        text = card_text(load_record(), flag_on=sleeve_paper_enabled())
        import re
        print(re.sub(r"</?[a-z]+>", "", html.unescape(text)))
        return 0
    result = asyncio.run(_run_once_with_public_client())
    print(f"{result.outcome}: {result.note}")
    return EXIT.get(result.outcome, 1)


if __name__ == "__main__":
    sys.exit(main())
