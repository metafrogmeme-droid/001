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
vesting, **60% of raise → Raydium liquidity**, LP locked 12 months.

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

npm run presale:plan             # OFFLINE preview: prints every derived on-chain param
npm run presale:create           # initializeV2 (genesis account) + addPresaleBucketV2
npm run presale:deposit -- --amount 1   # depositPresaleV2 during the deposit window
npm run presale:claim            # claimPresaleV2 once the claim window opens (post-TGE)
```

What the script does, mapped to the SDK:

1. **`presale:plan`** — pure offline derivation via `createTimeAbsoluteCondition` /
   `createClaimSchedule`; prints fixed price (`allocation / hardCap`), lamport caps,
   per-wallet min/max, deposit + claim windows, and the vesting schedule. No RPC, no keypair.
2. **`presale:create`** — `initializeV2` creates the genesis account (PDA via
   `findGenesisAccountV2Pda`), then `addPresaleBucketV2` configures the fixed-price presale:
   `baseTokenAllocation`, `allocationQuoteTokenCap` (= hard cap), the four time conditions,
   `minimumDepositAmount` / `depositLimit` (per-wallet floor/ceiling), and a `claimSchedule`
   (33% at TGE via `cliffAmountBps`, linear tail). Writes `token/.artifacts/presale.devnet.json`.
3. **`presale:deposit` / `presale:claim`** — `depositPresaleV2` / `claimPresaleV2` against the
   bucket recorded in the artifact.

**Funding mode** (`config.fundingMode.mode`): `mint` lets Genesis create + mint the supply
itself (self-contained demo); `transfer` reuses the mint from the `token/` tooling (set
`config.token.mint` from `token/.artifacts/token.devnet.json`). *Confirm the on-chain
`fundingMode` numeric against the Genesis program before mainnet.*

**Still operational (not in the script):**
- **Liquidity at finalize** — add a Raydium bucket via `addRaydiumCpmmBucketV2` with
  `createNeverClaimSchedule()` for a permanent LP lock (60% of raise → pool). Left as a
  deliberate manual step so LP routing is reviewed before it runs.
- **Whitelist** — the OG round uses a Merkle `allowlist` (see the SDK's `createMerkleTree`);
  wire the allowlist arg + per-deposit `proof` when the whitelist is finalized.
- **Soft-cap / refund** — Genesis fixed-price presale is "buy until cap"; soft-cap/refund is
  enforced operationally (cancel + refund) or via a min-raise extension. Confirm before launch.
- **Publish** — genesis account, bucket, mint, LP-lock proof, audit report.

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
