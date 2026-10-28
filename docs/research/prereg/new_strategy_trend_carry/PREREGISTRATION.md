# Pre-registration: daily trend sleeve + funding-aware carry overlay + drawdown de-gearing

Written 2026-10-28, before any funding or perp-price file of this study was fetched and before any
result of this study was computed. Spot daily bars are the free Binance spot series already fetched
by `../new_strategy_tsmom/` (copied, not re-downloaded; no paid data is used).

## Question
Every 1h directional rule failed net of costs; the daily vol-targeted trend sleeve is the only positive
family (Sharpe ~0.5-1.0, CI includes 0). Can that sleeve plus (a) a funding-aware construction and
(b) a drawdown de-gearing overlay clear a net Sharpe of 0.5 on dev AND holdout, and what does each
overlay add compared with the plain sleeve? A pass here is evidence about this specific construction
only; the base sleeve is the same family as earlier passes, so it is one more look at it.

## Data (fixed now)
- Spot: `data/<COIN>.csv`, Binance spot daily klines (USDT treated as USD), 16 coins: ADA ALGO ATOM BCH BTC
  DOGE DOT ETC ETH FIL LINK LTC TRX UNI XLM XRP. Copied from `../new_strategy_tsmom/data` (sha256 in
  `data_sha256.txt`). Prices are used as open-to-open.
- Funding: real Binance USDT-M settled funding, free static files `data.binance.vision/data/futures/um/
  monthly/fundingRate/<COIN>USDT/`, column `last_funding_rate`, every settlement (8h, or 4h where the
  venue changed the interval) summed per UTC day. A settlement at `ts` belongs to the UTC date of
  `ts - 1000 ms` (a 00:00 settlement, with its few-ms jitter, closes the previous day). A coin-day with no
  settlement is MISSING, never zero.
- Perp: `data.binance.vision/.../monthly/klines/<COIN>USDT/1d/`, daily open, used only for the basis leg of
  the carry book (spot open-to-open return minus perp open-to-open return).
- Last day analysed: **2026-09-30** (last full month of the funding files). Spot bars after it are unused.
- A missing month file for a coin (404) makes that coin have no perp/funding before its first available
  month; it is eligible for carry only from 30 readable days after it. A gap INSIDE a coin's perp or
  funding history, or a held carry coin-day with a missing funding/perp reading, stops the run with
  CANNOT CHECK. Nothing is filled, except the stated perp-comparator fill below.

## Split (by date, fixed now)
- Development: 2020-01-01 -> 2022-12-31 (perp funding exists for BTC/ETH from 2019-09 and most others from 2020).
- Holdout: 2023-01-01 -> 2026-09-30.
- The simulation runs from the first spot bar; days before 2020-01-01 only warm states and are never scored.
- Honest caveat, said in advance: the prices of 2023-2026 were read by earlier studies in this repo and
  the trend sleeve's results on them (including `new_strategy_tsmom`, which I read before writing this) are
  known. The holdout is therefore NOT clean for the trend leg. The funding and perp-basis series are new
  to the author (earlier funding work used CoinAPI and Hyperliquid), so the carry leg is the part that is
  unread. Survivorship: the 16 coins were picked from a 2022 view and bias the trend leg upward.

## The rules (exact; decision at the close of day t, fill at the OPEN of day t+1)
### Trend leg (TR): spot, long/flat
`ts.target_weights` of `scripts/tsmom_study.py` (reused, not re-written): score = mean over L in {20, 60, 120}
of sign(close_t/close_{t-L}-1), floored at 0; inverse-vol (60-day) weights /N; portfolio vol target 20%/yr
using the 60-day covariance; gross <= 1.0; per-coin cap 0.25; weekly (Sunday close, Monday-open fill);
no-trade band 0.25 x full-signal weight, exits never blocked; eligibility >= 150 own bars. Positions are
held in units between rebalances. Executed on SPOT, so no funding is paid (a long needs no leverage here).
### Carry leg (CA): delta-neutral long spot + short perp
- Weekly on the Sunday decision. For each coin with perp+funding readable on all of the last 30 UTC days:
  m30 = mean daily funding over the last 30 days, m7 = over the last 7.
- Enter when m30 >= 0.00025/day AND m7 > 0. Stay while m30 >= 0.00010/day AND m7 > 0, else exit.
- A coin the trend leg targets long on that same decision is excluded (and an existing carry on it exits).
- Hold at most 5 coins, ranked by m30 descending (ties by name); each at notional 0.10 of equity, so carry
  notional <= 0.50. Capital check at each weekly fill: carry capital = 1.2 x notional (spot + 20% perp
  margin); if trend gross (pre de-gear) + carry capital > 1.0 the carry notionals are scaled by the factor
  that makes it 1.0. Fixed until the next weekly fill.
- Daily return of one carry coin = n x [spot_ret - perp_ret + funding_day] (n = notional fraction at fill;
  funding_day of the UTC date the period starts on). Liquidation of the short is not modelled (stated).
### Drawdown de-gearing (DG): scales the trend leg only
At every daily close compute total net equity E and its drawdown from the maximum of the last 180 daily
equity points (including today). Gear g = 1.0 if dd < 8%, 0.5 if 8% <= dd < 15%, 0.25 if dd >= 15%. A change of
g is traded at the next open on the trend weights (cost on |w_base x (g_new - g_old)|). Carry is not scaled.

## Costs
0.10% per side per leg on traded notional, all-in (fee plus slippage): a carry entry or exit is two legs, so
0.20% of notional each way. De-gearing and rebalances pay it on |delta weight|. No funding on spot. Funding
paid/received on carry is the real series above. Sensitivity (reported, never deciding): 0.20%/side.

## Configurations (6; all counted for multiplicity; only TC decides)
| id | what |
|---|---|
| **TC** | TR (spot) + CA + DG, the strategy under test |
| P1 | TR (spot) alone, the plain sleeve done correctly |
| P0 | TR on perps alone, long notional pays the real funding each day it is held (the plain sleeve as the bot would run it); a held coin-day with no funding reading is charged the same-day mean of the readable held coins, else 0.03%/day, and the count is reported |
| TD | TR + DG |
| TK | TR + CA |
| K | CA alone |
The overlay value is TC against P1 and P0, and each of TD, TK against P1. 6 tests here; the project line is
past 25 tests; no multiplicity correction is applied to the bars, and the count is printed with any pass.

## Pass bars (all, for TC, net of costs and funding)
1. Net Sharpe >= 0.5 on dev AND >= 0.5 on holdout (daily returns, sqrt(365), mean/stdev ddof=1).
2. Holdout 90% moving-block-bootstrap lower bound of Sharpe > 0 (20-day blocks, 2000 draws, seed 20261028, 5th percentile).
3. Holdout max drawdown no worse than BTC buy-and-hold's over the same holdout days.
4. Positive net return in a majority (> 50%) of calendar years with >= 90 scored days.
A configuration failing any bar fails. Overlay value is claimed only if TC beats P1 on Sharpe in BOTH dev and
holdout; otherwise the report says the overlay added nothing. An unreadable input makes its bar False.

## Predictions (written now)
- P1 dev Sharpe ~0.8-1.2, holdout ~0.5-0.8, max DD 10-25%. P0 is lower than P1 by about 0.1-0.2 Sharpe
  (earlier re-cost: funding 15-19% of gross profit).
- K: funding pooled 9.6%/yr; carry earns ~3-6%/yr of equity gross at <=0.5 notional, weekly turnover and
  0.4% round trips take a visible bite, basis noise is small. Sharpe high in 2020-2021, much lower after.
- DG: costs a little in trend years, saves little because the sleeve's drawdown is already ~12-25%; expect
  |delta Sharpe| < 0.1. Expected CAGR of TC low single digits to ~8%: by construction a low-return sleeve.
  If TC does not beat P1 in both windows the verdict is that the overlay is not worth its complexity.

## Protocol
1. Seal this file (sha256 in PREREGISTRATION.sha256) before fetching funding/perp data.
2. `scripts/trend_carry_overlay_study.py fetch`, hash the data.
3. `run --split dev` (data truncated at 2022-12-31); seal code hash (`code_at_unseal.sha256`).
4. `run --split holdout` once; it reproduces the dev numbers from untruncated data (no-lookahead check).
5. REPORT.md with every number, failures included. No change after the holdout is read.
