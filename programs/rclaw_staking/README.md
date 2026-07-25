# `rclaw_staking` — ⚠️ UNAUDITED / DO NOT DEPLOY

> ## 🚨 DO NOT DEPLOY THIS PROGRAM TO ANY CLUSTER HOLDING REAL VALUE
>
> This program is **unaudited**. An earlier revision (merged in PR #801) shipped a
> **critical vault-drain vulnerability**, found by an adversarial review:
> `StakeAccount` recorded no mint and `unstake` accepted an arbitrary `mint`, while a
> single global `["vault"]` authority owned every per-mint vault. An attacker could
> mint a worthless SPL token, `stake` it, then `unstake` the same amount against the
> **real $RCLAW vault** — draining it and locking honest stakers out.
>
> That specific hole is fixed (see below), but **"one critical bug found and fixed"
> is not the same as "audited."** A real deployment stays gated behind the roadmap's
> Phase 0 Guardrails: legal review **and** an independent smart-contract audit.
> See [`docs/TOKEN_ROADMAP.md`](../../docs/TOKEN_ROADMAP.md) §8, §10, §11.

## What the fix changed

| Before (vulnerable) | After |
|---|---|
| `StakeAccount { owner, amount, staked_at, bump }` — **no mint** | `StakeAccount { owner, mint, amount, staked_at, bump }` |
| Stake PDA `["stake", owner]` — one balance across all mints | `["stake", owner, mint]` — **per-mint** accounting |
| Vault authority `["vault"]` — **shared by every mint** | `["vault", mint]` — **mint-scoped**, isolated vaults |
| `Unstake` checked only `has_one = owner` | also `has_one = mint` |
| No way to pin the canonical mint | `PINNED_MINT` constant + `UnexpectedMint` error |
| Legacy SPL Token only — **couldn't stake the real Token-2022 $RCLAW** | `token_interface` + `transfer_checked` (Token-2022 **and** legacy) |

Defense in depth: even with `PINNED_MINT = None`, each mint now has an isolated vault
and isolated stake records, so cross-mint redemption is impossible. Set `PINNED_MINT`
before any deployment where tiers carry value.

## Account layout (keep in sync with `bot/token/tier_gate.py`)

```
8 disc | owner @8 (32) | mint @40 (32) | amount @72 (u64 LE) | staked_at @80 (i64) | bump @88
```

The Python tier gate reads stake via `getProgramAccounts` with a `memcmp` on **owner @8**
and — when `RCLAW_MINT` is set — **mint @40**, then reads `amount` at **offset 72**.
Changing this layout without updating `tier_gate.py` silently breaks tier resolution;
`tests/test_token_tier_gate.py` locks both the offsets and the mint filter.

## Building / testing

```bash
cargo check -p rclaw_staking     # host type-check (works today)
anchor build                     # needs the Anchor + Solana CLIs
anchor keys sync                 # replace the placeholder declare_id!
```

**`anchor test` is not runnable as committed:** the TS spec lives at
`programs/rclaw_staking/tests/`, but there is no root `tsconfig.json` or `package.json`
providing `@coral-xyz/anchor`, `@solana/spl-token`, `ts-mocha`, and `chai`. Add those
before relying on the spec — it is currently an executable *specification*, not a
passing suite. (Stated plainly rather than left as a silently broken glob in `Anchor.toml`.)

## Known limitations

- **Unaudited** (above) — the binding constraint.
- No lock-up or cooldown: `unstake` is immediate by design (non-custodial).
- No reward accrual — this is an access-tier vault, not a yield product.
- `declare_id!` is a placeholder until `anchor keys sync` runs.
