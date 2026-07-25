# $RCLAW — Utility Token Roadmap

**The settlement and access layer for RUNECLAW's on-chain agent economy.**

> **Directional design, not a commitment or an offer.** Nothing in this document is
> financial advice or a solicitation to buy, sell, or hold any token. Order, scope, and every
> number below will shift with evidence, legal review, and regulation. A token is a
> **gated "◆ Vision" item** in [`ROADMAP.md`](./ROADMAP.md#guardrails) — it does **not** ship
> to users until the [Guardrails](#10-legal-compliance--risk-disclosures) are met. Trading
> derivatives and holding crypto assets involve substantial risk of loss. All figures here are
> a **proposed baseline to be ratified**, not fixed parameters.

> **Ticker note.** This document standardizes on **`$RCLAW`**. It supersedes the earlier
> `$CLAW` placeholder used in [`ROADMAP.md`](./ROADMAP.md); treat `$CLAW` as a legacy alias.

---

## Contents

1. [Vision — why a token](#1-vision--why-a-token)
2. [Token overview](#2-token-overview-proposed-baseline)
3. [Utility — what `$RCLAW` actually does](#3-utility--what-rclaw-actually-does)
4. [Tokenomics — allocation](#4-tokenomics--allocation)
5. [Presale mechanics](#5-presale-mechanics-proposed)
6. [Launch venue comparison + recommendation](#6-launch-venue-comparison--recommendation)
7. [Liquidity & DEX listing](#7-liquidity--dex-listing)
8. [Phased roadmap](#8-phased-roadmap)
9. [Cross-chain reality — Solana token ↔ Base identity](#9-cross-chain-reality--solana-token--base-identity)
10. [Legal, compliance & risk disclosures](#10-legal-compliance--risk-disclosures)
11. [Security & rug-resistance checklist](#11-security--rug-resistance-checklist)
12. [KPIs, community & go-to-market](#12-kpis-community--go-to-market)
13. [Open decisions / assumptions](#13-open-decisions--assumptions)

---

## 1. Vision — why a token

RUNECLAW today is an autonomous, risk-gated AI that trades perpetuals across four venues,
controllable from a Telegram bot and a web app, with a hard 23-check risk engine, a
self-improving learning loop, and a verifiable on-chain track record (Proof-of-PnL). The
[product roadmap](./ROADMAP.md) already points one direction: **"from an autonomous trader
to an on-chain agent economy."**

`$RCLAW` is the coordination, access, and settlement layer for that economy — **not a
fundraise for its own sake.** It exists to do three concrete jobs the platform already needs
a primitive for:

- **Access** — gate premium capability (scan tiers, higher live limits, priority agents)
  behind stake rather than a subscription silo.
- **Settlement** — meter agent-to-agent calls to the Shield risk engine (exposed as an MCP
  server and an on-chain tool) and split marketplace/copy-trading revenue.
- **Governance** — let holders steer risk parameters, new venues, and promoted strategies as
  the system decentralizes.

The token is deliberately the **last** piece, gated behind everything in §10. Utility comes
from features that already exist or are already on the roadmap; the token is the connective
tissue, not the product.

---

## 2. Token overview (proposed baseline)

| Field | Proposed value |
|---|---|
| Name | RUNECLAW Token |
| Ticker | **`$RCLAW`** |
| Chain | **Solana** |
| Standard | **SPL Token-2022** with on-chain **Metaplex** metadata |
| Transfer tax | **None** (DEX-friendly; no transfer hook that could break routing) |
| Total supply | **1,000,000,000 (1B), fixed — no inflation** |
| Decimals | **9** (Solana convention) |
| Mint authority | **Revoked** after the full supply is minted (supply can never increase) |
| Freeze authority | **Revoked** (no wallet can be frozen; credibly neutral) |
| Update authority | Held by a **Squads multisig**, then time-locked / renounced post-launch |

**Why Solana.** The launch venues the team is evaluating (Smithii, Metaplex Genesis,
Pump.fun/LetsBonk) are Solana-native, RUNECLAW already runs a **Solana ecosystem scan mode**
([`gitbook/solana-ecosystem.md`](./gitbook/solana-ecosystem.md)), and Solana's low fees suit
per-call metering. The one wrinkle — RUNECLAW's existing on-chain *identity* is on Base — is
addressed in §9.

**Why Token-2022 (not classic SPL).** Native metadata extension (no separate Metaplex
metadata program dependency) and a forward path to optional extensions later, while keeping
launch simple: **no transfer fees, no transfer hook** at TGE so every DEX and aggregator
routes it cleanly.

---

## 3. Utility — what `$RCLAW` actually does

Every utility below maps to a **real RUNECLAW feature or file**, so the token wires into the
existing platform rather than inventing generic use-cases.

| Utility | What it unlocks | Wires into (existing) |
|---|---|---|
| **Staking tiers** | Premium scan modes (`/scalp`, `/intraday`, `/swing`), higher per-user live limits, priority agent queue | `ROADMAP.md` §3 "$RCLAW staking"; scan commands in `bot/skills`; per-user gateway `bot/web/user_gateway.py` |
| **Fee discount + pay-in-token** | Pay platform / performance fees in `$RCLAW` at a discount; fees fund **buyback-and-burn** | `ROADMAP.md` §3/§5 revenue share |
| **MCP tool-call metering** | Settle agent-to-agent calls to the Shield risk engine (per-call, x402-style) | `bot/mcp/server.py`; ERC-8257 tool registry + x402 in [`ONCHAIN_GOLIVE.md`](./ONCHAIN_GOLIVE.md) |
| **Governance** | Vote on risk params, new venues, promoted strategies, fee splits | `ROADMAP.md` §3 "DAO governance", §5 compliance tiers |
| **Copy-trading / agent-marketplace** | Creators earn a share of follower fees; splits paid in-token | `ROADMAP.md` §4 |
| **Reputation staking** | Stake behind a verifiable, on-chain-anchored track record | `bot/proofofpnl/`, [`docs/proof_of_pnl/`](./proof_of_pnl/) |
| **Rewards / airdrop** | Referral perks and early-user rewards | `ROADMAP.md` §4 referral system; `app/routes/airdrops.js` |

**Design guardrail:** utility grants *access and coordination*, never a claim on profits or
a promise of return. Staking rewards are framed as protocol-fee sharing and emissions with
clear caps and disclosures (see §10), and **patterns/tokens may never override the risk
engine** — the same rule enforced in the AI learning system today.

---

## 4. Tokenomics — allocation

**Total supply: 1,000,000,000 `$RCLAW` (fixed).** Proposed distribution — a starting point to
tune before launch, not a fixed parameter:

| Bucket | % | Tokens | Vesting |
|---|---:|---:|---|
| Public presale | 15% | 150,000,000 | 33% at TGE, then linear over 2 months |
| DEX liquidity | 10% | 100,000,000 | Paired with raised SOL; LP burned/locked at TGE |
| Community & ecosystem (staking emissions, airdrops, rewards) | 25% | 250,000,000 | Released over 36 months |
| Team & contributors | 15% | 150,000,000 | 12-month cliff, then 24-month linear |
| Treasury / DAO | 20% | 200,000,000 | DAO-controlled multisig, time-locked |
| Partnerships & market makers | 8% | 80,000,000 | Deal-by-deal, 6–12 months |
| Advisors | 2% | 20,000,000 | 6-month cliff, then 18-month linear |
| Reserve / insurance fund | 5% | 50,000,000 | Locked; governance-unlockable only |
| **Total** | **100%** | **1,000,000,000** | — |

**Circulating supply at TGE (approx):** presale unlock (33% of 150M ≈ 49.5M) + DEX liquidity
(100M) + any airdrop TGE tranche. Team, treasury, advisors, and reserve are **fully locked at
TGE**, so initial float is a small fraction of supply — reducing early sell pressure. A full
**emissions / circulating-supply-over-time chart** should be published before the presale.

> **Reminder:** these percentages, vesting schedules, and the total supply are a **proposed
> baseline**. Finalize them with legal and market-maker input (see §13) before any mint.

---

## 5. Presale mechanics (proposed)

| Parameter | Proposed value |
|---|---|
| Soft cap | **1,000 SOL** |
| Hard cap | **5,000 SOL** |
| Presale allocation | 150,000,000 `$RCLAW` (15%) |
| Min contribution | **0.25 SOL / wallet** |
| Max contribution | **25 SOL / wallet** (anti-whale) |
| Round 1 — Whitelist / OG | 48 hours, discounted price |
| Round 2 — Public | 72 hours or until hard cap, standard price |
| Buyer vesting | **33% at TGE**, then linear over 2 months |
| Deposit window | Automated start/end timestamps |
| Claim window | Opens at TGE, automated |
| Anti-abuse | Anti-bot / anti-snipe at TGE; per-wallet caps; optional whitelist KYC (see §10) |

**Worked price example (illustrative).** At the **5,000 SOL hard cap** against the
150,000,000-token presale allocation, the effective presale price is **≈ 0.0000333 SOL per
`$RCLAW`** (≈ 30,000 `$RCLAW` per SOL). Whitelist Round 1 is priced below this; the public
round at this level. At an assumed SOL reference price, this implies a small, transparent
initial FDV — publish the exact SOL→USD assumption and resulting FDV alongside the sale so
buyers see it up front.

**If the soft cap is not met**, the sale is cancelled and contributions are **refundable** —
a hard requirement of whichever venue is chosen (see §6).

**Liquidity split of raised SOL:** **60% → DEX liquidity pool** (paired with the 100M
liquidity allocation), remainder to audit, operations, and treasury. Exact split ratified in
§13.

---

## 6. Launch venue comparison + recommendation

| Dimension | **Smithii Launchpad** | **Metaplex Genesis** | **Pump.fun / LetsBonk** |
|---|---|---|---|
| Model | No-code hosted launchpad | On-chain smart-contract + SDK | Bonding-curve fair launch |
| Caps (soft/hard) | ✅ Configurable | ✅ Configurable | ❌ None |
| Whitelist phases | ✅ | ✅ | ❌ |
| Vesting / claim windows | ✅ | ✅ (TGE, fixed-price, auctions) | ❌ (instant) |
| Trust model | Hosted / semi-custodial config | **Trustless, on-chain, auditable** | Trustless but no controls |
| Cost | ~**0.1 SOL + % of sales** | Program/deploy + audit effort | Minimal, curve fee |
| Dev effort | **Lowest** | Higher (SDK/contract integration) | Lowest |
| Dump risk | Controlled via vesting | Controlled via vesting | **High** (no vesting/caps) |
| Fit for a **vesting utility token** | Good | **Best** | Poor |

**Recommendation: Metaplex Genesis as the primary presale venue.** Its on-chain, trustless
fixed-price presale and TGE tooling is the **best aligned with RUNECLAW's Guardrails** —
*"proof over promises,"* non-custodial, and verifiable on-chain — which is exactly the posture
the rest of the platform already takes (non-custodial keys, on-chain-anchored track record).
The trade-off is more integration and a contract audit, both of which are required anyway
under §10–§11.

**Smithii Launchpad is the recommended fallback** if timelines demand the lowest-effort path:
it supports the same caps/whitelist/vesting controls with a no-code setup at ~0.1 SOL + a
percentage of sales, at the cost of a more hosted (less trustless) configuration surface.

**Do not use a pure Pump.fun / LetsBonk fair launch for the raise.** With no whitelist, no
vesting, and no caps, it is structurally wrong for a *utility token with a vesting schedule*
and invites immediate dumping. It remains a valid option only for an **optional, small
community fair-launch allocation later** (post-utility, as a community-distribution
experiment) — **never** the utility-token TGE.

---

## 7. Liquidity & DEX listing

- **Pool:** seed a `$RCLAW`/SOL pool on **Raydium** (or **Orca**), routable by **Jupiter** so
  every Solana aggregator picks it up.
- **Depth:** 100,000,000 `$RCLAW` (the 10% liquidity allocation) paired with 60% of raised SOL
  (§5). Worked FDV/price example carries over from §4–§5.
- **LP safety:** LP tokens **burned or locked for at least 12 months** — no silent liquidity
  pull. Lock proof published at TGE.
- **Market making:** the 8% partnerships/MM bucket funds a market maker to keep spreads tight
  in the first weeks; terms deal-by-deal (§4).
- **CEX path:** Bitget and other CEX listings are a **Phase 5** item (§8), gated on volume,
  compliance, and demand — never assumed.

---

## 8. Phased roadmap

Each phase has explicit **exit criteria** and sits under a **global kill switch**, mirroring
the *"staged rollout with caps"* principle in [`ROADMAP.md`](./ROADMAP.md#guardrails).

### Phase 0 — Foundations & Guardrails (pre-token)
Clear the existing Guardrails gate before anything is minted.
- Legal review per target jurisdiction; utility-not-security framing signed off.
- Tokenomics (§4) and presale params (§5) finalized with MM input.
- **Smart-contract / presale audit** commissioned.
- Non-custodial architecture confirmed; plain risk disclosures drafted.
- **Exit:** legal green-light + audit engaged + disclosures published.

### Phase 1 — Pre-launch
- Mint SPL Token-2022 supply; **revoke mint + freeze authority**; set Metaplex metadata.
- Stand up **Squads multisig** treasury + time-lock.
- Whitelist & community campaign; MM engagement; venue (Metaplex Genesis) setup.
- Publish audit report and LP-lock plan.
- **Exit:** audit passed, authorities revoked on-chain, whitelist filled, venue configured.

### Phase 2 — Presale & TGE
- Whitelist Round 1 → Public Round 2 → finalize (refund if soft cap missed).
- Seed DEX liquidity; **burn/lock LP**; open claim window.
- **Exit:** liquidity live, LP locked, tokens claimable, contract addresses published.

### Phase 3 — Utility activation
- Staking tiers live in bot/app: fee discounts, higher live limits, priority agents.
- Governance snapshot voting online (risk params, venues, strategies).
- **Buyback-and-burn** from platform/performance fees begins, on a published cadence.
- **Exit:** staking + governance usable end-to-end; first buyback executed and verifiable.

### Phase 4 — Ecosystem
- Agent-marketplace revenue split; copy-trading fee share.
- On-chain vaults (non-custodial, audited) and **MCP / x402 token settlement**.
- **Exit:** at least one third-party agent earning via the marketplace; x402 metering live.

### Phase 5 — Sustainability
- Insurance fund funded from fees; **proof-of-reserves** for any deposit product.
- CEX listings (demand + compliance gated); progressive **DAO handoff** of treasury/params.
- **Exit:** insurance fund capitalized; DAO controls a defined parameter set.

---

## 9. Cross-chain reality — Solana token ↔ Base identity

RUNECLAW's on-chain **identity and tool registry** live on **Base / EVM** — ERC-8004 identity
anchoring and the ERC-8257 tool registry with x402 metering, signed non-custodially by the
operator (`bot/proofofpnl/`, [`ONCHAIN_GOLIVE.md`](./ONCHAIN_GOLIVE.md), ethers-based). But
`$RCLAW` launches on **Solana**. This is a deliberate, manageable split:

- **Recommendation:** keep **`$RCLAW` Solana-native** (where the launch venues, liquidity, and
  the scan-mode community already are), and add an **optional Wormhole bridge to Base later**
  so the token can settle against the existing **ERC-8257 + x402** rail when marketplace
  metering goes live in Phase 4.
- **Convenient existing tie-in:** **Wormhole (`W`) and Drift are already in RUNECLAW's Solana
  scan universe** ([`gitbook/solana-ecosystem.md`](./gitbook/solana-ecosystem.md)) — the
  bridge and a Solana perp-DEX venue are already on the team's radar.
- **Frontend (draft implementation landed):** a lightweight Solana connect-and-sign flow now
  ships alongside the existing EVM/ethers path — `app/public/js/solana_wallet.js`
  (Phantom/Backpack via the injected provider), an ed25519 login-proof verifier
  `app/lib/solana_verify.js` (Node built-in `crypto`, no new dependency), and the
  `POST /api/auth/wallet/solana{,/nonce}` routes. It is draft/devnet and non-custodial (signs a
  login message only, never a transaction). A full `@solana/web3.js` wallet-adapter with
  Solflare/hardware support remains future work.

### Draft reference implementations in this repo

These are **draft, devnet, feature-flagged** starting points — not a launch:
- `token/` — SPL Token-2022 mint + verify scripts and the token config (§2, §4).
- `token/presale/genesis_presale.mjs` — **real `@metaplex-foundation/genesis` SDK integration**
  (Umi): a fixed-price presale via `initializeV2` + `addPresaleBucketV2`, with offline
  `presale:plan`, `presale:create`, `presale:deposit`, and `presale:claim` — all driven by
  `metaplex-genesis.config.json` (§5–§6). Smithii config + runbook remain the fallback.
- `bot/token/tier_gate.py` — the staking-tier gate (§3), OFF by default via
  `TOKEN_TIER_GATE_ENABLED`; gates `/scalp` `/intraday` `/swing`.
- `app/lib/solana_verify.js` + `app/public/js/solana_wallet.js` — the wallet connect-and-sign flow.

---

## 10. Legal, compliance & risk disclosures

This section is the **Guardrail gate** — nothing marked "Vision" ships until it is satisfied,
consistent with [`ROADMAP.md` § Guardrails](./ROADMAP.md#guardrails), `SECURITY.md`, `NOTICE`,
and the BUSL-1.1 license.

- **Utility, not investment.** `$RCLAW` grants access, settlement, and governance. Marketing
  must avoid any profit-expectation or "returns" framing. Staking rewards are described as
  protocol-fee sharing/emissions with caps and disclosures — never guaranteed yield.
- **Legal review per jurisdiction** before any token, vault, or revenue-share goes live.
- **Jurisdiction-aware access + KYC tiers** for the presale and for higher live limits;
  **exclude restricted jurisdictions** (e.g., US persons / sanctioned regions) as counsel
  advises. Geofencing at the sale and app layer.
- **Non-custodial by default** — users keep their keys; the protocol never takes custody it
  doesn't need. This matches the platform's existing posture.
- **Plain risk disclosures** — leverage, drawdown, smart-contract, and token-volatility risk
  stated up front, never buried. RUNECLAW remains an **educational prototype**; the token does
  not change that disclaimer.
- **Not an offer.** This document is directional design, not a solicitation.

---

## 11. Security & rug-resistance checklist

- [ ] **Mint authority revoked** after full supply minted (supply can never grow).
- [ ] **Freeze authority revoked** (no wallet freezes; credibly neutral).
- [ ] **LP burned or locked ≥ 12 months**, with public proof.
- [ ] **Squads multisig** treasury + **time-lock** on privileged actions; no single signer.
- [ ] **Independent audit** of the presale/vesting contracts (and any custom program) before
      the sale; report published.
- [ ] **Anti-snipe / anti-bot** at TGE; per-wallet caps enforced.
- [ ] **Metadata immutability** or multisig-gated update authority, renounced post-launch.
- [ ] **Verifiable on-chain reserves** before any deposit/vault product (Phase 4–5).
- [ ] Team/treasury/advisor allocations **on-chain-verifiable as locked** at TGE.

---

## 12. KPIs, community & go-to-market

**KPIs to publish and track:**
- Presale: participation (unique wallets), % of hard cap, whitelist conversion.
- Liquidity: pool depth, 2% slippage size, LP-lock duration remaining.
- Holders: holder count, top-10 concentration, staking ratio (staked / circulating).
- Governance: proposal count, voter turnout, quorum health.
- Protocol: fees collected, buyback-and-burn volume, MCP calls metered (Phase 4).

**Go-to-market:** lean on existing channels — the Telegram bot/community
([@HTRUNECLAW_bot](https://t.me/HTRUNECLAW_bot)), X, the GitBook docs, and the **referral
system** already shipped (`ROADMAP.md` §4). Sequence: audit + disclosures published →
whitelist campaign → presale → TGE + liquidity → utility activation. Proof (audit, LP lock,
locked allocations, verifiable track record) leads every announcement — *proof, not hype.*

---

## 13. Open decisions / assumptions

Everything below is a **proposed default that the team must ratify** — nothing here is fixed:

- **Final ticker** (`$RCLAW` assumed; confirm no Solana collision before mint).
- **Total supply & decimals** (1B / 9 assumed).
- **Allocation percentages and all vesting schedules** (§4).
- **Soft/hard caps, min/max contribution, round durations, presale price** (§5).
- **Liquidity split of raised SOL** (60% assumed) and **LP lock vs burn**.
- **Primary venue** (Metaplex Genesis recommended; Smithii fallback).
- **Jurisdiction exclusions and KYC threshold** (counsel-driven).
- **SOL→USD reference** used for any published FDV/price.
- **Wormhole bridge timing** and whether Base settlement is in scope for v1 (§9).

Once ratified, mirror the final numbers back into [`ROADMAP.md`](./ROADMAP.md) and the
condensed [GitBook page](./gitbook/token-roadmap.md) so the repo stays consistent.
