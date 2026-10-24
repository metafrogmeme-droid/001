# Cover escrow (testnet drafts)

Not offered to customers. Licence review pending. Do not enable in production.

Cash-settled cover on a price outcome is a derivative or insurance under
MiFID II. No EU customer may use either draft until an AFM licence is in
force. These files are a testnet draft of two products that share one
escrow. They are not a customer offer, and turning a flag on does not make
them one: the public status stays `offered: false`.

## What this draft is

`CoverEscrow.sol` holds two claim types.

- ADL re-entry cover. A long pays `size * max(0, mark - adlPx - deductible) / SIZE_SCALE`. A short swaps the two prices. The position precompile must show a magnitude strictly below `coveredSzi`.
- Parametric stop-gap cover. A long pays `size * max(0, trigger - oracle - deductible) / SIZE_SCALE`. A short swaps those two. The trigger is stored at open. A caller-supplied fill figure reverts.

`SIZE_SCALE` is 1e8. The cap is the most the escrow will pay. A zero gap is a real "nothing is owed": the claim reverts and the policy stays active. An unreadable price is not a zero price. A zero price, a zero timestamp, a timestamp in the future, a failed read, a short return, or `int64` minimum are all unreadable.

`size` (the payout multiplier, a `uint64`) and `coveredSzi` (the position magnitude that must shrink) are separate terms. This contract does not convert Hyperliquid `szDecimals`.

The premium token is a constructor argument. No token address is hardcoded.

## Chains

The constructor reverts unless `block.chainid` is HyperEVM testnet (998) or Base Sepolia (84532). HyperEVM mainnet (999), Base (8453), and every other chain revert with `ChainRefused`. There is no deploy script and no mainnet configuration.

`HyperPrecompileReader` reads position `0x800`, mark `0x806` and oracle `0x807`. Upstream helpers also expose a later position precompile at `0x813`; this reader stays on `0x800`, which is the layout these drafts were written against. The precompiles return a price and no clock. `updatedAt` is this block's timestamp only when a non-zero price was actually returned. Otherwise the reading is `(0, 0)` or `(0, false)`.

Base Sepolia has no HyperCore precompiles. The reader deploys there and reports those calls as unreadable. This draft ships no writable oracle.

Drift and Solana are not covered.

## Flags

`COVER_ADL_ENABLED` and `COVER_STOPGAP_ENABLED` are off unless the value is exactly `1` or `true` (case and surrounding space ignored). An empty environment object is a clean slate: it is not the process environment.

The website answers 404 to the public while the matching flag is off. An admin can preview that same hold: `flag_on_for` re-reads `users.plan` and does not trust a claim in the token. A flag that is on is public. Either way the status stays `offered: false`. The pages `/cover-adl` and `/cover-stopgap` are listed in `app/test/unlinked_routes.json` so the landing page does not advertise them. `api-retry.js` is the first script on each page. The pages are `noindex` and are not in the sitemap.

The payload is `offered: false`, `reason: licence_review_pending`, the two testnet chain names, and the hold sentence above. It carries no price, stake or amount.

The server holds no key and signs nothing.

## Watcher

`watcher/user_fills.ts` subscribes to `wss://api.hyperliquid-testnet.xyz/ws` and only when `COVER_ADL_ENABLED` is on. A fill whose `dir` is exactly `Auto-Deleveraging` becomes a notice of user, coin and time. A snapshot is ignored. A message that cannot be read is unreadable, not an empty book. The watcher does not copy a price, does not sign, and does not send a transaction.

## Who funds a policy

The underwriter is one address, set at construction. Nothing in the contract can change the underwriter, the readings contract, the premium token or the asset allowlist. There is no owner and no upgrade.

A policy stays pending until the underwriter posts collateral at least equal to the cap. Activation waits one hour. A claim waits another hour after activation. The claim has to land in `[trigger + delta, trigger + delta + 120 seconds]`, and the reading's `updatedAt` has to be within 120 seconds. A challenge window of 30 minutes follows. Settlement pays the buyer and returns the remainder to the underwriter.

The buyer can cancel a pending policy and receive the premium back. The underwriter can reclaim a voided funded policy, or an active policy after expiry. The underwriter cannot take an unfunded cancel.

An ADL challenge voids the claim only when a fresh readable position is no longer reduced. An unreadable challenge does not void it. A stop-gap challenge always fails: a later price is not evidence that the reading at `t + delta` was wrong. A position that is full size again during the challenge window voids the ADL claim. That is the rule, and it is a limitation of an optimistic window.

`adlPx` is supplied by the claimant and is bounded by the cap the underwriter posted. A challenge cannot restate that price.

A fee-on-transfer token reverts. A token that returns no boolean is accepted. Native coin sent to the escrow reverts. A re-entering token cannot open a second policy or settle twice: state is updated before the external call, and a lock is held across it.

The asset allowlist is the constructor's major-asset list. Anything else, including a HIP-3 market, reverts.

## What this draft does not include

- A mainnet deploy, a mainnet address, or a server key.
- The SaR indexer, signed pricing packets, or a premium backtest.
- A customer offer, a public token, or marketing copy.
- Cards on `/tools`. `app/data/tools.json` is not in this tree, so no card was added.

## Tests

`npm test` in this directory compiles with solc 0.8.37, the optimizer on, 200 runs, `paris`, and `viaIR`. The legacy codegen runs out of stack slots in `open` (eleven arguments plus the reentrancy lock). `viaIR` is the pipeline that still emits that function. The bytecode then runs on an in-process EVM. No network. The website and Python flag tests live with the rest of the suite.

Not offered to customers. Licence review pending. Do not enable in production.
