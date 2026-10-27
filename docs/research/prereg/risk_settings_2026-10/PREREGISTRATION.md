# Risk settings sweep: pre-registration (written before any arm is run)

Date: 27 October 2026. Driver: `scripts/risk_sweep.py`. Offline only.

## Why

The risk limits were each set on their own, and the voter work found that
position size correlates negatively with trade outcome on two disjoint windows
(larger positions do worse). Kelly sizing scales a position with the trade
record, and caps risk at 0.25% of equity until 20 closed trades, so the sizes
move with luck, not with the setup. The question is whether any single setting
change improves out-of-sample results on frozen data, without overfitting a
large grid to six calendar folds.

## Arms: one change at a time, fixed here, ten of them

| id | change (env vars read at import) | mechanism tested |
|---|---|---|
| kelly_off | KELLY_SIZING_ENABLED=0 | stop sizing from the trade record |
| kelly_floor | KELLY_FLOOR_RISK_PCT=1.0 | less size swing around the floor |
| risk_half | SCALP/INTRADAY/SWING/POSITION_MAX_RISK_PCT = 0.5 / 0.75 / 1.0 / 1.0 | halve the per-trade risk budget |
| cap_half | SCALP/INTRADAY/SWING/POSITION_MAX_POSITION_PCT = 4 / 5 / 6.5 / 7.5 | halve the per-strategy position cap |
| stop_floor | MIN_STOP_DISTANCE_PCT=0.015 | no tight-stop, large positions |
| sl_wide | SCALP/INTRADAY/SWING/POSITION_SL_ATR_MULT = 1.95 / 2.6 / 3.25 / 3.9 | stops 1.3x wider |
| conf_70 | MIN_CONFIDENCE=0.70 | fewer, higher-confidence entries |
| open_3 | MAX_OPEN_POSITIONS=3 | less concurrent exposure |
| streak_3 | MAX_CONSECUTIVE_LOSSES=3 | earlier loss-streak halt |
| regime_off | REGIME_SIZING_ENABLED=0 | remove regime size scaling |

Baseline is the code defaults. Every run uses `--honest --breaker-reset-bars 24
--walk-forward 6` and the runner's honest costs and leverage, so arms differ
only in the changed variables. Settings the backtest does not read are not
swept: `BacktestConfig.max_position_pct` and `max_open_positions` are dead
fields; the macro gate is absent from the backtest.

## Stage 1 (four datasets, disjoint calendar halves)

* Discovery: `corr_dense_1h` and `majors_1h_v2`, the walk-forward folds whose
  out-of-sample window ends on or before 2026-05-22.
* Holdout: `majors_1h_v3` and `alts_1h_v3`, the folds whose out-of-sample window
  starts on or after 2026-07-06.

Measure per fold: out-of-sample return, maximum drawdown, trade count. The
unit is the fold, paired with the baseline's fold, so the statistic is the
arm's fold return minus the baseline's.

## Stage 1 pass rule (all must hold, per arm)

1. The mean paired return difference is at least +1.0 percentage point in each
   of the four datasets.
2. The mean difference over the holdout folds is at least +1.0 pp and the
   holdout mean maximum drawdown is no larger than the baseline's.
3. The mean difference over all folds has a 95% percentile bootstrap interval
   above zero, resampling calendar fold positions (so datasets covering the
   same dates move together).

Ten arms and four sign conditions would pass by luck about once in two
attempts on signs alone, which is why the rule asks for a size, a drawdown
condition and an interval.

## Stage 2 (only arms that pass stage 1)

Repeat on `majors_1h`, `alts_1h` and `alts_1h_v2` (different symbols, partly
overlapping dates, so this is a replication on other symbols, not fresh
time). An arm is recommended only if its mean paired difference is positive
there as well. Nothing is recommended for a live default without the A/B
repeated with the per-trade comparison and an explicit decision by the owner.

## Limits stated in advance

Six calendar folds per dataset is small. Fold returns are equity returns and
carry sizing and breaker effects. Several settings interact (Kelly with the risk budget and the caps), and one-at-a-time arms cannot see interactions.
Results describe one crypto regime sample.
