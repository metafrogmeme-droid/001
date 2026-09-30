# ADR 0005: Practice fills for self-admitted paper users

- **Status:** Proposed
- **Date:** 2026-09-29
- **Plan items:** F8, B1 (docs/IMPROVEMENT_PLAN_2026-09-29.md, "F. Signals, patterns, website and product" and "B. One Idea Ledger"); "Decisions the owner needs to make" #9 and #10
- **Informed by:** ADR 0001 (not yet run) for `PAPER_SIM_OPT_IN_ENABLED` in production; the first-Confirm outcome rate for new paper users is unmeasured.

## Context

A user who opted into practice has their confirmed trade simulated into their
paper portfolio, before any exchange call, only while
`PAPER_SIM_OPT_IN_ENABLED` is on (`bot/config.py`
`AppConfig.paper_sim_opt_in_enabled`, default off; `bot/core/engine.py`
`RuneClawEngine._confirm_trade_inner`, the per-user paper opt-in branch that
calls `_simulate_paper_fill`). With it off, the bot is live-only and a paper
user's Confirm has nowhere to go. The plan measures that as a dead end for new
users. Practice outcomes must not train the engine's learners or reach public
records (B1).

## Options

- **(a)** Labelled PRACTICE fills on a paper user's first Confirm, excluded by
  B1 from engine learners and public records.
- **(b)** Live-only, with a watch-and-link onboarding and no Confirm for paper
  users.
- **(c)** Status quo: the Confirm dead-ends.

## Recommended default

(a), with B1's exclusions shipped first or together. It gives a new user a first
Confirm that does something honest, and the label keeps practice from being
read as a track record.

## Consequences

- Every practice row carries a label that every reader asks; guard tests show
  no practice row reaches an engine learner, a leaderboard or a public record.
- The onboarding checklist in chat: watch a signal, open its page, stage a
  practice ticket, link read-only keys.
- The `/connect` card reads `PER_USER_LIVE_ENABLED` instead of always saying
  "not yet enabled" (`bot/skills/account_commands.py`).
- Success is measured, not assumed: the share of new paper users whose first
  Confirm ends in a practice fill or an explicit next step, baselined in week 1.

## Decision

Decision: pending operator.
