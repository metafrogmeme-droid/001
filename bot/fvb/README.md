# Fair-value bridge

A session-aware risk price for a tokenized equity. The external print and
the on-chain books are blended with weights that depend on the session and
on how deep the book is. The number is a risk input for a testnet keeper
and for lending adapters. It is not a trading instruction.

Public rows carry the label `Public signals, not advice.` Prices are feed
units (an index, or KRW per share on the published-print replay). The
board has no dollar amounts.

## Flag

`FVB_ENABLED` is off unless the environment is `1` or `true`. While it is
off the public surface answers 404. An operator still sees the page, with
the banner `Admin preview. Not public.`

## Mechanism

Inputs on each tick: an external print tagged with a session
(`regular`, `pre`, `post`, `overnight`, `closed`) and one or more books.
HyperEVM's input tick is 1.0s and Solana's is 0.4s. Those are not the
keeper cadence.

A handoff opens when the session changes, or when the external print
arrives after a gap longer than 5 minutes. The first prints in that
window are candidates. A candidate's external weight is the session
coefficient times `exp(-|gap| / sigma)`. `sigma` is the standard
deviation of a stated gap prior, not a fit to a dataset this process
holds. If that prior is missing the external weight is zero.

The candidate is confirmed, on the tick the evidence arrives, if traded
size near the print clears the venue minimum (default 100 shares; one
share does not), or a second venue agrees within 1%, or a persisted book
mid has moved halfway toward the print within 60 seconds. After 5
minutes an unconfirmed print is taken into the blend at the full session
weight and `confirmed` stays false, so a consumer can halt.

Book weight is `Q / S(Q)` times a breadth cap and a concentration
haircut. A book that cannot be read, or that cannot fill `Q`, is left
out. It is not given zero slippage. A mid enters the blend only after it
has held for 30 seconds, so a flash that reverts is ignored.

Confirmed regular session: external share at least 0.9. Confirmed pre and
post: at least 0.7. The floor is applied to the full coefficient and
then multiplied by the handoff decay, so a candidate does not inherit
it. Closed session, once the print is not a candidate: book share at
most 0.6. That cap is not applied during a candidate window.

The band is a quantile of the same gap prior, widened when the effective
number of book levels is low. An unreadable book widens the band by a
fixed factor instead of leaving the prior width.

## Collateral

`R` is the last confirmed price. `P_c` is the new candidate print.

Collateral side:

- Unconfirmed up-move: borrow-power and liquidation both stay at `R`.
- Unconfirmed down-move: borrow-power is `P_c` (it tightens at once) and
  liquidation is `max(P_c, FV_lo)`, until the print is confirmed or forced
  through after 5 minutes.
- After a forced up-move, borrow power ramps from `R` to the forced fair
  value over 15 minutes. Liquidation is that fair value for the whole
  ramp.

Borrowed-asset side, the mirror: borrow-check is `max(R, P_c)`.
Liquidation is `min(P_c, FV_hi)` on an up-move, otherwise `R`.

The invariant is borrow-power <= liquidation, so a loan sized on the
borrow-power price is not instantly liquidatable. A missing input is
omitted, not filled with zero.

Perps use a symmetric clamp: an unconfirmed print does not move the mark
off `R` in either direction. Open interest is scaled by a ratio, and that
ratio is lower while the print is unconfirmed and while the band is wide.
The ratio is not a notional.

## Keeper

Call cadence defaults to 3 seconds. A mark older than 10 seconds is a
staleness fallback, not a fresh `setOracle`. Both numbers come from
Hyperliquid's HIP-3 deployer-actions note (call about every 3s; stale
marks fall back after 10s). A 2.5s figure from elsewhere is not used.
Both are fields on `FvbConfig` (`oracle_cadence_s`, `oracle_stale_s`).
An unconfirmed reading produces a halt, not a price update.

## Chains

Testnets only. The signer refuses a mainnet chain id before it signs.

| chain | network | role |
|---|---|---|
| Hyperliquid | HyperEVM testnet (chain id 998, confirm against the RPC) | HIP-3 risk price |
| Solana | devnet | Kamino xStocks |
| Ethereum | Sepolia (11155111) | Morpho and Euler adapters |
| Robinhood Chain | testnet (46630) | Morpho adapter |

Tokenized-stock lending for this bridge is on Ethereum (Morpho and
Euler) and on Robinhood Chain (Morpho). Base and Arbitrum are not
targets. No market address is recorded in the repo. Robinhood Chain
mainnet (4663) and Ethereum mainnet are on the refuse list.

Euler's `OP_LIQUIDATE` is bit 11 (`1 << 11`). Bit 12 is the flash-loan
operation on the current vault kit.

## Signers

A packet needs 3 of 5 signatures. Schemes this build will actually
check are secp256k1 (EIP-712, domain name `Fair-Value Bridge`) and
ed25519. `ml-dsa` and `falcon` are rejected. The next epoch's key is a
SHA-256 commitment; a rotation that does not match it is refused.
Tests mint keys in the process and drop them.

## Replay

`python3 scripts/fvb_board.py /tmp/fvb_board.json` writes a board of
replays. One row is the published pre-market print for symbol `000660`
(1,272,000 KRW per share, a drop of 29.96% from the prior the drop
implies) with a constructed book that does not follow. The other rows
are twenty deterministic weekend gaps, of which the board keeps nine.
None of these are live quotes.

## Open questions

- HyperEVM testnet chain id 998 should be confirmed against the operator's RPC before a keeper points at one. Mainnet 999 is refused.
- Which tokenized-equity markets are live on Morpho or Euler is not recorded here. Adapters are testnet-only and carry no market address. Robinhood Chain's testnet id 46630 is the one in Robinhood's connecting doc.
- `V_min` defaults to 100 shares. A venue that trades in a different unit needs its own value.
- The gap prior is a design prior. The weekend score bar (median reopen error at or under 38.4 bps, false holds at or under 5%) is not that prior's median.
- The published-print replay uses the published print and a constructed book, not an archive of HIP-3 fills.
- Post-quantum schemes are not implemented. A commitment is stored so a later verifier can be bound to a key that was named in advance. Collusion of 3 signers, and a quantum break before that rotation, remain residual.
- There is no production signer ceremony.
- The Solana program, when it lands, is host-tested. An SBF artifact is a separate question.
