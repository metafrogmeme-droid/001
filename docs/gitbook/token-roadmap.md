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

- **Access** — stake to unlock premium scan tiers, larger compute allowances, priority agents.
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
| Staking tiers | `/scalp` · `/intraday` · `/swing`, compute allowances, priority agents |
| Fee discount + pay-in-token | Discounted fees; **buyback-and-burn** from revenue |
| MCP tool-call metering | Settle agent-to-agent calls to the risk engine (x402-style) |
| Governance | Vote on risk params, venues, strategies |
| Marketplace / copy-trading | Creator revenue split |
| Reputation staking | Stake behind a verifiable Proof-of-PnL track record |

## How tiers are earned

Flat token thresholds are plutocratic and get ~25× harder to reach as the token appreciates.
The proposed replacement weighs two axes — capital opens the door, behaviour decides the floor:

```
tier_weight = √(staked) × lock_multiplier × standing
```

`√` compresses whale dominance (87.7% → 66.0% of weight) without erasing it.
`lock_multiplier` (1.0–2.5×) prices commitment. **`standing`** (0.2–2.0) is earned and
**non-transferable** — risk-discipline, risk-adjusted verified track record, Arena percentile,
and tenure. Tiers are then assigned by **relative percentile** (Elite top 5%, Pro top 25%), so
the ladder re-scales with the price instead of locking out later arrivals. The percentile step
is the one that does the work: `√` under *absolute* bands is mathematically identical to the
flat thresholds it replaces.

Tiers grant **metered compute** — deep scans, concurrent agents, backtest hours — not feature
flags. **Live trading limits, position size, and leverage are deliberately never tier-gated**;
those stay tied to KYC/compliance tiers.

Full spec, worked examples, anti-gaming analysis, and the phased implementation plan:
[`docs/TIER_MODEL.md`](https://github.com/Humanoid-Traders/RUNECLAW/blob/main/docs/TIER_MODEL.md).

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

- Soft cap **1,000 SOL** / hard cap **5,000 SOL**. The soft cap is enforced
  **operationally** — a Metaplex Genesis fixed-price presale has no native soft-cap/refund
  field, so the cancel-and-refund path is published before the sale rather than assumed.
- Min **0.25 SOL** / max **25 SOL** per wallet (anti-whale).
- Whitelist round (48h) → public round (72h or until cap).
- **66.67% of raised SOL → DEX liquidity**; LP **permanently locked** (never-claim).
- The pool's **token** side is deliberately thin — **20,001,000 $RCLAW**, sized for the *soft*
  cap, not the 100M DEX-liquidity bucket. The token side is frozen when the bucket is created
  while the SOL side is whatever is raised, so sizing for a full raise would open the pool
  **below** the presale price on any smaller one, permanently and with no refund. Sized this
  way it opens at the presale price at 1,000 SOL and above it from there. The remaining
  79,999,000 is earmarked in reserve for post-TGE depth once the raise is known.

## Launch venue

| Venue | Fit for a vesting utility token |
|---|---|
| **Metaplex Genesis** | **Best** — on-chain, trustless, audit-aligned → **chosen; integrated in draft (devnet)** |
| **Smithii Launchpad** | Good — no-code, ~0.1 SOL + % → **fallback** |
| **Pump.fun / LetsBonk** | Poor — no caps/whitelist/vesting → **not for the raise** |

## Roadmap phases

0. **Foundations & Guardrails** — legal review, audit, disclosures, tokenomics finalized.
1. **Pre-launch** — mint, revoke authorities, multisig, whitelist, publish audit.
2. **Presale & TGE** — whitelist → public → finalize → seed + lock liquidity → claim.
3. **Utility activation** — staking tiers, governance voting, buyback-and-burn.
4. **Ecosystem** — marketplace/copy-trading splits, vaults, MCP/x402 settlement.
5. **Sustainability** — insurance fund, proof-of-reserves, CEX listings, DAO handoff.

## Build status — and what is actually proven

Draft, devnet-only reference implementations now exist for most of the mechanics above:
the SPL Token-2022 mint tooling, a real **Metaplex Genesis** presale integration (whitelist,
liquidity with a permanent LP lock, withdraw/refund), an end-to-end lifecycle harness, an
Anchor **staking program**, a Wormhole **NTT bridge** script, and the wallet/tier-gate
plumbing. None of it is a launch — the project remains in **Phase 0**.

**An adversarial review of that code found 10 real defects, all fixed.** The most serious was
**critical**: the staking program bound stakes to no mint, so an attacker could stake a
worthless token and redeem the same amount from the real `$RCLAW` vault. It is fixed, and the
fix is *executed* rather than asserted — the attack is performed in-process against the real
program and rejected, with the honest vault balance asserted unchanged.

What that still does **not** mean:

| Proven | Not proven |
|---|---|
| Staking program executes; attack rejected; 8 tests pass | **No independent audit**; no SBF runtime; never on devnet/mainnet |
| Presale params derive correctly offline | No presale transaction has ever been sent |
| Bridge + TS specs typecheck | Never deployed or executed |

The gaps are environmental — the authoring environment blocks Solana devnet and the Anchor/
Solana CLIs — not optional. **Do not deploy the staking program anywhere holding value until
it is audited.**

## Cross-chain note

RUNECLAW's on-chain *identity* is on **Base** (ERC-8004/8257 + x402). `$RCLAW` stays
**Solana-native**, with an **optional Wormhole bridge to Base later** to settle against the
existing tool-registry rail — a **draft NTT bridge script** now exists for that (hub-and-spoke,
locking on Solana, because the mint authority is revoked). Wormhole (`W`) and Drift are already
in RUNECLAW's Solana scan universe. A lightweight Solana wallet connector
(Phantom/Backpack, connect-and-sign) now ships alongside the EVM/ethers stack; a full
wallet-adapter with Solflare/hardware support remains future work.

---

See the [full roadmap](https://github.com/Humanoid-Traders/RUNECLAW/blob/main/docs/TOKEN_ROADMAP.md)
for tokenomics detail, legal/compliance, the security checklist, KPIs, and the full list of
open decisions to ratify before launch — and
[§14 Verification status](https://github.com/Humanoid-Traders/RUNECLAW/blob/main/docs/TOKEN_ROADMAP.md#14-verification-status--what-is-actually-proven)
for precisely what has been proven versus merely built.
