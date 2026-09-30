# ADR 0003: Eligibility artefact schema and holdout sealing strictness

- **Status:** Proposed
- **Date:** 2026-09-29
- **Plan items:** C12, D2, D11 (docs/IMPROVEMENT_PLAN_2026-09-29.md, "C. Edge research program" and "D. Capital gate"); "Decisions the owner needs to make" #2
- **Informed by:** the code as of this date; no production figure is needed.

## Context

The eligibility record today is schema 1 with four fields the gate reads:
`schema`, `strategy_hash`, `verdict` (`survives` / `does_not` /
`inconclusive`) and `stage` (`none` / `minimum` / `25%` / `100%`)
(`bot/core/live_eligibility.py` `read_eligibility`, `STAGES`, `VERDICTS`;
`benchmark/eligibility/README.md`). The strategy hash is v1: every `.py` under
`bot/`, deliberately too strict, and it does not cover configuration
(`live_eligibility.strategy_hash`). The evidence behind a verdict is not in the
schema; a reviewer reads it in the commit.

D2 asks for a schema shared with D11, the KPIs and the phase gates: the
registration commit (dated before its data existed), the prospective
window(s), n and n_eff against the required n, the A7 cost-model version, the
C11 method, the family alpha, the three-way verdict, the C13 portfolio result,
regime coverage and the stage granted. C12 asks how strictly prospective data is
sealed until a hypothesis's evaluation date. The frozen benchmark windows have
already been read, so they cannot confirm anything.

## Options

Schema:
- **(s1)** Keep schema 1; evidence lives in the commit message and review.
- **(s2)** Schema 2 carrying D2's evidence fields; the gate still decides on
  `strategy_hash`, `verdict` and `stage`, and refuses a record missing any
  evidence field.

Sealing:
- **(a)** Hard access control on prospective partitions for money-family
  hypotheses (read only by the registered evaluation command, on or after its
  date); logged reads for display.
- **(b)** A logged honour system for everything.
- **(c)** No sealing; reuse existing windows.

## Recommended default

(s2) with (a). A record whose evidence a reviewer cannot see in the file is a
verdict taken on trust, and hard sealing is the only way left to get a clean
confirmation; it is cheap for the few money-family hypotheses.

## Consequences

- `live_eligibility` gains a schema-2 reader; a schema-1 record keeps being
  read as today until retired, and the README table grows with it.
- `scripts/research/seal.py` (new, C12) refuses and logs every other read;
  CI never reads sealed data.
- During a hypothesis's accrual window its strategy hash is frozen; only
  changes a replay proves decision-identical may merge.

## Decision

Decision: pending operator.
