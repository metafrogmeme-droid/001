"""Replays the published handoff cases.

The equity print and the weekend gaps are inputs. The book is
constructed so the bridge has a depth to weigh; it is not an archive
of fills. Scores belong to the tests.
"""
from __future__ import annotations

from bot.fvb.book import Book, Level
from bot.fvb.pricer import Pricer, Reading

# Published NXT pre-market print, in KRW per share. The prior is the
# print divided by one minus the published drop, not a second quote.
SK_PRINT = 1_272_000.0
SK_DROP = 0.2996
SK_SYMBOL = "000660"
SK_BASIS = "published pre-market print replay; constructed book; not a live quote"


def sk_prior() -> float:
    return SK_PRINT / (1.0 - SK_DROP)


def deep_book(mid: float, venue: str = "book") -> Book:
    """A book that can fill the reference size without one level dominating."""
    step = mid * 0.0002
    asks = tuple(Level(mid + step * (i + 1), 20.0) for i in range(6))
    bids = tuple(Level(max(step, mid - step * (i + 1)), 20.0) for i in range(6))
    return Book(venue=venue, mid=mid, bids=bids, asks=asks, ts=0.0)


def weekend_gaps() -> list[float]:
    """Twenty deterministic reopen gaps, as fractions. None is zero."""
    gaps: list[float] = []
    for i in range(20):
        bps = ((i * 17) % 80) - 40
        if bps == 0:
            bps = 7
        gaps.append(bps / 10_000.0)
    return gaps


def run_sk_hynix(pricer: Pricer | None = None) -> list[Reading]:
    """Warm the book, then one thin pre-market print that the book does not follow."""
    engine = pricer if pricer is not None else Pricer()
    prior = sk_prior()
    book = deep_book(prior, "hip3")
    for step in range(-40, 0, 10):
        engine.ingest_print(
            SK_SYMBOL, ts=float(step), session="regular", external=prior,
            books=[book], volume=1_000.0, venue="nxt", unit="KRW", basis="warmup",
        )
    out: list[Reading] = []
    for step in range(0, 121):
        # One share on the opening print. Later ticks are the same print
        # still on the tape, not another share each second — a hundred
        # restatements would clear the volume rule on their own.
        traded = 1.0 if step == 0 else None
        out.append(engine.ingest_print(
            SK_SYMBOL, ts=float(step), session="pre", external=SK_PRINT,
            books=[book], volume=traded, venue="nxt", unit="KRW", basis=SK_BASIS,
        ))
    return out


def run_weekends(pricer: Pricer | None = None) -> list[Reading]:
    """Twenty symbol-weekends. The reopen print is confirmed on that tick."""
    engine = pricer if pricer is not None else Pricer()
    out: list[Reading] = []
    for index, gap in enumerate(weekend_gaps()):
        symbol = f"EQ{index + 1}"
        close = 100.0
        reopen = close * (1.0 + gap)
        mid = close * (1.0 + 0.816 * gap)
        book = deep_book(mid, "hip3")
        engine.ingest_print(
            symbol, ts=0.0, session="closed", external=close,
            books=[book], volume=500.0, venue="primary", unit="index", basis="weekend replay",
        )
        engine.ingest_print(
            symbol, ts=40.0, session="closed", external=close,
            books=[book], volume=500.0, venue="primary", unit="index", basis="weekend replay",
        )
        out.append(engine.ingest_print(
            symbol, ts=80.0, session="regular", external=reopen, books=[book],
            volume=500.0, venue="primary", corroborations=[("second", reopen)],
            unit="index", basis="weekend replay",
        ))
    return out
