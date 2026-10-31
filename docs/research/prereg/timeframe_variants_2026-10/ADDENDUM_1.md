# Addendum 1 (2026-10-31): H3 with the history the live bot has

Written and sealed after the report and before any run below. H3 took no position in the sealed
run (no return of any H3 position was ever computed), because each walk-forward fold carried only
100 bars of history (REPORT.md). This addendum measures H3 as it was meant, and changes nothing
about H1, H2a or H2b, whose verdicts stand.

## The change to the harness

A new backtest control, `--fold-history-bars N` (BacktestConfig `fold_history_bars`, default 0 =
unchanged): each walk-forward fold's engines are also given up to N bars that precede the fold's
own slice, as context only. They are never traded; the analyzer and the timeframe controls read
them through the same history the default path reads. N = 4,824 one-hour bars, the window the
default path already uses for 1d context (201 days).

## Runs (same frozen data, `benchmark/fresh10_1h`)

| arm | extra flags | role |
|---|---|---|
| R1h | `--fold-history-bars 4824` | reference: the bot with live's history; decides nothing |
| **H3h** | `--fold-history-bars 4824 --daily-trend-days 50` | tested |

## Decision rule

H3h is judged by the sealed rule unchanged, including its share of the Bonferroni bound (the
0.625th percentile), so the family stays four tests. R1h against R1 (the sealed run) is reported:
it shows what the missing daily context did to every earlier walk-forward reading.
