# Maker entries versus taker entries: pre-registration (before any arm is run)

Date: 27 October 2026. Driver: `scripts/maker_test.py`. Offline only.

## Why this is not "the same trades at a lower fee"

A maker entry saves the entry leg's fee (0.02% against 0.06%) and its entry
slippage (the backtest charges a resting limit none), so a round trip that
costs about 0.22% as a taker costs about 0.13% with a maker entry and a taker
exit. Against that, a resting limit fills only when price comes to it. The
orders that do not fill are not random: price kept going, so they are the
winners a limit misses, and the orders that do fill are the ones price moved
against. The analyzer also sets the limit price itself (a pullback level
inside one ATR, at least 0.15 ATR better than the market) and shifts the stop
and target with it, so a limit entry is a different entry as well as a cheaper
one. The honest question is whether the live behaviour, limit entries with a
realistic fill, beats taking the market, on the whole system.

Every earlier backtest in this work used `--honest`, which forces the
`next_open` fill mode, and that mode ignores limit prices. They measured an
all-taker system. Live, with `DEFAULT_ORDER_TYPE=limit` (the default), rests
limits. This test measures that difference.

## Arms (all run the live system: `--as-live --breaker-reset-bars 24 --walk-forward 6`)

| arm | entry fills | settings |
|---|---|---|
| market | every entry a market order at the next open, taker fee and slippage | `LIMIT_ORDERS_ENABLED=0` (see Addendum 1) |
| touch | limits rest from the next bar, fill on a bare touch at the limit, maker fee, no slippage | the `--as-live` default: the optimistic upper bound |
| realistic | the same limits, but a fill needs 5 bp of trade-through, a 0.5 queue probability for a bare qualification, and a 10 bp trade-through fills for sure | `--limit-through-bp 5 --limit-queue-p 0.5 --limit-queue-sure-bp 10 --limit-maker-entry` |

The realistic values are `REALISTIC` in `bot/core/limit_shadow.py`, fixed there
before this test and not tuned here. Exits are the system's own (taker, with
`MAKER_TAKE_PROFIT_ENABLED` off). Same seed, same bars in every arm.

## Windows and datasets

Stage 1 of the risk sweep: discovery `corr_dense_1h` and `majors_1h_v2` folds
ending on or before 2026-05-22; holdout `majors_1h_v3` and `alts_1h_v3`.

## Measures

Per fold: out-of-sample return and maximum drawdown (paired with the `market`
arm), trades, ideas generated, and the limit lifecycle (filled, expired,
cancelled on drift). Fill rate is filled over filled plus expired plus
cancelled.

## Pass rule for `realistic` against `market` (the primary comparison)

The stage 1 rule of the risk sweep: a mean paired return difference of at
least +1.0 percentage point in each of the four datasets; at least +1.0 on the
holdout with holdout drawdown no worse; and a 95% bootstrap interval over
calendar fold positions above zero. `touch` against `market` is reported as the
upper bound and cannot pass the strategy. `touch` minus `realistic` is reported
as the size of the touch model's optimism.

## Limits stated in advance

The drift-to-market chase live uses is not modelled (a drift cancel is an upper
bound on the chase's effect). A fill inside the signal bar is optimistic about
the order of events within the bar. Queue position is a probability, not a
book. Six folds per dataset is small; the interval is over calendar folds.
Fees are the schedule in `bot/config.py`, not a venue's rebate tier.

## Addendum 1: the `market` arm was mis-specified (written after the first report)

The registration named `DEFAULT_ORDER_TYPE=market` for the all-taker arm. That
was taken from an exploration report and not checked. It only changes the
default order type: the analyzer still computes a pullback limit level and sets
the order type to limit whenever it finds one, so that arm still rested limits
(its first report showed 1020 limit fills and a 0.48 fill rate) and was not a
taker baseline. The variable that turns limit entries off is
`LIMIT_ORDERS_ENABLED=0`. Only the `market` arm was re-run. The `touch` and
`realistic` arms do not depend on it. The first run's `touch` and `realistic`
differences against the mis-specified arm were read before this correction
(`touch` +0.22, `realistic` -0.43 pp over all folds), so they are recorded as a
first, invalid comparison and carry no weight. The pass rule, the datasets and
the other two arms are unchanged.

## Addendum 2: is the limit arm better, or just smaller? (written before the control is run)

The first result left one question open: `realistic` beat `market` while taking
fewer trades at an unchanged profit factor in three of four datasets. A system
that loses on average improves by trading less, so the control is the `market`
arm with the same share of entries skipped at random.

**Arm `skip`**: the `market` arm (`LIMIT_ORDERS_ENABLED=0`) with
`--entry-skip-p P --entry-skip-seed k`, which drops that share of approved
entries by a hash of the symbol, the bar and the seed (a measurement-only hook,
off by default, that releases the entry's pending intent as an expired limit
does). Three seeds, k = 0, 1, 2. `P` is fixed per dataset from the trade counts
already on file, as 1 minus the ratio of the `realistic` arm's positions to the
`market` arm's:

| dataset | market positions | realistic positions | P |
|---|---|---|---|
| corr_dense_1h | 684 | 495 | 0.276 |
| majors_1h_v2 | 492 | 241 | 0.510 |
| majors_1h_v3 | 123 | 116 | 0.057 |
| alts_1h_v3 | 144 | 80 | 0.444 |

A skip changes which later ideas find a free slot, so the realised position
count will not equal the target; it is reported beside the target.

**Comparison**: the fold-wise mean over the three seeds is the baseline, and
`realistic` against it is judged by the same stage 1 rule as before (at least
+1.0 percentage point in each of four datasets; at least +1.0 on the holdout
with drawdown no worse; interval over calendar folds above zero). Reading:

* **Passes**: the limit entries pick better trades than skipping at random.
* **Does not pass**: the limit entries act as a trade filter; the earlier pass
  was fewer trades, not better ones.

`market` against the seed mean is reported too, to show what skipping alone
does. Nothing else in the registration changes.

## Addendum 3: the count-matched control (written before it is run)

The first random-skip control was not matched on what it controls for: its
realised position counts (596, 305, 107 on three datasets) were 17% to 25%
above the limit arm's, because a skipped entry frees a slot for a later idea.
This control calibrates the skip rate until the realised count matches.

**Targets**: the `realistic` arm's pooled position count per dataset, already
on file: corr_dense_1h 495, majors_1h_v2 241, majors_1h_v3 116, alts_1h_v3 80.

**Calibration** (reads only position counts, never returns): the `market` arm
with `--entry-skip-p P --entry-skip-seed 0`, starting at the Addendum 2 rate
(0.276, 0.510, 0.057, 0.444). Each step reads the realised pooled count `T`
and moves `P` by a secant step on the last two points (a first step of
`(T - target) / market positions`), clamped to [0, 0.95], rounded to 3 places.
It stops when `|T - target| <= max(5% of target, 3 positions)` or after 6
runs, and then uses the closest point. The calibrated `P` per dataset is
written down before the comparison is run.

**Comparison**: seeds 0, 1 and 2 at the calibrated `P` (the calibration run is
seed 0), the fold-wise mean as the baseline, and `realistic` against it by the
same stage 1 rule as before. Realised counts are reported beside the target.

* **Passes**: at the same number of positions, the limit entries pick better
  trades than a random subset.
* **Does not pass**: at equal count the limit entries are no better than
  skipping at random. They are a trade filter, and the earlier pass is
  explained by trading less.

Nothing else in the registration changes.
