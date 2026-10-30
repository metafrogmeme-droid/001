# Do the bot's calls make money on data it has never seen? Stage 2 report (2026-10-30)

Rule: `PREREGISTRATION.md` (sealed 2026-10-30 12:06:57Z). Data frozen before any run
(`DATA.md`, commit af597f97). Evaluator committed before any run (`scripts/signal_edge_stage2.py`).
All six runs at code sha b7846da4. Outputs and `report.json`: `runs/`.

## Verdict: neither arm makes money. The calls carry a small edge before costs, by the sealed screen.

Mean net return per position, % of position size, with the one-sided 98.75% week-block
bootstrap lower bound:

| arm | F1 Bitget fresh (Jul-Oct 2026) | F2 Kraken ETH/SOL (2021/2024-Oct 2025) | makes money? |
|---|---|---|---|
| A0 defaults | +0.011, lower -0.490 (n 234) | +0.064, lower -0.161 (n 580) | **no** |
| A80 confidence 0.80 | +0.229, lower -0.817 (n 49) | +0.560, lower -0.080 (n 117) | **no** |
| A0 at zero cost | +0.243, lower -0.228 (n 241) | +0.352, lower +0.099 (n 672) | screen: positive on both, so "carries information" |

No row had an unreadable return.

## Reported, deciding nothing

What the account would have done (fold returns, % of equity, in time order) and the dollar
profit factor:

| arm | F1 folds | F1 PF | F2 folds | F2 PF |
|---|---|---|---|---|
| A0 | -0.51, -1.40, -2.27, -5.50, -5.44, -4.64 | 0.56 | -8.61, -5.49, -0.29, -8.06, -9.36, -4.79 | 0.65 |
| A80 | +0.59, -1.11, +0.81, -2.14, +0.40, -1.33 | 0.67 | -0.79, -1.87, +1.40, -1.61, -2.16, +5.11 | 1.00 |
| A0 zero cost | +2.31, +0.42, -0.95, -2.69, -1.64, -1.39 | 0.90 | -4.24, -1.08, +10.42, -0.11, -6.65, +6.96 | 1.04 |

The per-position mean and the account disagree: A0's mean per position is slightly positive
on both sets, yet every one of its twelve folds lost money. The positions that lost were the
larger ones. Weighted by position size (`size_rel`), the mean net return per position is:

| arm | F1 | F2 |
|---|---|---|
| A0 | -0.285 | -0.226 |
| A80 | -0.198 | +0.002 |
| A0 zero cost | -0.054 | +0.025 |

Per coin (A0, net): every coin's lower bound is below zero on both sets.

## What this says

- **The bot's calls do not make money after costs on data it was not tuned on.** The default
  bot lost in all twelve out-of-sample folds. The stricter filter (0.80) roughly breaks even
  on Kraken ETH/SOL and loses on fresh Bitget data, on 49 and 117 positions.
- **The edge before costs is small and depends on how it is counted.** Per position it is
  positive on both sets, which is what the sealed screen asked. Weighted by size it is about
  zero, and negative on the fresh Bitget set.
- By the pre-registration, the published calls are not to be presented as trades that pay.

## Limits

F2 uses the bot's perp costs, not Kraken's higher spot fees, which flatters F2's net. F1 is
four months. The sealed measure is unweighted per position; the size-weighted figures and the
fold returns above are reported, not decided on, and they point the same way as the verdict.
