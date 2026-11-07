# An earlier exit ladder, and live's own limit entries: report (2026-11-07)

Pre-registration: `PREREGISTRATION.md` (sha256 6caa0ca4, sealed 09:28:04Z, pushed before any run).
Runs: `runs/` (summaries, trade rows gzipped, `report.json` from
`python3 scripts/exit_ladder_benchmark.py report --runs runs`). Code at 9dad4342.

The control (B) reproduces arm B of `live_history_benchmark_2026-10` exactly on all five primary
sets. Every arm passed its manipulation check: E1 and E2 reached TP1 on a larger share of
positions than B on every set, and LV rested and filled limit entries on every set (majors_1h:
105 filled, 163 expired, 26 cancelled for drift).

Mean fold return, profit factor in brackets:

| set | B | E1 | E2 | LV |
|---|---|---|---|---|
| majors_1h | -1.71% (0.79) | -1.20% (0.84) | -1.91% (0.73) | -0.27% (0.85) |
| majors_1h_v2 | -4.77% (0.69) | -4.61% (0.69) | -4.66% (0.69) | -1.12% (0.75) |
| majors_1h_v3 | -2.05% (0.60) | -2.22% (0.57) | -2.09% (0.58) | **+0.36% (1.18)** |
| alts_1h_v3 | -2.23% (0.53) | -1.69% (0.55) | -1.75% (0.50) | -0.50% (0.69) |
| corr_dense_1h | -6.56% (0.70) | -5.93% (0.73) | -5.67% (0.74) | -1.24% (0.86) |
| fresh10_1h (confirmation) | -4.10% (0.72) | -4.21% (0.70) | -4.08% (0.71) | -0.46% (0.90) |

## The sealed verdicts

- **E1, an earlier ladder: no verdict.** +0.33 points a fold over B, lower bound -0.09, better on
  4 of 5 sets, and worse on the confirmation set (-0.11).
- **E2, bank most at 1 stop distance: no verdict.** +0.25, lower bound -0.44, 3 of 5 sets.
- **LV, live's own limit entries: helps.** +2.91 points a fold over B, lower bound +1.54 (99.17%),
  better on all 5 primary sets, and +3.64 on the confirmation set.
- **Makes money: none.** LV averages -0.56% a fold (lower bound -1.25), positive on 1 of 5
  primary sets, -0.46% on the confirmation set. E1 and E2 average -3.1% and -3.2%.

## What it means

**Taking profit sooner does not fix the bot.** Moving the ladder's steps earlier changed which
exit closes a position and moved the result by a fraction of a point, inside the noise, with the
fresh set disagreeing. The early manual closes in the live record are not explained by the
ladder's levels.

**The benchmark's entry model was most of its loss.** Under `--honest` every entry fills at the
next bar's open, even when the analyzer asked for a pullback limit below it. Live places that
limit and lets it rest, and about half of them never fill. Measured that way, the bot loses about
a sixth as much on average, and less on every set: from -3.46% to -0.55% a fold on average over the primary sets.
Skipping the entries whose price ran away is what the closer-target arms stumbled on.

**It still does not make money.** With live's own entries the bot is close to flat, profit
factor 0.69 to 1.18 by set, and negative on four of five sets and on the fresh set.

**Live was worse than this.** The operator's 249 live exits had a profit factor of 0.61, below
every LV set. A touch of a 1h bar is counted as a fill here; a real resting limit can be touched
without filling, and fills that do happen are more often the ones the market runs through
(adverse selection). Live's exits were also partly priced from a ticker. So LV is an optimistic
reading of live's entries, not a measured one.

## What follows, as sealed

- **No change to any strategy setting.** E1 and E2 have no verdict; live already enters with
  resting limits.
- **The record is re-measured with `--as-live`,** and the change of method is stated. The figures
  on record were measured with market-at-next-open entries, which this shows overstate what live's
  entries lose. That correction is a separate, documented change, not made in this report.
- **Paper's fills should be checked against this.** The paper book fills at the price the engine
  reads when a confirm arrives. If that is a market fill rather than a resting limit, the paper
  forward tests (equal sizing, the sleeve aside) measure a different entry from live's. That is a
  question for the paper fill code, not answered here.

## Limits

The primary sets are the ones the defaults were tuned on; the confirmation set has been used
once before. Folds of one set are not independent. Touch fills on 1h bars are optimistic.
