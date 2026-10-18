# Pre-registration — the daily trend sleeve against REAL perpetual funding, written 2026-10-17 before the funding series were read

Question: the long-only daily trend rule (H1b) passed its bar on Kraken spot
(research3: development 2.12, holdout 2021-06→2026-06 0.63). On Bitget perps
with a FIXED 0.03%/day funding charge on the long book the same rule scored
about zero over the same years (research, 17 Oct). The fixed charge was an
assumption. This study replaces it with the funding a long book actually paid,
and measures the one thing the assumption cannot: whether the sleeve is long
exactly when funding is dearest (a trend-following long book is, by
construction, long after prices have risen, which is when perps trade rich).

## Data
- Prices: the Kraken spot daily bars of research3 (`research3/data/*_1D.csv`,
  archive sha256 bc273e90…e7b1c7), unchanged. 16 coins.
- Funding: Binance USDT-margined perpetuals, metric
  `DERIVATIVES_FUNDING_RATE_CURRENT` from CoinAPI, sampled per 8-hour window
  (`period_id=8HRS`), pulled 2026-10-17 into `coinapi/raw/funding8h_<COIN>.json`.
  The rate SETTLED at a window's end is read as the window's `last` sample (the
  rate current just before settlement; Binance settles at 00:00, 08:00, 16:00
  UTC). A window with no sample is a missing settlement: it is counted and
  reported, never read as zero. Binance, not Bitget, because CoinAPI's Bitget
  futures history starts 2023-06; the two venues' funding on the majors track
  each other closely, and the bias (Bitget's is what the sleeve would pay) is
  stated, not corrected.
- Venue mix, stated: prices from Kraken spot, funding from Binance perps. The
  sleeve's price leg is the spot trend; its cost leg is a perp's. A perp's price
  leg differs from spot by the basis, which funding is the payment for, so
  reading spot prices with perp funding charges the cost without the matching
  basis gain. That flatters nothing: it is the conservative reading.

## Window
2021-01-01 → 2026-06-30, the overlap of the funding series and the Kraken
archive. These PRICES have been read before (the research3 holdout and the
Bitget study). This is not a clean holdout; it is the same window re-costed.
The decision weight is on the funding share and the conditional funding
reading below, which no earlier study measured.

## Rule and costs
H1b exactly as `research3/rules_used.py` runs it (lookbacks 20/60/120, 30-day
vol, 20%/yr target split across eligible coins, cap 1×, band 10%, 150 days of
history before a coin is eligible). 0.10% per side on traded notional. Funding
per day on coin j = the sum of the day's settled rates, charged on the long
weight held through that day (positive rate = the long pays). Three runs of
the same weights: (a) no funding, (b) the fixed 0.03%/day of the Bitget study,
(c) real funding.

## Measured
Over the window, for (a), (b), (c): Sharpe, CAGR, max drawdown, funding paid
as a share of gross, return by calendar year; BTC buy-and-hold's drawdown over
the same days. And the conditional reading: the mean daily funding rate on
coin-days the sleeve HELD versus all coin-days in the window, per coin and
pooled — the adverse-selection ratio.

## Pass rule (fixed now)
Run (c), net of costs and real funding: Sharpe ≥ 0.5 AND funding paid ≤ 40%
of gross AND max drawdown ≤ half of BTC buy-and-hold's over the window — the
three criteria `docs/research/daily_trend_sleeve_prereg.md` fixed for the
Bitget paper record, applied to history. One test. A failure is reported with
its numbers. A pass does NOT replace the twelve-month Bitget record (different
venue, prices already read); it decides whether the sleeve stays on the list
while that record accrues.

## Prediction, written now
The adverse-selection ratio will be above 1: funding on held coin-days will
average 1.3–2× the unconditional mean, because the rule is long after rises.
Realised funding over 2021–2026 on the majors averaged well under the 0.03%/day
assumption in the bear years and above it in early 2021 and late 2024, so the
weighted charge the sleeve pays lands near 0.015–0.02%/day of long notional:
run (c) sits between (a) and (b), Sharpe 0.3–0.6, funding 20–40% of gross. The
pass is a coin flip; the ratio is the finding either way.
