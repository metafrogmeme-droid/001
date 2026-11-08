# Two market-neutral daily rules: the report (read 2026-11-08)

Pre-registration `PREREGISTRATION.md`, sha256 `3a231b00…`, sealed 2026-11-08T02:46:18Z before the
evaluator existed. Evaluator `scripts/daily_neutral_study.py`, sha256 `7f3ab4b2…`
(`code_at_unseal.sha256`), committed before the development run. Development read first
(`result_dev.json`), holdout read once (`result_holdout.json`). Panel: the 20-coin Binance daily
set frozen for `new_strategy_xsmom_regime`, last bar 2026-10-27.

## Verdict: both rules FAIL every bar, on development and on holdout

| rule | dev Sharpe | holdout Sharpe | holdout 2.5% bootstrap | holdout max DD | years up / traded | verdict |
|---|---|---|---|---|---|---|
| N1 reversal spread | -0.41 | -0.75 | -1.75 | 43.0% | 2 / 8 | **FAIL** |
| N2 ETH/BTC ratio | -1.03 | -0.19 | -1.09 | 31.6% | 2 / 9 | **FAIL** |

Net of 0.10% a side on every leg, no funding. N2 completed 57 round trips on development and 41 on
holdout, so it is a verdict, not a shortage of trades.

## What the numbers say

- **N1 loses before costs.** Gross spread over development -24%, over holdout -29%; costs took a
  further 15% and 17%. Buying last week's worst coins against the basket did worse than the basket:
  the long leg alone minus the equal-weight universe scored a Sharpe of -0.74 on development and
  -0.96 on holdout. In this universe, a week's losers kept losing the next week. That is a reading,
  not a tested claim about weekly momentum, which an earlier study (H3, `kraken_spot_history`)
  found does not pay on its own.
- **N2 loses before costs on development** (gross -84% summed over 2018-22, when the ETH/BTC ratio
  moved in long trends) and is flat gross on holdout (-1.1%), so costs decide it there.
- Both are close to uncorrelated with BTC on holdout (0.04 and 0.11), as a neutral rule should be.
  Being uncorrelated is not the same as earning.

## Against the expectation written before the run

N1 was expected to show a positive gross spread eaten by costs; the gross spread was negative. N2
was expected to be short of trades or fail; it failed with enough trades.

## What follows

Nothing changes in the bot. Neither family goes to paper. With the long/short results before
(`NEW_STRATEGY_SUMMARY_2026-10-28.md`), no market-neutral daily rule tested in this repo has earned.
