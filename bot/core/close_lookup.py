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

A third reading arrived with the 2026-09-28 parity card, which counted
``history raised ExchangeError`` on every close the reading had seen. The
executor builds its Bitget client with ``options["uta"] = True`` (a unified
trading account), so every ccxt read it makes -- fills, closed orders -- is
routed to the ``/api/v3`` family, while the history stage's one RAW call went
to the classic ``/api/v2/mix/position/history-position``, which a unified
account refuses with a code the pinned ccxt leaves as the bare
``ExchangeError``. The most authoritative stage was dead on that deployment,
on every close. ``client_is_uta`` reads the fact off the client in hand, and
``uta_history_row`` spells a v3 row in the v2 vocabulary the one reader knows,
so the reader stays one reader.
"""

from __future__ import annotations

from typing import Any, Iterable, NamedTuple, Optional, Sequence

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


def lookup_raised(stage: str, exc: BaseException, channel: str = "") -> StageOutcome:
    """The stage raised. The class travels; the text never does. ``channel``
    names WHICH endpoint family was asked when the stage has more than one
    (the history stage: ``v3`` on a unified account), so next week's WARNING
    can say which door refused; it stays out of ``lookup_class``, which is
    the cause a record carries, because a class is a thing to fix and the
    channel is where it was met."""
    detail = type(exc).__name__
    if channel:
        detail = f"{detail} ({channel})"
    return StageOutcome(stage, "raised", detail)


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


def client_is_uta(exchange: Any) -> bool:
    """Whether the ccxt client in hand speaks the unified-account (``/api/v3``)
    family. Read off the client's own options -- the fact that routes every
    ccxt call it makes -- and never inferred from a probe: a stand-in whose
    options are not a dict answers False, so a test double is the classic
    client it has always been."""
    opts = getattr(exchange, "options", None)
    return isinstance(opts, dict) and opts.get("uta") is True


#: The unified-account position-history row (``GET /api/v3/position/
#: history-position``, docs/bitget-uta/trade.md) spelled in the classic v2
#: row's vocabulary, which is the one the history reader knows. Only names
#: that differ are listed; ``netProfit`` and ``totalFunding`` are spelled
#: the same on both. The v3 row carries no ``closeType`` and no ``leverage``,
#: so the reason is inferred from the exit price and the leverage is absent,
#: which is what the reader already does for a v2 row missing either.
UTA_HISTORY_FIELDS = {
    "openPriceAvg": "openAvgPrice",
    "closePriceAvg": "closeAvgPrice",
    "cumRealisedPnl": "pnl",           # "Excluding fees and funding costs": the gross
    "openFeeTotal": "openFee",
    "closeFeeTotal": "closeFee",
    "posSide": "holdSide",
    "createdTime": "ctime",
    "updatedTime": "utime",
}


def uta_history_row(row: Any) -> dict:
    """A v3 history row as the v2 reader reads it. Every key the row carries
    is kept; the ones the reader knows by another name are added under that
    name. A row that is not a dict is handed back empty, which the reader
    matches to nothing rather than raising inside its own ``except``."""
    if not isinstance(row, dict):
        return {}
    out = dict(row)
    for uta_name, v2_name in UTA_HISTORY_FIELDS.items():
        if uta_name in row and v2_name not in row:
            out[v2_name] = row[uta_name]
    return out


#: What a closed row's ``commission`` is MADE OF (``fee_basis`` on the record).
#: The parity card's "realized" fee rate used to count any numeric commission
#: as a fee record, and on a record 182 of 211 ticker-priced the commission on
#: those rows was the configured entry rate plus the configured exit rate --
#: the model compared with itself, printed as "realized 0.093%/round-trip vs
#: modeled 0.200% (better than model)". A rate is realized only where the
#: venue stated the whole round trip.
FEE_VENUE = "venue"          # the venue stated both legs (a position-history row)
FEE_CLOSE_LEG = "close_leg"  # the venue stated the close leg; the entry leg is estimated
FEE_ESTIMATED = "estimated"  # both legs at the configured rates
FEE_BASES = (FEE_VENUE, FEE_CLOSE_LEG, FEE_ESTIMATED)


def fee_stated(row: Any) -> bool:
    """Whether a closed row's commission is the venue's own round trip. A row
    from a build that recorded no basis answers False: not knowing is not a
    statement, and a rate over such rows is not a realized one."""
    return isinstance(row, dict) and row.get("fee_basis") == FEE_VENUE


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
