# Pre-registration R2: funding-rate rules on USDT-M perps

Written 2026-10-17 (UTC), after the funding history was fetched and its coverage counted,
and BEFORE any return (price or funding P&L) of any rule below was computed. No funding
figure has yet been put beside a price move.

## Question
Is there a funding-rate rule, tradable with USDT-margined perpetuals only (no spot leg, so
no delta-neutral basis trade), that earns money after costs and funding out of sample?

Prior work (research/, same day) tested five slow daily price rules on the same Bitget
bars; none passed (trend L/S: dev Sharpe 0.66, holdout 0.24). That work read price data
through 2026-10-15, so **the price history in this study's holdout has been seen before**
(by price-only rules). The funding history has not been read against returns by anyone in
this project. Hypothesis F3 below is built on that earlier trend rule (H1a), whose holdout
result is known; F3's holdout therefore tests only whether the funding filter adds value
on top of a known base, and is reported against unfiltered H1a on the same dates.

## Data
- **Prices**: Bitget USDT-M perpetual daily bars (bars open 16:00 UTC), the files fetched on
  2026-10-17 for the earlier study and copied to `data/` (sha256 in `data_1D.sha256`). The
  last closed bar opened 2026-10-15 16:00 UTC.
- **Funding (proxy)**: Hyperliquid public `fundingHistory`, every settlement row, fetched
  2026-10-17 (`funding_raw/`, fetcher `fetch_funding.py`). Hyperliquid settled every 8 h
  until 2023-06-08 and hourly since; each row is the rate paid at that settlement.
  **This is a proxy.** The bot trades on Bitget; Bitget's public funding history starts
  only around July 2026 and OKX's covers ~3 months, so Hyperliquid funding stands in for
  Bitget's. The two venues' funding differ (level, timing, caps); results are about
  Hyperliquid-funding-as-signal and Hyperliquid-funding-as-cost, applied to Bitget prices.
- **Daily funding** for a coin and Bitget bar = the SUM of the settlement rates whose
  timestamp falls in that bar's span [open, open + 1 day). A settlement the venue did not
  publish (three 2-hour gaps in 2023–2024) is not invented: the day's sum is what was paid.
  A coin-day with NO rows is missing (NaN), never zero. A coin's first (partial) day of
  funding is dropped (NaN), as is every day before S for the primary universe; the last,
  still-open day is never used (its price bar is not closed).

### Funding coverage (Hyperliquid, fetched 2026-10-17; rows to 2026-10-17 06:00 UTC)
| coin | first row (UTC) | rows | coin-days | missing days | short days* | in primary universe |
|---|---|---|---|---|---|---|
| BTC | 2023-05-12 00:00 | 29,533 | 1,255 | 0 | 6 | yes |
| ETH | 2023-05-12 00:00 | 29,533 | 1,255 | 0 | 6 | yes |
| BNB | 2023-05-12 00:00 | 29,533 | 1,255 | 0 | 6 | yes |
| SOL | 2023-05-12 00:00 | 29,533 | 1,255 | 0 | 6 | yes |
| DOGE | 2023-05-12 00:00 | 29,533 | 1,255 | 0 | 6 | yes |
| AVAX | 2023-05-12 00:00 | 29,533 | 1,255 | 0 | 6 | yes |
| LTC | 2023-05-12 00:00 | 29,533 | 1,255 | 0 | 6 | yes |
| ATOM | 2023-05-12 00:00 | 29,533 | 1,255 | 0 | 6 | yes |
| LINK | 2023-05-18 08:00 | 29,514 | 1,249 | 0 | 6 | yes |
| XRP | 2023-06-18 00:00 | 29,212 | 1,218 | 0 | 5 | no (secondary only) |
| BCH | 2023-06-30 03:00 | 28,921 | 1,206 | 0 | 5 | no (secondary only) |
| TRX | 2023-08-06 16:00 | 28,021 | 1,168 | 0 | 3 | no (secondary only) |
| UNI | 2023-08-11 23:00 | 27,894 | 1,163 | 0 | 4 | no (secondary only) |
| DOT | 2023-09-12 12:00 | 27,138 | 1,132 | 0 | 3 | no (secondary only) |
| ADA | 2023-10-22 06:00 | 26,184 | 1,092 | 0 | 3 | no (secondary only) |
| NEAR | 2023-11-01 17:00 | 25,933 | 1,081 | 0 | 3 | no (secondary only) |
| FIL | 2023-11-14 04:00 | 25,634 | 1,069 | 0 | 3 | no (secondary only) |
| ETC | 2024-01-12 01:00 | 24,221 | 1,010 | 0 | 3 | no (secondary only) |
| XLM | 2024-11-23 03:00 | 16,636 | 694 | 0 | 2 | no (secondary only) |
| ALGO | 2024-12-04 13:00 | 16,362 | 683 | 0 | 2 | no (secondary only) |

*short day = fewer settlement rows than the day's spacing implies: the first and last
(partial) day, the 8 h → 1 h switch day (2023-06-08), and the three 2-hour settlement gaps
(2023-07-02, 2023-08-23, 2024-08-15). Unit check (levels only, no returns): BTC's median
hourly row is 0.0000125 (= 0.01% per 8 h), median daily sum 0.030%.
Raw files hashed in `funding_raw.sha256`; the daily matrix is `funding_daily.csv`
(built by `prep_funding.py`).

## Universe (fixed ex ante)
- **Primary universe (decides pass/fail), 9 coins**: the earlier study's 20-coin universe
  ∩ Hyperliquid coins with funding on every day from S = 2023-06-01:
  **BTC ETH BNB SOL DOGE AVAX LTC ATOM LINK**. Fixed now, from coverage alone.
  This is a small cross-section (F1 holds k = 2 coins per side). The other 11 coins' funding
  begins between 2023-06-18 and 2024-12-04.
- **Secondary universe (reported, never used to decide)**: all 20 coins, each entering a
  rule once its own funding signals exist (F7 from its own first full day of funding; F2/F3
  windows need ≥ 80 of 90 values), so the cross-section grows from 9 coins to 20 by early
  2025. n(t) then counts the coins eligible for the funding rule that day. Same dates,
  costs and code paths; read at the same time as the primary holdout.
- All 20 coins are listed on Hyperliquid (meta checked 2026-10-17; none delisted).
  Survivorship: the earlier universe already excluded LUNA and MATIC; Hyperliquid lists only
  coins that were still worth listing in 2023–2024. Stated, not corrected.

## Dates
- Funding start S = 2023-06-01: the first full calendar month of Hyperliquid funding
  coverage (first rows 2023-05-12). Funding rows before S are not used. The first Bitget
  bar whose funding is used opens 2023-06-01 16:00 UTC.
- Warm-up: F2's z-score and F3's decile need a 90-day window of F7 values (≥ 80 present),
  so they cannot form before late August 2023. Counting forced-flat warm-up days as zero-return days
  would score "could not trade" as "earned nothing", so performance is measured from
  **E = 2023-10-01**, the first month start after the longest warm-up. All rules and
  benchmarks are measured over the same dates.
- **Development**: bars opening 2023-10-01 → 2025-03-31 (18 months).
- **Holdout**: bars opening 2025-04-01 → the last bar with a booked return (opens
  2026-10-14 16:00, earning to the 2026-10-15 16:00 open) (≈ 18.5 months).
- A return is dated by the bar it is earned in (open of bar k → open of bar k+1, 16:00 →
  16:00 UTC), so the last development return ends 2025-04-01 16:00.
- The development run is made on data truncated after the first holdout bar's open, so no
  holdout price or funding can reach it. The holdout is read ONCE, after the code is sealed
  (`code_at_unseal.sha256`). The holdout run must reproduce the development numbers exactly
  from the untruncated data (a look-ahead check); if it does not, that is reported.

## Timing and costs
- Signal on data up to the close of bar t (funding rows settled before that close).
  Position filled at the open of bar t+1, earning open(t+1) → open(t+2).
- Trading cost: 0.10% per side on traded notional (|Δweight|), charged at each fill.
  Weights are held constant between trades; the drift back to target weight is not
  charged (as in the earlier study).
- Funding: each position pays or receives the actual proxy funding of the bar it is held
  in, on signed notional: funding P&L = −weight × daily funding sum. Longs pay positive
  funding, shorts receive it (and the reverse when funding is negative). A held coin-day
  with missing funding is charged the coin's trailing 7-day mean daily funding, and every
  such fill is counted and reported.
- Every rule is also reported net of trading costs but WITHOUT funding (price-only),
  labelled as such. The pass rule reads the version with funding.

## Shared definitions
- f_d(j, t): coin j's daily funding sum for bar t.
- F7(j, t): mean of f_d(j, t−6 … t), requiring at least 5 of the 7 days present (else NaN).
- vol(j, t): standard deviation of the last 30 daily close-to-close returns × √365.
- n(t): number of universe coins eligible on bar t: at least 150 days of price history
  (as H1) and, in the secondary universe only, funding signals available.

## Hypotheses (three primary tests: F1, F2, F3)
- **F1 cross-sectional funding carry, weekly.** Every 7 days (first rebalance on the first
  bar with F7 for ≥ 8 coins; then every 7th bar) rank the coins with F7, vol and a price by
  F7. k = ⌊count/4⌋. SHORT the k with the highest F7, LONG the k with the lowest (stable
  sort on F7). Within each side weight ∝ 1/vol; longs sum to +0.5, shorts to −0.5 (gross
  1×, dollar-neutral). Hold to the next rebalance.
- **F2 crowded-funding contrarian, time series.** z(j, t) = (F7(j,t) − mean) / sd, mean and
  sd (ddof 1) of F7(j, t−89 … t), requiring ≥ 80 values. A coin not in a holding period is
  evaluated at each close: z > +2 → short; z < −2 → long; else flat. A triggered position is
  held for 7 bars (signal bars t … t+6) at a size frozen at entry, then re-evaluated at
  t+7 (a renewed signal keeps the position, re-sized). Size = (0.20 / n(t)) / vol(j, t),
  capped at 1× per coin (as H1).
- **F3 funding filter on the earlier trend rule H1a.** H1a's target weight exactly as
  pre-registered before (signal = mean of sign of 20/60/120-day returns; size
  (0.20 / n) / vol, cap 1×), then: a LONG target is set to 0 when F7(j, t) is at or above the
  90th percentile of F7(j, t−89 … t); a SHORT target is set to 0 when F7(j, t) is at or below
  the 10th percentile of that window (≥ 80 values required; without them the coin is flat).
  Then the same 10% trade band as H1a.

Not hypotheses (reported for reference, never pass/fail): unfiltered H1a on the same dates
and with the same proxy funding; F1 price-only; every rule without funding.

## Report, per rule
Development and holdout: Sharpe (daily returns, √365), CAGR, annualised vol, max drawdown;
return by calendar year; turnover (Σ|Δw| per year); gross P&L, trading costs and funding
as sums of daily returns, and costs and funding each as a share of gross; the number of
coin-days on missing funding. 90% block-bootstrap interval of the full-period (E → end)
Sharpe: 20-day blocks, 2,000 draws, seed 7 (the earlier study's `boot_sharpe`).
Benchmarks over the same dates: BTC buy-and-hold and equal-weight buy-and-hold of the
universe, both price-only (no funding, as a spot holder), plus as perp longs paying the
proxy funding (reference).

## Pass rule (fixed now)
A rule PASSES only if all four hold, net of trading costs AND proxy funding:
1. development Sharpe ≥ 0.5;
2. holdout Sharpe ≥ 0.5;
3. the 90% block-bootstrap interval of the full-period Sharpe lies entirely above 0;
4. holdout max drawdown no worse (not larger) than price-only BTC buy-and-hold's over the
   same holdout dates.
Three tests here (F1, F2, F3); eight slow-rule tests in this line of work counting the
earlier five. Any pass is reported with those counts beside it. For F3 a pass is reported
as "the filtered trend rule passes"; the claim "the funding filter adds value" additionally
needs F3's holdout Sharpe above unfiltered H1a's on the same dates, and is reported
separately. A rule that fails is reported as failing, with its numbers. No parameter is
changed after a result is seen; any change is a timestamped addendum below, written before
the run it affects.
