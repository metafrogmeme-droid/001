# Two market-neutral daily rules: short-term reversal, and ETH/BTC. Pre-registration (2026-11-08)

Written and sealed before the evaluator exists and before either rule is computed on any price.

## Why

Every long/short rule the repo tested lost on its holdout, and the one rule that passed is long
only and earns its keep as a smaller drawdown than BTC, not a better return
(`NEW_STRATEGY_SUMMARY_2026-10-28.md`). Two families with a different source of return have not
been tested:
- **N1, short-term reversal:** coins that fell most over a week tend to recover part of it the next
  week. Measured as a spread against the whole universe, so it is not a bet on the market.
- **N2, ETH/BTC:** the ratio of the two largest coins stretches and comes back. Trading the ratio,
  long one and short the other, is not a bet on the market either.

## Data

The 20-coin Binance daily panel already frozen for `new_strategy_xsmom_regime`
(`docs/research/prereg/new_strategy_xsmom_regime/data/`, hashes in its `data.sha256`), loaded with
that study's `load_panel`. No new data is fetched.

- **Development:** 2018-06-01 to 2022-12-31.
- **Holdout:** 2023-01-01 to the last bar in the panel. These prices were read by earlier studies of
  other rules. The holdout is clean for these two rules, not for the market's behaviour.

## Rules (every number fixed now)

Shared: the decision is made on the close of day t, the position is entered at the open of day t+1
and held open to open (the xsmom study's clock). A coin is eligible on day t when it has at least
150 days of closes and readable closes on day t and t-7. Costs are 0.10% a side on every change of
weight, on every leg. Funding is not charged: in each rule the long and short legs are perps on the
same venue and pay and receive alike, which is stated as a limit rather than measured.

**N1, short-term reversal spread.** Every 7 days from 2017-08-17 (the xsmom anchor), rank the
eligible coins by their 7-day return, close t over close t-7. Hold, until the next rebalance:
- long the bottom quintile (round(n/5), at least 2 coins), equal weight, 0.5 of equity in total;
- short every eligible coin, equal weight, 0.5 of equity in total.
Weights drift with prices between rebalances. No position on a day with fewer than 10 eligible coins.

**N2, ETH/BTC ratio.** x = log(close ETH / close BTC); z = (x - mean of the last 30 x) / (standard
deviation of the last 30 x), on day t.
- Flat and z <= -2: long ETH 0.5, short BTC 0.5. Flat and z >= +2: short ETH 0.5, long BTC 0.5.
- Exit on the first day z crosses 0 from the entry side, or after 20 days held, whichever first.
- The legs are set at entry and held at fixed notional until exit; costs are charged at entry and
  exit only.

## Decision rule (two rules; each must pass on its own)

A rule **passes** when all hold:
1. net Sharpe (daily returns, sqrt(365), rf 0, days with no position count as zero return) at least
   0.5 on development AND on holdout;
2. on holdout, the 2.5th percentile (5% / 2 rules, Bonferroni) of a block bootstrap of the Sharpe
   (20-day blocks, 2,000 draws, seed 20261108) is above 0;
3. holdout max drawdown at most 20%;
4. positive net return in more than half of the calendar years in which the rule held a position on
   at least one day, development and holdout together.
N2 also needs at least 10 completed round trips on holdout; fewer is no verdict, not a pass.

A rule that passes goes to a paper forward test before anything else. Nothing in the live bot
changes on this result.

## Reported, deciding nothing

Gross and net return, turnover and cost share, by-year returns, exposure, and each rule's
correlation with BTC buy-and-hold on holdout (a neutral rule should be near zero). For N1, the long
leg alone against the equal-weight universe.

## Order of work

1. This file is sealed (`PREREGISTRATION.sha256`, `PREREGISTRATION.sealed_at`).
2. `scripts/daily_neutral_study.py` is written and its sha256 recorded in `code_at_unseal.sha256`.
3. `dev`: the development window only; no holdout bar is loaded. `result_dev.json`.
4. `holdout`: once. `result_holdout.json`, then `REPORT.md`.

## Expectation, written now

N1: short-term reversal in crypto is documented at shorter horizons, and costs at a weekly
rebalance of a quintile eat much of it. I expect a positive gross spread and a net Sharpe below 0.5.
N2: few trades; a z of 2 on a 30-day window fires a handful of times a year. I expect no verdict or
a fail.
