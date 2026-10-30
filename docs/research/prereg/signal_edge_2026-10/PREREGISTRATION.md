# Do the bot's signals carry any edge before costs? Pre-registration (2026-10-30)

Written and sealed before any run below.

## Question

The bot's calls lose net of costs on every frozen set tested this month (profit factor
0.47-0.82, `docs/research/prereg/timeframes_2026-10/REPORT.md`). That leaves two different
situations, which call for opposite fixes:
- the calls point the right way but costs eat the edge (fix: trade less, cheaper);
- the calls point the wrong way before costs (then fading them is worth a sealed test);
- or neither: the calls carry no information, and no setting will make them pay.

This study measures which, by running the bot unchanged with costs set to zero.

## Data (already frozen; no fetch)

- Primary: `benchmark/majors_1h` (10 majors, 1h, dataset hash ccc3f70b…).
- Second universe: `benchmark/alts_1h_v3` (8 alts, 1h, dataset hash 2ddf52d1…).

## Runs

For each dataset: `python -m bot.backtest.runner --dataset <ds> --timeframe 1h --honest
--walk-forward 6 --breaker-reset-bars 24 --commission 0 --slippage 0 --trades-out <file>`.
The same command with the default `--honest` costs is run as the costed reference. No
other setting changes.

## Measure and decision rule

Per OOS position: `net_pnl_pct` from `--trades-out` (zero cost, so it is the gross return);
a row whose value is None is left out and counted. Discovery: folds 0-2. Holdout: folds 3-5.

Per dataset, the mean gross return per position in each half gives one of:
- **POSITIVE**: both halves > 0. The calls point the right way; costs are the problem.
- **NEGATIVE**: both halves < 0. The calls point the wrong way before costs.
- **NONE**: anything else. No consistent direction.

Overall verdict: the primary and second universe must agree (both POSITIVE or both
NEGATIVE) for any follow-up. Then:
- both NEGATIVE: a separately sealed stage 2 tests the mirrored ("faded") call net of costs;
- both POSITIVE: a separately sealed stage 2 tests fewer, cheaper trades (the existing
  `--confidence-threshold` and maker-entry controls), net of costs;
- otherwise: the calls carry no measurable edge either way, and the published signals should
  stop being presented as trade ideas that pay.

No bootstrap: this is a screen that routes to stage 2, not proof; stage 2 carries the bar.

## Reported, deciding nothing

Gross profit factor and win rate per half; the same split by `setup` and by `direction`
(many groups, small counts: descriptive only); the costed reference's net figures.

## Limits

Two universes, one split each. Zero-cost runs may take slightly different trades from the
costed run (equity, and so sizing, differs). The bot's defaults were tuned on these data.
