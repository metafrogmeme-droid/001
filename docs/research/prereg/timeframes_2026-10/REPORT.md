# The bot, unchanged, on other bar sizes: report (2026-10-30)

Rule: `PREREGISTRATION.md` (sealed 2026-10-30 11:33:22Z, commit 2f6245e4) and
`ADDENDUM_1.md` (daily set resampled from 4h). Runs: `runs/*.json`, produced by
`python3 scripts/timeframes_study.py run` and judged by `... report`
(`runs/report.json`). Datasets frozen in commit fe572aa7.

## Verdict: no timeframe passes. Stage 2 is not run.

| bars | dataset hash | folds 1-6, return % | discovery mean | holdout mean | worst holdout | verdict |
|---|---|---|---|---|---|---|
| 15m | f7007f6a | -7.46, -9.76, -9.56, -5.06, -7.42, -5.84 | -8.93 | -6.11 | -7.42 | FAIL |
| 4h | e7c18511 | -2.87, -6.81, -6.94, -4.20, -9.67, -10.35 | -5.54 | -8.07 | -10.35 | FAIL |
| 1d | cae4abda | -1.62, -0.63, -1.93, -0.89, -1.98, +0.55 | -1.39 | -0.77 | -1.98 | FAIL |
| 1h (reference) | d12d70e0 | -0.94, -9.69, -5.52, +14.46, -6.61, -7.06 | -5.38 | +0.26 | -7.06 | decides nothing |

Each failing timeframe fails on the discovery mean and the holdout mean, so no
choice of fold boundary rescues it. 15m and 4h also break the -5% holdout floor.

## Reported, deciding nothing

| bars | trades | win rate | profit factor | worst fold max drawdown % |
|---|---|---|---|---|
| 15m | 463 | 45.6% | 0.47 | 10.1 |
| 4h | 428 | 45.3% | 0.55 | 11.4 |
| 1d | 93 | 36.6% | 0.68 | 5.8 |
| 1h | 383 | 52.5% | 0.82 | 10.8 |

Equal-weight buy-and-hold over the same out-of-sample windows, % per fold:

| bars | folds 1-6 |
|---|---|
| 15m | +6.4, +2.0, +21.9, +8.5, +2.6, -11.0 |
| 4h | -0.3, +36.7, -32.7, -32.7, -13.7, +19.4 |
| 1d | -1.0, +41.7, -33.9, -33.6, -12.5, +16.4 |
| 1h | -19.1, +8.9, -1.7, +25.1, +20.2, -18.9 |

The bot lost in rising and falling windows alike, so the losses are not explained by
the market's direction. Fewer trades (1d) means smaller losses, not a positive edge.

## Notes

- The 1h reference passing its holdout mean (+0.26) rests on one fold (+14.46); its
  other two holdout folds lose more than 6%. It is not a pass under this rule and was
  never eligible to be one.
- The 15m run records code sha 0a207c30 and the others fe572aa7. The only commit
  between them adds `scripts/timeframes_study.py` and its test; no bot code differs.
- Limits from the pre-registration stand: one universe, one split, defaults tuned on
  1h bars. A loss here may be a tuning mismatch; this test does not license re-tuning
  per timeframe and then re-testing on the same data.
- The Kraken ETH/SOL history the user supplied was set aside for stage 2. With no
  timeframe passing, it is not used here.
