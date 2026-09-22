# RUNECLAW — Upgrade Roadmap

Follow-up to `AUDIT.md`. The deep second-pass audit confirmed all prior HIGH/MEDIUM
fixes were applied and surfaced a strong trust architecture (LLM is numerically
powerless; learning cannot mutate risk/config; layered fail-closed gating). The
remaining gap to "could touch real money" is **execution-layer reliability** and
**continuous proof of correctness** — not security or risk-logic holes.

This document tracks the upgrade plan. **Tier 1, item 1 (order idempotency +
orphan detection) is implemented** — see `bot/core/live_executor.py`,
`bot/core/engine.py`, and `tests/test_order_idempotency.py`.

---

## Tier 1 — Before this ever sees real funds

### ✅ 1. Idempotent orders + orphan detection — DONE
- Every live order now carries a deterministic `clientOid` derived from the trade
  idea (`LiveExecutor._client_oid`). Bitget dedups on it, so a retry can never
  double-submit.
- `_create_order_idempotent` wraps all three entry-order paths: on a
  timeout/network error it **queries the exchange by `clientOid`** and recovers a
  landed order instead of losing or resubmitting it; only a *confirmed-absent*
  order re-raises.
- The v3 SL/TP strategy order also carries a `clientOid`.
- `detect_untracked_positions()` runs in the periodic monitor and flags exchange
  positions with no local record (the orphan case a timed-out order could create).
  It complements the existing `reconcile_positions()` (which handles the opposite
  direction). Read-only — it never mutates money state automatically.
- Covered by `tests/test_order_idempotency.py` (5 tests).

### 2. `Decimal` for money — Stage B done, Stage C next
Big-bang conversion across the tree risks silently corrupting PnL, so this
lands in stages, each green under CI before the next:
- **Stage A (done):** `bot/utils/money.py` (`to_money`, `quantize_to_tick`,
  `fmt`) at the close card and the venue reference price. Live order prices
  stay on float.
- **Stage B (done):** the paper book's open quantity, close settlement, and
  the equity/exposure/peak leg are `bot/utils/paper_money.py`. The trade
  record and the state file still store floats. A fixed sequence pins the
  published cents to the float path, and a book that adds the already-rounded
  net publishes a different balance.
- **Stage C (next):** `LivePosition` / `RiskEngine` sizing math.
- **Stage D:** delete the remaining float money paths; enforce with a lint rule.
Do **not** land C and D in one commit, and do not change a live order price
in either.

### ✅ 3. Tick/lot-size validation + price rounding — DONE
- Entry orders now validate quantity against the venue's `limits.amount.min` and
  notional against `limits.cost.min` (`_validate_order_limits`) **before**
  submission — a sub-minimum order is a clean audited BLOCK instead of an
  exchange rejection.
- SL/TP prices are rounded onto the symbol's tick grid via ccxt's own
  `price_to_precision` (`_round_price_to_market`), replacing the decimal-places
  heuristic; the heuristic remains as a graceful fallback when market data is
  unavailable.
- Covered by 2 new tests in `tests/test_order_idempotency.py` (7 total).

---

## Tier 2 — Reliability & operations

4. **Cancellation safety.** Tie any order cancel to the same `clientOid` so a
   cancelled/timed-out coroutine can't orphan an exchange-side order.
5. **Observability.** Add Prometheus-style counters/histograms: orders
   placed/filled/rejected, risk-check rejection reasons, LLM latency + cost,
   circuit-breaker trips. `bot/core/system_health.py` is the natural seam.
6. **Tamper-evident trade ledger.** Route every live state transition through the
   existing `bot/utils/audit_chain.py` hash chain.
7. **Kill-switch reachability.** Ensure `/halt` and the circuit breaker can fire
   even under a saturated event loop (dedicated watchdog task or signal handler).

---

## Tier 3 — Architecture & quality

8. **CI now.** 1,026 test functions exist but nothing runs them automatically.
   Add a GitHub Actions workflow running `pytest`, `ruff`, `mypy`, `bandit`, and
   `pip-audit` (all already in dev deps) with a coverage gate. Highest ROI item.
9. **Property-based risk-engine tests (Hypothesis).** Generate random
   `TradeIdea`s and assert invariants: no input yields APPROVED with a failing
   sub-check; any raised exception rejects; `SL == entry` always rejects. Locks
   the fail-closed contract against regressions.
10. **Pin `fastapi` / `uvicorn` exactly** (currently floating `>=` while the rest
    is pinned) and run `pip-audit` against `requirements.lock` in CI.
11. **Typed config at the boundary.** Validate all env vars through a Pydantic
    `Settings` model at startup (fail fast on a bad `LLM_DAILY_BUDGET_USD`,
    malformed chat-id list, etc.) instead of scattered `os.getenv` casts.

---

## Tier 4 — Product / edge

12. **Fill-deviation abort.** Reject/alert when the actual fill deviates beyond a
    per-symbol budget from the intended entry, so a fast market can't fill far
    from the risk-calculated price and invalidate the R:R.
13. **Backtest realism.** Add fees, funding, and realistic slippage to the
    backtester so the strategy-eval tiers reflect tradeable performance.

---

## Carried-over low-severity items (from `AUDIT.md`)

- **LLM text rendered unescaped in Telegram** (`telegram_handler.py:732`) — replace
  the all-or-nothing HTML branch with an allowlist sanitizer.
- **Prompt-injection residual** — bounded by the numerically-powerless LLM design,
  but add a defensive prompt boundary treating externally-sourced text as untrusted.
- **PBKDF2 260k iterations** — below current OWASP guidance (~600k for SHA256);
  raise or migrate to scrypt/argon2 (`cryptography` is already a dependency).
- **CORS default `*`** — require an explicit `DASHBOARD_CORS_ORIGIN` in production.
