# Every signal and pattern voter: results (27 October 2026)

`scripts/voter_edge_study.py` run once on the collected files, exactly as in
`PREREGISTRATION.md`. Discovery `corr_dense_1h` and `majors_1h_v2` before
2026-05-22, holdout `majors_1h_v3` and `alts_1h_v3` from 2026-07-06,
4000 bootstrap draws over calendar weeks, seed 7, Benjamini-Hochberg at 10%.
Nothing in the family, the windows or the rule was changed after the first
look.

## Verdict: 0 pass, 0 replicate as harmful, 175 fail

* 175 (voter, horizon) pairs had enough discovery calls and weeks to be tested,
  across every voter the analyzer cast in these data, including the pattern
  voters: `candlestick`, `chart_patterns`, `reversal`, `harmonic`, `wyckoff`,
  `ew_impulse`, `ew_corrective`, `ew_diagonal`, `ew_wxy`, `liquidity_sweep`,
  `divergence`, `fib_extension`, `fibonacci`, `supply_demand`, `mtf_choch`,
  `mtf_bos`.
* No pair survives the correction on discovery. The smallest uncorrected
  p-value is 0.011.
* Only 6 of 175 discovery intervals exclude zero before any correction, which
  is fewer than the 8.8 that chance alone gives at 95%. They are `harmonic` at
  24 and 48 bars (-0.73 and -0.93 ATR, pointing the wrong way), `keltner` at 1
  bar (+0.03), `mtf_choch` at 4 (+0.10), `ew_corrective` at 12 (+0.08) and
  `donchian` at 48 (-0.33). None has a holdout interval that excludes zero.

## Reading it

* The forward move in the direction a voter votes is not distinguishable from
  the unconditional drift, for any voter at any of 1, 4, 12, 24 or 48 bars, once
  one market move is counted once per week.
* `harmonic` is the only voter that looks wrong-way in discovery, and its
  holdout has 79 calls. That is a reason to test it on more data, not a result.
* This is the same answer as the earlier candidate-feature study. Taken
  together, nothing measured here has predictive power that survives honest
  clustering, so the voter set is not where a better result will come from.
  What the data do show is structural: costs, position size and stop distance
  (see the sizing result in the engineering log and the risk sweep).

## What was not tested

Voters in combination (confluence as a whole), regime-conditional edge, any
horizon above 48 bars, net-of-cost returns, order-flow and funding voters
(absent from frozen candle data), and any time after 2026-09-24.
