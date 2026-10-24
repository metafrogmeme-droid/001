# SaR Oracle

A read-only reading of how far a book would slip if a share of open interest
had to be traded. Public signals, not advice. Written with AI help.

The flag `SAR_ORACLE_ENABLED` is off unless it is `1` or `true`. While it is
off, `/tools` and `/api/sar-oracle` answer 404. While it is on, the public
feed is ratios and scores. Dollar amounts stay on
`GET /api/sar-oracle/admin/packets`, and only for the operator. There is no
public dashboard in this version. The catalog card stays "Coming soon" for
everyone except the operator, who gets a link to that admin feed while the
flag is on.

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

## Alerts and the HIP-3 suggestion

A webhook in `SAR_ORACLE_ALERT_WEBHOOK` receives the public SaR95 reading
when it is above 300 bps or above 500 bps. Unset, it sends nothing. The
body is ratios. A missing reading is not an all-clear.

`SAR_ORACLE_HIP3_COIN` and `SAR_ORACLE_HIP3_OI_UNITS` produce an advisory
`perpDeploy` / `setOpenInterestCaps` pair. The cap is
`max(1_000_000, half the supplied open interest)` in that same integer
unit. The Hyperliquid comment calls 1_000_000 one size unit of collateral,
and this module does not rescale. Nothing here signs or submits the
action. HIP-3 is not in the v0 coin list, so an unset coin is
`not_configured`.

`GET /api/sar-oracle/admin/adoption` is the operator's copy of the alert
evaluation, the suggestion, and the archive status. It is not a public
route and it carries no dollar amount.

## Euler hook

`SaRGovernorAdapter` may only lower borrow and liquidation LTV, with a
non-zero ramp, inside curator bounds, and only while the reading is not
stale. It reverts off Base Sepolia. Euler Vault Kit operation bits, current
`Constants.sol`: liquidation is bit 11 (`1 << 11`). Bit 12 is the flash
loan. An older kit numbered liquidation as bit 12. This adapter follows
the current kit, and the Foundry test refuses bit 12.

## Morpho pre-liquidation switch

`MorphoPreLiqSwitcher` authorizes the tighter of two pre-deployed
pre-liquidation contracts when the reading is fresh and above the
threshold. It does not authorize the looser contract, it does not revoke
the tighter one, and a stale or unread reading does not call Morpho. The
constructor reverts off Base Sepolia and on a zero address. It does not
call pre-liquidate and it does not move funds. The Morpho position owner
is the switcher itself.

## What this build does not claim

The October 2025 archive was not reachable from this build (the public
object URLs answered 403 on 23 October 2026). With `SAR_ORACLE_OCT10_DIR`
unset the replay answers `not_read` / `dataset_absent`. A directory with
no `packets.json` stays unreadable and is not filled with zeros. A
synthetic fixture exercises lead time, precision, recall and Spearman,
and it is labelled `fixture`. It is not an October 2025 measurement. A
lead time of several hours is the cited paper's statement and was not
remeasured. Whether the EU Benchmarks Regulation applies is unverified.
A node book whose shape is not the one this reader parses is unreadable,
not a guess.

The signing key comes from the environment, never from the repository.
Nothing in this module places an order.
