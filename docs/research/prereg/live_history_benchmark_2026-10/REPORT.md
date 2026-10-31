# The benchmark with the history the live bot has: report (2026-10-31)

Rule: `PREREGISTRATION.md` (sealed 2026-10-31 11:23:26Z). All fifteen runs at code sha 3b3ab734 on
the five frozen 1h sets. Outputs in `runs/` (trade rows gzipped; `scripts/live_history_benchmark.py
report --runs runs` re-derives everything below).

## Q1: with live's history the bot loses less on four of five sets, and still loses on all five

Mean fold return (profit factor, positions):

| set | A as recorded | B with live's history | C = B without the daily gate |
|---|---|---|---|
| majors_1h (the benchmark on record) | -3.62% (0.74, 414) | **-1.71%** (0.79, 260) | -6.42% (0.53, 416) |
| majors_1h_v2 | -7.29% (0.60, 548) | **-4.77%** (0.69, 475) | -6.79% (0.66, 650) |
| majors_1h_v3 | -0.55% (0.90, 182) | -2.05% (0.60, 176) | -1.50% (0.79, 232) |
| alts_1h_v3 | -2.56% (0.63, 196) | -2.23% (0.53, 115) | -0.75% (0.91, 251) |
| corr_dense_1h | -8.58% (0.66, 825) | **-6.56%** (0.70, 701) | -9.21% (0.56, 602) |

Size-weighted mean net per position, A / B / C: majors_1h -0.160 / -0.109 / -0.307; majors_1h_v2
-0.270 / -0.196 / -0.220; majors_1h_v3 -0.057 / -0.224 / -0.121; alts_1h_v3 -0.340 / -0.422 /
-0.071; corr_dense_1h -0.260 / -0.215 / -0.354.

- Every figure on record was measured with about four days of history per fold. Given the 201 days
  live reads, the bot loses less on four of the five sets (majors_1h by 1.9 points a fold), takes
  fewer positions, and loses more on majors_1h_v3.
- The direction differs from the ten fresh coins of `timeframe_variants_2026-10`, where live's
  history made the bot worse (-4.10% against -2.26%). Whether the record flattered or understated
  the live bot depends on the set; on none of these sets does the bot make money either way.

## Q2: the daily-weighted gate, no verdict

Paired fold differences C - B over the 30 folds: mean -1.47 points (removing the gate loses more),
one-sided 97.5% bounds -2.96 for C - B and -0.16 for B - C. B is better on 3 sets, C on 2. Neither
"costs money" nor "earns its place" holds, so `MTF_ALIGNMENT_GATE_ENABLED` stays as it is (on) and
no forward test follows from this. The point estimate leans towards keeping it.

## What changes

- Walk-forward figures compared with live from now on should be run with `--fold-history-bars`.
- The published benchmark numbers (README, `docs/FROZEN_BENCHMARK.md`) were measured the old way.
  They are not rewritten here: on majors_1h the live-history figure is less bad, which a correction
  would have to say in the same place, with this report as its source.

## Limits

These five sets are the ones the defaults were tuned on and this month's studies reused. Folds of
one set are not independent. C differs from B by one environment flag read at config load.
