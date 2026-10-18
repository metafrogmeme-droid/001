# R2: funding-rate rules on USDT-M perps (no spot leg) — 2026-10-17

**Verdict: nothing passes.** Three pre-registered rules, each failing three of the
four criteria (dev Sharpe ≥ 0.5, holdout Sharpe ≥ 0.5, 90% bootstrap above 0) and
passing only the drawdown one, which BTC's 53% holdout drawdown made easy.
Written by the session from the research agent's run; every figure below is in
`holdout_output.txt` and `result_holdout.json` beside this file.

## Protocol
- Funding fetched first (Hyperliquid public API, hourly, as a proxy for Bitget's);
  data only. `funding_raw/`, `funding_raw.sha256`, `coverage.txt`.
- Pre-registration sealed 2026-10-17T06:38:33Z,
  sha256 `75a1aaeb3ddc11a77db35f366f27b8b67d2479a0c192b8991b117487f608b078`.
- Dev run 06:42:12; code sealed 06:42:32 (`code_at_unseal.sha256`); holdout read
  once at 06:43:04. No addenda, no parameter changes. The holdout run reproduced
  every dev number from untruncated data (no look-ahead).

## Setup
- Primary universe (9 coins with funding on every day from 2023-06-01): BTC ETH
  BNB SOL DOGE AVAX LTC ATOM LINK. Secondary (reported, never decides): all 20.
- Dev 2023-10-01 → 2025-03-31; holdout 2025-04-01 → 2026-10-14.
- Costs 0.10%/side on traded notional; funding = actual settlements summed per
  Bitget day, applied to signed notional. Signal at close t, fill at open t+1.

## Results, primary universe (net of costs and funding)
| rule | dev Sharpe | holdout Sharpe | full 90% bootstrap | holdout DD | verdict |
|---|---|---|---|---|---|
| F1 cross-sectional carry (weekly, top/bottom quartile) | −0.32 | +0.39 | [−0.96, +0.92] | 14.9% | FAIL |
| F2 funding-z contrarian (7d z vs 90d, ±2) | −1.38 | −0.63 | [−1.98, +0.06] | 9.5% | FAIL |
| F3 trend (H1a) + crowded-funding filter | +0.39 | +0.48 | [−0.43, +1.44] | 6.5% | FAIL |
| ref: H1a unfiltered, same eligibility | +0.81 | +0.68 | [−0.19, +1.77] | 8.0% | not a test |
| BTC buy-and-hold | +1.87 | +0.14 | [+0.04, +2.17] | 53.3% | benchmark |

By year (net): F1 +9.2 / −23.7 / −2.4 / +9.8; F2 −5.0 / −8.0 / +0.2 / −4.7;
F3 +4.2 / +1.4 / +3.0 / +2.6 (2023 Oct–Dec … 2026 to 14 Oct).
Secondary universe agrees: F3 dev 0.59, holdout 0.35; F1 0.25 / 0.12; F2 −1.05 / −0.05.

## Why (post-hoc, decides nothing)
- F1's funding leg is real: +4–10% a year received, every year. Weekly rebalancing
  costs ~5.3%/yr and the unhedged price leg (20–30% vol) decides the result. Zero-cost
  ceiling ≈ 0.3–0.45 Sharpe at gross 1× with no spot hedge.
- F2 has the wrong sign: crowded funding continued. Flipped it is ~0.5–0.6
  correlated with trend and faded out of sample.
- F3's filter removed 21% of the trend rule's coin-days, its best ones: high
  funding marks strong trends. Filtered is worse than unfiltered in 3 of 4 cells.

## Caveats
Proxy venue (Hyperliquid funding for Bitget prices); 18-month dev and holdout
(Sharpe s.e. ≈ 0.8, low power); the holdout's prices were already read by the
price-only study; survivorship; 3 tests here, 8 slow-rule tests in this line.

## Next pre-registered test, if any
"F1b": monthly rebalance with a rank buffer, each side beta-matched to BTC, the
zero-cost ceiling stated in advance, tested only on unread data: liquid coins
outside this universe fixed by listing date, and forward from 2026-10-17 on
Bitget's own funding for at least 12 months. First compare Hyperliquid and Bitget
funding over Jul–Oct 2026 to measure the proxy. Following crowded funding (F2
flipped) is not recommended: it is trend in another coat and faded.
