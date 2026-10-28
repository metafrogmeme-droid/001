# Cross-sectional momentum behind a BTC regime gate: report, 2026-10-28

**Verdict: the primary configuration clears all four pre-registered bars, but the pass is
mostly the BTC regime filter, not cross-sectional momentum, and it is not a clean out-of-sample
proof.** One test decided; nine configurations reported.

## Protocol kept
- Pre-registration sealed 2026-10-28T11:16:18Z, sha256
  `14ce7481d24b0c0bb0cd0f5f3237215a12ac47a1df970dec62b4618ca57558da`, before any price was loaded.
- Data: Binance spot daily USDT klines from the free public endpoint, 20 coins, hashes in
  `data.sha256`. No paid data. (The earlier Kraken/Bitget daily files are not in the tree.)
- Dev run (loads no bar after 2022-12-31), code sealed (`code_at_unseal.sha256`,
  `code_sealed_at`), holdout read once (`holdout_read_at`). No parameter changed. The full-data
  run reproduces the dev Sharpe (1.5007 vs 1.501).
- Signal at close t, fill at next open, open-to-open returns, drift between rebalances,
  0.10%/side on traded notional, no funding (spot), cash earns 0, Sharpe sqrt(365) with rf 0.

## Pass table (primary: lookback 90d, rebalance 14d, BTC 150d mean, top-k, inverse-vol, 30% vol target, no leverage)
| bar | result | |
|---|---|---|
| dev Sharpe >= 0.5 (2018-06 to 2022-12, 1674 days) | +1.50 | pass |
| holdout Sharpe >= 0.5 (2023-01 to 2026-10-27, 1395 days) | +1.06 | pass |
| holdout 90% block-bootstrap lower bound > 0 | +0.11 (interval [+0.11, +1.97]) | pass, barely |
| holdout max DD <= BTC buy-and-hold | 21.6% vs 53.0% | pass |
| positive in a majority of calendar years held | 6 of 8 | pass |

Calendar years, net: 2019 +23.5%, 2020 +46.6%, 2021 +174.0%, 2022 -4.6%, 2023 +30.8%,
2024 +59.6%, 2025 -9.1%, 2026 (to 10-27) +29.9%. 2018 had no position (gate off).

## Every number
| series | window | Sharpe | ann. return | ann. vol | max DD | boot 90% |
|---|---|---|---|---|---|---|
| primary | dev | 1.50 | 40.4% | 24.6% | 25.2% | [+0.70, +2.20] |
| primary | holdout | 1.06 | 26.6% | 25.2% | 21.6% | [+0.11, +1.97] |
| BTC buy-and-hold | dev | 0.61 | 19.0% | 71.8% | 76.6% | [-0.17, +1.44] |
| BTC buy-and-hold | holdout | 1.12 | 51.1% | 46.2% | 53.0% | [+0.24, +1.93] |
| equal-weight universe | dev | 0.74 | 28.2% | 88.8% | 80.6% | [-0.02, +1.57] |
| equal-weight universe | holdout | 0.65 | 23.7% | 62.1% | 71.4% | [-0.23, +1.48] |
| BTC regime filter alone | dev | 1.13 | 52.6% | 47.3% | 44.8% | [+0.30, +1.92] |
| BTC regime filter alone | holdout | 1.11 | 40.2% | 36.3% | 25.6% | [+0.24, +1.91] |
| primary, funding 0.03%/day on exposure | dev | 1.44 | 38.1% | 24.6% | 26.6% | [+0.63, +2.14] |
| primary, funding 0.03%/day on exposure | holdout | 0.92 | 22.1% | 25.2% | 22.5% | [-0.02, +1.83] |
| primary, 0.20%/side | dev | 1.48 | 39.7% | 24.6% | 25.7% | [+0.68, +2.19] |
| primary, 0.20%/side | holdout | 1.01 | 25.1% | 25.2% | 21.9% | [+0.06, +1.92] |

Costs paid by the primary: 0.5%/yr dev, 1.2%/yr holdout (0.2% and 0.4% of gross). Average
exposure 15% (dev) and 33% (holdout): the 30% vol target on 60-90% vol coins keeps it mostly cash.

## Robustness grid (9 configurations; only the primary decides)
Sharpe dev / holdout: lb60 rb14 ma100 1.65/1.25; lb60 rb14 ma200 1.41/0.83; lb60 rb30 ma100
1.56/1.49; lb60 rb30 ma200 1.26/1.29; lb90 rb14 ma100 1.52/1.29; lb90 rb14 ma200 1.26/0.97;
lb90 rb30 ma100 1.36/1.24; lb90 rb30 ma200 1.18/0.89; primary 1.50/1.06. All 9 clear
dev >= 0.5 and holdout >= 0.5, so the pass is not fragile to those knobs. Holdout max DD in the
grid 21% to 31%, all well under BTC's 53%. The shared dependence is the gate and the long-only
crypto beta, which the grid does not vary.

## What this does and does not show
- **The gate is the edge; the momentum leg adds little.** BTC held only above its 150d mean
  scores holdout Sharpe 1.11 against the strategy's 1.06, with 40% vs 27% annual return. The
  momentum basket helped in dev (1.50 vs 1.13, almost all of it 2021's alt run: +174%) and not
  in holdout. This matches the earlier failure of weekly momentum on its own (H3).
- **It does not beat BTC buy-and-hold on Sharpe in holdout** (1.06 vs 1.12) or on return
  (27% vs 51%). It wins on drawdown (22% vs 53%) and on the dev window, where BTC suffered
  2018 and 2022. A reader who wanted more money than holding BTC does not get it here.
- **The evidence is thin.** The holdout bootstrap bound is +0.11; with funding at 0.03%/day it
  is -0.02, which would fail bar 2. About 60% of the 8 calendar years are positive; 2025 lost.
- **Not a clean holdout for the market**: the 2021-2026 prices of these coins were read in
  earlier studies of other rules. Parameters were untouched by either window.
- **Biases upward, stated not corrected**: survivorship (the universe was chosen from coins
  still alive), the 2021 bull in dev, zero interest on cash, no spot-liquidity or slippage
  cushion beyond the 0.10% fee for the smaller coins, and a 20-coin list that includes SOL,
  AVAX, DOT, UNI, FIL and NEAR, which were not eligible until 2020-21 and are winners.
  Because eligibility needs 150 days of own history, the sample before 2021 is 3-8 coins and
  k is 1 or 2.
- **Venue**: the test is spot with no funding. As perps the long book pays funding; the
  sensitivity row shows holdout Sharpe 0.92 and a lower bound of -0.02.
- Pass rule 4 counts years in which any position was held, 8 years; 2018 is excluded because
  the gate was off for all of it. Counting it as a flat year would make 6 of 9, still a majority.
- Lint: ruff reports 3 style findings in the new files; they were left alone because changing
  them after the seal changes the sealed code hash.

## Recommendation
Treat it as a candidate low-turnover sleeve, not a proven money-maker. If run, run it as the
BTC regime gate with a small momentum basket, on spot or with a funding allowance, paper first.
The next test that would mean something is forward data (from 2026-10-28) recorded as the
daily-trend book is, because every historical window is now read. Expected value against simply
holding BTC with the gate: about zero in Sharpe, a better drawdown, lower return.

## Files
`PREREGISTRATION.md` (+ `.sha256`, `.sealed_at`), `data/` and `data.sha256`,
`result_dev.json`, `code_at_unseal.sha256`, `code_sealed_at`, `result_holdout.json`,
`holdout_read_at`, this report; `scripts/xsmom_regime_study.py`,
`tests/test_xsmom_regime_study.py` (13 tests, synthetic data).
