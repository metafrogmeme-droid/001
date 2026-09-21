"""Why a close was priced by a ticker: the venue lookup's stages, classified.

The 2026-09-21 live parity card read "175 close(s) inferred (ticker_fallback)"
over 194 filled trades, and the operator's log carried 175 copies of one
sentence — "Using ticker price for %s close — exchange history unavailable" —
with no cause. Every stage of ``LiveExecutor._fetch_bitget_close_data``
caught its own exception at DEBUG, so from the log nobody could say whether
the endpoint RAISED (an auth, permission or network fault — one fix), ANSWERED
rows that matched nothing (a matching defect — a different fix), or ANSWERED
no rows at all (a symbol, product-type or window question — a third). That is
the "grep MARGIN MODE MISMATCH coming back empty said nothing at all" shape
this repo records for the leverage read-back: the quiet case reads as
healthy, 175 times over.

This leaf holds the vocabulary and nothing that talks to a venue. The
executor records one ``StageOutcome`` per stage attempt and asks two
questions of the list:

  ``lookup_class``     the ONE word the closed record carries, so next week's
                       parity card can count causes rather than closes
  ``lookup_sentence``  the WARNING, stage by stage, said once per position

Two rules travel with it. The exception's CLASS is recorded and never its
text — a ccxt error string can echo the request, and the request carries the
signature. And a stage that was never asked is ``skipped`` with its reason,
because "history found nothing" and "history was not consulted on this venue"
are different facts with different remedies, and the old code answered them
with one ``None``.
"""

from __future__ import annotations

from typing import Iterable, NamedTuple, Optional, Sequence

#: The stages in order of authority. ``lookup_class`` answers with the
#: outcome of the FIRST stage in this order that was actually asked, because
#: that is the source whose repair would price the most closes.
STAGES = ("history", "fills", "orders")

#: The kinds an attempt can end in when it did not price the close.
KINDS = ("raised", "no_rows", "unmatched", "skipped")

#: What the closed record carries when a build older than this reading wrote
#: it: no outcome at all, which is not any of the four above.
UNRECORDED = "unrecorded"


class StageOutcome(NamedTuple):
    stage: str        # one of STAGES
    kind: str         # one of KINDS
    detail: str = ""  # exception CLASS name, a row count with the nearest gap, or why it was skipped


def lookup_raised(stage: str, exc: BaseException) -> StageOutcome:
    """The stage raised. The class travels; the text never does."""
    return StageOutcome(stage, "raised", type(exc).__name__)


def lookup_no_rows(stage: str) -> StageOutcome:
    """The venue answered, with nothing in it."""
    return StageOutcome(stage, "no_rows", "")


def lookup_unmatched(stage: str, rows: int, nearest_gap_pct: Optional[float] = None,
                     why: str = "") -> StageOutcome:
    """The venue answered ``rows`` rows and none was this position's.
    ``nearest_gap_pct`` is how far the closest entry price was from ours, so
    a 0.6% gap under a 0.5% tolerance is visible as the near miss it is."""
    bits = [f"{rows} row{'s' if rows != 1 else ''}, none matched"]
    if nearest_gap_pct is not None:
        bits.append(f"nearest entry gap {nearest_gap_pct:.2f}%")
    if why:
        bits.append(why)
    return StageOutcome(stage, "unmatched", "; ".join(bits))


def lookup_skipped(stage: str, why: str) -> StageOutcome:
    """The stage was not asked, and ``why`` says so."""
    return StageOutcome(stage, "skipped", why)


def nearest_entry_gap_pct(entry_prices: Iterable[Optional[float]],
                          entry_price: float) -> Optional[float]:
    """The smallest |row entry − ours| / ours over the rows, in percent. None
    with no readable row or no entry price to measure against — a gap
    against an entry of zero is not a measurement."""
    if not entry_price or entry_price <= 0:
        return None
    gaps = [abs(float(p) - entry_price) / entry_price * 100.0
            for p in entry_prices if isinstance(p, (int, float)) and p > 0]
    return min(gaps) if gaps else None


def stage_summary(stage: str, outcomes: Sequence[StageOutcome]) -> Optional[StageOutcome]:
    """One outcome for a stage that may have been attempted several times
    (three history windows, two fill attempts): a raise anywhere is a raise;
    else rows that matched nothing; else an empty answer; else skipped.
    None when the stage never appears."""
    mine = [o for o in outcomes if o.stage == stage]
    if not mine:
        return None
    for kind in ("raised", "unmatched", "no_rows", "skipped"):
        hits = [o for o in mine if o.kind == kind]
        if hits:
            first = hits[0]
            n = len(mine)
            detail = first.detail
            if n > 1 and kind != "skipped":
                detail = f"{detail} ({len(hits)} of {n} attempts)" if detail else f"{len(hits)} of {n} attempts"
            return StageOutcome(stage, kind, detail)
    return mine[0]


def lookup_class(outcomes: Sequence[StageOutcome]) -> str:
    """The word the closed record carries.

    The outcome of the most authoritative stage that was ASKED — ``history
    raised NetworkError``, ``fills unmatched``, ``orders no_rows`` — because
    repairing that stage is what would price the most closes. ``skipped`` when
    no stage was asked at all (no order ids on record, a venue with no
    history channel and nothing to match), and ``unrecorded`` for an empty
    list, which is what a record written before this reading carries.
    """
    if not outcomes:
        return UNRECORDED
    for stage in STAGES:
        s = stage_summary(stage, outcomes)
        if s is None or s.kind == "skipped":
            continue
        if s.kind == "raised":
            return f"{stage} raised {s.detail.split(' (')[0]}" if s.detail else f"{stage} raised"
        return f"{stage} {s.kind}"
    return "skipped"


def lookup_sentence(symbol: str, outcomes: Sequence[StageOutcome]) -> str:
    """The WARNING: every stage, what it did, in stage order."""
    parts = []
    for stage in STAGES:
        s = stage_summary(stage, outcomes)
        if s is None:
            parts.append(f"{stage}: not attempted")
        elif s.kind == "raised":
            parts.append(f"{stage}: raised {s.detail}")
        elif s.kind == "no_rows":
            parts.append(f"{stage}: answered no rows" + (f" ({s.detail})" if s.detail else ""))
        elif s.kind == "unmatched":
            parts.append(f"{stage}: answered {s.detail}")
        else:
            parts.append(f"{stage}: skipped ({s.detail})")
    return f"No venue close data for {symbol} — " + "; ".join(parts)


def is_ticker_priced(fill_source: Optional[str]) -> bool:
    """True when a closed record's exit was a TICKER estimate rather than a
    venue fill — every spelling of it. The executor writes three: the 25227
    path's ``ticker_fallback``, the sweep's ``ticker_fallback_after_N_retries``
    and the bot's own close path's ``ticker_after_bot_close``. The parity
    reader used to compare one exact word, so the sweep's rows were never
    counted as inferred."""
    return str(fill_source or "").startswith("ticker")
