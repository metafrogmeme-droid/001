# Addendum 1 (2026-10-30, before any backtest was run)

The pre-registered daily dataset could not be fetched: `bot.backtest.snapshot`
requests daily bars from Bitget in one window, and Bitget refuses any window longer
than 90 days ("startTime and endTime interval cannot be greater than 90 days"), so
every symbol was skipped and no snapshot was written.

Change: `benchmark/majors_1d` is built by resampling the frozen `benchmark/majors_4h`
(dataset hash e7c18511ef17...) into UTC days with
`bot.utils.candles.resample_ohlcv`, keeping only days with all six 4-hour bars, then frozen with `bot.backtest.snapshot.build_manifest`.
It covers 2024-02-04 to 2026-10-29, about 1,000 days per symbol instead of up to
2,000. Nothing else changes: same 10 symbols, same runner flags, same rule.

No backtest of any timeframe had been run when this was written.
