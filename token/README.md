# `$RCLAW` token tooling — DRAFT / DEVNET-ONLY

Scripts to mint and verify the **`$RCLAW`** SPL **Token-2022** on Solana **devnet**, exactly as
specified in [`docs/TOKEN_ROADMAP.md`](../docs/TOKEN_ROADMAP.md): 1,000,000,000 fixed supply,
9 decimals, in-mint metadata, **freeze authority null**, **mint authority revoked** after mint.

> ⚠️ **This is draft tooling for devnet.** It refuses to run against mainnet. A real launch is
> gated behind legal review + a smart-contract audit (roadmap §10–§11). All parameters in
> `config/token.config.json` are a **proposed baseline to ratify**, not final.

## Prerequisites

- Node.js ≥ 18
- `npm install` (installs `@solana/web3.js`, `@solana/spl-token`, `@solana/spl-token-metadata`)

## Usage

```bash
cd token
npm install
cp .env.example .env          # defaults to devnet; never add a mainnet key

npm run keygen                # make a throwaway devnet payer + request an airdrop
npm run create                # create mint, mint supply, revoke authorities
npm run verify                # assert supply/decimals/authorities/metadata match config
```

`create` writes a non-secret summary to `.artifacts/token.devnet.json` (gitignored) with the
mint address, ATA, and tx signatures. `verify` reads that file (or `MINT=<address>`).

## What each script does

| Script | File | Purpose |
|---|---|---|
| `npm run keygen` | `scripts/keygen.mjs` | Generate `.keys/mint-payer.json` (gitignored) + devnet airdrop |
| `npm run create` | `scripts/create_token.mjs` | Token-2022 mint + metadata, mint 1B to ATA, revoke mint authority, null freeze authority |
| `npm run verify` | `scripts/verify_token.mjs` | Re-read on-chain state and assert it matches `token.config.json` |

## Config

`config/token.config.json` — token params (name, symbol, decimals, supply, metadata URI,
authority-revocation flags). `config/rclaw-metadata.json` — the off-chain metadata JSON the
mint URI points at. Both clearly labeled draft.

## Safety notes

- `.keys/`, `.artifacts/`, and `.env` are gitignored — **no keys or secrets are committed**.
- `getConnection()` throws if `RPC_URL` contains `mainnet`.
- Nothing here holds a mainnet key, signs a mainnet tx, or requests real funds — consistent
  with RUNECLAW's non-custodial, testnet-first posture.

See the full plan, tokenomics, presale mechanics, and Guardrails in
[`docs/TOKEN_ROADMAP.md`](../docs/TOKEN_ROADMAP.md).
