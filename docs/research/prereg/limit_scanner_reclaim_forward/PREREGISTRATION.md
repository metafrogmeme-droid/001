# Pre-registration: the corrected reclaim variants, forward shadow

Written 2026-11-12 (UTC), at about 02:00, before any bar after the cutoff
below was read by anyone working on this test. This file records the
hypothesis, the variants and the pass bar. It does not record a result.

## Why this is a new test

The original forward test (`docs/research/prereg/limit_scanner_forward/`,
cutoff 2026-10-23, N = 4) registered V1 and V3 with a reclaim fill that read
its own bar's close and booked the entry at the limit price. PR #773 fixed
that fill (`bot/backtest/limit_fill.py`). By the owner's decision of
2026-11-12, V1 and V3 as they now fill count as a new test, registered here
from today. The original test's record is kept as written and labelled
pre-fix (`docs/research/prereg/limit_scanner_forward/PRE_FIX.md`).

The windows already read are spent and are not this test: the frozen
holdout (2026-04-24 to 2026-10-10), and an interim look at 19.5 days after
2026-10-23 taken on 2026-11-11 with the old fill.

## Question

Does a pullback level that is traded through and then reclaimed on the
hourly close, entered at that close with a taker order, have a positive mean
net basis points per trade on data nobody had read when this was written?

## Variants

Both use `bot/core/limit_shadow.py::variant_params` as merged in PR #773.

- **V1**. The scanner's default parameters. Entry at the close of a bar that
  traded at least 5 bp through the limit and closed back through it, at the
  taker rate. No queue: a taker order fills. First exit step on the next bar.
- **V3**. V1, and a 1–1 leader vote is read as neutral, so it places nothing.

N is 2. A variant tried after this window is read is a new test.

## What is scored

- Cutoff: `2026-11-12T00:00:00+00:00`, exclusive. A fill at or before that
  instant refuses the verdict. A fill that is not strictly after its decision
  refuses the verdict.
- Costs: the entry pays the taker rate (`trade_costs.leg_rate_pct(TAKER)`,
  0.06% when written). Exits pay 2 bp, as in the original test.
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
  (1.96 standard errors);
- the deflated Sharpe probability that the true per-trade Sharpe exceeds 0
  is at least 0.95, with N = 2;
- **and the same three statistical conditions still hold when every exit is
  charged the taker rate instead of 2 bp.** A stop is a taker fill live, so
  the 2 bp exit flatters every stopped trade; a variant that survives only
  on the flattering cost is not a pass.

If the upper bound is below 0 and the window is evaluable, the variant is
refuted. Anything else is inconclusive.

Scored with `evaluate_forward(trades, funding_priced=...,
cutoff_ms=RECLAIM_FORWARD_CUTOFF_MS, cutoff=RECLAIM_FORWARD_CUTOFF,
n_trials=N_RECLAIM_FORWARD)`.

## What is already known, stated so it is not mistaken for a result

On the spent frozen holdout, the same trades entered at the reclaim close
lost about 12 to 13 bp a trade after costs and about 5 bp before them
(2026-11-11, metafrogmeme-droid/Playbooks-BITGET, RESULTS.md under
research/runeclaw-limit-scanner). The prior for a pass is low. This test is
registered so that the answer is read honestly, not because a pass is
expected.
