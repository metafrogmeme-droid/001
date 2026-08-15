# Token Safety Scanner (defensive, detection-only)

> Detects rug/honeypot/manipulation *shapes* in a token so the agent can stand
> down. It never proposes a buy, and it never treats "no data" as "safe."

## What this is

A pure, deterministic scorer that turns a token's on-chain + market safety
features into a verdict — `safe` / `caution` / `danger` — plus a per-check report.
It is the #1 defensive gap and does double duty:

1. **User-facing** — folds into research dossiers and the meme/AI-token radar so a
   user sees *why* a token is dangerous before touching it.
2. **Feeder** — `to_veto_features()` maps its readings onto the exact keys the
   Guardian Integrity Veto consumes (`holder_concentration`, `wash_volume_ratio`,
   `listing_age_hours`, `price_liquidity_divergence`), so this scanner is what
   unblocks the veto's engine wiring.

## Discipline (matches the veto-only / honest-UNVERIFIED rules)

- **Detection, never generation.** It flags honeypot/rug shapes; it never creates
  them, and it never originates or up-votes a trade — its only outputs are
  stand-down signals.
- **No data ≠ safe.** A check with missing input is `unknown`, never counted as a
  pass. A token whose safety cannot be established (too many unknowns) is at best
  `caution` — the same discipline as Proof-of-PnL's `UNVERIFIED`. `safe` requires
  positive evidence.
- **A single disqualifying reading forces `danger`** (a hard flag): e.g. a
  honeypot that can't be sold, a live mint authority (infinite-supply rug), a sell
  tax high enough to trap exits, or one wallet holding the majority of supply.

## Checks

| check | hard-danger trigger | soft flag |
|---|---|---|
| `honeypot_cannot_sell` | `True` | — |
| `mint_authority_active` | `True` (supply can be inflated) | — |
| `freeze_authority_active` | `True` (balances can be frozen) | — |
| `sell_tax_pct` | ≥ 30 (exit trap) | ≥ 10 |
| `buy_tax_pct` | — | ≥ 10 |
| `top_holder_pct` | ≥ 0.5 (one wallet dumps all) | ≥ 0.3 |
| `ownership_renounced` | — | `False` |
| `lp_locked` | — | `False` |
| `liquidity_usd` | — | < 10,000 |
| `holder_count` | — | < 50 |
| `listing_age_hours` | — | < 24 |

Verdict: any hard flag → `danger`; else weighted soft score maps to
`caution`/`danger`; a clean bundle with enough positive evidence → `safe`; a bundle
that is mostly `unknown` → `caution` (cannot certify).

## Pre-registered predictions (before the tests)

- **T1 — no data is not safe.** An empty (or mostly-unknown) feature bundle returns
  `caution`, never `safe`. *Falsifier:* an unknown-heavy bundle returning `safe`.
- **T2 — hard flag forces danger.** Any single hard trigger (honeypot, live mint,
  ≥30% sell tax, ≥50% top holder) returns `danger` regardless of clean fields.
  *Falsifier:* a hard trigger returning `safe`/`caution`.
- **T3 — clean, well-evidenced token clears.** A token with mint/freeze renounced,
  LP locked, low taxes, distributed holders, deep liquidity, and age returns
  `safe`. *Falsifier:* such a token not clearing.
- **T4 — veto-feature mapping + determinism.** `to_veto_features` maps readings
  onto the Integrity-Veto keys; the same bundle yields the same verdict + score
  every time; detection is never generation (no buy/positive output exists).
  *Falsifier:* a mismatched mapping, an unstable verdict, or any positive output.

Results: `tests/test_token_safety.py`.

## Reaching it: `/token`

`/token <address> [chain]` runs the whole chain — `token_sources` → `token_safety`
for the contract, `deployer_sources` → `deployer_history` for whoever shipped it,
`token_dossier` to compose — and renders it with every unread section named.

`/research` was already taken by the symbol research card (venue data + recorded
platform history), which is a different feature; registering over it would have
replaced that command silently.

### What the deployer half can and cannot answer

`EtherscanDeployerSource` supplies five of the eight facts `assess_deployer`
reads: `contract_verified`, `wallet_age_days`, `prior_deployments`,
`concurrent_launches_24h`, `deployer_supply_pct`.
It cannot supply `funded_by_mixer` or `reused_rug_bytecode` — those need a mixer
address list and a rug-bytecode corpus. It also cannot say how the deployer's
previous contracts ENDED, which is what `deployer_fates` is for.

### The fate pass

`bot/core/deployer_fates.py` takes the prior contract ADDRESSES (which is why the
explorer source emits `prior_contracts` and not just a count — you cannot look up
the fate of a number) and asks a price feed what became of each.

A price feed can prove two things and not a third:

| reading | conclusion |
|---|---|
| deep pool, traded in the last 24h | **alive** — a market exists |
| near-empty pool, ≥30 days old | **dead** — the market it had is gone |
| no indexed pair · failed request · young pool · middle band | **unresolved** |

It cannot prove a **rug**. "The liquidity left" and "somebody pulled the
liquidity" are the same reading; the difference is intent, and no price feed
carries it. So the pass writes `prior_dead` and *never* `prior_rugged`, and
`deployer_history` scores dead as a soft ratio with **no hard threshold**.
Feeding a heuristic into `prior_rugged` would hard-fail an honest builder on the
strength of the word "confirmed" — the most damaging thing this codebase can say
about a person, manufactured out of a liquidity number.

> A MARKET THAT ENDED IS NOT A MARKET THAT WAS STOLEN.

**Why "no pair at all" is not dead.** A deployer's history is full of contracts
that were never tokens — proxies, multisigs, NFT collections, factories — plus
tokens that only ever traded on a centralised venue. None has a DEX pair and none
of them died. Unindexed is `unresolved`, which costs the deployer only the
coverage they would have needed to be certified.

### What this unlocks, and what it does not

A deployer whose prior tokens are demonstrably still trading can now reach
`clean` — the first input that could ever produce it. `known_bad` stays
unreachable from this path.

The trap from the module's own first run stays closed: `_outcomes_resolved`
requires that somebody counted the BAD outcomes (`rugged` and `dead` both `None`
means nobody looked) *and* a determined fate for half the record. Nine survivors
nobody verified is still `unproven`.

With no `ETHERSCAN_API_KEY` the source reports `unavailable` — *we never asked* —
and the section stays **not read**, which is a different row from *we asked and
learned nothing*.

### Two traps a future source author should know

- **`deployer_supply_pct` is a FRACTION**, despite the name. Its hard threshold
  is `0.5` against the message "deployer holds ≥50% of supply", and
  `tests/test_deployer_history.py` passes `0.03` / `0.6`. Emitting `60` for 60%
  clears the hard threshold by 120× and marks every token a scam.
- **A partial read must omit, never zero.** An unreadable transaction list
  becoming `wallet_age_days: 0` manufactures the "less than a week old" flag out
  of a failed request, and a truncated list becoming a `prior_deployments` count
  publishes a floor as a total — that count is the denominator of the deployer's
  record. Both cases are pinned in `tests/test_deployer_sources.py`.

### Discoverability is enforced, not remembered

`/token` is listed in `bot/skills/command_catalog.py`, which is what `/help`
renders from. That is not optional: `tests/test_command_catalog.py` asserts the
catalogue and the handler's registration list match **exactly**, in both
English and Chinese, so a command cannot ship undocumented and a retired one
cannot linger in the docs.

Worth knowing before adding the next command — the i18n `help_*` blocks are a
different, older surface and grepping those instead suggests (wrongly) that
recent commands go unlisted. The catalogue is the source of truth, and the gate
catches you either way.
