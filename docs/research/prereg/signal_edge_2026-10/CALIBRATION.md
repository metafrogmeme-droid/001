# Stage 2 calibration: the confidence threshold on the stage 1 sets (2026-10-30)

Run after the stage 1 report and before any stage 2 seal, on the same two datasets, with the
default `--honest` costs. It chooses nothing by itself: its job is to say whether one level
deserves a sealed test on data the bot has never seen. Outputs: `calibration/`.

Mean net return per position, % of position size (profit factor):

| threshold | majors discovery | majors holdout | alts discovery | alts holdout |
|---|---|---|---|---|
| none | -0.194 (0.75), n 194 | +0.183 (1.34), n 220 | -0.194 (0.71), n 82 | +1.474 (2.20), n 114 |
| 0.65 | -0.318 (0.62), n 147 | -0.096 (0.86), n 158 | -0.071 (0.88), n 58 | +2.312 (3.24), n 95 |
| 0.70 | -0.175 (0.76), n 128 | -0.354 (0.55), n 135 | +0.109 (1.19), n 41 | +2.091 (3.40), n 93 |
| 0.75 | -0.073 (0.91), n 94 | +0.035 (1.05), n 115 | -0.048 (0.81), n 16 | -0.197 (0.84), n 40 |
| 0.80 | +0.090 (1.15), n 40 | +0.267 (1.58), n 55 | -0.126 (0.42), n 6 | -0.486 (0.60), n 17 |
| 0.85 | -0.330 (0.54), n 14 | +0.358 (1.80), n 33 | +0.107 (n/a), n 5 | -1.510 (0.06), n 7 |

## Reading

- No level is positive in all four halves. The response is not monotone in the threshold,
  which is what noise looks like at these counts.
- 0.80 is the only level positive in both majors halves, on 40 and 55 positions; on the alts
  it leaves 6 and 17 positions and loses.
- A filter tuned here and then tested on these same sets would measure nothing. Any stage 2
  must be sealed with its level fixed in advance and run on data outside both sets.
