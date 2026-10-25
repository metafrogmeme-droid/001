# Pre-registration: Limit Entry Scanner forward shadow

Written 2026-10-25 (UTC), before any trade from after 2026-10-23 was scored.
This file records the hypothesis, the variants, and the pass bar. It does
not record a result. A later note in this directory that carries numbers is
a post-hoc comparison of the window that was already read, and it cannot
pass this bar.

## Question

After the universe bug is fixed, does a resting pullback limit on the
scanner's own exits have a positive mean net basis points per trade on data
the study has not yet scored?

The internal review's holdout, through 2026-10-10 on the frozen Bitget book
and a separate Binance book, was negative after costs. That window is spent.
It is not this test. The 61 looks already taken on it are not added to N.

## Hypothesis

A limit that fills because price traded through it is selected for the
adverse moves. The pre-registered attempts, each one lever, are:

- V0. Fixed score (percentile volume, unread book scaled onto 100) and the
  realistic fill. This is the bugfix plus the fill model, with no extra filter.
- V1. V0, and a fill only when the bar closes back through the limit.
- V2. V0, and the limit is set an extra 0.25 ATR further from the market.
- V3. V1, and a 1–1 leader vote is mixed, so it places nothing.

N is 4. A fifth variant tried after this window is read is a new test.

## What is scored

- Cutoff: `2026-10-23T00:00:00+00:00`, exclusive. A fill at or before that
  instant refuses the verdict. A fill that is not strictly after its decision
  refuses the verdict.
- Costs: maker 2 bp a side. The realistic fill needs 5 bp of trade-through,
  fills half of the shallower qualifications (a hash of the placement time,
  the bar time, the limit and the symbol; not the close), and fills every
  trade-through of 10 bp.
- Funding must be a price that was read. Unpriced funding is not zero, and
  the verdict cannot be a pass while funding is unpriced.
- An empty window has mean `null` and verdict inconclusive. It is not 0 bp.
- Shadow and paper only. The live bot does not place these orders. No
  capital is allocated.

## Pass bar

A variant survives only when all of these hold:

- at least 100 closed trades with `fill_ms` strictly after the cutoff;
- funding priced;
- the 95% normal lower bound of the mean net bp/trade is above 0
  (1.96 standard errors; at n ≥ 100 this is close to a Student t);
- the deflated Sharpe probability that the true per-trade Sharpe exceeds 0
  is at least 0.95, with N = 4. The Sharpe is the per-trade mean over the
  sample standard deviation, not an annualised figure. Kurtosis is the raw
  fourth moment.

If that upper bound is below 0 and the window is evaluable, the variant is
refuted. Anything else is inconclusive.

The repo's frozen Bitget book ends 2026-10-10. Until bars after the cutoff
exist, this test is not evaluable. That absence is not a pass and not a zero.
