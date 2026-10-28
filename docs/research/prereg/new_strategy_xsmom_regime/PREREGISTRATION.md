# Pre-registration: cross-sectional momentum behind a BTC trend regime filter
Written 2026-10-28, before any price in the holdout window was loaded.

## Question
Every 1h directional rule failed net of costs. The only positive family is the daily
vol-targeted trend sleeve (Sharpe about 0.5 to 1.0, interval includes 0). Is there a
low-turnover long-only rule that earns net of fees and slippage on liquid coins: hold the
strongest 60-90 day momentum coins, sized by inverse volatility, and sit in cash whenever BTC
is below its long moving average?

Prior record in this line (kraken_spot_history): H3, weekly 28-day cross-sectional momentum,
long/short, FAILED (dev +0.49, holdout -0.46). This candidate differs in three pre-stated ways:
longer lookback, long-only top-k with a positive-return requirement, and a BTC regime gate.
That prior failure is the reason to expect the momentum leg alone to be weak; the gate is the
hypothesis under test. It is one test, not a new family.

## Data
- Binance spot daily klines, USDT quote, public endpoint `data-api.binance.vision`
  (free; no paid data used; nothing re-downloaded from CoinAPI/Kraken archives, which are not
  in the tree). Bars open 00:00 UTC. Only closed bars. Fetched by
  `scripts/xsmom_regime_study.py fetch` into `data/`, hashed in `data.sha256` before the dev run.
- Universe, fixed ex ante (the same 20 as bitget_slow_rules): BTC ETH BNB XRP ADA SOL DOGE DOT
  AVAX LTC LINK TRX BCH ATOM UNI ETC FIL NEAR XLM ALGO. A coin is eligible on day t only with at
  least 150 closed daily bars of its own history up to t. A coin with no Binance USDT history is
  absent and said so. Known bias, stated not corrected: survivorship (LUNA, FTT, MATIC-as-renamed
  and other failures are not in the list), so every result is biased upward.
- Stablecoin depegs and delistings are not modelled. A held coin whose next open is unreadable
  stops the run with an error; it is never treated as zero.

## Rule (primary, every parameter fixed here)
Decision at the close of day t using closes up to and including t. Fill at the open of t+1.
The position is held open-to-open and re-decided at the next rebalance.
1. Regime: BTC close(t) > simple mean of the last 150 closes. Off means target all cash.
2. Eligible coins n(t) as above. If n < 3, cash. k = min(4, floor(n/3)).
3. Momentum = close(t)/close(t-90) - 1. Take the top k by momentum; drop any with momentum <= 0.
4. Weights proportional to 1 / (30-day realised vol of daily close returns), summing to 1.
5. Gross scale = min(1, 30%/yr divided by the realised vol of that weighted basket over the
   last 60 daily returns, annualised sqrt(365)). Never above 1x: long-only, unlevered, no shorts.
   Remainder is cash earning zero (no interest credited; stated).
6. Rebalance when (days since 2017-08-17) mod 14 == 0, and also on any day the regime flips.
   Between rebalances weights drift with prices and nothing is traded.
7. Costs: 0.10% per side of traded notional (fee plus slippage all-in; Binance spot base taker
   fee is 0.10%, so this is the fee alone with no extra slippage cushion for the smaller coins,
   which is said in the report). Primary run is spot: no funding.

## Windows (by date, fixed now)
- Development: 2018-06-01 to 2022-12-31. Dev mode loads only bars on or before 2022-12-31.
- Holdout: 2023-01-01 to the last closed bar fetched. Read once, after the code hash is sealed.
- Honesty on the holdout: price history 2021-2026 of these coins was read in earlier studies of
  other rules. It is not a clean holdout for market behaviour; it is clean for this rule's
  parameters, none of which are tuned on anything. Said in the report.

## Pass bars (all must hold, on the PRIMARY configuration only, net of costs)
1. Net Sharpe >= 0.5 on development AND on holdout (daily returns, sqrt(365), rf = 0).
2. 90% moving-block bootstrap (20-day blocks, 2,000 draws, seed 20261028) lower bound of the
   HOLDOUT Sharpe > 0 (5th percentile).
3. Holdout max drawdown no worse than BTC buy-and-hold's over the same holdout dates.
4. Positive net calendar-year return in a majority (strictly more than half) of the calendar
   years in which the rule held a position on at least one day, across dev plus holdout.

## Pre-listed robustness grid (reported, never decides, counted for multiple testing)
Lookback {60, 90} x rebalance {14, 30} x regime mean {100, 200}: 8 variants, plus the primary
(lookback 90, 14 days, 150) = 9 configurations in total. Only the primary decides. The report
states how many of the 9 pass each bar; if the primary passes but fewer than 5 of 9 clear
dev Sharpe >= 0.5 and holdout Sharpe >= 0.5, the verdict says the pass is fragile.
Also reported, never deciding: (a) BTC held only when above its 150-day mean (the regime filter
alone, to show what the momentum leg adds); (b) the primary charged a perp-style funding of
0.03%/day on held long notional (the conservative figure used in earlier studies); (c) costs
doubled to 0.20%/side; (d) BTC buy-and-hold and equal-weight buy-and-hold of the eligible
universe.

## Predictions, written now
Long-only with the gate will lose to BTC buy-and-hold in raw return in bull years and win on
drawdown. I expect dev Sharpe near 1 (2018-2022 had a bear market the gate avoids and two
bull runs) and a holdout Sharpe at most 0.5 to 1.0 with an interval that includes zero,
because the holdout is about 3.8 years. I expect the momentum leg to add little over the
regime filter alone. If the holdout bootstrap bound is not above zero, that is a fail.

## Procedure and seal
1. Seal this file (`PREREGISTRATION.sha256`, `PREREGISTRATION.sealed_at`).
2. Fetch data, write `data.sha256`.
3. `xsmom_regime_study.py dev` (no holdout bar is loaded). Write result_dev.json.
4. Hash the script and the tests into `code_at_unseal.sha256`. Holdout mode refuses to run if
   the hash differs from that file or if result_dev.json is missing, and refuses a second read.
5. `xsmom_regime_study.py holdout`, once. Then REPORT.md with every number, failures included.
No parameter changes after step 5. Anything learned afterwards is post-hoc and labelled so.
