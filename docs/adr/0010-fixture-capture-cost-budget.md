# ADR 0010: Venue fixture-capture cost budget

- **Status:** Proposed
- **Date:** 2026-09-29
- **Plan items:** D4, D14 (docs/IMPROVEMENT_PLAN_2026-09-29.md, "D. Capital gate and execution integrity")
- **Informed by:** ADR 0001 (not yet run) for the account type and the venue-priced share; the cost per capture is not yet measured.

## Context

The repository holds no recorded venue HTTP fixtures as of 2026-09-29: the
executor's Bitget call sites (open, protect, trail, partial, close, reconcile,
adopt, history lookup) have no replay, so a refactor of `bot/core/live_executor.py` cannot be proven
decision-identical. D4 records sanitised HTTP pairs, one per call site, on
classic and unified (UTA) accounts, replayed through the pinned ccxt. The
close-lookup history stage behaves differently on a unified account
(`bot/core/close_lookup.py` module docstring, `client_is_uta`), and demo
recordings are not assumed to match production request shapes. Read-only
captures cost nothing; the order-path captures need real minimum-size orders,
which cost fees and slippage.

## Options

- **(a)** Read-only captures only; order paths stay unreplayed.
- **(b)** Read-only captures plus minimum-size real orders under an
  operator-approved budget: a ceiling on total fees and slippage for the
  capture run, a per-order cap at the research ceiling (ADR 0002), and a
  stop when the ceiling is reached.
- **(c)** Demo-environment captures only.

## Recommended default

(b), with the budget set by the operator here before any capture order is
placed. It is the only option that replays the order paths as production sees
them.

## Consequences

- `scripts/capture_venue_fixtures.py` (new) refuses to place an order once the
  recorded spend reaches the approved ceiling, and records each capture's cost
  from the venue's own fill and fee fields; an unread fee is counted against
  the budget at the configured estimate and said so, never as zero.
- Captures are sanitised (keys, signatures, account ids) before they are
  committed under `tests/venue_replay/bitget/`.
- A nightly read-only smoke diffs response shapes.
- The ceiling, the account(s) used and the realised spend are recorded here;
  none is chosen by this ADR.

## Decision

Decision: pending operator.
