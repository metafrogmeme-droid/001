# Timeframe variants: report (2026-10-31)

Rule: `PREREGISTRATION.md` (sealed 2026-10-31 08:49:54Z). Data frozen before any run (`DATA.md`,
commit 00ba8d1b). All six runs at code sha 00ba8d1b. Outputs and `report.json`: `runs/`.

## Verdict: no tested arm makes money. H3 could not be measured as sealed.

| arm | positions | mean net / position | 0.625th-pct bound | mean fold return | verdict |
|---|---|---|---|---|---|
| H1 swing stops on 4h ATR | 123 | +0.041% | -0.585% | -1.27% | **does not make money** |
| H2a scalps on 15m bars | 104 | -0.275% | -0.614% | -1.54% | **does not make money** |
| H2b scalps on 5m bars | 131 | +0.026% | -0.067% | -0.74% | **does not make money** |
| H3 daily trend (50 days) | 0 | n/a | n/a | n/a | **no verdict: not measurable as sealed** (below) |

## Reported, deciding nothing

| arm | positions | mean / position | size-weighted mean | profit factor | folds (%) | how positions ended |
|---|---|---|---|---|---|---|
| R1 the bot as it is (1h) | 606 | +0.625% | -0.102% | 0.87 | -7.03, +2.53, -3.24, -7.75, +4.95, -3.01 | SL 431, TP1 61, trail 61, time 35 |
| H1 | 123 | +0.041% | -0.267% | 0.62 | -2.11, -0.80, +0.28, -1.43, -2.63, -0.94 | SL 53, **time 50**, TP1 9, trail 9 |
| RS scalps as they are (1h) | 79 | +0.073% | -0.342% | 0.54 | +0.08, -0.56, -0.98, -1.92, -2.63, +0.16 | **time 42**, SL 25, TP1 5, trail 5 |
| H2a | 104 | -0.275% | -0.364% | 0.40 | +0.52, -2.20, -1.48, -1.29, -2.56, -2.22 | SL 74, time 15, TP1 7, trail 7 |
| H2b | 131 | +0.026% | -0.130% | 0.69 | -0.58, -1.07, -0.99, -1.66, -1.00, +0.89 | SL 100, TP1 13, trail 13, time 3 |

- **Swing stops on 4h (H1).** The wider stop did what it was meant to on exits: stops fell from 71% to
  43% of exits. But the trades it saved ran into the 48-hour time limit instead (41% of exits), and
  the bot took a fifth as many positions. Smaller positions lost less in total (folds -1.27% vs
  -2.26%), but per position it was worse than the bot as it is (-0.58 points).
- **Scalps on finer bars (H2).** On 5m bars the time stop no longer kills scalps (3 of 131 exits,
  against 42 of 79 on 1h) and the result is the closest to break-even of any arm, but its folds lose
  in five of six. On 15m bars it is worse than scalps on 1h.
- **The bot as it is (R1)** shows again what stage 2 found: a positive mean per position (+0.63%,
  bound above zero) while the account loses in four of six folds. Weighted by the size the bot gave
  each position, the mean is negative. R1 is a reference here and decides nothing.

## Why H3 has no verdict, and what it shows about every walk-forward run

`portfolio_walk_forward` hands each fold its out-of-sample block plus `lookback_size` (100) bars
of warm-up, and nothing older. A 50-day daily mean needs 1,224 hourly bars; an out-of-sample block
here is about 900, so the reading never formed and every idea was refused (590 ideas in fold 0,
0 positions). By the sealed rule fewer than 40 positions is no verdict, not a fail.

The same limit applies to the context the analyzer is given. It builds 1d context only once 30
closed daily groups exist, so in a 1h fold of 37 days the daily context appears only in the last
week, and in the 15m and 5m folds (21 and 7 days) never. The live bot always has 200 daily bars.
Every walk-forward result on record measured a bot whose daily-weighted trend gate
(`MTF_ALIGNMENT_GATE_ENABLED`, on in live) was blind for most of each fold.

## Limits

One universe of ten coins (recent, high-turnover listings, by the rule), one split. H2's 1d
context was absent and its 4h context partial, for the reason above.

## Addendum 1 result (2026-10-31): H3 with live's history does not make money

`ADDENDUM_1.md` (sealed 2026-10-31 09:40:05Z, before the harness change and any run). Runs at code
sha d452dfa0 on the same frozen `fresh10_1h`; outputs in `runs/` (`report_addendum_1.json`).

| arm | positions | mean / position | 0.625th-pct bound | mean fold | verdict |
|---|---|---|---|---|---|
| **H3h** daily trend, with 4,824 bars of fold history | 453 | +0.209% | -0.274% | -4.41% | **does not make money** |
| R1h the bot as it is, same history (reference) | 460 | +0.195% | -0.336% | -4.10% | reference |

Reported, deciding nothing:
- **The daily filter adds almost nothing once the bot can see the daily trend.** It removed 7 of
  460 positions. The bot's own daily-weighted gate (`MTF_ALIGNMENT_GATE_ENABLED`) already refuses
  most counter-trend ideas when it has daily bars to read.
- **Live's history made the bot worse on these coins.** Against the sealed reference R1 (100 bars
  of history per fold): 460 positions instead of 606, mean per position +0.195% instead of +0.625%,
  mean fold -4.10% instead of -2.26%, size-weighted mean -0.242% instead of -0.102%. One universe,
  one split: this is a measurement of what the missing context did here, not a general law. It
  does mean the earlier walk-forward figures measured a bot other than the one that trades live.

Folds (%): R1h -2.78, -9.63, -5.18, -2.61, -1.87, -2.54; H3h -6.51, -9.20, +0.45, -4.59, -6.04, -0.57.

## Overall

None of the four timeframe ideas makes money on unseen coins: swing stops on 4h, scalps on 15m,
scalps on 5m, the 50-day trend filter. Scalps on 5m came nearest. The harness gap the study found
is the more durable result: walk-forward runs should be given their history (`--fold-history-bars`)
before any further figure is compared with live.
