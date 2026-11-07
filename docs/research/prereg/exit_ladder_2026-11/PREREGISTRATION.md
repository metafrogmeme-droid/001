# An earlier exit ladder, and live's own limit entries: pre-registration (2026-11-07)

Written and sealed before any run below on the study's data.

## Why

The live record (249 strategy exits, 2026-08-18 to 2026-10-14) lost money (profit factor 0.61); the
exits that made money were the owner's early manual closes (23 of 23 positive).
`closer_target_2026-10/REPORT.md` found that every exit in the honest benchmark is run by the
partial-TP ladder, which takes profit at fixed multiples of the stop distance (TP1 at 1.5, 50%
of the position, stop to breakeven; TP2 at 2.5, 30%, stop locks 1; a trailing runner of 20%) and
never reads the idea's target. So the question "take profit sooner" is a question about the
ladder's multiples, which live reads from the same settings.

That study's arms also showed that, under `--honest`, an entry is filled at the next bar's open
even when the analyzer asked for a pullback limit below it; skipping fills that had already run
toward the target improved every set. Live places that limit and lets it rest
(`--as-live`). This asks whether the benchmark's market-at-next-open fill is what makes it lose,
by measuring the bot with live's own limit entries.

Mechanisms checked before sealing, on `benchmark/majors_4h` (not a study set): with the E1
settings TP1 exits rose from 19 to 57; with `--as-live` 127 limit entries rested and filled
against 0. No result of those runs is used here.

## Data (already frozen; no fetch)

Primary: `benchmark/majors_1h` (ccc3f70b), `majors_1h_v2` (c8ac57ce), `majors_1h_v3` (d74edd82),
`alts_1h_v3` (2ddf52d1), `corr_dense_1h` (c4b0874f).
Confirmation: `benchmark/fresh10_1h` (449f02e3), ten coins used once before, by
`timeframe_variants_2026-10`, for different questions.

## Arms (per dataset)

`python -m bot.backtest.runner --dataset benchmark/<ds> --timeframe 1h --honest --walk-forward 6
--breaker-reset-bars 24 --fold-history-bars 4824 --trades-out <file> --output <file>`, plus:

| arm | extra | meaning |
|---|---|---|
| B | none | the control: the bot as it runs |
| E1 | `PARTIAL_TP1_R=1.0 PARTIAL_TP2_R=1.75` | the same ladder, each step earlier |
| E2 | `PARTIAL_TP1_R=1.0 PARTIAL_TP1_CLOSE_PCT=70 PARTIAL_TP2_R=1.5 PARTIAL_TP2_CLOSE_PCT=20` | bank most at 1 stop distance; a 10% runner |
| LV | `--as-live` | entries as live places them: a limit rests and fills only on a touch |

Settings are environment variables for that run only; nothing in the repository changes.

## What is decided

Fold returns are paired by dataset and fold: 30 primary folds. Bootstrap over them, 10,000 draws,
seed 7. Three comparisons, so each one-sided bound is at 99.17% (Bonferroni over 97.5%).

**Manipulation checks, first.** E1 and E2 must show a larger share of positions reaching TP1
than B on every set (primary and confirmation); LV must show filled resting limits on every set.
An arm that fails its check gets **no verdict**, whatever its returns.

**Q1, does the arm help?** The paired difference arm - B. It **helps** when the mean difference is
above 0, its lower bound is above 0, the arm beats B on at least 4 of the 5 primary sets, and its
mean fold return on the confirmation set is above B's. It **hurts** in the mirror case. If the
primary rule holds and the confirmation set disagrees: no verdict. Anything else: no verdict.

**Q2, does it make money?** The arm's own fold returns: the primary mean above 0, its lower bound
above 0, positive on at least 4 of 5 primary sets, and a positive mean on the confirmation set.

`python3 scripts/exit_ladder_benchmark.py report --runs <dir>` computes all of it from the files.

## What follows

- **E1 or E2 "helps":** a sealed paper forward test of those settings, never a direct live
  change. Live is off; paper reads the same settings.
- **E1 or E2 "makes money":** the same, and it is said in those words, on simulated data.
- **LV "helps":** the benchmark's fill model overstates what live's entries lose. The record
  would be re-measured with `--as-live`, and the change of method stated; it is not a strategy
  change, since live already enters this way.
- **No verdict or "hurts":** nothing changes.

## Limits

The primary sets are the ones the defaults were tuned on and this autumn's studies reused; the
confirmation set has been used once. Folds of one set are not independent. A backtest fill on
a touch of a 1h bar is optimistic about queue position.
