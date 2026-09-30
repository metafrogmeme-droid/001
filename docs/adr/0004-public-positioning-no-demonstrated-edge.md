# ADR 0004: Public positioning: "no demonstrated edge" and exploratory labels

- **Status:** Proposed
- **Date:** 2026-09-29
- **Plan items:** F3, F12, C3 (docs/IMPROVEMENT_PLAN_2026-09-29.md, "F. Signals, patterns, website and product"); "Decisions the owner needs to make" #8
- **Informed by:** the eligibility state (no record ships; `benchmark/eligibility/README.md`) and the preset scorecards (ADR 0009). No production figure is needed.

## Context

No strategy holds an eligibility record, so the repository has no artefact
showing any strategy survives prospectively. Public surfaces still present
signals and patterns as trade calls: signal cards carry a direction and a
confidence (`bot/formatters/signal_card.py`; `app/lib/public_signal.js`), and
confidence is blended from an LLM term and a confluence term that have not been
validated against outcomes (`bot/config.py` `AnalyzerConfig.llm_weight`,
`AnalyzerConfig.confluence_weight`; plan C2, C3). The rule behind this repo's
tests applies to copy too: a label is a claim.

## Options

- **(a)** Observations with their measured record: every cell and chip reads
  "exploratory" (n, interval) or "unmeasured", a "no demonstrated edge" line
  sits on public signal and pattern surfaces until an eligibility record
  exists, and "survives" appears only on registered, prospectively replicated
  cells.
- **(b)** Trade signals with confidence percentages, as today.
- **(c)** Hide signals until something validates.

## Recommended default

(a). It matches the evidence and the repo's honesty rules, and it keeps the
surfaces useful: people still see what fired and how it has done, with the
uncertainty stated.

## Consequences

- F3 and F12 render the exploratory row or "unmeasured"; a guard test refuses
  "survives" without a registry entry.
- The confidence display follows ADR 0006's C3 default (a rung word or
  "unmeasured", never a percentage read as a probability).
- Public payloads stay free of dollar amounts; R and ratios are allowed.
- Marketing and README copy that implies an edge is corrected in the same
  pass.

## Decision

Decision: pending operator.
