# Learned voter weights against the hand-tuned ones: results (2026-10-29)

Pre-registration: `PREREGISTRATION.md` (sha256 in `PREREGISTRATION.sha256`,
sealed 2026-10-29T10:55:51Z and pushed as commit c68ab35e before any run).
Driver: `scripts/voter_weights_test.py`. Full output: `report.txt`. No addenda.

## Verdict: FAIL. Leave `VOTER_WEIGHT_LEARNING_ENABLED` off.

The learned weights made the bot do worse on the holdout, not better.

| holdout dataset | trades default / learned | learned - default, mean per fold |
|---|---|---|
| majors_1h_v3 | 167 / 151 | -0.99 pp |
| alts_1h_v3 | 170 / 173 | +0.05 pp |
| both | | **-0.47 pp, 95% interval [-0.94, -0.05]** |

All three rules fail: neither dataset reaches +1.0 pp, the holdout mean is below
+1.0 pp and the learned arm's drawdown is worse, and the interval lies below zero
rather than above it.

## The fit

1,130 discovery positions (243 that closed after the discovery window left out;
none lacked votes or a return). The learner's production defaults, unchanged.
The multipliers moved little: most within 0.95 to 1.05; the largest were
`reversal` 0.86, `bb_pct_b` 0.87, `fibonacci` 0.90, `mtf_bos` 1.09, `ew_wxy`
1.08 and `poc_magnet` 1.08. Weights file sha256 `8d5e4f51...` (in
`voter_weights.json.sha256`), fixed before the holdout runs.

## Reported, deciding nothing

- The learner's own out-of-sample check on discovery fails its bar: 10 of 31
  judged voters held direction (32% against 60%), and the AUC with learned
  weights was 0.428 against 0.429 without (-0.001, lower bound -0.005).
- The `/readiness` AUC check on the holdout default-arm positions: 0.527 with
  learned weights against 0.526 without (+0.001, lower bound -0.003). The +0.02
  the production report shows did not appear here.
- Plain confluence agreement ranks winners at about chance on the holdout (AUC
  0.53) and below chance on discovery (0.43). Re-weighting a score that does not
  rank outcomes cannot make it rank them, which matches the earlier finding that
  no voter has a directional edge (`voter_edge_2026-10`).

## Limits

This tests the learning procedure on backtest positions, not the exact weights
the live bot fitted on its own records, which are not in the repository. Those
were fitted on 58 unseen trades with a lower bound of +0.01 AUC; the same
procedure, on 1,130 positions here, found nothing. If the bot's
`data/learning/voter_weights.json` is exported, it can be run through the same
holdout as a third arm, labelled post hoc.
