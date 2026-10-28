# New-strategy search, 2026-10-28: three daily candidates, each audited

Each candidate was pre-registered and sealed, run on discovery data, and read on holdout once.
An independent auditor re-ran each one. Prices are Binance spot/perp daily bars (free endpoint).
The 2023-26 holdout was already seen by earlier studies, and the universes carry survivorship.
Both biases flatter the results.

| candidate | verdict | holdout Sharpe | 90% bootstrap | holdout max DD (BTC 53.0%) |
|---|---|---|---|---|
| tsmom (multi-lookback, long/flat and long/short) | FAIL, 0 of 4 | 0.75 best | [-0.26, +1.73] | 25.0% |
| xsmom + BTC regime gate | passes all 4 bars, weakly | 1.06 | [+0.11, +1.97] | 21.6% |
| trend + carry + de-gearing | FAIL (bootstrap bar) | 0.84 | [-0.22, +1.80] | 26.3% |

## What this does and does not say
- None beats BTC buy-and-hold on holdout Sharpe (1.12) or return. The gain is drawdown: about 22-26% against 53%.
- The xsmom pass is the BTC 150-day gate. The gate alone scores 1.11. Cross-sectional momentum adds nothing.
- The xsmom bound is +0.11 and falls to -0.02 once 0.03%/day perp funding is charged. It is a spot-only result.
  A multiple-testing correction would not leave it passing.
- Long/short variants lose money on holdout. Shorting the weak coins has negative gross profit.
- Carry alone looks very smooth, but its holdout CAGR is only +2.2%. The overlay's gain over the plain sleeve
  is inside noise. Running longs on spot rather than perps saves 0.06-0.21 Sharpe.
- With about 3.75 years of holdout, the Sharpe standard error is about 0.5. These tests are low-power either way.

Verdict: a defensible low-drawdown, long-only, spot, paper-first sleeve exists. A profitable edge over holding BTC is not shown.
