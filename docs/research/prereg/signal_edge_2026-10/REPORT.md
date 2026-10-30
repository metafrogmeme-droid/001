# Do the bot's signals carry any edge before costs? Report (2026-10-30)

Rule: `PREREGISTRATION.md` (sealed 2026-10-30 11:54:39Z, commit a8489513). Runs and
per-position rows: `runs/`. Code sha a8489513 for all four runs; the working tree also
held uncommitted edits to `bot/utils/user_store.py` and a new `bot/token/stars.py` while
they ran, neither of which the backtest runner imports (checked: neither module is loaded
by `bot.backtest.runner`).

## Verdict: POSITIVE on both universes. Stage 2 (fewer, cheaper trades) is next.

Mean return per position, % of position size, zero costs (the gross edge):

| dataset | discovery (folds 0-2) | holdout (folds 3-5) | label |
|---|---|---|---|
| majors_1h (ccc3f70b) | +0.113 (n 225, PF 1.18) | +0.366 (n 189, PF 1.84) | POSITIVE |
| alts_1h_v3 (2ddf52d1) | +0.067 (n 93, PF 1.13) | +1.494 (n 137, PF 2.34) | POSITIVE |

No row had an unreadable return. The universes agree, so by the sealed rule the calls point
the right way before costs and the follow-up is a separately sealed test of fewer, cheaper
trades, net of costs.

## Reported, deciding nothing

The same calls with the default `--honest` costs (taker 0.06% + slippage 0.05% per side):

| dataset | discovery | holdout |
|---|---|---|
| majors_1h | -0.194 (n 194, PF 0.75) | +0.183 (n 220, PF 1.34) |
| alts_1h_v3 | -0.194 (n 82, PF 0.71) | +1.474 (n 114, PF 2.20) |

Costs take about 0.3 points per position, roughly three times the discovery-half gross edge.

By setup and direction (gross, mean % per position; small groups, descriptive only):

| dataset | group | discovery | holdout |
|---|---|---|---|
| majors | swing | +0.045 (181) | +0.376 (167) |
| majors | scalp | +0.292 (26) | +0.328 (17) |
| majors | intraday | +0.537 (18) | +0.149 (5) |
| majors | long | -0.340 (138) | +0.088 (97) |
| majors | short | +0.831 (87) | +0.659 (92) |
| alts | swing | +0.099 (75) | +1.678 (123) |
| alts | scalp | -0.165 (15) | +0.014 (11) |
| alts | intraday | +0.447 (3) | -0.613 (3) |
| alts | long | +0.070 (58) | +1.848 (119) |
| alts | short | +0.063 (35) | -0.849 (18) |

## What this does and does not say

- It says the calls are not noise on these sets: before costs they made money in every half.
- It does not say the bot makes money. Net of costs the discovery halves lose, and these are
  the datasets the defaults were tuned on, which flatters every number here.
- Long and short disagree between universes (shorts carried the majors, longs the alts), so
  no direction filter is suggested by this.
- The holdout halves are strong partly because of their market windows (the alts holdout
  rallied). A fresh period the bot was never tuned on is required before any claim, which is
  why stage 2 must run on data outside these two sets.

## Addendum (2026-10-30, after stage 2): the per-position mean is not the account's result

The sealed measure here was the unweighted mean return per position. Stage 2 showed that this
can be positive while the account loses, because the bot sizes positions differently and the
larger ones lost. On this stage's own runs, weighted by position size (`size_rel`), the gross
mean per position is +0.110 on majors and -0.064 on alts (dollar profit factor 1.22 and 0.92).
The stage 1 verdict stands as sealed, but "the calls point the right way before costs" holds
for majors only once position size is counted.
