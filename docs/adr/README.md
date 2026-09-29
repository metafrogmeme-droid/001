# Architecture decision records

One file per decision the operator makes, dated, so the work that implements a
decision can cite the record instead of re-arguing it. The week-1 pass (plan
item G0 in `docs/IMPROVEMENT_PLAN_2026-09-29.md`) opened ADRs 0001 to 0010. All
are **Proposed**: each states the context with citations, the options, the
recommended default and its consequences, and ends `Decision: pending
operator.` None records a production measurement; ADR 0001 says how to take
them, and the others are decided after it has been run.

| ADR | Decision | Plan items | Status |
|---|---|---|---|
| [0000](0000-template.md) | Template | - | - |
| [0001](0001-day0-production-readout.md) | Day-0 production readout (the D0 appendix) | D0 | Proposed, readout not yet run |
| [0002](0002-research-mode-posture-and-target-notional.md) | Research-mode posture and target notional | D1, D2 | Proposed |
| [0003](0003-eligibility-artefact-schema-and-sealing.md) | Eligibility artefact schema and holdout sealing strictness | C12, D2, D11 | Proposed |
| [0004](0004-public-positioning-no-demonstrated-edge.md) | Public positioning: "no demonstrated edge" and exploratory labels | F3, F12, C3 | Proposed |
| [0005](0005-practice-fills-for-paper-users.md) | Practice fills for self-admitted paper users | F8, B1 | Proposed |
| [0006](0006-pre-registered-defaults-for-c2-and-c3.md) | Pre-registered defaults for C2 and C3 | C2, C3 | Proposed |
| [0007](0007-chat-write-policy-staged-drafts.md) | Chat write policy: staged drafts | E5, E3 | Proposed |
| [0008](0008-per-user-live-envelope.md) | Per-user live trading and the Authority Envelope | D12 | Proposed |
| [0009](0009-marketplace-presets.md) | Marketplace presets whose profit factor is below 1 | F7 | Proposed |
| [0010](0010-fixture-capture-cost-budget.md) | Venue fixture-capture cost budget | D4, D14 | Proposed |

## Writing one

Copy `0000-template.md` to the next free number. Cite code by path and symbol,
not by line number. A figure is a measurement only with its source, date and
basis; a production figure comes from ADR 0001's readout or is written as
unknown. When a decision is taken, replace the `Decision:` line with the option
chosen, who chose it and the date, and set the status to Accepted; a later
change of mind is a new ADR that supersedes this one.
