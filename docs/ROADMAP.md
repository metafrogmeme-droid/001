# RUNECLAW — Product &amp; Protocol Roadmap

**From an autonomous trader to an on-chain agent economy.**

Today RUNECLAW is an autonomous, risk-gated AI that trades perpetuals across four
venues and that you can chat with and trade alongside from the web — wrapped in a
live safety layer (Guardian), a verifiable record of every call it makes (Provable
Calls), a paper Arena to practise in, and an agent-facing API. This roadmap extends
that engine along five fronts — sharper agents, more venues, native web3 &amp;
staking, social growth, and the trust layer that has to come first.

> Directional roadmap, not a commitment or an offer. Order and scope will shift
> with evidence and regulation. Nothing here is financial advice or a
> solicitation to buy any token. Trading derivatives involves substantial risk
> of loss.

## What already ships today

- **Live engine** — autonomous scan → LLM analysis → 23-check risk gate → execute.
- **4 venues** — Bitget, Bybit, BingX, Hyperliquid (on-chain).
- **Per-user live** — bring-your-own-keys, encrypted at rest, operator-gated.
- **Web app** — chat, place-trade-from-chat, live portfolio, onboarding, invites.
- **Guardian safety suite** — six live surfaces: [Flight Recorder](https://pmvc58g2.mule.page/flight),
  [Stress Lab](https://pmvc58g2.mule.page/stress), [Risk Sentinel](https://pmvc58g2.mule.page/sentinel),
  [Transaction Firewall](https://pmvc58g2.mule.page/firewall),
  [Escape Agent](https://pmvc58g2.mule.page/escape), [Intent Compiler](https://pmvc58g2.mule.page/intent).
- **Provable Calls** — every engine call and Arena trade hashed at decision time, daily Merkle
  roots, per-call receipts anyone can re-derive in their own browser (`/provable`, `/roots`, `/call`).
- **Paper Trading Arena** — virtual accounts, live prices and liquidation mechanics, competition
  seasons, weekly quests, and a percent-only public board.
- **Agent-facing surface** — MCP server, public REST endpoints, ERC-8257 tool manifest and
  ERC-8004 identity (`/developers`).
- **Twelve languages** — the web UI ships fully translated in en, es, zh, pt, fr, de, nl, ja, ko,
  ru, tr, ar, with a test that fails the build if a swept page regrows untranslated copy.

> **Status is checked against the running product, not against intent.** Rows are moved to
> 🟢 only when the described capability is reachable by a user today; where something shipped
> *partially*, the row says which half — a green marker on a half-built feature is the fastest
> way for a roadmap to stop being worth reading.

## Legend

| Status | Meaning |
| --- | --- |
| 🟢 Shipped / extending | Live today, or a direct extension of a live system |
| 🟡 Building / near-term | Actively in progress or next up |
| 🔵 Planned | Scoped, scheduled after near-term |
| ◆ Vision | Gated behind the [Guardrails](#guardrails) — no user launch until those are met |

Horizons: **Now** · **Next** (~0–3 mo) · **Later** (~3–9 mo) · **Vision** (9+ mo).

---

## 1. Agent intelligence

Make the reasoning deeper, more personal, and self-improving — building on the
existing analyzer, learning loop, and shadow book.

| Capability | Horizon | Status | Builds on |
| --- | --- | --- | --- |
| **Real-time signal fusion** — news, funding/liquidation feeds, and social alpha (X, Telegram) folded into the vote ensemble | Next | 🟡 | the analyzer's voter model |
| **Per-user agent memory** — remembers risk appetite, watchlist, past decisions; sizes to your calibrated confidence | Next | 🟡 | chat + confidence calibration |
| **Multi-agent ensemble** — specialist sub-agents (scalp/swing/macro/market-maker) under a portfolio coordinator that allocates risk by live expectancy | Later | 🔵 | the strategy engine |
| **Talk-to-build strategies** — describe a strategy in plain language; it compiles to gated, backtested config (never unchecked live orders) | Later | 🔵 | the *policy* half shipped: the [Intent Compiler](https://pmvc58g2.mule.page/intent) turns plain words into a deterministic, revocable Authority Envelope. Compiling a full **strategy** is still ahead |
| **On-chain intelligence** — smart-money/whale tracking, DEX flow, liquidity-pool reads as first-class signals | Later | 🔵 | on-chain execution path |
| **Online self-improvement** — reinforcement from live outcomes, every change validated against the shadow book before it touches capital | Vision | ◆ | shadow-book replay |

## 2. Execution &amp; venues

The venue-abstraction layer already makes adding markets a data change, not a
rewrite. Push it toward every liquid perp market, on-chain and off.

| Capability | Horizon | Status | Builds on |
| --- | --- | --- | --- |
| **More CEX venues + smart routing** — best price / lowest fees across connected venues, maker-preferred | Next | 🟢 | the venue adapter + router |
| **On-chain perp DEXs** — native adapters for dYdX v4, GMX, Vertex, Drift (Solana), beside Hyperliquid | Later | 🔵 | new venue adapters |
| **Cross-venue funding arbitrage** — capture funding/basis spreads delta-neutral (backbone of the yield vaults) | Later | 🔵 | powers Web3 · vaults |
| **Intent-based execution** — solvers compete to fill across chains/venues, gas-aware and MEV-protected | Vision | ◆ | account abstraction |

## 3. Web3, staking &amp; vaults

Non-custodial by default. Users keep their keys; the protocol adds staking,
agent-managed vaults, and shared upside — **every step gated by the
[Guardrails](#guardrails)**.

| Capability | Horizon | Status | Notes |
| --- | --- | --- | --- |
| **Self-custody sign-in** — connect a wallet (EIP-6963 picker / MetaMask) to log in without handing over keys; read-only wallet link mirrors balances and DeFi positions | Now | 🟢 | live: sign-in, `/wallet-link`. Trading *from* the wallet on on-chain venues is still ahead |
| **Verifiable track record** — sealed statements, daily Merkle roots, per-call receipts; trust performance without trusting the operator | Now | 🟢 | live: `/track`, `/proof`, `/provable`, `/roots`, `/call` |
| **Idle-margin yield** — optionally park unused stablecoin margin in audited lending (e.g. Aave); opt-in, withdraw anytime | Next | 🔵 | **radar only so far**: the dashboard surfaces best available rates read-only and moves nothing. Parking funds is the unshipped part, and stays audit-gated |
| **$RCLAW staking** — stake for fee discounts, larger compute allowances, priority agents, and a share of protocol revenue ([token roadmap](./TOKEN_ROADMAP.md) · [tier model](./TIER_MODEL.md)) | Later | ◆ | gated — see Guardrails. **Draft devnet tooling exists** (mint, Metaplex Genesis presale, Anchor staking program, NTT bridge) and an adversarial review already found and fixed a **critical vault-drain** in it — see [§14 Verification status](./TOKEN_ROADMAP.md#14-verification-status--what-is-actually-proven). **Unaudited; do not deploy.** Still Phase 0 |
| **Agent vaults (ERC-4626)** — deposit stablecoins into a vault an agent trades (e.g. delta-neutral funding-farming); hold standard vault shares, redeem on demand | Later | ◆ | gated · non-custodial · audited |
| **DAO governance** — token-holders vote on risk params, new venues, promoted strategies; on-chain performance-fee splits | Vision | ◆ | gated · post-token |

## 4. Product, social &amp; growth

Turn single users into a network — building directly on the referral system that
just shipped.

| Capability | Horizon | Status | Builds on |
| --- | --- | --- | --- |
| **Invite friends** — unique links, signup attribution, live "friends joined" count | Now | 🟢 | live: the code and the joined count both come from `/api/auth/referrals` |
| **Referral rewards &amp; tiers** — turn invites into perks (fee credits, higher limits, post-token rewards) with milestone tiers | Next | 🟡 | the invite system. **The tiers are live** — five milestones on `/api/auth/referrals`, shown on the Account panel. The **rewards are not**, and the card now says which is which: each perk declares whether it is in force, and a planned one prints what it waits on. Two of the five ride on the token and say so. The one perk that is real is the squad — [Daily Duel](../app/lib/duel_squads.js) squads are built from this same referral graph |
| **Leaderboards &amp; shareable cards** — opt-in performance leaderboards and one-tap shareable trade cards for Telegram/X | Now | 🟢 | live: `/leaderboard` (percent + ratios, anonymous handles), `/trader` cards, Arena board + seasons |
| **Copy-trading marketplace** — follow top agents/users; creators earn a share of follower fees | Later | 🔵 | verifiable track record |
| **Agent marketplace — catalogue** — browse real strategy presets, each with a verified reproducible backtest; follow one and copy its picks on paper | Now | 🟢 | live: `/agents` |
| **Agent marketplace — economics** — publish a strategy as a subscribable agent; the protocol handles risk gating, billing, revenue split | Vision | ◆ | the catalogue is live; **billing and revenue split are not**, and stay gated |

## 5. Trust, risk &amp; compliance

The layer that gates everything above. Real money is already live — so safety
leads, it doesn't follow.

| Capability | Horizon | Status | Notes |
| --- | --- | --- | --- |
| **Hard risk engine** — 23-check gate, circuit breakers, per-user loss breakers, margin caps, kill switch, encrypted secrets vault | Now | 🟢 | live today |
| **Guardian suite** — Flight Recorder (sealed decision ledger), Stress Lab (digital-twin liquidation modelling), Risk Sentinel (market-wide crowding radar), Transaction Firewall (pre-sign prompt-injection scan, local-only), Escape Agent (dependency-aware unwind planner), Intent Compiler (plain words → revocable Authority Envelope) | Now | 🟢 | live: `/flight`, `/stress`, `/sentinel`, `/firewall`, `/escape`, `/intent`. All six **warn, explain, simulate and prove — none of them move funds** |
| **Provable Calls** — decisions hashed before the market moves; outcomes attach to the sealed record and cannot change it. Daily Merkle roots make a whole day independently timestampable | Now | 🟢 | live today; full verification contract at `/provable` |
| **Independent security audit** — third-party review of money endpoints, credential store, gateway | Next | 🟡 | precedes wider live rollout |
| **Contract audits &amp; proof-of-reserves** — every vault/staking contract audited; reserves provable on-chain before any deposit | Later | 🔵 | blocks vault launch |
| **Compliance tiers &amp; disclosures** — jurisdiction-aware access, KYC tiers for higher limits/vaults, clear risk disclosures | Later | 🔵 | gates token &amp; vaults |
| **Insurance fund** — protocol-owned backstop for vault tail-risk, funded from performance fees | Vision | ◆ | post-revenue |

---

## Guardrails

**Nothing marked "Vision" (◆) ships to users until this is true.**

A token, managed vaults, revenue share, and yield products carry real securities,
regulatory, and smart-contract risk. They are exciting — and they are exactly
where projects hurt their users. So each is gated, non-negotiably, behind:

- **Legal review** per jurisdiction before any token, vault, or revenue-share goes live.
- **Independent audits** — security review of the app, and separate smart-contract audits for every on-chain contract.
- **Non-custodial by default** — users keep their keys and can withdraw at any time; the protocol never takes custody it doesn't need.
- **Plain risk disclosures** — leverage, drawdown, and smart-contract risk stated up front, never buried.
- **Staged rollout with caps** — small limits first, widened only on evidence, with a global kill switch throughout.
- **Proof over promises** — verifiable track record and on-chain reserves before we ask anyone to deposit.

## How we sequence the work

- **Extend, don't rebuild.** Every near-term item leans on a proven system — the venue adapter, risk engine, learning loop, or web app — so we ship fast without new blast radius.
- **Gated &amp; opt-in.** New capability lands behind a flag, off by default. Live money and web3 features are choices the user makes, not defaults they inherit.
- **Safety leads.** Audits, disclosures, and caps come before the feature they protect — not after an incident.
- **Proof, not hype.** Verifiable performance and on-chain transparency are prerequisites for anything that touches other people's capital.
