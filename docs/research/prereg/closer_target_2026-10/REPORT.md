# A closer target on the same entries: report (2026-10-31)

Pre-registration: `PREREGISTRATION.md` (sha256 a532477a, sealed 18:12:49Z, pushed before any run).
Runs: `runs/` (the run summaries, the trade rows gzipped, and `report.json` from
`python3 scripts/closer_target_benchmark.py report --runs runs`). Code at a3e22612.

## The sealed verdicts, as computed

Mean fold return (positions in brackets):

| set | B (control) | T10 | T15 |
|---|---|---|---|
| majors_1h | -1.71% (260) | -0.55% (128) | -0.23% (196) |
| majors_1h_v2 | -4.77% (475) | -0.14% (229) | -3.09% (371) |
| majors_1h_v3 | -2.05% (176) | -0.71% (128) | -1.82% (174) |
| alts_1h_v3 | -2.23% (115) | -0.11% (71) | -1.03% (93) |
| corr_dense_1h | -6.56% (701) | -3.15% (406) | -5.65% (527) |

The control reproduces arm B of `live_history_benchmark_2026-10` exactly on every set (same
mean fold return and pooled count).

- **Q1:** T10 - B is +2.53 points a fold, lower bound (98.75%) +1.30, better on 5 of 5 sets:
  "helps" by the rule. T15 - B is +1.10, lower bound +0.22, 5 of 5: "helps" by the rule.
- **Q2:** neither arm makes money. T10 averages -0.93% a fold (lower bound -2.05%) and is
  negative on all five sets; T15 averages -2.36%.

## The arms did not test the question that was sealed

**What the flag did.** `--target-r-after-gate` did not move any exit. Under `--honest` the
backtest manages every position with the partial-TP ladder (`BACKTEST_PARTIAL_TP`, which
`--honest` turns on). The ladder scales out at fixed multiples of the stop distance (TP1 at
1.5, TP2 at 2.5, then a trailing runner) and never reads the idea's own target.

**Its one effect.** The fill opens at the next bar's open, and the analyzer's entry is often a
pullback level below that price. When the open had already passed the new, closer target, the
position record failed validation (a target on the wrong side of the entry), and the fill was
rejected. The trade was never opened.

**Confirmed on alts_1h_v3,** re-run with its own logs:

| | control | T10 |
|---|---|---|
| fills rejected for that reason | 1 | 118 |
| positions opened | 109 | 60 |

Profit-target exits did not rise in any arm (majors_1h: TP1 exits 15, 14 and 16), which an exit
change would have shown.

**What the arms really measured** is an entry filter: skip a fill that has already moved one
(T10) or one and a half (T15) stop distances toward the target before it opens. They also
measured its knock-on effects. With fewer losing entries, the live-performance governor and the
loss-streak gate paused the bot less (alts_1h_v3: 200 governor rejections in the control, 0 in
T10).

**So the verdicts above are true arithmetic about the wrong intervention.** They say nothing
about a closer target, and the sealed question is unanswered.

## What follows

- **No change to any live setting.** The sealed rule sends a "helps" verdict to a paper forward
  test of a closer target. That test would be testing something these runs never measured, so
  it is not opened on this evidence.
- **To ask the original question,** the ladder's own multiples have to move:
  `CONFIG.partial_tp`, TP1 at 1.5 and TP2 at 2.5 stop distances. That needs its own
  pre-registration.
- **A new candidate the runs turned up,** not a finding: do not chase. An entry whose fill has
  already run a stop distance or more toward the target was, in these sets, worth skipping. It
  was not the sealed question, and these five sets are spent. It needs its own sealed test on
  data these studies have not used.
- **The flag's help text and docstring now say what it does** under the ladder.

## Limits

The five sets are the ones the defaults were tuned on and this month's studies reused. Folds of
one set are not independent. Neither arm makes money in any case.
