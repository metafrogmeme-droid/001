# Claw Firewall

Testnet module v0.1. The flag `DRAIN_FIREWALL_ENABLED` is off unless it is `1` or `true`. While it is off, the public gets 404 and an operator can open the page. While it is on, `/claw-firewall` is public. The server does not hold a key for this module and does not sign or broadcast its transactions.

The contract package is `contracts/drainfw`. This page is the same rules in product language.

## One shared module

Deploy gas for the policy alone is about 5.22M on the CREATE trace measured 25 Oct 2026 (Forge 1.8.6, solc 0.8.28, optimizer 200, prague). Hasher, policy, and hook together are about 9.53M. A Reviewer estimate of 3.64M was for an earlier, smaller prototype. v0.1 deploys one policy, one hasher, and one hook per chain. Each account inits on that policy (about 219k gas for an empty init). Accounts do not each deploy a copy.

A gated ERC-20 transfer through the 7702 account cost about 113k gas more than the same ungated transfer, cold or warm, on that measurement. The window write is most of it. See `contracts/drainfw/README.md` for the table.

Branch coverage of `src/` on that date was 63.33% (190 of 300 branches). The Foundry summary total, which also counts the deploy script and the tests, was 63.52% (202 of 318).

## Chains

* Base Sepolia (84532) has the policy, the hook, and the 7702 implementation. The 7702 path is a test of whether the wallet allows delegation to this contract, and it stays behind the flag.
* Arbitrum Sepolia (421614) has the policy and the hook from the deploy script. Fork tests may still construct the 7702 account. They skip when `ARBITRUM_SEPOLIA_RPC` is unset.
* Avalanche Fuji (43113) has no EIP-7702. The module there is an ERC-7579 smart account: the shared policy and the hook. The deploy script refuses `--delegate` on Fuji.

## What the account enforces

Outflows fail closed. A new recipient and a new unknown-spender approval wait 24 hours. Loosening a cap, adding an executor, changing the guardian, changing the vault, or uninstalling waits 7 days. Tightening applies immediately and bumps a nonce so a queued loosening of the old policy cannot execute.

The guardian can freeze for at most 72 hours. A later freeze waits 7 days. The user can extend a short queue once, by 24 hours. The longest path out is about 11 days.

Revoke stays available the whole time, including during a freeze. Exit to the registered vault stays available to the owner and the guardian. The guardian can send only to that vault.

An agent with a grant spends only the stored hot balance, and only instantly. An agent cannot queue, and cannot raise the hot balance.

`isValidSignature` answers the ERC-1271 magic only when the digest was registered from the full permit. The hasher builds that digest on chain. A caller-supplied hash is not accepted.

## Limits, stated as limits

* An approval that is already open is not stopped by the hook. It has to be revoked, or explicitly kept. Keeping one is "left in place". The page does not call that a judgement.
* DAI, OpenZeppelin ERC20Permit, and USDS call `ecrecover` before ERC-1271. This module does not gate those permits.
* A facilitator that only submits a plain v, r, s signature fails closed. The account does not recover one.
* Installing the gate makes outstanding unused Permit2 and USDC v2.2 signatures fail until they are registered.
* Someone who holds the 7702 root key bypasses the module. They can transfer directly, and they can clear the delegation or point it at another contract.
* An unread chain is not a clean scan. If every chain failed, there is no list.

The rules on the account keep running if this site is down. The guardian is a device or a person the user chooses. It is never RUNECLAW.

## Not in v0.1

A plain Safe guard, a balance-delta check, lookalike denials, P-256, a full hot and vault ledger, self-describing signatures, Nexus, EAS, USD caps, and a canary. No mainnet until there is an audit and the owner says so.

The server-side testnet signing key used by the older web3 execute route is unchanged. This module does not use it. The product is not non-custodial while that key still exists. Wallet support for delegating to a third-party 7702 contract is an open test.
