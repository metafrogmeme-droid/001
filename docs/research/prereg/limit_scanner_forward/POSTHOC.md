# Post-hoc note: the forward window is empty

Written 2026-10-25 (UTC). This is not a result of the pre-registered test.

The frozen Bitget book used by the scanner card, `benchmark/limit_scanner_1h`,
has 34 symbols. Its last bars are `2026-10-10T16:00:00Z` and
`2026-10-10T17:00:00Z`. The pre-registered cutoff is
`2026-10-23T00:00:00Z`, exclusive. No bar in that book is after the cutoff,
so there is no forward trade to score. Funding on those bars is not in the
file. A verdict from this book is refused: unpriced, and on the wrong side
of the cutoff.

`scripts/limit_scanner_measure.py` prints dev and holdout rows for the
variants named in the pre-registration. Those rows describe a window that
was already read. They cannot pass the bar (deflated Sharpe at least 0.95,
at least 100 trades, funding priced, fills strictly after the cutoff).

The playbook is not run and not published from this change. Follow-trade
stays opt-in, and the shadow readout stays off unless `LIMIT_SCANNER_SHADOW`
is on for an operator.
