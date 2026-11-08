# Long only, behind BTC's trend, on the bot's own signals. Pre-registration (2026-11-08)

Written and sealed before the dataset below is fetched, and before the BTC-trend control exists.

## Why

Three readings, all from earlier sealed studies, point the same way:
- The bot's calls point the right way before costs (`signal_edge_2026-10`, `signal_edge_stage2_2026-10`)
  and lose after them.
- Every long/short rule tested lost on its holdout, and shorting the weak coins lost money before
  costs (`NEW_STRATEGY_SUMMARY_2026-10-28.md`, `kraken_spot_history`).
- The one filter that held up was BTC's own 150-day trend (`new_strategy_xsmom_regime`: the gate
  alone scored 1.11).

So: does the bot make money if it only buys, and only while BTC is above its 150-day mean? None
of the arms below is changed after a result is seen.

## Data (frozen after this seal; hashes committed before any run)

**The universe, by rule:** the 10 Bitget USDT-M perpetuals with `symbolType` crypto, online, and
the highest 24 h turnover at fetch time, whose base coin appears in no `benchmark/*/manifest.json`
at commit af7fd6c2, taking the next coin whenever one cannot serve 9,000 closed one-hour bars.
- `benchmark/fresh10b_1h`: 9,000 one-hour bars per coin (375 days), ending at the fetch.
- `benchmark/fresh10b_btc_1d`: `BTC/USDT:USDT`, 600 closed daily bars ending at the fetch, used
  only for the BTC trend reading.

## Arms

Every arm: `python -m bot.backtest.runner --dataset benchmark/fresh10b_1h --as-live --walk-forward 6
--fold-history-bars 4824 --breaker-reset-bars 24 --trades-out <file>`, the frozen benchmark's own
method (`bot/backtest/benchmark_record.py::WRITE_COMMAND`), default costs, plus:

| arm | extra flags | role |
|---|---|---|
| R | none | reference: the bot as it is |
| **L1** long only | `--direction long_only` | tested |
| **L2** long only, BTC trend | L1 + `--btc-regime-days 150 --btc-regime-dataset benchmark/fresh10b_btc_1d` | tested |
| **L3** L2, fewer and longer trades | L2 + `--swing-stop-tf 4h` | tested |

`--btc-regime-days N` is a backtest control added after this seal, in
`bot/backtest/engine.py::BacktestEngine._apply_timeframe_variants`: an entry is admitted only when
the last BTC daily bar that had CLOSED by the close of the deciding bar is above the mean of the
last N closed daily closes (a SHORT only below; no reading, fewer than N days, or an unreadable
series admits nothing). `--swing-stop-tf 4h` is the existing control: a swing idea's stop and
target at the swing ATR multiples of ATR(14) over closed 4h groups; the risk gate sizes from the
stop, so a wider stop is a smaller position. Nothing here reaches the live bot.

## Decision rule (three tested arms)

Per out-of-sample position, `net_pnl_pct` from `--trades-out`; None rows left out and counted. An
arm **makes money** when all hold:
1. at least 40 scored positions (fewer: no verdict, not a fail);
2. the pooled mean net return per position is above 0, and the lower end of a one-sided interval
   at the 0.833rd percentile (2.5% / 3 arms, Bonferroni) of a block bootstrap over ISO weeks of
   entry time (10,000 draws, seed 7) is above 0;
3. the mean of the six walk-forward fold returns is above 0.

An arm that makes money goes to a paper forward test before any change to the live bot. Nothing
in the live bot changes on this result alone.

## Reported, deciding nothing

- Each arm against R: mean net per position, fold returns, profit factor, positions, exit reasons,
  per-coin means, and the share of days BTC was above its 150-day mean.
- L2 at 100 and 200 days, on the same data.
- R, L1 and L2 on two frozen sets already read by earlier studies, `benchmark/majors_1h` and
  `benchmark/corr_dense_1h`, with the BTC reading from `benchmark/majors_1d`. These sets are not
  a test: the bot and the reasons above were shaped on data like them.

## Limits

- One universe, 375 days, one BTC regime history. If BTC spent most of the window below its
  150-day mean, L2 and L3 take few positions, and the likely result is no verdict.
- `--as-live` fills a resting limit on a touch, with no queue: optimistic about entries.
- The coins are the venue's most traded outside the majors, a universe unlike the majors the
  bot's defaults were tuned on.

## Expectation, written before any run

L1 above R, still negative. L2 has the best chance, with a mean near zero and a bound below it.
L3 trades least. I expect no arm to make money by this rule; a pass would be a surprise and goes
to paper first.
