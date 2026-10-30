# Weekend reversal with maker costs, forward test: pre-registration (2026-10-30)

Follow-up to `docs/research/prereg/weekend_stock_perps_2026-10/`. That study failed
all three pre-registered rules, but reported (deciding nothing) that the early-weekend
move of a US-stock perp partly reverses before Monday 09:00 ET (correlation -0.31
discovery, -0.25 holdout), by about as much as taker costs. This tests the reversal at
maker-like costs, ONLY on weekends that had not happened when this was written.

## Data

The same 30 Bitget USDT-M stock perps and the SPYUSDT hedge, frozen in
`docs/research/prereg/weekend_stock_perps_2026-10/data/manifest.json`; the same public
endpoints; fetched once, after the last weekend below, with sha256 recorded before the
outcome is computed. A symbol delisted in the window is left out and counted.

## Weekends

Every weekend whose Friday is on or after 2026-10-30 and whose Monday is on or before
2027-02-15: 16 weekends. The evaluation is run once, after 2027-02-15.

## Clock and signal

Unchanged from the earlier study: A = Friday 16:00 ET, B = Sunday 20:00 ET,
C = Monday 09:00 ET; s = r(A to B) - r_SPY(A to B); y = r(B to C) - r_SPY(B to C).

## Rules (two)

1. R1 reversal, SPY-hedged: P&L = -sign(s) * y per symbol, legs as in the earlier H2.
2. R2 reversal, unhedged: P&L = -sign(s) * r(B to C), one leg.

## Costs

0.04% per side per leg: maker 0.02% plus 0.02% for adverse selection on a resting
order. R1 pays 8 one-way fills (0.32% a round trip), R2 pays 4 (0.16%), plus actual
funding between B and C. Reported only: the same rules at the earlier 0.10% taker cost.

## Decision rule

Per weekend, the equal-weight mean net P&L over its symbols. A rule passes when the
mean over the 16 weekends is above 0 and the lower bound of a bootstrap over weekends
(10,000 resamples, seed 7) at the 1.25th percentile (95% two-sided, Bonferroni for two
rules) is above 0. A pass is grounds for a Bitget Demo agent that places real maker
orders on the pass rule, to measure fills, which this model only assumes. A fail ends
this line.

## Limits

16 weekends is little data. Maker fills are assumed at the bar close; a resting limit
can miss the move. Funding is real; spreads are not measured.
