# Learned voter weights against the hand-tuned ones: pre-registration (2026-10-29)

Written and sealed before any run below. Nothing in it changes after the
holdout is read; a correction goes in a dated addendum that says what changed
and that it came after.

## Question

`/readiness` reports the voter-weight learner as validated and recommends
`VOTER_WEIGHT_LEARNING_ENABLED=true`. Its evidence is 58 unseen live/paper
trades, AUC 0.55 against 0.53 (+0.02, 95% lower bound +0.01), with the
direction-hold point estimate 72% over a 60% bar whose 95% lower bound is 52%.
The flag changes how the analyzer scores every idea. Does the learning
PROCEDURE, unchanged, produce weights that make the bot more money on data it
never saw, measured the way every other change this month was measured?

The production weights were fitted on the bot's own records, which are not in
the repository. This tests the procedure, not that exact file. If the file is
exported later, the same holdout runs can take it as a third arm; that run
would be labelled post hoc.

## Data and windows

As in `risk_settings_2026-10` and `maker_orders_2026-10`:
- Discovery: `corr_dense_1h`, `majors_1h_v2`, walk-forward folds ending on or
  before 2026-05-22T02:00:00Z.
- Holdout: `majors_1h_v3`, `alts_1h_v3`, walk-forward folds starting on or after
  2026-07-06T12:00:00Z.
- Every run: `python -m bot.backtest.runner --dataset benchmark/<ds> --honest
  --breaker-reset-bars 24 --walk-forward 6`.

## Procedure

1. Fit. Run the default bot (`VOTER_WEIGHT_LEARNING_ENABLED` off) on the two
   discovery datasets and keep each position's entry votes. Fit
   `VoterWeightLearner()` with its production defaults (min samples 20, min
   voter samples 8, gain 1.0, shrinkage 10, bounds [0.5, 1.5]) on discovery-window
   positions only, in exit-time order. A position is a win when its net return
   is above zero; a position with no readable return or no votes is left out
   and counted. The fitted multipliers are written to a file and hashed before
   any holdout run.
2. Test. On each holdout dataset run two arms that differ only in the flag:
   `default` (off) and `learned` (on, reading the fitted file). Each arm gets
   its own empty state directory (`RUNECLAW_STATE_DIR`), the learned arm's
   holding only `learning/voter_weights.json`.

## Decision rule (fixed now)

`risk_sweep.judge_stage1` applied to the two holdout datasets only: per fold,
learned minus default return.
- Each holdout dataset's mean difference is at least +1.0 percentage point;
- the holdout mean difference is at least +1.0 pp and the learned arm's mean
  fold drawdown is no worse than the default's;
- the 95% bootstrap interval over calendar folds (5000 resamples, seed 7) lies
  above zero.

All three, or the result is FAIL and the recommendation is to leave the flag
off. A PASS is evidence for a paper trial with the flag on, not for live.

## Reported, deciding nothing

- The learner's own `trade_ranking` on the holdout default-arm positions,
  scored with and without the fitted multipliers (the AUC `/readiness` quotes),
  to compare with the production report.
- `validate_oos` on the discovery positions.
- Discovery-window results for both arms, which are in-sample for the fit.
- Trade counts per arm.
