# ADR 0006: Pre-registered defaults for C2 (LLM direction) and C3 (confidence)

- **Status:** Proposed
- **Date:** 2026-09-29
- **Plan items:** C2, C3 (docs/IMPROVEMENT_PLAN_2026-09-29.md, "C. Edge research program"); "Decisions the owner needs to make" #5 and #6
- **Informed by:** ADR 0001 (not yet run): the LLM-sourced row count in `data/learning/llm_calibration.jsonl` decides whether C2 has power. Neither test has been run.

## Context

The thesis direction is blended from an LLM confidence and a confluence score:
`bot/config.py` `AnalyzerConfig.llm_weight` (`LLM_BLEND_WEIGHT`, default 0.6)
and `AnalyzerConfig.confluence_weight` (default 0.4), with the LLM's weight
capped while no fitted calibration curve is applied
(`AnalyzerConfig.uncalibrated_llm_weight_cap`). Whether the LLM's live direction
beats simple baselines net of cost is unmeasured.

Confidence gates entries (`RiskLimits.min_confidence`, `MIN_CONFIDENCE` 0.60),
sets the auto-confirm bar, is shown to people as a percentage and feeds sizing
paths that read it. The backtest rows that could test it are censored at the
floor and carry the rule-engine blend, not live's (plan C3).

Pre-registering the action before the result is what keeps the result from
choosing the rule.

## Options

C2, the LLM's authority over direction:
- **(a)** Keep the current blend weight.
- **(b)** Blend weight to 0 unless the LLM survives against the best baseline
  (the recorded rule-engine direction, sign of the 24h change and its
  negation, always-long) net of cost; a survival on existing rows only
  nominates it for prospective confirmation.
- **(c)** Replace it with the rule engine entirely.

C3, confidence as a displayed percentage and a sizing input:
- **(a)** Keep both until a test says otherwise.
- **(b)** Show a rung word or "unmeasured", and keep confidence-scaled sizing
  off until the decisive prospective test on uncensored ledger rows passes.
- **(c)** Drop confidence entirely.

## Recommended default

C2 (b) and C3 (b). Both remove claims rather than add them, so both can be
applied on an "inconclusive" result, which is the expected result if
LLM-sourced rows are few. The LLM stays analyst and narrator in chat and on
cards either way.

## Consequences

- C2 writes `benchmark/llm_direction/result.json` with n and n_eff per source,
  the model id pinned, and a three-way verdict per baseline; a model change
  resets the evidence clock, and no model is scored on history before its
  training cutoff.
- C3 commits a provisional table labelled provisional, a guard test refuses any
  confidence rendered as a probability, and the decisive test is registered
  with an evaluation date.
- The minimum-confidence floor stays as a filter; its value is judged through
  counterfactual labels, not changed here.

## Decision

Decision: pending operator.
