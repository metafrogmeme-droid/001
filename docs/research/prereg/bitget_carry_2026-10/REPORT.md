# Delta-neutral funding carry on Bitget, last three months: results (2026-10-30)

Pre-registration: `PREREGISTRATION.md` (sealed 2026-10-30T11:28:53Z, pushed in 5c361452
before any data was fetched). Data with hashes pushed in 2a98a745 before evaluation.
Driver: `scripts/bitget_carry_study.py`. Output: `report.txt`, `result.json`. No addenda.

## Verdict: FAIL. Carry was real but too small to beat idle USDT.

| book | days | annualised net | 95% lower bound | verdict |
|---|---|---|---|---|
| C2 positive-funding only, weekly | 90 | **+1.2%** | +0.5% | FAIL (bar: 5%) |
| C1 always on, all 20 coins | 89 | +2.5% | +1.0% | reported only |

- Gross funding on the 20 coins was about +4% to +8% a year (TRX was negative). Each
  book holds half its capital on the short perp, so it receives half of that, and the
  entry, exit and weekly rebalancing costs take part of what is left.
- The positive-only book did worse than always-on: it held nothing for the first week
  and paid to rotate coins whose funding barely changed sign.
- Both lower bounds are above zero: the income is steady. It is just small.

## What this means

On Bitget's own numbers for August to October 2026, delta-neutral carry paid about
1-2.5% a year after costs, less than the roughly 5% the pre-registration used for idle
USDT in exchange savings (not measured here; check the current Earn rate before
comparing). The earlier Binance backtest agrees on the order of magnitude (+2.2% a year
in its holdout). By the pre-registered rule, a forward carry recorder is not worth
building now.

## Reported, deciding nothing

Capital efficiency: posting the perp leg on margin instead of half the capital would
raise the yield towards the gross funding rate (about 5-7% a year), but adds
liquidation risk on a short-squeeze spike, which this study does not model. That is a
different risk, not free money.

## Limits

About 90 days in one market regime; current-liquidity universe; daily closes for the
basis leg; no margin, liquidation or counterparty model; 20 coin-days missing a close
were left out and counted.
