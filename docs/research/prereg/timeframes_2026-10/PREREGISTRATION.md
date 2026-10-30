# The bot, unchanged, on other bar sizes: pre-registration (2026-10-30)

Written and sealed before any dataset below is fetched.

## Question

Every test this month ran the bot on 1-hour bars, and on them it does not make money
net of costs. Does the same bot, with every setting at its default, make money on
15-minute, 4-hour or daily bars?

## Data (frozen with `python -m bot.backtest.snapshot` after this file is sealed)

The 10 liquid majors in `bot/backtest/snapshot.py::DEFAULT_BENCHMARK_SYMBOLS`, Bitget
USDT-M perps, ending at fetch time:
- `benchmark/majors_15m`: 15-minute bars, 20,000 per symbol (about 208 days);
- `benchmark/majors_4h`: 4-hour bars, 6,000 per symbol (about 2.7 years);
- `benchmark/majors_1d`: daily bars, up to 2,000 per symbol (whatever the venue serves).
- Reported only: `benchmark/majors_1h_tf`, 1-hour bars, 6,000 per symbol, as the
  reference the other three are read against.

## Runs

`python -m bot.backtest.runner --dataset benchmark/<ds> --timeframe <tf> --honest
--walk-forward 6 --breaker-reset-bars <n>`, where n is one day of bars: 96 (15m), 24
(1h), 6 (4h), 1 (1d). No setting changed otherwise.

## Decision rule (per timeframe; three timeframes tested)

The six walk-forward out-of-sample folds, in time order. Discovery: folds 1-3.
Holdout: folds 4-6. A timeframe "makes money" when all hold:
- mean fold return over the discovery folds > 0;
- mean fold return over the holdout folds > 0;
- every holdout fold's return > -5% (no single fold losing a large share).
Six folds are too few for a bootstrap interval worth the name, so none is used; this
is a screen, not proof. A timeframe that passes goes to a pre-registered stage 2 on a
second universe before any change to the live bot.

## Reported, deciding nothing

Trade counts, win rate, profit factor and max drawdown per timeframe; equal-weight
buy-and-hold return over the same out-of-sample windows; the 1-hour reference run.

## Limits

One universe of 10 majors; one walk-forward split; the bot's defaults were tuned on
1-hour bars, so a loss elsewhere may be a tuning mismatch rather than the timeframe.
Scalp setups use 15m data live; on 15m bars here they get it, on the others they do not.
