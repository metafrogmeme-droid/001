# Claw Firewall module v0.1

Testnet only. One shared policy, one hasher, and one ERC-7579 hook per chain. A second account is an init on that policy. It is not a second deploy.

The 7702 implementation (`FirewallAccount`) exists for a wallet-delegation test on Base Sepolia (chain id 84532). Avalanche Fuji has no EIP-7702. The Fuji deploy is the policy, the hasher, and the hook. `deploy.sh --delegate` refuses every network except Base Sepolia. Arbitrum Sepolia can run the 7702 contract in a fork test. This script does not deploy it there.

`DRAIN_FIREWALL_ENABLED` is off unless it is `1` or `true`. While it is off the public gets 404 and an operator gets the page. While it is on the page is public. The server does not sign and does not broadcast.

Clean-room MIT. No Nexus. No BUSL code.

## What v0.1 does

1. An install-time approval scan and a one-transaction revoke batch. The scan reuses the Panic Button reader. Logs propose and state decides. An unread chain is not a clean result. The contract stores the hash the user supplied. It cannot prove the scan saw every approval.
2. A policy and a signature gate. Per-token outflow caps. 24 hours on a new recipient or a new approval. 7 days to loosen a policy, add an executor, or change the vault. Allow-lists. `isValidSignature` returns the ERC-1271 magic only for a digest the hasher registered from the full permit. Permit2, a Permit2 batch, and USDC v2.2 (EIP-3009) go through that path.
3. A queue, a guardian, and an exit. Revoke and exit stay available. The guardian is the user's second device or a person they pick. It is never RUNECLAW. There is no RUNECLAW key in the contracts. A rescue sends only to the vault registered at install. Changing that vault is a 7-day queue. A freeze lasts at most 72 hours, then 7 days before another freeze. The longest exit is about 11 days: 7 days queued, one 24-hour extension, then a 72-hour freeze.

An agent spends instantly only up to the stored hot balance. An agent cannot queue. `grantId != 0` cannot enter the queue. Raising the hot balance is a 7-day queue.

## Known limits

These are limits of this module. The page and the API say the same sentences.

* **Pre-existing approvals still need a revoke.** The hook does not see a later `transferFrom` that spends an allowance that was already open. Revoke it, or leave it in place on purpose. Revoke stays available, including while the account is frozen: `approve(spender, 0)`, `setApprovalForAll(spender, false)`, Permit2 lockdown and nonce invalidation, and USDC `cancelAuthorization`.
* **DAI, OpenZeppelin ERC20Permit, and USDS are not gated.** Those tokens call `ecrecover` before ERC-1271, so a signature from the 7702 root key never reaches `isValidSignature`. A unit test uses an OpenZeppelin-style mock that accepts the raw ECDSA signature and never consults the account. Do not describe those permits as covered.
* **A facilitator that only accepts a plain v, r, s signature fails closed.** `isValidSignature` does not recover one. It returns the magic value only for a registered digest, and `0xffffffff` otherwise. There is no `ecrecover` fallback.
* **Installing the gate invalidates outstanding unused Permit2 and USDC v2.2 signatures** until they are registered through the hasher, which builds the digest on chain from the permit and reserves the cap.
* **A thief who holds the 7702 root key bypasses the module.** They can transfer tokens directly, and they can point the delegation at another contract or at the zero address. The contract cannot see a transaction the root key sends as the account itself.
* **The scan is a best effort.** A chain the reader could not reach is named. It is not an empty list.

## Gas

Measured 25 Oct 2026. Forge 1.8.6, solc 0.8.28, optimizer 200 runs, `evm_version` prague, `via_ir` off. `gasleft()` around `new` is not the deploy cost: Forge rewrites `new` to `VM::deployCode` and the meter reads about 930. The numbers below are the CREATE trace and the code-deposit floor (`32000 + 200 * runtime bytes`).

| contract | runtime bytes | deposit floor | CREATE trace |
|---|---:|---:|---:|
| DrainHasher | 9,985 | 2,029,000 | 2,214,764 |
| DrainPolicy | 23,886 | 4,809,200 | 5,222,966 |
| DrainHook7579 | 9,437 | 1,919,400 | 2,093,700 |
| FirewallAccount | 10,223 | 2,076,600 | 2,289,655 |

One shared module (hasher + policy + hook) is about **9,531,430** gas on that CREATE trace. A second account init on the same policy was about **219,475** gas and did not grow the policy code. The policy runtime is 23,886 bytes, 690 under the 24,576-byte limit. New functions do not go on `DrainPolicy`.

A Reviewer prototype estimated about 3.64M to deploy and about +32k on a cold gated ERC-20 call. This build is larger because the policy is near the size limit. Per gated call, `gasleft()` around the external call (the account lock and the policy check, not only the marginal policy):

| | ungated `Tok.transfer` | gated `FirewallAccount.execute` | delta |
|---|---:|---:|---:|
| cold | 50,089 | 163,223 | 113,134 |
| warm | 32,989 | 146,832 | 113,843 |

The warm delta stays near the cold delta because the window write dominates.

## Coverage

`forge coverage --report summary`, 25 Oct 2026, Foundry 1.8.6. CI runs `forge test` and does not run coverage. Fork tests skip unless `BASE_SEPOLIA_RPC` or `ARBITRUM_SEPOLIA_RPC` is set. The unit tests always run. No public RPC is passed in CI.

On `src/` the branch figure is **63.33%** (190/300). The summary total, which also counts the deploy script and the tests, is **63.52%** (202/318). Lines on `src/` are 79% to 100% per file (policy 79.01%, account 100%).

## Deploy

From the owner's machine, never from the server:

```bash
contracts/drainfw/script/deploy.sh --network base-sepolia
contracts/drainfw/script/deploy.sh --network base-sepolia --delegate
contracts/drainfw/script/deploy.sh --network fuji
```

Simulation unless `--broadcast`. The script refuses `RUNECLAW_SERVER=1`, a server signing key in the environment, a mainnet network name, and `--delegate` on Fuji or Arbitrum Sepolia. It does not source `.env`.

## Follow-ups

Not in v0.1: a Safe 1.5.0 guard and module for a plain Safe, including Fuji Safes that do not speak ERC-7579; a balance-delta check; lookalike denials; P-256; a full hot and vault ledger; self-describing signatures; a Nexus emergency-uninstall shield; EAS; USD caps; a canary. Kernel and the reference MSA are not supported. No mainnet without an audit.

## Open

The server-side testnet signing key in `app/routes/web3_execute.js` and `bot/web/web3_exec_gate.py` is still there. This module is not non-custodial until that path is gone. Whether MetaMask, Rabby, and Coinbase Wallet allow 7702 delegation to a third-party contract is untested. If they do not, the 7702 path is for agents only.
