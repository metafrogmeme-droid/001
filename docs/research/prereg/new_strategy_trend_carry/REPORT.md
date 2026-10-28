# Trend sleeve + funding-aware carry + drawdown de-gearing: report, 2026-10-28

**Verdict: the pre-registered strategy (TC) FAILS one of four bars** (holdout 90% bootstrap lower bound -0.22, needs > 0).
It clears Sharpe on both windows (dev 1.86, holdout 0.84), drawdown (26% vs BTC 53%) and years (4 of 7 positive).
The overlays add almost nothing to the plain sleeve out of sample. The carry leg on its own (K, an ablation, not the
decision test) clears all four bars but earns 2.2%/yr on equity. Nothing here is a strategy that "really makes money" at a
size that matters, and nothing should go to live on this evidence.

## Protocol (all seals in this directory)
- `PREREGISTRATION.md` sha256 `eb2f2ab6...87f960`, sealed 2026-10-28T11:23:16Z, before any funding or perp file was fetched.
- `PREREGISTRATION.addendum_1.md` (written after the fetch, before any simulation): Binance's monthly perp-kline file for
  2022-02 omits Feb 26-28 for FIL, LTC, TRX, XLM, XRP; those days are read from the same venue's daily file. No rule, bar or split changed.
- Data: spot daily = the free Binance series copied from `../new_strategy_tsmom/data`; funding and perp daily opens from
  `data.binance.vision` (free). Hashes in `data_sha256.txt`. No paid data re-downloaded.
- Dev run 11:45:02Z, code sealed 11:45:10Z (`code_at_unseal.sha256`), holdout read once 11:45:18Z. The holdout run reproduced every
  dev number from the untruncated data (no look-ahead). No parameter changed after the holdout.
- Dev 2020-01-01 -> 2022-12-31 (1,096 days). Holdout 2023-01-01 -> 2026-09-30 (1,369 days). Costs 0.10%/side per leg.
- 6 configurations tested in this study (counted; no multiplicity correction); only TC decides.

## Results (net of costs and funding)
| id | what | dev Sharpe | holdout Sharpe | holdout 90% bootstrap Sharpe | holdout CAGR | holdout max DD | dev CAGR / DD | years up / traded | passes |
|---|---|---|---|---|---|---|---|---|---|
| **TC** | trend (spot) + carry + de-gear | **1.86** | **0.84** | **[-0.22, +1.80]** | +14.0% | 26.3% | +39.7% / 17.3% | 4/7 | **FAIL** (bootstrap) |
| P1 | plain sleeve, spot | 1.58 | 0.78 | [-0.27, +1.65] | +14.8% | 34.4% | +37.2% / 23.0% | 4/7 | FAIL (bootstrap) |
| P0 | plain sleeve, perps, real funding | 1.37 | 0.71 | [-0.32, +1.58] | +13.3% | 34.2% | +31.0% / 22.7% | 5/7 | FAIL (bootstrap) |
| TD | trend + de-gear | 1.70 | 0.79 | [-0.26, +1.73] | +13.0% | 26.2% | +35.5% / 19.3% | 5/7 | FAIL (bootstrap) |
| TK | trend + carry | 1.71 | 0.78 | [-0.26, +1.67] | +15.0% | 34.4% | +40.9% / 22.3% | 5/7 | FAIL (bootstrap) |
| K | carry alone | 7.25 | 5.79 | [+4.41, +7.26] | +2.2% | 0.3% | +10.4% / 0.8% | 7/7 (2026 = -0.0%) | PASS (not the decision test) |
| BTC buy and hold | | 0.76 | 1.16 | | +53.9% | 53.0% | dev DD 76.6% | | |

By calendar year, net (2026 = Jan-Sep): TC 2020 +74.3, 2021 +67.7, 2022 -6.7, 2023 +33.3, 2024 +43.9, 2025 -5.7, 2026 -9.8.
P1 +83.3, +56.6, -9.9, +49.6, +36.5, -7.1, -11.6. K +9.3, +22.5, +0.5, +3.0, +5.0, +0.5, 0.0.
At 0.20%/side: TC dev 1.72 / holdout 0.69, P1 1.55 / 0.73, K 5.59 / 2.63; TC still fails the bootstrap bar (K still passes).

## Isolating the overlay value
Pre-registered claim rule: the overlay is worth claiming only if TC beats P1 on Sharpe in both windows. It does, narrowly:
dev 1.86 vs 1.58 (+0.28), holdout 0.84 vs 0.78 (+0.06). The holdout gap is far inside noise (the bootstrap interval is about 2 Sharpe wide).
Decomposed against P1, holdout / dev Sharpe:
- Spot execution instead of perps (P1 vs P0): +0.06 / +0.21. Real funding on the perp sleeve cost 13.4% of dev gross profit and 7.8% of holdout gross
  (sum of daily 13.96% / 4.97% against trend gross 104% / 63%). This is the one robust "funding-aware" gain, and it needs no overlay at all:
  a long-only book does not need a perp.
- De-gearing (TD vs P1): +0.01 / +0.12 Sharpe; holdout max DD 26.2% vs 34.4%, but CAGR 13.0% vs 14.8%. It cut risk and return about equally.
  Geared down on 54% of holdout days.
- Carry added to the trend sleeve (TK vs P1): +0.01 / +0.13 Sharpe, CAGR +0.2 pts holdout. Carry funding received in the holdout summed to 3.3% of
  equity over 3.75 years (mean notional 0.11), basis -0.2%, costs for the carry legs about 2.5%.
- Carry alone (K): funding +11.5% sum over the holdout, basis -0.04%, costs 3.2%, net total return +8.6% in 3.75 years at a mean notional of 0.26.
  The extreme Sharpe (5.8) is the model's, not a live expectation: see caveats.
- The funding-crowding avoidance asked for in the brief is realised by holding longs on spot (zero funding) and by excluding from carry any coin the trend leg is long.
  A filter that cuts longs on high funding was not tested here because `funding_rules` already found it removes the best trend coin-days.

## Predictions versus outcome
- P1 dev Sharpe 0.8-1.2: **wrong, 1.58** (2020-21 was a strong trend window). P1 holdout 0.5-0.8: right, 0.78. P0 below P1 by 0.1-0.2: dev 0.21, holdout 0.06.
- K "Sharpe much lower after 2021": **wrong in Sharpe (5.8 holdout)**, right in return: CAGR fell from 10.4% to 2.2% as funding normalised and the book shrank (mean notional 0.36 -> 0.26).
- DG |delta Sharpe| < 0.1: wrong in dev (+0.12), right in the holdout (+0.01).
- TC CAGR low single digits to ~8%: **wrong, higher** (holdout +14.0%, dev +39.7%) because the trend leg ran at 17-27% mean gross exposure.

## Honest caveats
- The holdout is not clean for the trend leg (2023-26 prices read by earlier studies, and I read `new_strategy_tsmom`'s results before writing this).
  The funding and basis series were unread by me. Survivorship in the 16 coins biases the trend leg upward. Dev 2020-22 is a strong-trend window.
- Carry is modelled with no liquidation or margin call on the short perp, no exchange or counterparty risk, no transfer between spot and futures accounts,
  open-to-open basis sampled once a day (intraday basis swings and wide-spread days are invisible), funding assumed settled to the perp at the rate read.
  Sharpe 5-7 on a delta-neutral book is the model's smoothness; treat it as an upper bound, not a forecast. A -1.5% to -2% stress on basis would not be visible here.
- The 20%/yr vol target on 16 coins gives a book with ~10-20% realised vol and 20-35% of equity in coins; the carry leg is capped at 0.5 notional. Both were fixed in advance, not tuned.
- Holdout bootstrap on ~3.75 years: Sharpe standard error is about 0.5. A "fail" on the lower bound is a power statement as much as a negative one.
- 2025 (-5.7%) and 2026 to September (-9.8%) are both losing years for TC; the sleeve is currently in a drawdown it has not recovered (26% holdout max DD).

## What this means
1. No pre-registered strategy here passes. The plain sleeve on spot (P1) has the same bootstrap failure; the overlay does not change the statistical verdict.
2. The robust, free improvement is operational: run the long-only sleeve on spot (or accept that perp funding costs 0.1-0.2 Sharpe). That alone is worth more than the overlays.
3. Funding carry is a real but small cash-like yield here (2-5%/yr of equity in 2023-25, ~0 in 2026 YTD); it does not raise the sleeve's holdout Sharpe meaningfully and costs 3-7 round trips a year.
4. De-gearing trades return for lower drawdown roughly one for one; it is a risk preference, not alpha.
5. To make this "really make money" one would have to lever the carry book or the trend sleeve, which brings exactly the margin and liquidation risks this simulation ignores. That is not supported by this evidence.

## Files
`PREREGISTRATION.md` (+ `.sha256`, `.sealed_at`), `PREREGISTRATION.addendum_1.md` (+ `.sha256`), `data/`, `data_sha256.txt`, `result_dev.json`,
`dev_run_at`, `code_at_unseal.sha256`, `code_sealed_at`, `result_holdout.json`, `holdout_read_at`.
Code: `scripts/trend_carry_overlay_study.py` (reuses `scripts/tsmom_study.py`), tests `tests/test_trend_carry_overlay_study.py` (16 tests, pass).

## Audit correction
The adversarial audit found the calendar-year count wrong: TC and P1 are 4 of 7 positive years
(2022, 2025 and 2026 down), not 5 of 7. The years bar still passes, so the verdict is unchanged.
The P0, TD and TK year counts in the table above were not re-checked by the audit. Re-derive them
from `result_holdout.json` before relying on them.
