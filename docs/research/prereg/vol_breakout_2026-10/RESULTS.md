# Volatility breakout: results (27 October 2026)

`scripts/vol_breakout_backtest.py` at the commit that added it, rules and
parameters exactly as in `PREREGISTRATION.md`. Fees 0.06% and slippage 0.05%
per side, 1% risk, 2 x ATR stop, 4 x ATR target, 48-bar time stop, 1x notional
cap, 2000 bootstrap draws over ISO weeks, seed 7, 200 random-entry draws.

## Verdict: not passed, in the registered run and in the amendment

| run | window | trades | net expectancy (R) | 95% interval | random-entry mean / p95 |
|---|---|---|---|---|---|
| registered | discovery, SOL only | 338 | -0.050 | [-0.192, +0.100] | -0.106 / +0.011 |
| registered | holdout, BTC ETH SOL | 180 | -0.132 | [-0.435, +0.184] | -0.176 / -0.040 |
| amendment A | discovery, BTC ETH SOL | 1046 | -0.026 | [-0.148, +0.089] | -0.130 / -0.060 |
| amendment A | holdout | 180 | -0.132 | [-0.465, +0.183] | -0.162 / -0.003 |

## An error in the registration

The registration named `benchmark/corr_dense_1h` for discovery. That snapshot
holds 21 alts and SOL, not BTC or ETH, so the registered discovery run tested
SOL alone. It is reported above as it came out. **Amendment A** (post hoc,
written after seeing that run, for a reason that has nothing to do with the
outcome) uses `benchmark/majors_1h_v2`, which holds all three symbols over the
same period from 2025-02-21, with the same cut at 2026-05-22. Everything else is
unchanged. The holdout rows are the same in both because the holdout was never
in error.

## What the numbers say

* The rule's entries beat the random-entry control in amendment A's discovery
  (-0.026R against a control mean of -0.130R and a p95 of -0.060R), so the entry
  signal adds something over random timing with the same exits and costs.
* It does not clear zero. The pass rule needs the interval above zero, and the
  interval spans it.
* Diagnostic, not registered: the same rules at zero fee and zero slippage give
  +0.108R [-0.010, +0.225] in discovery and +0.031R [-0.286, +0.322] in the
  holdout. Costs take about 0.13R to 0.16R per trade. A gross edge of about
  0.1R, if real, is smaller than the cost of getting in and out at 1h.
* Per symbol in discovery: BTC -0.059R, ETH +0.032R, SOL -0.050R. Maximum
  drawdowns at 1% risk were 29%, 17% and 34% over the discovery window, which is
  what a negative-expectancy run of 340 to 360 trades at that size looks like.
* Realised risk averaged 0.83% to 0.99% per trade: the 1x notional cap bound on
  some trades and cut their risk below the 1% target.

## What was not tested

A 3x notional cap (which would restore 1% risk on those trades), a different
window than 20 bars or threshold than 1.5 (none was tuned, deliberately),
maker orders or lower costs, funding, other timeframes, and any data after
2026-09-24.
