# Risk settings sweep: results (27 October 2026)

`scripts/risk_sweep.py` stage 1 on all twelve arms (ten registered and the two
fee-gate arms of Addendum 1), `--honest --breaker-reset-bars 24 --walk-forward 6`,
exactly as in `PREREGISTRATION.md`. Discovery: `corr_dense_1h` and
`majors_1h_v2` folds ending on or before 2026-05-22. Holdout: `majors_1h_v3` and
`alts_1h_v3`. Mean paired out-of-sample return difference to the baseline, in
percentage points per fold; the right-hand columns are the holdout mean, whether
holdout drawdown was no worse, and the mean over all folds with a 95% bootstrap
interval over calendar fold positions (5000 draws, seed 7).

| arm | corr_dense | majors_v2 | majors_v3 | alts_v3 | holdout | dd ok | all folds, 95% interval | verdict |
|---|---|---|---|---|---|---|---|---|
| kelly_off | -2.65 | -0.82 | -0.06 | -0.68 | -0.37 | n | -0.99 [-3.01, +1.21] | fail |
| kelly_floor | -2.21 | -0.78 | -0.14 | -0.75 | -0.44 | n | -0.92 [-2.67, +1.09] | fail |
| risk_half | +0.84 | +0.26 | -0.62 | -0.06 | -0.34 | y | +0.07 [-0.69, +0.65] | fail |
| cap_half | +0.78 | +0.98 | -0.07 | +0.45 | +0.19 | y | +0.50 [-0.47, +1.29] | fail |
| stop_floor | -0.54 | +1.00 | +0.52 | +0.72 | +0.62 | y | +0.44 [-0.72, +1.51] | fail |
| sl_wide | +0.40 | -0.87 | -0.03 | +0.00 | -0.02 | n | -0.11 [-0.47, +0.13] | fail |
| conf_70 | +3.24 | +1.40 | -1.69 | +2.57 | +0.44 | y | +1.29 [-0.43, +3.13] | fail |
| open_3 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | y | 0.00 [0.00, 0.00] | fail |
| streak_3 | -0.26 | +0.73 | +0.08 | +0.31 | +0.19 | y | +0.21 [-0.70, +1.14] | fail |
| regime_off | +0.03 | -0.18 | +0.37 | +0.07 | +0.22 | n | +0.09 [-0.36, +0.67] | fail |
| fee_gate_3 | -0.37 | 0.00 | 0.00 | 0.00 | 0.00 | y | -0.08 [-0.25, 0.00] | fail |
| fee_gate_6 | -0.01 | +0.37 | -0.52 | -0.73 | -0.62 | n | -0.26 [-0.77, +0.13] | fail |

## Verdict: no arm passes stage 1

Every interval over all folds spans zero, and no arm gains at least one
percentage point in each of the four datasets. Stage 2 (replication on other
symbols) is for arms that pass, so it was not run, and nothing is recommended
for a default.

## What the table says

* **Turning Kelly sizing off made results worse** (-2.65, -0.82, -0.06, -0.68;
  the interval spans zero). The idea that record-based sizing is what pushes
  large positions into bad trades is not supported: with a flat risk budget the
  folds did worse, not better. The size-outcome correlation stays unexplained.
* **The cost mechanism is not supported by the fee gate.** The gate at 3 times
  the round-trip cost almost never binds (zero effect on three of four
  datasets), and at 6 times it hurt the holdout (-0.62).
* **`open_3` is exactly zero everywhere:** with the default of five open
  positions, capping at three never changed a trade, so the cap does not bind in
  these backtests.
* **`conf_70` has the largest mean (+1.29) and is the least reliable:** it loses
  1.69 points on `majors_1h_v3` and gains 3.24 on `corr_dense_1h`.
* **`stop_floor` (+0.44 overall, +0.62 on the holdout) and `cap_half` (+0.50)**
  are positive in most datasets with no worse drawdown on the holdout, and they
  are small. They are the settings to look at again with more data.

## What was not tested

Interactions (Kelly with the caps and the risk budget), more than one value per
setting, the 3x and 10x leverage runs, the settings the backtest does not read
(the macro gate, several live-only gates), and any time after 2026-09-24.
