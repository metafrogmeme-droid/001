"""
Funding-arb paper tracker — evidence before capital.

The funding radar (bot.core.funding_radar) shows cross-venue spreads at a
moment in time; this module answers the question that actually gates the
roadmap's capture strategy: *"had we run a delta-neutral pair on these
spreads, what would it have earned — and does it survive fees?"*

It records hourly spread snapshots to ``data/learning/funding_arb.jsonl``
and, at read time, accrues hypothetical carry on a FIXED paper notional:
while a coin's spread stays at or above the entry threshold, the pair is
treated as continuously held and earns ``spread_apr`` pro-rata over the
observed interval. Gaps longer than a few hours break the position (we
refuse to extrapolate across unobserved time).

Strictly paper: nothing here places, sizes, or even proposes an order.
The /arb report includes the fee reality check — a real pair pays 4 taker
fees (open+close on two venues) plus slippage, which at ~0.06%/side is
~0.24% of notional; spreads must out-earn that before the strategy is
worth gating in.
"""

from __future__ import annotations

import json
import logging
import math
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from bot.compat import UTC

log = logging.getLogger(__name__)

_RECORD_FILE = (Path(__file__).resolve().parent.parent.parent
                / "data" / "learning" / "funding_arb.jsonl")

PAPER_NOTIONAL_USD = 1_000.0     # fixed hypothetical size per tracked pair
DEFAULT_MIN_SPREAD_APR = 3.0     # % / yr — below this the pair is "flat"
MAX_GAP_HOURS = 3.0              # unobserved gaps break the paper position
ROUND_TRIP_FEE_PCT = 0.24        # 4 taker legs @ ~0.06% — the reality check

_HOURS_PER_YEAR = 24 * 365

# ── the verdict's floors ─────────────────────────────────────────────────────
# The card printed a total paper carry and a fee sentence and no verdict, and
# a reader with a total in front of them makes one — from a sum over however
# few entries happened to accrue it. The verdict is the discipline the voter
# card and the shadow scoreboard already use: the WHOLE 95% interval on the
# per-entry net (carry earned during the on-period minus the round-trip fee)
# has to clear zero, and the sample has to be one an interval means anything
# on. MIN_VERDICT_ENTRIES sits beside the interval for the reason
# MIN_GATE_TRADES does: three entries that all earned the same carry have a
# sample sd of zero, hence a lower bound at the mean, from three entries.
MIN_VERDICT_ENTRIES = 10
MIN_VERDICT_HELD_HOURS = 72.0
_Z = 1.96          # ~95%, the z the other two readings use


def _spread_of(snapshot: dict) -> Optional[float]:
    """The snapshot's spread, or None when it carries none — absent is not
    a flat spread, and the one place this is read as a fact about an EXIT
    must not turn an unreadable row into an observed one."""
    v = snapshot.get("spread_apr")
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _env_f(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except (TypeError, ValueError):
        return default


def snapshot_opportunities(bases: Optional[list[str]] = None) -> int:
    """Fetch the current cross-venue comparison and append one snapshot line
    per >=2-venue coin. Returns rows written (0 on any failure — best-effort,
    called from the proactive monitor's background thread)."""
    from bot.core.funding_radar import build_comparison
    bases = bases or ["BTC", "ETH", "SOL", "XRP", "DOGE", "BNB", "AVAX", "LINK"]
    try:
        rows = build_comparison(bases[:20])
    except Exception as exc:
        log.debug("arb tracker: comparison failed: %s", exc)
        return 0
    if not rows:
        return 0
    ts = datetime.now(UTC).isoformat()
    lines = [json.dumps({
        "ts": ts, "base": r.base, "rates": r.rates,
        "spread_apr": round(r.spread_apr, 4),
        "long_venue": r.long_venue, "short_venue": r.short_venue,
    }) for r in rows]
    try:
        _RECORD_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(_RECORD_FILE, "a") as f:
            f.write("\n".join(lines) + "\n")
    except OSError as exc:
        log.debug("arb tracker: write failed: %s", exc)
        return 0
    return len(lines)


def load_snapshots(path: Optional[Path] = None) -> list[dict]:
    p = Path(path) if path else _RECORD_FILE
    if not p.exists():
        return []
    out = []
    for line in p.read_text().splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


@dataclass
class PaperCarry:
    base: str
    earned_usd: float = 0.0      # accrued carry on the paper notional
    held_hours: float = 0.0      # time the pair counted as "on"
    observed_hours: float = 0.0  # total observed span (incl. flat time)
    entries: int = 0             # distinct on-periods
    last_spread_apr: float = 0.0
    venues: str = ""             # latest long/short pairing
    #: Gross carry of each CLOSED on-period, in order — the samples the
    #: verdict's interval is over. An on-period still running at the end of
    #: the record is not in it: its fee is not yet paid and its carry is
    #: partial, so it is counted (`open_entry`) and never scored.
    entry_carry_usd: list = field(default_factory=list)
    open_entry: bool = False


def compute_paper_carry(snapshots: list[dict],
                        notional: float = PAPER_NOTIONAL_USD,
                        min_spread_apr: Optional[float] = None,
                        max_gap_hours: float = MAX_GAP_HOURS) -> list[PaperCarry]:
    """Accrue hypothetical carry per coin from the snapshot history.

    Between consecutive snapshots of the same coin (gap <= max_gap_hours):
    if the EARLIER snapshot's spread was at/above the threshold, the pair is
    treated as held for that interval and earns spread_apr pro-rata on the
    notional. Unobserved gaps and sub-threshold intervals earn nothing —
    the estimator only credits time it actually watched.
    """
    if min_spread_apr is None:
        min_spread_apr = _env_f("ARB_MIN_SPREAD_APR", DEFAULT_MIN_SPREAD_APR)
    per_base: dict[str, list[tuple[datetime, dict]]] = {}
    for s in snapshots:
        try:
            ts = datetime.fromisoformat(str(s.get("ts")))
            ts = ts if ts.tzinfo else ts.replace(tzinfo=UTC)
        except (ValueError, TypeError):
            continue
        per_base.setdefault(str(s.get("base", "")).upper(), []).append((ts, s))

    out: list[PaperCarry] = []
    for base, seq in per_base.items():
        if not base:
            continue
        seq.sort(key=lambda p: p[0])
        pc = PaperCarry(base=base)
        was_on = False
        entry_usd = 0.0

        def _close_entry() -> None:
            # The on-period ended (a flat interval or an unobserved gap): its
            # gross carry is one sample.
            pc.entry_carry_usd.append(entry_usd)

        for (t0, s0), (t1, _s1) in zip(seq, seq[1:]):
            gap_h = (t1 - t0).total_seconds() / 3600.0
            if gap_h <= 0 or gap_h > max_gap_hours:
                if was_on:
                    _close_entry()
                was_on = False
                continue
            pc.observed_hours += gap_h
            spread = float(s0.get("spread_apr", 0) or 0)
            if spread >= min_spread_apr:
                earned = notional * (spread / 100.0) * (gap_h / _HOURS_PER_YEAR)
                pc.earned_usd += earned
                pc.held_hours += gap_h
                if not was_on:
                    pc.entries += 1
                    entry_usd = 0.0
                entry_usd += earned
                was_on = True
            else:
                if was_on:
                    _close_entry()
                was_on = False
        # The last interval decides the last period's fate by its EARLIER
        # snapshot, so `was_on` says whether that interval earned — not
        # whether the pair is still on. The LAST snapshot says that: a spread
        # still at or above the threshold is a position that has not closed
        # (counted, never scored); one below it is an exit that was observed,
        # and the period closes on it like any other.
        if was_on:
            last_spread = _spread_of(seq[-1][1])
            if last_spread is None or last_spread >= min_spread_apr:
                # Still on — or a row whose spread cannot be read, which is
                # not an observed exit: counted, never scored.
                pc.open_entry = True
            else:
                _close_entry()
        last = seq[-1][1]
        pc.last_spread_apr = float(last.get("spread_apr", 0) or 0)
        pc.venues = f"long {last.get('long_venue', '?')} / short {last.get('short_venue', '?')}"
        if pc.observed_hours > 0:
            out.append(pc)
    out.sort(key=lambda p: p.earned_usd, reverse=True)
    return out


# ── the verdict ──────────────────────────────────────────────────────────────

VERDICT_STATES: tuple[str, ...] = ("survives", "does_not", "thin", "unread")


@dataclass(frozen=True)
class ArbVerdict:
    """Does the recorded paper carry survive the fee a real pair pays?

    Four outcomes, and only one of them is a pass: ``survives`` (the whole
    interval on the per-entry net is above zero, on a sample past both
    floors), ``does_not`` (the whole interval is below zero, same floors),
    ``thin`` (a floor unmet, or an interval that straddles zero — the record
    cannot say either way), ``unread`` (the record could not be read — not an
    empty one). ``scored`` is the closed entries the interval is over,
    ``total`` counts the open ones beside them; the mean is per closed entry,
    net of the round-trip fee, in dollars of the paper notional.
    """
    state: str
    reason: str
    notional: float = PAPER_NOTIONAL_USD
    fee_usd: float = 0.0            # one round trip on the notional
    scored: int = 0                 # closed entries (the interval's sample)
    total: int = 0                  # closed + still-open entries
    coins: int = 0
    held_hours: float = 0.0
    observed_hours: float = 0.0
    gross_usd: float = 0.0          # carry over the CLOSED entries
    net_usd: float = 0.0            # gross minus scored × fee
    mean_net_usd: Optional[float] = None
    interval_usd: Optional[tuple] = None   # (lo, hi) on the per-entry net

    @property
    def mean_net_pct(self) -> Optional[float]:
        """The per-entry net as a percent of the notional — the public wire's
        spelling, because a dollar figure has no business on a public payload
        even when the dollars are hypothetical."""
        return None if self.mean_net_usd is None else round(
            100.0 * self.mean_net_usd / self.notional, 4)

    @property
    def interval_pct(self) -> Optional[tuple]:
        if self.interval_usd is None:
            return None
        lo, hi = self.interval_usd
        return (round(100.0 * lo / self.notional, 4), round(100.0 * hi / self.notional, 4))


def _mean_interval(samples: list[float], z: float = _Z) -> Optional[tuple]:
    """The 95% normal interval on the mean of ``samples``; None below two.
    The shadow scoreboard's instrument (`mean_r_interval`), for the same
    reason it is not Wilson: a signed magnitude per entry, not a proportion."""
    n = len(samples)
    if n < 2:
        return None
    mean = sum(samples) / n
    var = max(0.0, sum((x - mean) ** 2 for x in samples) / (n - 1))
    margin = z * math.sqrt(var / n)
    return (round(mean - margin, 4), round(mean + margin, 4))


def arb_verdict(carries: list[PaperCarry], *, notional: float = PAPER_NOTIONAL_USD,
                fee_pct: float = ROUND_TRIP_FEE_PCT,
                min_entries: int = MIN_VERDICT_ENTRIES,
                min_held_hours: float = MIN_VERDICT_HELD_HOURS) -> ArbVerdict:
    """The verdict over every coin's closed entries, pooled.

    Pooled, because the question the roadmap asks is whether THE STRATEGY
    survives fees, not whether one coin did on one week; a per-coin verdict
    over three entries each is the per-setup key space `setup_expectancy`
    could never fill. Every closed entry pays one round trip.
    """
    fee = notional * fee_pct / 100.0
    samples = [c - fee for pc in carries for c in pc.entry_carry_usd]
    scored = len(samples)
    total = scored + sum(1 for pc in carries if pc.open_entry)
    held = sum(pc.held_hours for pc in carries)
    observed = sum(pc.observed_hours for pc in carries)
    gross = sum(c for pc in carries for c in pc.entry_carry_usd)
    base = dict(notional=notional, fee_usd=round(fee, 4), scored=scored, total=total,
                coins=len(carries), held_hours=round(held, 2),
                observed_hours=round(observed, 2), gross_usd=round(gross, 4),
                net_usd=round(gross - scored * fee, 4))
    if not carries:
        return ArbVerdict("thin", "no tracked history yet — nothing to score", **base)
    mean = (sum(samples) / scored) if scored else None
    interval = _mean_interval(samples)
    base.update(mean_net_usd=None if mean is None else round(mean, 4),
                interval_usd=interval)
    open_note = f" ({total - scored} still open, not scored)" if total > scored else ""
    floors = (f"the bar is {min_entries} closed entries and {min_held_hours:.0f}h held")
    if scored < min_entries or held < min_held_hours or interval is None:
        return ArbVerdict("thin",
                          f"record too thin — {scored} closed entr{'y' if scored == 1 else 'ies'}"
                          f"{open_note} over {held:.0f}h held; {floors}", **base)
    lo, hi = interval
    n_words = f"{scored} closed entries{open_note}, {held:.0f}h held"
    if lo > 0:
        return ArbVerdict("survives",
                          f"survives fees — mean net ${mean:+.2f} per entry after the "
                          f"${fee:.2f} round trip, 95% interval ${lo:+.2f}..${hi:+.2f}, "
                          f"{n_words}", **base)
    if hi < 0:
        return ArbVerdict("does_not",
                          f"does not survive fees — mean net ${mean:+.2f} per entry after "
                          f"the ${fee:.2f} round trip, 95% interval ${lo:+.2f}..${hi:+.2f}, "
                          f"{n_words}", **base)
    return ArbVerdict("thin",
                      f"record too thin to say — mean net ${mean:+.2f} per entry, but the "
                      f"95% interval ${lo:+.2f}..${hi:+.2f} straddles zero over {n_words}",
                      **base)


def public_verdict_sentence(v: ArbVerdict) -> str:
    """The verdict in percent of the notional — no dollar figure, for the
    public reports payload. Same four states, same numbers, different unit."""
    if v.state == "unread":
        return f"could not read the record ({v.reason})"
    if v.state == "thin" and v.mean_net_usd is None:
        return v.reason
    pct = v.mean_net_pct
    fee_pct = round(100.0 * v.fee_usd / v.notional, 4) if v.notional else 0.0
    open_note = f" ({v.total - v.scored} still open, not scored)" if v.total > v.scored else ""
    if v.interval_pct is None:
        return (f"record too thin — {v.scored} closed entries{open_note} over "
                f"{v.held_hours:.0f}h held")
    lo, hi = v.interval_pct
    words = {"survives": "survives fees", "does_not": "does not survive fees",
             "thin": "record too thin to say"}[v.state]
    return (f"{words} — mean net {pct:+.3f}% of notional per entry after the {fee_pct:.2f}% "
            f"round trip, 95% interval {lo:+.3f}%..{hi:+.3f}%, {v.scored} closed "
            f"entries{open_note}, {v.held_hours:.0f}h held")


def arb_reading(path: Optional[Path] = None) -> tuple[list[PaperCarry], ArbVerdict]:
    """The record, read once: the carries and the verdict over them.

    ``unread`` is the third answer beside "empty" and "scored": `load_snapshots`
    answers [] for a file that is not there (no history yet, a real empty)
    and RAISES for one that is there and cannot be read, and the two used to
    reach the card as the same "report failed" line."""
    try:
        snapshots = load_snapshots(path)
    except Exception as exc:
        log.warning("arb tracker: record unreadable: %s", type(exc).__name__)
        return [], ArbVerdict("unread", f"could not read the record: {type(exc).__name__}")
    carries = compute_paper_carry(snapshots)
    return carries, arb_verdict(carries)


def format_arb_html(carries: list[PaperCarry], current_rows=None,
                    notional: float = PAPER_NOTIONAL_USD,
                    verdict: Optional[ArbVerdict] = None) -> str:
    """Telegram-HTML report: tracked paper carry + fee reality check + the
    verdict. ``verdict=None`` means the caller did not compute one, and the
    card says nothing either way — it never derives one from the total."""
    lines = ["🧪 <b>Funding-arb paper tracker</b>"]
    if verdict is not None and verdict.state == "unread":
        lines.append(f"🔴 Verdict: <b>{verdict.reason}</b> — the paper record on disk "
                     "did not open, so nothing below was scored.")
        return "\n\n".join(lines)
    if current_rows:
        top = current_rows[0]
        lines.append(
            f"Widest spread now: <b>{top.base}</b> "
            f"<code>{top.spread_apr:.1f}%/yr</code> "
            f"(long {top.long_venue} / short {top.short_venue})")
    if not carries:
        lines.append(
            "No tracked history yet — snapshots accrue hourly once the bot "
            "runs with <code>ARB_TRACKER_ENABLED=true</code> (default on).")
        return "\n\n".join(lines)

    total = sum(c.earned_usd for c in carries)
    total_entries = sum(c.entries for c in carries)
    fee_cost = notional * ROUND_TRIP_FEE_PCT / 100.0
    for c in carries[:8]:
        days = c.observed_hours / 24.0
        lines.append(
            f"<b>{c.base}</b>: paper carry <code>${c.earned_usd:+.2f}</code> "
            f"on ${notional:,.0f} · held {c.held_hours:.0f}h of "
            f"{days:.1f}d observed · {c.entries} entr{'y' if c.entries == 1 else 'ies'}\n"
            f"- now <code>{c.last_spread_apr:.1f}%/yr</code> ({c.venues})")
    lines.append(
        f"Total paper carry: <code>${total:+.2f}</code> across "
        f"{len(carries)} coin(s)")
    if verdict is not None:
        icon = {"survives": "🟢", "does_not": "🔴"}.get(verdict.state, "🟡")
        lines.append(f"{icon} Verdict: <b>{verdict.reason}</b>")
    lines.append(
        f"<i>Fee reality check: a REAL pair pays ~{ROUND_TRIP_FEE_PCT:.2f}% "
        f"of notional per round trip (≈${fee_cost:.2f} on ${notional:,.0f}) "
        f"— {total_entries} tracked entr{'y' if total_entries == 1 else 'ies'} "
        f"would have cost ≈${fee_cost * total_entries:.2f}. Carry must beat "
        "that before the capture strategy is worth gating in. This tracker "
        "is 100% paper — it never places orders.</i>")
    return "\n\n".join(lines)
