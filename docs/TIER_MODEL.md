# $RCLAW Tier Model — Stake × Standing

**A two-axis design for holder tiers: capital opens the door, behaviour decides the floor.**

> **Proposed design, not a commitment.** Nothing here is financial advice or a solicitation.
> Every weight, threshold, and entitlement below is a **baseline to ratify** (§9). The token
> remains a gated "◆ Vision" item behind the Guardrails in
> [`TOKEN_ROADMAP.md`](./TOKEN_ROADMAP.md) §10–§11.

---

## 1. Why change

The shipped gate (`bot/token/tier_gate.py`) is the generic one: `staked ≥ 10,000 → pro`,
`≥ 100,000 → elite`. It has three measurable problems.

**Plutocracy.** Across a sample holder set, linear thresholds give a 2,000,000-token whale
**87.7%** of tier weight and a 25,000-token target user **1.1%**.

**Price drift.** Thresholds are denominated in *tokens*, so their real cost tracks price:

| Token price | Cost of Pro (10k) | Cost of Elite (100k) |
|---|---:|---:|
| 0.00004 SOL (listing) | 0.40 SOL | 4 SOL |
| 0.0004 SOL (10×) | 4 SOL | 40 SOL |
| 0.001 SOL (25×) | 10 SOL | 100 SOL |

A fixed threshold gets **25× harder to reach exactly as the token succeeds**, locking out
everyone who arrives later — the opposite of what a growing platform wants.

**It rewards nothing the product is about.** Buy 100k, get Elite, never trade, dump. RUNECLAW
is "Governed by Discipline"; the tier model currently cannot tell a disciplined operator from
a parked wallet.

---

## 2. The model

```
tier_weight = √(staked) × lock_multiplier × standing
```

Each term fixes one of the problems above.

| Term | Range | Fixes | Source |
|---|---|---|---|
| `√(staked)` | — | Whale share 87.7% → **66.0%** | on-chain (`rclaw_staking`) |
| `lock_multiplier` | 1.0 – 2.5 | Mercenary capital | on-chain (lock duration) |
| `standing` | 0.2 – 2.0 | Rewards discipline; non-transferable | platform data (§4) |

### Lock multiplier

```
lock_multiplier = min(1.0 + 1.5 × (lock_months / 24), 2.5)
```

| Lock | 0 mo | 3 mo | 6 mo | 12 mo | 24 mo |
|---|---|---|---|---|---|
| Multiplier | 1.000 | 1.188 | 1.375 | 1.750 | 2.500 |

Unlocking early forfeits the multiplier immediately (weight recomputes on the next read); it
does not forfeit principal — staking stays non-custodial.

---

## 3. Why `√` rather than linear

| Holder | Tokens | Linear share | √ share |
|---|---:|---:|---:|
| Small | 5,000 | 0.2% | **3.3%** |
| Target | 25,000 | 1.1% | **7.4%** |
| Large | 250,000 | 11.0% | **23.3%** |
| Whale | 2,000,000 | **87.7%** | **66.0%** |

`√` compresses without erasing — a whale still leads, which is correct, but a committed
mid-sized holder is no longer rounding error. Optional refinement (§9): cap the `√(staked)`
term at a percentile so the top of the book cannot run away.

---

## 4. Standing — the earned axis

**Non-transferable.** Standing attaches to the account, never the tokens, and cannot be
bought, sold, or moved with a transfer. Stake buys access to the ladder; standing decides how
far up it goes.

```
raw      = 0.35·discipline + 0.25·track_record + 0.20·arena + 0.20·tenure
standing = 0.2 + 1.8 × raw          # 0.2 … 2.0, default 1.0 for a new account
```

| Input | Weight | What it measures | Where it comes from |
|---|---:|---|---|
| **discipline** | 0.35 | Share of trade decisions that respected the risk engine — no overrides, stops honoured, no circuit-breaker trips | `bot/risk/` verdicts (`RiskVerdict.REJECTED`, failed-check list) |
| **track_record** | 0.25 | **Risk-adjusted** verified performance — never raw PnL | `bot/proofofpnl/` (`assemble.py`, `csf.py`, anchored records) |
| **arena** | 0.20 | Season percentile, streaks, badges | `app/lib/arena.js`, `arena_seasons.js`, `arena_streaks.js` |
| **tenure** | 0.20 | Active weeks, decaying | account activity |

**Deliberately risk-adjusted, not profit-ranked.** Standing must not become "whoever gambled
biggest wins," which would contradict the risk-first posture and invite reckless behaviour to
farm tier. A blown-up account with a lucky month should score *worse* than a flat, disciplined
one.

**Decay.** Standing decays toward the 0.2 floor with inactivity, half-life ≈ 90 days. Tier is
something you keep earning.

---

## 5. Tier assignment — relative, not absolute

Rank by `tier_weight` among **eligible stakers** (those meeting a small absolute floor, e.g.
1,000 tokens, so zero-stake accounts cannot occupy slots):

| Tier | Band |
|---|---|
| **Elite** | Top 5% of eligible weight |
| **Pro** | Top 25% |
| **Basic** | Everyone else, including all non-stakers |

This is immune to price drift — the ladder re-scales itself as the token moves. The trade,
stated plainly: a user cannot know their exact requirement in advance, and the bands are
zero-sum. Publish the current cut-offs live so it is never opaque.

---

## 6. Entitlements — meter compute, not booleans

A tier should grant **compute**, not flip a feature flag. Compute is the real scarce resource
(LLM calls, scans, backtests), so metering it is economically honest: the token pays for the
thing it unlocks, and heavy users fund the infrastructure.

| Entitlement (monthly) | Basic | Pro | Elite |
|---|---:|---:|---:|
| Deep scans | 30 | 300 | 1,500 |
| Concurrent agents | 1 | 3 | 8 |
| Backtest hours | 2 | 20 | 100 |
| Premium scan modes (`/scalp` `/intraday` `/swing`) | — | ✓ | ✓ |
| Analysis queue | standard | fast | priority |

The existing LLM token optimizer (semantic cache, tiered pipeline, batching — up to 70%
savings) and the zero-cost provider options make these credits cheap to honour.

### What is deliberately NOT tier-gated

**Live trading limits, position size, and leverage.** Gating those behind a token reads as
selling access to financial services and cuts directly against the utility-not-investment
framing in `TOKEN_ROADMAP.md` §10. Risk limits stay tied to **KYC/compliance tiers**, never to
holdings. Gate *analysis*, never *risk capacity*.

---

## 7. Worked examples

| Persona | Stake | Lock | Standing | Weight |
|---|---:|---:|---:|---:|
| Whale, parked, no history | 2,000,000 | 0 mo | 0.20 | 282.8 |
| Whale, locked, average | 2,000,000 | 12 mo | 1.10 | 2,722.4 |
| Committed user, strong | 25,000 | 12 mo | 1.70 | **470.4** |
| Small holder, exemplary | 5,000 | 24 mo | 1.92 | **339.4** |
| Holder who overrides the engine | 100,000 | 0 mo | 0.66 | 208.7 |
| Small holder, new, no lock | 5,000 | 0 mo | 0.59 | 41.7 |

The outcomes that matter:

- A **parked whale (282.8)** ranks **below a committed 25k user (470.4)** — impossible today.
- A **5k holder with exemplary standing (339.4)** outranks the parked whale.
- Someone holding **100k who overrides the risk engine (208.7)** ranks below a disciplined 5k
  holder. Discipline is load-bearing, not decorative.
- An engaged, locked whale still leads (2,722.4) — capital should count, and it does.

---

## 8. Implementation plan

> **Corrected against the code, 2026-08-23.** The first draft of this section was written
> from the design rather than from the repository, and two of its five phases described work
> that is either already done or does nothing. Both corrections are below, in place, because
> a plan that is wrong about the starting state is worse than no plan — it sends someone to
> rewrite a wire layout that is already correct.

### Phase A — `√` weighting — **does nothing on its own**

The original text read: *"replace threshold comparison with `√(staked)`, keep absolute bands
initially… Zero migration,"* under a heading promising each phase ships value alone.

It ships **no** value. `√` is monotonic, so `√(staked) ≥ √(threshold)` is the same predicate
as `staked ≥ threshold` for every non-negative input. Verdicts are byte-identical at every
holding:

| Staked | Linear | `√`, absolute bands |
|---:|---|---|
| 9,999 | basic | basic |
| 10,000 | pro | pro |
| 99,999 | pro | pro |
| 2,000,000 | elite | elite |

**`√` only changes an outcome where weights are compared *between* holders — which is
Phase D.** Plutocracy (§1) is a statement about *shares of a total*, and no absolute band
computes a share. So the sequencing advice in §11 was exactly backwards: A+B do not fix
plutocracy; D does, and A is the arithmetic D needs.

What Phase A should actually deliver, and what this repo has, is the **seam**:
`bot/token/tier_weight.py` — `tier_weight()`, `lock_multiplier()`, `tier_for_weight()` as
pure functions, wired into `tier_gate.check_user` behind `RCLAW_TIER_WEIGHT_ENABLED`
(default **off**). Weight is computed and available; it does not decide anything until the
flag is set, and `tests/test_tier_weight.py` pins the no-op above so nobody ships `√` again
believing it did something.

### Phase B — Lock multiplier — **the field already exists, at different offsets**

The original text said to *add* `lock_until: i64` to `StakeAccount`, keeping `owner@8`,
`mint@40`, `amount@72`, `staked_at@80`, `bump@88` and appending `lock_until@89` with
`SPACE 81 → 89`.

Every one of those offsets is wrong, and the real layout already carries the field:

| | Doc claimed | `programs/rclaw_staking/src/lib.rs::layout` |
|---|---|---|
| version | — | **8** |
| owner | 8 | **9** |
| mint | 40 | **41** |
| amount | 72 | **73** |
| staked_at | 80 | **81** |
| lock field | add at 89 | `unlock_at` at **89**, already present |
| bump | 88 | **97** |
| SPACE | 81 → 89 | **90** |

Following the original instruction would have moved `amount` from 73 to 72 and broken
`tier_gate.py`'s reader — the precise failure it congratulated itself on avoiding.

Already shipped, and not worth rebuilding:

- `unlock_at: i64` at offset 89, with a leading `version` byte so any future layout change is
  *detectable* rather than silently misparsed.
- The lock is **monotonic**: `stake()` does `sa.unlock_at = sa.unlock_at.max(unlock)`, so a
  top-up cannot shorten an existing lock. Proven by
  `programs/rclaw_staking/tests/attack.rs::restake_extends_but_never_shortens_the_lock`, and
  held as invariant **I4 MONOTONIC** across the randomized run in `tests/solvency.rs`.
- `staked_of` already **excludes expired locks entirely** (`if unlock_at <= now: continue`) —
  stricter than this model's "unlocking forfeits the multiplier", because a liquid position
  can be rotated between wallets and confer the tier again.
- The Rust and Python offsets are cross-checked in both directions
  (`layout_tests::borsh_offsets_match_the_python_gate`, and
  `test_python_offsets_are_read_from_the_rust_source_not_a_fixture`).

**What is genuinely missing** is a *variable* lock. `LOCKUP_SECONDS` is a fixed 30 days
chosen at stake time, so today every position's remaining lock is ≤ 30 days and the
multiplier ceiling is `1 + 1.5 × (1/24) ≈ 1.06` — the 2.5× in §2 is unreachable. Phase B is
therefore: add a caller-chosen duration to `stake()` (bounded, monotonic, defaulting to the
current 30 days), not a new field. `lock_multiplier()` already reads seconds-remaining, so it
needs no change when that lands.

### Phase C — Standing scorer (off-chain)
New `bot/token/standing.py`: pure functions over the §4 inputs, returning `0.2–2.0`.
Persist a per-account daily snapshot so the score is auditable and cannot be recomputed
retroactively to someone's advantage. Feature-flagged `STANDING_ENABLED=false`.

### Phase D — Relative bands
Snapshot all eligible stakers (`getProgramAccounts`), compute percentile cut-offs on a fixed
cadence (daily), cache, and publish the live cut-offs. Falls back to absolute bands if the
snapshot is unavailable — **fail-open, matching the existing gate's posture**.

### Phase E — Compute credits
Replace boolean feature checks with a credit ledger; meter deep scans, agent slots, and
backtest hours. Wire into the existing per-user gateway.

---

## 9. Open decisions

- Component weights (0.35 / 0.25 / 0.20 / 0.20) and the 0.2–2.0 standing range.
- Lock ceiling (24 months) and maximum multiplier (2.5×).
- Percentile bands (5% / 25%) and the eligibility floor (1,000 tokens).
- Whether to cap the `√(staked)` term at a percentile (§3).
- Decay half-life (90 days).
- Exact credit quantities per tier (§6) — must be costed against real LLM/GPU spend.
- Standing bootstrapping: what a brand-new account scores before it has history (proposed 1.0,
  the neutral midpoint, so newcomers are neither punished nor advantaged).

## 10. Anti-gaming

| Vector | Mitigation |
|---|---|
| Sybil — split stake across wallets | Tier is assigned **per account** and weight never merges across wallets, so splitting strictly lowers your best account's weight (√100k = 316.2 → √50k = 223.6 each). Combined with **superlinear entitlements** (§6: Pro→Elite is 5× the scans, not 2×), concentrating always beats splitting. See the caveat below — this is a design constraint to hold, not a free property. |
| Buy standing | Non-transferable; attaches to account, not tokens. |
| Wash-trade the Arena to farm standing | Risk-adjusted scoring; season percentile, not raw volume. |
| Lock, get tier, unlock immediately | Multiplier recomputes on read; unlock forfeits it at once. |
| Farm tier then go dormant | 90-day decay half-life. |
| Retroactive score manipulation | Daily immutable snapshots; scores are not recomputed from current data. |

**Sybil caveat — stated plainly.** `√` is concave, so *summed across wallets* splitting
**increases** total weight: `2·√50k = 447.2 > √100k = 316.2`. That sum is only harmless
because nothing in this model ever adds weight across accounts — every entitlement is granted
per account, from that account's own tier. Two properties must therefore hold, or splitting
becomes profitable:

1. **Entitlements stay superlinear in tier.** If Pro were ever set at half of Elite rather
   than a fifth, two Pro accounts would equal one Elite and the sybil defence evaporates.
   Any future re-costing of §6 must re-check this.
2. **Standing does not transfer or duplicate.** Each split wallet starts at the neutral 1.0
   and must earn its own history, so splitting also *divides* the earned axis.

The residual, unsolved by tier design: **Basic requires no stake at all**, so free accounts
are limited by account-creation controls, not by this model. Sybil resistance at the Basic
tier is an abuse-prevention problem and must be handled there.

## 11. Complexity is a cost

Every multiplier is something a user must understand and the team must explain and defend.

> **This paragraph used to say** *"Shipping A+B alone (both pure on-chain arithmetic) already
> fixes plutocracy and mercenary capital and needs no new data pipeline."* It does not, and
> §8 now shows why: `√` under absolute bands is the identity, so **A fixes nothing without
> D**, and B's chain work is already done except for a variable lock duration. The cheapest
> real fix for plutocracy is **A + D** — the weight function plus relative bands — and D is
> the one with the operational cost (a daily snapshot over `getProgramAccounts`, a cache, and
> published cut-offs).

Standing is the differentiated part, and it is also the part that must be *trustworthy*
before it gates anything — ship it dark, publish scores read-only, and only let it affect
tiers once the numbers survive scrutiny. The same applies one step earlier:
`RCLAW_TIER_WEIGHT_ENABLED` exists so weight can be computed and inspected before it is ever
allowed to decide.
