# Recursion Breaker — sentinel onboarding

Verified 2026-10-24. RXB_ENABLED off: the public gets 404 and an admin can read. On: /rxb and /v1/node are public signals, not verdicts, with no dollar amounts.

## Price

P_RXB = min(P_mkt, P_back), where P_back = min(NAV_issuer, (V_chain + w(delta) * V_att) / S) and w(delta) = 2^(-delta/72h).

P_mkt is a 30-minute DEX TWAP and only an upper bound. If DEX depth within 2% is under 1 million dollars, P_mkt is left out of the min. It is not set to zero. An unread depth is the same: left out, not zero.

Packets are signed by this deployment's own 2-of-3 signers and are valid for 1 hour.

Quotes are wad (1e18 = 1.0). Morpho IOracle.price() is 1e36-scaled by 1e36 * 10^(loanDec - collDec). A nonzero quote that would round to zero reverts.

## Who can grant the sentinel

Only the vault owner can call setIsSentinel. The curator cannot. Each vault owner has to grant the role.

decreaseAbsoluteCap(bytes idData, uint256 newAbsoluteCap) and decreaseRelativeCap(bytes idData, uint256 newRelativeCap). A curator or a sentinel can call them. There is no timelock on a decrease. idData is abi.encode of collateralToken and the token. The cap id is the keccak256 of that idData.

Vault V2 cap-increase timelocks default to 0, and the delay can be configured down to zero. Onboarding refuses a vault whose cap-increase timelock is unread or under 72 hours, so a curator cannot undo a cap-down at once. Fork setup calls setIsSentinel from the owner. This sentinel never calls an increase.

This process never takes a sentinel role on mainnet and never signs there. Reading is allowed. An unsigned decrease can be prepared for the owner's key or multisig. It is not deployed and not activated. Going live needs the owner's go after an external audit. No environment variable turns activation on. RXB_SENTINEL_PAUSED pauses it. A fork broadcast rehearses it.

## Shared decreases

Cap decreases from CGC, RXB and T2C go through one coordinator. The lowest cap wins. An unreadable proposal blocks that vault id and is not treated as zero. An absent opinion is skipped. Nothing here raises a cap. The audit log is append-only.

## Challenge

Challenge bonds are 5000 USDC of the testnet mock (6 decimals), doubling after each failed challenge within 30 days, with a 72 hour dispute window. An unresolved challenge reclaimed after the window is not a failure and does not double. The bond is held by this contract. No escrow from another system is on this branch.

## What the backtest can say

No. The backtest does not show a threshold crossing before 28 Oct 2025. Earlier windows are unreadable, which is not an all-clear. The earliest sourced snapshot that crosses is 28 Oct 2025, on the published comparison, not on a chain read.

The Morpho DAY series was pulled 2026-10-23 from https://api.morpho.org/graphql. A local professor directory is not a source.

## Phase 2

Silo on Ethereum is phase 2 and is not indexed. Euler on Ethereum is in the indexer. Hidden vaults are part of that read. An unread oracle is left out of the oracle sum, not counted as zero.

## State

Alert above CBF 5 percent, UF 25 percent or DMS 0.5. Fired above CBF or LTS 10 percent or UF 40 percent, on two runs 1 hour apart. Watching after 24 hours below. Recovered after 7 clear days and a curator re-raise. act() is 2-of-3, caps to zero, fork only.

## Read order

The indexer reads Ethereum, then Robinhood Chain, then Base. Robinhood is not on the public card. A failed read is unreadable, not an empty book.
