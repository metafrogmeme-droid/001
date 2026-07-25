# $RCLAW Token Roadmap

**The settlement and access layer for RUNECLAW's on-chain agent economy.**

> **Directional design, not a commitment or an offer.** Nothing here is financial advice or a
> solicitation to buy any token. A token is a **gated "◆ Vision" item** — it does not ship to
> users until the Guardrails (legal review, audits, non-custodial, disclosures) are met. Every
> number below is a **proposed baseline to be ratified**, not a fixed parameter.

This is the condensed overview. The full design lives in
[`docs/TOKEN_ROADMAP.md`](https://github.com/Humanoid-Traders/RUNECLAW/blob/main/docs/TOKEN_ROADMAP.md).

`$RCLAW` supersedes the earlier `$CLAW` placeholder in the product roadmap.

---

## Why a token

RUNECLAW is moving *"from an autonomous trader to an on-chain agent economy."* `$RCLAW` is the
coordination, access, and settlement primitive for that economy — **not a fundraise for its
own sake**. It does three jobs the platform already needs:

- **Access** — stake to unlock premium scan tiers, higher live limits, priority agents.
- **Settlement** — meter agent-to-agent calls to the Shield risk engine; split marketplace
  and copy-trading revenue.
- **Governance** — vote on risk params, new venues, and promoted strategies.

The token is the **last** piece, gated behind everything below.

## Token at a glance

| Field | Proposed |
|---|---|
| Ticker | **`$RCLAW`** |
| Chain | **Solana** (SPL Token-2022 + Metaplex metadata) |
| Supply | **1,000,000,000, fixed** (decimals 9) |
| Authorities | **Mint + freeze revoked**; treasury via Squads multisig |
| Transfer tax | None (DEX-friendly) |

## Utility (mapped to real features)

| Utility | Unlocks |
|---|---|
| Staking tiers | `/scalp` · `/intraday` · `/swing`, higher limits, priority agents |
| Fee discount + pay-in-token | Discounted fees; **buyback-and-burn** from revenue |
| MCP tool-call metering | Settle agent-to-agent calls to the risk engine (x402-style) |
| Governance | Vote on risk params, venues, strategies |
| Marketplace / copy-trading | Creator revenue split |
| Reputation staking | Stake behind a verifiable Proof-of-PnL track record |

## Allocation (1B, proposed)

| Bucket | % | Tokens |
|---|---:|---:|
| Public presale | 15% | 150M |
| DEX liquidity | 10% | 100M |
| Community & ecosystem | 25% | 250M |
| Team & contributors | 15% | 150M |
| Treasury / DAO | 20% | 200M |
| Partnerships & MM | 8% | 80M |
| Advisors | 2% | 20M |
| Reserve / insurance | 5% | 50M |
| **Total** | **100%** | **1,000M** |

Team, treasury, advisors, and reserve are **fully locked at TGE**; presale vests 33% at TGE
then linear over 2 months.

## Presale (proposed)

- Soft cap **1,000 SOL** / hard cap **5,000 SOL** (refund if soft cap missed).
- Min **0.25 SOL** / max **25 SOL** per wallet (anti-whale).
- Whitelist round (48h) → public round (72h or until cap).
- **60% of raised SOL → DEX liquidity**; LP burned/locked ≥ 12 months.

## Launch venue

| Venue | Fit for a vesting utility token |
|---|---|
| **Metaplex Genesis** | **Best** — on-chain, trustless, audit-aligned → **recommended primary** |
| **Smithii Launchpad** | Good — no-code, ~0.1 SOL + % → **fallback** |
| **Pump.fun / LetsBonk** | Poor — no caps/whitelist/vesting → **not for the raise** |

## Roadmap phases

0. **Foundations & Guardrails** — legal review, audit, disclosures, tokenomics finalized.
1. **Pre-launch** — mint, revoke authorities, multisig, whitelist, publish audit.
2. **Presale & TGE** — whitelist → public → finalize → seed + lock liquidity → claim.
3. **Utility activation** — staking tiers, governance voting, buyback-and-burn.
4. **Ecosystem** — marketplace/copy-trading splits, vaults, MCP/x402 settlement.
5. **Sustainability** — insurance fund, proof-of-reserves, CEX listings, DAO handoff.

## Cross-chain note

RUNECLAW's on-chain *identity* is on **Base** (ERC-8004/8257 + x402). `$RCLAW` stays
**Solana-native**, with an **optional Wormhole bridge to Base later** to settle against the
existing tool-registry rail. Wormhole (`W`) and Drift are already in RUNECLAW's Solana scan
universe. A Solana wallet-adapter (Phantom/Backpack) alongside the current EVM/ethers stack
is future engineering work, not built here.

---

See the [full roadmap](https://github.com/Humanoid-Traders/RUNECLAW/blob/main/docs/TOKEN_ROADMAP.md)
for tokenomics detail, legal/compliance, the security checklist, KPIs, and the full list of
open decisions to ratify before launch.
