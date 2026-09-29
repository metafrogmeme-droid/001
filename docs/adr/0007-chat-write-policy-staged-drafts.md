# ADR 0007: Chat write policy: staged drafts

- **Status:** Proposed
- **Date:** 2026-09-29
- **Plan items:** E5, E3 (docs/IMPROVEMENT_PLAN_2026-09-29.md, "E. Chat copilot"); "Decisions the owner needs to make" #7
- **Informed by:** the code as of this date; no production figure is needed.

## Context

A ticket a person types enters through `bot/skills/manual_trade.py`
`build_manual_idea` with `source='manual'`, and every manual-keyed site reads
it as typed (levels as shown, the 24h expiry, no drift chase). Chat tools today
read (`bot/nlp/chat_tools.py` `tools_for`). The question is whether, and how,
the model may put a ticket in front of a person. A tool result is text the
model did not write and the operator did not either: an instruction planted in
it must not become a one-tap live ticket.

## Options

- **(a)** Read-only tools only; the model never proposes a ticket.
- **(b)** Staged drafts: a `draft_trade` tool computes a proposed ticket
  deterministically (ATR stop, the levels floor, the strategy's minimum R:R,
  the net ratio from trade costs) and returns it as data, registering nothing.
  It is offered only when the user's own message in that turn asks for a
  ticket (the check reads the user's text, never tool-result text). Only a
  person pressing "Stage this ticket" calls `build_manual_idea` with
  `source='manual'` and a separate provenance `origin='chat_draft'`; review and
  the existing Confirm card follow.
- **(c)** The model registers drafts directly.

## Recommended default

(b), shipped only after E3's injection cases pass. (c) would let a planted
instruction in a tool result stage a ticket; (a) leaves the model writing
prices in prose, which is worse than a computed ticket a person must stage.

## Consequences

- The "execution" reply contract calls the tool instead of writing prices; the
  eval counts model-typed entry/stop/target triples (target 0).
- A staged draft is exactly a `/trade` ticket to every manual-keyed site,
  driven by a test; `origin='chat_draft'` is the only difference.
- Conversion (drafted, staged, confirmed) and each draft's outcome are recorded
  for the ledger (B), so the copilot's usefulness is measured.
- Nothing here lets chat change which trades the engine takes.

## Decision

Decision: pending operator.
