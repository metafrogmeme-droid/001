# Addendum 1 (written 2026-10-28 after the data fetch, BEFORE any simulation was run)

The fetch showed that Binance's monthly perp-kline file for 2022-02 omits 2022-02-26..28 for FIL, LTC, TRX, XLM and
XRP (the per-coin daily file for those days exists). The main text says a gap inside a coin's perp history stops the
run with CANNOT CHECK. To keep the universe, the fetch now reads any day missing inside a monthly file from the same
venue's daily kline file for that exact day (same source, same field; no interpolation, no other venue). A day that
is still missing after that remains a gap and still stops the run. No rule, parameter, split or bar changes.
No result of this study had been computed when this was written.
