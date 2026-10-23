# Recursion Breaker — sentinel onboarding

Verified 2026-10-23. This is the operator note. Readings stay on the admin
page. The public card says coming soon and names no issuer.

## Price

P_RXB = min(P_mkt, P_back), where P_back = min(NAV_issuer, (V_chain + w(delta) * V_att) / S) and w(delta) = 2^(-delta/72h).

P_mkt is a 30-minute DEX TWAP and only an upper bound. If DEX depth within 2% is under 1 million dollars, P_mkt is left out of the min. It is not set to zero. An unread depth is the same: left out, not zero.

Packets are signed by this deployment's own 2-of-3 signers and are valid for 1 hour.

Quotes are wad (1e18 = 1.0). Morpho IOracle.price() is 1e36-scaled. Confirmed 2026-10-23 against morpho-blue IOracle.sol: the price of 1 asset of collateral quoted in 1 asset of loan token, scaled by 1e36. If both tokens are 18 decimals, 1:1 is 1e36. A wad quote is multiplied by 1e18. Signers are responsible for the decimal adjustment.

## Who can grant the sentinel

Only the vault owner can call setIsSentinel. The curator cannot. Each vault owner has to grant the role.

decreaseAbsoluteCap(bytes idData, uint256 newAbsoluteCap) and decreaseRelativeCap(bytes idData, uint256 newRelativeCap). A curator or a sentinel can call them. There is no timelock on a decrease.

Cap increases are timelocked, and the delay can be configured down to zero, so a timelock is not a safety property. This sentinel never calls an increase.

This process never takes a sentinel role on mainnet and never signs there. Reading mainnet data is allowed. The cap-down bot runs on a fork only.

## Shared decreases

Cap decreases from CGC, RXB and T2C go through one coordinator. The lowest cap wins. An unreadable proposal blocks that vault id and is not treated as zero. An absent opinion is skipped. Nothing here raises a cap. The audit log is append-only.

## Challenge

Challenge bonds are 5000 USDC of the testnet mock (6 decimals), doubling after each failed challenge within 30 days, with a 72 hour dispute window. An unresolved challenge reclaimed after the window is not a failure and does not double. The bond is held by this contract. No escrow from another system is on this branch.

## What the backtest can say

No. The backtest does not show a threshold crossing before 28 Oct 2025. Earlier windows are unreadable, which is not an all-clear. The earliest sourced snapshot that crosses is 28 Oct 2025, on the published comparison, not on a chain read.

The Morpho DAY series was pulled 2026-10-23 from https://api.morpho.org/graphql. A local professor data directory is not a source. The October series does not contain the circular book. xUSD and deUSD on that published snapshot are the first sourced crossing: xUSD fails both thresholds, deUSD fails the look-through and not the on-chain-face share, because the mutual claim is on-chain and inside one SCC.

## Phase 2

Euler and Silo on Ethereum are phase 2 and are not indexed. Reported for this build, not measured here: Euler about 16.1 million US dollars of XUSD, Silo about 7.6 million US dollars.

## Read order

The indexer reads Ethereum, then Robinhood Chain, then Base. Robinhood is not on the public card. A failed read is unreadable, not an empty book.
