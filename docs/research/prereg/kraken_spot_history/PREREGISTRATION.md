# Pre-registration — Kraken spot long history, written 2026-10-17 before the archive was read

Question: do the five slow daily rules already pre-registered on Bitget perps
(research/PREREGISTRATION.md, 2021-06 → 2026-10: none passed, H1a trend L/S came
nearest at dev 0.66 / holdout 0.24) hold on the EARLIER, unread history? The
Bitget study's weakness was power: an 18-month to 3-year Sharpe has a standard
error near 0.8. Kraken's downloadable OHLCVT archive (spot, every pair from its
first trade to 2026-06-30) gives daily bars from 2013 for BTC and from 2017–18
for most majors.

## Data
- Kraken spot OHLCVT daily (1440-minute) bars, USD quote, from the archive
  `Kraken_OHLCVT_Full.zip` (sha256 bc273e90…e7b1c7), converted to the
  `research/rules.py` shape without any other change. Bars are 00:00 UTC.
- Universe, fixed by rule before the listing is read: the coins of the Bitget
  study's 20 (BTC ETH BNB XRP ADA SOL DOGE DOT AVAX LTC LINK TRX BCH ATOM UNI ETC
  FIL NEAR XLM ALGO) that have a USD pair in the archive with at least 150 daily
  bars before 2021-06-01. A coin enters a rule after 150 days of its own history,
  as before. Coins without a Kraken USD pair are absent and said so (BNB is
  expected to be).
- Known biases, stated not corrected: survivorship (the 20 were chosen by 2022
  market cap); spot, not perps (no funding; no shorting cost modelled below the
  trading cost, which flatters the L/S rules in 2013–2017 when borrowing was
  expensive or impossible).

## Costs
- 0.10% per side on traded notional, as in the Bitget study, so the two are
  comparable; no funding (spot). Signals at the close of day t, fills at the open
  of t+1.

## Hypotheses (the same five, no parameter changed)
H1a trend L/S, H1b trend long-only, H2a Donchian 55/20 L/S, H2b long-only, H3
weekly cross-sectional 28-day momentum, exactly as `research/rules.py` runs them.

## Windows
- Development (the new, unread history): first day with at least two eligible
  coins → 2021-05-31.
- Holdout: 2021-06-01 → 2026-06-30. This window's PRICES have already been read
  in the Bitget study (different venue, same coins, same rules), so it is not a
  clean holdout: it is a replication on another venue's prints. Said so in the
  report; the decision weight is on the development window and on the full-period
  bootstrap.

## Pass rule (fixed now)
As before, net of costs: development Sharpe ≥ 0.5 AND holdout Sharpe ≥ 0.5;
positive return in at least 6 of the calendar years 2014–2026 the rule traded;
90% block-bootstrap interval (20-day blocks, 2,000 draws) of the full-period
Sharpe above 0; holdout max drawdown no worse than BTC buy-and-hold's over the
holdout. Five tests; reported with that count.

## Prediction, written now
H1a (trend L/S) is the one most likely to pass: the 2013–2021 window holds two
full trend cycles (2017, 2020–21). If it fails there too, the daily trend sleeve
is dropped from the list; if it passes on development and fails the holdout
again, that is the regime change the Bitget study saw, and the sleeve stays a
paper candidate only.
