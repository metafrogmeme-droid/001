# R3: the five slow rules on Kraken spot, 2014–2026 — report, 2026-10-17

**Verdict: one of five passes.** Trend long-only (H1b) clears every pre-registered
criterion: development Sharpe +2.12 (2014-04 → 2021-05, unread
history), holdout Sharpe +0.63 (2021-06 → 2026-06), positive in
8 of 13 calendar years traded, full-period Sharpe +1.60 with a 90%
block-bootstrap interval of [+1.05, +2.15], holdout drawdown
10.1% against BTC's 76.7%. Five tests here; thirteen in this line
of work; this is the first pass.

## Protocol
- Pre-registration sealed 2026-10-17T07:36:30Z, sha256
  `b54e4e859357ecc5b45c7e40da62d244a7bace8abceeac3df10dafabff26587a`, before the
  archive was readable. Universe fixed by rule (a USD pair with ≥150 daily bars
  before 2021-06-01): 16 of the 20; AVAX, BNB, NEAR and SOL have no such history.
- Data: `Kraken_OHLCVT_Full.zip`, sha256 `bc273e90…e7b1c7`, every part verified
  against Kraken's checksums; daily USD bars converted without change
  (`convert.py`, `data_1D.sha256`).
- Development run, then code sealed (`code_at_unseal.sha256`, 07:41:58Z), then the
  holdout read once (`holdout_read_at`). No parameter changed; the rules are
  `research/rules.py` byte for byte (`rules_used.py`).

## Pass table
| rule | dev Sharpe | holdout Sharpe | years up / traded | 90% bootstrap (full) | holdout DD | verdict |
|---|---|---|---|---|---|---|
| H1a trend L/S | +1.46 ✓ | +0.29 ✗ | 12/13 ✓ | [+0.52, +1.54] ✓ | 18.8% ✓ | **FAIL** |
| H1b trend long-only | +2.12 ✓ | +0.63 ✓ | 8/13 ✓ | [+1.05, +2.15] ✓ | 10.1% ✓ | **PASS** |
| H2a breakout L/S | +1.38 ✓ | +0.15 ✗ | 9/13 ✓ | [+0.42, +1.43] ✓ | 20.8% ✓ | **FAIL** |
| H2b breakout long-only | +1.73 ✓ | +0.42 ✗ | 8/12 ✓ | [+0.75, +1.80] ✓ | 14.6% ✓ | **FAIL** |
| H3 xs momentum | +0.49 ✗ | -0.46 ✗ | 3/8 ✗ | [-0.48, +0.53] ✗ | 57.3% ✓ | **FAIL** |

## Return by calendar year, net (trend long-only)
2014:-1.8% 2015:+12.2% 2016:+27.6% 2017:+95.7% 2018:-6.5% 2019:+13.7% 2020:+25.0% 2021:+21.9% 2022:-4.5% 2023:+7.8% 2024:+24.0% 2025:-1.2% 2026:-0.7%

## How to read it
- **The development window is the new information.** 2014–2021 had never been
  read by this project. All four trend and breakout rules earned Sharpe 1.4–2.1
  there, through 2017 and 2018 alike. Cross-sectional momentum did not (0.49) and
  lost in the holdout (−0.46); it is dropped.
- **The holdout is a replication on another venue's prints, not a clean holdout.**
  The same coins and years were read in the Bitget study. There, H1b scored dev
  0.89 / holdout −0.02 *with* a fixed 0.03%/day funding charge on long exposure and
  with SOL, AVAX, NEAR and BNB in the universe; here, on spot with no funding and
  without those four, it scores 0.63. The funding assumption is the likely swing:
  0.03%/day is about 11% a year on a full long book, and the study flagged it as
  conservative. Measuring the rule against Bitget's real funding (public history
  from July 2026; CoinAPI once credited) is the next pre-registered test.
- **What H1b is and is not.** Holdout CAGR +4.3% with a 10.1% drawdown; 2025
  and 2026 to date are slightly negative. It preserves capital in bad years (2018
  −6.5%, 2022 −4.5% while BTC lost 74% and 64%) and compounds in trend years. It is
  a low-turnover sleeve (about 7 rebalances a year per coin), not a replacement
  for the hourly bot, and it earns nothing like the hourly bot's advertised
  frequency.
- **Caveats.** Spot shorting was not generally possible in 2014–17, so the L/S
  rows flatter; the long-only rows are the honest ones. 2014–2016 hold two to
  three coins. Survivorship: the 20 were chosen by 2022 market cap. Costs 0.10%
  per side; no slippage model beyond that. Thirteen tests in this line.

## Recommendation
Run H1b as a paper sleeve on Bitget perps from today, at the pre-registered sizing
(20%/yr target vol, equal split), recording real funding paid, and pre-register the
Bitget-funding replication before reading its first month. Nothing here changes
the verdict on the hourly indicators.

## Files
`PREREGISTRATION.md` (+ `.sha256`, `.sealed_at`), `convert.py`, `evaluate_k.py`,
`rules_used.py`, `data/` (16 coins) and `data_excluded/`, `data_1D.sha256`,
`result_dev.json`, `code_at_unseal.sha256`, `code_sealed_at`,
`result_holdout.json`, `holdout_read_at`. Archive: `../kraken/csv/` (890 MB,
all USD pairs at 60/240/1440 min) and `../kraken/listing.txt`.
