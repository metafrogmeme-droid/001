# The benchmark with the history the live bot has: pre-registration (2026-10-31)

Written and sealed before any run below.

## Why

`docs/research/prereg/timeframe_variants_2026-10/REPORT.md` found that every walk-forward fold
was handed only 100 bars of history, so the analyzer's daily context (which live always has, 200
daily bars) formed only in the last week of a 1h fold. The daily-weighted trend gate
`MTF_ALIGNMENT_GATE_ENABLED` is on in live. On ten fresh coins, giving the folds live's history made
the bot worse (mean fold -4.10% against -2.26%). This measures the same thing on the five 1h sets
every earlier study used, and asks whether the gate earns its place once it can see.

## Data (already frozen; no fetch)

`benchmark/majors_1h` (ccc3f70b), `majors_1h_v2` (c8ac57ce), `majors_1h_v3` (d74edd82),
`alts_1h_v3` (2ddf52d1), `corr_dense_1h` (c4b0874f).

## Arms (per dataset)

`python -m bot.backtest.runner --dataset <ds> --timeframe 1h --honest --walk-forward 6
--breaker-reset-bars 24 --trades-out <file>`, plus:

| arm | extra | meaning |
|---|---|---|
| A | none | as every figure on record was measured |
| B | `--fold-history-bars 4824` | the bot with live's history |
| C | B, and `MTF_ALIGNMENT_GATE_ENABLED=0` in the environment | the same, without the daily-weighted gate |

## What is decided

**Q1, does live's history change the record?** Reported per dataset: mean fold return, profit
factor, positions, size-weighted mean per position, A against B. No pass or fail: it is a
correction of what the record measured.

**Q2, does the gate earn its place when it can see?** The paired difference in fold return C - B
over all 30 folds (5 sets x 6 folds). The gate **costs money** when the mean difference is above
0, its one-sided 97.5% lower bound from a bootstrap over the 30 folds (10,000 draws, seed 7) is
above 0, and C beats B on at least 4 of the 5 sets. The gate **earns its place** when the same
holds for B - C. Anything else: no verdict. Either verdict leads to a sealed paper forward test,
never to a direct change of the live setting.

## Limits

These are the sets the defaults were tuned on and that this month's studies reused, so any
difference here is weaker evidence than it would be on fresh data. Folds of one set are not
independent of each other.
