# Pre-registration: multi-lookback time-series momentum (TSMOM), weekly, vol-targeted

Written 2026-10-28, before any price bar of this study was fetched or read.

## Question
Can a low-turnover, vol-targeted, multi-lookback trend book on liquid spot coins earn a net
Sharpe of at least 0.5 on a development window AND a later holdout, after fees, slippage and
(where the variant needs a perp) funding? Prior work in this repo: every 1h directional rule failed;
the daily 20/60/120 trend sleeve is the only positive family (Sharpe ~0.5-1.0, CI includes 0).
This is a variant of that family, so a pass here is one more look at the same family, not independent
evidence of a new one. Said in advance.

## Data
- Binance spot daily klines, USDT pairs, from the public market-data host `data-api.binance.vision`
  (free, no key; no paid data re-downloaded). USDT is treated as USD. Closed bars only; the last
  (still open) bar at fetch time is dropped.
- Universe, fixed by rule, no other coins considered: ADA ALGO ATOM BCH BTC DOGE DOT ETC ETH FIL LINK
  LTC TRX UNI XLM XRP (the 16 of the Kraken study). A coin is eligible on a day only with >= 150 bars of
  its own history and 60 valid daily returns. Survivorship gap (the 16 were picked from a 2022 view)
  is stated, not corrected, and biases results upward.
- A bar series with an internal gap (a missing day) makes the study stop and report CANNOT CHECK for
  that coin; nothing is filled.

## Split (by date, fixed now)
- Development: 2018-01-01 -> 2022-12-31. Holdout: 2023-01-01 -> last closed bar at fetch.
- Warm-up bars before 2018-01-01 are used for signals only. Returns are scored from the first day a
  position exists.
- Honest caveat: Kraken prints of the same coins for 2021-2026 were read by earlier studies, and those
  studies' results are known to the author. The holdout is a replication on another venue's prints with
  a different rule, not clean unseen data. Dev 2018-2022 is partly unread by the author in this form.

## Rule (exact)
Daily bars, UTC. For each eligible coin i on day t (decision at the close of day t):
1. score_i = mean over L in LOOKBACKS of sign(close_t / close_{t-L} - 1), in [-1, 1].
2. Variants: LF (long/flat) uses max(score, 0); LS (long/short) uses score.
3. vol_i = stdev of the last 60 daily log returns x sqrt(365).
4. u_i = score_i / vol_i / N, where N = number of eligible coins that day.
5. Sigma = 60-day sample covariance of daily log returns x 365 over the eligible coins.
   sigma_p = sqrt(u' Sigma u). k = 0.20 / sigma_p (portfolio vol target 20%/yr). If sigma_p == 0 or all
   scores are 0, the target is all flat.
6. w_i = k u_i; if sum|w_i| > 1.0 scale all down to gross 1.0 (no leverage); then clip each |w_i| <= 0.25.
7. Rebalance only on days where t is a Sunday (UTC). Fill at the OPEN of day t+1 (signal at close t,
   fill next open). Between rebalances positions are held in units (weights drift with price).
8. No-trade band: at a rebalance, coin i moves to its target only if |target_i - current_i| >
   0.25 x b_i, where b_i = k / vol_i / N (the full-signal weight); a target of exactly 0 always
   executes (exits are never blocked).
9. Daily return = sum of weight x open-to-open return of the coin for the day, minus costs.

## Costs
- 0.10% per side on traded notional (all-in: fee plus slippage), charged on sum |delta w| at each fill.
- Funding. LF is executable on spot: no funding. LS needs perps for the short leg: longs pay
  0.03%/day of long notional (the Bitget study's fixed charge; the funding re-cost study read the
  conditional figure at 0.06%/day on held days, so this is lenient), shorts receive nothing
  (conservative). The decision run charges funding on LS only.
- Reported beside it, never deciding: LF with the 0.03%/day charge as if run on perps; every variant
  at 0.20%/side; every variant with zero funding.

## Grid (pre-listed, counted)
LOOKBACKS in {A = 20/50/100/200, B = 50/100/200} x {LF, LS} = **4 tests**. Nothing else is tuned:
vol target 20%, vol window 60, band 0.25, cap 0.25, gross 1.0 are fixed. 4 tests here; 22 in this line of
work counting earlier ones loosely (not an exact figure); no multiplicity correction is applied to the
bars, and the count is printed beside any pass.

## Pass bars (every one, per test, net of costs and the decision funding)
1. Net Sharpe >= 0.5 on dev AND >= 0.5 on holdout (daily returns, sqrt(365), mean/stdev ddof=1).
2. Holdout 90% block-bootstrap lower bound > 0: moving-block bootstrap of the holdout daily returns,
   20-day blocks, 2000 draws, seed 20261028, lower bound = 5th percentile of the resampled Sharpe.
3. Holdout max drawdown no worse than BTC buy-and-hold's max drawdown over the same holdout days.
4. Positive net return in a majority (> 50%) of calendar years with >= 90 live days, full period.
A test that fails any bar fails. If several pass, the headline is the one with the highest DEV Sharpe,
with the test count beside it. If none passes the verdict is fail, with numbers.

## Protocol
1. Seal this file (sha256 in PREREGISTRATION.sha256) before fetching data.
2. Fetch data (`scripts/tsmom_study.py fetch`), hash it.
3. Dev run truncated at 2022-12-31 (`run --split dev`), seal the code hash (`code_at_unseal.sha256`).
4. Holdout run once (`run --split holdout`); it also reproduces dev from the untruncated data
   (no-lookahead check). No change after it. Failures reported.
5. REPORT.md carries every number, including the failures.

## Predictions, written now
Trend with this much dilution stays low-exposure; I expect dev Sharpe 0.8-1.5 on 2018-2022 (2020-21
bull) and holdout Sharpe 0.2-0.7 with the bootstrap lower bound below 0 in most variants. I expect LF
to beat LS on the holdout. Probability that at least one test clears every bar: about 25%.
