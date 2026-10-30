# Delta-neutral funding carry on Bitget, last three months: pre-registration (2026-10-30)

Written and sealed before any funding or price data below is fetched.

## Question

Long spot plus short USDT-M perp on the same coin cancels the price move and collects
funding when funding is positive. A long backtest on Binance funding already exists
(`docs/research/prereg/new_strategy_trend_carry/`: carry alone +10.4% a year in its
discovery window, +2.2% in its holdout). Bitget's public API serves funding history
back only to about 2026-08-01, so this asks a narrower question on Bitget's own
numbers: over the three months available, did carry earn enough, net of costs, to be
worth more than idle USDT, and is a forward recorder worth building?

## Data

- Universe, fixed by rule: Bitget USDT-M perpetuals of symbolType "crypto", status
  online, that also have a spot pair with the same symbol; the top 20 by 24h quote
  turnover at fetch time. Current-liquidity ranking is a selection bias, disclosed.
- Funding: every settlement from 2026-08-01 00:00 UTC to 2026-10-29 24:00 UTC.
- Prices: daily (1D, UTC) closes of the perp and the spot pair over the same days.
- Each file's sha256 is recorded before any outcome is computed.

## Books (two)

Each holds equal notional per coin, half the capital long spot and half short the perp,
so gross exposure is 100% of capital per coin and price moves cancel apart from basis.
Daily P&L per coin = funding received by the short perp + (spot return - perp return)
on the half-capital legs.
- C1 always on: all 20 coins for the whole window. Entry and exit once.
- C2 positive-only: rebalanced every 7 days; holds the coins whose mean funding over
  the previous 7 days was above zero, equal weight; a coin dropped is closed, a coin
  added is opened; nothing held when none qualifies (that capital earns nothing).

## Costs

Per side: spot 0.10% plus 0.05% slippage, perp 0.06% plus 0.05%. Opening or closing a
coin costs both legs on half the capital each. Funding settlements that cannot be read
are left out and counted, not treated as zero.

## Decision rule

The unit is the day. For each book: annualised net return = mean daily net P&L x 365.
"Worth a forward recorder" when C2's annualised net return is at least 5% (a rough level
for idle USDT in exchange savings products, which this study does not measure) and the
lower bound of a block bootstrap over weeks (7-day blocks, 10,000 resamples, seed 7) at
the 2.5th percentile is above 0. C1 is reported, deciding nothing.

## Limits

About 90 days; one market regime. Daily closes, not the settlement times, for the basis
leg. No margin, liquidation or counterparty model: a real short perp can be liquidated
on a spike while the spot leg is fine, and that risk is not priced here.
