# SaR Oracle

A read-only reading of how far a book would slip if a share of open interest
had to be traded. Public signals, not advice. Written with AI help.

The flag `SAR_ORACLE_ENABLED` is off unless it is `1` or `true`. While it is
off, `/tools` and `/api/sar-oracle` answer 404. While it is on, the public
feed is ratios and scores. Dollar amounts stay on
`GET /api/sar-oracle/admin/packets`, and only for the operator. There is no
public dashboard in this version. The catalog card stays "Coming soon".

## What a cycle reads

Hyperliquid public info (`l2Book`, `metaAndAssetCtxs`) and, when
`SAR_ORACLE_HL_NODE_URL` is set, a local node (`l4Snapshots` with users,
`liquidatable`, `exchangeStatus`). The public book is at most 20 levels a
side. Account concentration and liquidation density need the node. A node
failure does not fall through into those fields.

The public reader keeps its own weight cap of 80 against the venue's
published cap of 1200 per minute. A 429 is not retried inside the cycle.
The poll interval is at least 60 seconds.

Jupiter perps have no order book. The cycle records that, and it does not
invent a slippage.

Stress size walked through the book is beta times open interest. Beta
defaults to 0.10. A value outside (0, 1] is unreadable and is not clamped.
A value-at-risk cap is not applied, so the paper's Q is not published and
an epoch is not signed until every on-chain field, including that cap, was
read. Null is not encoded as zero.

## Haircut

Account concentration, after a persistence filter of 30 seconds:

`h = 0.5 * max(0, 15 / N_eff - 1) + 0.3 * max(0, CR1 - 0.25)`,
then `S_adj = S * (1 + h)`.

The band is resting size within 100 bps of mid. An order-count stand-in
does not feed the haircut. Adjusted slippage is published only after a
five-minute window is complete, and only as the max of the spot haircut
and that average. One account above half the band is recorded as
`dominant_account`. It is not a new flag bit.

## Verifier

`SaRVerifier` runs on Base Sepolia (chain id 84532) only. Phase 1 signatures
are EIP-712. ML-DSA scheme ids revert: this contract does not pretend to
verify a signature it cannot check. A Winternitz hash chain verifies for
real. Required schemes only tighten. Signer rotation is a 7-day timelock
of a hash commitment. The quantum canary is an off-chain nonce watch of a
NUMS address. A failed read does not trip and does not clear a latch.
Unconfigured is not "safe". The indexer does not send the trip transaction
and does not hold a trip key.

## Euler hook

`SaRGovernorAdapter` may only lower borrow and liquidation LTV, with a
non-zero ramp, inside curator bounds, and only while the reading is not
stale. It reverts off Base Sepolia. Euler Vault Kit operation bits, current
`Constants.sol`: liquidation is bit 11 (`1 << 11`). Bit 12 is the flash
loan. An older kit numbered liquidation as bit 12. This adapter follows
the current kit, and the Foundry test refuses bit 12.

## What this build does not claim

The October 2025 replay was not run here. A lead time of several hours is
the cited paper's statement and was not remeasured. Whether the EU
Benchmarks Regulation applies is unverified. A node book whose shape is
not the one this reader parses is unreadable, not a guess.

The signing key comes from the environment, never from the repository.
Nothing in this module places an order.
