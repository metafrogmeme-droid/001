"""Live ↔ backtest parity report — is live P&L tracking the frozen benchmark?

The frozen benchmark now says the strategy is profitable on majors_1h
(+0.31% OOS, PF 1.14, measured with the live partial-TP exit). This tool closes
the loop: it reads the LIVE realized trades (``data/closed_trades.json``) and
reports the same lens — realized PF / win / net, fee drag, and per-signal-type /
per-setup / per-exit-reason breakdowns — so live can be compared directly to the
backtest, and any divergence (fees, slippage, fill timing) shows up as a gap.

It is pure, read-only observability: no exchange calls, no order logic. Run:

    python -m bot.backtest.parity                       # data/closed_trades.json
    python -m bot.backtest.parity --file path/to.json   # explicit path
    python -m bot.backtest.parity --by signal_type      # focus one dimension

The point is not to reproduce backtest P&L exactly (live and backtest take
different trades) but to answer: are live *fills and fees* as good as the model
assumes, and is live realized edge in the same ballpark as the benchmark?
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from bot.config import CONFIG
from bot.utils.paths import state_path

DEFAULT_TRADES_FILE = str(state_path("data/closed_trades.json"))


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


def _pf(nets: list[float]) -> float:
    gross_win = sum(n for n in nets if n > 0)
    gross_loss = sum(-n for n in nets if n < 0)
    return (gross_win / gross_loss) if gross_loss > 0 else float("inf")


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


def _exit_reason(t: dict) -> str:
    r = str(t.get("close_reason") or "(unknown)")
    return _LEGACY_REASON_ALIASES.get(r, r)


def _group(trades: list[dict], key: str) -> dict[str, dict]:
    groups: dict[str, list[float]] = {}
    for t in trades:
        n = _net(t)
        if n is None:
            continue   # unscored: excluded from every bucket, counted in the header
        k = str(t.get(key) or "(unknown)")
        groups.setdefault(k, []).append(n)
    out = {}
    for k, nets in groups.items():
        wins = sum(1 for n in nets if n > 0)
        out[k] = {"trades": len(nets), "net": round(sum(nets), 2),
                  "win_rate": wins / len(nets) if nets else 0.0,
                  "pf": _pf(nets)}
    return dict(sorted(out.items(), key=lambda kv: kv[1]["net"], reverse=True))


def parity_summary(trades: list[dict], modeled_commission_pct: float) -> dict:
    """Aggregate live realized performance + the fee-parity comparison.

    ``modeled_commission_pct`` is the per-side % the backtest charges
    (``CONFIG.risk.commission_pct``); a round trip models ~2× that.

    Never-filled records (expired/canceled/price_drift/stale_pending with
    zero PnL) are EXCLUDED from every stat — no capital was at risk, and
    the live report showed 75 of them among 292 "trades" diluting the
    headline win rate. The excluded count is reported.
    """
    from bot.utils.close_reason import is_filled_close
    total_records = len(trades)
    trades = [t for t in trades
              if is_filled_close(t.get("close_reason"), _net(t))]
    excluded = total_records - len(trades)
    # A close with no PnL record is EXCLUDED from every stat and counted,
    # the way never-filled records are. It is not a loss and not a flat.
    unscored = [t for t in trades if _net(t) is None]
    trades = [t for t in trades if _net(t) is not None]
    nets = [float(_net(t) or 0.0) for t in trades]   # _net is not None here
    # Fees are measured over the closes that CARRY a fee record, and the
    # notional they are measured against is those closes' notional -- a rate
    # diluted by notional nobody was charged for is not the rate. The verdict
    # is withheld unless every close was read.
    fee_read = [(t, _fees(t)) for t in trades if _fees(t) is not None]
    fees_read = len(fee_read)
    fees = sum(float(f or 0.0) for _t, f in fee_read)
    notional = sum(_notional(t) for t, _f in fee_read)
    gross = sum(float(t["gross_pnl"]) if isinstance(t.get("gross_pnl"), (int, float))
                and not isinstance(t.get("gross_pnl"), bool) else float(_net(t) or 0.0)
                for t in trades)
    wins = sum(1 for n in nets if n > 0)
    n = len(trades)
    realized_fee_rate = ((fees / notional) if notional > 0 else 0.0) if fees_read else None
    modeled_fee_rate = 2.0 * (modeled_commission_pct / 100.0)  # round trip
    # Fraction of gross profit eaten by fees (the churn-drag number); None
    # unless every close carried a fee record.
    gross_win = sum(n for n in nets if n > 0)
    fee_vs_model = ((realized_fee_rate / modeled_fee_rate)
                    if (realized_fee_rate is not None and fees_read == n and modeled_fee_rate > 0)
                    else None)
    return {
        "trades": n,
        "excluded_non_fills": excluded,
        "unscored_pnl": len(unscored),
        "win_rate": (wins / n) if n else 0.0,
        "net_pnl": round(sum(nets), 2),
        "gross_pnl": round(gross, 2),
        "pf": _pf(nets),
        "fees_read": fees_read,
        "total_fees": round(fees, 2),
        "notional": round(notional, 2),
        "realized_fee_rate": realized_fee_rate,       # per round trip, of the fee-read notional; None when none read
        "modeled_fee_rate": modeled_fee_rate,
        "fee_vs_model": fee_vs_model,                 # None unless every close carries a fee record
        "fee_drag_of_gross": ((fees / gross_win) if gross_win > 0 else 0.0) if fees_read == n else None,
        "inferred_fills": sum(1 for t in trades if t.get("fill_source") == "ticker_fallback"),
        "by_signal_type": _group(trades, "signal_type"),
        "by_setup": _group(trades, "strategy_type"),
        "by_exit_reason": _group(
            [{**t, "close_reason": _exit_reason(t)} for t in trades],
            "close_reason"),
    }


def _pf_str(pf: float) -> str:
    return "inf" if pf == float("inf") else f"{pf:.2f}"


def _bucket_lines(title: str, stats: dict) -> list[str]:
    if not stats or (len(stats) == 1 and "(unknown)" in stats):
        return []
    lines = [f"  {title}:"]
    for k, g in stats.items():
        sign = "+" if g["net"] >= 0 else ""
        lines.append(f"    {k:<22} {g['trades']:>3} tr  net {sign}${g['net']:>9,.2f}"
                     f"  win {g['win_rate']:.0%}  PF {_pf_str(g['pf'])}")
    return lines


def format_report(s: dict) -> str:
    if not s["trades"]:
        return ("  No closed live trades found. Run the bot live, then re-run this "
                "report against data/closed_trades.json.")
    lines = ["", "  ── LIVE ↔ BACKTEST PARITY " + "─" * 42,
             f"  Live realized: {s['trades']} trades  net ${s['net_pnl']:+,.2f}"
             f"  win {s['win_rate']:.0%}  PF {_pf_str(s['pf'])}"
             + (f"  ({s['excluded_non_fills']} never-filled records excluded)"
                if s.get("excluded_non_fills") else "")
             + (f"  ({s['unscored_pnl']} close(s) with no PnL record excluded)"
                if s.get("unscored_pnl") else ""),
             "  Backtest benchmark (majors_1h, --honest): +0.31% / PF 1.14 — "
             "is live in the same ballpark?"]
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
    if s["inferred_fills"]:
        lines.append(f"  ⚠ {s['inferred_fills']} close(s) inferred (ticker_fallback), "
                     f"not authoritative exchange fills — treat their PnL as approximate")
    lines.extend(_bucket_lines("By signal type", s["by_signal_type"]))
    lines.extend(_bucket_lines("By setup", s["by_setup"]))
    lines.extend(_bucket_lines("By exit reason", s["by_exit_reason"]))
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
