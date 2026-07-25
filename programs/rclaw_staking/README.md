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

## The fix is EXECUTED, not just reasoned about

`tests/attack.rs` runs the **real program in-process** via `solana-program-test`'s
`processor!()` — no SBF toolchain, no validator, no network required:

```bash
cargo test -p rclaw_staking --test attack     # 4 passed
```

The headline test, `mint_confusion_attack_is_rejected`, performs the exact attack the
audit found: an honest staker funds the real vault, then the attacker stakes a worthless
self-minted token and tries to redeem that stake record against the **real** vault.
Pre-fix this drained the vault. It is now rejected by the runtime:

```
Program log: Instruction: Unstake
AnchorError caused by account: stake_account.
Error Code: ConstraintSeeds. Error Number: 2006.
Program failed: custom program error: 0x7d6
```

That rejection comes specifically from the fix — the stake PDA is seeded with the mint,
so the attacker's record cannot match the seeds derived for another token. The test then
asserts the real vault balance is **unchanged**. The other tests cover the stake/unstake
round-trip (with real balance assertions) and over-withdrawal (rejected with
`InsufficientStake`), and both negative cases fail for the *correct* reason rather than
incidentally.

**What this still does NOT prove:** it is not an audit, it does not exercise the SBF/BPF
runtime (compute budget, serialization limits), and it has never run against devnet or
mainnet. `release.anza.xyz` and GitHub are blocked by this environment's egress policy, so
the Solana/Anchor CLIs cannot be installed here — `anchor build`, `anchor test`, and any
devnet run must happen in an environment with network access.

## Setting `PINNED_MINT`

The pin is a **build-time setting**, not a hardcoded literal:

```bash
RCLAW_PINNED_MINT=<base58 mint address> anchor build
```

It is deliberately not baked into the source because **the `$RCLAW` mint does not
exist yet** — `token/` has never run against a live cluster (devnet is blocked in the
authoring environment), so `token/.artifacts/token.devnet.json` has never been produced.
Committing a placeholder pubkey into a security constant would either brick staking or
give false assurance, so `option_env!` makes the pin a real, enforced deployment step
while keeping the source honest about what isn't known yet.

| State | Behaviour |
|---|---|
| **Unset** (default) | Any mint accepted. Vaults and stake records are still per-mint, so cross-mint drain is impossible — but a user can stake a worthless token, so the off-chain gate must filter on mint (`bot/token/tier_gate.py` does, via `RCLAW_MINT`). |
| **Set** | `stake` rejects every other mint with `UnexpectedMint` (error 6005). |
| **Set to junk** | Fails closed with `InvalidPinnedMint` — a typo'd pin never silently accepts everything. |

**Verified end-to-end**, not assumed. Built with a pin, the runtime refuses a
non-matching mint:

```
AnchorError thrown in programs/rclaw_staking/src/lib.rs:76.
Error Code: UnexpectedMint. Error Number: 6005.
Error Message: Mint does not match the pinned $RCLAW mint.
```

Because the integration tests mint their own throwaway tokens, they skip themselves
under a pinned build (with an explanatory message) rather than showing red for a build
that is behaving correctly. Both modes are green:

```bash
cargo test -p rclaw_staking                       # 4 unit + 4 integration pass
RCLAW_PINNED_MINT=<addr> cargo test -p rclaw_staking --test attack   # 4 pass (3 skip)
```

Set the pin, and set `RCLAW_MINT` for the bot gate, before any deployment where tiers
carry value.

## Building / testing

```bash
cargo check -p rclaw_staking     # host type-check
cargo test  -p rclaw_staking     # unit + in-process integration tests (no toolchain needed)

npm install                      # root package.json — Anchor TS test toolchain
npm run typecheck                # tsc over programs/*/tests/*.ts
anchor build                     # needs the Anchor + Solana CLIs
anchor keys sync                 # replace the placeholder declare_id!
anchor test                      # runs the TS spec via ts-mocha
```

**`anchor test` tooling is now committed.** The root `package.json` + `tsconfig.json`
supply `@coral-xyz/anchor`, `@solana/spl-token`, `ts-mocha`, `mocha`, `chai`, and
`typescript`, and `Anchor.toml`'s `test` script points at
`programs/rclaw_staking/tests/**/*.ts`. `npm run typecheck` passes.

Two things to know about the TS spec:
- It needs the **Anchor + Solana CLIs** (unavailable in the authoring environment —
  `release.anza.xyz` is blocked), so it has not been executed here. The Rust
  integration tests in `tests/attack.rs` are the ones that actually run today.
- Its program handle is untyped (`const program: any`) because the generated IDL type
  at `target/types/rclaw_staking` only exists after `anchor build`. Once you have built,
  you can import `RclawStaking` from there and restore full typing.

## Known limitations

- **Unaudited** (above) — the binding constraint.
- No lock-up or cooldown: `unstake` is immediate by design (non-custodial).
- No reward accrual — this is an access-tier vault, not a yield product.
- `declare_id!` is a placeholder until `anchor keys sync` runs.
