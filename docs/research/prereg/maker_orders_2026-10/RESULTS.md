# Maker entries versus taker entries: results (27 October 2026)

`scripts/maker_test.py` on the registered arms, `--as-live --breaker-reset-bars 24
--walk-forward 6`, stage 1 datasets of the risk sweep. The `market` arm uses
`LIMIT_ORDERS_ENABLED=0` (Addendum 1 of the registration: the first
`market` arm used `DEFAULT_ORDER_TYPE=market`, which left limit entries on,
and was re-run).

## First comparison, invalid

Against the mis-specified `market` arm, `touch` read +0.22 pp and `realistic`
-0.43 pp over all folds. That arm still rested limits (1020 limit fills, fill
rate 0.48) and was not a taker baseline. Those numbers carry no weight.

## Corrected comparison (paired fold return, percentage points)

| arm | corr_dense | majors_v2 | majors_v3 | alts_v3 | holdout | dd ok | all folds, 95% interval | verdict |
|---|---|---|---|---|---|---|---|---|
| touch (upper bound) | +4.85 | +5.30 | +2.19 | +1.31 | +1.75 | y | +3.26 [+2.09, +4.42] | not scored |
| realistic | +3.18 | +4.69 | +1.93 | +1.07 | +1.50 | y | +2.61 [+1.71, +3.62] | **PASS** |

`realistic` passes the registered stage 1 rule: at least +1.0 pp in each of
four datasets, +1.5 pp on the holdout with no worse drawdown, and an interval
above zero.

## What the pass is made of

| dataset | arm | mean OOS return | trades | profit factor | win rate | mean max drawdown |
|---|---|---|---|---|---|---|
| corr_dense_1h | market | -8.39% | 684 | 0.56 | 39.5% | 9.96% |
| corr_dense_1h | realistic | -4.83% | 495 | 0.56 | 38.6% | 5.30% |
| majors_1h_v2 | market | -7.16% | 492 | 0.52 | 40.0% | 8.57% |
| majors_1h_v2 | realistic | -2.64% | 241 | 0.53 | 37.8% | 3.42% |
| majors_1h_v3 | market | -1.77% | 123 | 0.49 | 35.8% | 2.28% |
| majors_1h_v3 | realistic | +0.15% | 116 | 1.07 | 50.9% | 1.27% |
| alts_1h_v3 | market | -1.87% | 144 | 0.58 | 40.3% | 2.94% |
| alts_1h_v3 | realistic | -0.80% | 80 | 0.57 | 37.5% | 1.29% |

Over the four datasets the `market` arm took 1443 positions and `realistic`
932; the limit fill rate was 0.46 (932 filled, 898 expired, 178 cancelled on
drift). The `touch` model took 1115 positions at a fill rate of 0.49.

* **The gain is fewer trades, not better ones.** The profit factor is unchanged
  in three of four datasets (0.56 against 0.56, 0.53 against 0.52, 0.57 against
  0.58) and the win rate is the same or lower. The system loses on average, so
  filling a third fewer of its orders loses less, and drawdown roughly halves
  for the same reason. That is a filter on how often it trades, not an edge.
* **The holdout `majors_1h_v3` is the one place quality moves** (profit factor
  0.49 to 1.07, win rate 35.8% to 50.9%), on 116 positions in one dataset.
* **Almost every arm still loses.** Of the twelve arm-and-dataset rows only the
  two `majors_1h_v3` limit rows have a positive mean fold return (+0.42% touch,
  +0.15% realistic).
* **The touch model flatters limits.** It shows a profit factor of 0.75 on
  `corr_dense_1h` where the realistic fill shows 0.56. Trade-through and a queue
  take away the fills that were never available.
* The maker saving (about 0.09 percentage points of notional per round trip) is
  too small to see in the profit factor next to adverse selection.

## What was not tested

Whether the filter is the cause (an arm with the same fills at the taker fee,
or a taker arm that skips a matching share of its trades at random), the drift
to market chase live uses, maker take-profit exits (`MAKER_TAKE_PROFIT_ENABLED`),
other queue and trade-through settings than the repo's own, and any time after
2026-09-24.

## Addendum 2 result: the random-skip control (27 October 2026)

`scripts/maker_test.py report --control`, the registered skip rates 0.276, 0.510,
0.057 and 0.444, three seeds, the fold-wise mean as the baseline.

| comparison (paired fold return, pp) | corr_dense | majors_v2 | majors_v3 | alts_v3 | holdout | all folds, 95% interval | verdict |
|---|---|---|---|---|---|---|---|
| realistic vs random skip | +2.99 | +1.94 | +1.97 | -0.07 | +0.95 | +1.64 [+0.90, +2.44] | **fail** |
| market vs random skip | -0.19 | -2.76 | +0.04 | -1.15 | -0.55 | -0.97 [-1.77, -0.30] | not scored |

`realistic` misses the registered rule on two counts: `alts_1h_v3` is -0.07
where +1.0 was needed, and the holdout mean is +0.95 where +1.0 was needed. The
interval over all folds is above zero, so the limit arm is not simply a random
filter, and it is not clearly better either.

| dataset | arm | positions | profit factor | win rate | mean OOS return | mean max drawdown |
|---|---|---|---|---|---|---|
| corr_dense_1h | market | 684 | 0.56 | 39.5% | -8.39% | 9.96% |
| corr_dense_1h | skip (3 seeds) | 596 | 0.55 | 41.0% | -7.36% | 8.59% |
| corr_dense_1h | realistic | 495 | 0.56 | 38.6% | -4.83% | 5.30% |
| majors_1h_v2 | market | 492 | 0.52 | 40.0% | -7.16% | 8.57% |
| majors_1h_v2 | skip | 305 | 0.51 | 39.4% | -4.64% | 6.31% |
| majors_1h_v2 | realistic | 241 | 0.53 | 37.8% | -2.64% | 3.42% |
| majors_1h_v3 | market | 123 | 0.49 | 35.8% | -1.77% | 2.28% |
| majors_1h_v3 | skip | 130 | 0.49 | 37.2% | -1.82% | 2.33% |
| majors_1h_v3 | realistic | 116 | 1.07 | 50.9% | +0.15% | 1.27% |
| alts_1h_v3 | market | 144 | 0.58 | 40.3% | -1.87% | 2.94% |
| alts_1h_v3 | skip | 107 | 0.74 | 46.5% | -0.73% | 1.85% |
| alts_1h_v3 | realistic | 80 | 0.57 | 37.5% | -0.80% | 1.29% |

## Reading it

* **About 1.0 of the 2.6 points is a filter.** Random skipping alone beats
  `market` by 0.97 points over all folds (interval -1.77 to -0.30 for market
  against skip).
* **The control is not count-matched.** A skip frees a slot for a later idea, so
  the skip arm took 596, 305 and 107 positions where the limit arm took 495, 241
  and 80, 17% to 25% more. A larger share of the remaining +1.64 points is the
  limit arm still trading less than its control, not picking better.
* **Quality is the same in three of four datasets.** Profit factor 0.55 against
  0.56, 0.51 against 0.53 and 0.74 against 0.57 for skip against realistic. Only
  `majors_1h_v3` moves (0.49 to 1.07), on 116 positions in one dataset.
* **The verdict:** limit entries are mostly a trade filter here. There is a
  residual advantage over random skipping that does not meet the pre-registered
  bar, and it is confounded with a lower trade count. The honest status is not
  established as better picking.
* A matched-count control (skipping until the realised count equals the limit
  arm's, per dataset) would remove the confound. It was not registered and was
  not run.
