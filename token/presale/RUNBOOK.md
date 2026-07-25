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

## Path A — Metaplex Genesis (recommended)

1. **Fund a devnet operator wallet** (see `token/` keygen airdrop).
2. **Create the sale** via Metaplex Genesis SDK/UI using `metaplex-genesis.config.json`:
   fixed-price presale, quote = SOL, caps + per-wallet limits, the two rounds, and the
   deposit/claim timeline. (Wire the SDK call here once the team picks the exact Genesis
   version; parameters map 1:1 to the config fields.)
3. **Configure vesting** — 33% at TGE, linear over 2 months.
4. **Dry-run on devnet:** contribute from a few test wallets across both rounds; confirm caps,
   whitelist gating, refund-if-soft-cap-missed, and the claim window all behave.
5. **Finalize:** on hard cap or deposit-end, finalize the sale → route **60% of raised SOL +
   100M tokens** into a Raydium pool → **burn/lock LP** → open the claim window.
6. **Publish:** mint address, sale address, LP-lock proof, audit report.

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
