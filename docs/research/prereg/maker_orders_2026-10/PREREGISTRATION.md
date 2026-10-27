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
| market | every entry a market order at the next open, taker fee and slippage | `DEFAULT_ORDER_TYPE=market` |
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
