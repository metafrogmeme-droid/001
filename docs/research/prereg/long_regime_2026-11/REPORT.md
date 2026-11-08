# Long only, behind BTC's trend, on the bot's own signals: the report (read 2026-11-08)

Pre-registration `PREREGISTRATION.md`, sha256 `8172ddaf…`, sealed 2026-11-08T02:42:47Z before the
data was fetched and before the BTC-trend control existed. Data frozen and committed before any run
(`DATA.md`): `benchmark/fresh10b_1h` (ONE, ICNT, LYN, CYBER, QNT, MET, ACT, ARIA, SIGN, FIL; 9,000
hours each, to 2026-11-08) and `benchmark/fresh10b_btc_1d`. Runs: `runs/plan.txt`, every arm on the
frozen benchmark's own method (`--as-live`, six walk-forward folds, live's 4,824 bars of history,
breaker reset 24). Judged by `scripts/long_regime_study.py` (the timeframe-variants rule at a
Bonferroni share of 2.5% / 3); figures in `result.json`.

The reference arm on `benchmark/majors_1h` reproduces the committed benchmark record exactly (6
folds, 3 profitable, mean -0.27%, worst -2.56%, 105 positions), so the arms run the method the
record names.

## Verdict: no tested arm makes money

| arm | rows | mean net per row | 0.833rd-pct bound | mean fold return | PF | verdict |
|---|---|---|---|---|---|---|
| R the bot as it is | 279 | +0.29% | -0.12% | -1.48% | 0.66 | reference |
| **L1** long only | 170 | +0.37% | -0.47% | -0.94% | 0.65 | **does not make money** |
| **L2** long only, BTC above its 150-day mean | 119 | +0.28% | -0.89% | -0.88% | 0.57 | **does not make money** |
| **L3** L2 with swing stops on 4h | 33 | | | -0.28% | 0.46 | **no verdict** (fewer than 40) |

A row is one exit fill from `--trades-out`, so a position closed in parts is several rows, as in the
timeframe-variants study. Each arm fails condition 2 (the bound is below zero) and condition 3
(every arm's account lost over the six folds).

## What the numbers say

- **The mean per row is positive in every arm, and every account loses.** The size-weighted mean is
  negative (R -0.18%, L1 -0.21%, L2 -0.28%, L3 -0.34%): the small partial take-profit fills win
  often and the full-size stops lose more. A per-row mean above zero is not money, which is why the
  rule asks for the folds too.
- **Long only lost less on these coins, and more on the earlier sets.** On the fresh set L1 lost
  0.94% a fold against the reference's 1.48%. On the two frozen sets, which decide nothing, removing
  the shorts made it worse: majors_1h -0.51% against -0.27%, corr_dense_1h -1.86% against -1.24%.
  The bot's shorts are most of its trades there (106 of 144 rows on majors_1h, 480 of 667 on
  corr_dense_1h). So "drop the shorts" is not a fix that holds across data.
- **BTC was below its 150-day mean on 73% of the hours** of the fresh window. L2 took 119 rows; the
  gate cut the long trades by a third and did not improve them (PF 0.57 against L1's 0.65). At 100
  and 200 days (reported, deciding nothing) the folds were -0.99% and -0.57%.
- **L3 traded too little to judge** (33 rows, -0.28% a fold).

## Against the expectation written before the run

L1 above R and still negative: as expected. L2 was expected to have the best chance; it did not
beat L1. No arm made money: as expected.

## What follows

Nothing changes in the bot, and nothing goes to paper. With `signal_edge_stage2_2026-10`,
`timeframe_variants_2026-10`, `exit_ladder_2026-11` and this study, the bot's hourly signals have now
been tested with fewer trades, wider stops, earlier exits, a coin trend filter, long only, and a BTC
trend gate; none makes money after costs.
