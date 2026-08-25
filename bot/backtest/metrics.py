"""Aggregating backtest metrics without inventing an edge that is not there.

WHAT THIS EXISTS FOR

`backtest_deep_results.json` (2026-08-07, 500 runs) reported:

    avg_profit_factor: 19.17

Read alone — which is how a one-line summary gets read — that is a spectacular
edge. Every other figure in the same block disagreed:

    avg_return_pct: -0.46     avg_sharpe: -1.24     avg_sortino: -0.18
    profitable runs: 113 of 485 (23%)     median profit factor: 0.45

The 19.17 came from averaging in ``PF_UNDEFINED`` — the sentinel a run gets
when it had **no losing trades at all**, so gross-loss is zero and the ratio
does not exist. Nine runs out of 485, 1.9% of the sample, moved the headline
from 0.62 to 19.17.

That is the CLAUDE.md shape almost exactly — "sum(...) over a set that includes
unreadable rows — a partial total, printed as whole" — with the aggravation
that the poisoned value means *undefined* and reads as *outstanding*, on the
single number a person checks first when asking "does this strategy work?"

TWO RULES, AND THE SECOND IS NOT ABOUT SENTINELS

1. A sentinel never enters a mean. It is not a measurement.
2. **The headline for a ratio is the MEDIAN.** Profit factor is unbounded above
   and floored at zero, so its mean is dragged by a handful of lucky runs even
   with every sentinel removed. Here: mean 0.62 vs median 0.45 over the defined
   runs — same verdict, but the mean flatters. Report the median, and carry the
   mean beside it rather than instead of it.

Undefined runs are COUNTED AND STATED, never silently dropped: "9 runs had no
losing trades" is real information about the sample, and a reader who is told
nothing cannot tell an excluded row from one that never existed.
"""

from __future__ import annotations

import statistics
from typing import Iterable, NamedTuple

#: A run that won and never lost. The ratio is undefined; this is the marker.
#: Kept as a float rather than None because BacktestResult.profit_factor is
#: typed float and serialises to JSON, where inf is not representable.
PF_UNDEFINED = 999.99

#: At or above this, a value is the sentinel rather than a measurement. A plain
#: `== PF_UNDEFINED` would miss a value that survived a float round-trip.
PF_UNDEFINED_FLOOR = 999.0


class ProfitFactorSummary(NamedTuple):
    """What can honestly be said about a set of profit factors."""

    median: float | None       # the headline; None when nothing was defined
    mean: float | None         # beside it, never instead of it
    n_defined: int             # runs whose ratio exists
    n_undefined: int           # runs with no losing trades
    n_profitable: int          # defined runs scoring above 1.0

    def render(self) -> str:
        """One line, stating what is measured and what is merely counted."""
        if self.median is None:
            # Not "0.00". No run produced a defined ratio, and a zero here
            # would read as "every run lost", which is the opposite of what an
            # all-undefined sample means.
            return (f"profit factor: no run had a defined ratio "
                    f"({self.n_undefined} had no losing trades)")
        out = (f"profit factor: median {self.median:.2f} "
               f"(mean {self.mean:.2f} over {self.n_defined} defined run(s); "
               f"{self.n_profitable} profitable)")
        if self.n_undefined:
            out += f"; {self.n_undefined} run(s) had no losing trades — ratio undefined"
        return out


def profit_factor_summary(values: Iterable[float]) -> ProfitFactorSummary:
    """Summarise profit factors without letting the sentinel become a score."""
    defined: list[float] = []
    undefined = 0
    for v in values:
        if v is None:
            continue
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        # `is None` discipline applied to a numeric sentinel: an unrepresentable
        # value is not a large one.
        if f >= PF_UNDEFINED_FLOOR:
            undefined += 1
        else:
            defined.append(f)

    if not defined:
        return ProfitFactorSummary(None, None, 0, undefined, 0)
    return ProfitFactorSummary(
        median=statistics.median(defined),
        mean=statistics.fmean(defined),
        n_defined=len(defined),
        n_undefined=undefined,
        # Strictly greater than 1.0: exactly 1.0 is break-even, not a win.
        n_profitable=sum(1 for f in defined if f > 1.0),
    )


def share_profitable(returns: Iterable[float]) -> tuple[int, int]:
    """(profitable, total) over run returns.

    The number the deep backtest never printed and the one that settles the
    argument fastest. "Avg return -0.46%" invites "so it is roughly flat";
    "113 of 485 runs profitable" does not.

    `> 0`, not `>= 0`: a run that returned exactly zero did not make money.
    """
    total = 0
    good = 0
    for r in returns:
        if r is None:
            continue
        try:
            f = float(r)
        except (TypeError, ValueError):
            continue
        total += 1
        if f > 0.0:
            good += 1
    return good, total
