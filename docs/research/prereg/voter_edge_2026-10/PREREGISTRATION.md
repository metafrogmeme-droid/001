# Every signal and pattern voter: pre-registration (before any analysis)

Date: 27 October 2026. Collector: `scripts/signal_edge.py collect` (existing).
Analysis: `scripts/voter_edge_study.py`. Offline only.

## Question

For every voter in the confluence electorate, including every pattern voter
(candlestick, chart patterns, reversal, harmonic, Wyckoff, the Elliott voters,
liquidity sweep, divergence, fair value gaps), is the forward move in the
direction it voted larger than the unconditional drift, on data it was not
found on?

## Data and windows

The collector runs the honest 6-fold walk-forward and records, for every
analyzer call, each voter's non-zero vote and the ATR-scaled forward move at 1,
4, 12, 24 and 48 bars.

* Discovery: `corr_dense_1h` and `majors_1h_v2`, calls before 2026-05-22T02:00Z.
* Holdout: `majors_1h_v3` and `alts_1h_v3`, calls from 2026-07-06T12:00Z.

## Statistic

For a vote above zero the move is the call's long move; below zero it is the
negated long move. The excess is that move minus the same direction's
unconditional drift measured over the same file. The per-voter, per-horizon
statistic is the mean excess in ATR units. Intervals are a 95% percentile
bootstrap over calendar weeks: every call of every symbol and file in a week is
one cluster, because one market move reaches every symbol and every voter at
once. 4000 draws, seed 7.

## Multiple testing and pass rule

The family is every (voter, horizon) pair with at least 200 discovery calls and
8 discovery weeks. Discovery p-values (two-sided, from the bootstrap) are
corrected by Benjamini-Hochberg at 10%. A pair passes only if all hold:

1. it survives the correction on discovery, with the discovery interval
   excluding zero;
2. the holdout mean has the discovery sign and the holdout interval excludes
   zero;
3. the holdout has at least 100 calls and 4 weeks.

A voter whose significant discovery effect is NEGATIVE (it points the wrong way)
and replicates on the holdout is reported as harmful, not as a pass.

## Limits stated in advance

Overlapping forward windows: consecutive bars share most of a 24 or 48 bar
window, so weeks, not calls, are the independent unit and the effective sample
is small. The close of the signal bar is not a fill, and the excess is gross of
cost. A voter that casts a vote in many bars is measured on those bars only.
The holdout is about three months of one regime.
