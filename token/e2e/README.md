# `$RCLAW` end-to-end devnet dry-run — DRAFT / DEVNET-ONLY

`devnet_dryrun.mjs` exercises the **full presale lifecycle** on Solana devnet with a
**generated near-now timeline**, so the `create → deposit → claim` paths (which are
timestamp-gated and therefore unreachable in CI) actually run end to end. It reuses the
committed `token/presale/genesis_presale.mjs` commands verbatim via a `GENESIS_CONFIG`
override — no presale logic is duplicated.

## Usage

```bash
cd token
npm install
cp .env.example .env          # devnet; never a mainnet key

npm run e2e:plan              # OFFLINE: generate the near-now config + run presale:plan
npm run e2e:dryrun            # LIVE devnet: keygen → create → liquidity → deposit → claim
```

## What it does

1. Writes a **generated** near-now config to `token/.artifacts/dryrun.genesis.config.json`
   (deposit window opens ~20s out, ~90s long; claim/TGE ~20s later). Funding mode = `mint`
   so the run is self-contained (Genesis mints the supply; no separate `token/` mint needed).
2. Runs each `presale:*` command with `GENESIS_CONFIG` pointed at that file:
   `keygen → presale:plan → presale:create → presale:liquidity → presale:deposit → presale:claim`,
   waiting for the deposit and claim windows to open between steps.
3. Writes a step-by-step `token/.artifacts/e2e-report.json` (each step's ok/exit/output tail),
   mirroring the repo's existing `e2e_report.json` style.

## Safety / robustness

- **`--dry` (`npm run e2e:plan`)** is fully offline: it generates the config and runs
  `presale:plan` against it, printing the planned sequence — no RPC, no keypair, no sends.
- **Never mainnet:** `genesis_lib` refuses any `mainnet` RPC.
- **Faucet-graceful:** if the devnet airdrop is rate-limited, it logs the address to fund
  manually (https://faucet.solana.com) and stops cleanly instead of hard-failing.
- Keys and artifacts stay gitignored (`token/.gitignore`).

See the full flow and the operational steps in
[`../presale/RUNBOOK.md`](../presale/RUNBOOK.md).
