# Formatting-only change after the seal

`code_at_unseal.sha256` (59bd34a5…) is the hash of the code that read the holdout. It is left as it was.
Two script lines and one test line were then wrapped to meet the repo's 120-column ruff ratchet (E501):
the `turnover_cost_share_of_gross` expression, the funding-sensitivity line (a local `funded` variable
now holds the backtest), and one test fixture line. No logic changed.

Check made after the wrap: `evaluate()` was recomputed in memory from the shipped data for the dev
and holdout windows, and both equal `result_dev.json` and `result_holdout.json` exactly. The 13 tests pass.
The hash of the two files now is 5c90955d6aeb1616c6c0b9b40d81c370c2eeedd156fca19a39dae53f8e71ebd8.
