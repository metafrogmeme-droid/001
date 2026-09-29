# ADR 0009: Marketplace presets whose profit factor is below 1

- **Status:** Proposed
- **Date:** 2026-09-29
- **Plan items:** F7 (docs/IMPROVEMENT_PLAN_2026-09-29.md, "F. Signals, patterns, website and product"); "Decisions the owner needs to make" #13
- **Informed by:** the scorecards committed in `benchmark/scorecards/` (backtest records on discovery data, read 2026-09-29); no production figure.

## Context

Each marketplace preset has a committed scorecard. As recorded in the files on
2026-09-29, `metrics.profit_factor` and `metrics.total_trades` are:
`dip-sniper` 0.91 over 19 trades, `full-scan` 0.55 over 30, `safe-scalper`
0.29 over 11, and `momentum-hunter` 0.0 over 3 (no winning trade). All four are
below 1, all are on discovery data already read, and all are small samples. No
preset's strategy holds an eligibility record (ADR 0003). The copy and follow
routes (`app/routes/copy.js`) and the marketplace views offer presets to follow.

## Options

- **(a)** Hide PF-below-1 presets.
- **(b)** Show them with their verdict (PF, n, folds, "discovery data") and
  copy/follow disabled.
- **(c)** Status quo.

## Recommended default

Hide them from public listing, promotion and copy/follow; inside the logged-in
marketplace show them as (b) until a strategy has an eligibility record. A
preset offered for following implies it is worth following, and the record says
otherwise.

## Consequences

- A guard test: no preset is shown or offered for following without its
  verdict, and none below PF 1 is publicly listed or followable.
- Copy panels show the followed agent's record in R with a C11 interval; no
  dollar figure on the public marketplace payload.
- Re-listing a preset needs an eligibility record, not a better backtest.

## Decision

Decision: pending operator.
