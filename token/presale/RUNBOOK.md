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
npm install                      # pulls @metaplex-foundation/genesis + umi
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
npm run presale:claim            # claimPresaleV2 once the claim window opens (post-TGE)
# recovery: BOTH OF THESE ARE DEAD FOR A V2 PRESALE — they refuse up front.
# withdrawPresaleV1/withdrawUnsoldPresaleV1 are V1-only and reject a V2 genesis
# account (0x2f); the SDK has no V2 equivalent. Verified on devnet 2026-07-26.
# There is NO depositor refund and NO unsold-token recovery. See the audit doc.
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
4. **`presale:liquidity`** — `addRaydiumCpmmBucketV2` with `baseTokenAllocation` = the 100M
   liquidity allocation, `lpLockSchedule` = `createNeverClaimSchedule()` (**LP locked forever**),
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
- **Soft-cap / refund semantics** — Genesis fixed-price presale is "buy until cap." The
  `withdraw`/`withdraw-unsold` commands cover depositor-cancel and operator-recover-unsold; a
  true *soft-cap-missed → auto-refund-all* still needs an operational cancel (or a min-raise
  extension). Validate the exact V2 behavior on devnet.
- **Liquidity finalize** — `presale:liquidity` adds the Raydium bucket + permanent LP lock;
  confirm the pool-creation/finalize flow end-to-end on devnet before mainnet.
- **Publish** — genesis account, bucket, mint, whitelist root, LP-lock proof, audit report.

## Path B — Smithii (fallback)

1. Open Smithii's Solana launchpad, select **devnet**.
2. Enter the values from `smithii.config.json` (caps, whitelist phase, vesting, auto-list %).
3. Confirm the exact **% of sales** platform fee in-app (placeholder in config).
4. Run the same devnet dry-run (contribute → finalize → auto-list → claim).
5. Publish the same proof artifacts.

## Post-sale checklist (both paths)

- [ ] LP burned or locked ≥ 12 months, proof link published.
- [ ] Mint + freeze authority already revoked (verified via `token` `npm run verify`).
- [ ] Treasury/team/advisor allocations on-chain-verifiable as locked.
- [ ] Claim window open and tested end-to-end.
- [ ] KPIs wired (participation, LP depth, holders) — roadmap §12.
- [ ] Disclosures + risk warnings live on the sale page (roadmap §10).

## Where these numbers come from

Every value traces to [`docs/TOKEN_ROADMAP.md`](../../docs/TOKEN_ROADMAP.md) §4 (allocation),
§5 (presale mechanics), §7 (liquidity). Change them there first, then mirror into these config
files, so the roadmap stays the single source of truth.
