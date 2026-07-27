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
| DEX liquidity | 2.0001% | 20,001,000 | Paired with raised SOL; LP locked forever (never-claim) |
| Community & ecosystem (staking emissions, airdrops, rewards) | 25% | 250,000,000 | Released over 36 months — **Streamflow stream**, daily periods from TGE |
| Team & contributors | 15% | 150,000,000 | 12-month cliff, then 24-month linear — **Streamflow stream** |
| Treasury / DAO | 20% | 200,000,000 | DAO-controlled multisig, time-locked |
| Partnerships & market makers | 8% | 80,000,000 | Deal-by-deal, 6–12 months |
| Advisors | 2% | 20,000,000 | 6-month cliff, then 18-month linear — **Streamflow stream** |
| Reserve / insurance fund **+ post-TGE liquidity** | 12.9999% | 129,999,000 | Locked; governance-unlockable only |
| **Total** | **100%** | **1,000,000,000** | — |

**The DEX liquidity row moved from 10% to 2.0001% on 2026-07-26, and the 79,999,000
difference is in the reserve row, earmarked.** This is the F-25 decision and it is
not a tokenomics preference — it is forced by how the venue works.

The LP token side is fixed when the bucket is created and can never be changed
(`updateRaydiumCpmmBucketV2` is rejected once the genesis account is finalized,
error `0x2b`; deposits are impossible before finalize, `0x2c`). The SOL side is
whatever is raised. So the pool's opening price is **linear in the raise**:

| LP allocation | Pool opens at a 1,000 SOL raise | at 5,000 SOL (hard cap) |
|---|---|---|
| 100,000,000 (the old 10%) | **5x BELOW** the presale price | at the presale price |
| 20,001,000 (now) | at the presale price | 5x above it |

At 10% the pool opened at what buyers paid **only on a full raise**, and
proportionally under it on anything less. The two failure modes are not
symmetric, and that asymmetry is the whole argument:

- **Below the presale price** puts every buyer underwater the moment trading
  opens — permanently, because the LP lock is never-claim and **no refund
  instruction exists for a V2 presale** (proven, §14). Unrecoverable.
- **Above the presale price** leaves a pool that is thin next to the
  150,000,000 tokens in presale hands, so early price discovery is violent.
  Bad — but recoverable, by adding liquidity once the raise is known.

The irreversible bet is therefore taken on the side that can be corrected, and
the correction is funded: the 79,999,000 sits in the reserve bucket earmarked for
post-TGE liquidity provisioning. Deploying it is a governance action.

**What this does not fix.** `softCapSol` reaches no on-chain account, so the
program permits a raise below 1,000 SOL, and at that level this allocation still
opens the pool under the presale price. No allocation closes that gap — the value
required for parity falls to zero with the raise. `presale:trigger` therefore
reads the realised raise from the bucket and **refuses** to open the pool below
the presale price unless `--accept-below-presale` is passed, and
`config.disclosures.softCapNotEnforced` says so publicly.

**Circulating supply at TGE (approx):** presale unlock (33% of 150M ≈ 49.5M) + DEX liquidity
(20.001M) + any airdrop TGE tranche. Team, treasury, advisors, and reserve are **fully locked at
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

> **Encoded on-chain and publicly verifiable (2026-07-26).** `presale:create` attaches a
> `SendQuoteTokenPercentage` end behavior to the presale bucket, naming the liquidity bucket
> PDA as its destination. `presale:trigger` executes it and is **permissionless** — the
> underlying `triggerBehaviorsV2` takes a payer but no authority — so any participant can run
> it once the deposit window closes. Editing the config afterwards has no on-chain effect: the
> bucket wins, and `presale:liquidity` / `presale:trigger` both re-read the **live account**
> and refuse on any mismatch.
>
> **Correction (2026-07-26, same day).** This paragraph previously said the percentage is
> "fixed at creation" and that "the operator can neither change the share nor decline to send
> it". **That was wrong**, and it overstated the guarantee. The Genesis SDK exposes
> `setPresaleBucketV2Behaviors` (discriminator 80), which takes the genesis authority and
> replaces `endBehaviors` wholesale — so the authority can lower the percentage, repoint the
> destination, or delete the behavior entirely at any point before it is triggered. The
> permissionless trigger means a participant can win that *race*; it does not remove the
> authority's power.
>
> The honest claim is **verifiable, not immutable**: the value lives in an account anyone can
> read, and the tooling checks it against this config before every irreversible step, so a
> silent change is detectable rather than prevented. Making it genuinely immutable requires
> `treasury.authority` to be a Squads multisig — see §11, which already requires that for
> other reasons. Two further authority powers were found in the same review and are recorded
> in §11: `updateGenesisV2` can mint or burn base supply, and `updateRaydiumCpmmBucketV2` can
> change the LP token allocation.

**Pool pricing — the fix is at configuration time, not after the raise
(2026-07-26, corrected same day).** The LP token side is fixed while the SOL side
scales with the raise, so a soft-cap raise opens the pool ~5x *below* what
presale buyers paid, against a permanent LP lock (audit F-25).

> **A correction.** Earlier today this section described `presale:rebalance-lp`,
> which scaled the token side to the realised raise via
> `updateRaydiumCpmmBucketV2` after the deposit window closed. **That command has
> been removed: it cannot run.** Proven against devnet —
>
> ```
> Program log: UpdateRaydiumCpmmBucketV2
> Program log: The Genesis Account is already finalized and no new buckets can be added
> ```
>
> The ordering is a closed trap: `depositPresaleV2` requires the genesis account
> to be **finalized** (error `0x2c`), `finalizeV2` **permanently locks** bucket
> configuration (error `0x2b`), and the realised raise is only known **after**
> deposits. By the time the number needed to compute the allocation exists, the
> allocation is already immutable. No ordering escapes this.

The real fix is to size `liquidity.tokenAllocation` for the **soft cap** rather
than the hard cap, before `presale:create`. At the current parameters that is
**20,001,000 tokens**, not 100,000,000 — `npm run presale:plan` computes and
prints the number. Sized that way the pool opens at or above the presale price at
every raise between the caps; sized at 100M it does not.

**Settled 2026-07-26: 20,001,000.** It was a tokenomics decision rather than a
code change, and the argument that decided it is the asymmetry between the two
ways of being wrong — a pool below the presale price is unrecoverable (permanent
lock, no refund), a thin pool is not. §4 carries the full reasoning, the §4 table
now reads 2.0001%, and the 79,999,000 freed is earmarked in the reserve bucket to
fund the correction. The remaining exposure — a raise below the sized-for floor —
is guarded at trigger time and disclosed in §10 rather than papered over.

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
- **Depth:** 20,001,000 `$RCLAW` (the 2.0001% liquidity allocation) paired with 66.67% of raised
  SOL (§5). Worked FDV/price example carries over from §4–§5. **This is deliberately thin, and
  the reason is in §4:** the token side is frozen at bucket creation while the SOL side is
  whatever gets raised, so sizing it for a full raise guarantees an underwater pool on anything
  less — permanently, with no refund. Sized for the soft cap instead, the pool opens at or above
  the presale price from 1,000 SOL up.
- **Post-TGE depth is a funded, deliberate follow-up, not an afterthought.** The 79,999,000
  freed by that sizing sits in the reserve bucket earmarked for it. Once the raise is known the
  right depth is knowable, and adding liquidity is a governance action that can be taken; the
  opposite mistake cannot be undone. Publish the intent alongside the sale terms so a thin
  opening pool is not read as a rug.
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

### The refundable alternative — considered and rejected (2026-07-26)

**Corrected 2026-07-26.** This roadmap and the audit both said a refund was
impossible with Metaplex Genesis. That was wrong: it is impossible with the
**fixed-price presale bucket** this tooling builds, and the program offers
another bucket type that supports exactly what §5 originally wanted.

| | Fixed-price presale (current) | LaunchPool |
|---|---|---|
| Price | **Fixed and known** — 30,000 tokens/SOL | Pro-rata: a fixed token allocation split across all deposits, so price depends on total demand |
| Buyer knows their allocation | **Yes**, at deposit time | No — not until the sale closes |
| Soft cap | Descriptive only, unenforceable | **Enforced on-chain** (`SoftCap` extension) |
| Refund if soft cap missed | **None** | `refundLaunchPoolV2` |
| Exit during the sale | None | `withdrawLaunchPoolV2`, subject to `withdrawPenalty` |

Each structure hands the buyer a different uncertainty. Fixed-price guarantees
what they receive but nothing about getting their money back. A launch pool
guarantees the downside but not the allocation. Neither is strictly better, which
is precisely why the tooling has **not** switched on its own — moving would
rewrite §5's mechanics and §4's fixed-price arithmetic.

**Proven:** the instructions, the `SoftCap` extension and the
`LaunchPoolFundingThresholdNotMet` / `LaunchPoolThresholdMet` error codes all
exist in the SDK. **Not proven:** that the refund is gated on the threshold being
missed — inferred from the error names, **not executed**. Held to the same
standard as everything else here, that means it must be run on a validator before
anyone relies on it.

**Decision: the fixed-price presale stays.** §6 selected Metaplex Genesis
fixed-price on the strength of allocation certainty, and the existence of a
refund path elsewhere in the same program does not change that reasoning — it
changes which uncertainty the buyer carries, not how much there is. The refund
gap is **disclosed** (§10) rather than hidden, which is the honest way to carry
it.

**What would reopen it** — a real bar, not a formality:

1. **Counsel requires a refund** for a target jurisdiction, which would make the
   current structure unusable there rather than merely riskier.
2. **Evidence that buyers price allocation uncertainty below principal risk** —
   for instance a comparable Solana launch where the pro-rata structure
   demonstrably raised more.
3. **The soft cap becomes a hard commitment** in published terms, since it cannot
   be enforced on this bucket type.

Absent one of those, this is settled. If it ever reopens, note that the refund
gating is **inferred and never executed** — it would need a validator run before
anything is built on it.

### Unsold presale tokens

A partial raise leaves `presaleAllocation - sold` in the presale bucket, and no
V2 instruction can move it out — `withdrawUnsoldPresaleV1` rejects a V2 genesis
account. Without a mechanism attached **at creation** (end behaviors cannot be
added after finalize) that supply is stranded permanently; at a soft-cap raise
that is roughly 120,000,000 tokens, 12% of supply, frozen in an account no key
can reach.

`sale.unsoldRollover` attaches a `BaseTokenRollover` behavior routing **100% of
unsold presale tokens to the `reserve` bucket**, executed by the permissionless
`presale:trigger`. Reserve is governance-gated behind a long cliff, so nothing
reaches the market on its own, and it is already earmarked for post-TGE
liquidity — which is what a weak raise needs.

The destination is the decision, and the alternatives were rejected for
concrete reasons: an insider bucket (team, advisors, partnerships, treasury)
would raise their share above the §4 table precisely when the sale
underperformed; the liquidity bucket would add base tokens against an unchanged
SOL side and push the opening price *down*, punishing a weak raise twice; a
Streamflow bucket would never release the rolled-in tokens, because
`amountPerPeriod` was derived from the original allocation.

Proven on a validator: 149,999,706 unsold tokens moved to reserve, with exactly
the 294 tokens the deposit bought left behind. `presale:trigger` reads
`baseTokenBalance` either side and fails if the numbers do not balance or if the
behavior moved nothing while unsold tokens remain.

### Mandatory sale-terms disclosures

Three facts about *this* sale are not visible from its marketing surface and are
not optional to publish. Each was established by executing the thing rather than
reading about it, and each is held in one place —
`token/presale/metaplex-genesis.config.json` → `disclosures` — which
`presale:plan` prints in full on every run, so the sale cannot be planned without
them being seen. `token/presale/lp_parity.test.mjs` fails if any is removed.

1. **There is no refund, at any raise level — with this sale structure.** Once a
   deposit lands in the fixed-price V2 presale bucket this tooling builds, no
   instruction returns it: `withdrawPresaleV1` and `withdrawUnsoldPresaleV1` are
   V1-only and reject a V2 genesis account (`0x2f`), and the SDK ships no V2
   equivalent *for a presale bucket*. Published terms must not promise a refund
   of any kind while the sale uses this bucket type.

   **Corrected 2026-07-26:** this previously read "no instruction in the Genesis
   program can return it", which was wrong. The program ships
   `refundLaunchPoolV2`, `withdrawLaunchPoolV2` and an on-chain `SoftCap`
   extension — on **LaunchPool** buckets. A refundable, soft-cap-enforced sale is
   possible with this venue; it is unavailable here because of the bucket type
   chosen, not a missing feature. That is a product decision — see §5, *The
   refundable alternative*.

2. **The program running the sale is upgradeable, by someone else.** Metaplex
   Genesis (`GNS1S5J5AspKXgpjz6SvKL66kPaKWAhaGRhCqPRxii2B`) custodies the entire
   raise, every vesting schedule and the never-claim LP lock. Its mainnet-beta
   upgrade authority is `bfQVv6niKVgEURYqQ1beJmiEQQN7MrvLRvk3mZGFubb` — a third
   party, not RUNECLAW — which can replace that bytecode at any time with no
   change to this repository and no signal to holders. **Every immutability claim
   in §11 holds within the current Genesis program, not against it.** The
   authority is an off-curve address, so a PDA rather than a bare keypair
   (consistent with a multisig vault); the threshold is not observable and is not
   ours to set. The other four programs in the money path — SPL System, SPL
   Token, SPL Associated Token, Metaplex Token Metadata — are all immutable on
   mainnet-beta. Measured 2026-07-26; re-run `npm run programs:inventory` before
   launch in case the authority or the deployed slot has moved.

3. **The soft cap is descriptive, and the listing price depends on the raise.**
   `softCapSol` reaches no on-chain account; nothing stops the sale ending below
   it. The pool opens at or above the presale price only if the raise reaches
   `liquidity.sizedForRaiseSol`. Terms must state that level rather than imply a
   floor that does not exist.

Counsel should review the wording. The facts are measured and not negotiable.

---

## 11. Security & rug-resistance checklist

- [ ] **Mint authority revoked** after full supply minted (supply can never grow).
- [ ] **Freeze authority revoked** (no wallet freezes; credibly neutral).
- [ ] **LP permanently locked** (never-claim) or burned, with public proof.
- [ ] **Squads multisig** treasury + **time-lock** on privileged actions; no single signer.
- [ ] **`treasury.authority` set to the Squads vault PDA *at `presale:create`*.** This is
      immutable once `initializeV2` runs, and a review of the Genesis SDK on 2026-07-26 found
      the genesis authority is considerably more powerful than this roadmap previously
      assumed. It can:

      | Instruction | Power |
      |---|---|
      | `setPresaleBucketV2Behaviors` | replace the presale bucket's `endBehaviors` wholesale — lower, repoint or delete the raise→liquidity split before it is triggered |
      | `updateRaydiumCpmmBucketV2` | change the LP `baseTokenAllocation` and the pool start condition |
      | `updateGenesisV2` | **mint or burn base supply** (`mintAmount` / `burnAmount`), and transfer the authority |
      | `updatePresaleBucketV2` | change presale bucket parameters |
      | `revokeV2` | revoke the base mint's mint/freeze authority |

      Two consequences worth stating plainly. First, in `mint` funding mode the fixed-supply
      guarantee is **not** established until `revokeV2` is called — until then the genesis
      authority can mint more; add it to the post-sale runbook. Second, none of these powers
      can be renounced: `revokeV2` revokes the *token's* authorities, not the genesis
      account's, and there is no "renounce genesis authority" instruction. A multisig is the
      only available mitigation, which is why this item is a hard gate rather than a
      preference.
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
- **Allocation percentages and all vesting schedules** (§4) — except the two rows below,
  which are now settled.
- ~~**DEX liquidity allocation**~~ — **settled: 20,001,000 (2.0001%)**, ratified 2026-07-26,
  with the 79,999,000 difference moved to the reserve bucket and earmarked for post-TGE
  liquidity. This was F-25 and it had to be decided before `presale:create`, because the LP
  token side is immutable from bucket creation and the raise is only known afterwards.
  Reasoning and the two-failure-mode asymmetry in §4; the arithmetic is pinned in
  `token/presale/lp_parity.test.mjs` and the number is enforced at trigger time.
- **Soft/hard caps, min/max contribution, round durations, presale price** (§5).
- **Liquidity split of raised SOL** (**66.67%**, ratified 2026-07-26 — see below). ~~LP lock vs burn~~ — **settled:**
  permanent never-claim lock (§7).
- ~~**Staking lock-up period**~~ — **settled:** **30 days**, ratified 2026-07-26.
  `LOCKUP_SECONDS` in `programs/rclaw_staking/src/lib.rs`. Without a lock the tier is a live
  spot balance, so one position can be unstaked and re-staked to another wallet in the same
  slot and serve unlimited users by rotation; the lock is what makes a tier cost something to
  hold. It is read at stake time and written into each record's `unlock_at`, so changing it
  later applies to **new deposits only** — existing positions keep the unlock they were
  promised. Safe to change freely today because nothing is deployed.
- ~~**Primary venue**~~ — **settled:** Metaplex Genesis, integrated in draft (§6). Smithii
  remains a documented fallback only.
- **Jurisdiction exclusions and KYC threshold** (counsel-driven).
- **SOL→USD reference** used for any published FDV/price.
- **Wormhole bridge timing** and whether Base settlement is in scope for v1 (§9).

**Opened by building the integration — these did not exist as questions before:**

- ~~**Sale structure: fixed-price vs refundable launch pool**~~ — **settled
  2026-07-26: fixed-price presale kept.** Investigated after discovering the
  program does support a refundable, soft-cap-enforced LaunchPool bucket. Kept
  for allocation certainty; the refund gap is disclosed instead. §5 records the
  three things that would reopen it.
- **Soft-cap enforcement mechanism.** Genesis has no native soft-cap/refund
  **for the presale bucket type this sale uses** (§5). Choose an
  operational cancel-and-refund path or a min-raise extension, and disclose it before the sale.
  **Partially addressed 2026-07-26:** there is still no on-chain enforcement and there cannot
  be — but `presale:trigger` now reads the realised raise from the bucket and refuses to open
  a pool below the presale price without an explicit `--accept-below-presale`, and
  `config.disclosures.softCapNotEnforced` states publicly that no floor exists. That converts
  a silent irreversible step into a deliberate one. It does not create a refund, and nothing
  can: declining to trigger leaves the raise in the presale bucket with no V2 withdraw path,
  so **the operational cancel-and-refund path is still an unanswered question and still
  blocks any sale that promises one.**
- **Disclosure of the Genesis upgrade authority — decided, needs legal sign-off on wording.**
  `config.disclosures.programIsUpgradeable` states that Metaplex Genesis is upgradeable by
  `bfQVv6niKVgEURYqQ1beJmiEQQN7MrvLRvk3mZGFubb`, a third party, and that every immutability
  claim this project makes holds *within* that program rather than against it. `presale:plan`
  prints it on every run so it cannot be shipped unseen. What remains is counsel reviewing the
  wording for the published terms — the fact itself is measured and not negotiable.
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

### Verification is now free, and that changed what gets checked

Devnet SOL is faucet-limited to 10 SOL per 8 hours, which made every full
lifecycle run a scarce resource and quietly biased the work toward *not*
re-running things. The whole presale now runs against a local
`solana-test-validator` instead — programs cloned from devnet, no faucet, no
budget, resettable at will:

```bash
solana-test-validator --reset --quiet --ledger /tmp/ledger \
  --url https://api.devnet.solana.com \
  --clone-upgradeable-program GNS1S5J5AspKXgpjz6SvKL66kPaKWAhaGRhCqPRxii2B \
  --clone-upgradeable-program metaqbxxUerdq28cj1RbAWkYQm3ybzjb6a8bt518x1s
RPC_URL=http://127.0.0.1:8899 npm --prefix token run e2e:dryrun
```

`assertDevnet()` accepts this by *structure*, not by allowlist: a local
validator generates a fresh genesis hash on every `--reset`, so it can never be
named in advance. An unrecognised chain reached over **loopback** is a validator
on this machine and holds nothing of value. Mainnet is still refused first and
unconditionally, by genesis hash, before that reasoning runs — so tunnelling
`127.0.0.1:8899` to a real mainnet RPC gains nothing. The loopback test parses
the URL rather than substring-matching it, because `https://localhost.evil.com`
is exactly the mistake that check exists to avoid.

**The narrower environment found a dependency the full one hid.** A validator
carrying only the programs the sale genuinely needs turned an invisible
dependency into a hard failure: `presale:deposit` died with
`Attempt to load a program that does not exist`. The missing program was
`TokExjvjJmhKaRBShsBAsbSvEWMA1AgUNK7ps4SAc2p` — **MPL Token Extras**, a
third-party upgradeable program that `createTokenIfMissing` invokes internally,
and that the deposit, trigger and claim paths therefore all ran through. It is
deployed on mainnet, devnet and testnet, so nothing would ever have failed;
it appeared in no config, no runbook and no dependency list, because the source
says `createTokenIfMissing` and nothing says `TokExjvjJ…`.

Those three paths now build the SPL Associated Token program's own
`CreateIdempotent` instruction directly, so they reach nothing but SPL System,
SPL Token and SPL Associated Token. Full write-up in
`docs/TOKEN_SECURITY_AUDIT.md`, *New (2026-07-26)*.

The general lesson is cheap to apply and now wired into CI: **a minimal test
environment is a dependency inventory that a realistic one cannot perform.**
Devnet has everything, so on devnet every dependency is satisfied and none is
disclosed.

### Verification matrix

| Component | Verified how | Not verified |
|---|---|---|
| `rclaw_staking` program | **Executed in-process** (`solana-program-test`): 4 unit + 4 integration tests, pinned and unpinned; attack rejected, balances asserted | **No audit**; no SBF/BPF runtime (compute budget, serialization limits); never on devnet/mainnet |
| `PINNED_MINT` | Enforcement observed at runtime (`UnexpectedMint` 6005); malformed pin fails closed | No real mint exists to pin yet |
| Tier gate (`tier_gate.py`) | 17 tests incl. mint-filter and byte-layout locks | Never read a real on-chain stake account |
| Genesis presale scripts | **Executed on devnet and on a local validator** (2026-07-26): `create` → `liquidity` → `allocate` → `finalize` → `deposit` → `trigger` → `claim` all land. `presale:plan` derives real params offline; allowlist args serialize with the real serializer | `withdraw`/`withdraw-unsold` are V1-only and **cannot run at all** against the V2 presale this tooling creates. Unsold supply is handled instead by a `BaseTokenRollover` behavior (executed and verified); the **depositor refund has no mechanism for this bucket type**; a LaunchPool bucket does have one (`refundLaunchPoolV2`), untested — see §5 |
| e2e harness | **Full lifecycle green** against a local validator, and previously against devnet. The **gated round is proven too** (`npm run e2e:whitelist`): invited wallet admitted, uninvited wallet refused, uninvited admitted after expiry — two wallets, on chain | Never run against a public cluster; no concurrency coverage beyond the two-wallet allowlist test |
| Wormhole bridge | Script resolves + typechecks | No NTT deployment, no transfer |
| Anchor TS spec | `npm run typecheck` passes | **Never executed** — needs the Anchor/Solana CLIs |

### Why the gaps exist

They are environmental, not optional. The authoring environment's egress policy returns
**403 CONNECT** for `api.devnet.solana.com` and `faucet.solana.com`, and blocks
`release.anza.xyz`/GitHub — so the Solana and Anchor CLIs cannot be installed, `anchor build`
and `anchor test` cannot run, and no devnet transaction is possible. In-process execution
via `solana-program-test` was used because `index.crates.io` *is* reachable.

**No longer true as of 2026-07-26.** Devnet, the faucet and `release.anza.xyz`
are all reachable now; the Solana CLI is installed, the SBF artifact builds, the
presale runs end to end, and the bytecode has been deployed and executed. What
that means for this section is worth stating plainly: **every gap listed above
was described as environmental, and every one of them was hiding a real defect.**
Restoring the environment did not confirm the work — it produced thirteen
blocking bugs across paths that had never run, three of which were introduced by
earlier fixes in this same effort. Treat any remaining "cannot be verified here"
as an open finding rather than an excuse, because that is what each of these
turned out to be.

### Before any deployment holding value

1. **Independent smart-contract audit** — Phase 0, non-negotiable. One critical bug
   found-and-fixed is not an audit.
2. Run `cargo test -p rclaw_staking`, `anchor build && anchor test`, and
   `npm run e2e:dryrun` from a network-capable machine.
3. **Run `anchor keys sync`. This is a hard blocker, not hygiene.** Confirm the
   placeholder id `Fg6PaFpoGXkYsidMpWTK6W2BeZ7FEfcYkg476zPFsLnS` appears nowhere
   in the tree. CI fails a value-bearing build if it does, but the sync itself is
   manual.

   Proven by deploying the real bytecode to a local validator on 2026-07-26:
   Anchor **refuses to execute** when the deployed address differs from
   `declare_id!` —

   ```
   AnchorError: DeclaredProgramIdMismatch. Error Number: 4100.
   The declared program id does not match the actual program id.
   ```

   So the program as committed can only run if deployed at an address whose
   keypair nobody holds. It is not "identifiable but deployable" as the audit
   framed it — **every instruction fails**. The first stake attempt against a
   freshly deployed .so burned 4,071 CU and reverted.
4. Set `RCLAW_PINNED_MINT` (program) and `RCLAW_MINT` (bot gate) to the real mint.

   **`RCLAW_PINNED_MINT` is a build-time input that changes the deployed
   bytecode, and it is recorded nowhere.** `lib.rs` reads it through
   `option_env!`, so the same commit produces two different deployable
   artifacts depending on a variable that appears in no config file, no CI log
   and no on-chain account. The hazard is not that the wrong one ships — it is
   that nobody can tell which one did. A verifier who rebuilds the tagged commit
   and gets a hash that differs from the deployed program cannot distinguish
   "the deployer set the pin" from "the deployer stripped the mint check".

   This is not hypothetical: the first SBF build of this program was deployed to
   a local validator on 2026-07-26 with the variable unset, and the smoke test
   then staked a **randomly generated** mint successfully. Nothing recorded that,
   and nothing would have.

   Measured the same day, so the mitigation rests on evidence rather than on
   how the build is assumed to behave:

   - The build **is reproducible** — a full clean rebuild at a different
     absolute path (`/tmp/reprocheck` vs the repo root) produced byte-identical
     bytecode, so no path, timestamp or random seed is embedded. This is what
     makes publishing a hash meaningful at all.
   - The pin **is load-bearing** — pinned and unpinned builds differ
     (`67296c4f…` vs `3467c133…`). And `option_env!` unset compiles to exactly
     the same bytecode as a literal `None`, confirming what "unset" means.

   So: **publish the artifact sha256 alongside the commit and the exact
   `RCLAW_PINNED_MINT` used**, and state that verification means rebuilding with
   that value. A deployer who misreports the pin cannot make the hashes agree.
   `scripts/build_provenance_gate.py` re-checks both properties per commit (CI,
   `staking` job) and prints a provenance record with `--record`.
5. **Transfer the program upgrade authority off the deploy key — before the vault
   accepts its first deposit, not after.** See §11; the window between deploy and
   transfer is the entire exposure.

   **Rehearsed end to end on devnet, 2026-07-26.** This is a one-way operation
   with no recovery, so it was executed against a real Squads v4 multisig before
   anyone has to do it for real. Sequence, addresses from that rehearsal:

   ```bash
   # 1. CHECK FIRST — this is the step that prevents the irreversible mistake.
   node programs/rclaw_staking/scripts/check_upgrade_authority.mjs <PROGRAM_ID> \
     --rpc <URL> --new-authority <VAULT_PDA> --squads-multisig <MULTISIG>

   # 2. Transfer. --keypair matters: the fee payer defaults to the CLI's
   #    configured signer, not to --upgrade-authority.
   solana program set-upgrade-authority <PROGRAM_ID> \
     --keypair <AUTHORITY_KEY> --upgrade-authority <AUTHORITY_KEY> \
     --new-upgrade-authority <VAULT_PDA> \
     --skip-new-upgrade-authority-signer-check --url <URL>

   # 3. The IDL authority moves too, or it stays on the old hot key.
   node programs/rclaw_staking/scripts/claim_idl.mjs <PROGRAM_ID> \
     --rpc <URL> --keypair <AUTHORITY_KEY> --set-authority <VAULT_PDA>
   ```

   **The mistake this prevents.** A Squads multisig has two addresses, and the
   wrong one is the one you would naturally copy:

   | | address (rehearsal) | can it sign? |
   |---|---|---|
   | multisig account | `5b3bw9qJx38MVWgz5qoxZGPsYQeMxwRjU5JVJPtg7BGZ` | **no** — a data account |
   | vault PDA, index 0 | `BStC8ReWMGowGHZGnRMkRMP8sxhGEXE6LPpzdfUdEj5e` | yes, via CPI |

   `set-upgrade-authority` accepts any 32 bytes and never asks whether anything
   can sign for them. Hand it the multisig account and the transfer *succeeds*,
   the CLI prints nothing alarming, and the upgrade authority is destroyed
   permanently — on a program that signs for every `["vault", mint]` PDA. The
   loss stays invisible until the first time an upgrade is needed. Hence step 1,
   which re-derives the vault PDA from the multisig you name (seeds
   `["multisig", multisigPda, "vault", u8(index)]`) and refuses on mismatch. It
   also refuses any off-curve target with no multisig to check it against, and
   warns — without blocking — when the target is an ordinary single key.

   **Then it was proven that the multisig can still upgrade**, which is the step
   that would otherwise discover a silent brick in production. A buffer was
   written, its authority handed to the vault, and a Squads
   `vaultTransactionCreate → proposalCreate → approve → execute` drove
   `BPFLoaderUpgradeable::Upgrade` with the vault as signer. Result: the
   program's deploy slot advanced `479095709 → 479104181`, the buffer was
   consumed, and the bytecode still hashes to the recorded
   `67296c4f…`. Both the upgrade authority and the IDL authority now sit behind
   the multisig on devnet.

   Two things that cost time and are not in any doc:

   - `solana program set-upgrade-authority` fails with **"Attempt to debit an
     account but found no record of a prior credit"** when the CLI's *default*
     keypair has no SOL on the target cluster. The error names the authority,
     which is misleading — the fee payer is the problem. Pass `--keypair`.
   - The Squads SDK's `multisigCreateV2` returns before the account is readable
     at `confirmed`; an immediate `Multisig.fromAccountAddress` throws "Unable
     to find Multisig account" for a multisig that was created perfectly well.
     Same commitment-race family as the deposit-window bug in §14.

   Still to do for real: the multisig above is a 1-of-1 created for this
   rehearsal and is **not** a governance setup. A production handoff needs a
   real threshold and real members, and `--skip-new-upgrade-authority-signer-check`
   means nothing verifies the destination on your behalf — step 1 is the only
   check there is.
6. Ratify every remaining §13 parameter. `LOCKUP_SECONDS` in
   `programs/rclaw_staking/src/lib.rs` is **settled: 30 days** (2026-07-26).
7. **Set a recipient for every allocation bucket, and make `treasury` a multisig.**
   `token/presale/metaplex-genesis.config.json` ships all six `allocations.buckets`
   with an empty `recipient` on purpose — `presale:allocate` **refuses** rather than
   defaulting to the signing key, because the default would put 750,000,000 tokens
   under one hot wallet. `presale:plan` flags each unset one. The `treasury` bucket
   must be the Squads vault PDA.

   **Vesting — resolved 2026-07-26.** This used to read: these are
   `addUnlockedBucketV2` buckets, `unlockAt` is a hard cliff for the *full*
   amount, and the linear tails §4 specifies are not implemented, so the
   published terms must describe cliffs or they are false. That gap is now
   closed. Team, advisors and community are created with
   `addStreamflowBucketV2` — real Streamflow streams with the §4 schedules
   (12mo cliff + 24mo linear, 6mo + 18mo, 36mo from TGE), verified on chain.
   Treasury, partnerships and reserve stay `addUnlockedBucketV2` deliberately:
   §4 describes them as governance-gated, which a cliff models correctly.

   Two properties carry it, and neither is visible from the phrase "12-month
   cliff":

   - **Conservation.** A stream releases `cliffAmount + amountPerPeriod x
     periods`. Integer division makes that *less* than the allocation unless
     something absorbs the remainder, and whatever is not released is stranded
     in the stream forever. The remainder goes to the cliff, so the end date
     stays exactly what §4 publishes, and `presale:allocate` reads each stream
     back off the chain and refuses to continue if the stored schedule does not
     release the whole allocation.
   - **The permissions.** A Streamflow stream can be cancelable, pausable,
     transferable or rate-editable. Any of those makes the schedule revocable —
     a promise, not a commitment — and the genesis authority would hold the
     revocation. All are set false, pinned in `REQUIRED_STREAM_FLAGS`, and
     re-read from the chain at creation. This is the last moment it can be
     checked: `finalizeV2` locks bucket configuration permanently.
8. **Claim the IDL account in the same session as the deploy — it is
   first-come-first-served.** `programs/rclaw_staking/Cargo.toml` has
   `default = []` with `no-idl` disabled, so `anchor build` emits an IDL, and
   Anchor 0.30.1 stores it in a program-owned PDA whose authority belongs to
   **whoever calls `anchor idl init` first**. Nothing in this repository ever
   initializes, versions or assigns it: `deploy.sh` contains no Solana commands,
   the `Makefile` and root `package.json` invoke no anchor, and CI has no anchor
   step. Left unclaimed after deploy, anyone may publish an IDL for our program
   id — explorers and wallets render account and instruction names from it, so a
   hostile IDL mislabels what a user is signing without touching the bytecode.
   Immediately after `anchor deploy`:

   ```bash
   anchor idl init  --filepath target/idl/rclaw_staking.json <PROGRAM_ID>
   anchor idl authority <PROGRAM_ID>          # verify it is ours
   anchor idl set-authority --new-authority <MULTISIG> --program-id <PROGRAM_ID>
   ```

   Then re-run `anchor idl upgrade` on every subsequent deploy, or the published
   IDL silently describes an older program. This was flagged as unexamined in the
   audit (`docs/TOKEN_SECURITY_AUDIT.md`, *Coverage & Limitations*, deferred area
   1).

   **The Anchor CLI is not required, and the item is no longer blocked on it
   (2026-07-26).** This sat undone because `anchor idl init` needs a CLI that
   cannot be installed in the authoring container, which left the exposure open
   for want of a tool. It turns out the CLI is not what claims the account. The
   IDL instructions are compiled into every Anchor program unless the `no-idl`
   feature is set — ours does not set it — and they dispatch on a fixed 8-byte
   tag rather than through generated code, so the instruction can be built by
   hand:

   ```bash
   node programs/rclaw_staking/scripts/claim_idl.mjs <PROGRAM_ID> \
     --rpc <URL> --keypair <PATH>                     # claim, idempotent
   node programs/rclaw_staking/scripts/claim_idl.mjs <PROGRAM_ID> \
     --rpc <URL> --keypair <PATH> --set-authority <MULTISIG>
   ```

   It exits non-zero if the account is held by anyone else, so it works as a
   deploy gate rather than only as a command. It claims the account; it does not
   publish an IDL, because generating one still needs `anchor build
   --features idl-build`. That split is the right one — **claiming is the part
   that is irreversible if lost, writing the content is not**, and the authority
   is retained so the real IDL can be written later.

   Every constant in it was read out of the vendored `anchor-lang` /
   `anchor-syn` source rather than recalled; the first value reached for from
   memory (`IDL_IX_TAG`) was wrong.

### Devnet presale lifecycle (2026-07-27)

The full sequence run end to end on **devnet**, not a local validator —
`create → liquidity → allocate → finalize → deposit → trigger → claim`. It had
only ever been rehearsed on a validator whose ledger dies with the container,
which meant the two on-chain verification claims could never actually be
checked by anyone else.

| | |
|---|---|
| Genesis account | `8ok2QT92P6cRpu8Mj18rfR7TjszinWYhF77YZ8LYJ8og` |
| Base mint | `5KdefGy7aUSXNEwie3D9ambWVaVuBDikSjanKRhre3iT` |
| Presale bucket | `GiAfvH5TtAvs66qzBrJY9vRk7bg72p7qb4nnLvnNJVxW` |
| Liquidity bucket | `CLZhnD1DjEnG9KuP2eTfkGMVV4Ynp59kWt9hMTgMZxu7` |
| Finalized | `True` |

`presale:verify` reports **16/16 against the chain** — base mint, finalization,
full-supply allocation, bucket parentage, allocation and hard cap, the
never-claim LP lock, and all six allocation buckets.

`programs:inventory` reports **five programs, all accounted for**: SPL System,
SPL Token, SPL Associated Token, Metaplex Token Metadata and Metaplex Genesis.
MPL Token Extras is absent, so the `createAtaIdempotent` fix holds on devnet and
not merely on a local validator.

This is still a **dry run**: the timeline is generated near-now so the
deposit and claim windows are minutes rather than days, and the artifact is
named `presale.dryrun.*` rather than `presale.devnet.json`. `scripts/receipts.py`
reports the published-presale claim as UNVERIFIED for exactly that reason —
nothing has been published, which is not the same as a check that failed.

### Devnet deployment record (2026-07-26)

The first real deployment of `rclaw_staking`. Devnet only — nothing here holds
value, and none of the Phase 0 Guardrails are cleared.

| | |
|---|---|
| Program id | `6yGc2n7vZyp7nvJJ8uXEdy56P1UT8Ma4En26bTtBrJhW` |
| ProgramData | `FhkLJcdkwNSTyAwwEBYFnpQwcZw93dGt5m97sspeFYez` |
| Upgrade authority | `5g7AScZQEnhqJkxXvmp3FikZ7zQu6Pwa4r936DYtdcMh` |
| IDL account | `9juexZjZymueKaaSuqEjoc1kqVGmst66eVLU5dEbudxo` (claimed same session) |
| Artifact sha256 | `67296c4fb4bd17b99d10adaaedb4f5e8cd9837ff9bf053ebb261343e0786ff22` |
| `RCLAW_PINNED_MINT` | **unset** — no canonical mint exists yet |

Two things about this record are load-bearing.

**The upgrade authority is a dedicated key, not the presale payer.** Deploying
from `token/.keys/mint-payer.json` would have been one command shorter and would
have collapsed the separation of duties F-04 asks for: the same key would hold
the presale/genesis authority and the power to replace the bytecode that signs
for every `["vault", mint]` PDA. It is still a single hot key and still has to
move to a Squads multisig — see item 5 — but the split is rehearsed rather than
deferred.

**The pin state is published with the hash, so the claim is checkable.** Rebuild
this commit with `RCLAW_PINNED_MINT` unset and the artifact hashes to the value
above; build it *pinned* and it hashes to `3467c133…` instead. Verified against
the live cluster, not asserted: `solana program dump` of the deployed program
matches the local artifact byte for byte. That is what makes "which build is
deployed" an answerable question — see step 4.

Two operational notes for whoever repeats this:

- `solana program deploy` **panics** in this environment on the QUIC/TPU path
  (`InvalidCertificate(UnknownIssuer)` — the agent proxy's CA). `--use-rpc`
  routes around it. Do not disable TLS verification.
- A failed deploy **strands its buffer**, holding the full 2.13 SOL. Two failed
  attempts left 4.27 SOL locked. `solana program show --buffers
  --buffer-authority <KEY>` finds them and `solana program close <BUFFER>
  --recipient <KEY>` refunds in full. Check for these before concluding the
  wallet is short.
9. **Do not run a bare `cargo update`.** The program is only buildable for
   deployment because `Cargo.lock` is pinned to **lockfile v3** and 19 transitive
   crates are held at older versions. The SBF toolchain pinned in `Anchor.toml`
   (Solana 1.18.26) bundles **cargo 1.75**, and the current ecosystem has moved
   to crates requiring `edition2024` and rustc 1.85+ — which that cargo cannot
   parse or compile. Before this was fixed, `cargo build-sbf` and `anchor build`
   **could not run at all**, so no deployable artifact existed.

   Every host check stays green if you undo it, which is exactly what makes it
   dangerous. CI now builds the SBF bytecode on every run to catch that.

   The long-term fix is a **Solana/platform-tools bump** to a release whose cargo
   supports modern crates — the same decision the RustSec backlog needs. Until
   then the pins are load-bearing.
10. **Inventory the on-chain programs every signed transaction invokes** — not
   just the npm packages it imports. Package-level SCA cannot see a CPI target,
   and this repository was routing all three money-moving instructions
   (`deposit`, `trigger`, `claim`) through a third-party upgradeable program
   nobody had chosen and nothing had recorded. Replay the lifecycle against a
   local validator carrying **only** the programs the sale is supposed to need;
   anything that then fails with `Attempt to load a program that does not exist`
   is an undeclared dependency.

   `npm run programs:inventory` does this from the run's own transaction logs
   and fails on anything not in `token/.program-inventory.json`.
### One command for every claim: `scripts/receipts.py`

Each check above lives somewhere different and is invoked differently, so in
practice nobody runs them all — and **a verification nobody runs is
indistinguishable from one that does not exist**.

```bash
python3 scripts/receipts.py                   # run every check available here
python3 scripts/receipts.py --offline         # skip the ones needing a live RPC
python3 scripts/receipts.py --markdown --out receipts.md    # publishable page
```

It adds no new checking. It is the index of the checks that already exist:
build reproducibility and the mint pin, the Rust and npm advisory ratchets, the
presale tooling's offline invariants, paywall coverage and the never-gated
safety commands, the Proof-of-PnL verifier including forged-identity rejection,
the on-chain program inventory, and the presale artifact against the chain.

**It reports three states, not two.** `PASS`, `FAIL`, and `UNVERIFIED` — the
last meaning the check did not run to completion, whether from a missing
toolchain, no network, or a missing artifact. `UNVERIFIED` is never folded into
either of the others and makes the exit non-zero (2) unless
`--allow-unverified` is passed explicitly. A page that quietly upgrades
*unknown* to *fine* is worth less than no page, and this is precisely the
document where a project is tempted to do that.

**No generated page is committed.** A snapshot goes stale the moment anything
changes, and a stale receipts page is the same record-versus-reality gap this
roadmap keeps closing elsewhere — it would assert as current a state nobody
re-checked. Generate it at publish time, from the commit you are publishing.
 Measured over a
   full lifecycle, five programs execute: SPL System, SPL Token, SPL Associated
   Token, Metaplex Token Metadata and Metaplex Genesis. The first four are
   immutable on mainnet-beta (native, non-upgradeable loader, or upgrade
   authority revoked).

   **Metaplex Genesis is not**, and that is the item for this checklist.
   `GNS1S5J5AspKXgpjz6SvKL66kPaKWAhaGRhCqPRxii2B` custodies the entire raise,
   the vesting schedules and the never-claim LP lock, and its mainnet upgrade
   authority — `bfQVv6niKVgEURYqQ1beJmiEQQN7MrvLRvk3mZGFubb`, an off-curve PDA,
   so program-controlled rather than a bare key — can replace that bytecode at
   any time with no change here. Every immutability claim §11 makes about the
   presale holds *within* Genesis, not against it. **Say so in the published sale
   terms**, and re-run the inventory before launch in case the authority or the
   deployed slot has moved.
11. **Clear the npm advisory backlog** — `cd token && npm run audit:gate` reports
   it; as of 2026-07-26 it is 1 critical and 15 high, baselined in
   `token/.audit-baseline.json`. The ratchet stops it *growing*; it does not make
   the existing advisories acceptable in code that signs transactions.
