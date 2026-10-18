# Research 4 — the daily trend sleeve against real perpetual funding (read 2026-10-17)

Pre-registration: `PREREGISTRATION.md`, sha256 `93450568c5a67c85…` sealed 2026-10-17T15:01:29Z,
before any funding series was read. Evaluator `evaluate_funding.py` (sha256 in `evaluate.sha256`),
built on research3's sealed `rules_used.py`; result in `result.json`.

## Data as read
- Prices: Kraken spot daily bars, 16 coins (research3's set), unchanged.
- Funding: Binance USDT-perp `DERIVATIVES_FUNDING_RATE_CURRENT` from CoinAPI, 8-hour windows, the
  window's `last` sample read as the settled rate. 20 coins pulled (the 16 plus AVAX BNB NEAR SOL for
  later), 2021-01-01 → 2026-10-17; ~1,000 request-credits of the $25.
- Coverage inside the window (2,007 days per coin): a day with at least one settlement read on
  1,480–1,723 days per coin; all three settlements on 1,037–1,439. A held coin-day with no sample is
  **charged nothing in run (c) and counted** (2,795 such coin-days, 24% of the 11,489 held); run (d)
  fills them (a day with one or two settlements scaled to three; a day with none charged the coin's
  mean over its read held days). (c) is a lower bound on the funding paid, (d) is the bound the other way.

## The runs (same weights every time; window 2021-01-01 → 2026-06-30)

| run | Sharpe | CAGR | max DD | funding paid | funding / gross profit | funding per yr of long notional | Sharpe on research3's holdout window (2021-06 →) | CAGR there |
|---|---|---|---|---|---|---|---|---|
| (a) no funding | 1.03 | +8.0% | 11.4% | +0.0% | +0.0% | 0.0% | 0.63 | +4.3% |
| (b) fixed 0.03%/day | 0.92 | +7.0% | 12.5% | +4.9% | +10.4% | 11.0% | 0.51 | +3.4% |
| (c) real funding, as read | 0.88 | +6.7% | 12.0% | +6.9% | +14.5% | 15.3% | 0.54 | +3.6% |
| (d) real funding, gaps filled | 0.83 | +6.3% | 12.8% | +9.0% | +18.9% | 20.0% | 0.49 | +3.2% |
| BTC buy-and-hold | 0.51 | +13.7% | 76.7% | | | | | |

Gross profit over the window (sum of daily gross returns): +47.6%. Mean gross
long exposure: **8.2% of equity** — the sleeve is small by construction (20%/yr vol target split
across the eligible coins, each divided by its own 60–100%/yr vol).

By calendar year (net):

| year | (a) | (b) | (c) | (d) |
|---|---|---|---|---|
| 2021 | +21.9% | +20.8% | +16.6% | +16.2% |
| 2022 | -4.5% | -4.7% | -4.4% | -4.5% |
| 2023 | +7.8% | +6.2% | +7.0% | +6.4% |
| 2024 | +24.0% | +22.5% | +21.9% | +21.4% |
| 2025 | -1.2% | -2.0% | -1.4% | -1.8% |
| 2026 | +3.5% | +2.9% | +3.6% | +3.4% |

## The conditional reading: what the sleeve pays when it is long

| coin | mean funding, all days | mean funding, days held | ratio | held / all coin-days | days with ≥1 / 3 settlements / window |
|---|---|---|---|---|---|
| ADA | 0.0300% | 0.0777% | 2.59 | 605 / 1668 | 1668 / 1233 / 2007 |
| ALGO | 0.0322% | 0.0881% | 2.73 | 536 / 1480 | 1480 / 1037 / 2007 |
| ATOM | 0.0155% | 0.0747% | 4.81 | 648 / 1720 | 1720 / 1411 / 2007 |
| BCH | 0.0024% | 0.0224% | 9.26 | 867 / 1711 | 1711 / 1427 / 2007 |
| BTC | 0.0288% | 0.0492% | 1.71 | 867 / 1695 | 1695 / 1415 / 2007 |
| DOGE | 0.0341% | 0.0709% | 2.08 | 703 / 1680 | 1680 / 1313 / 2007 |
| DOT | 0.0164% | 0.0840% | 5.13 | 536 / 1722 | 1722 / 1397 / 2007 |
| ETC | 0.0206% | 0.0541% | 2.63 | 648 / 1680 | 1680 / 1273 / 2007 |
| ETH | 0.0318% | 0.0586% | 1.85 | 854 / 1723 | 1723 / 1439 / 2007 |
| FIL | 0.0327% | 0.0661% | 2.02 | 442 / 1568 | 1568 / 1076 / 2007 |
| LINK | 0.0393% | 0.0753% | 1.92 | 708 / 1564 | 1564 / 1154 / 2007 |
| LTC | 0.0359% | 0.0720% | 2.01 | 735 / 1652 | 1652 / 1303 / 2007 |
| TRX | -0.0010% | 0.0121% | -11.58 | 1209 / 1723 | 1723 / 1358 / 2007 |
| UNI | 0.0390% | 0.0580% | 1.49 | 563 / 1575 | 1575 / 1115 / 2007 |
| XLM | 0.0323% | 0.0791% | 2.45 | 687 / 1613 | 1613 / 1275 / 2007 |
| XRP | 0.0357% | 0.0622% | 1.74 | 881 / 1717 | 1717 / 1306 / 2007 |
| **pooled** | 0.0263%/day | 0.0589%/day | **2.24** | 11489 / 26491 | |

Pooled: funding on the coin-days the sleeve held averaged **0.0589%/day
(21.5%/yr)** against 0.0263%/day
(9.6%/yr) over all coin-days: the trend-following long book is long exactly
when perps trade rich, by a factor of 2.24. Exposure-weighted (BTC and ETH carry the largest
weights and the lowest funding) the book paid 15.3%/yr of its long notional as
read, 20.0%/yr with the gaps filled, against the
Bitget study's fixed 10.9%/yr. (TRX's negative ratio is a negative all-days mean: TRX perps
traded cheap on average and dear when held.)

## Verdict against the pre-registered rule (run (c))
- Sharpe ≥ 0.5: **0.88** — pass
- funding ≤ 40% of gross profit: **14.5%** (18.9% gaps filled) — pass
- max drawdown ≤ half of BTC buy-and-hold's: **12.0%** vs half of 76.7% = 38.3% — pass

**Pass, on the window pre-registered.** Said plainly beside it: on research3's stricter holdout window
(2021-06-01 → 2026-06-30, which drops the strong first five months of 2021) the same runs read
Sharpe 0.54 as read and
0.49 with the gaps filled — on the bar, not
over it. The sleeve stays on the list while the twelve-month Bitget record accrues; it earns nothing more
than that from this reading.

## Prediction versus outcome
- "The adverse-selection ratio will be above 1, 1.3–2×": **above, and higher: 2.24×.**
- "The weighted charge lands near 0.015–0.02%/day, so (c) sits between (a) and (b)": **wrong in
  direction.** The book pays 0.042%/day of its long notional as read
  (0.055% gaps filled), MORE than the fixed 0.03%/day, so (c) sits below (b).
  The sleeve survives it only because its exposure is 8% of equity: funding is 14–19% of
  gross profit, not the 20–40% predicted.
- "Sharpe 0.3–0.6, coin flip": 0.88 on the window, 0.54/0.49 on the holdout window. The window choice
  decides the headline; the funding ratio does not depend on it.

## What this changes
1. The fixed 0.03%/day the Bitget study charged was not conservative for a trend-following long book;
   a long book pays about twice the unconditional rate. Any future funding assumption for a long trend
   rule should be the conditional figure (≈0.06%/day on held coin-days, ≈15–20%/yr of long notional),
   or real funding.
2. The Bitget paper record's pass rule (funding ≤ 40% of gross) stands; this reading says the sleeve
   would have met it at ~15–19% on Binance's funding over 2021–26.
3. Biases, as pre-registered: Binance's funding, not Bitget's; Kraken spot prices with a perp's funding
   and without the perp's basis gain (conservative); survivorship in the 16; the window's prices were
   read before (not a clean holdout).
