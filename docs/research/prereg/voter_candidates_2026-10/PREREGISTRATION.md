# Candidate voters: pre-registration (written before any result)

Date: 27 October 2026. Harness: `scripts/signal_research.py`. Nothing here has
been run against the holdout when this is written.

## Question

Does any candidate feature, computed from past 1h OHLCV only, predict the
forward ATR-scaled return well enough to be worth a voter, and does that
survive on data it was not found on?

## Candidates (fixed here, not added after seeing results)

Time-series momentum over 6, 24, 72 and 168 bars; the same momentum scaled by
realised volatility; short reversal over 1 and 4 bars; close minus its 48-bar
mean in units of its 48-bar standard deviation; realised-volatility percentile
(24-bar volatility ranked within the last 500 bars); volatility of volatility;
range expansion (bar range over ATR14); volume-weighted momentum (24 bars);
Amihud illiquidity; rolling skew and kurtosis (72 bars). Two comparators the
analyzer already has: contrarian RSI(14) and the MACD histogram. Cross-sectional
rank momentum is left to a later round: it needs every symbol aligned per bar
and is not a per-symbol voter.

## Targets and sampling

Forward log return over 4, 12 and 24 bars, divided by ATR14 as a fraction of
price, from the close of the signal bar. Samples are taken every h bars per
symbol so forward windows do not overlap.

## Windows

* Discovery: `benchmark/corr_dense_1h`, bars before 2026-05-22T02:00Z.
* Holdout: `benchmark/majors_1h_v3` and `benchmark/alts_1h_v3`, signal bars from
  2026-07-06T12:00Z onward. Earlier bars of those sets are feature lookback
  only. Every symbol's discovery window ends h bars before the cut, so no
  forward window crosses it.

## Statistics

Within each symbol the feature and the target are rank-transformed over the
window, so the pooled statistic is a Spearman correlation net of each symbol's
own drift. Intervals are a 95% percentile cluster bootstrap over
(symbol, ISO week), 2000 draws, seed 7. The tested family is every
(feature, horizon) pair, and discovery p-values are corrected by
Benjamini-Hochberg at 10%.

## Pass rule (all must hold)

1. The discovery interval excludes zero and the pair survives the
   Benjamini-Hochberg correction.
2. The holdout interval excludes zero with the same sign.
3. The holdout top-minus-bottom quintile spread, in ATR units, is positive in
   the discovery sign and exceeds a round-trip cost of 0.10 ATR.
4. The feature's average within-symbol rank correlation with each existing
   comparator (RSI(14), MACD histogram), over the discovery bars, is at most
   0.90 in absolute value. A candidate above that is a copy of a voter the
   analyzer already has.

A candidate that passes becomes a voter behind a flag that is off by default.
A candidate that fails is recorded as a failure. The family is not widened to
rescue one.

## Limits stated in advance

All symbols are one crypto market, so symbol clusters are not independent and
the week clusters are the honest unit. IC is gross of fills: the signal bar's
close is not a price anyone trades at. A candidate that survives this still has
to pass the backtest A/B with per-trade comparison before any default changes.
