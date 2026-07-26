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

> **Implementation status — devnet draft, nothing launched.** Parts of this plan now exist as
> code in [`token/`](../token/), which changes what is *decided* versus *proposed*:
>
> - **Mint tooling** ([`token/README.md`](../token/README.md)) creates the Token-2022 mint,
>   mints the fixed supply and revokes mint + freeze authority, then verifies all of it. It
>   **refuses to run against mainnet**.
> - **Presale integration** against `@metaplex-foundation/genesis`
>   ([`token/presale/`](../token/presale/)) implements `plan | create | deposit | claim`,
>   driven entirely by `metaplex-genesis.config.json`. Also devnet-only.
> - Building it settled two things this document previously left open (§13) and **disproved
>   one assumption** about soft caps (§5).
>
> No token exists. No sale has run. Legal review and the audit in §10–§11 still gate
> everything, and every number below remains a baseline to ratify.

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
14. [Verification status — what is actually proven](#14-verification-status--what-is-actually-proven)

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
| Round 1 — Whitelist / OG | 48 hours, **priority access at the same fixed price** |
| Round 2 — Public | 72 hours or until hard cap, same fixed price |
| Buyer vesting | **33% at TGE**, then linear over 2 months |
| Deposit window | Automated start/end timestamps |
| Claim window | Opens at TGE, automated |
| Anti-abuse | Anti-bot / anti-snipe at TGE; per-wallet caps; optional whitelist KYC (see §10) |

**Worked price example (illustrative).** At the **5,000 SOL hard cap** against the
150,000,000-token presale allocation, the effective presale price is **≈ 0.0000333 SOL per
`$RCLAW`** (≈ 30,000 `$RCLAW` per SOL). **Both rounds transact at this price.** Round 1
confers earlier access to a shared cap, not a lower price. At an assumed SOL reference price, this implies a small, transparent
initial FDV — publish the exact SOL→USD assumption and resulting FDV alongside the sale so
buyers see it up front.

> **Correction (2026-07-26).** Two claims above previously described behaviour the
> implementation does not have, and were rewritten rather than left to be discovered at
> the sale:
>
> - **Round 1 was described as "discounted".** A Genesis presale prices a bucket by
>   `baseTokenAllocation / allocationQuoteTokenCap`, so the price is a property of the
>   **bucket**, not of the round. Both rounds draw on one bucket and one cap; the Merkle
>   allowlist gates *who may deposit during the whitelist window*, not *at what price*. A
>   genuine OG discount needs a second bucket with its own allocation and cap. Until that
>   exists, Round 1 buys priority, not a better price.
> - **"Refund if soft cap missed" was listed as a phase step.** Genesis has no native
>   soft-cap or refund field, and `softCapSol` reaches no on-chain account.
>   `refundIfSoftCapMissed` is now `false` and `derivePresaleParams()` throws if it is set
>   to `true`, so the promise cannot ship unimplemented.

**If the soft cap is not met**, the sale is cancelled and contributions are **refundable**.

> **Correction (from building it).** This was written as *"a hard requirement of whichever
> venue is chosen"* — an assumption that did not survive contact with the SDK. A Metaplex
> Genesis fixed-price presale is *"buy at a fixed price until the cap"*: `allocationQuoteTokenCap`
> is the **hard** cap, and there is **no native soft-cap or refund field**. A soft cap must
> therefore be enforced **operationally** (a published cancel-and-refund path) or via a
> min-raise extension, and the chosen mechanism must be settled and disclosed *before* the
> sale opens — not assumed. Tracked in §13.

**Liquidity split of raised SOL:** **66.67% → DEX liquidity pool** (paired with the 100M
liquidity allocation), remainder to audit, operations, and treasury. Exact split ratified in
§13.

> **Enforced on-chain (2026-07-26).** This split is no longer an operator promise.
> `presale:create` attaches a `SendQuoteTokenPercentage` end behavior to the presale bucket,
> which fixes the percentage at creation and names the liquidity bucket PDA as its
> destination. `presale:trigger` executes it and is **permissionless** — the underlying
> `triggerBehaviorsV2` instruction takes a payer but no authority — so any participant can
> run it once the deposit window closes, and the operator can neither change the share nor
> decline to send it. Editing the config afterwards has no on-chain effect; the bucket wins,
> and `presale:liquidity` refuses on the mismatch.

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

**Decided: Metaplex Genesis, and now integrated in draft.** The recommendation below has
been acted on — `token/presale/` drives a real Genesis presale (`initializeV2`,
`addPresaleBucketV2`, `depositPresaleV2`, `claimPresaleV2`) from config, on devnet, refusing
mainnet. Smithii remains the documented fallback but is no longer the expected path.

**Why Metaplex Genesis is the primary presale venue.** Its on-chain, trustless
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
- **Depth:** 100,000,000 `$RCLAW` (the 10% liquidity allocation) paired with 66.67% of raised SOL
  (§5). Worked FDV/price example carries over from §4–§5.
- **LP safety:** LP **permanently locked** — the Genesis path adds the pool via
  `addRaydiumCpmmBucketV2` with `createNeverClaimSchedule()`, so the LP position can never be
  claimed at all. This supersedes the earlier *"burned or locked for at least 12 months"*
  baseline: a permanent never-claim lock is strictly stronger than a 12-month one. Lock proof
  published at TGE.
- **Market making:** the 8% partnerships/MM bucket funds a market maker to keep spreads tight
  in the first weeks; terms deal-by-deal (§4).
- **CEX path:** Bitget and other CEX listings are a **Phase 5** item (§8), gated on volume,
  compliance, and demand — never assumed.

---

## 8. Phased roadmap

Each phase has explicit **exit criteria** and sits under a **global kill switch**, mirroring
the *"staged rollout with caps"* principle in [`ROADMAP.md`](./ROADMAP.md#guardrails).

> **Status honesty.** Draft tooling now exists for most of the *mechanics* below, but the
> project is still in **Phase 0**, and every Phase 0 exit criterion is a legal or audit gate
> that no amount of code satisfies. See [§14](#14-verification-status--what-is-actually-proven)
> for exactly what has been proven versus merely built.

### Phase 0 — Foundations & Guardrails (pre-token)
Clear the existing Guardrails gate before anything is minted.
- Legal review per target jurisdiction; utility-not-security framing signed off.
- Tokenomics (§4) and presale params (§5) finalized with MM input.
- **Smart-contract / presale audit** commissioned.
- Non-custodial architecture confirmed; plain risk disclosures drafted.
- *Done (draft):* devnet mint + presale tooling, the e2e harness, the staking program, and
  the NTT bridge script are all built and exercised as far as this environment allows
  ([`token/`](../token/), [`programs/rclaw_staking/`](../programs/rclaw_staking/)), and an
  adversarial review has already found and fixed 10 defects in them — including a **critical
  vault-drain** (§14). This de-risks Phase 1 but satisfies **no** Phase 0 exit criterion,
  all of which are legal/audit gates. If anything, the audit is evidence for why the gate
  exists.
- **Exit:** legal green-light + audit engaged + disclosures published.

### Phase 1 — Pre-launch
- Mint SPL Token-2022 supply; **revoke mint + freeze authority**; set Metaplex metadata.
- Stand up **Squads multisig** treasury + time-lock.
- Whitelist & community campaign; MM engagement; venue (Metaplex Genesis) setup.
- Publish audit report and LP-lock plan.
- *Draft exists:* the mint and presale steps are scripted end-to-end on devnet, and the
  whitelist is a Merkle allowlist (`presale:whitelist` → root applied at `presale:create`,
  proofs presented automatically during the whitelist window).
- **Exit:** audit passed, authorities revoked **on mainnet**, whitelist filled, venue
  configured. Nothing on devnet counts toward this.

### Phase 2 — Presale & TGE
- Whitelist Round 1 → Public Round 2 → finalize. (No automatic soft-cap refund exists —
  see the correction below and `metaplex-genesis.config.json`.)
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
- **Bridge mode (decided):** **hub-and-spoke — lock on Solana**, wrapped supply on Base.
  Burn-and-mint would require handing mint authority to an NTT-controlled PDA, which
  contradicts the fixed-supply / authorities-revoked guarantee in §2 and §11. A draft
  testnet implementation lives in `token/bridge/` (Solana devnet ↔ Base Sepolia).
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

These are **draft, devnet, feature-flagged** starting points — not a launch. What each
one has actually been *verified* to do is stated in
[§14 Verification status](#14-verification-status--what-is-actually-proven), which is the
section to read before trusting any of it.

- `token/` — SPL Token-2022 mint + verify scripts and the token config (§2, §4).
- `token/e2e/devnet_dryrun.mjs` — end-to-end lifecycle harness (keygen → create → liquidity
  → deposit → claim) driven by a generated near-now timeline, so the timestamp-gated paths
  can be exercised. `npm run e2e:plan` previews it fully offline; the live run needs devnet.
- `token/bridge/` — Wormhole **NTT** config + transfer script for Solana ↔ Base (§9),
  hub-and-spoke (lock-on-Solana) because the mint authority is revoked.
- `token/presale/genesis_presale.mjs` — **real `@metaplex-foundation/genesis` SDK integration**
  (Umi): a fixed-price presale via `initializeV2` + `addPresaleBucketV2`, a **Merkle whitelist**
  (`prepareAllowlist` → `presale:whitelist`, proof auto-presented on deposit), **Raydium
  liquidity with a permanent LP lock** (`addRaydiumCpmmBucketV2` + `createNeverClaimSchedule` →
  `presale:liquidity`), and **withdraw/refund** paths (`withdrawPresaleV1` /
  `withdrawUnsoldPresaleV1`). Offline `presale:plan` previews it all; everything is driven by
  `metaplex-genesis.config.json` (§5–§6). Smithii config + runbook remain the fallback.
- `bot/token/tier_gate.py` — the staking-tier gate (§3), OFF by default via
  `TOKEN_TIER_GATE_ENABLED`; gates `/scalp` `/intraday` `/swing`. When
  `RCLAW_STAKING_PROGRAM` is set it derives tiers from **staked** balance (read via
  `getProgramAccounts` + a `memcmp` on the stake account's owner) instead of wallet balance.
- `programs/rclaw_staking/` — **Anchor (Rust) staking program** (devnet/draft): `stake` /
  `unstake` with a per-user, **per-mint** `StakeAccount` PDA (`["stake", owner, mint]`) and a
  **mint-scoped** vault authority (`["vault", mint]`). Token-2022 aware (`transfer_checked`).
  Non-custodial (users can unstake at any time). The canonical mint can be pinned at build
  time with `RCLAW_PINNED_MINT=<address> anchor build` — deliberately **not** a hardcoded
  literal, because the `$RCLAW` mint does not exist yet and a placeholder in a security
  constant would either brick staking or fake the appearance of protection. Unset → any mint;
  set → others rejected with `UnexpectedMint`; malformed → **fails closed** with
  `InvalidPinnedMint`. ⚠️ **Still unaudited, and an earlier revision shipped a critical
  vault-drain bug** — see §14 and
  [`programs/rclaw_staking/README.md`](../programs/rclaw_staking/README.md). **Do not deploy**
  anywhere holding value until audited. The repo's first Rust code — root `Cargo.toml`
  workspace + `Anchor.toml`, with `tsconfig.json`/`package.json` supplying the `anchor test`
  TS toolchain.
- `app/lib/solana_verify.js` + `app/public/js/solana_wallet.js` — the wallet connect-and-sign
  flow; `/linkwallet` (Telegram) links the Solana address the tier gate reads.

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
- [ ] **LP permanently locked** (never-claim) or burned, with public proof.
- [ ] **Squads multisig** treasury + **time-lock** on privileged actions; no single signer.
- [ ] **Independent audit** of the presale/vesting contracts (and any custom program) before
      the sale; report published.
- [ ] **Anti-snipe / anti-bot** at TGE; per-wallet caps enforced.
- [ ] **Metadata immutability** or multisig-gated update authority, renounced post-launch.
      Revoking `MintTokens` fixes the *supply*; the metadata **update authority** and the
      **MetadataPointer authority** are separate and fix the *identity*. All four flags in
      `token/config/token.config.json` must be true, and `npm run verify` checks all four
      on-chain. Renounce only after the metadata URI points at immutable, content-addressed
      storage — renouncing first permanently freezes the token's identity onto a mutable URL.
- [ ] **Program upgrade authority** on `rclaw_staking` transferred to a **Squads multisig
      \+ time-lock** immediately post-deploy; single-signer deploy keys never retained.
      Whoever holds it can replace the bytecode and sign for every `["vault", mint]` PDA —
      it is the trust root for every staked lamport. Verify and publish the result:
      ```bash
      solana program show <PROGRAM_ID>                          # inspect current state
      solana program set-upgrade-authority <PROGRAM_ID> \       # do this FIRST
        --new-upgrade-authority <SQUADS_VAULT_PDA>
      solana program set-upgrade-authority <PROGRAM_ID> --final # only once audited — IRREVERSIBLE
      ```
- [ ] **Immutability plan published**: either `--final` once the program is stable, or a
      standing multisig + timelock with the quorum and delay stated up front.
- [ ] **IDL account initialized and its authority claimed, in the same session as the
      deploy.** `no-idl` is not enabled, so `anchor build` emits an IDL and Anchor 0.30.1
      stores it in a program-owned PDA whose authority goes to **whoever calls
      `anchor idl init` first** — not necessarily the deployer. Nothing in this repository
      initializes, versions, or assigns it today. Whoever holds it controls what every
      explorer, wallet, and client-side decoder believes this program's instructions and
      accounts are; it cannot change on-chain behaviour, but it can misrepresent it to
      everyone reading. Claim it immediately after deploy, then transfer it alongside the
      upgrade authority:
      ```bash
      anchor idl init --filepath target/idl/rclaw_staking.json <PROGRAM_ID>
      anchor idl authority <PROGRAM_ID>                        # verify who holds it
      anchor idl set-authority --new-authority <SQUADS_VAULT_PDA> --program-id <PROGRAM_ID>
      ```
      An unclaimed IDL account is a live squatting surface for as long as it stays unclaimed.
- [ ] **Key custody**: the mint/metadata/presale/LP authority is one keypair at
      `token/.keys/mint-payer.json` (mode 0600, enforced at load). Move it to the multisig
      before any value-bearing run — a single file read is otherwise total compromise of
      both the supply and the presale proceeds.
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
- **Liquidity split of raised SOL** (**66.67%**, ratified 2026-07-26 — see below). ~~LP lock vs burn~~ — **settled:**
  permanent never-claim lock (§7).
- ~~**Primary venue**~~ — **settled:** Metaplex Genesis, integrated in draft (§6). Smithii
  remains a documented fallback only.
- **Jurisdiction exclusions and KYC threshold** (counsel-driven).
- **SOL→USD reference** used for any published FDV/price.
- **Wormhole bridge timing** and whether Base settlement is in scope for v1 (§9).

**Opened by building the integration — these did not exist as questions before:**

- **Soft-cap enforcement mechanism.** Genesis has no native soft-cap/refund (§5). Choose an
  operational cancel-and-refund path or a min-raise extension, and disclose it before the sale.
- **Presale funding mode** — `mint` (Genesis initializes and mints the supply itself) vs
  `transfer` (pre-mint with `token/` tooling and transfer from the treasury ATA). The config
  defaults to `mint` for a self-contained devnet demo; a real launch that pre-mints and
  verifies authorities first probably wants `transfer`. The on-chain numeric values must be
  confirmed against the Genesis program before mainnet.

Once ratified, mirror the final numbers back into [`ROADMAP.md`](./ROADMAP.md) and the
condensed [GitBook page](./gitbook/token-roadmap.md) so the repo stays consistent.

---

## 14. Verification status — what is actually proven

Read this before trusting anything in §8's "shipped" language. Working code and *verified*
code are different claims, and conflating them is how the vault-drain bug below shipped
green in the first place.

### An adversarial review found real defects in this work

After the draft implementations landed, they were reviewed adversarially: independent
finders across the Anchor program, the Python tier gate, the presale scripts, the e2e and
bridge tooling, the wallet-auth surface, and documentation accuracy — each finding then put
to two independent skeptics instructed to **refute by default**. 35 candidates, **18
confirmed**, deduplicated to **10 real defects**. All were fixed.

The headline was **critical**: `StakeAccount` recorded no mint and `unstake` accepted an
arbitrary one, while a single global `["vault"]` authority owned every per-mint vault. An
attacker could mint a worthless token, stake it, and redeem the same amount from the **real
`$RCLAW` vault** — draining it, locking honest stakers out, and granting free `elite` tier.
Also found: two presale commands that could never run (missing required serializer fields), a
Token-2022/legacy-SPL mismatch that made the real mint unstakeable, a **zero-length whitelist
window** (`timeline.whitelistStart` was read nowhere), a tier gate that blocked every user
because nothing ever linked a wallet, and a documented 60%-to-liquidity split that was only
ever printed, never encoded on-chain.

Two lessons are baked into the code now rather than just noted:

- **`cargo check` + passing tests did not catch a soundness hole.** The fix is therefore
  *executed*: `programs/rclaw_staking/tests/attack.rs` performs the exact attack in-process
  and asserts it is rejected (`ConstraintSeeds`) with the honest vault balance unchanged.
- **Some tests monkeypatched away the logic they claimed to test.** New regressions were
  checked for vacuity — e.g. the allowlist serialization test fails with the exact
  `TypeError` the audit predicted when the fix is removed.

### Verification matrix

| Component | Verified how | Not verified |
|---|---|---|
| `rclaw_staking` program | **Executed in-process** (`solana-program-test`): 4 unit + 4 integration tests, pinned and unpinned; attack rejected, balances asserted | **No audit**; no SBF/BPF runtime (compute budget, serialization limits); never on devnet/mainnet |
| `PINNED_MINT` | Enforcement observed at runtime (`UnexpectedMint` 6005); malformed pin fails closed | No real mint exists to pin yet |
| Tier gate (`tier_gate.py`) | 17 tests incl. mint-filter and byte-layout locks | Never read a real on-chain stake account |
| Genesis presale scripts | All SDK exports resolve; `presale:plan` derives real params offline; allowlist args serialize with the real serializer | `create`/`deposit`/`claim`/`liquidity`/`withdraw` **never sent a transaction** |
| e2e harness | `e2e:plan` runs offline end-to-end | The live devnet run has never executed |
| Wormhole bridge | Script resolves + typechecks | No NTT deployment, no transfer |
| Anchor TS spec | `npm run typecheck` passes | **Never executed** — needs the Anchor/Solana CLIs |

### Why the gaps exist

They are environmental, not optional. The authoring environment's egress policy returns
**403 CONNECT** for `api.devnet.solana.com` and `faucet.solana.com`, and blocks
`release.anza.xyz`/GitHub — so the Solana and Anchor CLIs cannot be installed, `anchor build`
and `anchor test` cannot run, and no devnet transaction is possible. In-process execution
via `solana-program-test` was used because `index.crates.io` *is* reachable.

### Before any deployment holding value

1. **Independent smart-contract audit** — Phase 0, non-negotiable. One critical bug
   found-and-fixed is not an audit.
2. Run `cargo test -p rclaw_staking`, `anchor build && anchor test`, and
   `npm run e2e:dryrun` from a network-capable machine.
3. Run `anchor keys sync` and confirm the placeholder program id
   `Fg6PaFpoGXkYsidMpWTK6W2BeZ7FEfcYkg476zPFsLnS` appears nowhere in the tree. CI
   fails the build if it does, but the sync itself is a manual step.
4. Set `RCLAW_PINNED_MINT` (program) and `RCLAW_MINT` (bot gate) to the real mint.
5. **Transfer the program upgrade authority off the deploy key — before the vault
   accepts its first deposit, not after.** See §11; the window between deploy and
   transfer is the entire exposure.
6. Ratify every §13 parameter, including `LOCKUP_SECONDS` in
   `programs/rclaw_staking/src/lib.rs` (currently 7 days) — it is a tokenomics
   choice, not a security constant.
