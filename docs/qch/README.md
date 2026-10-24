# Quantum custody haircut (QCH)

QCH scores each wrapped-BTC reserve by how much of it sits behind an
already-exposed public key, and it tells a curator sentinel when to cut
a supply cap and schedule a lower LTV. The score is an estimate. Every
published issuer score carries `label: estimate`, `estimate: true`, the
method string, and the data source next to the number.

The method is integer basis points:

`QES = weighted_exposed_sats * 10000 // total_sats`

P2TR counts as exposed because the key is in the output. Any other
output type counts as exposed only after at least one spent output. A
missing spent count is unreadable, which is not "unexposed" and not 0
bps. A zero total is not 0 bps. One snapshot is that snapshot
(`single_observation`). It is not labelled a time average. A fresh
signed issuer attestation subtracts only the configured reduction.
MPC ECDSA/TSS is classical and does not reduce the score.

## Unverified parameters

These are config, with placeholder defaults. They are not facts copied
out of a reserve file.

| Variable | Placeholder |
|---|---|
| `QCH_WBTC_MULTISIG_M` / `QCH_WBTC_MULTISIG_N` | unset. Full weight, reason `unverified_threshold`. One of the two set and the other missing withholds the score. |
| `QCH_UNIT_DEPOSIT_PREFIX` | `unverified`. The treasury fixture address is P2TR (`bc1p`). |
| `QCH_LBTC_SCANNED` / `QCH_TBTC_SCANNED` | off. Those reserves were not scanned, so their score is null, reason `not_scanned`. |
| `QCH_QES_JUMP_BPS` | 50. A design constant, not a measured curator policy. |
| `QCH_POLICY_DEADLINE_UNIX` | unset. |
| `QCH_ATTESTATION_REDUCTION_BPS` | 0. No attested reduction until a signed plan is recorded. |

The cap cut is 1000 bps (10%). The LTV wait is the SaR verifier's
`TIMELOCK`, read from `tools/sar-oracle/src/SaRVerifier.sol`. Scheme ids
and the peg/canary flag bits are read from that verifier and from
`tools/sar-oracle/runtime/chains.js`. QCH does not keep a second copy.

## Where it sits

The graph shape is an RXB `btc_custody` node with edge kind
`mint_backing`. This package does not open the RXB database and does
not write it.

`QCHOracle` and `QCHSentinel` live in `contracts/qch`. They accept
Ethereum Sepolia (11155111), Base Sepolia (84532), Arbitrum Sepolia
(421614), and HyperEVM testnet (998). Chain ids 1, 8453, 42161, and 999
revert. Signing is 2-of-3 ECDSA (scheme 0). Schemes 1 and 3 revert.
Scheme 2 stores a WOTS commitment and does not verify a reading. There
is no liquidation entrypoint. The server does not sign on mainnet, and
it does not read `QCH_SIGNING_KEYS` unless the chain id is one of those
four testnets.

## Who can see it

`QCH_ENABLED` is off unless the value is `1` or `true`. The page and
`/api/qch/score` and `/api/qch/policy` are admin-only. A non-admin gets
404 while the flag is off and 404 while it is on. An admin with the
flag off gets the preview banner. The website serves
`bot/qch/fixtures/published.json`. It does not apply environment
overrides on a request. Republish with `python3 -m bot.qch` and replace
that file.

The fixture is a 2026-10-24 snapshot (18:53 +02:00). It keeps reserve
totals, a handful of example rows, and the exposure rule. It is not the
raw reserve download. Amounts are sats and bps.

Solana appears only as an issuance count in that snapshot. It is not an
EVM contract in this workspace.
