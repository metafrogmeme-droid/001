# Addendum 1 (post hoc, 2026-10-29): the bot's own production weights

Written after the pre-registered result in `RESULTS.md`. Not pre-registered.

The operator exported the live bot's `data/learning/voter_weights.json`
(`production_voter_weights.json`, sha256 in the `.sha256` file beside it): fitted
2026-10-03 on 219 of the bot's own trades, with the learner's default settings.
It was run as a third arm (`production`) on the same holdout, against the same
`default` runs, with `scripts/voter_weights_test.py extra-arm`, and judged by
the same three rules. Output: `production_report.txt`.

| holdout dataset | trades default / production | production - default, mean per fold |
|---|---|---|
| majors_1h_v3 | 167 / 162 | -0.65 pp |
| alts_1h_v3 | 170 / 170 | +0.10 pp |
| both | | **-0.28 pp, 95% interval [-0.95, +0.12]** |

FAIL on all three rules: neither dataset reaches +1.0 pp, drawdown is worse, and
the interval includes zero.

## Two caveats, which cut in opposite directions

- **Overlap.** The holdout runs from 2026-07-11 to 2026-09-24; the weights were
  fitted on trades up to 2026-10-03. Trades the bot took in that window may be
  among the 219 it learned from, which would flatter this arm. It failed anyway.
- **Voters the backtest cannot hear.** About ten of the file's 38 multipliers are
  for order-flow and on-chain voters (`of_*`, `smart_money_composite`,
  `whale_accumulation`, and others) that cast no vote in a backtest of candle data.
  Only the other multipliers were exercised.

## Reading

Both the procedure's weights (`RESULTS.md`, -0.47 pp) and the bot's own weights
(-0.28 pp) score below the hand-tuned defaults on the holdout. The recommendation
stands: leave `VOTER_WEIGHT_LEARNING_ENABLED` off.
