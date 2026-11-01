# A closer target on the same entries: pre-registration (2026-10-31)

Written and sealed before any run below.

## Why

The operator's 249 live strategy exits (2026-08-18 to 2026-10-14, read 2026-10-31) lost money
(profit factor 0.61). The median stop sat 0.77% from entry and the median target 3.04%, about four
stop distances; 10 of 249 exits reached their target. The exits that made money were the owner's
own early closes (23 of 23 positive) and the closes with no recorded reason. That suggests, and
does not show, that the target is too far for what the bot's entries do. This asks whether a target
one or one and a half stop distances from entry, on the same entries, does better.

## Data (already frozen; no fetch)

`benchmark/majors_1h` (ccc3f70b), `majors_1h_v2` (c8ac57ce), `majors_1h_v3` (d74edd82),
`alts_1h_v3` (2ddf52d1), `corr_dense_1h` (c4b0874f).

## Arms (per dataset)

`python -m bot.backtest.runner --dataset <ds> --timeframe 1h --honest --walk-forward 6
--breaker-reset-bars 24 --fold-history-bars 4824 --trades-out <file>` (arm B of
`live_history_benchmark_2026-10`, the bot with live's history, re-run at this commit), plus:

| arm | extra | meaning |
|---|---|---|
| B | none | the control |
| T10 | `--target-r-after-gate 1.0` | target at 1 stop distance |
| T15 | `--target-r-after-gate 1.5` | target at 1.5 stop distances |

`--target-r-after-gate` replaces the target **after** the risk gate has approved and sized the
idea on the analyzer's own levels, and leaves the stop where it was, so each arm is offered the
same entries. The positions taken still differ somewhat: an earlier exit frees a slot, and the
loss streak, cooldown and breaker see a different history. Positions per arm are reported.

## What is decided

Fold returns are paired by dataset and fold: 30 folds (5 sets x 6). Bootstrap over the 30 folds,
10,000 draws, seed 7. Two arms are tested, so each one-sided bound is at **98.75%** (Bonferroni
over 97.5%).

**Q1, does the closer target help?** For each of T10 and T15, the paired difference arm - B. It
**helps** when the mean difference is above 0, its lower bound is above 0, and the arm beats B on
at least 4 of the 5 sets. It **hurts** when the same holds for B - arm. Anything else: no verdict.

**Q2, does it make money?** For each of T10 and T15, the fold returns themselves. It **makes
money** when the mean is above 0, its lower bound is above 0, and the mean is positive on at least
4 of the 5 sets. Anything else: it does not show it makes money.

`python3 scripts/closer_target_benchmark.py report --runs <dir>` computes both from the run files.

## What follows

A "helps" verdict leads to a sealed paper forward test of that target, never to a direct change
of a live setting: the live risk gate requires a reward of at least 1.5 stop distances for a swing
idea (`SWING_MIN_RR`), so a live 1.0 target would also need that rule changed, which is its own
decision. No verdict and "hurts" leave the target as it is.

## Limits

These are the sets the defaults were tuned on and that this month's studies reused, so a
difference here is weaker evidence than one on fresh data. Folds of one set are not independent.
The live figures that motivated this (0.77%, 3.04%, 10 of 249) were read before this was written;
they chose the question, not the rule.
