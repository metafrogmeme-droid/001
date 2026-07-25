# `$RCLAW` Wormhole NTT bridge — DRAFT / TESTNET-ONLY

Bridges `$RCLAW` between **Solana devnet** (hub) and **Base Sepolia** (spoke) using
Wormhole **Native Token Transfers (NTT)**, so the token can eventually settle against
RUNECLAW's existing **Base** identity rail (ERC-8004 / ERC-8257 + x402 — see
[`docs/ONCHAIN_GOLIVE.md`](../../docs/ONCHAIN_GOLIVE.md) and roadmap §9).

> ⚠️ Testnet draft. A mainnet bridge is gated behind the roadmap's Phase 0 Guardrails
> (legal review + contract audits). `plan` refuses any network other than `Testnet`.

## Why hub-and-spoke (lock on Solana)

NTT supports two modes:

| Mode | Requires | Fit for `$RCLAW` |
|---|---|---|
| **Burn-and-mint** | Mint authority handed to an NTT-controlled PDA | ❌ — `token/scripts/create_token.mjs` **revokes** mint authority; delegating it would break the fixed-supply / authorities-revoked guarantee (roadmap §2, §11) |
| **Hub-and-spoke (locking)** | Tokens locked in a manager on the hub; wrapped supply on spokes | ✅ **chosen** — the Solana mint stays untouched and immutable |

## Usage

```bash
cd token
npm install
npm run bridge:plan        # OFFLINE: validate config, resolve chains, print deploy steps
```

### Deploy (ntt CLI — one time)

The managers/transceivers are deployed with Wormhole's `ntt` CLI, not from this script
(deployment is a reviewed, operator-signed step):

```bash
ntt new rclaw-ntt && cd rclaw-ntt
ntt init Testnet
ntt add-chain Solana      --latest --mode locking --token <RCLAW_MINT>
ntt add-chain BaseSepolia --latest --mode burning --token <WRAPPED_ERC20>
ntt push                  # deploy + register managers/transceivers
```

Then copy the resulting **manager** and **token** addresses into `ntt.config.json`
(`hub.manager`, `hub.token`, `spokes[0].manager`, `spokes[0].token`). `bridge:plan`
reports `Ready: yes ✓` once all four are filled.

### Transfer

```bash
npm run bridge:transfer -- --amount 1 --sender <ADDR>            # hub → spoke
npm run bridge:transfer -- --amount 1 --sender <ADDR> --reverse  # spoke → hub
```

Non-custodial: the script builds the transfer; **signing and sending stay with the
operator** (wire a signer and use the SDK's `signSendWait`). It never holds a key.

## Known upstream issue (verified)

`@wormhole-foundation/sdk-solana-ntt` **7.0.0–7.2.0** ships an ESM build whose internal
`./side-effects` import has **no file extension**, and its `exports` map blocks deep
imports — so **plain Node ESM cannot load it** (`ERR_MODULE_NOT_FOUND`). The package is
built for bundlers. `@wormhole-foundation/sdk-evm-ntt` loads fine.

Consequences, handled explicitly in `ntt_bridge.mjs`:
- **`bridge:plan` never imports it** → always works in plain Node (verified).
- **`bridge:transfer`** imports it lazily and, on failure, tells you to re-run under a
  bundler-style loader: `npx tsx token/bridge/ntt_bridge.mjs transfer --amount 1 …`.

Re-check on SDK upgrades. Versions are **pinned** (`sdk@5.2.0` + `*-ntt@7.2.0`) because the
NTT packages peer on `sdk-base ^5`, while the `sdk` meta-package has already moved to 6.x —
installing `sdk@latest` alongside NTT produces a peer conflict.

## Config

`ntt.config.json` — mode, network, hub/spoke chains + RPCs + addresses, transfer defaults,
and the intended rate limits (the on-chain limits are set via the `ntt` CLI; the file mirrors
them for review). All values are a **proposed baseline to ratify**.
