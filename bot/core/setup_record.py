"""A setup's own record, in net R per trade, with an interval verdict.

The setup-expectancy nudge scores a setup by how often it WON: ``[wins,
total]`` per key and nothing else. A win count flatters a setup that loses
money -- many small wins and a few full stops read as a good record -- and
the frozen benchmark is exactly that shape: it wins more than half its
trades and loses (docs/FROZEN_BENCHMARK.md). So the analyze card, which is
where a person decides whether to take a new idea, prints what this kind of
setup has actually RETURNED per trade instead.

THE UNIT IS NET R. The journal records each close's R as net P&L over the
dollar risk its stop defined (``trade_journal.r_multiple_for``), and ``None``
when that risk cannot be read, so a close with no recorded stop is counted
and never averaged in as 0R. R is also the unit that makes trades of
different sizes one population, and it carries no dollar figure, so the card
publishes nothing about any account's size.

THE VERDICT IS AN INTERVAL, NOT A MEAN. "+0.3R over 6 trades" is noise; the
whole 95% interval on the per-trade mean has to clear zero, the discipline the
arb verdict and the parity edge already use (``arb_tracker.mean_interval`` is
the one instrument), with a floor of ``MIN_TRADES`` beside it because a handful
of identical results has a sample spread of zero and an interval at its mean.
Six words: ``edge`` and ``losing`` (the interval is wholly above or below
zero), ``no_edge`` (the floor is met and the interval straddles zero -- a
reading, not "too thin"), ``thin`` (below the floor), ``unscored`` (closes on
record, none with a readable R) and ``none`` (no close on record).

WHICH CLOSES. The journal's, filtered to strategy outcomes by the one rule
the parity card uses: a never-filled order is not a trade
(``close_reason.is_filled_close``) and a post-fill flatten by the executor's
own guards is an execution event, not something the setup did
(``close_reason.is_execution_abort``). A setup is its symbol, the regime the
analyzer read for that symbol WHEN THE TRADE CLOSED (the journal tags it then,
through ``RuneClawEngine._outcome_regime``, and the key for a new idea is read
through the same function, so the two vocabularies match by construction),
and its direction -- the key setup-expectancy uses. When the setup's own
record is below the floor the reading backs off, as the nudge does, to every
symbol in that regime and direction, then to the direction alone, and says so.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Optional

from bot.core.arb_tracker import mean_interval
from bot.utils.close_reason import is_execution_abort, is_filled_close
from bot.utils.win_rate import OUTCOME_FLAT, OUTCOME_LOSS, OUTCOME_WIN, outcome_of

#: Scored closes a tier needs before its interval is a verdict.
MIN_TRADES = 10

#: Most specific first; the order is the backoff order.
TIERS = ("setup", "regime", "direction")

#: The regime word a close carries when the analyzer read none.
_UNKNOWN = "UNKNOWN"


def _norm_symbol(s: object) -> str:
    """``SOL/USDT:USDT``, ``SOLUSDT`` and ``sol/usdt`` are one market."""
    base = str(s or "").strip().upper().split(":", 1)[0].replace("/", "")
    return base[:-4] if base.endswith("USDT") and len(base) > 4 else base


def _norm_regime(r: object) -> str:
    return str(r or "").strip().upper() or _UNKNOWN


def _norm_direction(d: object) -> str:
    return str(getattr(d, "value", d) or "").strip().upper()


def _strategy_close(e: Any) -> bool:
    reason = getattr(e, "exit_reason", "") or ""
    return (is_filled_close(reason, getattr(e, "pnl", None))
            and not is_execution_abort(reason))


def _r_of(e: Any) -> Optional[float]:
    r = getattr(e, "r_multiple", None)
    if isinstance(r, bool) or not isinstance(r, (int, float)):
        return None
    r = float(r)
    return None if r != r or r in (float("inf"), float("-inf")) else r


@dataclass(frozen=True)
class SetupRecord:
    """One tier's record for a setup, and the verdict its interval supports."""
    tier: str                          # which of TIERS answered
    symbol: str
    regime: str
    direction: str
    closes: int                        # strategy closes at this tier
    scored: int                        # of those, with a readable R
    wins: int
    losses: int
    flat: int
    mean_r: Optional[float]
    interval: Optional[tuple]
    verdict: str                       # edge | losing | no_edge | thin | unscored | none
    own_closes: int                    # the setup tier's own closes, for a backed-off tier


def _tier_stats(rows: list) -> dict:
    rs = [r for r in (_r_of(e) for e in rows) if r is not None]
    outcomes = [outcome_of(getattr(e, "pnl", None)) for e in rows]
    mean = sum(rs) / len(rs) if rs else None
    return {"closes": len(rows), "scored": len(rs), "rs": rs, "mean": mean,
            "wins": outcomes.count(OUTCOME_WIN),
            "losses": outcomes.count(OUTCOME_LOSS),
            "flat": outcomes.count(OUTCOME_FLAT)}


def _verdict(stats: dict, min_trades: int) -> tuple[str, Optional[tuple]]:
    if stats["closes"] == 0:
        return "none", None
    if stats["scored"] == 0:
        return "unscored", None
    if stats["scored"] < min_trades:
        return "thin", None
    iv = mean_interval(stats["rs"])
    if iv is None:
        return "thin", None
    lo, hi = iv
    if lo > 0:
        return "edge", iv
    if hi < 0:
        return "losing", iv
    return "no_edge", iv


def setup_record(entries: Iterable[Any], symbol: object, regime: object,
                 direction: object, *, min_trades: int = MIN_TRADES) -> SetupRecord:
    """The record the analyze card prints for a new idea on this setup.

    The most specific tier with ``min_trades`` scored closes answers; when
    none has, the setup's own record answers, so "too few to judge" is said
    about the setup and never about a population it is only part of.
    """
    sym, reg, dirn = _norm_symbol(symbol), _norm_regime(regime), _norm_direction(direction)
    closes = [e for e in entries if _strategy_close(e)
              and _norm_direction(getattr(e, "direction", "")) == dirn]
    in_regime = [e for e in closes if _norm_regime(getattr(e, "regime", "")) == reg]
    own = [e for e in in_regime if _norm_symbol(getattr(e, "symbol", "")) == sym]
    tiers = (("setup", own), ("regime", in_regime), ("direction", closes))
    chosen = None
    for name, rows in tiers:
        stats = _tier_stats(rows)
        if stats["scored"] >= min_trades:
            chosen = (name, stats)
            break
    if chosen is None:
        chosen = ("setup", _tier_stats(own))
    name, stats = chosen
    verdict, iv = _verdict(stats, min_trades)
    return SetupRecord(
        tier=name, symbol=sym, regime=reg, direction=dirn,
        closes=stats["closes"], scored=stats["scored"],
        wins=stats["wins"], losses=stats["losses"], flat=stats["flat"],
        mean_r=None if stats["mean"] is None else round(stats["mean"], 2),
        interval=iv, verdict=verdict, own_closes=len(own))


# ── Rendering ────────────────────────────────────────────────────────────────

_SIDE = {"LONG": "longs", "SHORT": "shorts"}


def _scope(rec: SetupRecord) -> str:
    side = _SIDE.get(rec.direction, rec.direction.lower() or "trades")
    regime = ("an unknown regime" if rec.regime == _UNKNOWN else rec.regime)
    if rec.tier == "setup":
        return f"{rec.symbol} {side} closed in {regime}"
    if rec.tier == "regime":
        return f"{side} closed in {regime}, any symbol ({rec.symbol} itself: {rec.own_closes})"
    return f"{side}, any symbol or regime ({rec.symbol} itself: {rec.own_closes})"


def _count(rec: SetupRecord) -> str:
    wl = f"{rec.wins}W/{rec.losses}L" + (f"/{rec.flat}F" if rec.flat else "")
    unscored = rec.closes - rec.scored
    tail = f", {unscored} with no readable R" if unscored else ""
    return f"{rec.closes} trade{'s' if rec.closes != 1 else ''} ({wl}{tail})"


_VERDICT_WORDS = {
    "edge": "made money per trade",
    "losing": "lost money per trade",
    "no_edge": "no edge measurable either way",
}


def setup_record_line(rec: SetupRecord) -> str:
    """The analyze card's line, plain text (any surface can escape it)."""
    head = f"📒 Record, {_scope(rec)}: "
    if rec.verdict == "none":
        return head + "no closed trade on record."
    if rec.verdict == "unscored":
        return head + f"{_count(rec)}, none with a readable R."
    if rec.verdict == "thin":
        return head + f"{_count(rec)}, too few to judge."
    if rec.verdict in _VERDICT_WORDS and rec.interval is not None and rec.mean_r is not None:
        lo, hi = rec.interval
        return (head + f"{_count(rec)}, {rec.mean_r:+.2f}R per trade "
                f"(95% {lo:+.2f} to {hi:+.2f}): {_VERDICT_WORDS[rec.verdict]}.")
    raise ValueError(f"unplaceable setup record {rec.verdict!r}")
