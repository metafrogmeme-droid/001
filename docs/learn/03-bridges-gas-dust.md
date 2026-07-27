# Bridges, gas and dust — what moving an asset really costs

Cross-chain positions carry a cost that never shows on a balance sheet until
you try to leave: the price of getting home.

## Gas is a market price

Every chain has its own gas market, repriced block by block. The escape
planner reads it live over public RPCs and shows it per chain — indicative,
the node's current suggestion, never a quote for your transaction. A chain
that cannot be read shows nothing: an omitted number beats an invented one.

## The floor cost of a bridge

A bridge transaction burns execution gas on the source chain — but that is
only the floor. Bridge fees, relayer fees, L1 data fees on rollups and
destination-chain gas all come on top. When the escape planner flags a
balance with "moving this may cost ≥X% of it", the ≥ is literal: the true
cost can only be higher than the floor shown.

## Dust: when leaving costs more than staying

A small balance on a far chain can be genuinely uneconomical to move — worth
holding, worth spending on that chain, but not worth bridging. That is not a
failure; it is arithmetic. The honest mistake is not *having* dust, it is
bridging it anyway because the number was never in front of you.

## Order matters

Unwinding a cross-chain book has dependencies: close leverage before it
liquidates, repay debt to unlock collateral, exit LPs to reclaim the
underlying, then convert and bridge home last. The escape planner sequences
this for you — but the principle is worth knowing without the tool: the
expensive mistake is usually doing the right things in the wrong order.

*Education, not investment advice.*

## Self-check

1. A transfer-cost floor computed from live gas is:
- [x] A minimum — real transactions add fees on top, so ≥ is literal
- [ ] A quote for your transaction
- [ ] The maximum you could pay

2. "Dust" is a balance that:
- [ ] Has zero market value
- [x] Costs more to move than it is worth
- [ ] Cannot be sold

3. When a chain's gas cannot be read, an honest tool shows:
- [ ] Zero
- [ ] The last known value silently
- [x] Nothing for that chain, and says it was omitted
