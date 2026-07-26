# `$RCLAW` end-to-end devnet dry-run — DRAFT / DEVNET-ONLY

`devnet_dryrun.mjs` exercises the **full presale lifecycle** on Solana devnet with a
**generated near-now timeline**, so the `create → deposit → claim` paths (which are
timestamp-gated and therefore unreachable in CI) actually run end to end. It reuses the
committed `token/presale/genesis_presale.mjs` commands verbatim via a `GENESIS_CONFIG`
override — no presale logic is duplicated.

## Usage

```bash
cd token
npm ci
cp .env.example .env          # devnet; never a mainnet key

npm run e2e:plan              # OFFLINE: generate the near-now config + run presale:plan
npm run e2e:dryrun            # LIVE devnet: keygen → create → liquidity → deposit → claim
```

### Run it for free against a local validator (preferred)

Devnet SOL is faucet-limited to **10 SOL per 8 hours**, which makes every full
lifecycle a scarce resource — and a verification step you can only afford
occasionally is one you stop running. A local validator with the Genesis
programs cloned from devnet costs nothing and can be reset at will:

```bash
solana-test-validator --reset --quiet --ledger /tmp/ledger \
  --url https://api.devnet.solana.com \
  --clone-upgradeable-program GNS1S5J5AspKXgpjz6SvKL66kPaKWAhaGRhCqPRxii2B \
  --clone-upgradeable-program metaqbxxUerdq28cj1RbAWkYQm3ybzjb6a8bt518x1s

solana airdrop 100 "$(solana-keygen pubkey token/.keys/mint-payer.json)" \
  --url http://127.0.0.1:8899

cd token && RPC_URL=http://127.0.0.1:8899 npm run e2e:dryrun
```

The full lifecycle — `create → liquidity → allocate → finalize → deposit →
trigger → claim` — lands there exactly as it does on devnet.

Two things to know about it:

- **The cluster guard accepts this by structure, not by allowlist.** A local
  validator generates a fresh genesis hash on every `--reset`, so it can never
  be named in advance; an unrecognised chain reached over **loopback** is
  treated as a validator on this machine. Mainnet is still refused first and
  unconditionally, by genesis hash, so tunnelling `127.0.0.1:8899` to a real
  mainnet RPC gains nothing.
- **Give the airdrop a moment.** Umi preflights at `finalized`, and a freshly
  started validator's finalized slot trails by ~32 slots — so `solana balance`
  can show the SOL while the first transaction still fails with *"Attempt to
  debit an account but found no record of a prior credit"*. The harness's
  balance gate now reads at `finalized` and waits, but a manual `presale:*`
  command run seconds after an airdrop can still hit it.

Cloning only what the sale needs is also a **dependency check**, and not a
theoretical one: it is how `presale:deposit` was found to be routing through
MPL Token Extras (`TokExjvjJ…`), a third-party upgradeable program that appears
in no config or runbook here. Devnet has every program, so on devnet no
dependency is ever disclosed. See `docs/TOKEN_SECURITY_AUDIT.md`,
*New (2026-07-26)*.

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
