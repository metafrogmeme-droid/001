# `$RCLAW` presale — setup runbook

Operational steps for the `$RCLAW` presale. **Venue of record: Smithii (Path B), decided
2026-10-08.** The documented alternative is **Metaplex Genesis (Path A)**, which is devnet-only
and not used for this sale. Full rationale and the venue comparison are in
[`docs/TOKEN_ROADMAP.md` §6](../../docs/TOKEN_ROADMAP.md#6-launch-venue-comparison--recommendation).

> ⚠️ **Path A is a devnet dry-run only. Path B is a mainnet sale.** Do not press Create until
> Phase 0 Guardrails are cleared: legal review per jurisdiction, the published disclosures
> (roadmap §8, §10–§11). Create is a mainnet transaction that moves the presale tokens into a
> vault owned by an upgradeable program, and nothing in the program can take a payment back.
> Config values are a proposed baseline to ratify (§13).

## Config files

| File | Venue | Notes |
|---|---|---|
| `smithii.config.json` | Smithii (venue of record) | No-code form; 0.1 SOL to create + 2.5% of each purchase, paid by the creator. Read by `smithii_plan.mjs`, `smithii_verify.mjs` and the tests |
| `metaplex-genesis.config.json` | Metaplex Genesis (alternative) | Fixed-price presale + TGE, on-chain; consumed by `genesis_presale.mjs` |
| `locks.plan.json` | Smithii Token Vesting | The three time locks (community, team, advisors) as staircases of at most five unlocks; read by `locks_lib.mjs`, `locks_plan.mjs` and the tests |

The two share the **economics** — 150M presale allocation, **1,000 SOL soft / 5,000 SOL hard
cap**, 0.25–25 SOL per wallet, a 336 h public phase (15 → 29 Oct 2026). They differ in what
the **program enforces**, and the differences are declared, not accidental: Genesis vests buyers
(33% at TGE then linear over 2 months), keeps a wallet whitelist, and creates the pool with
66.67% of the raise and a never-claim LP lock; the Smithii program does none of those (no
vesting, no wallet list; the operator creates the pool with **80% of the raise** and burns the LP).
`venue_parity.test.mjs` checks the shared terms agree, every difference is declared with a
reason, and the published docs state the chosen venue's terms.

## Prerequisites (Path A)

1. Mint exists on devnet — run `token/` tooling first (`npm run create`), then copy the mint
   address from `token/.artifacts/token.devnet.json` into the `token.mint` field of the chosen
   presale config. (Path B needs no such step: the mint is on mainnet and recorded in
   `token/config/rclaw.mainnet.json`.)
2. Whitelist collected (wallet allowlist) for the OG round.
3. Treasury **Squads multisig** created; presale proceeds and unsold tokens flow to it.
4. Legal sign-off + audit report links ready to publish (Phase 0 exit criteria).

## Path A — Metaplex Genesis (alternative; not this sale's venue) — real SDK integration

This path is wired against the real **`@metaplex-foundation/genesis`** SDK
(`token/presale/genesis_presale.mjs`, built on Umi). Parameters are derived from
`metaplex-genesis.config.json` — edit the config, not the script.

```bash
cd token
npm ci                           # pulls @metaplex-foundation/genesis + umi, lockfile-exact
cp .env.example .env             # devnet; never a mainnet key
npm run keygen                   # devnet operator wallet + airdrop

npm run presale:plan             # OFFLINE preview: every derived param + whitelist + liquidity
npm run presale:whitelist        # build the Merkle allowlist from config.whitelist
npm run presale:create           # initializeV2 (genesis account) + addPresaleBucketV2 (+ allowlist)
npm run presale:liquidity        # addRaydiumCpmmBucketV2 with a permanent LP lock
npm run presale:allocate         # REQUIRED: buckets for the other 75% of supply. finalizeV2
                                 # refuses while ANY supply is unallocated. Refuses to run
                                 # unless every allocations.buckets[].recipient is set.
npm run presale:finalize         # REQUIRED before any deposit (deposits fail 0x2c without it).
                                 # PERMANENTLY LOCKS bucket configuration — the LP token
                                 # allocation can never be changed after this point.
npm run presale:deposit -- --amount 1   # depositPresaleV2 (auto-presents whitelist proof)
npm run presale:trigger          # triggerBehaviorsV2 — routes the 66.67% quote share to the
                                 # liquidity bucket. Permissionless: ANYONE may run it once
                                 # the deposit window closes. Required before the pool is real.
                                 # REFUSES if the realised raise would open the pool below the
                                 # presale price — see the decision point below.
npm run presale:claim            # claimPresaleV2 once the claim window opens (post-TGE)

npm run presale:verify           # READ BACK every claim the artifact makes and check it
                                 # against the chain. The artifact is a JSON file written
                                 # by the same process that sent the transactions, so it
                                 # records what that process INTENDED. Run this before
                                 # publishing any of those addresses. Exits non-zero on a
                                 # mismatch, so it can gate a publish step.
# recovery: BOTH OF THESE ARE DEAD FOR A V2 PRESALE — they refuse up front.
# withdrawPresaleV1/withdrawUnsoldPresaleV1 are V1-only and reject a V2 genesis
# account (0x2f); the SDK has no V2 equivalent. Verified on devnet 2026-07-26.
# Unsold tokens ARE recovered — by the BaseTokenRollover behavior presale:trigger
# executes, not by these commands. The DEPOSITOR refund is what does not exist
# for a presale bucket (a LaunchPool bucket has one). See the audit doc.
npm run presale:withdraw          # refuses: no V2 refund instruction exists
npm run presale:withdraw-unsold   # refuses: no V2 unsold-recovery instruction exists
```

What the script does, mapped to the SDK:

1. **`presale:plan`** — pure offline derivation via `createTimeAbsoluteCondition` /
   `createClaimSchedule` / `createNeverClaimSchedule`; prints fixed price (`allocation / hardCap`),
   lamport caps, per-wallet min/max, deposit + claim windows, vesting, the **whitelist root**,
   and the **liquidity/LP-lock** summary. No RPC, no keypair.
2. **`presale:whitelist`** — `prepareAllowlist(config.whitelist)` builds the Merkle tree and
   writes the root + per-address proofs to `token/.artifacts/allowlist.devnet.json`.
3. **`presale:create`** — `initializeV2` creates the genesis account (PDA via
   `findGenesisAccountV2Pda`), then `addPresaleBucketV2` configures the fixed-price presale:
   `baseTokenAllocation`, `allocationQuoteTokenCap` (= hard cap), the four time conditions,
   `minimumDepositAmount` / `depositLimit` (per-wallet floor/ceiling), a `claimSchedule`
   (33% at TGE via `cliffAmountBps`, linear tail), and — if a whitelist artifact exists — the
   `allowlist` (Merkle root, ends at `publicStart`). Writes `token/.artifacts/presale.devnet.json`.
4. **`presale:liquidity`** — `addRaydiumCpmmBucketV2` with `baseTokenAllocation` = the 20,001,000
   liquidity allocation (soft-cap sized — see §4/§7 of the roadmap), `lpLockSchedule` = `createNeverClaimSchedule()` (**LP locked forever**),
   and a `startCondition` at the deposit-window close.
5. **`presale:deposit` / `presale:claim`** — `depositPresaleV2` / `claimPresaleV2` against the
   bucket. During the whitelist window the depositor's Merkle `proof` is looked up and presented
   automatically.
6. **`presale:withdraw` / `presale:withdraw-unsold`** — `withdrawPresaleV1` (depositor
   cancel/refund; deposit PDA via `findPresaleDepositV2Pda`) / `withdrawUnsoldPresaleV1` (operator
   recovers unsold tokens; ATAs via `findAssociatedTokenPda`).

**Funding mode** (`config.fundingMode.mode`): `mint` lets Genesis create + mint the supply
itself (self-contained demo); `transfer` reuses the mint from the `token/` tooling (set
`config.token.mint` from `token/.artifacts/token.devnet.json`). *Confirm the on-chain
`fundingMode` numeric against the Genesis program before mainnet.*

**Confirm on devnet / before launch:**
- **Soft-cap / refund semantics** — ~~The `withdraw`/`withdraw-unsold` commands cover
  depositor-cancel and operator-recover-unsold~~ **CORRECTED — they do not, and this bullet
  said to "validate on devnet" something that was validated and came back negative.**
  Genesis fixed-price presale is "buy until cap." PROVEN on devnet 2026-07-26:
  `withdrawPresaleV1` and `withdrawUnsoldPresaleV1` are **V1-only** and reject a V2 genesis
  account with `The Genesis Account is invalid` (0x2f); the SDK ships no V2 equivalent. So
  there is **no depositor cancel and no operator recovery of unsold tokens** — a deposit that
  lands cannot be returned. Unsold supply is handled instead by the on-chain
  `unsoldRollover` end behavior (see `metaplex-genesis.config.json`), without which it is
  stranded permanently. Published terms must not promise a refund of any kind. This holds at
  the **fallback venue too** until Smithii's refund path is executed on devnet and the
  transaction published — `venue_parity.test.mjs` fails if either config sets
  `refundIfSoftCapMissed`.
- **Liquidity finalize** — `presale:liquidity` adds the Raydium bucket + permanent LP lock;
  confirm the pool-creation/finalize flow end-to-end on devnet before mainnet.
- **Publish** — genesis account, bucket, mint, whitelist root, LP-lock proof, audit report.

## Path B — Smithii (venue of record)

> **The Smithii program does less than Path A, by design, and this section used to say
> otherwise.** It has five instructions — initialize, edit, buy, claim, withdraw — so it cannot
> vest buyers, keep a wallet whitelist, refund, or create or lock liquidity, and it never reads
> the soft cap. The two checks this section used to call BLOCKING are **answered** (2026-10-08,
> from CoinFabrik's audit, Smithii's SDK and live mainnet transactions; recorded in
> `smithii.config.json`): a **permanent LP lock is not expressible** — the program never
> touches liquidity, so the operator creates the pool and burns the LP — and a **refund is not
> possible**, because each purchase pays the creator directly. The fee placeholder is resolved
> too: **0.1 SOL to create** (0.2 with a whitelist phase, per the audit) and **2.5% of each
> purchase, taken from the creator's side** (observed: a 0.5 SOL buy moved 0.4875 SOL to the
> creator and 0.0125 SOL to Smithii; the buyer was credited the full 0.5). Whatever the program
> does not enforce is the operator's action — checkable afterwards, enforced by nothing — and
> must be published as exactly that.

### The dated plan (15 → 29 Oct 2026)

The operator's dates (2026-10-10): the public phase opens on **15 Oct 2026** and closes on **29 Oct
2026**, with no whitelist round. The clock time, 14:00 UTC, is a placeholder: set the real one in
`smithii.config.json` → `schedule` and everything derived follows (the plan, the read-back rows, the
locks' dates, the cards). Working back from the opening:

| When | What |
|---|---|
| now → Sun 11 Oct | Confirm the clock time. Clear the gates below. Make four wallets: the sale's signing wallet and one each for the community, team and advisor locks; hardware-backed, about 0.5 SOL each (the signing wallet also needs the 0.1 SOL creation fee) |
| Mon 12 Oct | Move the tokens, a small test first: the amounts under "who holds what" in `npm run presale:locks-plan`. Rehearse the vesting tool once on a throwaway wallet |
| Mon 12 → Tue 13 Oct | Create the three locks; publish each certificate link and wallet address |
| Tue 13 Oct, by 14:00 UTC | Create the sale (the form's four steps), then `presale:smithii-verify`; fix anything with Smithii's `edit`. 48 hours before the opening is the latest that leaves a day to fix. Once verify passes, publish the sale link: set `token/config/rclaw.mainnet.json` → `presale.sale_url`, run `node app/scripts/sync_content.js`, and change the test that pins it to `null` (`the_record_states_the_sale.test.mjs`) in the same commit. The site and the bot print it only if it is an `https` link on smithii.io (`tests/fixtures/sale_url_cases.json`); anything else reads "unreadable" |
| Wed 14 Oct | Buffer. The announcement is already in place: the site's /token page, its home strip and the bot's /rclaw card read `presale` from the same record, which says "announced" with the sale's terms since 2026-10-10. Those terms are a copy of this config: `the_record_states_the_sale.test.mjs` fails when they differ and prints the sentences the record should carry |
| Thu 15 Oct, 14:00 UTC | The sale opens |
| Thu 29 Oct, 14:00 UTC | The sale ends: claims open and the community's first unlock is available. Create the pool in the same hour, burn the LP, `withdraw` the unsold tokens once, move the proceeds to the multisig. If the hard cap sells out sooner, claims open then and the pool steps start then, but the locks' dates do not move: the first unlock still waits for this date |

The dates are proposals; the checks do not depend on them.

### Before Create

1. **Gates.** Legal sign-off, the jurisdiction decision and the published disclosures
   (roadmap §10) come first. Create moves the presale tokens — about **150,001,500 RCLAW** at
   the hard cap (hard cap ÷ the *stored* price; expected, not yet measured on this sale) — out
   of the signing wallet into a vault owned by an upgradeable program.
2. **The offline plan.** `cd token && npm ci && npm run presale:smithii-plan`. It prints what
   to type into each field, what the form must show back (**"Sending" 150,000,015 RCLAW** — the
   hard cap divided by the price *as typed*, as read off the live form on 2026-10-09 — and
   total fees 0.1 SOL), what the program is expected to take (the vault line, **150,001,500.015**,
   which is 1,485 more because the program floors the price to whole lamports), what the sale
   pays at each cap, the pool the operator will have to create, and every disclosure. It exits 1
   on a config that cannot describe a coherent sale, and CI runs it.
3. **The signing wallet.** Create is signed by the wallet that holds the tokens — today only
   one does. It becomes the launch authority: it receives about 97.5% of every purchase the
   moment it happens and alone can edit the launch and withdraw unsold tokens. Use a
   hardware-backed wallet, not an exchange's in-app browser, and plan to move the proceeds to
   the Squads multisig after the sale.
4. **Lock the rest first.** The other ~850,000,000 RCLAW. Three buckets are time locks made in
   Smithii's Token Vesting tool (method Cliffs): `npm run presale:locks-plan` prints, for each,
   what to type, every date, the amount the tool must show back at each step, and who must hold
   which tokens before anything is created. What that tool is and is not (read 2026-10-09,
   recorded in `locks.plan.json`): a staircase of at most five unlocks and no linear mode; **one
   vesting per wallet per token**, so three locks need three wallets, and the sale's signing wallet
   is a fourth; only the creating wallet can claim; no cancel and no edit; 0.4 SOL each; an
   upgradeable program, last deployed before its audit's fixes, so never "audited". Rehearse
   once on a throwaway wallet (3 RCLAW, three steps ten minutes apart, claim after each).
   Treasury, partners and the reserve are not locked by it; they go to the multisig. Publish
   every address (roadmap §11). The sale program locks and vests nothing, and scanners show one
   holder with everything unlocked until this is done.
5. **Rehearse on a throwaway token** with tiny caps: Create, one buy from a second wallet,
   claim, withdraw, create a pool, burn the LP. About 0.1 SOL plus dust. Nothing in this
   repository has run a Smithii sale of its own, and no Smithii guide read here mentions a
   devnet, so do not assume one.
6. **Have the pool ready**: the SOL and RCLAW to open it at the sale price (the plan prints
   the amounts for the soft and hard caps) and the transaction prepared. Claims open the moment
   the sale ends, and anyone holding claimed tokens can create a pool of their own.

### Create (Smithii's four-step form)

1. **Step 1** — type exactly what `presale:smithii-plan` prints, digits only (a locale that
   prints `30.000` for thirty thousand can misread `5.000` as five; Smithii's own form does:
   its "Sale Rate" read `30.000` and its "Sending" read `150.000.015`). Check the "Sending"
   line against the plan's **Sending** line before continuing: it is the typed-price figure,
   not the vault figure.
2. **Step 2** — leave the whitelist phase **off** (a whitelist price of 0 disables it).
   Turning it on doubles the creation fee and adds only an earlier time window that is open to
   everyone. Set the public phase's start and end to the two INSTANTS the plan prints under "the
   sale window" (15 Oct 2026 14:00 UTC → 29 Oct 2026 14:00 UTC today). The form's date picker
   uses your browser's clock, not UTC: run `npm run presale:smithii-plan -- --tz <your IANA
   zone>` for the clock times to type, and mind a clock change between the two dates (Europe's
   is on 25 Oct 2026: the same clock time on both dates is 337 hours, and the read-back flags
   the end). **Create at least 24–48 hours before the start**, because `edit` stops working once
   the first phase starts, and that window is when `presale:smithii-verify` can still be acted
   on.
3. **Step 3** — name, description, images, socials. Do not say "audited", and do not promise a
   refund, vesting, a locked LP, or returns. State the disclosures.
4. **Step 4** — preview, then Create. Before you sign, compare the token amount your wallet
   shows moving out with the plan's **Vault** line (about 150,001,500 RCLAW, 1,485 more than the
   form's "Sending"). A figure that is neither that nor the form's, or that is further from them
   than the price's lamport rounding (0.001%), is a stop: do not sign it.

### After Create, before the first phase starts

```bash
cd token
npm run presale:smithii-verify -- --authority <the wallet that signed Create>
```

Read-only. It reads the Launch account, the vault, the program and the mint off mainnet and
compares each with `smithii.config.json`. **Exit 0** — everything read matches; **1** — a row
FAILED; **3** — something could not be read, and nothing is claimed about it. It compares the on-chain
start and end of the public phase with `smithii.config.json` → `schedule`, to the minute: a gap of
whole hours there means the form was filled in local time, not UTC. A FAIL is fixable
with Smithii's `edit` only until the first phase starts. The first run also settles the one
assumption nothing earlier can: that the site rounds the price like Smithii's SDK (expect
**33,333 lamports per token**). Then publish the launch address, the program id, its upgrade
authority, the audit's scope, and the disclosures.

### During and after the sale

- The signing wallet receives the SOL as buyers buy. Do not spend it until the rule for a raise
  below the soft cap (roadmap §13) is decided and published.
- When the sale ends or the hard cap is reached, buyers can claim. **Create the pool in that
  same window**, at the sale price, with 80% of the gross raise; then **burn the LP tokens**
  and publish the pool address and the burn transaction.
- Call `withdraw` **once** to recover unsold tokens (a second call is refused), and move them
  to the reserve allocation as the config states.
- Move the proceeds to the multisig.

## The one decision point (Path A): `presale:trigger` may refuse

*Genesis alternative only — the Smithii sale has no trigger and no sized pool; the operator
creates the pool after the raise at whatever size honours the sale price.*

`presale:trigger` reads the realised raise from the bucket
(`quoteTokenDepositTotal`) and compares the pool's opening price against the
presale price. If the pool would open **below** it, the command stops:

```
Refusing to open the pool below the presale price.
  realised raise      : 412 SOL
  pool was sized for  : 1000 SOL (liquidity.sizedForRaiseSol)
  pool opening price  : 0.4120x the presale price
```

This is not a bug and re-running will not clear it. The LP token side was fixed
when the bucket was created, so a raise short of what it was priced for opens the
pool under what buyers paid — permanently, because the LP lock is never-claim,
and there is no refund instruction for a V2 PRESALE bucket. (A LaunchPool bucket does have one — see roadmap §5, The refundable alternative. Switching is a product decision.)

**There is no clean option at this point**, and the runbook should say so rather
than imply one:

- `npm run presale:trigger -- --accept-below-presale` proceeds. Every presale
  buyer is underwater at listing, irreversibly. The override is logged.
- Not triggering leaves the raise sitting in the presale bucket, and no V2
  withdraw path exists to get it out. That is not a free wait — it is a
  different irreversible position.

So the real work is upstream: decide what you will tell depositors **before** the
sale, publish the raise level the listing price depends on
(`config.disclosures.softCapNotEnforced` states it), and hold an operational
cancel-and-refund procedure ready if the terms promise one. This guard exists to
make sure that conversation happens before the button, not after.

## Rehearse it for free, on a local validator (Path A)

*Genesis alternative only. The Smithii sale has no local rehearsal: its program is not in this
repository, so the rehearsal is the throwaway-token run described under Path B.*

Devnet SOL is faucet-limited to 10 SOL per 8 hours. Rehearse against a local
validator instead — it costs nothing and can be reset as often as you like, so
there is no reason to run this sequence for the first time on the real thing.
Full instructions in [`../e2e/README.md`](../e2e/README.md).

After any rehearsal, run the program inventory:

```bash
RPC_URL=http://127.0.0.1:8899 npm run programs:inventory
```

It pulls the logs of every transaction the run produced and lists each program
that actually executed, CPIs included, failing on anything not in
`token/.program-inventory.json`. This is not the same check as `npm audit`: a
CPI target is not a package, so no dependency scanner can see it. That is how
the deposit, trigger and claim paths were found to be routing through MPL Token
Extras (`TokExjvjJ…`), third-party upgradeable bytecode that appeared nowhere in
this repository. Anything new in that list is a program you are trusting with
buyer funds — identify it and its upgrade authority before you continue.

## Post-sale checklist (both paths)

- [ ] LP burned (Smithii: the operator's burn transaction) or never-claim locked (Genesis),
      proof link published. The old "burned or locked ≥ 12 months" baseline is superseded:
      only a permanent burn or lock counts.
- [ ] Path A: `npm run programs:inventory` clean — no unattributed program in the money path.
      Path B: the program in the money path is Smithii's (`program.id` in the config), its
      upgrade authority and last-deploy slot re-read on launch day by `presale:smithii-verify`.
- [ ] Mint + freeze authority revoked (Path A: `npm run verify`; Path B: a row of
      `presale:smithii-verify`).
- [ ] Path B: `presale:smithii-verify` passed after Create and before the first phase started;
      unsold tokens withdrawn once; proceeds moved to the multisig.
- [ ] Treasury/team/advisor allocations on-chain-verifiable as locked.
- [ ] Claim window open and tested end-to-end.
- [ ] KPIs wired (participation, LP depth, holders) — roadmap §12.
- [ ] Disclosures + risk warnings live on the sale page (roadmap §10).

## Where these numbers come from

Every value traces to [`docs/TOKEN_ROADMAP.md`](../../docs/TOKEN_ROADMAP.md) §4 (allocation),
§5 (presale mechanics), §7 (liquidity). Change them there first, then mirror into these config
files and the GitBook page, so the roadmap stays the single source of truth —
`venue_parity.test.mjs` fails when the config, the roadmap and the GitBook disagree.
