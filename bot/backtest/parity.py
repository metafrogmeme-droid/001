"""Live ↔ backtest parity report — is live P&L tracking the frozen benchmark?

The report reads the LIVE realized trades (``data/closed_trades.json``) and
puts them beside the benchmark ON RECORD (``bot/backtest/benchmark_record``:
the artefact the benchmark command writes, never a number typed here) with
the same lens — realized PF / win / net, fee drag, and per-signal-type /
per-setup / per-exit-reason breakdowns — and then ANSWERS the question it
used to end on: a verdict on live's own edge, and whether live's hit rate in
the benchmark's universe is in the benchmark's ballpark, each with its
interval and its floor, the `arb_verdict` discipline.

It is pure, read-only observability: no exchange calls, no order logic. Run:

    python -m bot.backtest.parity                       # data/closed_trades.json
    python -m bot.backtest.parity --file path/to.json   # explicit path

THREE KINDS OF ROW, KEPT APART, because the question is strategy fidelity:

  never-filled   expired / canceled / price_drift / … — no capital was at
                 risk (`NON_FILL_CLOSE_REASONS`); excluded and counted.
  execution      the bot OPENED the position and a post-fill guard FLATTENED
  aborts         it seconds later — leverage_overshoot, sl_placement_failed,
                 slippage_guard (`EXECUTION_ABORT_REASONS`). Real money (the
                 fees), and not a strategy outcome: the strategy never chose
                 to exit, the executor refused to stay in. A backtest cannot
                 have one, so they sit in their own bucket with their cost
                 stated, and never inside the win rate, the PF or the net
                 the benchmark is compared against. The 2026-09-21 card
                 carried eleven of them inside its 194 "filled trades".
  strategy       everything else — the rows every headline figure describes.
  exits

The point is not to reproduce backtest P&L exactly (live and backtest take
different trades) but to answer: are live *fills and fees* as good as the
model assumes, and is live realized edge in the benchmark's ballpark?
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Callable, Optional

from bot.backtest.benchmark_record import (
    BenchmarkReading,
    benchmark_on_record,
    card_line,
    profit_factor,
    symbol_base,
)
from bot.config import CONFIG
from bot.core.arb_tracker import MIN_VERDICT_ENTRIES, mean_interval
from bot.learning.readiness import wilson_lower_bound
from bot.utils.close_reason import is_execution_abort, is_filled_close
from bot.utils.paths import state_path

DEFAULT_TRADES_FILE = str(state_path("data/closed_trades.json"))

#: The floor under both verdicts — the same one the arb verdict and the shadow
#: scoreboard use, for the same reason: a handful of identical outcomes has a
#: sample sd of zero and an interval that calls them certainty.
MIN_VERDICT_TRADES = MIN_VERDICT_ENTRIES
_Z = 1.96

#: A missing close reason is a fact about the RECORD, and "CLOSED (unknown)"
#: is the classifier's word for a close it looked at and could not place.
#: Two different facts, two spellings, on purpose.
NO_REASON = "(no reason recorded)"


def load_closed_trades(path: str | Path) -> list[dict]:
    """Load the persisted closed-trade records (a JSON list of LivePosition
    dicts). Returns [] if the file is absent or malformed (fail-soft — this is a
    reporting tool, never a gate)."""
    p = Path(path)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text())
    except (ValueError, OSError):
        return []
    if isinstance(data, dict):  # tolerate {"closed": [...]} wrappers
        data = data.get("closed") or data.get("trades") or []
    return [t for t in data if isinstance(t, dict)]


def _net(t: dict) -> float | None:
    """Realized net PnL for a trade, tolerant of field naming.

    None when no field carries a number. It used to answer 0.0, which made a
    close with no PnL record a scored, losing, break-even trade: win rate
    down, PF untouched, nobody told. LivePosition declares pnl_usd Optional
    and the persisted-record loader restores a null faithfully, so the shape
    is real. A genuine 0.0 is still 0.0.
    """
    for k in ("pnl_usd", "net_pnl", "net_pnl_usd"):
        v = t.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return float(v)
    return None


def _scored_nets(rows: list[dict]) -> list[float]:
    """The nets of the rows that HAVE one. Every caller hands this a list the
    partition has already scored, so nothing is dropped here -- the filter is
    the type-honest spelling of that, where ``float(_net(t) or 0.0)`` was the
    or-zero shape claiming a break-even for a row that cannot reach it."""
    return [x for x in (_net(t) for t in rows) if x is not None]


def _gross(t: dict) -> Optional[float]:
    """Recorded gross PnL when the row carries one (a real 0.0 included --
    ``gross_pnl or net`` once swapped a measured flat gross for the negative
    net); otherwise the net, which is what a close with no fee record is."""
    g = t.get("gross_pnl")
    if isinstance(g, (int, float)) and not isinstance(g, bool):
        return float(g)
    return _net(t)


def _notional(t: dict) -> float:
    """Position notional in USD: entry_price × quantity, else cost_usd × leverage."""
    entry = t.get("entry_price") or 0.0
    qty = t.get("quantity") or 0.0
    if entry and qty:
        return abs(float(entry) * float(qty))
    cost = t.get("cost_usd") or 0.0
    lev = t.get("leverage") or 1
    return abs(float(cost) * float(lev or 1))


def _fees(t: dict) -> float | None:
    """Recorded commission, or None when the close carries no fee record.

    Reading None as 0.0 made every unrecorded fee a free trade and pulled the
    realized fee rate DOWN, so enough of them turned "WORSE than model" into
    "better than model" -- a confident positive assembled from absent data,
    on the line whose purpose is to say whether live fills are as good as
    the backtest assumes. A genuine 0.0 (a rebate, a fee-free promo) is a
    fee record and still counts.
    """
    v = t.get("commission")
    return abs(float(v)) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


#: One arithmetic for the card and for the runner's pooled block; the tests
#: spell it `parity._pf`.
_pf = profit_factor


def _pf_str(pf: Optional[float]) -> str:
    return "—" if pf is None else f"{pf:.2f}"


# Pre-#52 close records carry labels the current code no longer writes.
# "MANUAL CLOSE" was the catch-all asserted for ANY unattributable close
# (unclassified Bitget closeType, close fill not matching tracked SL/TP
# order ids, exit between SL and TP) — none of which prove a user close;
# genuine operator closes travel their own paths (manual_telegram /
# manual_nlp). Same semantics as today's honest "CLOSED (unknown)", so
# the report merges them rather than resurrecting the misnomer.
_LEGACY_REASON_ALIASES = {
    "MANUAL CLOSE": "CLOSED (unknown)",
}

#: The provenance suffixes the executor appends to a reason — "how do we
#: know" — which used to split ONE reason into two rows (`SL HIT 1 tr` beside
#: `SL HIT (inferred) 51 tr`). The bucket is the reason; the provenance is a
#: count beside it.
_PROVENANCE_SUFFIXES = (
    (" (exchange, combined TPSL)", "exchange"),
    (" (exchange)", "exchange"),
    (" (inferred)", "inferred"),
)


def exit_reason(t: dict) -> tuple[str, str]:
    """``(reason, provenance)`` — the reason with its provenance suffix
    stripped, and the provenance as one of ``"exchange"``, ``"inferred"`` or
    ``""`` (the record carries none)."""
    raw = t.get("close_reason")
    if not raw:
        return NO_REASON, ""
    r = _LEGACY_REASON_ALIASES.get(str(raw), str(raw))
    for suffix, word in _PROVENANCE_SUFFIXES:
        if r.endswith(suffix):
            return r[: -len(suffix)], word
    return r, ""


def partition(trades: list[dict]) -> dict[str, list[dict]]:
    """The four kinds of row (module docstring), read once. ``unscored`` is a
    filled close with no PnL record — neither a loss nor a flat, excluded from
    every stat and counted; ``aborts`` and ``strategy`` are both scored."""
    non_fills: list[dict] = []
    filled: list[dict] = []
    for t in trades:
        (filled if is_filled_close(t.get("close_reason"), _net(t)) else non_fills).append(t)
    unscored = [t for t in filled if _net(t) is None]
    scored = [t for t in filled if _net(t) is not None]
    aborts = [t for t in scored if is_execution_abort(t.get("close_reason"))]
    strategy = [t for t in scored if not is_execution_abort(t.get("close_reason"))]
    return {"non_fills": non_fills, "unscored": unscored,
            "aborts": aborts, "strategy": strategy}


def strategy_exits(trades: list[dict]) -> list[dict]:
    """The rows every headline figure describes — one rule for every reader
    (the card, the digest, the web section, `/parity`'s asset-class bucket),
    so no two surfaces can disagree about what a "trade" is."""
    return partition(trades)["strategy"]


def _row(nets: list[float], inferred: int = 0) -> dict:
    wins = sum(1 for n in nets if n > 0)
    losses = sum(1 for n in nets if n < 0)
    return {"trades": len(nets), "net": round(sum(nets), 2),
            "wins": wins, "losses": losses, "flat": len(nets) - wins - losses,
            "win_rate": wins / len(nets) if nets else 0.0,
            "pf": _pf(nets), "inferred": inferred}


def _ticker_priced(t: dict) -> bool:
    return t.get("fill_source") == "ticker_fallback"


def _group(trades: list[dict], key: str,
           inferred_of: Callable[[dict], bool] = _ticker_priced) -> dict[str, dict]:
    """Bucket the scored rows by ``key``. Each row carries its own sample —
    wins, losses, flat — beside the rate and the ratio, and ``inferred`` counts
    the rows ``inferred_of`` says were inferred (the exit price from a ticker
    by default; the REASON's provenance for the exit-reason table)."""
    groups: dict[str, list[float]] = {}
    inferred: dict[str, int] = {}
    for t in trades:
        n = _net(t)
        if n is None:
            continue   # unscored: excluded from every bucket, counted in the header
        k = str(t.get(key) or "(unknown)")
        groups.setdefault(k, []).append(n)
        if inferred_of(t):
            inferred[k] = inferred.get(k, 0) + 1
    out = {k: _row(nets, inferred.get(k, 0)) for k, nets in groups.items()}
    return dict(sorted(out.items(), key=lambda kv: kv[1]["net"], reverse=True))


def _by_exit_reason(trades: list[dict]) -> dict[str, dict]:
    rows = []
    for t in trades:
        reason, prov = exit_reason(t)
        rows.append({**t, "_reason": reason, "_reason_inferred": prov == "inferred"})
    return _group(rows, "_reason", inferred_of=lambda t: bool(t["_reason_inferred"]))


def _wilson(successes: int, n: int, z: float = _Z) -> Optional[tuple[float, float]]:
    """Both ends of the Wilson interval on ``successes / n``. The upper end is
    the lower end of the complement, which is the interval's own symmetry —
    one piece of arithmetic in the tree, not a second."""
    if n <= 0:
        return None
    return (wilson_lower_bound(successes, n, z),
            1.0 - wilson_lower_bound(n - successes, n, z))


def parity_verdict(strategy: list[dict], benchmark: BenchmarkReading, *,
                   min_trades: int = MIN_VERDICT_TRADES) -> dict:
    """Two verdicts, each with its instrument and its floor, and each able to
    say "too thin" or "could not compare" rather than round to a side.

    ``edge`` — live's OWN edge over every strategy exit: the 95% normal
    interval on the per-trade net (`mean_interval`, the arb verdict's and the
    shadow scoreboard's instrument) has to clear zero.

    ``ballpark`` — against the benchmark on record, over the live rows in the
    benchmark's OWN universe: the live hit rate's Wilson interval either
    contains the benchmark's pooled hit rate or it does not. Hit rate, because
    it is the one scale-free figure both sides carry an interval instrument
    for; a per-trade dollar mean is not comparable across account sizes and a
    profit factor has no interval instrument here, so the PFs are printed
    beside the verdict and never rounded to a word. The 2026-09-21 book was
    128 crypto and 66 stock/ETF/commodity rows against a ten-major benchmark:
    the rows outside the universe are counted and named, never compared.
    """
    nets = _scored_nets(strategy)   # every strategy exit is scored
    n = len(nets)
    inferred = sum(1 for t in strategy if _ticker_priced(t))
    v: dict = {"n": n, "inferred": inferred, "min_trades": min_trades}
    if n == 0:
        v.update(edge="nothing", edge_sentence="no strategy exits on record — nothing to score")
    else:
        mean = sum(nets) / n
        interval = mean_interval(nets)
        v.update(mean_net=round(mean, 4), interval=interval)
        if n < min_trades or interval is None:
            v.update(edge="thin", edge_sentence=(
                f"record too thin — {n} strategy exit{'s' if n != 1 else ''}; "
                f"the bar is {min_trades}"))
        else:
            lo, hi = interval
            words = (f"mean net ${mean:+.2f} per trade, 95% interval ${lo:+.2f}..${hi:+.2f} "
                     f"over {n} strategy exits")
            if lo > 0:
                v.update(edge="positive", edge_sentence=f"live edge POSITIVE — {words}")
            elif hi < 0:
                v.update(edge="negative", edge_sentence=f"live edge NEGATIVE — {words}")
            else:
                v.update(edge="straddles",
                         edge_sentence=f"no edge measurable either way — {words}, "
                                       f"straddling zero")
    if benchmark.state != "read":
        v.update(in_universe=None, outside=None,
                 ballpark=("no_benchmark" if benchmark.state == "none"
                           else "benchmark_unreadable"),
                 ballpark_sentence=(
                     "no benchmark on record to compare against"
                     if benchmark.state == "none" else
                     f"benchmark artefact could not be read ({benchmark.reason}) — "
                     f"no comparison, and that is not \"no benchmark\""))
    else:
        universe = set(benchmark.universe)
        inside = [t for t in strategy if symbol_base(t.get("symbol", "")) in universe]
        k = len(inside)
        outside = n - k
        v.update(in_universe=k, outside=outside)
        bench_wr = benchmark.pooled_win_rate
        inside_nets = _scored_nets(inside)
        wins = sum(1 for x in inside_nets if x > 0)
        # No interval under the bar -- and none over an empty sample either,
        # which _wilson refuses for itself; both are the same absence here.
        interval = _wilson(wins, k) if k >= min_trades else None
        if interval is None or bench_wr is None or not benchmark.pooled_trades:
            why = ("the benchmark on record measured no trades" if not benchmark.pooled_trades
                   else f"the bar is {min_trades}")
            v.update(ballpark="thin", ballpark_sentence=(
                f"too thin to compare — {k} live strategy exit{'s' if k != 1 else ''} in the "
                f"benchmark's universe ({', '.join(sorted(universe)) or 'empty'}); {why}"))
        else:
            lo, hi = interval
            live_wr = wins / k
            pf_live = _pf(inside_nets)
            base = (f"hit rate {live_wr:.0%} over {k} live strategy exits in the benchmark's "
                    f"universe (95% interval {lo:.0%}..{hi:.0%}) vs the benchmark's "
                    f"{bench_wr:.0%} over {benchmark.pooled_trades}; PF {_pf_str(pf_live)} live "
                    f"vs {_pf_str(benchmark.pooled_pf)} benchmark, printed beside the hit rate "
                    f"because a ratio has no interval instrument here")
            if lo <= bench_wr <= hi:
                v.update(ballpark="in", ballpark_sentence=f"in the ballpark on hit rate — {base}")
            elif hi < bench_wr:
                v.update(ballpark="below",
                         ballpark_sentence=f"BELOW the benchmark on hit rate — {base}")
            else:
                v.update(ballpark="above",
                         ballpark_sentence=f"ABOVE the benchmark on hit rate — {base}")
            v.update(live_win_rate=live_wr, live_wr_interval=(lo, hi), live_pf=pf_live)
        if outside:
            v["outside_sentence"] = (
                f"{outside} of {n} live strategy exits are outside the benchmark's universe "
                f"and are not in the comparison")
    if inferred and n:
        v["inferred_sentence"] = (
            f"{inferred} of {n} strategy exits are ticker-priced (fill_source=ticker_fallback: "
            f"the exit price inferred from a ticker, not an exchange fill), so every figure "
            f"above is approximate to that extent")
    return v


def parity_summary(trades: list[dict], modeled_commission_pct: float,
                   benchmark: Optional[BenchmarkReading] = None) -> dict:
    """Aggregate live realized performance + the fee-parity comparison + the
    benchmark on record + the verdict.

    ``modeled_commission_pct`` is the per-side % the backtest charges
    (``CONFIG.risk.commission_pct``); a round trip models ~2× that.

    ``benchmark`` is the artefact reading; None reads the one on record.
    Every headline figure is over STRATEGY EXITS (module docstring): the
    never-filled records, the unscored closes and the execution aborts are
    each excluded and counted, in their own words.
    """
    parts = partition(trades)
    strategy, aborts = parts["strategy"], parts["aborts"]
    nets = _scored_nets(strategy)   # every strategy exit is scored
    # Fees are measured over the closes that CARRY a fee record, and the
    # notional they are measured against is those closes' notional -- a rate
    # diluted by notional nobody was charged for is not the rate. The verdict
    # is withheld unless every close was read.
    fee_rows = [(t, f) for t, f in ((t, _fees(t)) for t in strategy) if f is not None]
    fees_read = len(fee_rows)
    fees = sum(f for _t, f in fee_rows)
    notional = sum(_notional(t) for t, _f in fee_rows)
    gross = sum(x for x in (_gross(t) for t in strategy) if x is not None)
    head = _row(nets, sum(1 for t in strategy if _ticker_priced(t)))
    n = head["trades"]
    realized_fee_rate = ((fees / notional) if notional > 0 else 0.0) if fees_read else None
    modeled_fee_rate = 2.0 * (modeled_commission_pct / 100.0)  # round trip
    # Fraction of gross profit eaten by fees (the churn-drag number); None
    # unless every close carried a fee record.
    gross_win = sum(x for x in nets if x > 0)
    fee_vs_model = ((realized_fee_rate / modeled_fee_rate)
                    if (realized_fee_rate is not None and fees_read == n and modeled_fee_rate > 0)
                    else None)
    reading = benchmark if benchmark is not None else benchmark_on_record()
    abort_nets = _scored_nets(aborts)
    return {
        "trades": n,
        "excluded_non_fills": len(parts["non_fills"]),
        "unscored_pnl": len(parts["unscored"]),
        "aborts": {"trades": len(aborts), "net": round(sum(abort_nets), 2),
                   "by_reason": _group(aborts, "close_reason")},
        "win_rate": head["win_rate"],
        "wins": head["wins"],
        "losses": head["losses"],
        "flat": head["flat"],
        "net_pnl": round(sum(nets), 2),
        "gross_pnl": round(gross, 2),
        "pf": head["pf"],                             # None with no losing trade
        "fees_read": fees_read,
        "total_fees": round(fees, 2),
        "notional": round(notional, 2),
        "realized_fee_rate": realized_fee_rate,       # per round trip, of the fee-read notional; None when none read
        "modeled_fee_rate": modeled_fee_rate,
        "fee_vs_model": fee_vs_model,                 # None unless every close carries a fee record
        "fee_drag_of_gross": ((fees / gross_win) if gross_win > 0 else 0.0) if fees_read == n else None,
        "inferred_fills": head["inferred"],
        "benchmark": reading._asdict(),
        "verdict": parity_verdict(strategy, reading),
        "by_signal_type": _group(strategy, "signal_type"),
        "by_setup": _group(strategy, "strategy_type"),
        "by_exit_reason": _by_exit_reason(strategy),
    }


def _sample(g: dict) -> str:
    """``12W/30L`` — and ``/2F`` only when a measured flat is there to count:
    a permanent ``0F`` is the column that trains a reader to skip the line."""
    s = f"{g['wins']}W/{g['losses']}L"
    return s + (f"/{g['flat']}F" if g.get("flat") else "")


def _bucket_lines(title: str, stats: dict, inferred_word: str = "ticker-priced") -> list[str]:
    if not stats or (len(stats) == 1 and "(unknown)" in stats):
        return []
    lines = [f"  {title}:"]
    for k, g in stats.items():
        sign = "+" if g["net"] >= 0 else ""
        tail = f"  {g['inferred']} {inferred_word}" if g.get("inferred") else ""
        lines.append(f"    {k:<22} {g['trades']:>3} tr  net {sign}${g['net']:>9,.2f}"
                     f"  win {g['win_rate']:.0%}  PF {_pf_str(g['pf'])}  {_sample(g)}{tail}")
    return lines


def aborts_line(s: dict) -> str:
    """One sentence for the execution aborts, shared by the card and the digest
    so the two cannot describe the same eleven rows differently."""
    a = s.get("aborts") or {}
    if not a.get("trades"):
        return ""
    reasons = ", ".join(f"{k} {g['trades']}" for k, g in a["by_reason"].items())
    return (f"Execution aborts kept apart: {a['trades']} (net ${a['net']:+,.2f}) — {reasons} "
            f"(post-fill flatten guards, not strategy exits)")


def format_report(s: dict) -> str:
    if not s["trades"] and not (s.get("aborts") or {}).get("trades"):
        return ("  No closed live trades found. Run the bot live, then re-run this "
                "report against data/closed_trades.json.")
    v = s["verdict"]
    lines = ["", "  ── LIVE ↔ BACKTEST PARITY " + "─" * 42,
             f"  Live realized: {s['trades']} strategy exits  net ${s['net_pnl']:+,.2f}"
             f"  win {s['win_rate']:.0%}  PF {_pf_str(s['pf'])}  ({_sample(s)})"
             + (f"  ({s['excluded_non_fills']} never-filled records excluded)"
                if s.get("excluded_non_fills") else "")
             + (f"  ({s['unscored_pnl']} close(s) with no PnL record excluded)"
                if s.get("unscored_pnl") else "")]
    if aborts_line(s):
        lines.append("  " + aborts_line(s))
    lines.append(card_line(BenchmarkReading(**s["benchmark"])))
    lines.append(f"  Verdict: {v['edge_sentence']}")
    lines.append(f"           {v['ballpark_sentence']}")
    for key in ("outside_sentence", "inferred_sentence"):
        if v.get(key):
            lines.append(f"           {v[key]}")
    # Fee parity — the concrete fills/fees gap.
    fvm = s.get("fee_vs_model")
    fees_read = int(s.get("fees_read") or 0)
    if fvm is not None:
        verdict = ("~ matches model" if 0.8 <= fvm <= 1.25 else
                   "WORSE than model" if fvm > 1.25 else "better than model")
        lines.append(
            f"  Fees: realized {s['realized_fee_rate']*100:.3f}%/round-trip vs modeled "
            f"{s['modeled_fee_rate']*100:.3f}% → {fvm:.2f}× ({verdict}); "
            f"${s['total_fees']:,.2f} total = {s['fee_drag_of_gross']*100:.0f}% of gross profit")
    elif fees_read:
        # Some closes carry no fee record: the rate is measured on the ones
        # that do, and the verdict is withheld -- a ratio over part of the
        # book is not the ratio.
        lines.append(
            f"  Fees: realized {s['realized_fee_rate']*100:.3f}%/round-trip on the "
            f"{fees_read} of {s['trades']} closes that carry a fee record "
            f"(vs modeled {s['modeled_fee_rate']*100:.3f}%); verdict withheld — "
            f"fees recorded on {fees_read} of {s['trades']} closes")
    else:
        lines.append(
            f"  Fees: no fee record on any close — fee parity cannot be measured "
            f"(modeled {s['modeled_fee_rate']*100:.3f}%/round-trip)")
    # The ticker-priced share is said once, in the verdict block above, where
    # it qualifies the figures it sits under; a second sentence here said the
    # same thing in different words.
    lines.extend(_bucket_lines("By signal type", s["by_signal_type"]))
    lines.extend(_bucket_lines("By setup", s["by_setup"]))
    lines.extend(_bucket_lines("By exit reason", s["by_exit_reason"],
                               inferred_word="reason inferred"))
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Live↔backtest parity report from data/closed_trades.json.")
    parser.add_argument("--file", type=str, default=DEFAULT_TRADES_FILE,
                        help=f"Closed-trades JSON (default: {DEFAULT_TRADES_FILE})")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    trades = load_closed_trades(args.file)
    summary = parity_summary(trades, CONFIG.risk.commission_pct)
    print(format_report(summary))


if __name__ == "__main__":
    main()
