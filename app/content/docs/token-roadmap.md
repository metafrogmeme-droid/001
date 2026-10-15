# $RCLAW Token Roadmap

**The settlement and access layer for RUNECLAW's on-chain agent economy.**

> **Not financial advice, and not a solicitation to buy any token.** `$RCLAW` is minted on Solana
> mainnet, and its sale on Smithii's launchpad was created on 2026-10-13; it opens on 15 Oct 2026
> at 14:00 UTC and closes on 29 Oct 2026 (the Presale table below has the exact window). The sale terms, the three time locks and the treasury multisig are on chain; the
> utility, the tiers and the later phases below are still proposals.

This is the condensed overview. The full design lives in
[`docs/TOKEN_ROADMAP.md`](https://github.com/metafrogmeme-droid/001/blob/main/docs/TOKEN_ROADMAP.md).

`$RCLAW` supersedes the earlier `$CLAW` placeholder in the product roadmap.

---

## Why a token

RUNECLAW is moving *"from an autonomous trader to an on-chain agent economy."* `$RCLAW` is
designed as the coordination, access, and settlement primitive for that economy — **not a
fundraise for its own sake**. It is meant to do three jobs, none of which is live yet:

- **Access** — stake to unlock premium scan tiers, larger compute allowances, priority agents.
- **Settlement** — meter agent-to-agent calls to the Shield risk engine; split marketplace
  and copy-trading revenue.
- **Governance** — vote on risk params, new venues, and promoted strategies.

## Token at a glance

| Field | On chain |
|---|---|
| Ticker | **`$RCLAW`** |
| Chain | **Solana** (classic SPL Token, minted 2026-09-30) |
| Supply | **1,000,000,000, fixed** (decimals 9) |
| Authorities | **Mint + freeze revoked**; treasury in a Squads v4 multisig (since 2026-10-14) |
| Transfer tax | None (DEX-friendly) |

## Utility (proposed)

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
[`docs/TIER_MODEL.md`](https://github.com/metafrogmeme-droid/001/blob/main/docs/TIER_MODEL.md).

## Allocation (1B)

| Bucket | % | Tokens |
|---|---:|---:|
| Public presale | 15% | 150M |
| DEX liquidity | 2.0001% | 20.001M |
| Community & ecosystem | 25% | 250M |
| Team & contributors | 15% | 150M |
| Treasury / DAO | 20% | 200M |
| Partnerships & MM | 8% | 80M |
| Advisors | 2% | 20M |
| Reserve / insurance + post-TGE liquidity | 12.9999% | 129.999M |
| **Total** | **100%** | **1,000M** |

On chain today, the presale allocation is in the sale's vault; community, team and advisor
tokens are in the three time locks below; the DEX-liquidity bucket and a share of the reserve
went to the wallet that will create the pool; and treasury, partnerships and the rest of the
reserve are in the multisig.

The sale program locks and vests **nothing**, so the team made three time locks with Smithii's
Token Vesting tool, each with its own claim wallet, and read them back from the chain on
2026-10-13. Their addresses and certificates are on
[www.humanoid-traders.com/token](https://www.humanoid-traders.com/token):

- **Community & ecosystem:** 5 unlocks (5%, 20%, 25%, 25%, 25%): at TGE, then every 9 months (the last at month 36)
- **Team & contributors:** 12-month cliff, then 4 unlocks of 25% every 6 months (the last at month 36)
- **Advisors:** 6-month cliff, then 3 unlocks (33%, 33%, 34%) every 6 months (the last at month 24)

That tool has no linear mode, so each schedule is a staircase: a step's share can be claimed in
full once its date has passed. A lock cannot be cancelled or edited, but the tool's program is
upgradeable by an off-curve authority (the same one as the sale program; who can sign for it is
not visible) and was last deployed before its audit's fixes
([Halborn, April 2025](https://www.halborn.com/audits/smithii/vesting-1ae001); the two findings
those fixes closed concern multi-recipient Merkle vestings and zero-amount locks, which these locks
do not use), so these locks are not called audited. Treasury, partners and the rest of the reserve sit in a Squads v4 multisig (since
2026-10-14): moving them needs its members' approvals and then its time lock's wait, which is
custody with a delay, not a lock. It takes 2 of its 3 members to approve, and its time lock is
48 hours; the vault's address, the members and the delay are on
[www.humanoid-traders.com/token](https://www.humanoid-traders.com/token). **Presale buyers are not
vested:** they can claim everything the moment the sale ends.

The pool is created by the team after the raise, so it is sized then. Opening at the sale price
it needs about **24.0M RCLAW at the soft cap and 120.0M at the hard cap**. Whether the table
above returns to a 10% / 5% liquidity / reserve split is an open decision (roadmap §13).

## Presale (created on mainnet 2026-10-13)

The sale runs on **Smithii's launchpad** (its Mantis program on Solana mainnet). That program
does less than the Metaplex Genesis design kept as the alternative, so the terms below are what
it actually does — including what it does **not** do.

| Term | What happens |
|---|---|
| Price | Fixed, the same for everyone: **30,000.3 RCLAW per SOL** as the program stores it (33,333 lamports per token) |
| Soft cap / hard cap | Soft cap **1,000 SOL** is a target, not a floor — the program never reads it. The hard cap **5,000 SOL** ends the sale |
| Per wallet | Min **0.25 SOL**, max **25 SOL** per wallet (a wallet, not a person: more wallets get around it) |
| Public sale | 15 Oct → 29 Oct 2026 (336 hours) or until the hard cap. There is no whitelist round: the program has no wallet list |
| Buyer vesting | **None.** Every buyer can claim 100% of what they bought when the sale ends |
| Refund | **None.** Each purchase is paid straight to the team's wallet; nothing is held back to return |
| Proceeds | Paid directly to the team's launch wallet at each purchase, less Smithii's 2.5%. The plan is a hardware-backed wallet, with proceeds moved to the multisig after the sale |
| Liquidity | **The team creates the pool** on Raydium after the sale — 80% of the gross raise, opened at the sale price — and burns the LP tokens, publishing both transactions. The program does neither |
| Unsold tokens | Come back to the team's wallet in one `withdraw` call; the team intends to move them to the reserve allocation and publish that transaction |
| Program | Smithii's, **upgradeable**: its upgrade authority `CyTc…7KTC` is an off-curve address (program-controlled; who can sign for it is not visible). CoinFabrik's 2024 audit covers Smithii's program, not RUNECLAW |

If the raise ends below the soft cap the sale still ends and the team still holds the SOL. What
the team will do then is an open decision (roadmap §13); the contract will not make it.

## Launch venue

| Venue | Role in this sale |
|---|---|
| **Smithii Launchpad** | **Chosen** (2026-10-08) — no-code, 0.1 SOL + 2.5% of sales, a program CoinFabrik audited in 2024 (last deployed before the audit's final report); but it has **no vesting, no wallet whitelist, no refund and no liquidity** |
| **Metaplex Genesis** | **Alternative** — on-chain vesting, whitelist and LP lock; integrated in draft (devnet only) and not used for this sale |
| **Pump.fun / LetsBonk** | Poor — no caps/whitelist/vesting → **not for the raise** |

## Roadmap phases

0. **Foundations & Guardrails** — legal review, audit, disclosures, tokenomics finalized.
1. **Pre-launch** — mint, revoke authorities, multisig, locks, publish disclosures.
2. **Presale & TGE** — public sale → claim → the team creates the Raydium pool and burns the LP.
3. **Utility activation** — staking tiers, governance voting, buyback-and-burn.
4. **Ecosystem** — marketplace/copy-trading splits, vaults, MCP/x402 settlement.
5. **Sustainability** — insurance fund, proof-of-reserves, CEX listings, DAO handoff.

## Build status — and what is actually proven

Draft, devnet-only reference implementations exist for most of the mechanics above:
the SPL Token-2022 mint tooling, a real **Metaplex Genesis** presale integration (the
alternative venue: whitelist, liquidity with a permanent LP lock, claim; its withdraw and refund
paths do not run against the V2 presale it creates — roadmap §14), an end-to-end lifecycle
harness, an Anchor **staking program**, a Wormhole **NTT bridge** script, and the wallet/tier-gate
plumbing. None of that draft code is the launch.

The launch itself is on mainnet: the classic SPL Token mint with its authorities revoked
(2026-09-30), the three time locks (read back 2026-10-13), the Squads multisig (2026-10-14) and
the sale on **Smithii**. The sale's numbers are derived offline from one config
(`npm run presale:smithii-plan`); it was created on 2026-10-13 and read back off mainnet against
that config (`npm run presale:smithii-verify`), and every row passed: price, caps, per-wallet
limits, the window, no whitelist phase, and the tokens in the sale's vault.

**An adversarial review of the draft code found 10 real defects, all fixed.** The most serious was
**critical**: the staking program bound stakes to no mint, so an attacker could stake a
worthless token and redeem the same amount from the real `$RCLAW` vault. It is fixed, and the
fix is *executed* rather than asserted — the attack is performed in-process against the real
program and rejected, with the honest vault balance asserted unchanged.

What that still does **not** mean:

| Proven | Not proven |
|---|---|
| Staking program executes in-process; the attack is rejected | **No independent audit**; deployed on devnet only (2026-07-26), never on mainnet |
| Presale params derive correctly offline; the Smithii sale was created and read back (2026-10-13) | Nothing after Create is recorded here yet (buys, claims, the withdraw); the pool is created after the sale |
| Smithii's programs: CoinFabrik audited the sale program in 2024 and Halborn the vesting tool in 2025 (for Smithii); what the sale program does was read from its SDK and live mainnet transactions | The deployed code is not proven to be the audited code: both programs were last deployed before their audits' final reports |
| Community, team and advisor locks exist and were read back (2026-10-13); treasury, partnerships and the rest of the reserve are in a 2-of-3 Squads vault with a 48 h time lock (2026-10-14) | The vesting tool's program is upgradeable by an authority whose signers are not visible |
| Bridge + TS specs typecheck | Never deployed or executed |

The remaining gaps are open findings, not limits of the environment (roadmap §14). **Do not
deploy the staking program anywhere holding value until it is audited.**

## Cross-chain note

RUNECLAW's agent *identity* is designed for **Base**: it publishes an ERC-8004-shaped identity
card, whose on-chain anchor reads UNVERIFIED until a registration transaction confirms, and x402
settlement is still a design ([`docs/INTEROP.md`](https://github.com/metafrogmeme-droid/001/blob/main/docs/INTEROP.md)).
`$RCLAW` stays **Solana-native**, with an **optional Wormhole bridge to Base later** — a **draft
NTT bridge script** exists for that (hub-and-spoke, locking on Solana, because the mint authority
is revoked). Wormhole (`W`) and Drift are already
in RUNECLAW's Solana scan universe. A lightweight Solana wallet connector
(Phantom/Backpack, connect-and-sign) now ships alongside the EVM/ethers stack; a full
wallet-adapter with Solflare/hardware support remains future work.

---

See the [full roadmap](https://github.com/metafrogmeme-droid/001/blob/main/docs/TOKEN_ROADMAP.md)
for tokenomics detail, legal/compliance, the security checklist, KPIs, and the full list of
open decisions — and
[§14 Verification status](https://github.com/metafrogmeme-droid/001/blob/main/docs/TOKEN_ROADMAP.md#14-verification-status--what-is-actually-proven)
for precisely what has been proven versus merely built.
