# Weekend moves on Bitget US-stock perps: results (2026-10-30)

Pre-registration: `PREREGISTRATION.md` (sealed 2026-10-30T08:02:00Z, pushed in
41d45ed4 before any data was fetched). Data hashes: `data/manifest.json` (pushed in
89755d0e before any outcome was computed). Driver: `scripts/weekend_perps_study.py`.
Full output: `report.txt`, `result.json`. No addenda.

## Verdict: FAIL. None of the three rules passes.

Net P&L per weekend (equal weight over symbols), entered Sunday 20:00 ET, closed
Monday 09:00 ET, costs 0.10% per side per leg plus actual funding:

| rule | discovery (18 weekends) | holdout (17 weekends) | holdout lower bound | verdict |
|---|---|---|---|---|
| H1 momentum, SPY-hedged | -0.72% | -0.80% | -1.05% | FAIL |
| H2 reversal, SPY-hedged | -0.08% | -0.00% | -0.22% | FAIL |
| H3 SPY momentum | -0.18% | -0.20% | -0.31% | FAIL |

## What the data does show (reported, deciding nothing)

- **The weekend moves partly reverse.** The correlation between a stock's move
  against SPY from Friday's close to Sunday evening and its move from Sunday
  evening to Monday 09:00 is -0.31 in discovery and -0.25 in the holdout. The sign
  holds in both windows.
- **The reversal is about the size of the cost.** Hedged reversal (H2) earns about
  +0.4% per weekend before costs, and its two legs cost 0.40% at taker rates, so it
  nets to zero. Unhedged reversal (one leg, 0.20% cost): +0.20% discovery, +0.21%
  holdout, lower bound -0.01%. At 0.05% per side, hedged reversal nets about +0.20%
  on the holdout; at 0.20% per side, -0.40%.
- **The perps really trade on weekends**: 71 of 53,134 weekend hourly bars (0.1%)
  had zero volume. One weekend was left out for a missing bar (30 symbol-weekends).

## What this means for the Weekend Event Agent (Hackathon kit)

- A simple price rule does not survive costs, as the kit's non-negotiable #4 asks
  this step to check. The kit's thesis, that weekend NEWS read by an LLM gives an
  edge, cannot be tested offline, and nothing here supports it: any agent edge
  would have to come from the news layer alone.
- The one consistent pattern is reversal of the early-weekend move, and it is
  roughly equal to taker costs. A version that pays maker fees (0.02%) on one leg
  is the only follow-up worth testing. It must be pre-registered fresh and
  validated on weekends after 2026-10-26, because these weekends have now been
  seen. That is a forward paper test on Bitget Demo, which is what the kit asks for
  anyway.

## Limits

35 weekends (17 in the holdout) is little data; only a large effect could pass.
The universe was ranked by current liquidity (a selection bias). Slippage is
assumed, not measured; weekend spreads on thin names may be wider. US holidays
were not removed.
