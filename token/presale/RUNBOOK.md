# `$RCLAW` presale — setup runbook (DRAFT / DEVNET)

Operational steps for standing up the `$RCLAW` presale. Recommended primary venue:
**Metaplex Genesis**; fallback: **Smithii**. Full rationale and the venue comparison are in
[`docs/TOKEN_ROADMAP.md` §6](../../docs/TOKEN_ROADMAP.md#6-launch-venue-comparison--recommendation).

> ⚠️ Devnet dry-run only. **Do not run the presale on mainnet** until Phase 0 Guardrails are
> cleared: legal review per jurisdiction, smart-contract/presale audit, disclosures published
> (roadmap §8, §10–§11). Config values are a proposed baseline to ratify (§13).

## Config files

| File | Venue | Notes |
|---|---|---|
| `metaplex-genesis.config.json` | Metaplex Genesis (primary) | Fixed-price presale + TGE, on-chain/trustless |
| `smithii.config.json` | Smithii (fallback) | No-code; ~0.1 SOL + % of sales |

Both encode the same economics: 150M presale allocation, **1,000 SOL soft / 5,000 SOL hard
cap**, 0.25–25 SOL per wallet, whitelist (48h) → public (72h), **33% TGE + 2-month linear**
vesting, **66.67% of raise → Raydium liquidity**, LP locked permanently (never-claim).

## Prerequisites

1. Mint exists on devnet — run `token/` tooling first (`npm run create`), then copy the mint
   address from `token/.artifacts/token.devnet.json` into the `token.mint` field of the chosen
   presale config.
2. Whitelist collected (wallet allowlist) for the OG round.
3. Treasury **Squads multisig** created; presale proceeds and unsold tokens flow to it.
4. Legal sign-off + audit report links ready to publish (Phase 0 exit criteria).

## Path A — Metaplex Genesis (recommended) — real SDK integration

This path is wired against the real **`@metaplex-foundation/genesis`** SDK
(`token/presale/genesis_presale.mjs`, built on Umi). Parameters are derived from
`metaplex-genesis.config.json` — edit the config, not the script.

```bash
cd token
npm ci                           # pulls @metaplex-foundation/genesis + umi, lockfile-exact
cp .env.example .env             # devnet; never a mainnet key
npm run keygen                   # devnet operator wallet + airdrop

npm run presale:plan             # OFFLINE preview: every derived param + whitelist + liquidity
npm run presale:whitelist        # build the Merkle allowlist from config.whitelist
npm run presale:create           # initializeV2 (genesis account) + addPresaleBucketV2 (+ allowlist)
npm run presale:liquidity        # addRaydiumCpmmBucketV2 with a permanent LP lock
npm run presale:allocate         # REQUIRED: buckets for the other 75% of supply. finalizeV2
                                 # refuses while ANY supply is unallocated. Refuses to run
                                 # unless every allocations.buckets[].recipient is set.
npm run presale:finalize         # REQUIRED before any deposit (deposits fail 0x2c without it).
                                 # PERMANENTLY LOCKS bucket configuration — the LP token
                                 # allocation can never be changed after this point.
npm run presale:deposit -- --amount 1   # depositPresaleV2 (auto-presents whitelist proof)
npm run presale:trigger          # triggerBehaviorsV2 — routes the 66.67% quote share to the
                                 # liquidity bucket. Permissionless: ANYONE may run it once
                                 # the deposit window closes. Required before the pool is real.
                                 # REFUSES if the realised raise would open the pool below the
                                 # presale price — see the decision point below.
npm run presale:claim            # claimPresaleV2 once the claim window opens (post-TGE)

npm run presale:verify           # READ BACK every claim the artifact makes and check it
                                 # against the chain. The artifact is a JSON file written
                                 # by the same process that sent the transactions, so it
                                 # records what that process INTENDED. Run this before
                                 # publishing any of those addresses. Exits non-zero on a
                                 # mismatch, so it can gate a publish step.
# recovery: BOTH OF THESE ARE DEAD FOR A V2 PRESALE — they refuse up front.
# withdrawPresaleV1/withdrawUnsoldPresaleV1 are V1-only and reject a V2 genesis
# account (0x2f); the SDK has no V2 equivalent. Verified on devnet 2026-07-26.
# Unsold tokens ARE recovered — by the BaseTokenRollover behavior presale:trigger
# executes, not by these commands. The DEPOSITOR refund is what does not exist
# for a presale bucket (a LaunchPool bucket has one). See the audit doc.
npm run presale:withdraw          # refuses: no V2 refund instruction exists
npm run presale:withdraw-unsold   # refuses: no V2 unsold-recovery instruction exists
```

What the script does, mapped to the SDK:

1. **`presale:plan`** — pure offline derivation via `createTimeAbsoluteCondition` /
   `createClaimSchedule` / `createNeverClaimSchedule`; prints fixed price (`allocation / hardCap`),
   lamport caps, per-wallet min/max, deposit + claim windows, vesting, the **whitelist root**,
   and the **liquidity/LP-lock** summary. No RPC, no keypair.
2. **`presale:whitelist`** — `prepareAllowlist(config.whitelist)` builds the Merkle tree and
   writes the root + per-address proofs to `token/.artifacts/allowlist.devnet.json`.
3. **`presale:create`** — `initializeV2` creates the genesis account (PDA via
   `findGenesisAccountV2Pda`), then `addPresaleBucketV2` configures the fixed-price presale:
   `baseTokenAllocation`, `allocationQuoteTokenCap` (= hard cap), the four time conditions,
   `minimumDepositAmount` / `depositLimit` (per-wallet floor/ceiling), a `claimSchedule`
   (33% at TGE via `cliffAmountBps`, linear tail), and — if a whitelist artifact exists — the
   `allowlist` (Merkle root, ends at `publicStart`). Writes `token/.artifacts/presale.devnet.json`.
4. **`presale:liquidity`** — `addRaydiumCpmmBucketV2` with `baseTokenAllocation` = the 20,001,000
   liquidity allocation (soft-cap sized — see §4/§7 of the roadmap), `lpLockSchedule` = `createNeverClaimSchedule()` (**LP locked forever**),
   and a `startCondition` at the deposit-window close.
5. **`presale:deposit` / `presale:claim`** — `depositPresaleV2` / `claimPresaleV2` against the
   bucket. During the whitelist window the depositor's Merkle `proof` is looked up and presented
   automatically.
6. **`presale:withdraw` / `presale:withdraw-unsold`** — `withdrawPresaleV1` (depositor
   cancel/refund; deposit PDA via `findPresaleDepositV2Pda`) / `withdrawUnsoldPresaleV1` (operator
   recovers unsold tokens; ATAs via `findAssociatedTokenPda`).

**Funding mode** (`config.fundingMode.mode`): `mint` lets Genesis create + mint the supply
itself (self-contained demo); `transfer` reuses the mint from the `token/` tooling (set
`config.token.mint` from `token/.artifacts/token.devnet.json`). *Confirm the on-chain
`fundingMode` numeric against the Genesis program before mainnet.*

**Confirm on devnet / before launch:**
- **Soft-cap / refund semantics** — ~~The `withdraw`/`withdraw-unsold` commands cover
  depositor-cancel and operator-recover-unsold~~ **CORRECTED — they do not, and this bullet
  said to "validate on devnet" something that was validated and came back negative.**
  Genesis fixed-price presale is "buy until cap." PROVEN on devnet 2026-07-26:
  `withdrawPresaleV1` and `withdrawUnsoldPresaleV1` are **V1-only** and reject a V2 genesis
  account with `The Genesis Account is invalid` (0x2f); the SDK ships no V2 equivalent. So
  there is **no depositor cancel and no operator recovery of unsold tokens** — a deposit that
  lands cannot be returned. Unsold supply is handled instead by the on-chain
  `unsoldRollover` end behavior (see `metaplex-genesis.config.json`), without which it is
  stranded permanently. Published terms must not promise a refund of any kind. This holds at
  the **fallback venue too** until Smithii's refund path is executed on devnet and the
  transaction published — `venue_parity.test.mjs` fails if either config sets
  `refundIfSoftCapMissed`.
- **Liquidity finalize** — `presale:liquidity` adds the Raydium bucket + permanent LP lock;
  confirm the pool-creation/finalize flow end-to-end on devnet before mainnet.
- **Publish** — genesis account, bucket, mint, whitelist root, LP-lock proof, audit report.

## Path B — Smithii (fallback)

> **The fallback is not automatically equivalent to Path A.** `smithii.config.json` used to
> claim it "mirrors the Metaplex Genesis params so the two are interchangeable" while three
> values diverged — refund promise, liquidity share of the raise, and LP lock duration — so
> which venue the operator happened to use silently changed what buyers were told. The values
> are corrected and `venue_parity.test.mjs` now checks the full key set, but two of the three
> depend on what Smithii's contract can actually do, and **nothing in this repository has read
> that contract**. Steps 3-4 below are blocking for that reason.

1. Open Smithii's Solana launchpad, select **devnet**.
2. Enter the values from `smithii.config.json` (caps, whitelist phase, vesting, auto-list %).
   If the auto-list field takes only an integer, enter **67**, not 66 — rounding up sends more
   SOL to the pool, which opens it higher and deeper, the recoverable side of the sizing
   decision (`metaplex-genesis.config.json` → `liquidity._liquidityPricing_comment`).
3. **BLOCKING — permanent LP lock.** Genesis uses `createNeverClaimSchedule()`: the LP is
   never claimable, with no expiry. Confirm Smithii can express that. If it can only offer a
   fixed duration, the fallback is a **materially different product** and must be re-ratified
   and published as such before the sale — not discovered by a holder reading the pool
   afterwards.
4. **BLOCKING — refund.** `refundIfSoftCapMissed` is `false` at both venues. Do not enable a
   refund in Smithii's UI or in published terms unless the refund has been executed on devnet
   and the transaction published, exactly as Path A requires of itself.
5. Confirm the exact **% of sales** platform fee in-app (placeholder in config).
6. Run the same devnet dry-run (contribute → finalize → auto-list → claim).
7. Publish the same proof artifacts, plus the outcome of steps 3 and 4.

## The one decision point: `presale:trigger` may refuse

`presale:trigger` reads the realised raise from the bucket
(`quoteTokenDepositTotal`) and compares the pool's opening price against the
presale price. If the pool would open **below** it, the command stops:

```
Refusing to open the pool below the presale price.
  realised raise      : 412 SOL
  pool was sized for  : 1000 SOL (liquidity.sizedForRaiseSol)
  pool opening price  : 0.4120x the presale price
```

This is not a bug and re-running will not clear it. The LP token side was fixed
when the bucket was created, so a raise short of what it was priced for opens the
pool under what buyers paid — permanently, because the LP lock is never-claim,
and there is no refund instruction for a V2 PRESALE bucket. (A LaunchPool bucket does have one — see roadmap §5, The refundable alternative. Switching is a product decision.)

**There is no clean option at this point**, and the runbook should say so rather
than imply one:

- `npm run presale:trigger -- --accept-below-presale` proceeds. Every presale
  buyer is underwater at listing, irreversibly. The override is logged.
- Not triggering leaves the raise sitting in the presale bucket, and no V2
  withdraw path exists to get it out. That is not a free wait — it is a
  different irreversible position.

So the real work is upstream: decide what you will tell depositors **before** the
sale, publish the raise level the listing price depends on
(`config.disclosures.softCapNotEnforced` states it), and hold an operational
cancel-and-refund procedure ready if the terms promise one. This guard exists to
make sure that conversation happens before the button, not after.

## Rehearse it for free, on a local validator

Devnet SOL is faucet-limited to 10 SOL per 8 hours. Rehearse against a local
validator instead — it costs nothing and can be reset as often as you like, so
there is no reason to run this sequence for the first time on the real thing.
Full instructions in [`../e2e/README.md`](../e2e/README.md).

After any rehearsal, run the program inventory:

```bash
RPC_URL=http://127.0.0.1:8899 npm run programs:inventory
```

It pulls the logs of every transaction the run produced and lists each program
that actually executed, CPIs included, failing on anything not in
`token/.program-inventory.json`. This is not the same check as `npm audit`: a
CPI target is not a package, so no dependency scanner can see it. That is how
the deposit, trigger and claim paths were found to be routing through MPL Token
Extras (`TokExjvjJ…`), third-party upgradeable bytecode that appeared nowhere in
this repository. Anything new in that list is a program you are trusting with
buyer funds — identify it and its upgrade authority before you continue.

## Post-sale checklist (both paths)

- [ ] LP burned or locked ≥ 12 months, proof link published.
- [ ] `npm run programs:inventory` clean — no unattributed program in the money path.
- [ ] Mint + freeze authority already revoked (verified via `token` `npm run verify`).
- [ ] Treasury/team/advisor allocations on-chain-verifiable as locked.
- [ ] Claim window open and tested end-to-end.
- [ ] KPIs wired (participation, LP depth, holders) — roadmap §12.
- [ ] Disclosures + risk warnings live on the sale page (roadmap §10).

## Where these numbers come from

Every value traces to [`docs/TOKEN_ROADMAP.md`](../../docs/TOKEN_ROADMAP.md) §4 (allocation),
§5 (presale mechanics), §7 (liquidity). Change them there first, then mirror into these config
files, so the roadmap stays the single source of truth.
