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
| `StakeAccount { owner, amount, staked_at, bump }` — **no mint** | `StakeAccount { version, owner, mint, amount, staked_at, unlock_at, bump }` |
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
8 disc | version @8 (u8) | owner @9 (32) | mint @41 (32) | amount @73 (u64 LE)
       | staked_at @81 (i64) | unlock_at @89 (i64) | bump @97
       | + StakeAccount::RESERVED (64) zeroed bytes of growth headroom
```

The Python tier gate reads stake via `getProgramAccounts` with a `memcmp` on **owner @9**
and — when `RCLAW_MINT` is set — **mint @41**, then reads `amount` at **offset 73**. It also
checks `version @8` and skips any record whose `unlock_at @89` has already passed.
Changing this layout without updating `tier_gate.py` silently breaks tier resolution;
These offsets are machine-checked on both sides, and that claim is now true in
both directions — it was not before. `layout_tests::borsh_offsets_match_the_python_gate`
asserts the offsets against the real Borsh encoding, which protects the Rust side.
But the Python test used to assert against a Python-built fixture, so editing
`bot/token/tier_gate.py` alone changed nothing that any test could see: CI stayed
green while the gate read the wrong bytes off the chain.
`tests/test_token_tier_gate.py::test_python_offsets_are_read_from_the_rust_source_not_a_fixture`
now parses `pub mod layout` straight out of `lib.rs` and compares it to the gate's
constants. Changing either side alone fails CI.

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

**What this still does NOT prove:** it is not an audit, and the program has never run
against devnet or mainnet.

It no longer excuses itself for skipping the SBF runtime. That paragraph used to read
"it does not exercise the SBF/BPF runtime … `release.anza.xyz` and GitHub are blocked
by this environment's egress policy, so the Solana/Anchor CLIs cannot be installed
here." The Solana CLI **is** installed (1.18.26) and the SBF runtime **is** exercised —
see `tests/bpf_smoke.mjs` below, which deploys the real `.so` to a local validator and
reports measured compute. Only the **Anchor** CLI is still missing, which is why
`anchor build`, `anchor test` and `anchor idl init` remain undone; it needs a source
build this container has no disk for.

The correction is left visible rather than quietly overwritten, because a stale "we
cannot check that here" is worse than no note at all: it reads as a reason to stop
looking, and in this audit every gap excused as environmental turned out to be hiding
a real defect.

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

npm ci                           # root package.json — Anchor TS test toolchain
npm run typecheck                # tsc over programs/*/tests/*.ts
anchor build                     # needs the Anchor + Solana CLIs
anchor keys sync                 # keeps declare_id! and Anchor.toml in step
anchor test                      # runs the TS spec via ts-mocha
```

### What runs as real SBF bytecode: `tests/bpf_smoke.mjs`

`cargo test` executes this program **natively and in-process** via
`solana-program-test`. That proves the logic and it is how the vault-drain fix and
the solvency invariants are pinned — but it is a different claim from "the deployed
artifact works". Native execution cannot surface a wrong instruction discriminator,
an account list the SBF loader rejects, a syscall that behaves differently under the
runtime, or whether the `.so` even loads. In this audit, *every* path that had never
been executed for real turned out to contain something.

So `tests/bpf_smoke.mjs` deploys `target/deploy/rclaw_staking.so` to a local
validator and drives it:

```bash
solana-test-validator --ledger /tmp/ledger --reset --quiet &
solana program deploy target/deploy/rclaw_staking.so
node programs/rclaw_staking/tests/bpf_smoke.mjs <PROGRAM_ID>
```

Verified 2026-07-26 against `6yGc2n7vZyp7nvJJ8uXEdy56P1UT8Ma4En26bTtBrJhW`:

| Check | Result |
|---|---|
| First `stake` (creates stake record + vault ATA) | 57,124 CU of 200,000 |
| Second `stake` (steady state) | 28,172 CU |
| `StakeAccount` at the tier-gate offsets | 162 bytes, version 1, `amount@73` correct |
| `unstake` inside the lock window | refused, `StillLocked` (6007) |
| Vault balance after the refused `unstake` | unchanged |

The `unstake` row is the one worth reading twice. The lock is the entire reason a
tier costs something to hold — without it a position is a live spot balance that can
be unstaked and re-staked to another wallet in the same slot, serving unlimited users
by rotation. Until this ran, that guard had only ever executed natively.

The check requires the **specific** error code, not merely a failure. Every
account-resolution mistake in the harness also produces "a failure", and a test that
accepts any failure would report a broken harness as a working lock. Both directions
are mutation-tested: dropping `token_program` from the account list yields
`AccountNotEnoughKeys` (3005) and the run **fails** rather than passing; and a
deliberately lockless build, deployed under its own program id, unstakes successfully
and drains the vault by exactly the requested amount — the run fails on both the
error-code check and the balance check independently.

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

## Upgrade authority

**Upgradeable by default.** Whoever holds the BPF upgrade authority can replace this
bytecode and thereby sign for every `["vault", mint]` PDA — it is the trust root for
every staked lamport, and it outranks every constraint in this source file. Move it to
a Squads multisig **before the vault accepts its first deposit**, not after; the window
between deploy and transfer is the entire exposure. See `docs/TOKEN_ROADMAP.md` §11.

## Known limitations

- **Unaudited** (above) — the binding constraint.
- `unstake` enforces the record's `unlock_at` so a tier costs something to hold. `stake`
  writes `LOCKUP_SECONDS` (**30 days**, ratified 2026-07-26); `stake_for(amount,
  lock_seconds)` lets a depositor choose a longer one, bounded to
  `[LOCKUP_SECONDS, MAX_LOCK_SECONDS]` (24 months). Both are tokenomics parameters, not
  security constants. The lock only ever **extends** — a later `stake` cannot shorten a
  long `stake_for` lock, and nothing in the program can bring an unlock forward, which is
  why the ceiling is enforced rather than left open.
- No reward accrual — this is an access-tier vault, not a yield product.
- `declare_id!` now carries the real program id
  `6yGc2n7vZyp7nvJJ8uXEdy56P1UT8Ma4En26bTtBrJhW` (`Anchor.toml` synced to match); the
  keypair is not in git. CI still rejects the Anchor placeholder. Proven by execution:
  a mismatch is not "deployable but misnamed" — Anchor aborts with
  `DeclaredProgramIdMismatch` (4100) and *every* instruction fails.
- No migration or rescue instruction. `StakeAccount` carries a `version` byte and 64
  reserved bytes so a future field can be added in place, but nothing can yet rewrite an
  existing record — a layout change still requires landing before value is at stake.
