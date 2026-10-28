# TSMOM (multi-lookback, weekly, vol-targeted): report, read 2026-10-28

**Verdict: nothing passes.** All four pre-registered tests fail. The two long/flat variants clear both
Sharpe bars, the drawdown bar and the calendar-year bar, and fail the one bar that asks whether the
holdout Sharpe is distinguishable from zero (90% block-bootstrap lower bound -0.26 and -0.36). The two
long/short variants lose money on the holdout. 4 tests here; this is another look at the daily-trend
family, not a new family, and it adds no independent evidence that the family has an edge.

## Protocol as run
- `PREREGISTRATION.md` sha256 `47d081c4...e9107`, sealed 2026-10-28T11:16:17Z; `PREREGISTRATION.addendum_1.md`
  (implementation clarifications, no parameter or bar changed) sealed 11:16:54Z. Both before any price
  bar was fetched.
- Data: Binance spot USDT daily klines via the free `data-api.binance.vision` host (no paid data used,
  the repo has no local daily history and the Kraken archive is not in the tree). 16 coins, closed bars to
  2026-10-27, no gaps in any series (`data_sha256.txt`, `data/`).
- Dev run (data cut at 2022-12-31) at `dev_run_at`; code sealed (`code_at_unseal.sha256`, `code_sealed_at`);
  holdout read once (`holdout_read_at`). The holdout run reproduces every dev number from the full data
  (checked in the output: `dev` blocks equal for all four tests, so no look-ahead).
- No code changed between seal and holdout. Test-file edits happened before the dev run.
- Script `scripts/tsmom_study.py`, tests `tests/test_tsmom_study.py` (15 pass). The full preflight was
  not run.

## Results, net of 0.10%/side (LS also net of 0.03%/day funding on long notional)
Dev 2018-03-12 (first scored day) -> 2022-12-31; holdout 2023-01-01 -> 2026-10-27 (1,396 days).

| test | dev Sharpe | holdout Sharpe | holdout CAGR | holdout 90% bootstrap Sharpe | holdout max DD | years up / counted | verdict |
|---|---|---|---|---|---|---|---|
| A (20/50/100/200) LF | 1.02 | 0.75 | +13.1% | [-0.26, +1.73] | 25.0% | 5/9 | FAIL (bootstrap) |
| A LS | 1.22 | -0.42 | -11.1% | [-1.16, +0.54] | 43.1% | 6/9 | FAIL (holdout Sharpe, bootstrap) |
| B (50/100/200) LF | 0.82 | 0.64 | +11.0% | [-0.36, +1.58] | 29.8% | 5/9 | FAIL (bootstrap) |
| B LS | 1.01 | -0.31 | -8.9% | [-1.03, +0.63] | 33.1% | 6/9 | FAIL (holdout Sharpe, bootstrap) |
| BTC buy-and-hold | 0.55 (dev) | **1.12** | **+50.9%** | | 53.0% (holdout), 76.6% (dev) | | benchmark |

Bars per test (dev Sharpe / holdout Sharpe / bootstrap lower > 0 / holdout DD <= BTC / years majority):
A_LF pass pass FAIL pass pass; A_LS pass FAIL FAIL pass pass; B_LF pass pass FAIL pass pass;
B_LS pass FAIL FAIL pass pass.

Year-by-year net return (A_LF): 2018 -3.7% (from Mar), 2019 +17.9%, 2020 +82.5%, 2021 +55.8%,
2022 -26.8%, 2023 +28.6%, 2024 +42.1%, 2025 -6.8%, 2026 -5.8% (to Oct 27).
A_LS: +28.6, +11.1, +44.6, +54.2, -2.7, -13.2, +2.6, -18.5, -12.0.
B_LF: -8.2, +11.6, +71.0, +58.8, -29.4, +34.7, +35.3, -8.1, -11.0.
B_LS: +4.2, +7.2, +26.7, +54.7, +15.6, -21.5, +19.3, -19.0, -7.5.

Operating figures (holdout, A_LF): annual vol 18.7%, mean long exposure 33%, turnover 10.8x equity a
year (914 trades), costs 7.1% of gross profit. A_LS: turnover 20.6x a year, gross profit sum -16.1%
before costs and funding. Dev A_LF: vol 19.4%, max DD 35.2%, mean exposure 17%.

## Sensitivities (not deciding)
| test | holdout Sharpe, 0.20%/side | no funding | with 0.03%/day funding on longs |
|---|---|---|---|
| A_LF | 0.70 | 0.75 | 0.56 |
| B_LF | 0.60 | 0.64 | 0.44 |
| A_LS | -0.51 | -0.28 | -0.42 |
| B_LS | -0.38 | -0.17 | -0.31 |

On a perp the long/flat book's Sharpe falls by about 0.2 and B_LF drops below 0.5, so the long/flat
result depends on being run on spot.

## What this says
1. The long/flat rule is a real but weak sleeve here: positive Sharpe on both windows, low drawdown
   against BTC, costs a minor share. But the holdout Sharpe cannot be told from zero (the 90% interval
   spans -0.26 to +1.73 on 1,396 days), the sleeve lost money in 2022, 2025 and 2026, and it earned
   13% a year against BTC's 51% in the holdout. It is capital preservation, not a way to make money
   beyond holding the coin. BTC buy-and-hold had a higher holdout Sharpe (1.12) than every variant;
   the drawdown bar is easy against a 53% BTC drawdown.
2. Short legs destroy the holdout: both long/short variants have negative gross profit in 2023-26
   (A: -16% before costs), the trend rule being whipsawed in range markets. Dev looked good
   (Sharpe 1.0-1.2) mostly through 2018 and 2022 shorts; that edge did not carry.
3. The vol-target machinery works as designed (realised vol 19-22% against a 20% target), and turnover
   stays moderate for LF (4.6-10.8x a year).
4. Prediction versus outcome (written in the pre-registration): dev 0.8-1.5, **yes** (0.8-1.2);
   holdout 0.2-0.7, LF **yes** (0.64-0.75, slightly above for A), LS **no** (negative); bootstrap
   lower bound below 0 in most variants, **yes**; LF beats LS on holdout, **yes**; 25% that something
   passes, **it did not**.

## Caveats, all of them
- The holdout is not clean: earlier studies read Kraken prints of these coins for 2021-2026 and their
  results are known; the rule is a close cousin of the already-reported daily trend sleeve.
- Survivorship: the 16 were chosen from a 2022 view; Binance prices (USDT), not Kraken USD; USDT is
  treated as USD.
- A dev window dominated by the 2020-21 bull (A_LF made +82% and +56% there) overstates the family;
  only 9 calendar years exist, of which 4 are down for LF.
- Costs are the assumed 0.10%/side; no order-book depth model. Funding is the fixed 0.03%/day, not real
  funding, shorts credited nothing. No borrow model (shorts are perp shorts).
- The full preflight (ruff/mypy baselines, honesty gates, the full test gate) was not run; ruff on the
  two new files is clean and `pytest tests/test_tsmom_study.py` passes.

## Files
`PREREGISTRATION.md`, `.sha256`, `.sealed_at`, `PREREGISTRATION.addendum_1.md`/`.sha256`, `data/` and
`data_sha256.txt`, `result_dev.json`, `result_holdout.json`, `code_at_unseal.sha256`, `code_sealed_at`,
`dev_run_at`, `holdout_read_at`, this report; `scripts/tsmom_study.py`, `tests/test_tsmom_study.py`.
