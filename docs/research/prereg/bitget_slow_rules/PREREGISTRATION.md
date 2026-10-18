# Pre-registration — written 2026-10-17, before any result below was computed

Question: is there a rule, on data the bot already trades, that makes money after costs
out of sample? Earlier work (strategy review, 10–11 Oct) found no edge in the 1h entries
on ~4 months of 1h bars, which have all been read many times. This study moves to longer,
unread history and slower rules, and fixes every parameter here before the first run.

## Data
- Bitget USDT-M perpetuals, closed bars, 2021-06-01 → last closed bar (fetched 2026-10-17).
- Universe, chosen ex ante: the 20 largest non-stablecoin coins by market cap in January
  2022 that Bitget still lists: BTC ETH BNB XRP ADA SOL DOGE DOT AVAX LTC LINK TRX BCH ATOM
  UNI ETC FIL NEAR XLM ALGO. Known survivorship gaps: LUNA (collapsed May 2022, no data),
  MATIC (renamed). Results are biased upward by these exclusions; stated, not corrected.
- A coin enters a rule only after 150 days of its own history.

## Costs (applied to every rule)
- 0.10% per side on traded notional (Bitget taker 0.06% + 0.04% slippage).
- Funding: longs pay 0.01% per 8h (0.03%/day) while held; shorts receive nothing.
  (Conservative: Bitget's public funding history only starts July 2026.) Every rule is
  also reported without funding, labelled as such.
- Signals on the close of bar t, fills at the open of bar t+1.

## Hypotheses (primary: H1a, H1b, H2a, H2b, H3 — five tests)
- **H1 time-series trend, daily.** Per coin, signal = mean of sign(return over 20, 60, 120
  days) ∈ [−1, 1]. Position = signal × (target vol / 30-day realised vol), per-coin cap 1×
  notional, portfolio target 20%/yr vol split equally across the coins in the universe
  that day. Rebalance daily; trade only when the target moves by more than 10% of the
  current position (cost band). H1a long/short; H1b long-only (negative signal → flat).
- **H2 Donchian breakout, daily.** Enter long on a close above the prior 55-day high, short
  on a close below the prior 55-day low; exit a long on a close below the prior 20-day
  low, a short on a close above the prior 20-day high. Size each position to 20%/yr vol /
  n coins as in H1 (sized at entry, not rebalanced). H2a long/short; H2b long-only.
- **H3 cross-sectional momentum, weekly.** Every 7 days rank the eligible coins by 28-day
  return; long the top 4, short the bottom 4, each sized to equal vol, gross 1×. One variant.

Secondary (reported, not used to decide): H1 and H2 on 4h bars with every lookback × 6.

Separate question (H4, discovery only): on the bot's own 1h backtest positions (frozen
datasets, already read), does a position whose direction agrees with its coin's daily H1
signal at entry earn more than one that disagrees?

## Evaluation
- Development: 2021-06-01 → 2024-12-31. Holdout: 2025-01-01 → end. Nothing is tuned on
  either; the holdout is read once, after the code is final.
- Report per rule: annual return, vol, Sharpe (daily returns, √365), max drawdown, return
  by calendar year, turnover, costs paid as a share of gross, trades.
- Benchmarks: BTC buy-and-hold; equal-weight buy-and-hold of the universe.
- 90% block-bootstrap interval (20-day blocks, 2,000 draws) on the full-period Sharpe.

## Pass rule (fixed now)
A rule passes only if ALL hold, net of costs and funding:
1. development Sharpe ≥ 0.5 and holdout Sharpe ≥ 0.5;
2. positive return in at least 4 of the 6 calendar years 2021 (Jun–Dec) to 2026 (Jan–Oct);
3. the bootstrap 90% interval of the full-period Sharpe is above 0;
4. holdout max drawdown no worse than BTC buy-and-hold's over the same holdout.
Five primary tests: a pass is reported with that count beside it. A rule that fails is
reported as failing, with its numbers.

## Addendum A — written 2026-10-17T05:43:11Z, after the v3 datasets were read, before the v2 dumps finished
On majors_1h_v3 + alts_1h_v3 (one window, discovery), the bot's backtest positions whose
direction opposed BTC's daily H1 signal (sign of the mean of 20/60/120-day return signs at
the last daily close before entry) lost on average; those agreeing did better, in both
directions. Prediction for majors_1h_v2 + alts_1h_v2 (a different window), fixed now:
1. the mean net return per position of the trades the BTC filter DROPS is below zero;
2. it is below the mean of the trades it KEEPS.
Both must hold for the filter to count as replicated. Anything else is a failure to replicate.

## Addendum B — written 2026-10-17T05:43:54Z, still before the v2 dumps finished
Further cuts seen on the same v3 window, with the predictions they make for v2:
3. regime_trend positions earn more per position than momentum_confluence and volume_spike
   positions combined;
4. scalp + intraday positions have a negative mean net return;
5. positions whose last exit is within 3 hours of entry have a lower mean than the rest.
Confidence buckets showed no order on v3; no prediction is made for them.

## Addendum C — every indicator, written 2026-10-17T05:47:18Z, before any signal-study result
Asked: measure every signal the bot has (candles incl. doji, Elliott, VWAP, POC, all voters).
- Harness: signal_study.py drives the bot's own Analyzer.analyze() at every 4th closed 1h
  bar (100-bar window, 4h/1d resampled as in the backtest, LLM off). Records each
  confluence voter's signed vote, each named candlestick pattern, and the analyzer's idea.
  Order-flow, on-chain and live-sentiment inputs are absent here, so those voters are not
  measured (reported as such).
- Event: a voter with vote != 0 (direction = sign); a named candle pattern marked bullish
  or bearish; the analyzer's idea (direction). Neutral patterns (e.g. plain doji) are
  measured as "what follows" without a direction claim.
- Outcome: forward return from the next bar's open over h = 4 and h = 24 bars, signed by
  the event's direction, both raw and market-adjusted (minus the mean forward return of
  all the dataset's coins at the same bar).
- Discovery: majors_1h_v2 (10 coins, Feb 2025 – Jul 2026). Replication: alts_1h_v2
  (8 other coins, same months) + majors_1h_v3 bars after 2026-07-06 (later months).
- Inference: block bootstrap over time (48-bar blocks), p for mean > 0. Benjamini–Hochberg
  at q = 0.10 across every (event, horizon) in discovery.
- A signal "carries information" only if: discovery BH-significant (market-adjusted), AND
  replication same sign with one-sided p < 0.05. It is "tradable on its own" only if its
  raw signed mean also exceeds 0.22% (one round trip of the bot's modelled costs).
