# Do the bot's calls make money on data it has never seen? Stage 2 pre-registration (2026-10-30)

Written and sealed before either dataset below is frozen and before any run on it.

## Why

Stage 1 (`docs/research/prereg/signal_edge_2026-10/REPORT.md`) found the bot's calls positive
before costs in both halves of both tuned sets, with costs about three times the discovery-half
edge. The calibration (`CALIBRATION.md` there) found no confidence level positive on both
universes; 0.80 was the only level positive in both majors halves. Every number so far is on
data the defaults were tuned on. This test asks the question on data they were not.

## Data (frozen after this seal; hashes committed before any run)

- **F1, Bitget fresh:** `benchmark/majors_1h_fresh`, the 10 majors of `benchmark/majors_1h_tf`
  (dataset d12d70e0…) keeping only bars after `benchmark/majors_1h`'s last bar
  (2026-07-04T14:00Z), so none of it is in the tuned set.
- **F2, Kraken history:** `benchmark/kraken_ethsol_1h`, the user-supplied Kraken spot 60-minute
  files: ETHUSDT from 2021-01-01, SOLUSDT from 2024-01-01 (earlier years are thin: 2019 has 158
  bars, ETH 2020 a median of about 6,000 USDT an hour, SOL starts at a 1.0 listing print), both
  ending 2025-10-26T23:00Z, before `benchmark/majors_1h` begins. Bars kept as supplied; a missing
  hour stays missing (listed in the manifest), none is invented.

## Arms

`python -m bot.backtest.runner --dataset <ds> --timeframe 1h --honest --walk-forward 6
--breaker-reset-bars 24 --trades-out <file>`, with default `--honest` costs (taker 0.06% +
slippage 0.05% per side) and:
- **A0:** no other setting;
- **A80:** `--confidence-threshold 0.80`.
Plus, deciding the gross question only: A0 with `--commission 0 --slippage 0`.

## Measure

Per OOS position, `net_pnl_pct` from `--trades-out`; None rows left out and counted. All six
folds pooled: the whole set is out of sample for this test.

## Decision rule

An arm **makes money** when, on F1 and on F2 separately:
- the pooled mean net return per position is above 0, and
- the lower end of a one-sided 98.75% interval (two arms, Bonferroni over a 97.5% one-sided
  bar) from a block bootstrap over ISO calendar weeks of entry time (10,000 draws, seed 7) is
  above 0.

The calls **carry information out of sample** when the zero-cost A0 pooled mean per position is
above 0 on both F1 and F2 (a screen, no interval).

An arm that makes money goes to a paper forward test before any change to live trading. If
neither does, the published calls are not presented as trades that pay, whatever the gross
reading.

## Reported, deciding nothing

Per fold returns, trade counts, profit factor and win rate per arm and dataset; F2 per coin.

## Limits

F2 is spot on one venue with the bot's perp cost model, not Kraken's fees (Kraken's taker fee
is higher, so F2 flatters the net). F1 is about four months. ETH sits in both the tuned majors
and F2 (different years). Two coins on F2.
