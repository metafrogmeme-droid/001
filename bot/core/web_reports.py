"""
RUNECLAW — Web reports builder.

Gathers the read-only intelligence reports that were previously
Telegram-only — cross-venue funding scan, funding-arb paper tracker,
live↔backtest parity, and the idle-asset yield radar — into one JSON
payload the bot pushes to the website (POST /api/bot/sync/reports) on an
hourly cadence, so the dashboard reaches parity with the bot.

Every section is independently fail-soft: a venue outage, a missing
closed-trades file, or absent Earn credentials just nulls that section
(with a reason) — it never raises out of build_reports_payload and never
touches the trading path. All work here is synchronous/blocking by design;
the caller (proactive monitor) runs it in a worker thread.

Privacy: §4 — GET /api/reports carries NO auth and no limiter, so
funding/arb/parity carry percent, ratio and count only. They did not: the arb
rows shipped `earned_usd` per coin and the parity headline shipped the
operator's `net_pnl` and `total_fees`, the latter two under a header claiming
they were "already public on /track" when /track indexes its equity curve to
100 precisely so no account figure escapes.

`arb.notional_usd` STAYS and is not an exception to that rule. It is a
published CONSTANT (`PAPER_NOTIONAL_USD`) — the hypothetical size the tracker
is denominated in, the same shape as the caller-supplied `stake_usd` the MCP
`run_what_if` tool is allowed to echo — so it discloses nothing about
RUNECLAW's capital, and it is the basis that makes every percent beside it
readable. §4 is about account money, not about every number with a currency
in its name.

The yield section contains OPERATOR account balances — the web app must only
serve it to admin-plan users (app/routes/reports.js enforces this).
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Optional

log = logging.getLogger(__name__)

FUNDING_BASES = ["BTC", "ETH", "SOL", "XRP", "DOGE", "BNB", "AVAX", "LINK"]


def _funding_section() -> Optional[dict]:
    from bot.core.funding_radar import build_comparison
    rows = build_comparison(FUNDING_BASES[:12])
    if not rows:
        return None
    return {"rows": [asdict(r) for r in rows]}


def _arb_section() -> Optional[dict]:
    from bot.core.arb_tracker import (
        PAPER_NOTIONAL_USD, arb_verdict, compute_paper_carry, load_snapshots,
        public_verdict_sentence)
    snapshots = load_snapshots()
    if not snapshots:
        return None
    carries = compute_paper_carry(snapshots)
    verdict = arb_verdict(carries)
    return {
        "notional_usd": PAPER_NOTIONAL_USD,
        "snapshots": len(snapshots),
        "carries": [_carry_row(c, PAPER_NOTIONAL_USD) for c in carries[:10]],
        # The verdict on the PUBLIC wire, in percent of the notional: the
        # state, the sentence the panel prints, and the numbers it is made
        # of — no dollar figure, because /api/reports is served to anyone.
        "verdict": {
            "state": verdict.state,
            "sentence": public_verdict_sentence(verdict),
            "scored": verdict.scored,
            "total": verdict.total,
            "held_hours": verdict.held_hours,
            "fee_pct": round(100.0 * verdict.fee_usd / verdict.notional, 4),
            "mean_net_pct": verdict.mean_net_pct,
            "interval_pct": list(verdict.interval_pct) if verdict.interval_pct else None,
        },
    }


def _carry_row(c, notional: float) -> dict:
    """A carry row for the wire: percent of the notional, never dollars.

    §4 — /api/reports is served to ANYONE (no auth, no limiter), so this row
    carries percent / ratio / count only. The sibling `verdict` three lines up
    has said so in its own comment since it was written, and the fix reached
    the verdict and stopped there: `earned_usd` went on the public wire per
    coin, and the per-entry sample list was popped while the total it sums to
    stayed. Ask which OTHER field on the same payload makes the same claim.

    `earned_pct` is that carry as a percent of the tracked notional — the same
    denominator `verdict.mean_net_pct` already uses, so the row and the
    verdict beneath it are in one unit. `None` when the carry could not be
    read: an unreadable carry is not a break-even one.
    """
    row = asdict(c)
    row.pop("entry_carry_usd", None)
    earned = row.pop("earned_usd", None)
    row["earned_pct"] = _pct_of(earned, notional)
    return row


def _pct_of(value, notional: float) -> Optional[float]:
    """`value` as a percent of `notional`, or None when it is not a reading.

    FINITE, not merely a float. `isinstance(float("nan"), float)` is True, so
    a type check alone puts a NaN on the wire — and `json.dumps` writes it as
    a bare `NaN`, which is not valid JSON, so a strict parser at the other end
    fails the whole public read over one unreadable coin. An infinity is the
    same shape and renders as `Infinity%` on the card. Both are junk wearing
    the units of a measurement, which is what `price_on_record` exists to
    refuse one surface over.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    v = float(value)
    if v != v or v in (float("inf"), float("-inf")):
        return None
    if not notional or notional <= 0:
        return None
    return round(100.0 * v / notional, 4)


def _parity_section(engine) -> Optional[dict]:
    from bot.backtest.parity import load_closed_trades, parity_summary
    from bot.config import CONFIG
    executor = getattr(engine, "live_executor", None)
    path = getattr(executor, "_closed_trades_file", None)
    if not path:
        return None
    trades = load_closed_trades(path)
    if not trades:
        return None
    summary = parity_summary(trades, CONFIG.risk.commission_pct)
    # Headline stats only — the bucket breakdowns stay in Telegram /parity
    # (they're long) and the web panel links there for the full report.
    #
    # §4 — AND THE HEADER'S "already public on /track" WAS NOT TRUE OF THE
    # DOLLARS. Driven, `track.js` publishes `equity_curve_idx` (indexed to 100
    # precisely so no account size escapes), `win_rate_pct` and
    # `profit_factor`, and no dollar figure at all. `net_pnl` and `total_fees`
    # are the OPERATOR's realized net and fees paid on the live book, and this
    # route has no auth and no limiter — so it was strictly more revealing
    # than the page it named as its precedent. That is the get_track_record
    # defect `app/test/public_no_dollars.test.js` records in its own header,
    # one route over, with the same justifying sentence.
    #
    # `pf` is the net expressed as a ratio and `fee_drag_of_gross` is what
    # `total_fees` was there to say — fees as a share of gross profit, and
    # already None when the fee record covers only some closes, so a partial
    # record cannot read as a measured drag.
    keep = ("trades", "excluded_non_fills", "unscored_pnl", "win_rate", "pf",
            "fees_read", "realized_fee_rate", "modeled_fee_rate",
            "fee_vs_model", "fee_drag_of_gross", "inferred_fills")
    out = {k: summary.get(k) for k in keep}
    # The verdict's WORDS and the benchmark's scale-free figures travel; the
    # sentences do not, because each carries a per-trade dollar mean, and the
    # benchmark's pooled net is a dollar figure too (a simulated one, on a
    # public route — the rule is about the shape, not the account).
    v = summary.get("verdict") or {}
    b = summary.get("benchmark") or {}
    out["aborts"] = (summary.get("aborts") or {}).get("trades")
    out["verdict"] = {k: v.get(k) for k in ("edge", "ballpark", "n", "in_universe",
                                            "outside", "inferred", "min_trades")}
    out["benchmark"] = {k: b.get(k) for k in ("state", "reason", "dataset", "recorded_at",
                                              "code_sha", "folds_run", "profitable_folds",
                                              "mean_oos_return_pct", "pooled_trades",
                                              "pooled_win_rate", "pooled_pf")}
    return out


def _yield_section(engine) -> Optional[dict]:
    """OPERATOR-SENSITIVE (account balances) — web serves admins only."""
    from bot.core.bitget_v3_client import BitgetV3Client
    from bot.core.yield_radar import build_report
    client = BitgetV3Client.from_config()
    if not client.has_credentials:
        return None
    # None means "could not read", and build_report has a path for exactly
    # that: it marks the report INCOMPLETE and leaves the futures row out
    # because we do not know, not because there is nothing there. This used
    # to coerce the None that live_balance_cached returns by design into 0.0,
    # which took the second path and presented a spot-only total as the
    # whole picture -- honest plumbing on both sides, defeated in between.
    free_usdt: Optional[float] = None
    try:
        _fn = getattr(engine, "live_balance_cached", None)
        cache = (_fn() if callable(_fn) else None) or {}
        _free = cache.get("free")
        if isinstance(_free, (int, float)) and not isinstance(_free, bool):
            free_usdt = float(_free)      # a real 0.0 stays 0.0: nothing idle
    except Exception:
        pass
    report = build_report(client, futures_free_usdt=free_usdt)
    if report.error and not report.rows:
        return None
    return {
        "total_idle_usd": report.total_idle_usd,
        "total_est_year_usd": report.total_est_year_usd,
        "rows": [asdict(r) for r in report.rows[:12]],
        # Non-empty when the futures margin could not be read: the totals
        # above are then a floor, and the panel must say so beside them.
        "incomplete": report.incomplete or "",
    }


def build_reports_payload(engine=None) -> dict:
    """Assemble all report sections; each fails soft to None independently."""
    payload: dict = {"generated_at": datetime.now(UTC).isoformat()}
    for key, builder, needs_engine in (
        ("funding", _funding_section, False),
        ("arb", _arb_section, False),
        ("parity", _parity_section, True),
        ("yield", _yield_section, True),
    ):
        try:
            if needs_engine and engine is None:
                payload[key] = None
                continue
            payload[key] = builder(engine) if needs_engine else builder()
        except Exception as exc:  # noqa: BLE001 — sections must fail independently
            log.debug("web report section %s skipped: %s", key, exc)
            payload[key] = None
    return payload
