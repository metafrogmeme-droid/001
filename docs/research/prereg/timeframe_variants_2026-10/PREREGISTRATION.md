# Timeframe variants: scalps on finer bars, swing stops on 4h, the daily trend. Pre-registration (2026-10-31)

Written and sealed before any dataset below is fetched.

## Why

The bot decides every trade on 1-hour bars. A descriptive read of stage 2's out-of-sample
positions (`docs/research/prereg/signal_edge_stage2_2026-10/runs/`, 814 positions, net):
- 86% are labelled `swing` (allowed 48 h), yet the median one is held 3 h and 77% end at the
  stop: a 1-hour-ATR stop is a short leash for a multi-day idea.
- `scalp` positions get two hourly bars before the 2-hour time stop closes 62% of them at a loss.
Those readings come from the stage 2 data, so they are tested here only on data that was in
no earlier test, with the variants fixed below. No variant is changed after a result is seen.

## Data (frozen after this seal; hashes committed before any run)

**The universe, by rule:** the 10 Bitget USDT-M perpetuals with `symbolType` crypto and the
highest 24 h turnover at fetch time whose base coin appears in no `benchmark/*/manifest.json`
on `main` at commit fe7081f7, taking the next coin whenever one cannot serve every series below
in full. Three frozen series per coin, all ending at the fetch:
- `benchmark/fresh10_1h`: 9,000 one-hour bars (375 days);
- `benchmark/fresh10_15m`: 20,000 fifteen-minute bars (208 days);
- `benchmark/fresh10_5m`: 20,000 five-minute bars (69 days).

## Arms

`python -m bot.backtest.runner --dataset <ds> --timeframe <tf> --honest --walk-forward 6
--breaker-reset-bars <one day of bars> --trades-out <file>`, default costs, plus:

| arm | data | extra flags | role |
|---|---|---|---|
| R1 | fresh10_1h | none | reference: the bot as it is |
| **H1** swing stops on 4h | fresh10_1h | `--swing-stop-tf 4h` | tested |
| **H3** daily trend | fresh10_1h | `--daily-trend-days 50` | tested |
| RS | fresh10_1h | `--setup-only scalp` | reference: scalps as they are |
| **H2a** scalps on 15m | fresh10_15m | `--setup-only scalp --mtf-scaled-history` | tested |
| **H2b** scalps on 5m | fresh10_5m | `--setup-only scalp --mtf-scaled-history` | tested |

The controls are in `bot/backtest/engine.py` (`_apply_timeframe_variants`, `_scaled_mtf_context`)
and change nothing in the live bot. `--swing-stop-tf 4h` sets a swing idea's stop and target at
the configured swing ATR multiples (2.5 / 3.5) of ATR(14) over closed 4h groups; the risk gate
sizes from the stop, so the money at risk per trade is unchanged. `--daily-trend-days 50` admits
a LONG only above the coin's 50-day close mean and a SHORT only below; no reading is no trade.

## Decision rule (four tested arms: H1, H2a, H2b, H3)

Per OOS position, `net_pnl_pct` from `--trades-out`; None rows left out and counted. An arm
**makes money** when all hold:
1. at least 40 scored positions (fewer: no verdict, not a fail);
2. the pooled mean net return per position is above 0, and the lower end of a one-sided
   interval at the 0.625th percentile (2.5% / 4 arms, Bonferroni) of a block bootstrap over
   ISO weeks of entry time (10,000 draws, seed 7) is above 0;
3. the mean of the six walk-forward fold returns (the account) is above 0.

Condition 3 is there because stage 2 found a positive per-position mean while every fold lost.

An arm that makes money goes to a paper forward test before any change to the live bot.

## Reported, deciding nothing

Each arm against its reference (H1 and H3 against R1, H2a and H2b against RS): the difference
in mean net per position, size-weighted means, fold returns, profit factor, trade counts,
exit reasons, per-coin means.

## Limits

One universe of 10 coins, unseen by any earlier test, but the bot's defaults were tuned on
other coins of the same venue. The 5m set is 69 days. The 5m and 15m runs build their 4h and
1d context from their own bars, so the 1d context exists only after 30 days of a set.
