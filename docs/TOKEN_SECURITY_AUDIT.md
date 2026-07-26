# $RCLAW Token Security Audit

## Executive Summary

The audit request assumed EVM/Solidity with ERC-20 and ERC-2612 permit semantics; none of that framing applies, because this repository contains zero `.sol` files. $RCLAW is a Solana-native SPL **Token-2022** mint (`token/config/token.config.json:5-6` — 9 decimals, 1,000,000,000 fixed supply), its only on-chain program is `programs/rclaw_staking` (338 lines, Anchor 0.30.1 per `programs/rclaw_staking/Cargo.toml:21`), and the mint, the Metaplex Genesis presale and the Wormhole NTT bridge are all off-chain Node ESM tooling under `token/`. Read in Solana terms the result is lopsided in a specific way: **the on-chain program is the strong half.** Both handlers — `stake` (`lib.rs:90`) and `unstake` (`lib.rs:137`) — constrain the accounts an attacker would substitute: stake records are seeded on `["stake", owner, mint]` and re-checked with `has_one = owner` / `has_one = mint` (`lib.rs:226-232`), the vault must be the ATA of a mint-scoped `["vault", mint]` authority (`lib.rs:198-205`, `lib.rs:239-245`), and both user token accounts are owner- and mint-checked (`lib.rs:207-212`, `lib.rs:247-252`). The vault-drain an earlier revision shipped (`lib.rs:4-8`) is genuinely closed and no equivalent path replaced it. **Nothing rated Critical or High survived adversarial verification.** The issues concentrate in the off-chain tooling and the tier gate, and the most serious of them is that the security property the token exists to sell does not hold: `/linkwallet` accepts any base58-shaped string with no proof of key ownership (`bot/skills/telegram_handler.py:8103`), so any user can claim any staker's tier by typing their address, and `tier_gate._rpc()` returns `None` for every RPC URL containing `mainnet` (`bot/token/tier_gate.py:100-104`), which `allows_user` converts into a fail-open `True` (`bot/token/tier_gate.py:233-234`) — the paid gate unlocks for every user at the precise moment it is aimed at the chain it is meant to enforce on.

Deployment status is stated honestly in the manifests and should stay that way until four things change: `Anchor.toml:1,16` declares DRAFT / DEVNET-ONLY on devnet, both `declare_id!` (`lib.rs:41`) and `Anchor.toml:10` still carry the well-known Anchor placeholder id `Fg6PaFpoGXkYsidMpWTK6W2BeZ7FEfcYkg476zPFsLnS`, `token/package.json:3` is `0.1.0-draft`, `ntt.config.json:5` is `Testnet`, `lib.rs:3` says do not deploy to any cluster holding real value, and no mint exists (`PINNED_MINT` is an `option_env!` at `lib.rs:63`, unset by default). **Key custody:** one plaintext, world-readable keypair at `token/.keys/mint-payer.json` (`token/scripts/keygen.mjs:11-19`) holds mint, metadata, metadata-pointer, presale and unsold-token authority plus the entire supply, and the program upgrade authority — the trust root for every vault — is addressed in no checklist; without a multisig, a single file read is total compromise of both supply and staked funds. **Cluster identification:** the only mainnet guard in the repository is a case-sensitive substring test (`token/scripts/lib.mjs:30`, `bot/token/tier_gate.py:100`) and the `cluster` field at `token.config.json:17` is dead text, so "devnet-only" is currently enforced by a string match that `https://rpc.helius.xyz/...` or `MAINNET` defeats. **The gate must fail closed and require a signed challenge**, or the tier is decorative. **Reproducibility:** `[toolchain]` at `Anchor.toml:3` is empty, there is no `rust-toolchain.toml`, and `.github/workflows/ci.yml` runs only Python (ruff/mypy/bandit/pip-audit) — so no Rust, Anchor or Node code here is checked by any automation, and deployed bytecode cannot be verified against this source. These conclusions come from six parallel specialist lenses producing 55 raw findings, semantically triaged to 43 canonical, adversarially verified with three independent refuters per Critical/High candidate (majority refutation kills the finding) which retired 8, then a completeness critic and five targeted gap sweeps, leaving 41.

## Status since publication

This report is an as-of record, audited at commit `1fb55d4` on 2026-07-25, and
the body below is deliberately left as written. Remediation has since landed, so
several statements in it — including in *Coverage & Limitations* — are no longer
true of the current tree. What changed, and when:

| Date | Change | Effect on this report |
|---|---|---|
| 2026-07-26 | 16 Medium findings fixed (#825) | See each finding's remediation. |
| 2026-07-26 | 22 Low + 3 Info findings fixed (#828) | ” |
| 2026-07-26 | ATA-decoy tests committed (#834) | Retires the *Coverage & Limitations* note that the decoy test "is **not committed to the repository**" and that "the single most important guard … has no permanent regression coverage". It is now covered in both directions, and the pinned CI run exercises it. |
| 2026-07-26 | Quote split encoded on-chain (#836) | Closes the enforcement half of F-11. |
| 2026-07-26 | Lock-up ratified at 30 days (#839) | Resolves the §13 open decision behind F-18; supersedes the 7-day default suggested here. |
| 2026-07-26 | `BOT_SYNC_SECRET` rotated on both the bot and web sides | Closes the live-credential incident described in the CI notes. The historical value in commit `9435602` is dead, and full-history secret scanning is now enforced in CI (it was previously blocked precisely *because* the credential was live). |
| 2026-07-26 | Vault invariants mechanically checked (`tests/solvency.rs`) | Narrows "**No fuzzing and no formal verification** were performed … no invariant (including vault solvency) was mechanically proven." Solvency, conservation, lock-up and lock-monotonicity are now checked after every operation across a deterministic randomised sequence, and each invariant is mutation-tested. It remains sampling, not proof: no fuzzer and no formal verification. |
| 2026-07-26 | Cluster guards verified against live chains (`scripts/cluster_guard.test.mjs`) | The audit environment returned 403 for `api.devnet.solana.com`, so the three genesis-hash constants were asserted from memory rather than observed. Devnet is now reachable and all three were confirmed against the live chains, including that real mainnet-beta is refused. |
| 2026-07-26 | npm advisory ratchet in CI (`token/scripts/audit_gate.mjs`) | Narrows "**Dependency advisories are current only as of the audit date** … a new advisory landing tomorrow will not surface anywhere." New advisories now fail CI. The 37 recorded here (1 critical, 15 high) are baselined, still outstanding, and still block a value-bearing deployment. |

Unchanged and still open: no live devnet deployment or SBF build has happened
(blocked on faucet funding, not on code), no third-party audit exists, the
program upgrade authority is still a single key, and the Anchor IDL account
lifecycle noted below remains unowned.

## Scope

Audited at commit `1fb55d4` with a clean working tree. **Chain and standards:** Solana; SPL Token-2022 (`create_token.mjs` uses the Token-2022 program with the MetadataPointer and TokenMetadata extensions); Anchor 0.30.1 (`programs/rclaw_staking/Cargo.toml:21-22`, confirmed resolved in `Cargo.lock:223-224`); Metaplex Genesis `^0.40.0` for the fixed-price presale; Wormhole NTT `@wormhole-foundation/sdk` 5.2.0 with `sdk-solana-ntt` / `sdk-evm-ntt` 7.2.0 for the Solana↔Base bridge (`token/package.json:29-39`). Every file below was read in full.

**On-chain program and build configuration**

| File | Lines |
|---|---|
| `programs/rclaw_staking/src/lib.rs` | 338 |
| `programs/rclaw_staking/tests/attack.rs` | 341 |
| `programs/rclaw_staking/tests/rclaw_staking.ts` | 103 |
| `programs/rclaw_staking/Cargo.toml` | 29 |
| `programs/rclaw_staking/README.md` | 150 |
| `Cargo.toml` (workspace, `overflow-checks = true` at `:8-9`) | 16 |
| `Anchor.toml` | 24 |

**Off-chain token tooling (`token/`, Node ESM)**

| File | Lines |
|---|---|
| `token/scripts/create_token.mjs` | 150 |
| `token/scripts/verify_token.mjs` | 50 |
| `token/scripts/keygen.mjs` | 40 |
| `token/scripts/lib.mjs` | 61 |
| `token/presale/genesis_presale.mjs` | 340 |
| `token/presale/genesis_lib.mjs` | 247 |
| `token/presale/allowlist_serialize.test.mjs` | 69 |
| `token/bridge/ntt_bridge.mjs` | 149 |
| `token/e2e/devnet_dryrun.mjs` | 157 |
| `token/presale/metaplex-genesis.config.json` | 61 |
| `token/presale/smithii.config.json` | 36 |
| `token/bridge/ntt.config.json` | 34 |
| `token/config/token.config.json` | 18 |
| `token/config/rclaw-metadata.json` | 12 |
| `token/package.json` (+ committed `package-lock.json`, never enforced) | 41 |
| `token/.env.example` / `token/.gitignore` | 12 / 5 |
| `token/README.md`, `token/presale/RUNBOOK.md`, `token/bridge/README.md` | 78 / 113 / 75 |

**Trust chain outside `token/`.** The sweep deliberately followed the token's guarantees to the place they are actually consumed, because a staking program is only as strong as the gate that reads it: an unforgeable on-chain stake is worthless if the consumer accepts an unproven wallet address or fails open. Three files carry that chain end to end — `bot/token/tier_gate.py` (247 lines: `staked_of()` parses `StakeAccount` bytes at `:171-183`, `allows_user()` makes the access decision at `:214-236`), `bot/skills/telegram_handler.py` (11,143 lines, of which the $RCLAW surface is in scope: command registration at `:358`, `_cmd_linkwallet` at `:8080-8111`, `_token_gate_blocks` at `:8113-8127`), and `bot/utils/user_store.py` (598 lines; `get_sol_wallet` at `:444`, `set_sol_wallet` at `:455`, the persistence the gate trusts). Repo-level controls in scope: `.github/workflows/ci.yml` (62), `.gitignore` (103), `.env.example` (684).

**Excluded, with reasons.** `docs/TOKEN_ROADMAP.md` (522 lines) was read as a statement of intent and used to test claims against implementation, not audited as code. The rest of the Python trading bot, `app/`, `api_bridge.py` and the dashboard are outside the token's trust chain and were not reviewed. The EVM code that does exist in this repository — `bot/core/contract_studio.py` (an optional `solcx` compile helper), `bot/proofofpnl/ingest_onchain_evm.py`, `app/lib/defi.js` — belongs to Contract Studio and Proof-of-PnL, references $RCLAW nowhere, and compiles no Solidity that ships here; there are no `.sol` files in the repository at all. The transitive npm dependency graph under `token/node_modules` was not audited package-by-package; the absence of any lockfile enforcement or `npm audit` is reported as a finding rather than substituted for by this review. `token/e2e/README.md` (41 lines) is documentation. Finally, no deployed artifact was examined because none exists: there is no mint, no deployed program id, and no on-chain state to inspect.

## Findings

**Finding ID key.** Ids were assigned at triage and are stable. Gaps in the sequence (F-13, F-27, F-32, F-33, F-36, F-38) are candidates that were retired in adversarial verification; they appear in the Refuted Candidates table rather than here. F-41 and above come from the targeted gap sweeps that ran after the completeness critic.

Forty-one findings survived verification, none Critical or High, and they cluster in off-chain key custody, presale correctness, and the tier gate rather than in the staking program.

| Critical | High | Medium | Low | Info | Total |
|---|---|---|---|---|---|
| 0 | 0 | 16 | 22 | 3 | 41 |

### [MEDIUM] F-01 -- /linkwallet accepts any base58 address with no ownership proof — any user can claim any staker's tier

**Severity.** Graded down from the reported Critical to Medium, deliberately, and the title is the reason a reader will expect worse. Vector is `AV:N/AC:L/PR:L/UI:N/S:U/C:N/I:H/A:N`: any Telegram account can reach it, complexity is trivial, no victim interaction is required. But nothing is stolen. The victim's `StakeAccount` is untouched — the attacker cannot lock it, drain it, or unstake it, and the victim keeps their own tier. This is impersonation, not theft. Nor is any bot authority gained: the gated set is exactly three read-only market-scan commands (`_cmd_scalp`/`_cmd_intraday`/`_cmd_swing`, `bot/skills/telegram_handler.py:8129/8146/8163`), and live trading is a separate authority (`_can_trade_live`, `telegram_handler.py:1933`) requiring both the env allowlist and a per-user store flag, which this does not touch. Confidentiality and availability are unaffected. What *is* lost is the entire economic premise of $RCLAW as an access token: a tier becomes an attribute of a public string rather than a right held by a keyholder, and because `set_sol_wallet` enforces no uniqueness (`bot/utils/user_store.py:455-469`), one staker's address can be replayed by unlimited Telegram accounts concurrently. That is a total authentication bypass of the paywall, which is why it is not Low. It is capped at Medium because it is dormant as shipped — `TOKEN_TIER_GATE_ENABLED` and `RCLAW_MINT` appear nowhere in `.env.example`, so `allows_user` returns `True` at `tier_gate.py:222-223` for everyone and there is currently no gate to bypass.

**Confidence.** High. PLAUSIBLE. Votes: 1 confirmed / 2 plausible / 0 refuted.

**Location.** `bot/skills/telegram_handler.py:8101-8106`

**Code.**
```python
        addr = args[0].strip()
        # Base58, 32-44 chars — same shape the web app validates.
        if not re.fullmatch(r"[1-9A-HJ-NP-Za-km-z]{32,44}", addr):
            await self._send(update, "\U0001f534 That doesn't look like a Solana address (base58, 32-44 chars).")
            return
        if not self.users.set_sol_wallet(uid, addr):
```

**What's wrong.** The only validation on a linked wallet is a base58 shape regex. There is no signature challenge, no sign-in-with-Solana nonce, and no proof of key control anywhere on the path. `UserStore.set_sol_wallet` (`bot/utils/user_store.py:462`) writes the self-asserted string verbatim and persists it; `tier_gate._resolve_wallet` (`bot/token/tier_gate.py:197-211`) reads that exact field back, and `allows_user` (`tier_gate.py:227-236`) derives the caller's tier from that address's on-chain stake. `set_sol_wallet` also enforces no uniqueness, so N Telegram accounts may hold the same `sol_wallet` simultaneously and all receive the same tier.

The strongest evidence that this is a mistake rather than an accepted risk is unreported by the original finder and sits in this repo already. `app/lib/solana_verify.js:49` implements a correct, tested ed25519 `verifySignedMessage`, wired into a single-use expiring-nonce flow at `app/auth.js:865-875` (nonce lookup, expiry check, verify, `delNonce`). But that endpoint writes `users.sol_address` in the Node DB (`app/auth.js:876`), while the tier gate reads `sol_wallet` from the Python `users.json`. The two stores are disjoint and nothing bridges them, so the verified link never reaches the gate and the unverified Telegram link is the gate's *sole* input. The comment at `telegram_handler.py:8102` — "same shape the web app validates" — is the root misconception: the web app validates shape *plus key control*.

Two corrections to the original write-up. First, "any user can claim any staker's tier" is literally true but reads as theft; the loss falls entirely on the operator as revenue bypass, not on the staker. Second, reachability is config-gated, which the original omits: it requires the operator to have set `TOKEN_TIER_GATE_ENABLED` + `RCLAW_MINT` and to have left `TELEGRAM_CHAT_ID`/`ADMIN_TELEGRAM_IDS` empty.

**Exploit / reachability.** This is an off-chain Python path, not an Anchor instruction — there is no on-chain state delta at all, which is precisely the point: the attacker never signs anything, so there is no on-chain trace, no rate limit, and no revocation path for the victim.

Preconditions, all operator-set at deploy time: `TOKEN_TIER_GATE_ENABLED=true` and `RCLAW_MINT` set (required by `gate_enabled()`, `tier_gate.py:88-94`); `RCLAW_STAKING_PROGRAM` set for the staked-balance path, though without it the same attack works against raw wallet balance via `balance_of`; all three allowlist variables empty (`.env.example:108,112` ship them blank); and `RCLAW_RPC_URL` not containing the literal substring `"mainnet"`, else `_rpc` returns `None` and the gate opens for everyone anyway.

1. The attacker enumerates victims with an unauthenticated `getProgramAccounts` on the staking program, base64 encoding, reading `owner` at byte offset 8 and `amount` (u64 LE) at offset 72. I verified those offsets against the real struct at `programs/rclaw_staking/src/lib.rs:257-264` — `#[account]` prepends the 8-byte Anchor discriminator, so 8/40/72 is exact. This step is optional; any publicly known staker address works.
2. `/start` — `UserStore.register` auto-approves the stranger with `"role": auto_role` (`DEFAULT_AUTO_ROLE = "trader"`, `user_store.py:122`) and `"authorized": True` (`user_store.py:199`). This satisfies the authorized check at `telegram_handler.py:2036` and puts the key in `self._users` so `set_sol_wallet` will not return `False`.
3. `/linkwallet <victim_address>` — decorated `@guard("help")`, and `"help"` is in every role set including `pending` (`user_store.py:41`). `_is_allowlisted` returns `True` because the allowlist is empty. The regex checks shape only. The address is written and saved. No signature, no nonce, no uniqueness check, no notification to the victim.
4. `/scalp` — `@guard("scan")`, and `"scan"` is in `ROLE_PERMISSIONS["trader"]` (`user_store.py:32`). `_token_gate_blocks` calls `allows_user` (`telegram_handler.py:8121`); `_resolve_wallet` returns the victim's address; `staked_of` memcmps offset 8 on it; the victim's real staked amount comes back; `tier_for_balance` returns `pro`/`elite`; the rank comparison at `tier_gate.py:236` passes.
5. Repeat from N accounts against the same address concurrently. Nothing dedupes.

The verdict is PLAUSIBLE rather than CONFIRMED for exactly one reason: the code defect is unconditional and certain, but the gate flag ships disabled and is absent from `.env.example`, so no deployment is verifiably exposed from source. Three near-miss guards were checked and none blocks it. `_is_allowlisted` (`telegram_handler.py:1924-1931`) self-disables when unconfigured (`if not allow: return True`) and is structurally incompatible with the feature it would protect — an allowlist of specific Telegram IDs already decides who is in, so a stake-to-unlock gate is meaningless alongside it; the two are only both meaningful in the exact configuration where the allowlist is empty. `gate_enabled()` is a precondition, not a constraint on the attacker. And the mainnet refusal at `tier_gate.py:100-104` makes the attack *unnecessary* rather than impossible, because `None` flows to the fail-open at `:233-234`. A repo-wide grep confirms `set_sol_wallet` has exactly one caller — `_cmd_linkwallet` — so the sole writer of the field the gate consumes is the unverified command.

**Remediation.** No new dependency is required: `cryptography>=43.0.1` is already declared (`pyproject.toml:28`, `requirements.lock:13`) and `Ed25519PublicKey` is already used in-repo at `bot/proofofpnl/erc8004.py:226-228`.

First, fail closed, which is a one-line change that ships immediately. An unverified address must not carry a tier. At `bot/token/tier_gate.py:227-229`:

```python
    wallet = _resolve_wallet(users, uid)
    if not wallet or not _wallet_verified(users, uid):
        return False
```

Store `sol_wallet_verified_at` alongside the address and treat a legacy address with no flag as unverified — otherwise every record that predates the change bypasses the next step.

Second, add challenge-response to `/linkwallet`, mirroring the flow the repo already has at `app/auth.js:865-875`. Split the command: `/linkwallet <address>` validates shape, generates a single-use nonce, stores `{pending_wallet, nonce, expires_at}` with a five-minute TTL, and replies with the exact message to sign — it must **not** call `set_sol_wallet`. Then `/linkwallet verify <base64_signature>` looks up the pending nonce, rejects if missing or expired, and commits only after:

```python
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
Ed25519PublicKey.from_public_bytes(base58_decode(pending_wallet)).verify(
    base64.b64decode(sig), nonce_message.encode())
```

Delete the nonce on use and on failure. Bind the message to the Telegram id and a timestamp — `RUNECLAW link tg:<uid> nonce:<hex> exp:<iso>` — so a signature harvested for one account cannot be replayed onto another.

Third, enforce uniqueness in `set_sol_wallet` (`bot/utils/user_store.py:455-469`), inside the existing `self._lock`, so one stake cannot back N accounts:

```diff
     if address:
+        addr = str(address)
+        for other, rec in self._users.items():
+            if other != key and rec.get("sol_wallet") == addr:
+                return False          # already bound to another account
-        self._users[key]["sol_wallet"] = str(address)
+        self._users[key]["sol_wallet"] = addr
```

The caller at `telegram_handler.py:8106` already handles a `False` return, though its "unknown user" message should be widened to cover the collision case.

Longer term, consider having the tier gate read the already-verified `sol_address` the web flow produces rather than maintaining a second, weaker linking path, and persist the `verified` boolean that `app/auth.js:878` currently computes and throws away.

*Without this, the first time an operator enables the tier gate on a public bot, every paying staker's tier is available for free to every non-payer who copies their address off the ledger, and the token's only stated utility stops generating revenue.*

---

### [MEDIUM] F-02 -- Program upgrade authority is the entire trust root for every staking vault and is unaddressed everywhere — no multisig, no timelock, no renounce plan, not in any checklist

**Severity.** This grades in two directions and lands at Medium. The impact *if realized* is catastrophic: the upgrade authority can replace bytecode while keeping the program id, so the `["vault", mint]` PDA derivation and its ability to sign `transfer_checked` survive verbatim. That is total, unrecoverable loss of every escrowed stake across every mint — I:High/A:High, a single privileged actor, no user recourse and no exit window, since `unstake` is served by the replaced bytecode. On a live mainnet vault this is Critical. Present reachability, however, is zero: `declare_id!` is still the stock `anchor init` placeholder `Fg6PaFpoGXkYsidMpWTK6W2BeZ7FEfcYkg476zPFsLnS` (`programs/rclaw_staking/src/lib.rs:41`, `Anchor.toml:10`) and has never been synced; no $RCLAW mint exists (`docs/TOKEN_ROADMAP.md:500`: "No real mint exists to pin yet"); `Anchor.toml:16` targets devnet; and DO-NOT-DEPLOY banners sit at `lib.rs:3` and `programs/rclaw_staking/README.md:3`. There is also no code fix possible — a program cannot defend against replacement of its own bytecode — so this is a deploy-time process control, not a missing constraint. Medium is the honest grade for a verified must-fix-before-mainnet process gap with catastrophic contingent impact and no present exploitability.

**Confidence.** High. PLAUSIBLE. Votes: 0 confirmed / 3 plausible / 0 refuted.

**Location.** `docs/TOKEN_ROADMAP.md:399-410` (primary — the omission), with `Anchor.toml:15-17` as corroborating evidence of the default single-key deploy path.

**Code.**
```toml
[provider]
cluster = "devnet"
wallet = "~/.config/solana/id.json"
```

**What's wrong.** Both `anchor deploy` and `solana program deploy` use BPFLoaderUpgradeable by default and record the deploying wallet as upgrade authority in the ProgramData account. Staked tokens live in the ATA of `vault_authority`, a PDA of this program (`lib.rs:194`, `:236`), and the only thing preventing that PDA from signing an arbitrary transfer is the bytecode — which the upgrade authority can replace at will.

The defect is an omission with no single line number, and `Anchor.toml:15-17` is evidence rather than the defect itself. Program upgrade authority is absent from every pre-deploy control surface in the repo. `docs/TOKEN_ROADMAP.md:399-410` is a section literally titled "Security & rug-resistance checklist" and enumerates nine authority-hygiene controls — mint authority revoked (`:401`), freeze authority revoked (`:402`), LP locked (`:403`), Squads multisig treasury with time-lock (`:404`), independent audit (`:405`), anti-snipe (`:407`), metadata immutability (`:408`), verifiable reserves (`:409`), locked allocations (`:410`) — covering the full SPL token surface while omitting the one authority that unilaterally governs the program holding all escrowed user funds. The §14 pre-deploy gates (`docs/TOKEN_ROADMAP.md:516-523`) omit it too, as do `programs/rclaw_staking/README.md:145-151` ("Known limitations": unaudited, no cooldown, no rewards, placeholder `declare_id!`) and `token/presale/RUNBOOK.md:103` (token authorities only). `.github/workflows/ci.yml` is Python-only — a grep for `cargo|rust|anchor|solana` returns nothing, so it does not even run `cargo test -p rclaw_staking`. A repo-wide grep for `upgrade[- _]?auth|set-upgrade-authority|--final|BPFLoaderUpgradeable` returns zero relevant hits; every `immutable`/`multisig`/`renounce` hit is token, treasury, or unrelated app code.

The nearest thing to coverage is `TOKEN_ROADMAP.md:404`, "Squads multisig treasury + time-lock on privileged actions; no single signer" — but it is scoped to the treasury, never names the program or its deploy key, and is unchecked. `TOKEN_ROADMAP.md:282`'s "authorities revoked on mainnet" refers back to `:275`'s mint and freeze revocation, which are SPL mint authorities with no effect whatsoever on program upgradeability. So the substantive harm is false assurance: an operator who ticked all nine §11 boxes and all four §14 gates would ship a vault whose entire balance remains transferable at the discretion of a single deploy key.

One factual correction to the original write-up: it claims "no timelock, no on-chain event." A BPFLoaderUpgradeable upgrade *does* produce a visible on-chain transaction and mutates the ProgramData account — it is observable, just neither preventable nor something stakers would be monitoring. The "no timelock" and "no quorum" halves are accurate.

**Exploit / reachability.** Not reachable today; reachable only after a value-bearing deployment that the repo currently forbids. The mechanism is mechanically sound and nothing stops it once deployed. `anchor deploy` against the provider block at `Anchor.toml:15-17` records `~/.config/solana/id.json` as upgrade authority. Users call `stake`, and each mint's vault ATA (`lib.rs:198-205`, authority = the `["vault", mint]` PDA at `lib.rs:194`) accumulates escrowed supply. The key holder then signs `solana program deploy --program-id <same id> evil.so`. Because the program id is unchanged, `evil.so` derives the identical PDA and can sign with `&[b"vault", mint.as_ref(), &[bump]]` — the exact construction already present at `lib.rs:143` — to CPI `transfer_checked` out of every vault in one transaction. BPFLoaderUpgradeable's `Upgrade` handler checks precisely one thing: that the address in the ProgramData header's `upgrade_authority_address` field signed. There is no timelock primitive in the loader and no notice period.

No Anchor constraint blocks this and none could, because the attack operates one level below the program: `owner: Signer<'info>` (`lib.rs:176`, `:222`), the seeds/bump derivations (`lib.rs:187-188`, `:194`, `:228-229`, `:236`), `has_one = owner` / `has_one = mint` (`lib.rs:230-231`), and the `user_token_account` owner+mint constraints (`lib.rs:209-210`, `:249-250`) are all correct and all defend against hostile *callers*, not a hostile program author. An upgrade replaces them wholesale.

The verdict is PLAUSIBLE because three preconditions are unverifiable from source, all off-chain or future-deploy-time: that the program is ever deployed to a cluster holding real value (today it is not); that the deployer retains upgrade authority rather than transferring it to a multisig or running `--final` at deploy time (there is no `anchor deploy` or `solana deploy` script anywhere in the repo, and the `Makefile` `deploy` target is Docker-only, so neither outcome is pinned); and that the key is subsequently compromised or misused. What raises confidence above generic centralization boilerplate is that the bad outcome is the committed default and the good outcome requires an undocumented manual deviation.

**Remediation.** Documentation and process only. Add to `docs/TOKEN_ROADMAP.md` §11, alongside the audit item:

```markdown
- [ ] **Program upgrade authority** on `rclaw_staking` transferred to a **Squads multisig
      + time-lock** immediately post-deploy; single-signer deploy keys never retained.
      Verify with `solana program show <PROGRAM_ID>` and publish the result.
- [ ] **Immutability plan** published: either `--final` once the program is stable, or a
      standing multisig + timelock with the quorum and delay stated up front.
```

Add a fifth gate to §14: *transfer the program upgrade authority off the deploy key before the vault accepts its first deposit — not after*. Ordering matters, because the window between deploy and authority transfer is the exposure. Add to `programs/rclaw_staking/README.md:145-151`, which is where an operator reading only that file will look: "Upgradeable by default. Whoever holds the upgrade authority can replace the bytecode and sign for every `["vault", mint]` PDA. Move it to a multisig before any real value."

For the runbook:

```bash
solana program show <PROGRAM_ID>                         # inspect current state
solana program set-upgrade-authority <PROGRAM_ID> \      # do this FIRST
  --new-upgrade-authority <SQUADS_VAULT_PDA>
solana program set-upgrade-authority <PROGRAM_ID> --final  # only once audited — IRREVERSIBLE
```

Sequence multisig-then-immutable, never straight to `--final` on an unaudited program: that would permanently freeze any bug, including one an audit later finds. Pair this with `anchor keys sync` to replace the placeholder `declare_id!`, publish the resulting program id, and ship a verifiable build (`solana-verify`) so stakers can confirm deployed bytecode matches this source — without a pinned, published, verifiable id, a silent upgrade is undetectable in practice even though the transaction is technically on-chain. Optionally add `cargo test -p rclaw_staking` to `.github/workflows/ci.yml`, which currently never touches Rust.

*Without this, an operator who completes every published pre-deploy gate still ships a system where one hot filesystem key can drain every vault in a single transaction, and the checklist that says otherwise is what they will point third parties at.*

---

### [MEDIUM] F-03 -- tier_gate's mainnet RPC refusal makes the paid gate fail OPEN at mainnet launch — every gated feature unlocks for every user

**Severity.** The mechanism is fully confirmed by execution, but impact is business/revenue only and is deployment-gated. At mainnet enablement this is a complete bypass of the token's single stated access-control utility (roadmap §3), with no asset loss, no key or authority compromise, no availability loss, and no on-chain state change — the staking program and vault are untouched. The loss is "the paywall collects nothing." Four factors hold it at Medium rather than High. It has zero impact in any configuration that ships today: `gate_enabled()` (`tier_gate.py:94`) requires `TOKEN_TIER_GATE_ENABLED` plus a mint, the default RPC is devnet, no `.env` exists in the repo, and per the roadmap header no token exists and no sale has run. It is not silent — `system_log.warning` fires on every `_rpc` call (`tier_gate.py:103`), so a mainnet-configured deployment emits one WARNING per gated command, though the user-visible symptom (free access) generates no complaints and so can persist. The blast radius is one feature, not "every gated feature": `FEATURE_MIN_TIER` (`tier_gate.py:45-47`) contains exactly one entry, `premium_scan`, covering `/scalp`, `/intraday`, and `/swing`. And users with no linked wallet are still denied at `tier_gate.py:228-229`, so it is not literally "every user" — though that precondition is free to satisfy. It is not Low because at the documented launch config it is a 100% bypass, and because a security guardrail that inverts into a permissive one is a defect regardless of what sits behind it.

**Confidence.** High. CONFIRMED. Votes: 2 confirmed / 1 plausible / 0 refuted.

**Location.** `bot/token/tier_gate.py:99-104`

**Code.**
```python
    url = _env("RCLAW_RPC_URL", _DEFAULT_RPC)
    if "mainnet" in url:
        # Draft tooling is devnet-first; refuse mainnet reads to avoid implying
        # a live deployment (see roadmap Guardrails).
        system_log.warning("tier_gate: refusing mainnet RPC %s; treating as unconfigured", url)
        return None
```

**What's wrong.** `_rpc()` returns `None` for any RPC URL containing the lowercase substring `"mainnet"`. That `None` is indistinguishable from a transient network failure, so it propagates through `staked_of` (`:169-170`) and `balance_of` (`:132-133`) into `allows_user`'s fail-open branch, `if bal is None: return True  # fail-open on infra error` (`:233-234`). Crucially, the refusal returns *before any network I/O is attempted*, so unlike the transient RPC hiccup that fail-open was designed for (module docstring, `:6-7`), this `None` is deterministic and permanent for the entire lifetime of a mainnet-configured deployment. `gate_enabled()` (`:94`) checks only `TOKEN_TIER_GATE_ENABLED` and `RCLAW_MINT` and never considers the RPC URL, so the gate reports itself enabled while being structurally incapable of ever evaluating a stake.

The strongest corroboration that this is a defect and not intended design is internal inconsistency: the same guardrail, with the same comment and the same roadmap citation, is implemented twice elsewhere in this repo and both times it fails **closed** by throwing — `token/scripts/lib.mjs:30-35` ("Refusing to run against mainnet. This is draft/devnet tooling…") and `token/presale/genesis_lib.mjs:58-64`. Three implementations of one policy; two fail closed, one fails open.

Three corrections to the original report. It is not "every user": a caller with no linked wallet is denied at `:228-229` — I confirmed this by execution. It is not "elite access": no tier is ever computed, because `tier_for_balance` (`:186-194`) is never reached — `allows_user` short-circuits to `True` at `:234`. And it is not silent: a WARNING fires on every call.

The finder's cross-reference to the substring test is correct and worth keeping. `if "mainnet" in url` is case-sensitive and matches on the whole URL, so it neither reliably catches mainnet nor is a meaningful cluster check. Whether the paywall functions therefore depends on incidental hostname spelling.

**Exploit / reachability.** This has no on-chain component — there is no instruction, no account list, no signer set. I executed the real `bot.token.tier_gate` module with `urllib.request.urlopen` replaced by a raising tripwire to confirm the behavior:

| Scenario | Config | Result |
|---|---|---|
| A | staking mode, `RCLAW_RPC_URL=https://api.mainnet-beta.solana.com`, zero-stake wallet | `staked_of()=None`, `allows_user()=True`, network contacted **False** |
| B | wallet-balance mode (no `RCLAW_STAKING_PROGRAM`), same URL | `balance_of()=None`, `allows_user()=True`, network contacted **False** |
| C | same config, no wallet linked | `allows_user()=False` |
| substring | `https://API.MAINNET-BETA.SOLANA.COM` | refusal **not** triggered (case-sensitive) |
| substring | `https://rpc.ankr.com/solana` | refusal **not** triggered (real mainnet, gate works) |

"Network contacted: False" is the load-bearing result — it proves the refusal short-circuits before any I/O, so the `None` is permanent, not the transient condition the fail-open was written for.

The attacker sequence: `/start` auto-approves the caller as role `trader`, tier `basic` (`user_store.py:198-208`). `/linkwallet 11111111111111111111111111111111` passes the shape-only regex at `telegram_handler.py:8103` and is stored verbatim — this step exists purely to clear the `if not wallet: return False` check at `:228-229`, and per F-01 there is no ownership proof, so any string of the right shape works. `/scalp` then passes `@guard("scan")` (`"scan"` is in `TIER_FEATURES["basic"]`, `user_store.py:51`), reaches `_token_gate_blocks` (`telegram_handler.py:8113`), and `allows_user` returns `True` at `:234`. Every guard on the path was enumerated and each one passes: the allowlist check at `:2029` (self-disabling when unset), the authorized check at `:2036`, the permission check at `:2045`, and the rate limiter at `:2054`, which throttles but never denies.

Preconditions are all operator-set at deploy time — `TOKEN_TIER_GATE_ENABLED=true`, `RCLAW_MINT` set, `RCLAW_RPC_URL` containing `"mainnet"`, and the bot reachable. I graded this CONFIRMED rather than PLAUSIBLE because the uncertainty is not about the mechanism: I executed the real code and observed the exact claimed outcome with no guard blocking any step. The only unverifiable element is whether an operator follows the enablement procedure the project's own documentation describes, which is a question of when rather than whether.

**Remediation.** Never let a configuration refusal enter the transient fail-open path. Distinguish "cannot evaluate because of a hiccup" (fail open, correct) from "cannot evaluate because this deployment is misconfigured" (must not fail open):

```python
class GateMisconfigured(RuntimeError):
    """Permanent, non-recoverable gate configuration error. Never fail-open on this."""

_DEVNET_HOSTS = {"api.devnet.solana.com", "api.testnet.solana.com",
                 "localhost", "127.0.0.1"}

def _rpc(method: str, params: list) -> Optional[dict]:
    url = _env("RCLAW_RPC_URL", _DEFAULT_RPC)
    host = (urllib.parse.urlsplit(url).hostname or "").lower()   # not a substring test
    if host not in _DEVNET_HOSTS:
        raise GateMisconfigured(
            f"tier_gate: refusing non-devnet RPC host {host!r}; draft tooling is "
            "devnet-only (see roadmap Guardrails §10-11)")
```

and in `allows_user`, replacing `:232-234`:

```python
    try:
        bal = staked_of(wallet) if staking_program() else balance_of(wallet)
    except GateMisconfigured:
        system_log.error("tier_gate: enabled but misconfigured; denying premium access")
        return False          # fail CLOSED on permanent misconfiguration
    if bal is None:
        return True           # fail OPEN on a transient infra error (unchanged)
```

The host allowlist replaces the substring test, so `https://rpc.ankr.com/solana` and uppercase spellings no longer slip through in the other direction. Validate this once at startup rather than per call — if `TOKEN_TIER_GATE_ENABLED` is true and the configured RPC host is refused, that is a self-contradictory configuration; log an error at boot or refuse to start, which also removes the per-call WARNING spam.

One adjacent gap found while verifying this path, worth filing separately because it is unconditional and works in every deployment including a correctly functioning devnet gate: `_token_gate_blocks` is called at exactly `telegram_handler.py:8132`, `:8149`, and `:8166` and nowhere else, while the natural-language intent path dispatches the identical premium scan ungated at `telegram_handler.py:1736-1738`. The router regex at `bot/nlp/intent_router.py:303-304` fires on the bare word "scalp", so typing `scalp` instead of `/scalp` bypasses the paywall today. `bot/web/user_gateway.py:325-326` maps the same intents to `scan_market` on the web surface and should be checked for the same gap. Structurally, enforce the gate once inside the `pro_scan` skill dispatch rather than at each caller, so a new entry point cannot silently ship ungated.

*Without this, the moment the team follows its own §14 launch procedure and points `RCLAW_RPC_URL` at mainnet, the tier gate silently grants premium access to every caller with zero staked $RCLAW, and the only signal is a WARNING line nobody is reading.*

---

### [MEDIUM] F-04 -- One plaintext, world-readable keypair holds mint, metadata, metadata-pointer, presale, LP-bucket and unsold-token authority plus the entire 1B supply — no multisig, no file mode, no separation of duties

**Severity.** `AV:L/AC:H/PR:L/UI:N/S:C`. The defect is certain and unguarded — I reproduced the modes empirically (0755 directory, 0644 file under umask 022, Node v22). Scope is Changed because a filesystem read converts directly into on-chain token authority with no second factor. But C/I/A:High is conditional on the key holding value, and today it provably does not: no mainnet mint exists, `metaplex-genesis.config.json` still carries `"mint": "<FILL_FROM ...>"` as a placeholder, and both entry points hard-refuse mainnet RPCs (`token/scripts/lib.mjs:30`, `token/presale/genesis_lib.mjs:58`). Real-money loss on the committed state is zero, which is why this is not High. Two facts keep it from dropping to Low. First, the mainnet guard is a substring denylist (`url.includes('mainnet')`), not a cluster-identity check — I verified no `getGenesisHash()` comparison exists anywhere in `token/`, and ordinary private mainnet endpoints (Triton `*.rpcpool.com/<token>`, Helius `rpc.helius.xyz/?api-key=`) do not contain the literal substring, so the "can never touch mainnet" claim at `token/README.md:73-75` is weaker than advertised and the key can acquire real value without anyone lifting the roadmap gate. Second, `token/README.md:23` and `:35` document `npm run keygen` as *the* way to produce the launch key, so the same 0644 file is on the mainnet path by default. The fix is one `{ mode: 0o600 }` argument, so there is no engineering reason to defer it behind the mainnet gate.

**Confidence.** High. CONFIRMED. Votes: 2 confirmed / 1 plausible / 0 refuted.

**Location.** `token/scripts/keygen.mjs:9-19`

**Code.**
```javascript
const dir = path.join(ROOT, '.keys');
fs.mkdirSync(dir, { recursive: true });
const outPath = path.join(dir, 'mint-payer.json');

if (fs.existsSync(outPath)) {
  console.log(`Keypair already exists at ${outPath} — leaving it in place.`);
} else {
  const kp = Keypair.generate();
  fs.writeFileSync(outPath, JSON.stringify(Array.from(kp.secretKey)));
```

**What's wrong.** Neither `mkdirSync` (`:10`) nor `writeFileSync` (`:17`) specifies a `mode`, so under umask 022 this produces a 0755 directory containing a 0644 plaintext Ed25519 secret key, with no passphrase. `solana-keygen new` opens with `.mode(0o600)` for exactly this reason, and this repo already knows the idiom — `bot/core/secrets_vault.py:142` does `os.chmod(str(tmp), 0o600)` on its secrets vault, so the token tooling is inconsistent with its own codebase rather than making a deliberate devnet trade-off. The idempotent branch at `:13-14` only `console.log`s; it never repairs an existing file's mode, so patching only the write path would leave every already-generated key at 0644 forever.

That one key is then loaded by `token/scripts/lib.mjs:39-47` and `token/presale/genesis_lib.mjs:67-83` and becomes simultaneously: mint authority (`create_token.mjs:85`), freeze authority when the flag is off (`:64`), metadata `updateAuthority` (`:93`), MetadataPointer authority (`:77`, arg 2 of `createInitializeMetadataPointerInstruction`), holder of the full minted supply (`:107-111`), Genesis presale authority and payer (`genesis_presale.mjs:122-131`), Raydium LP bucket authority (`:244-251`), the unsold-token recipient (`:296`), and the depositor identity (`:207`). All ten references verified. There is no multisig, no threshold, no confirmation prompt, and no `simulateTransaction` before any privileged send.

Four claims in the original write-up are wrong and should be dropped. There is no committed `.env` — `git ls-files token/` lists only `.env.example`, and `token/.gitignore:4` ignores `.env`; separately, `process.env` taking precedence over the `.env` file is standard 12-factor ordering, not a vulnerability, since setting env vars already implies code execution as that user. The CI-runner scenario does not exist in this repo: `.github/workflows/ci.yml` is the only workflow and is Python-only, and nothing in `.github/`, `Makefile`, `deploy.sh`, or `docker-compose.yml` references `keygen`, `.keys`, or `token/scripts`. The missing `secret.length === 64` check is input validation, not a security control — both `Keypair.fromSecretKey` and `umi.eddsa.createKeypairFromSecretKey` already validate it. And under the shipped config (`token/config/token.config.json:15`, `"setFreezeAuthorityToNull": true`) the freeze authority is `null`, so that sub-claim is contingent on flipping a committed flag.

Two amplifiers the original missed. First, **Docker bakes the key into an image**: the root `Dockerfile` does `COPY . .` and `.dockerignore` never excludes `.keys/`; Docker ignore patterns are root-anchored, so even the existing `.env`/`.env.*` entries do not match `token/.env`. `docker-compose.yml:11-12` and `:45-47` build `context: .` into `runeclaw:latest`, and the `RUN useradd -m -u 1001 runeclaw && chown -R runeclaw:runeclaw /app` step changes ownership but not permissions. Any operator who runs keygen and then builds ships the plaintext secret in an image layer, readable by every process in a container that also exposes uvicorn on `0.0.0.0:8000`. This directly contradicts the original's "the exposure is filesystem-local." Second, the repo's own gate cannot detect the residue: `create_token.mjs:121` revokes only `AuthorityType.MintTokens`, and `verify_token.mjs:26-41` never asserts the metadata `updateAuthority` or the metadata-pointer authority, so `npm run verify` prints "ALL CHECKS PASSED ✓" while a thief retains permanent control of the token's name, symbol, URI, and metadata pointer.

**Exploit / reachability.** Confirmed reachable. No Anchor program is on this path — these are off-chain Node scripts and the only authorization primitive in play is possession of the Ed25519 secret, so account-substitution, seeds, `has_one`, and `Signer<'info>` analysis do not apply.

Preconditions: the operator runs `npm run keygen` in `token/` (per `token/README.md:23`) on a host where a second uid can read the repo path — another user account, a service account, a container process, an npm lifecycle script under a different uid, or a backup/indexing daemon — with default umask 022. For real-money impact, the key must subsequently hold value, either because the mainnet gate is lifted or because `RPC_URL` points at a private mainnet endpoint whose URL lacks the literal substring `"mainnet"`.

Step 0 (operator, benign): `cd token && npm run keygen` produces `.keys/` at 0755 and `mint-payer.json` at 0644. Empirically reproduced under Node v22.22.2 with umask 022. Step 1 (attacker, different uid): `cat /path/RUNECLAW/token/.keys/mint-payer.json`, granted by `o+r` on the file and `o+x`/`o+r` on the directory. Step 2: reconstruct the identity using the exact two lines already present at `lib.mjs:45-46` — `Uint8Array.from(JSON.parse(fs.readFileSync(abs, 'utf8')))` then `Keypair.fromSecretKey(secret)`. This is the identity, not a revocable capability; there is no rotation and no second factor. Step 3 (only if before `create_token.mjs:118-123`): Token-2022 `MintTo` with `authority` = the stolen key, inflating supply past the "fixed 1B" invariant. Step 4 (any time, **no race needed**): `TransferChecked` with `source` = the payer ATA holding the entire minted supply and `owner` = the stolen key — revoking `AuthorityType.MintTokens` does not protect tokens already sitting in an ATA the stolen key owns, so this works indefinitely and is the durable capability the original write-up understated by framing step 3 as the mechanism. Step 5 (permanent): `TokenMetadataUpdateField` and `UpdateMetadataPointer`, neither of which is ever revoked (see F-06). Step 6: on the presale side the same key is `authority: umi.identity` on `initializeV2`, `addPresaleBucketV2` and `addRaydiumCpmmBucketV2`, and `recipient: me` on `withdrawUnsoldPresaleV1`, so a thief can stand up a competing genesis account against the same base mint and sweep unsold tokens.

Three candidate guards were evaluated and none blocks the sequence. The mainnet refusals at `lib.mjs:30-35` and `genesis_lib.mjs:58-63` do not constrain the attacker at all — a thief holding the raw 64 bytes never runs this repo's code; they sign with their own script against any cluster, so the guard only limits what value the key accumulates, never who can spend it. `token/.gitignore:2` (`.keys/`) is verified effective — `git check-ignore -v token/.keys/mint-payer.json` returns `token/.gitignore:2:.keys/`, and `git log --all --diff-filter=A` finds no key blob in any branch's history — so repository exposure is fully closed, but that is not the finding. And `verify_token.mjs:26-41` is worse than neutral, as described above.

One nuance that changes the remediation: `chmod 0600` only defends against *other uids*. It does nothing against a malicious `postinstall` among this package's dependencies (including large Metaplex and Wormhole SDK trees), which runs as the operator and reads the key regardless of mode. The mode fix is correct and cheap but is not the control that matters for a key holding real value.

**Remediation.** Fix the modes, and repair on the idempotent path:

```javascript
const dir = path.join(ROOT, '.keys');
fs.mkdirSync(dir, { recursive: true, mode: 0o700 });
fs.chmodSync(dir, 0o700);                       // mkdirSync mode is umask-masked
const outPath = path.join(dir, 'mint-payer.json');

if (fs.existsSync(outPath)) {
  fs.chmodSync(outPath, 0o600);                 // repair, don't just log
  console.log(`Keypair already exists at ${outPath} — left in place, mode repaired to 0600.`);
} else {
  const kp = Keypair.generate();
  fs.writeFileSync(outPath, JSON.stringify(Array.from(kp.secretKey)), { mode: 0o600 });
```

Then refuse to load a loose key at both call sites (`lib.mjs:39-47`, `genesis_lib.mjs:67-75`), which also turns the unconfined `KEYPAIR_PATH` into a fail-closed input:

```javascript
const st = fs.statSync(abs);
if (process.platform !== 'win32' && (st.mode & 0o077)) {
  throw new Error(`Keypair ${abs} is group/world-readable (mode ${(st.mode & 0o777).toString(8)}). chmod 600 it.`);
}
```

Replace the substring denylist at `lib.mjs:30` and `genesis_lib.mjs:58` with a real cluster check: compare `await connection.getGenesisHash()` against the mainnet genesis hash `5eykt4UsFv8P8NJdTREpY1vzqKqZKvdpKuc147dw2N9d` and refuse on match, so a private mainnet RPC cannot slip past on URL text alone. Add `**/.keys/` and `**/.env` to `.dockerignore` — the existing root-anchored `.env`/`.env.*` entries do not match `token/.env`. Close the verify gap by adding `revokeMetadataUpdateAuthorityAfterLaunch` and `revokeMetadataPointerAuthorityAfterLaunch` alongside `token.config.json:13-16`, revoking both in `create_token.mjs` after `:124`, and asserting both in `verify_token.mjs:26-41`.

Finally, and this is the part a `chmod` does not fix: before any mainnet run the launch key must not be a single hot Ed25519 file holding every role. Put mint, freeze, metadata-update, metadata-pointer, and the Genesis presale authority behind a Squads or SPL multisig; keep the supply-holding ATA under a key distinct from the authority keys; require an explicit `simulateTransaction` plus operator confirmation before each privileged send in `create_token.mjs` and `genesis_presale.mjs`; and add a hard guard so a key generated by `keygen.mjs` can never be used against a non-devnet cluster, given that `README.md:23` and `:35` currently document it as the way to produce the launch key.

*Without this, any second uid on the build host — or anyone who receives an image built after keygen — silently becomes the mint, metadata, presale and LP authority for $RCLAW, and the repo's own verifier will still report the token as sound.*

---

### [MEDIUM] F-05 -- verify_token.mjs's mint- and freeze-authority checks are tautologies over the same config that drove creation — one flag flip disables the safety property and its detection together

**Severity.** The code defect is certain and the downstream on-chain impact is unbounded — permanent freeze of every staked balance, or unlimited mint — but the actor who can trigger it is the mint-payer key holder, that is, the project itself or whoever compromises that key. There is no anonymous-attacker path. What is broken is an *assurance control*: the script whose stated purpose (`token/scripts/verify_token.mjs:2`) is to assert "mint authority == null, freeze authority == null" is structurally incapable of failing in exactly the case it exists to catch, and it is named as a published post-sale gate at `token/presale/RUNBOOK.md:103`. That is a real integrity defect with a silent-green failure mode, but insider-triggered and currently devnet/draft-scoped, which caps it at Medium rather than High. The shipped config (`token/config/token.config.json:14-15`, both `true`) makes the checks sound today — the tautology only manifests after a config change, which is the main reason this is not High. It becomes the High-severity leg of any mainnet launch gate, because it is the artifact third parties would be pointed at to prove the token is non-freezable and fixed-supply.

**Confidence.** High. CONFIRMED. Votes: 1 sanity check.

**Location.** `token/scripts/verify_token.mjs:29-38`

**Code.**
```javascript
  [
    'mint authority revoked',
    onchain.mintAuthority === null || !cfg.authorities.revokeMintAuthorityAfterMint,
    onchain.mintAuthority ? onchain.mintAuthority.toBase58() : 'null',
  ],
  [
    'freeze authority null',
    onchain.freezeAuthority === null || !cfg.authorities.setFreezeAuthorityToNull,
    onchain.freezeAuthority ? onchain.freezeAuthority.toBase58() : 'null',
  ],
```

**What's wrong.** Both predicates have the form `<on-chain state is safe> || <the config didn't ask for it>`, and the flag is read from the very file loaded at `:9` — `loadConfig()` (`token/scripts/lib.mjs:10-13`) reads only `token/config/token.config.json`, with no independent expected-value file, no signature over the config, and no second source. When `cfg.authorities.setFreezeAuthorityToNull === false`, the right disjunct is `true` and the predicate is `true` for every possible on-chain state. `create_token.mjs` gates the actual behaviour on the same flags — `:64` sets `freezeAuthority = cfg.authorities.setFreezeAuthorityToNull ? null : payer.publicKey`, and `:118` gates the `AuthorityType.MintTokens → null` SetAuthority at `:121`. One flag flip therefore both leaves the dangerous authority live on-chain and flips the verifier's disjunct to `true`.

`:44-45` then prints `✓ mint authority revoked: 7xKX...` — a green check immediately followed by the address proving it is not revoked, because the label and the detail string are computed from different things — and `:49-50` exits 0, which is what any CI or runbook step keys off. The two adjacent checks in the same array (`decimals` at `:27`, `supply` at `:28`) compare on-chain state against config with no escape disjunct, so this is an oversight rather than a deliberate "only check what was requested" convention.

The correction to the original framing matters for how this is read: this is a broken assurance control, not an externally exploitable vulnerability. Exercising the surviving authority requires the mint-payer key (`lib.mjs:39-47`), so the editor of the config and the exploiter must be the same principal. Two further nuances the original omits: with the shipped config both flags are `true`, so exposure requires a config change rather than existing today; and `create_token.mjs` *does* report the unsafe state honestly at creation time (`:126` prints `[3/3] SKIPPED mint-authority revoke (config flag off).`, and `:147-148` print `(NOT revoked!)` / `(present!)`). The gap is specifically at re-verification — which is the whole point of a separate verify script and exactly what `RUNBOOK.md:103` designates as the post-sale proof step.

**Exploit / reachability.** The defect is pure control-flow logic and needs no on-chain step to demonstrate. Someone edits `token.config.json:15` to `"setFreezeAuthorityToNull": false`, plausibly framed as "keep the option open for compliance freezes." `create_token.mjs:64` then sets `freezeAuthority = payer.publicKey` and feeds it to `createInitializeMintInstruction` at `:82-88`. `npm run verify` prints `✓ freeze authority null: <payer address>`, then `ALL CHECKS PASSED ✓`, exit 0.

Users stake into `rclaw_staking`; tokens land in the vault ATA declared at `programs/rclaw_staking/src/lib.rs:197-205` as an ATA of the `["vault", mint]` PDA. The freeze-authority holder signs a Token-2022 `FreezeAccount` on that vault ATA. Both instructions move tokens via `token_interface::transfer_checked` (`lib.rs:146-159`), so every transfer now fails inside the token program and both `stake` and `unstake` revert. I enumerated the program's full instruction surface: `stake` (`lib.rs:90`) and `unstake` (`lib.rs:137`) are the only two `pub fn`s in the 338-line file. There is no admin, no pause, no emergency-withdraw, and no authority field on `StakeAccount`. `sa.amount` still records the debt (`lib.rs:161-162` only decrements on a successful transfer), so stake records permanently claim tokens that can never be moved, and `bot/token/tier_gate.py` keeps reading the stale amount. Recovery would require a program upgrade. The identical pattern with `revokeMintAuthorityAfterMint: false` yields an infinite-supply mint that the verifier certifies as revoked.

Nothing blocks the verifier defect. Three partial mitigations exist and none close it. `lib.mjs:30-35` confines the tooling to non-mainnet clusters, which is the main reason severity stays at Medium — but it is a hostname substring heuristic (mainnet endpoints without the literal string, such as Triton `*.rpcpool.com` or private Helius/Alchemy/QuickNode URLs, sail past), and it is not an adversarial barrier here at all, since the same actor who flips the config flag chooses the RPC URL. The honest creation-time output at `create_token.mjs:126` and `:147-148` exists in only that one console session. And the shipped `true` defaults bound present-day exposure but are not a guard — they are the exact values the change flips, and nothing pins, signs, or independently attests them. `.github/workflows/ci.yml` is Python-only and never runs `npm run verify`, so the only consumer is a human ticking a box against green output.

On the Anchor side there is nothing to blame: `associated_token::mint`/`authority`/`token_program` (`lib.rs:201-203`, `:241-243`), `seeds` + `bump` on the vault authority (`lib.rs:193`, `:238`), and `has_one = owner` / `has_one = mint` with `bump = stake_account.bump` on unstake (`lib.rs:230-234`) are all correct and prevent account substitution. Freeze is not an account-substitution bug — it is the token program correctly enforcing an authority the mint was deliberately configured to retain. No Anchor constraint can defend against it; only not having the authority can.

**Remediation.** Make the safety predicates absolute. The invariant "no mint authority, no freeze authority" is the token's security property and should never be waivable by the same file that configures minting:

```diff
-  [
-    'mint authority revoked',
-    onchain.mintAuthority === null || !cfg.authorities.revokeMintAuthorityAfterMint,
-    onchain.mintAuthority ? onchain.mintAuthority.toBase58() : 'null',
-  ],
-  [
-    'freeze authority null',
-    onchain.freezeAuthority === null || !cfg.authorities.setFreezeAuthorityToNull,
-    onchain.freezeAuthority ? onchain.freezeAuthority.toBase58() : 'null',
-  ],
+  // Absolute invariants: a live mint or freeze authority is a FAIL regardless of
+  // config. The config must never be able to waive the property it configures.
+  ['mint authority revoked', onchain.mintAuthority === null,
+    onchain.mintAuthority ? onchain.mintAuthority.toBase58() : 'null'],
+  ['freeze authority null', onchain.freezeAuthority === null,
+    onchain.freezeAuthority ? onchain.freezeAuthority.toBase58() : 'null'],
```

Then fail loudly on an unsafe config so the two files cannot drift silently — near `:9`, after `loadConfig()`:

```javascript
if (!cfg.authorities.revokeMintAuthorityAfterMint || !cfg.authorities.setFreezeAuthorityToNull) {
  console.error('UNSAFE CONFIG: authorities.* must both be true. Refusing to verify.');
  process.exit(1);
}
```

That converts a config flip from "silent green" into "cannot pass," which is the property the RUNBOOK gate needs. Beyond that: verify against a committed expected-value artifact rather than the live config, recording the intended mint address, decimals, supply and `mintAuthority: null` / `freezeAuthority: null` in a separate checked-in file, since today `loadConfig()` is the single source for both create and verify and verify can only ever confirm that create did what create was told. Fix the misleading detail string so a failing check renders `PRESENT: <addr>` rather than an address beside a "revoked/null" label. Add a Node job to `.github/workflows/ci.yml` — there is none — that at minimum runs the config-sanity assertion without network access, so a PR flipping either flag fails the build rather than relying on a human ticking `RUNBOOK.md:103`. As defence in depth for the roadmap's audit gate, consider an Anchor `constraint =` on the `Mint` account asserting `mint.freeze_authority.is_none()` at stake time; that is not the fix for F-05 — the correct primary fix is that the mint never retains a freeze authority — but it would keep a misconfigured mint from stranding user funds.

*Without this, the one artifact the project points at to prove $RCLAW is non-freezable and fixed-supply will print "ALL CHECKS PASSED ✓" over a mint that is neither, and every staked balance can be permanently frozen with no recovery path in the program.*

---

### [MEDIUM] F-06 -- Metadata updateAuthority and MetadataPointer authority are granted to the payer at mint creation, never revoked by any code, and never checked by the verifier

**Severity.** Impact is identity and reputation, not custody. The two authorities that move value — `MintTokens` and `Freeze` — *are* handled (`create_token.mjs:118-124` revokes `MintTokens`; `:64` nulls freeze when the flag is set) and *are* asserted by the verifier (`verify_token.mjs:29-38`). What survives is the Token-2022 `TokenMetadata` `updateAuthority` and the `MetadataPointer` authority, both held by one hot Ed25519 key. Their maximum reach is rewriting name/symbol/uri, adding or removing `additionalMetadata` keys, or repointing the mint's metadata at an attacker-controlled account. Roughly `AV:N/AC:L/PR:H/UI:R/S:C/C:N/I:H/A:N` — integrity of the token's displayed identity is fully compromised; confidentiality and availability are untouched. I deliberately did not grade this High: I looked for escalation and found none. Token-2022's `UpdateField` handler only reallocs the mint's TLV region to fit a longer value, with rent paid by the updater; it has no write path to the base `Mint` struct's `supply`, `mint_authority`, or `freeze_authority` fields, so a retained metadata authority cannot be parlayed into minting or freezing. Mutable metadata is also the single most common state of live Solana mints and is what rug-checkers surface as a warning rather than a critical. I equally did not grade it Low: on mainnet this is a working phishing and impersonation primitive against every wallet, explorer, and aggregator that renders the mint; it is irreversible, because the authority is never renounced so nobody can pin the identity; and the repo's own verifier actively certifies the mint as sound while the hole is open. Holding it at Medium rather than higher: the tooling is devnet-gated (`lib.mjs:30-35`) and the roadmap already names this exact item as an open pre-launch gate (`docs/TOKEN_ROADMAP.md:408`), so it is a documented gap rather than a silent one.

**Confidence.** High. CONFIRMED. Votes: 1 sanity check.

**Location.** `token/scripts/create_token.mjs:75-99`

**Code.**
```javascript
const pointerIx = createInitializeMetadataPointerInstruction(
  mint.publicKey,
  payer.publicKey,
  mint.publicKey,
  TOKEN_2022_PROGRAM_ID
);
...
const initMetaIx = createInitializeMetadataInstruction({
  programId: TOKEN_2022_PROGRAM_ID,
  metadata: mint.publicKey,
  updateAuthority: payer.publicKey,
```

**What's wrong.** `create_token.mjs` establishes four authorities on the payer: MetadataPointer (`:77`, the second positional argument to `createInitializeMetadataPointerInstruction` is the authority), mint (`:85`), freeze (`:64`/`:86`), and metadata `updateAuthority` (`:93`). Only the first of these is ever revoked. A repo-wide grep for `SetAuthority`/`AuthorityType`/`updateAuthority`/`MetadataPointer`, excluding lockfiles and `node_modules`, returns eight hits, all in `create_token.mjs`, and the only revoke is `:121` — `createSetAuthorityInstruction(mint.publicKey, payer.publicKey, AuthorityType.MintTokens, null, [], TOKEN_2022_PROGRAM_ID)`. There is no `AuthorityType.MetadataPointer` revoke, no metadata `UpdateAuthority → null`, and no flag or CLI path that would issue one.

`token/config/token.config.json:13-16` exposes revocation flags for only two of the four, so the `authorities` block — the object a reviewer reads to answer "what powers survive launch?" — is an incomplete policy presenting as a complete one. `verify_token.mjs:26-41` then certifies the mint by checking decimals, supply, mint authority, freeze authority, metadata name, and metadata symbol. It never reads `meta.updateAuthority` (which `getTokenMetadata` does return), never fetches the MetadataPointer extension, never asserts `meta.uri === cfg.metadataUri`, and never enumerates the mint's extension list. So the documented flow ends with `ALL CHECKS PASSED ✓` (`:49`, exit 0) on a mint whose name, symbol, and URI remain unilaterally rewritable by one hot key.

Framing correction: this is a retained-centralization and rug-resistance finding with a false-assurance component, not an unauthorized-access or account-substitution bug. There is no Anchor program on this path — these are client-side scripts driving stock SPL Token-2022, whose checks for `TokenMetadataUpdateField` and `MetadataPointer::Update` are single `authority == signer` comparisons that the script's own payer satisfies by construction. The actor is the key holder: the deployer, or anyone who reads `token/.keys/mint-payer.json`, which `keygen.mjs:17` writes at 0644 (F-04).

**Exploit / reachability.** Run the documented flow — `npm run keygen && npm run create && npm run verify` — and verify exits 0 with `ALL CHECKS PASSED ✓`. Post-listing, the key holder submits a Token-2022 `TokenMetadataUpdateField` (spl-token-metadata's `createUpdateFieldInstruction`), accounts `[metadata: mint (writable), updateAuthority: payer (signer)]`, setting `uri` to attacker-hosted JSON, then repeating for `name` and `symbol` to impersonate a higher-value asset. Token-2022 checks only that `update_authority == signer`. If the new value is longer than the old, the handler reallocs the mint's TLV and requires post-realloc rent exemption — the attacker simply prepends a `SystemProgram.transfer` of a few thousand lamports; not a blocker. Alternatively, `MetadataPointerInstruction::Update` with accounts `[mint (writable), metadataPointerAuthority: payer (signer)]` repoints the mint at an entirely attacker-owned metadata account, under the same single signer check.

The resulting state delta is that the mint's `TokenMetadata` name/symbol/uri, or the pointer target, now reads attacker-chosen values, while `supply`, `mint_authority = None`, and `freeze_authority = None` are all unchanged. Every wallet, explorer, and aggregator renders the new identity. No holder action is required. Re-running `npm run verify` still exits 0 — for the pointer-redirect case because the on-mint name and symbol are untouched, and for a uri-only change because `uri` is never asserted.

Nothing on the path blocks the retention. The only guards in the tree are `lib.mjs:30-35` (`if (url.includes('mainnet')) throw ...`), a substring check on an operator-supplied RPC URL that does not stop non-`"mainnet"`-named mainnet endpoints and does not constrain the authority at all, and `create_token.mjs:118-124`, which revokes only `AuthorityType.MintTokens`. Neither touches the metadata `updateAuthority` or the MetadataPointer authority. Partially offsetting, as documentation rather than control: `docs/TOKEN_ROADMAP.md:408` is an explicitly unchecked pre-launch gate — "Metadata immutability or multisig-gated update authority, renounced post-launch" — and `:90` specifies the update authority should sit behind a Squads multisig, then be time-locked or renounced. So the intended design is documented and the code simply has not implemented it, which is why this is a known gap rather than a silent one.

**Remediation.** Renounce both surviving authorities in `create_token.mjs`, gated on new config flags, in the same run that revokes `MintTokens` (after `:124`):

```javascript
// Renounce the Token-2022 metadata update authority (name/symbol/uri become immutable).
if (cfg.authorities.revokeMetadataUpdateAuthority) {
  const { createUpdateAuthorityInstruction } = await import('@solana/spl-token-metadata');
  await sendAndConfirmTransaction(connection, new Transaction().add(
    createUpdateAuthorityInstruction({
      programId: TOKEN_2022_PROGRAM_ID,
      metadata: mint.publicKey,
      oldAuthority: payer.publicKey,
      newAuthority: null,               // PublicKey.default on the wire == renounced
    })
  ), [payer]);
}
// Renounce the MetadataPointer authority (the metadata source can never be redirected).
if (cfg.authorities.revokeMetadataPointerAuthority) {
  await sendAndConfirmTransaction(connection, new Transaction().add(
    createSetAuthorityInstruction(
      mint.publicKey, payer.publicKey,
      AuthorityType.MetadataPointer, null, [], TOKEN_2022_PROGRAM_ID
    )
  ), [payer]);
}
```

Order matters: do both revokes *after* the final metadata write. Prefer renouncing outright over transferring — if the roadmap's Squads-multisig step (`docs/TOKEN_ROADMAP.md:90`) is wanted first, pass the multisig pubkey instead of `null` and leave the null-renounce as the post-launch step, but do not leave the single hot key in place either way.

Close the verifier blind spot at `verify_token.mjs:26-41` so the gap can never again pass silently:

```javascript
['metadata uri', meta ? meta.uri === cfg.metadataUri : false, meta ? meta.uri : '(none)'],
['metadata update authority renounced',
  !cfg.authorities.revokeMetadataUpdateAuthority ||
    !meta?.updateAuthority || meta.updateAuthority.equals(PublicKey.default),
  meta?.updateAuthority ? meta.updateAuthority.toBase58() : 'null'],
['metadata pointer authority renounced',
  !cfg.authorities.revokeMetadataPointerAuthority ||
    !getMetadataPointerState(onchain)?.authority,
  String(getMetadataPointerState(onchain)?.authority ?? 'null')],
['metadata pointer target == mint',
  getMetadataPointerState(onchain)?.metadataAddress?.equals(mint) ?? false, ''],
```

`getMetadataPointerState` is exported by `@solana/spl-token`; note that `getTokenMetadata` surfaces a renounced authority as `PublicKey.default` or `null` depending on version, so assert on both. Also consider asserting the mint's extension set contains exactly `[MetadataPointer, TokenMetadata]`, so an unexpected extension cannot slip past. Note that per F-05 these new predicates should not be written in the `X || !flag` shape — make them absolute, or at minimum add the config-sanity assertion described there. Finally, complete the policy object at `token/config/token.config.json:13-16` by adding `"revokeMetadataUpdateAuthority": true` and `"revokeMetadataPointerAuthority": true`, so `authorities` enumerates all four authorities the script creates rather than two.

*Without this, $RCLAW ships with a permanently mutable on-chain identity — one key can rename it, re-symbol it, and repoint its metadata URI at any moment after listing, with no way for holders to pin it and no signal from the repo's own verification step.*

---

### [Medium] F-08 -- stake() credits the requested amount, not the amount the vault actually received — a Token-2022 transfer-fee mint breaks vault solvency and yields a free stake record

**Severity.** Attack vector is network-reachable and permissionless (anyone can call `stake`), attack complexity is low, no privileges are required, and no user interaction is needed beyond the victims' own ordinary staking. Confidentiality impact is none; integrity and availability impact is real and permanent for a subset of users' principal. The reason this is Medium and not High is bounded blast radius plus an unreachable path under the documented mainnet procedure. It cannot touch a real-$RCLAW vault or any other mint's vault: the vault authority is derived per-mint (`programs/rclaw_staking/src/lib.rs:194` and `:236`) and stake records carry `has_one = mint` (`:231`), so damage is confined to holders of the fee-bearing mint itself. And the canonical mint can never carry a fee — `token/scripts/create_token.mjs:60` sizes the mint with `getMintLen([ExtensionType.MetadataPointer])` and nothing else, and Token-2022 extensions must be initialized before `InitializeMint`, so `TransferFeeConfig` cannot be retrofitted onto the mint that `docs/TOKEN_ROADMAP.md:521` tells you to pin. It is not Low, because the loss is created by this program's accounting rather than by the mint: a vault that credited the observed delta would remain solvent under a fee mint. Deployment-gate note: in the default build the pin is unset (`option_env!` yields `None` with no env var), so this is live on any cluster the program is deployed to, and this program is the tier gate for a live trading bot with a stated mainnet roadmap.

**Confidence.** High. CONFIRMED. Votes: 1 sanity check.

**Location.** `programs/rclaw_staking/src/lib.rs:113-120`

**Code.**
```rust
            amount,
            ctx.accounts.mint.decimals,
        )?;

        let sa = &mut ctx.accounts.stake_account;
        sa.owner = ctx.accounts.owner.key();
        sa.mint = ctx.accounts.mint.key();
        sa.amount = sa.amount.checked_add(amount).ok_or(StakeError::Overflow)?;
```

**What's wrong.** `stake()` credits the *requested* amount rather than the amount the vault actually received. The escrow CPI at `lib.rs:103-115` is `transfer_checked` against `Interface<'info, TokenInterface>` (`lib.rs:214`), which accepts spl-token-2022 by design. On a mint carrying `TransferFeeConfig`, `transfer_checked` debits the full `amount` from the source, credits `amount - fee` to the destination's `TokenAccount::amount`, and parks the fee in the destination's `withheld_amount` extension field, which is not part of `TokenAccount::amount`. Line 120 nevertheless adds the full `amount` to the record. The shortfall is undetectable to the program: `.amount` appears in this file only on `stake_account` (lines 98, 120, 127, 138, 162, 167) — `vault.amount` is never read before or after the CPI — and `reload()` appears nowhere in the file. This is a missing-read bug, not a stale-read bug; the canonical snapshot / CPI / `reload()` / credit-the-delta pattern is simply absent. `unstake()` compounds the drift in the same direction: `lib.rs:146-159` debits the vault the full `amount` while `lib.rs:162` decrements the record by the same `amount`, so the vault falls by the gross on the way out but only ever rose by the net on the way in.

Two corrections to how this was originally written up. First, the headline "zero-cost stake inflation / free stake record" is not an exploit. Every step of it is true, but it grants the attacker nothing they did not already have: with the pin unset the program accepts any mint, so an attacker can create a plain zero-fee mint, mint themselves N, stake it, obtain an identical `StakeAccount` with `amount = N` at identical zero cost, and additionally keep the ability to unstake those tokens. Burning them to a 100% fee is strictly worse for the attacker. The record is also worthless to the only consumer — `bot/token/tier_gate.py:161-164` appends `{"memcmp": {"offset": 40, "bytes": mint}}` whenever `RCLAW_MINT` is configured, so a record for a foreign mint gates nothing — and the program header at `lib.rs:56-59` together with `programs/rclaw_staking/README.md:28-30` already documents "a user can stake a worthless token" as an accepted consequence of leaving the pin unset. Second, the real finding is the one the original filed as a second-order note: vault insolvency with permanently locked principal. That is what this section reports.

**Exploit / reachability.** The loss sequence, with nothing on the path blocking it:

1. An attacker (or simply a careless listing) creates a Token-2022 mint M with `TransferFeeConfig` at, say, 100 bps, naming themselves `withdrawWithheldAuthority`. No RUNECLAW instruction is involved. `check_pinned_mint` at `lib.rs:92` returns `Ok` via the `let Some(expected) = pinned else { return Ok(()) };` early exit at `lib.rs:68` because `PINNED_MINT` is `None` in the default build.
2. Victims call `stake(1_000)` with `owner` = victim (`Signer`), `mint` = M (`InterfaceAccount<Mint>`, unconstrained beyond deserializing as a mint), `stake_account` = PDA `["stake", victim, M]`, `vault_authority` = PDA `["vault", M]`, `vault` = the ATA created by `init_if_needed` at `lib.rs:198-205`, `user_token_account` = the victim's ATA, and `token_program` = spl-token-2022.
3. The CPI at `lib.rs:103-115` is a plain `TransferChecked`. Token-2022 applies the fee on plain `transfer_checked` — only `TransferCheckedWithFee` additionally *asserts* the expected fee — so the source is debited 1,000, the vault's `TokenAccount::amount` rises by 990, and 10 lands in the vault's `withheld_amount`. Line 120 credits 1,000.
4. After N victims stake `a` each, the vault holds `N*a*(1-f)` against records totalling `N*a`. Roughly the first `N*(1-f)` users unstake whole; the remainder's `transfer_checked` CPI at `lib.rs:146-159` aborts with insufficient funds and their principal is unrecoverable. The program exposes exactly two instructions, `stake` at `lib.rs:90` and `unstake` at `lib.rs:137` — there is no admin, sweep, or top-up handler and no upgrade path in-program to add one.
5. Separately, M's `withdrawWithheldAuthority` harvests the accumulated `withheld_amount` off the vault ATA via `HarvestWithheldTokensToMint` / `WithdrawWithheldTokensFromAccounts`. That is permissionless for that mint's authority and the staking program is never consulted, so the skim on both legs comes out of victims' balances.

I enumerated every guard on the stake path and none of them constrains the mint's extension set or observes the vault balance: `require!(amount > 0, ...)` at `lib.rs:91`; `check_pinned_mint` at `:92`; the identity-only re-stake asserts at `:98-101`; `owner: Signer<'info>` at `:176`; `InterfaceAccount<Mint>` at `:180`, which validates that the account deserializes as a mint owned by a token program but places no restriction on extensions; the seeds/bump on `stake_account` at `:183-189`; `seeds = [b"vault", mint.key().as_ref()]` at `:194`; the `associated_token::mint` / `::authority` / `::token_program` triple on the vault at `:198-204`; the owner and mint constraints on `user_token_account` at `:209-210`; and `Interface<'info, TokenInterface>` at `:214`, which accepts spl-token-2022 by design. There is also no compensating test — `programs/rclaw_staking/tests/attack.rs` contains no case matching fee, extension, permanent, or solvency.

**Remediation.** Two independent fixes; apply both.

First, credit the observed delta instead of the requested amount:

```rust
let before = ctx.accounts.vault.amount;
token_interface::transfer_checked( /* unchanged, lib.rs:103-115 */ )?;
ctx.accounts.vault.reload()?;
let credited = ctx.accounts.vault.amount
    .checked_sub(before)
    .ok_or(StakeError::Overflow)?;
require!(credited > 0, StakeError::ZeroAmount);

let sa = &mut ctx.accounts.stake_account;
sa.owner  = ctx.accounts.owner.key();
sa.mint   = ctx.accounts.mint.key();
sa.amount = sa.amount.checked_add(credited).ok_or(StakeError::Overflow)?;
```

Emit `credited` rather than `amount` in the `Staked` event at `lib.rs:123-128` so the event, the record, and the offset-72 read at `bot/token/tier_gate.py:179` all agree. The symmetric hazard on the unstake leg (the user receives `amount - fee` while the record drops by the full `amount`) is safe for vault solvency — it over-debits the user, never the vault — so once this fix is in place the vault cannot go negative. If you want the user made whole instead, compute the gross via the mint's `TransferFeeConfig::calculate_inverse_fee`, but do not do that without this fix or you reintroduce the deficit.

Second, reject mints whose extension set can break the vault's invariants, which also closes the adjacent holes described below. Add a helper called from `stake()` alongside `check_pinned_mint` at `lib.rs:92` that unpacks the mint's extension TLV via `StateWithExtensions::<spl_token_2022::state::Mint>::unpack` and returns an error for `TransferFeeConfig`, `PermanentDelegate`, and `TransferHook`, with a new `#[msg("Mint carries an extension incompatible with the vault")] UnsupportedMintExtension` variant added to the `StakeError` enum at `lib.rs:287-303`. Allowlisting the permitted extensions rather than denying known-bad ones is stricter and preferable if you can enumerate them. Two adjacent cases share this root cause and are currently unguarded: a `PermanentDelegate` mint lets its delegate move tokens straight out of the vault ATA, and a mint retaining freeze authority can freeze the vault ATA and brick every unstake for that mint. A `TransferHook` mint is not exploitable today — `CpiContext::new` at `lib.rs:104` forwards no `remaining_accounts`, so the hook's extra account metas are missing and the stake simply fails.

Setting `RCLAW_PINNED_MINT` at build time should be a release-gate requirement rather than an option, but treat it as a deployment control, not a substitute for the first fix. Finally, add a regression test asserting `sum(StakeAccount.amount) <= vault.amount` after a stake against a `TransferFeeConfig` mint; `tests/attack.rs` has no such case today.

*Without this fix, any deployment that leaves the mint unpinned will, the first time users stake a fee-bearing Token-2022 mint, strand the last stakers' principal permanently with no instruction in the program capable of recovering it.*

---

### [Medium] F-09 -- Presale allowlist enforcement is decided by whether a gitignored artifact happens to exist on disk, while the deposit window opens 48h early based on the config — the OG round silently becomes a fully open sale

**Severity.** Not attacker-triggerable: no hostile call sequence forces this state, and the trigger is entirely operator-side. That said, the triggering state is the *default* one — `token/.gitignore:3` ignores `.artifacts/`, and `token/` contains no `.artifacts` directory — so every fresh clone, every second operator machine, and every CI runner starts in it. Once triggered, the impact is severe and unrecoverable: the 48-hour OG window opens with an ungated `PresaleBucketV2`, and the entire 150M-token / 5,000-SOL presale can be filled by sniper wallets under the 25 SOL `depositLimit` before any OG participant deposits. Confidentiality impact none; on-chain integrity none in the strict sense, since the Genesis program does exactly what it was told; availability and economic fairness are totally compromised for the OG round. Held at Medium rather than High for two reasons: it requires an operator mistake rather than an attacker capability, and `token/presale/genesis_lib.mjs:56-65` currently refuses any RPC URL containing "mainnet", which gates real-money exposure today. It becomes High the moment this tooling is pointed at a real raise, and note that the mainnet guard is a substring check (see F-19), so a mainnet endpoint whose hostname does not literally contain "mainnet" sails past it.

**Confidence.** High. CONFIRMED. Votes: 1 sanity check.

**Location.** `token/presale/genesis_presale.mjs:136-159`

**Code.**
```javascript
  // Optional Merkle whitelist for the OG round (run `presale:whitelist` first).
  const wl = readArtifact(ALLOWLIST_ARTIFACT);
  ...
  if (wl) {
    presaleInput.allowlist = allowlistInitArgsFromArtifact(cfg, wl);
    console.log('    whitelist: applying Merkle root', wl.rootHex.slice(0, 16) + '… (ends at publicStart)');
  }
  console.log('[2/2] addPresaleBucketV2 — configuring the fixed-price presale…');
  await addPresaleBucketV2(umi, presaleInput).sendAndConfirm(umi);
```

**What's wrong.** Two independent sources decide the two halves of the whitelist round. The deposit window's *start* comes from the committed config: `token/presale/genesis_lib.mjs:118-119` sets `depositStart` to `whitelistStart` whenever `cfg.whitelist` is non-empty, which is 48 hours before `publicStart` given the timeline at `token/presale/metaplex-genesis.config.json:33`. *Enforcement* comes from whether `token/.artifacts/allowlist.devnet.json` happens to exist on the operator's disk, because `readArtifact` (`genesis_presale.mjs:60-63`) returns `null` for a missing file and the `if (wl)` block is then skipped. There is no error, no warning, no cross-check against `cfg.whitelist`, no reference to `cfg.antiAbuse.whitelistViaAllowlistMerkle` (set `true` at `metaplex-genesis.config.json:56` and read nowhere in the codebase), and no post-send read-back of the on-chain `merkleRoot`. The only difference between a gated launch and an ungated one is the absence of one console line among ten.

Three things make this worse than the bare mechanism suggests. First, `presale:plan` — the pre-flight check the doc comment at `genesis_lib.mjs:105-108` explicitly sells as showing "exactly what gets sent" — reads the whitelist from the *config*, not the artifact (`genesis_presale.mjs:88-95` calls `buildAllowlist(cfg, wlAddrs)` directly). On the artifact-missing machine, `plan` prints a fully configured whitelist with a real Merkle root while `create` silently ships an open bucket. The one guard an operator would trust actively confirms the wrong thing. Second, the codebase already knows how to do this check and does it in the harmless direction: `cmdWhitelist` fails closed at `genesis_presale.mjs:213-215` with `throw new Error('config.whitelist is empty. …')`. So this is an inconsistency within a single file, not a missing concept. Third, the fail-open asymmetry is backwards relative to the depositor path, which fails closed at `genesis_presale.mjs:196-202` when a wallet has no proof. And this is a regression introduced by a prior fix: `docs/TOKEN_ROADMAP.md:481-482` records that the previous adversarial audit found a "zero-length whitelist window (`timeline.whitelistStart` was read nowhere)"; the fix wired the window's start to `cfg.whitelist` and left enforcement keyed to the artifact, which is precisely what creates the gap.

The stale-artifact half is equally real. `allowlistInitArgsFromArtifact` (`genesis_lib.mjs:207-216`) re-derives `endTime` and `quoteCap` from the current config but copies `merkleRoot` and `merkleTreeHeight` verbatim from the artifact, never re-deriving them from `cfg.whitelist`. An operator who edits the whitelist and re-runs `create` without re-running `presale:whitelist` publishes a root over the previous member set while reading the new list on screen.

**Exploit / reachability.** No Anchor accounts are on this path; this is off-chain Umi tooling calling an external program, so the guard surface is JS control flow only.

1. The operator adds OG base58 addresses to `whitelist` in `metaplex-genesis.config.json` (currently `[]` at line 59) and commits. The config is committed; the derived Merkle artifact is not.
2. `npm run presale:create` (`token/package.json:14`) runs on any machine that has not run `presale:whitelist`. `token/presale/RUNBOOK.md:44-45` lists the two commands as adjacent steps with nothing enforcing the order or the artifact's presence.
3. `genesis_lib.mjs:118-119` evaluates `hasWhitelist` as true and sets `depositStartCondition` to `TimeAbsolute(whitelistStart)`, 48 hours before `publicStart`.
4. `genesis_presale.mjs:137` returns `null` from `readArtifact`, so the `if (wl)` block at `:154-157` is skipped. `presaleInput` therefore carries the allocation, the four time conditions, the per-wallet floor and ceiling, and the claim schedule — and no `allowlist` field.
5. `genesis_presale.mjs:159` lands an ungated bucket whose deposit window opens 48 hours early. The SDK cannot cross-check a config it never sees, and the Genesis program has no way to know an allowlist was intended.
6. `genesis_presale.mjs:161-175` then writes `presale.devnet.json` and prints `=== DONE ===` with the genesis account and bucket address. Nothing reads the on-chain bucket back.
7. Anyone polling the Genesis program for new `PresaleBucketV2` accounts sees an ungated bucket and fans out across roughly 200 wallets, each depositing at or under the 25 SOL `maxContributionSol`, filling the 5,000 SOL `allocationQuoteTokenCap`. `depositPresaleV2` requires no proof because the bucket has no allowlist. OG participants and the whole public round find the sale sold out.

**Remediation.** Make the authority path fail closed like the depositor path, and verify what actually landed on chain. Replacing `genesis_presale.mjs:136-137` and `:154-157`:

```javascript
const wlAddrs = Array.isArray(cfg.whitelist) ? cfg.whitelist : [];
const wl = readArtifact(ALLOWLIST_ARTIFACT);
if (wlAddrs.length && !wl) {
  throw new Error(
    `config.whitelist has ${wlAddrs.length} members and deposits open at ` +
    `${cfg.timeline.whitelistStart} (48h before publicStart), but ` +
    `token/.artifacts/${ALLOWLIST_ARTIFACT} is missing on this machine. ` +
    `Sending now would open an UNGATED presale 48h early. Run \`npm run presale:whitelist\` first.`
  );
}
if (wl && !wlAddrs.length) {
  throw new Error('An allowlist artifact exists but config.whitelist is empty — refusing to publish a root with no config backing.');
}
if (wl) {
  const fresh = buildAllowlist(cfg, wlAddrs);          // re-derive; do not trust the artifact
  if (fresh.rootHex !== wl.rootHex) {
    throw new Error(
      `Stale allowlist artifact: config derives root ${fresh.rootHex.slice(0, 16)}… ` +
      `but the artifact holds ${wl.rootHex.slice(0, 16)}…. Re-run \`npm run presale:whitelist\`.`
    );
  }
  presaleInput.allowlist = fresh.initArgs;
  console.log('    whitelist: applying Merkle root', fresh.rootHex.slice(0, 16) + '… (ends at publicStart)');
}
```

Re-deriving from config removes the second source of truth entirely: `allowlistInitArgsFromArtifact` (`genesis_lib.mjs:207-216`) becomes dead and should be deleted, since `buildAllowlist` already returns ready `initArgs` (`genesis_lib.mjs:192-203`) and the artifact is thereafter only needed for the per-address proofs on the deposit path. Then, after the `sendAndConfirm` at `genesis_presale.mjs:159`, fetch the `PresaleBucketV2` account and assert both that `allowlist.enabled === (wlAddrs.length > 0)` and that the on-chain `merkleRoot` bytes equal the derived root, throwing loudly on mismatch — a silent `=== DONE ===` that cannot distinguish a gated launch from an ungated one is the core of this finding. Finally, either honor `cfg.antiAbuse.whitelistViaAllowlistMerkle` as the authoritative intent flag in the check above, or delete it; a config field asserting an anti-abuse control that no code reads is itself misleading.

*Without this fix, the first `presale:create` run on a machine that has not built the Merkle tree opens a 48-hour ungated window over the entire 5,000 SOL raise, and nothing in the tooling's output will tell the operator it happened.*

---

### [Medium] F-11 -- The 60% raise-to-liquidity split is encoded by no instruction — the Raydium bucket is created with 100M tokens and no protocol-routed quote side

**Severity.** The code-level defect is certain: a published economic commitment — 60% of up to 5,000 SOL raised, roughly 3,000 SOL — has no on-chain encoding, while the matching token side of 100,000,000 RCLAW (10% of supply) is irrevocably committed via a never-claim LP lock. Three facts keep this out of High. There is no third-party attacker path, no missing account constraint, and no account-substitution vector — the harmful outcome requires the off-chain key holder to choose not to seed the pool. The whole path is gated off mainnet today by `token/presale/genesis_lib.mjs:56-65`. And the gap is self-disclosed twice in the code (`token/presale/genesis_presale.mjs:99` and `:253-254`), in the runbook's pre-launch confirmation list (`token/presale/RUNBOOK.md:88-89`), and as a prior-audit item at `docs/TOKEN_ROADMAP.md:483-484`. It is not lower than Medium because `RUNBOOK.md:20` and `docs/TOKEN_ROADMAP.md:187-188` and `:235-236` state the 60% split to purchasers as settled economics, and the roadmap's own verification matrix at `docs/TOKEN_ROADMAP.md:502` records that `liquidity` has never sent a transaction, so the finalize semantics that would have to compensate are entirely unvalidated. On mainnet with a real raise this is a custody-and-disclosure failure in the High-to-Critical range; today it is a deployment-gated design gap.

**Confidence.** High. PLAUSIBLE. Votes: 1 sanity check.

**Location.** `token/presale/genesis_presale.mjs:244-254`

**Code.**
```javascript
  await addRaydiumCpmmBucketV2(umi, {
    genesisAccount: publicKey(a.genesisAccount),
    baseMint: publicKey(a.baseMint),
    bucketIndex: LIQUIDITY_BUCKET_INDEX,
    baseTokenAllocation: lp.baseTokenAllocation,
    lpLockSchedule: lp.lpLockSchedule, // never-claim => LP locked forever
    startCondition: lp.startCondition, // pool created at deposit-window close
  }).sendAndConfirm(umi);
  console.log('Liquidity bucket added; LP is permanently locked (never-claim schedule).');
  console.log(`WARNING: the ${lp.raisedSolToLiquidityBps / 100}% raise->pool split is NOT encoded on-chain by this command.`);
```

**What's wrong.** `deriveLiquidityParams` returns `raisedSolToLiquidityBps` (`token/presale/genesis_lib.mjs:236`, sourced from `metaplex-genesis.config.json:47`, value 6000) but `cmdLiquidity` never passes it to any instruction — the `addRaydiumCpmmBucketV2` input carries exactly six fields, none of which touches the quote side. Nor does the presale bucket: the fully enumerated `presaleInput` at `genesis_presale.mjs:138-153` contains no `endBehaviors` or quote-routing field of any kind. A repo-wide grep for `raisedSolToLiquidity`, `endBehaviors`, and `SendQuoteToken` outside `node_modules` returns only the derivation, the config value, and the two console warnings. The code names its own missing mechanism at `genesis_presale.mjs:99` and `:254`: "needs an `endBehaviors` `SendQuoteTokenPercentage` on the presale bucket".

The sharp part is the asymmetry. `genesis_lib.mjs:233-234` commits the token side irrevocably via `createNeverClaimSchedule()`, while the quote side is governed by nothing on-chain and settles wherever the Genesis program's default routing sends it, under the sole control of the key at `token/.keys/mint-payer.json`. That single keypair is simultaneously the genesis authority (`genesis_presale.mjs:124`), the payer (`genesis_lib.mjs:67-75`), and the unsold-token recipient (`genesis_presale.mjs:296`, `recipient: me`) — despite `RUNBOOK.md:28` specifying that presale proceeds and unsold tokens flow to a Squads multisig. No code routes them there.

One correction to the original write-up. Its "Path B" claimed that the CPMM pool finalizes with a near-zero quote side and that the first swap therefore buys a dominant share for negligible SOL at a publicly derivable slot. That is *not* established. `@metaplex-foundation/genesis` 0.40.0 is declared in `token/package.json` but is not installed in this repo, so the Raydium bucket's quote-sourcing behavior at finalize could not be inspected, and a CPMM pool cannot normally be initialized with a zero quote reserve — the more likely outcome is a failed finalize, which is a launch-day availability bug with a different fix. Treat Path B as an open question for devnet validation, which `docs/TOKEN_ROADMAP.md:502` confirms has never been run. The finding stands on the discretionary-proceeds path and the disclosure mismatch alone.

**Exploit / reachability.** There is no hostile-caller sequence; the operator sequence is what matters and is fully verifiable from source. `npm run presale:create` calls `initializeV2({ authority: umi.identity, ... })` at `genesis_presale.mjs:122-131`, where `umi.identity` is the single keypair loaded at `genesis_lib.mjs:67-75`. It then sends `addPresaleBucketV2(presaleInput)` at `:159` with no quote-routing field. `npm run presale:liquidity` sends `addRaydiumCpmmBucketV2` at `:244-251` with six fields, again with no quote-side field. The resulting on-chain state is: bucket 1 holding 100,000,000 × 10⁹ base units with a never-claim LP schedule and a `TimeAbsolute` start condition at `cfg.timeline.depositEnd`, and no on-chain record whatsoever of any obligation over the raised quote tokens. Whether the pool is ever seeded, and with how much, is a decision made off-chain by whoever holds the mint-payer key.

The verdict is PLAUSIBLE rather than CONFIRMED for two reasons, both stated precisely: the harmful outcome on the discretionary path is a function of a key holder's future behavior, which cannot be verified from source; and the mispricing/sniping outcome depends on Genesis 0.40.0 finalize semantics that could not be read because the dependency is absent from the repo.

**Remediation.** Encode the commitment on-chain rather than printing it. The code names its own fix — add the end behavior to the presale bucket input at `genesis_presale.mjs:138-153` so the program moves the quote share itself:

```javascript
  const presaleInput = {
    /* … existing fields, genesis_presale.mjs:139-152 … */
    claimSchedule: p.claimSchedule,
+   // Route the ratified share of the raise to the Raydium bucket on close, so the
+   // quote side is as irrevocable as the never-claim token side.
+   endBehaviors: [sendQuoteTokenPercentage({
+     bps: lp.raisedSolToLiquidityBps,              // 6000, from config
+     destinationBucketIndex: LIQUIDITY_BUCKET_INDEX,
+   })],
  };
```

Verify the exact export name and argument shape against `@metaplex-foundation/genesis` 0.40.0 once it is installed — the field name above is taken from the repo's own comment at `genesis_presale.mjs:254` and must be confirmed, not copied blindly. Then, in priority order: make the omission fail closed by having `cmdLiquidity` throw when `cfg.liquidity.raisedSolToLiquidityBps > 0` and no quote-routing behavior was set on the presale bucket, so the irrevocable never-claim token lock can never be created ahead of an unenforced quote commitment; point the remaining discretionary proceeds and the `withdraw-unsold` recipient at `genesis_presale.mjs:296` at the Squads multisig that `RUNBOOK.md:28` already requires, rather than the mint-payer hot key; and until the first item lands, correct `RUNBOOK.md:20` and `docs/TOKEN_ROADMAP.md:187-188` to state that the 60% split is an operator undertaking, not an on-chain guarantee, because the current wording reads as a protocol property to purchasers. Separately, resolve the finalize question empirically on devnet — `RUNBOOK.md:88-89` already lists this — since "the bucket sources its quote side automatically" and "finalize aborts with no quote balance" need different fixes.

*Without this fix, the only thing standing between purchasers and a raise that never reaches the pool is the operator's discretion, and the token side of the pool is already locked forever regardless of what the quote side does.*

---

### [Medium] F-16 -- Every presale/liquidity/deposit/claim/withdraw command discards the confirmation result and reports success for a transaction that landed and failed on-chain

**Severity.** Impact is confined to off-chain reporting: no on-chain state is corrupted, no funds move incorrectly, no authority is lost, and there is no attacker-controlled trigger — the trigger is a benign race (cap fill, `depositEnd` or the allowlist `endTime` elapsing between simulation and inclusion, or a lamport or compute shortfall at execution). A failed transaction moves nothing, so a depositor's SOL is safe; the harm is a false belief that is fully discoverable by reading the chain. That rules out High. It is above Low for two reasons. The create path turns a false message into durable bad state: `cmdCreate` writes `presale.devnet.json` and prints `=== DONE ===` at `token/presale/genesis_presale.mjs:161-175` regardless of whether the two sends at `:131` and `:159` actually succeeded on chain. And `token/e2e/devnet_dryrun.mjs` derives step success purely from the child process exit code at `:86`, then prints `ALL STEPS OK ✓` at `:152-153` and writes `ok: true` into `.artifacts/e2e-report.json` at `:103` — so the artifact the roadmap relies on as evidence that create, deposit, and claim work can be green on a run where deposit and claim both failed on chain. That is precisely the evidence a mainnet gate gets signed off against. Mitigating: `skipPreflight` is left undefined and therefore false, so the majority of failures are caught by preflight and thrown; only the simulate-clean, execute-fail window is silent. This would be High on mainnet, where the same silent path covers a real 5,000 SOL raise and its refunds.

**Confidence.** High. CONFIRMED. Votes: 1 sanity check.

**Location.** `token/presale/genesis_presale.mjs:206-208`

**Code.**
```javascript
  console.log(`Depositing ${amountSol} SOL into presale bucket ${a.bucket}…`);
  await depositPresaleV2(umi, input).sendAndConfirm(umi);
  console.log('Deposit confirmed.');
```

**What's wrong.** All seven privileged Umi sends in this file `await ...sendAndConfirm(umi)` and discard the returned `{signature, result}`: `:131` (`initializeV2`), `:159` (`addPresaleBucketV2`), `:207` (`depositPresaleV2`), `:251` (`addRaydiumCpmmBucketV2`), `:275` (`withdrawPresaleV1`), `:303` (`withdrawUnsoldPresaleV1`), and `:318` (`claimPresaleV2`). Verified against the pinned dependencies rather than from memory: umi 1.5.1's `TransactionBuilder.sendAndConfirm` (`dist/esm/TransactionBuilder.mjs:204-215`) sends, confirms, and returns the confirmation result without inspecting it; `umi-rpc-web3js` 1.5.1's `confirmTransaction` (`src/createWeb3JsRpc.ts:408-415`) is a bare pass-through to `Connection.confirmTransaction`; and web3.js 1.98.4's blockheight-strategy confirmation (`lib/index.cjs.js:6694-6705`) resolves normally whenever the transaction reached `PROCESSED`, throwing only `TransactionExpiredBlockheightExceededError` on expiry. A processed-but-failed transaction therefore resolves as `{context, value: {err: {InstructionError: [...]}}}` and the caller prints "Deposit confirmed." over it. Preflight is enabled (`createWeb3JsRpc.ts:359-368` forwards an empty options object, so `skipPreflight` is false), which catches failures already present at simulation time but nothing that changes between simulation and inclusion.

This is an avoidable inconsistency inside this repo, not an SDK limitation. `token/scripts/create_token.mjs:103`, `:113`, and `:123` use web3.js's `sendAndConfirmTransaction`, which does `if (status.err) { ... throw ... }` (`lib/index.cjs.js:2300-2309`) and prints the signature. The presale path is the only one that drops the result.

One correction to the original write-up: the "operator ticks wallets off a refund list and under-refunds" scenario is not supported by the code. `cmdWithdraw` is strictly self-service — `genesis_presale.mjs:265-267` derives both the deposit PDA and the recipient token account from `umi.identity.publicKey`, so an operator cannot refund another wallet. The accurate refund harm is one step weaker: the depositor themselves sees "Withdraw/refund confirmed." for a refund that failed, stops checking, and feeds a false self-report into the operational soft-cap reconciliation that `genesis_presale.mjs:276` explicitly says is "enforced operationally".

**Exploit / reachability.** No attacker is required; this is a correctness defect reachable on any of the seven commands.

1. A depositor runs `npm run presale:deposit -- --amount 25` near the 5,000 SOL cap. `cmdDeposit` (`genesis_presale.mjs:179-209`) builds the input and calls `sendAndConfirm` at `:207`.
2. Preflight simulates against the current slot and succeeds; `send()` returns a signature.
3. Between simulation and inclusion, other depositors fill `allocationQuoteTokenCap` (or block time crosses `depositEndCondition`, or the allowlist `endTime` passes). The transaction is included with `err != null`. No SOL moves.
4. `confirmTransaction` resolves with `{value: {err: {InstructionError: [...]}}}`. Line 207 discards it; line 208 prints "Deposit confirmed." and the process exits 0.
5. Under `npm run e2e:dryrun`, the same false success propagates: `run()` returns `ok: res.status === 0` (`token/e2e/devnet_dryrun.mjs:86`), `saveReport` writes `ok: steps.every((s) => s.ok)` (`:103`), and `main()` prints `ALL STEPS OK ✓` and exits 0 (`:152-154`). With a 90-second deposit window (`DEPOSIT_FOR` at `:32`) plus the two-second cushion in `sleepUntil` (`:90`), a deposit landing after `depositEndCondition` is a realistic outcome in this harness, not a theoretical one.

The `cmdCreate` variant is the sharpest instance: nothing gates the artifact write on the two sends succeeding, so a create whose `initializeV2` or `addPresaleBucketV2` failed on chain still leaves an authoritative-looking `presale.devnet.json` naming a genesis account and bucket that do not exist. Damage is bounded because later commands reading that artifact fail preflight loudly, but the artifact is the thing a human trusts.

**Remediation.** Capture the confirmation result and fail loudly on a non-null on-chain error. Add one helper in `token/presale/genesis_lib.mjs` and route all seven sends through it (note the file is ESM — `token/package.json:5` sets `"type": "module"` — so use an import, not `require`):

```javascript
import { base58 } from '@metaplex-foundation/umi';

export async function sendChecked(builder, umi, label) {
  const { signature, result } = await builder.sendAndConfirm(umi);
  const sig = base58.deserialize(signature)[0];
  if (result.value.err) {
    throw new Error(`${label} FAILED on-chain: ${JSON.stringify(result.value.err)} (sig ${sig})`);
  }
  return sig;
}
```

Then at each call site, for example `genesis_presale.mjs:206-208`:

```diff
-  await depositPresaleV2(umi, input).sendAndConfirm(umi);
-  console.log('Deposit confirmed.');
+  const sig = await sendChecked(depositPresaleV2(umi, input), umi, 'depositPresaleV2');
+  console.log('Deposit confirmed. tx:', sig);
```

Apply identically at `:131`, `:159`, `:251`, `:275`, `:303`, and `:318`, and print the signature in every success message so any claim of success is independently verifiable against an explorer — `create_token.mjs` already does this. Two supporting fixes: move the `saveArtifact` and `=== DONE ===` block at `genesis_presale.mjs:161-175` so it runs only after both sends confirm error-free; and set an explicit commitment on the confirm options rather than relying on the connection default, so the result reflects the intended durability level.

*Without this fix, the tooling cannot distinguish a presale that launched from one that did not, and `.artifacts/e2e-report.json` can certify a devnet run in which the deposit and claim steps both failed on chain.*

---

### [Medium] F-18 -- No lock-up, cooldown or time-weighting: the tier is a live spot balance, so one position serves unlimited users by rotation

**Severity.** No fund loss, no authority compromise, no availability impact — this is an economic and business-logic defect, so it cannot reach High on a fund-loss scale. The impact is the complete defeat of the token's only stated utility sink: the marginal cost of granting one more user "elite" access drops from "acquire 100,000 $RCLAW" to roughly three transactions of fees plus about 0.0016 SOL of rent for that user's stake PDA, and the same principal is reusable within seconds, unbounded times. That is a permanent property of the on-chain design, not a transient state. It is not Low, because it fully nullifies the gate's economic premise for any mainnet deployment; it is not High, because nothing is stolen and no privilege beyond a paid feature flag is obtained. Note that the grade is for the on-chain design gap on its own terms — it is deliberately not discounted because a cheaper upstream bypass exists today (see below), since that bypass is a separate fixable bug and this one survives its fix.

**Confidence.** High. CONFIRMED. Votes: 1 sanity check.

**Location.** `programs/rclaw_staking/src/lib.rs:137-139`

**Code.**
```rust
    pub fn unstake(ctx: Context<Unstake>, amount: u64) -> Result<()> {
        let staked = ctx.accounts.stake_account.amount;
        require!(amount > 0 && amount <= staked, StakeError::InsufficientStake);
```

**What's wrong.** `unstake` gates only on `amount` versus the recorded balance. It reads no clock, consults no unlock timestamp, and enforces no minimum holding period. Every constraint on the `Unstake` accounts struct governs *who* and *which accounts*, never *when*: `owner: Signer<'info>` at `lib.rs:222`; `seeds`, `bump = stake_account.bump`, `has_one = owner`, and `has_one = mint` at `:226-232`; the `seeds = [b"vault", mint]` PDA at `:236`; the `associated_token::*` triple on the vault at `:239-244`; and the owner/mint constraints on `user_token_account` at `:247-251`. The only `Clock::get()` call in the entire program is the write at `lib.rs:121`, and `staked_at` is read by nothing — on-chain or off — anywhere in the repository. Because line 121 overwrites it unconditionally on every re-stake, it could not support time-weighting even if a consumer wanted it. So the claim is not merely "no lockup was configured" but "no lockup could be enforced with the data currently recorded."

The off-chain consumer closes the loop: `bot/token/tier_gate.py:165-168` issues `getProgramAccounts` at `"commitment": "confirmed"` and `:179` reads `int.from_bytes(raw[72:80], "little")`, the live `amount` field only, which `tier_for_balance` (`:186-194`) maps straight to a tier. The tier is therefore a spot read of a freely mutable balance, with no snapshot or TWAP to fall back on.

Two clarifications. First, the flash-loan framing does not apply today, and the original write-up correctly self-refuted it: `stake(N)` and `unstake(N)` genuinely can be composed as two instructions in one transaction (Anchor serializes the account back at instruction exit and the second instruction re-deserializes it, with `bump = stake_account.bump` at `:229` resolving to the value written at `:122`), but the tier is consumed by an off-chain Telegram RPC read that cannot be composed into a transaction, so atomicity buys an attacker nothing. That is a property of the current consumer, not of the program — any future consumer that gates via CPI or reads the `Staked` event makes this trivially flash-loanable with no change to this code. Second, a strictly cheaper bypass exists upstream right now: `/linkwallet` performs no proof of wallet ownership, only a base58 shape check at `bot/skills/telegram_handler.py:8101-8106` before calling `set_sol_wallet`, so any number of users can link the same known whale or treasury address and pass the gate with zero on-chain activity. Rotation is the second-cheapest bypass, not the cheapest — but fixing `/linkwallet` does not touch this finding.

**Exploit / reachability.** Rotation, no flash loan needed. Whale W holds 100,000 RCLAW. For each customer U(i):

1. W sends an SPL transfer of 100,000 RCLAW to U(i)'s ATA.
2. U(i) signs `stake(100000)` with `owner` = U(i), `mint` = RCLAW, `stake_account` = PDA `["stake", U(i), mint]` (created by `init_if_needed`, U(i) pays rent), `vault_authority` = PDA `["vault", mint]`, `vault` = that authority's ATA, and `user_token_account` = U(i)'s ATA. Every constraint at `lib.rs:175-216` is satisfied — U(i) genuinely owns the tokens at that instant, and nothing rejects a position that is seconds old.
3. U(i) runs `/scalp`. `allows_user` in `bot/token/tier_gate.py` resolves the linked wallet, `staked_of` reads 100,000 at offset 72, `tier_for_balance` returns `elite`, access is granted.
4. U(i) signs `unstake(100000)`. Line 139 passes because `amount == staked`, the vault PDA signs the transfer back out, and line 162 zeroes the record. No time guard is consulted anywhere.
5. U(i) returns the tokens to W, who repeats with U(i+1) in the next slot.

Per cycle the stake record goes 0 → 100,000 → 0, net token movement is zero, and one more paying-tier user has been served. The gate is off unless `TOKEN_TIER_GATE_ENABLED` is set and a mint is configured (`tier_gate.py:88-94`), and `tier_gate.py:100-104` refuses any RPC URL containing "mainnet" — but those are guards on the current off-chain consumer, not on the program.

**Remediation.** Give the stake record a temporal dimension and make the consumer honor it; either half alone is bypassable. On-chain:

```rust
// in StakeAccount (lib.rs:255-262); bump SPACE by 8 at lib.rs:265-267
pub unlock_at: i64,

// in stake(), replacing the write at lib.rs:121
let now = Clock::get()?.unix_timestamp;
sa.staked_at = now;
// extend, never shorten, so a 1-lamport top-up cannot reset an existing lock
sa.unlock_at = sa.unlock_at.max(
    now.checked_add(LOCKUP_SECONDS).ok_or(StakeError::Overflow)?,
);

// in unstake(), before the require! at lib.rs:139
require!(
    Clock::get()?.unix_timestamp >= ctx.accounts.stake_account.unlock_at,
    StakeError::StillLocked
);
```

with `pub const LOCKUP_SECONDS: i64 = 7 * 24 * 60 * 60;` and a `StillLocked` variant added to the `StakeError` enum at `lib.rs:287-303`. The `.max()` matters: taking the new timestamp unconditionally would let a trivial top-up shorten an existing lock. Bumping `SPACE` changes the layout, so the offset table at `lib.rs:23-32`, the layout block in `programs/rclaw_staking/README.md:32` onward, and the offset-72 read at `tier_gate.py:179` must move in lockstep — appending `unlock_at` after `bump` keeps `amount` at offset 72 and is the cheapest option. Off-chain, have `staked_of` read `unlock_at` as well and require it to be comfortably in the future, or gate on a periodic snapshot or time-weighted average of `amount` rather than an instantaneous read, so a position that appears and vanishes inside one interval never grants a tier. Independently and with higher priority, fix the unauthenticated wallet link at `bot/skills/telegram_handler.py:8101-8106`: issue a random nonce, require an ed25519 signature over it from the claimed address, and verify it before `set_sol_wallet`. Until that lands, an on-chain lockup buys nothing, because the gate is bypassable by typing a whale's public address.

*Without this fix, the staking requirement prices tier access at the cost of a few transaction fees rather than the cost of holding the token, so the tier gate generates no demand for $RCLAW at all.*

---

### [Medium] F-19 -- The mainnet guard is a case-sensitive substring match on 'mainnet' and the `cluster` config field is dead text — no authoritative cluster check exists anywhere

**Severity.** This is a safety-control weakness, not an adversarial vulnerability. Three things pulled toward High: the damage is irreversible, since a Token-2022 mint cannot be un-created and a revoked mint authority cannot be restored; this guard is the sole enforcement behind an explicit written promise at `token/README.md:7` ("It refuses to run against mainnet") and `:73-74` ("Nothing here holds a mainnet key, signs a mainnet tx, or requests real funds"); and the correct check is one RPC call away. Three things kept it at Medium and won. There is no hostile third party anywhere on this path — whoever sets `RPC_URL` also holds the payer keypair, so all harm is operator self-inflicted. No third-party funds, user assets, or authority are at risk; the loss is the operator's own rent and fees plus the reputational damage of a squatted mint. And firing it requires the operator to have already deviated from the documented workflow on a second, independent axis, because `token/scripts/keygen.mjs` generates a fresh keypair and airdrops on devnet only, so the default `.keys/mint-payer.json` cannot pay a mainnet fee. Under CVSS this scores near-nothing: no privilege escalation, no confidentiality or integrity impact on any victim other than the actor. The consequence class worth fixing before any mainnet gate opens is "permanent, unrecoverable mainnet artifact created by tooling that documents itself as incapable of creating one."

**Confidence.** High. CONFIRMED. Votes: 1 sanity check.

**Location.** `token/scripts/lib.mjs:28-37`

**Code.**
```javascript
export function getConnection(env) {
  const url = env.RPC_URL || clusterApiUrl('devnet');
  if (url.includes('mainnet')) {
    throw new Error(
      'Refusing to run against mainnet. This is draft/devnet tooling — mainnet is gated behind ' +
        'legal review + audit (see docs/TOKEN_ROADMAP.md §10-11).'
    );
  }
  return new Connection(url, 'confirmed');
}
```

**What's wrong.** A denylist substring match stands in for an authoritative chain-identity check that is never performed anywhere in the repository — grepping `token/` and `bot/` for `getGenesisHash`, `genesisHash`, and `getVersion` returns zero hits, so the endpoint is never asked which chain it is on. The same weak guard is duplicated at `token/presale/genesis_lib.mjs:58` (`rpcUrl()`, reached by `makeUmi()` on every presale command) and `bot/token/tier_gate.py:100`. It fails open along two independent axes, both reachable in good faith. Case: DNS is case-insensitive but `String.prototype.includes` is not, so `https://api.MAINNET-beta.solana.com` resolves to mainnet-beta and contains no lowercase "mainnet". Hostname: a large population of real mainnet endpoints simply do not contain the word — `https://solana-rpc.publicnode.com`, `https://rpc.ankr.com/solana`, `https://ssc-dao.genesysgo.net`, the older `https://rpc.helius.xyz/?api-key=…` form, any bare IP, any local port-forward or corporate proxy.

The config cannot compensate, because the field a reviewer would trust most is dead text. `cluster` appears as a top-level deploy parameter at `token/config/token.config.json:17`, `token/presale/metaplex-genesis.config.json:4`, and `token/presale/smithii.config.json:4`, and grepping every `.mjs` for "cluster" yields only four hits: `lib.mjs:5` and `:29` (`clusterApiUrl` as a *default*, unreachable once `RPC_URL` is set), `token/scripts/create_token.mjs:133`, and `token/presale/genesis_presale.mjs:162`. Nothing reads `cfg.cluster`. The latter two are `cluster: env.CLUSTER || 'devnet'` — cosmetic labels written into the artifact *after* the transactions were sent, so an artifact can and will be stamped `"cluster": "devnet"` for a mint created on mainnet. `token/scripts/create_token.mjs:129-143` sits below the `sendAndConfirmTransaction` calls at `:103`, `:113`, and `:123`, and line 142 additionally hardcodes `note: 'DRAFT / DEVNET artifact — see docs/TOKEN_ROADMAP.md'`.

The authors knew the right pattern: `token/bridge/ntt_bridge.mjs:58-60` enforces its analogous `network` field with fail-closed strict equality.

One framing correction to the original write-up, which was phrased as an exploit. There is no attacker here — the actor and the victim are the same party — and the scenario as filed omitted that the transactions cannot land without a mainnet-funded payer, which the documented keygen flow never produces.

**Exploit / reachability.** No Anchor program is on this path; this is entirely off-chain Node tooling, so there are no accounts, seeds, or constraints to enumerate.

1. The operator's environment contains `RPC_URL=https://rpc.ankr.com/solana` (or one of the other forms above), set in their shell or inherited from a CI or deploy environment. `token/e2e/devnet_dryrun.mjs:76` forwards the entire parent environment into every spawned child (`{ ...process.env, GENESIS_CONFIG }`), so one inherited `RPC_URL` propagates through keygen, create, liquidity, deposit, and claim.
2. `lib.mjs:29` sets `url` from `env.RPC_URL`; `:30` evaluates `url.includes('mainnet')` as false; `:36` returns a `Connection` pointed at mainnet-beta. No throw, and no downstream check exists to catch it.
3. `npm run create` (`token/package.json:9`) executes three transactions against mainnet in order: create account plus metadata pointer plus `initializeMint` plus `initializeMetadata` (`create_token.mjs:102-103`); create ATA plus `mintTo` of 1,000,000,000 × 10⁹ base units (`:109-113`); and `setAuthority(MintTokens, null)`, which is permanent (`:118-124`).
4. `create_token.mjs:132-143` then writes an artifact labelled `"cluster": "devnet"` and `note: 'DRAFT / DEVNET artifact'` for a mint that now exists on mainnet with draft parameters and an unratified metadata URI.

The one precondition that could not be verified from source, and the reason this is not graded higher: every transaction above needs the payer to hold mainnet SOL. `token/scripts/keygen.mjs` generates a fresh keypair and airdrops only on devnet, so the default key's mainnet balance is zero and the first transaction dies at fee payment. The realistic path is an operator who has also pointed `KEYPAIR_PATH` (`lib.mjs:40`) at an already-funded wallet — which is exactly what someone preparing a real launch does — or a dev box or CI runner that already carries both a funded key and an inherited `RPC_URL`. The presale path is worse in one respect: `presale:create` sends `initializeV2` plus `addPresaleBucketV2` with the placeholder September-2026 timeline, and `presale:deposit` moves real SOL.

**Remediation.** Replace the substring denylist with an authoritative chain-identity check and fail closed. The mainnet-beta genesis hash is a fixed constant, so this is exact rather than heuristic. `getConnection` must become `async`, which is mechanical since all three call sites in `token/scripts/` are top-level-await ES modules:

```javascript
const MAINNET_GENESIS = '5eykt4UsFv8P8NJdTREpY1vzqKqZKvdpKuc147dw2N9d';
const DEVNET_GENESIS  = 'EtWTRABZaYq6iMfeYKouRu166VU2xqa1wcaWoxPkrZBG';

export async function getConnection(env, { allowedGenesis = [DEVNET_GENESIS] } = {}) {
  const url = env.RPC_URL || clusterApiUrl('devnet');
  const conn = new Connection(url, 'confirmed');
  const genesis = await conn.getGenesisHash();          // authoritative, not textual
  if (genesis === MAINNET_GENESIS) {
    throw new Error(
      `Refusing to run against mainnet-beta (genesis ${genesis}). Draft/devnet tooling — ` +
      'mainnet is gated behind legal review + audit (docs/TOKEN_ROADMAP.md §10-11).'
    );
  }
  if (!allowedGenesis.includes(genesis)) {              // fail CLOSED on anything unrecognized
    throw new Error(`Refusing unrecognized cluster (genesis ${genesis}). Expected devnet.`);
  }
  return conn;
}
```

The allowlist, not the denylist, is the point: an unknown genesis hash must throw, so a new provider or a private validator cannot silently pass. Mirror the identical change into `token/presale/genesis_lib.mjs:56-65` and `bot/token/tier_gate.py:100`. Two cheap supporting fixes: make the dead `cluster` field authoritative by having `loadConfig()`'s consumers pass `cfg.cluster` into `getConnection` as the expected cluster and asserting the observed genesis hash matches, which turns `token/config/token.config.json:17` from a comment into an enforced deploy parameter along the lines of `token/bridge/ntt_bridge.mjs:58-60`; and stop stamping an unverified label into the artifact, recording the genesis hash actually observed on the connection at `create_token.mjs:133` and `genesis_presale.mjs:162` instead of `env.CLUSTER || 'devnet'`. `token/README.md:73` should also be corrected once fixed, since it currently documents the weaker substring behavior as the security guarantee.

*Without this fix, the repository's central safety promise — that this tooling cannot touch mainnet — is enforced by a string search that a large fraction of real mainnet RPC URLs do not trip, and the artifact left behind will report the wrong chain.*

---

### [MEDIUM] F-23 -- Build toolchain is entirely unpinned — empty `[toolchain]` and no rust-toolchain.toml — so the deployed bytecode cannot be reproduced or verified

**Severity.** There is no attack vector in the runtime sense: no instruction sequence, no attacker-supplied account, no token movement, and no authority change. Attack complexity and privileges-required are therefore not the right axes — the loss scenario this defect enables (a malicious program upgrade whose source is not this repo) is fully owned by whoever holds the program upgrade authority, and this finding does not confer that authority. What the defect destroys is *detection capability*: it removes the ability of any third party to prove that deployed bytecode corresponds to this commit. Confidentiality and integrity impact on existing assets is None; availability impact is None. Graded Medium rather than High for exactly that reason, and rather than Low for four: the residual unpinned inputs (rustc, Solana platform-tools/SBF LLVM, Anchor CLI) are precisely the ones that change emitted bytecode; the `RCLAW_PINNED_MINT` amplifier below makes the gap structural rather than a one-line config fix; the program is the tier gate for a live trading bot with a stated mainnet roadmap; and verifiability cannot be retrofitted onto an already-deployed program. Present live impact is zero — `declare_id!` at `programs/rclaw_staking/src/lib.rs:41` is still the Anchor default placeholder `Fg6PaFpoGXkYsidMpWTK6W2BeZ7FEfcYkg476zPFsLnS`, `Anchor.toml:10` repeats it, and `Anchor.toml:16` targets devnet. The correct disposition is a hard deployment gate, not a live-exploit entry.

**Confidence.** High. CONFIRMED. Votes: 1 sanity check.

**Location.** `Anchor.toml:3-7`

**Code.**
```toml
[toolchain]

[features]
resolution = true
skip-lint = false
```

**What's wrong.** The `[toolchain]` table exists but is empty — no `anchor_version`, no `solana_version`, no `package_manager`. Those keys are what Anchor reads to select the CLI and Solana platform-tools (and, for `anchor build --verifiable`, the Docker build image) used for a build. With them absent, all three fall back to whatever the operator happens to have installed. There is also no `rust-toolchain.toml` or `rust-toolchain` file anywhere in the tree (verified by `find` over the repo), so rustc is whatever is on `PATH`; the only declared compiler setting in the whole workspace is `edition = "2021"` at `programs/rclaw_staking/Cargo.toml:5`.

One correction to the title's framing, which overstates the case: the build is not *entirely* unpinned. `Cargo.lock` is committed at the repo root and pins the full crate graph exactly (anchor-lang and anchor-spl at 0.30.1, solana-program and solana-sdk at 1.18.26 — all EOL relative to current upstream, which is a separate maintenance concern). `Cargo.toml:8-11` also fixes the codegen flags that matter for this audit (`overflow-checks = true`, `lto = "fat"`, `codegen-units = 1`), and since Cargo ignores `[profile]` sections in workspace members and `programs/rclaw_staking/Cargo.toml` carries no override, that root profile is authoritative for the SBF release build. The honest residual is compiler-and-CLI drift, not dependency drift.

The finding's sharpest instance is one the original report missed, and it is the reason this is not a Low. `programs/rclaw_staking/src/lib.rs:63` reads `pub const PINNED_MINT: Option<&str> = option_env!("RCLAW_PINNED_MINT");`. That is a build-time environment variable which alters emitted bytecode and is recorded nowhere in the repo or in any CI config. `anchor build` and `RCLAW_PINNED_MINT=<addr> anchor build` produce different binaries from an identical commit, and a verifiable-build container does not inherit the variable unless it is passed explicitly. Because `PINNED_MINT` is exactly the guard that makes `stake` reject non-canonical mints (`check_pinned_mint` at `lib.rs:92`, `require_keys_eq!` / `UnexpectedMint` at `lib.rs:76`), a verifier who rebuilds the tagged commit and gets a hash mismatch cannot distinguish "the deployer legitimately set the pin" from "the deployer stripped the mint check."

Compounding both: `.github/workflows/ci.yml` contains no cargo or anchor step at all (see F-40), so the program is never compiled in CI and no reference artifact or reference hash exists anywhere to compare against.

**Exploit / reachability.** Reachability here is operational rather than transactional. The sequence is: (1) an operator builds and deploys with some ambient rustc, Anchor CLI, and platform-tools, optionally with `RCLAW_PINNED_MINT` set; (2) nothing in the repo records any of those inputs; (3) at some later point the upgrade-authority holder pushes an upgrade built from source that is not this repo — say `lib.rs` with the `has_one` and mint-pin guards removed; (4) a user who wants to check clones the tagged commit and runs `anchor build --verifiable` or `solana-verify verify-from-repo`; (5) their hash differs from the on-chain hash *regardless of whether a substitution occurred*, because their toolchain and their build-time environment differ from the deployer's. Benign drift and malicious substitution are indistinguishable, so the check that would have caught the swap yields no signal in either direction. Step (3) is gated on a precondition external to this defect — possession of the upgrade authority or the deploy keypair — which is why the impact is detection-loss rather than compromise.

**Remediation.** Close all three unpinned inputs before deploying to any value-bearing cluster.

```diff
--- a/Anchor.toml
+++ b/Anchor.toml
 [toolchain]
+anchor_version = "0.30.1"
+solana_version = "1.18.26"
+package_manager = "npm"
 
 [features]
```

```diff
--- /dev/null
+++ b/rust-toolchain.toml
+[toolchain]
+channel = "1.75.0"
+components = ["rustfmt", "clippy"]
```

Pick the rustc channel that the chosen platform-tools release actually ships, and pin it as an exact patch version, never `stable`. Setting `package_manager = "npm"` also resolves the yarn mismatch: `Anchor.toml:24` invokes `yarn run ts-mocha` while only `package-lock.json` is committed (there is no `yarn.lock`), so the declared test entrypoint currently resolves dependencies outside the committed pins — change that line to `npm exec -- ts-mocha ...`.

Then make the `RCLAW_PINNED_MINT` build input reproducible. Once the canonical mint exists, replace the `option_env!` at `lib.rs:63` with a committed literal (`pub const PINNED_MINT: Option<&str> = Some("<base58 mint>");`) so the constant lives in source and is covered by the commit hash. If the env-var form is kept for flexibility, the exact value used for the release build must be recorded in the repo and in the release notes, and the documented verify command must pass it explicitly. Finally, add a build-and-verify job to `.github/workflows/ci.yml` that runs `anchor build --verifiable` and publishes the artifact hash per tagged release, and at deploy time run `solana-verify verify-from-repo` against the tagged commit and publish the verification PDA. Independently of all of this, decide and publish the upgrade-authority policy — verifiable builds only make a malicious upgrade *detectable*; moving the authority to a multisig, or burning it, is what makes one hard to perform.

*Without this, no third party — including the maintainer six months from now — can ever prove that the program running on chain is the program in this repository, so a silent authority-stripping upgrade is undetectable by construction.*

---

### [MEDIUM] F-31 -- The default 'mint' funding mode sells a base mint created by a discarded ephemeral signer, whose authority chain and supply invariants verify_token.mjs never checks

**Severity.** Not attacker-reachable: there is no hostile caller, no signer or PDA gap, no account substitution, and no on-chain instruction in this repo is involved. The impact is launch-integrity and false assurance. In the shipped default configuration the mint buyers actually receive is never checked by any tooling in this repo, while `token/presale/RUNBOOK.md:103` tells the operator that `npm run verify` proves "Mint + freeze authority already revoked" — a published proof that covers a different mint the presale never touches. Confidentiality impact None, integrity of funds not directly compromised; the exposure is that a live mint authority, a live freeze authority, or unexpected Token-2022 extensions on the sale mint would go undetected at launch, which is an integrity risk to every buyer's holdings mediated entirely by operator trust. In the current tree the two mainnet refusals (`token/presale/genesis_lib.mjs:56-65`, `token/scripts/lib.mjs:28-35`) hold this to devnet, so today's realized impact is Low. It is graded Medium against the mainnet gate, because the mis-targeted verification is a documented, publishable launch artifact and the pre-launch window is the only time it is cheap to fix. Not High: this code does not itself create a bad authority, and it requires the operator to rely on a checklist rather than any attacker action.

**Confidence.** Medium. PLAUSIBLE. Votes: 1 sanity check.

**Location.** `token/presale/genesis_presale.mjs:113-115`

**Code.**
```javascript
  const transferMode = cfg.fundingMode.mode === 'transfer';
  const baseMint = transferMode ? publicKey(cfg.token.mint) : generateSigner(umi);
  const baseMintPk = transferMode ? baseMint : baseMint.publicKey;
```

**What's wrong.** With the shipped default `"mode": "mint"` (`token/presale/metaplex-genesis.config.json:15`, also forced by `token/e2e/devnet_dryrun.mjs:59`), line 114 calls `generateSigner(umi)` and silently ignores `cfg.token.mint` — the field that `token/presale/RUNBOOK.md:24-26` explicitly instructs the operator to populate from `token.devnet.json`. The Genesis program then creates and mints a brand-new mint (call it M2) and the presale sells it. An operator who follows every step of the runbook still ends up selling a mint the runbook never told them about, with no warning beyond the `(new)` marker printed at line 119.

Nothing verifies M2. `token/scripts/verify_token.mjs:13-14` reads `token.devnet.json` and checks M1 — the Token-2022 mint from `create_token.mjs`, which in mint mode the presale never touches — for decimals, supply, revoked mint authority, null freeze authority, and metadata. The `MINT=<address>` override at `verify_token.mjs:14` is not a workaround, because `:22` hardcodes `TOKEN_2022_PROGRAM_ID` for `getMint` and `:23` uses the Token-2022 metadata-extension reader; pointing it at a Genesis-created mint under a different token program throws rather than verifying.

The original title's "discarded ephemeral signer" framing should be dropped: a mint account's keypair confers no authority once `InitializeMint` has run, and `create_token.mjs:44` discards its own the same way. What matters is that M2's authorities are set entirely by the Genesis program and are inspected by nothing.

Compounding this: `programs/rclaw_staking/src/lib.rs:63` pins the tier-gate mint at build time. Mint mode *guarantees* the presale mint differs from the `token/` mint, so a staking deployment pinned to M1 would reject every presale buyer's M2 tokens at `check_pinned_mint` (`lib.rs:92`). That is the same root cause — two unreconciled notions of "the $RCLAW mint" — and is a stronger argument for changing the default than the authority question is.

Secondary and genuinely Low: `fundingModeValue` (`token/presale/genesis_lib.mjs:159-162`) takes the on-chain enum discriminant from JSON (`mintValue: 0` / `transferValue: 1`) rather than from the SDK, and the config itself concedes at `:14` that the value is unratified. Transfer mode, by contrast, is fail-closed — `publicKey(cfg.token.mint)` at line 114 throws on the committed placeholder at config `:11` before any transaction is built.

**Exploit / reachability.** No attacker sequence exists; this is an operator path. Preconditions: the shipped config unmodified, a devnet RPC, and an operator following RUNBOOK Path A. (1) `npm run create` generates mint M1, nulls the freeze authority, mints the full supply, revokes the mint authority, and writes `token/.artifacts/token.devnet.json`. (2) The operator copies M1 into `token.mint` per RUNBOOK.md:24-26 — which has no effect on the default path. (3) `npm run presale:create` reaches line 113; `transferMode` is false, so line 114 evaluates `generateSigner(umi)` and M2 is created and minted by `initializeV2` (lines 122-131) with `fundingMode: fundingModeValue(cfg)` = 0. (4) `addPresaleBucketV2` at line 159 sells M2; the artifact at line 164 records only `baseMint: M2`. (5) Buyers deposit and claim, receiving M2. (6) The post-sale checklist at RUNBOOK.md:103 runs `npm run verify`, which prints "ALL CHECKS PASSED" for M1. State delta: six invariants verified and published for a mint no buyer holds; zero invariants checked on the mint every buyer holds. A repo-wide grep for `presale.devnet.json` confirms no script, test, or CI job ever reads `baseMint` for verification — it is used only as an instruction input for deposit, liquidity, withdraw, and claim.

The verdict is PLAUSIBLE rather than CONFIRMED for one precondition that cannot be checked from source: `@metaplex-foundation/genesis` is not installed in this checkout, so `initializeV2` cannot be read and M2's resulting mint authority, freeze authority, owning token program, and extension set are unknown. If Genesis revokes both authorities itself, this collapses to a documentation-and-coverage gap. If it retains mint authority on a genesis PDA or on the operator identity, the sale ships a mint with live inflation authority that nothing in this repo would ever surface.

**Remediation.** Three changes, all in off-chain tooling; none touch on-chain code. First, make the default coherent with the runbook and make the ignored-field case loud.

```diff
--- a/token/presale/metaplex-genesis.config.json
+++ b/token/presale/metaplex-genesis.config.json
-    "mode": "mint",
+    "mode": "transfer",
```

```diff
--- a/token/presale/genesis_presale.mjs
+++ b/token/presale/genesis_presale.mjs
   const transferMode = cfg.fundingMode.mode === 'transfer';
+  if (!transferMode && cfg.token.mint && !cfg.token.mint.startsWith('<FILL_FROM')) {
+    throw new Error(
+      `fundingMode.mode='mint' generates a NEW mint and ignores config.token.mint (${cfg.token.mint}). ` +
+      'Set mode to "transfer" to sell that mint, or clear token.mint to confirm you want a new one.'
+    );
+  }
   const baseMint = transferMode ? publicKey(cfg.token.mint) : generateSigner(umi);
```

Second, verify the mint that is actually sold. Add a `presale:verify` command that reads `baseMint` from `token/.artifacts/presale.devnet.json` and asserts against chain: owning token program, decimals, supply, `mintAuthority === null`, `freezeAuthority === null`, absence of transfer hook / transfer fee / permanent delegate / default-frozen extensions, and metadata `uri` and `updateAuthority`. Generalize `verify_token.mjs` first by replacing the hardcoded `TOKEN_2022_PROGRAM_ID` at `:22` with the mint account's actual `owner`, so it can verify mints under either token program. Then change `RUNBOOK.md:103` to name that command instead of `npm run verify`, and add the sale mint to the publish list at `:90`. Third, source the funding-mode discriminant from the SDK rather than JSON, and delete the `mintValue`/`transferValue` config fields once you do. Separately, tighten the mainnet refusals at `genesis_lib.mjs:58` and `token/scripts/lib.mjs:30` from `url.includes('mainnet')` to a positive allowlist (resolve the genesis hash, or require `CLUSTER=devnet` plus a devnet-hash assertion), so a custom mainnet RPC hostname cannot slip past the gate. Finally, reconcile the sale mint with `RCLAW_PINNED_MINT` before any staking deployment and document that the pin must equal `presale.devnet.json:baseMint`, not `token.devnet.json:mint`.

*Without this, the launch publishes a verification result for a mint nobody owns, and the mint every buyer does own ships with its authorities and extensions completely unexamined.*

---

### [MEDIUM] F-35 -- Upgradeable program with no account-schema version field, no reserved space, no realloc and no migration or rescue instruction

**Severity.** Not adversarially reachable — there is no attacker call sequence and no untrusted input drives it, which caps it below High. It is nonetheless under-graded at Low for three reasons. First, the failure mode is total and unrecoverable rather than degrading: `vault_authority` signs only inside `unstake` (`lib.rs:142-156`), and `unstake` is precisely the handler that breaks, so there is no in-program path to the escrowed tokens and no admin, rescue, or close instruction anywhere in the program. The loss is 100% of staked principal. Second, the probability is empirical rather than hypothetical: this exact class of breaking change has already shipped once in this repo (commit `b5e9868` changed both the field set and the PDA seeds), giving a demonstrated base rate of one in three commits to this file. Third, the mitigating control the team believes it has does not exist as described (see below), so the risk is currently unowned. Confidentiality None; integrity of the stake ledger None (nothing is falsified); availability of 100% of escrowed funds, permanently. Held at Medium rather than High because the trigger is entirely maintainer-controlled and gated behind a mainnet deployment that has not happened. Deployment-gate note: on a value-bearing deployment with real TVL this is a High — the upgrade authority can irreversibly brick every staker's position with a routine, well-intentioned feature commit and no warning at build, test, or deploy time.

**Confidence.** High. CONFIRMED. Votes: 1 sanity check.

**Location.** `programs/rclaw_staking/src/lib.rs:257-269`

**Code.**
```rust
#[account]
pub struct StakeAccount {
    pub owner: Pubkey,
    pub mint: Pubkey,
    pub amount: u64,
    pub staked_at: i64,
    pub bump: u8,
}

impl StakeAccount {
    // owner(32) + mint(32) + amount(8) + staked_at(8) + bump(1)
    pub const SPACE: usize = 32 + 32 + 8 + 8 + 1;
}
```

**What's wrong.** `StakeAccount` carries no schema version or variant tag beyond Anchor's 8-byte discriminator, and that discriminator is derived from the struct *name* only — it does not change when fields are added, removed, or reordered. `space = 8 + StakeAccount::SPACE` at `lib.rs:186` is fixed at 89 bytes. There is no `realloc` constraint, no `close`, no admin instruction, and no second pair of handlers: `stake` (`lib.rs:90`) and `unstake` (`lib.rs:137`) are the complete instruction set, confirmed by grep over the whole `programs/` tree. Crucially, `init_if_needed` takes the create branch only when the account is uninitialized; on an existing program-owned account it falls through to `Account::try_from` and never reallocs. Combined with an upgradeable deployment, the schema is effectively frozen at deploy.

The original report described two failure variants. Field growth is the loud one: a v2 adding `reward_debt: u128` bumps `SPACE` to 97, but Alice's pre-upgrade account is still 89 bytes on chain, so Borsh decoding of a 97-byte payload from an 89-byte buffer fails with `AccountDidNotDeserialize` during account validation of *both* `stake` and `unstake`. Field reorder is the silent one: inserting `tier: u8` before `amount` keeps every size legal and leaves the on-chain code self-consistent, but `bot/token/tier_gate.py:179` still slices `raw[72:80]`, still passes its `len(raw) >= 80` guard, and now reads a mix of `amount` and `staked_at` bytes, which `tier_for_balance` maps to a tier with no exception, no log, and no fail-closed.

Two corrections. The worst variant is neither of those — it is a **seed** change, and this repo has already executed one. Commit `b5e9868` changed the stake PDA seeds from `["stake", owner]` to `["stake", owner, mint]` (`lib.rs:187`) and the vault authority from a single global `["vault"]` to `["vault", mint]` (`lib.rs:194`). A seed change relocates the PDA, so pre-upgrade stake records are orphaned at an address the new code never derives — and decisively, the v1 vault tokens sit in the ATA of the `["vault"]` PDA while v2 signs only with `["vault", mint]` at `lib.rs:143`. No instruction in the deployed program can produce a signature for the old authority. Unlike field growth, this is not repairable by reverting the struct; it requires shipping a new rescue instruction. Had v1 held real TVL, that commit would have permanently stranded every staker's principal.

The second correction is that the claimed guard does not guard. `programs/rclaw_staking/README.md` asserts that `tests/test_token_tier_gate.py` "locks both the offsets and the mint filter." It does not lock the cross-language contract: the test builds its own fixture at `tests/test_token_tier_gate.py:131-136` using the same hardcoded offset 72 that it later asserts on at `:213`, and nothing under `tests/` reads `lib.rs` or the IDL. A Rust-side field reorder that preserves total size passes the entire Python suite green.

**Exploit / reachability.** Reachability is operational — an upgrade-authority action, not an attack — and no attacker-controlled accounts are involved. The concrete sequence for the field-growth variant: (1) v2 adds a field and bumps `SPACE`; (2) the upgrade is deployed; (3) Alice calls `unstake` with her pre-existing 89-byte `stake_account` PDA; (4) Anchor's `Account<StakeAccount>` runs the name-derived discriminator check, which *passes*, then Borsh-decodes the larger payload from the smaller buffer and errors during account validation; (5) the same failure occurs on `stake`, so she can neither add to nor exit her position. For the seed variant: (1) v2 changes the seeds; (2) every subsequent call derives a fresh, empty PDA, so `init_if_needed` happily creates a zeroed record and Alice's old balance is simply gone from the program's view; (3) the tokens themselves remain in the ATA of the old vault authority, for which the deployed program can never sign. State delta in both cases: the full vault balance becomes permanently unreachable, with no admin or migration path. I also probed the adjacent attacker angle and rejected it — an attacker cannot inject a forged program-owned account to poison the tier gate's `getProgramAccounts` scan, because `allocate`/`create_account` zero-initializes and only the owning program can write, so zeros at offset 8 never match the wallet memcmp filter at `tier_gate.py:161`.

**Remediation.** Four fixes, in order of value; the first two would have caught the `b5e9868` incident.

```rust
#[account]
pub struct StakeAccount {
    pub version: u8,          // NEW — bump on every layout change
    pub owner: Pubkey,
    pub mint: Pubkey,
    pub amount: u64,
    pub staked_at: i64,
    pub bump: u8,
}

impl StakeAccount {
    pub const CURRENT_VERSION: u8 = 1;
    // version(1) + owner(32) + mint(32) + amount(8) + staked_at(8) + bump(1)
    pub const SPACE: usize = 1 + 32 + 32 + 8 + 8 + 1;
    pub const RESERVED: usize = 64;   // headroom for in-place growth
}
```

with `space = 8 + StakeAccount::SPACE + StakeAccount::RESERVED` at `lib.rs:186`. Note this is itself a layout change that shifts every documented offset by one, so it must land *before* any value-bearing deployment and in the same commit as the matching `tier_gate.py` update. Second, make the cross-language contract machine-checked: add a CI step that parses the offsets out of the generated IDL (or the struct in `lib.rs`) and asserts they equal the constants `tier_gate.py` uses, replacing the self-referential fixture, and promote the inline literals at `tier_gate.py:161`, `:164`, and `:179` to named constants. Third, add a rescue path so an upgrade mistake is recoverable — at minimum an admin-gated instruction that can sign as the vault authority independently of `unstake`, ideally paired with a `migrate_stake_account` handler that takes the account as `UncheckedAccount`, reads the old layout manually, reallocs, and rewrites it, gated on the version byte. Adding an admin authority is itself a centralization tradeoff and should be a deliberate, documented decision. Fourth, fail closed off-chain: verify the 8-byte discriminator via an additional memcmp at offset 0, check the version byte, and skip any account the reader does not recognize. Items one and two belong on the Phase 0 Guardrails checklist in `docs/TOKEN_ROADMAP.md`, because after the first value-bearing deployment item one stops being a cheap fix and becomes the very migration the program cannot perform.

*Without this, any future layout or seed change — including one as ordinary as adding a reward field — permanently strands 100% of escrowed principal, and the reorder case does so silently, with the Python tier gate reading garbage and reporting a tier for it.*

---

### [MEDIUM] F-47 -- No automated enforcement exists for any Rust, Anchor, or Node code in this repo — ci.yml predates the staking program and runs only Python

**Severity.** Assigned from the gap sweep; no id existed in the original finding list, so this takes the next id in the F-4x range. This is not an on-chain vulnerability: no instruction sequence moves tokens as a result of it and nothing is drained by the absence of CI. The impact is second-order — it removes the safety net that would catch a reintroduced Critical. Held at Medium rather than Low for four reasons. The asset left ungated is the one guarding a demonstrated vault-drain Critical (commit `b5e9868`, PR #809). That exact regression has already occurred once in this repo and was missed by whatever human review exists, so the compensating control has a documented failure record on this specific bug class. The fix is trivial and technically unblocked — `cargo test -p rclaw_staking` needs no Solana toolchain, per `programs/rclaw_staking/README.md:123` — so there is no engineering tradeoff justifying the omission. And the roadmap intends mainnet, so the pre-deployment window is when this gate carries the most value. Not graded higher because the program is DRAFT/DEVNET, has never been deployed, and the $RCLAW mint does not yet exist, so there is no live value at risk today. This belongs in the process/SDLC category and should **not** be counted among the on-chain findings.

**Confidence.** High. CONFIRMED. Votes: 1 refuter (gap sweep).

**Location.** `.github/workflows/ci.yml:26-62`

**Code.**
```yaml
      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install -r requirements-ci.txt
...   # lines 30-54 elided: two ruff steps and the mypy ratchet, all Python-scoped
      - name: SAST — bandit (high severity, high confidence)
        run: bandit -r bot/ --severity-level high --confidence-level high -q

      - name: SCA — dependency vulnerability audit (pip-audit)
        run: pip-audit -r requirements.lock

      - name: Tests — baseline-diff regression gate (+ coverage floor)
        run: python scripts/ci_test_gate.py
```

**What's wrong.** `.github/workflows/ci.yml` is the only workflow file in the repo, and all five of its gates are Python-only: ruff (lines 36 and 43), mypy (50-53), bandit (56), pip-audit (59), and `python scripts/ci_test_gate.py` (62). Grepping the workflow for `cargo|anchor|npm|yarn|node |rust|solana|tsc|typecheck` returns zero matches. I searched exhaustively for an alternate gate and found none: `find .github -type f` returns `ci.yml` alone; there is no GitLab, CircleCI, Travis, Azure, or Jenkins config; no `CODEOWNERS`; no `.pre-commit-config.yaml` and no `.husky` (only the stock `.git/hooks/*.sample`); and the `Makefile`, `scripts/ci_test_gate.py`, `Dockerfile`, `deploy.sh`, and `docker-compose.yml` contain zero cargo or anchor references, so no Python step shells out to the Rust suite either. The root `package.json` defines `test` and `typecheck` scripts, but nothing invokes them.

The git history explains it: `ci.yml`'s only commit is `cee3133` (PR #768), while the staking program landed in `5dac977` (PR #801), the adversarial-audit fixes in `b5e9868` (PR #809), and the anchor TS tooling in `e64867b` (PR #816). The workflow has never been updated to know the program exists.

Consequently the entire chain of assurance here is manual: `programs/rclaw_staking/tests/attack.rs` — added specifically so the vault-drain fix would be *executed* rather than merely reasoned about — runs only when a human types the command; the TypeScript spec has never been executed; `npm run typecheck` is not gated; and `anchor build`, which is what actually applies the workspace `overflow-checks = true` release profile at `Cargo.toml:8-9`, is not gated either. The overflow protection this audit is asked to credit is never verified to compile.

One fact that strengthens the finding beyond the original report: the gap is a pure omission with no technical obstacle. `programs/rclaw_staking/README.md:123` documents `cargo test -p rclaw_staking` as running "unit + in-process integration tests (no toolchain needed)", and `attack.rs` runs the real program in-process via `solana-program-test`'s `processor!()` with no SBF toolchain, validator, or network. The `release.anza.xyz` egress block noted in that README constrains the authoring sandbox only and is irrelevant to a GitHub `ubuntu-latest` runner.

**Exploit / reachability.** This is a control gap, not an on-chain attack — there is no hostile caller, no account substitution, and no instruction sequence. The "call sequence" is a contributor workflow. Step 1: author a PR reverting `programs/rclaw_staking/src/lib.rs:187` from `seeds = [b"stake", owner.key().as_ref(), mint.key().as_ref()]` back to `seeds = [b"stake", owner.key().as_ref()]`, and `:194` from `seeds = [b"vault", mint.key().as_ref()]` back to `seeds = [b"vault"]`, restoring the PR #801 Critical verbatim. Nothing blocks authoring. Step 2: CI runs and passes — verified, no guard exists. A Rust-only diff touches nothing any of the five gates read: the two ruff steps scope to `bot/` and `tests/`, mypy targets explicit `bot/` modules, bandit scopes to `bot/`, pip-audit reads `requirements.lock`, and `ci_test_gate.py` has zero cargo/anchor/rclaw_staking references. The green check is guaranteed. Step 3, merge, is the one step I could not verify: branch-protection, required-status-check, and required-review settings live in GitHub repo settings rather than in the tree, and no `gh` CLI is available here to query them. That does not gate the verdict, because the finding as titled is a statement of fact about repository contents that I established by exhaustive enumeration, and because required human review is not automated enforcement in any case — it empirically failed on this exact bug, which shipped in #801 and survived until the adversarial audit caught it in #809. State delta: the only executable proof of the Critical fix is run by nothing automatic.

**Remediation.** Add a Rust/Anchor job. The critical step is `cargo test`, which needs only the Rust toolchain and runs on a stock runner.

```yaml
  staking:
    name: Staking program (cargo)
    runs-on: ubuntu-latest
    timeout-minutes: 20
    steps:
      - uses: actions/checkout@v4
      - uses: dtolnay/rust-toolchain@stable
        with:
          components: clippy, rustfmt
      - uses: Swatinem/rust-cache@v2

      # Runs tests/attack.rs in-process via solana-program-test.
      # This is the step that blocks a revert of the mint-scoped seeds
      # at src/lib.rs:187 and :194.
      - name: Tests — staking program (incl. vault-drain regression)
        run: cargo test -p rclaw_staking --all-targets

      - name: Lint — clippy (deny warnings)
        run: cargo clippy -p rclaw_staking --all-targets -- -D warnings

      # Compiles under [profile.release], where overflow-checks = true applies.
      - name: Build — release profile
        run: cargo build -p rclaw_staking --release

      - uses: actions/setup-node@v4
        with:
          node-version: "20"
          cache: npm
      - run: npm ci
      - run: npm run typecheck
```

Then mark the `staking` job a required status check in branch protection for `main`, since a green-but-skipped job still permits the merge. Update `CONTRIBUTING.md:9-13`, whose "Run the test suite before submitting" step currently names only `python -m pytest tests/ -v` and does not mention cargo or anchor at all — the sole documented contributor gate omits the Rust program entirely. Before mainnet, add a separate `anchor build` plus `anchor test` job (which does need the Anchor and Solana CLIs on the runner) to cover the SBF runtime that in-process `cargo test` explicitly does not exercise, and gate it on `RCLAW_PINNED_MINT` being set so the deployment pin is verified as part of the release path.

*Without this, the regression test written specifically to prove the vault-drain fix is never executed by anything automatic, and a PR reverting that fix merges with a full green check.*

---

### [LOW] F-07 -- The staked `mint` account carries zero constraints, so a Token-2022 transfer hook, freeze authority, permanent delegate or default-frozen state permanently bricks the vault with no recovery instruction

**Severity.** Downgraded from the reported Medium, and deliberately graded below what the title implies. Against the canonical $RCLAW mint the claimed impact is unreachable by construction, not by luck: all four bricking mechanisms are Token-2022 *mint* extensions or a freeze authority, and the mint is created at exactly `getMintLen([MetadataPointer])` with `freezeAuthority = null`, so none can ever be added post-init. Against an arbitrary mint (with `PINNED_MINT` unset) the sequence does execute, but the locked value is the attacker's own self-issued token held by users who receive no tier benefit for it, so realized loss is approximately zero and the attacker gains nothing — they can lock the tokens but never extract them. What survives is a genuine defense-in-depth gap with a deploy-time precondition: the safety of this program rests entirely on an off-chain invariant that the program never verifies on-chain, and whose off-chain verifier self-disables. CVSS-style: AV:N/AC:H/PR:H/UI:R/S:U/C:N/I:N/A:L — availability-only, high attack complexity, and it requires privileged deploy-time misconfiguration before it can touch anything of value.

**Confidence.** High. PLAUSIBLE. Votes: 1 sanity check.

**Location.** `programs/rclaw_staking/src/lib.rs:178-180`

**Code.**
```rust
    /// The mint being staked. Each mint is fully isolated (own vault, own
    /// stake records). Pin it via `PINNED_MINT` for a value-bearing deployment.
    pub mint: InterfaceAccount<'info, Mint>,
```

**What's wrong.** `mint` is declared with no constraints in both `Stake` (line 180) and `Unstake` (line 224). `InterfaceAccount<Mint>` validates only the owning program and that the data deserializes as a mint. The program never reads `mint.freeze_authority` and never inspects the Token-2022 extension TLV — a grep for hook, freeze, extension, or delegate across all of `programs/rclaw_staking` returns zero matches. Both transfer CPIs (`lib.rs:103-115` and `lib.rs:146-159`) use plain `CpiContext::new` / `new_with_signer` with exactly four account metas, and neither handler touches `ctx.remaining_accounts`, so spl-token-2022's `invoke_execute` cannot locate a hook program or its `ExtraAccountMetaList` in the CPI account slice and `transfer_checked` reverts. anchor-spl 0.30.1 ships `transfer_checked_with_transfer_hook` precisely for this and it is not used. There is also no admin recovery instruction, so any condition that makes the vault's outbound transfer fail is permanent.

The correction to the finding is about reachability of impact, not about the mechanism. For the canonical mint, `token/scripts/create_token.mjs:60` allocates the mint account at exactly `getMintLen([ExtensionType.MetadataPointer])`, and Token-2022 mint extensions can only be initialized between `SystemProgram.createAccount` and `InitializeMint` — there is no mint realloc (the `Reallocate` instruction covers token *accounts*, for account-type extensions). That permanently forecloses TransferHook, PermanentDelegate, TransferFeeConfig, DefaultAccountState, NonTransferable, and Pausable on that mint. `create_token.mjs:64` passes `freezeAuthority = null` into `InitializeMint`, and once the freeze authority is `COption::None`, `SetAuthority` cannot install one. The original report's most compelling step — flipping `TransferHookUpdate` from `program_id: None` to `Some(x)` after victims have staked — specifically requires the extension to have been initialized at mint creation, which never happens for $RCLAW.

I also checked whether the unpinned case hides something larger, and it does not. `vault_authority` is `PDA["vault", mint]` (lines 194 and 236), `vault` is the ATA of that authority with `associated_token::mint`/`authority`/`token_program` all enforced, `stake_account` is `PDA["stake", owner, mint]` with `has_one = mint` on `Unstake` (line 231), and `user_token_account.mint == mint` is constrained on both sides (lines 210 and 250). Substituting `mint` moves the caller wholesale into a different, fully isolated vault. There is no shared state across mints and no account-substitution angle.

The real residual is regression risk at deployment. Every guard above is off-chain or deploy-time; the on-chain code has none. And `token/scripts/verify_token.mjs:36` reads `onchain.freezeAuthority === null || !cfg.authorities.setFreezeAuthorityToNull` — so flipping the config flag to `false` makes the check pass vacuously rather than fail, meaning the off-chain verifier would not catch a regression in the very guard it exists to enforce. The same pattern is at `:31` for the mint authority.

**Exploit / reachability.** Path A, hostile third-party mint, executes but is zero-value. Precondition: the program is built with `RCLAW_PINNED_MINT` unset, which is the current source default, making `check_pinned_mint` at `lib.rs:92` a no-op via the early return at `lib.rs:68`. (1) The attacker creates Token-2022 mint M with space for the TransferHook extension and calls `InitializeTransferHook` with `authority = attacker, program_id = None`; transfers behave normally, so M is indistinguishable from a plain mint. (2) A victim calls `stake(M, N)`; every constraint passes and `transfer_checked` succeeds because `get_program_id(mint)` is `None`, so the vault accrues N. (3) The attacker submits `TransferHookInstruction::Update` on M setting `program_id = Some(X)`. (4) Every subsequent `unstake(M, ..)` reaches `lib.rs:146`, spl-token-2022's processor calls `onchain::invoke_execute`, the hook program and its `ExtraAccountMetaList` PDA are absent from the four-account CPI slice, and the instruction reverts; future `stake` calls fail identically at `lib.rs:103`. State delta: the vault ATA for `["vault", M]` is permanently sealed with no recovery instruction. Realized impact: the sealed balance is denominated in the attacker's own token, `bot/token/tier_gate.py:163-164` memcmps `mint` at offset 40 so it confers no tier, and the attacker cannot extract it. The program is the venue, not the vulnerability.

Path B, the canonical mint, dies at step 1 for the structural reasons above. The verdict is PLAUSIBLE because of one precondition that cannot be verified from source: whether the mint actually pinned into a production build was produced by this script with this config. A mainnet mint created under a different config, a bridged or wrapped variant, or simply `setFreezeAuthorityToNull` flipped to `false` would hand a single key the ability to freeze the vault ATA and hold the entire staked supply hostage indefinitely, with `verify_token.mjs:36` written so that the flag flip silently disables the check that would catch it.

**Remediation.** Move the invariant on-chain so it survives a bad mint choice, and make the check asymmetric — validate on the way in, never on the way out, so funds can always exit even if a mint's properties change later.

```rust
    /// The mint being staked. Rejected at entry if it can freeze the vault.
    #[account(
        constraint = mint.freeze_authority.is_none() @ StakeError::MintHasFreezeAuthority,
    )]
    pub mint: InterfaceAccount<'info, Mint>,
```

Add the matching `StakeError` variant, and deliberately do **not** add this constraint to `Unstake` at `lib.rs:224` — a constraint there would itself become a bricking vector. Then reject dangerous extensions at stake time, inside the handler rather than as an Anchor constraint since it needs the raw account data:

```rust
    let mint_ai = ctx.accounts.mint.to_account_info();
    let mint_data = mint_ai.try_borrow_data()?;
    let mint_state = StateWithExtensions::<SplMint>::unpack(&mint_data)?;
    for ext in mint_state.get_extension_types()? {
        require!(
            !matches!(
                ext,
                ExtensionType::TransferHook
                    | ExtensionType::PermanentDelegate
                    | ExtensionType::TransferFeeConfig
                    | ExtensionType::DefaultAccountState
                    | ExtensionType::NonTransferable
                    | ExtensionType::Pausable
            ),
            StakeError::UnsupportedMintExtension
        );
    }
    drop(mint_data);
```

The `TransferFeeConfig` entry closes an adjacent accounting hole the original finding did not raise: with a fee-bearing mint, `sa.amount` is credited the full `amount` at `lib.rs:120` while the vault receives `amount - fee`, so the record over-states the escrow and the last unstakers cannot withdraw. If hook mints must ever be stakeable, replace both CPIs with `transfer_checked_with_transfer_hook` and thread `ctx.remaining_accounts` through rather than relying on the exclusion list. Separately, fix the self-disabling verifier: assert the desired end state unconditionally (`onchain.freezeAuthority === null`) and let the config decide only whether a failure is fatal, not whether the check is evaluated. Finally, make "`RCLAW_PINNED_MINT` is set, and the pinned mint has a null freeze authority and no extensions beyond MetadataPointer" an explicit checked precondition in the Phase 0 checklist, verified against the live mint rather than against the local config file.

*Without this, the program's entire defense against a bricked vault lives in an off-chain script and a config flag that the off-chain verifier stops checking the moment the flag is flipped — so a single wrong mint at deploy time hands one key the ability to freeze the whole staked supply with no recovery path.*

---

### [LOW] F-10 -- Once an allowlist artifact exists on disk, presale:deposit refuses every non-whitelisted wallet forever — the public round is unreachable through the shipped CLI

**Severity.** The code defect is real and the control flow reproduces exactly, but the impact is availability-only, self-inflicted, and far narrower than the title suggests. There is no hostile caller and nothing on-chain is affected — the bucket, its allowlist `endTime`, and the deposit window are all configured correctly (`genesis_lib.mjs:181` and `:213`). The throw happens client-side at `genesis_presale.mjs:201`, before the `depositPresaleV2` call at `:207`, so no transaction is ever built. No funds, authority, mint, or PDA is at risk: confidentiality None, integrity None, availability partial and local. The blast radius is one machine — `token/.gitignore:3` ignores `.artifacts/`, so `allowlist.devnet.json` exists only where `presale:whitelist` was run, and a depositor who lacks the file is entirely unaffected because `readArtifact` returns null and the `if (wl)` block is skipped. The original "the raise is capped" impact does not follow: `cmdDeposit` also hard-requires the equally operator-local `presale.devnet.json` (`:183-184`), and RUNBOOK Path A presents the whole `plan → whitelist → create → liquidity → deposit → claim` chain as one operator's devnet smoke test; public participants deposit through the Genesis UI or their own client, which is unaffected. `rpcUrl` at `genesis_lib.mjs:56-65` additionally refuses any RPC URL containing "mainnet". I would raise this to Medium only if the team intends to hand this CLI to public participants as the deposit path, which nothing in the repo indicates.

**Confidence.** High. CONFIRMED. Votes: 1 sanity check.

**Location.** `token/presale/genesis_presale.mjs:196-205`

**Code.**
```javascript
  const wl = readArtifact(ALLOWLIST_ARTIFACT);
  if (wl) {
    const me = umi.identity.publicKey.toString();
    const hexProof = wl.proofByAddress?.[me];
    if (!hexProof) {
      throw new Error(`Wallet ${me} is not on the whitelist (no proof). Add it and re-run presale:whitelist, or wait for the public round.`);
    }
    input.proof = proofToPublicKeys(hexProof);
    console.log(`  presenting whitelist proof (${hexProof.length} nodes)`);
  }
```

**What's wrong.** The gate at line 197 is `if (wl)` — the mere existence of `token/.artifacts/allowlist.devnet.json` on the machine running the command. There is no comparison against `cfg.timeline.publicStart` anywhere in `cmdDeposit`. On chain the allowlist does expire: `genesis_lib.mjs:181` (`buildAllowlist`) and `:213` (`allowlistInitArgsFromArtifact`) both set `endTime = unix(cfg.timeline.publicStart)`, while `derivePresaleParams` opens the deposit window at `whitelistStart` when a whitelist is configured and closes it at `depositEnd`. The intended design is therefore allowlist-only over `[whitelistStart, publicStart)` and open over `[publicStart, depositEnd)`. The CLI implements only the first half, and the error message even instructs the user to "wait for the public round" — the state they are already in and which the code will never let them out of. With the committed timeline that is a 72-hour public window (2026-09-03 to 2026-09-06) the shipped `deposit` command can never enter.

The tell that the time branch was simply never written: `cmdDeposit` loads the config at `:180` (`const cfg = loadConfig();`) and then never references `cfg` again anywhere in lines 181-209. The object carrying `timeline.publicStart` is loaded and discarded at exactly the function that needs it.

A related variant not in the original report is more likely to actually bite: the artifact is unkeyed to any run or config and nothing ever deletes it. `token/e2e/devnet_dryrun.mjs` generates a near-now config, never runs `presale:whitelist`, and deposits at exactly `publicStart` (`:142-146`). A leftover `allowlist.devnet.json` from any earlier manual run therefore fails the e2e harness's deposit step even though the harness's own generated config has an empty whitelist — the derived config is passed via `GENESIS_CONFIG`, but the artifact path is unconditional.

The secondary variant the finder raised — `input.proof` being attached unconditionally at line 203 even after the on-chain allowlist has expired — is unverified. `@metaplex-foundation/genesis` is not installed in this checkout, so how the program validates a proof against a disabled allowlist cannot be read from source. It is also moot on this path, since the throw at line 201 fires first for exactly the wallets that would otherwise exercise it.

**Exploit / reachability.** Reachable, but the sequence is an operator self-DoS rather than an attack; no accounts are attacker-controlled and no instruction is dispatched. Two preconditions, neither present in the committed repo: `metaplex-genesis.config.json:59` is `"whitelist": []` and `cmdWhitelist` throws at `:215-217` on an empty array, so the artifact cannot be produced from the committed config without an operator first populating it; and the running machine must hold both `allowlist.devnet.json` and `presale.devnet.json`, both gitignored. Given those: (1) the operator adds addresses and runs `npm run presale:whitelist`, which writes the artifact at `:219` — a file with no expiry field, no copy of the timeline, and no deletion path. (2) `npm run presale:create` reads the same artifact at `:137` and, because `wl` is truthy, attaches `allowlistInitArgsFromArtifact` at `:155`, setting `endTime` to `publicStart`; the on-chain state is correct and does encode a public window. (3) The clock passes `publicStart`; on chain the allowlist is expired and the bucket accepts any depositor. (4) A non-whitelisted wallet on that machine runs `npm run presale:deposit -- --amount 1`; `readArtifact` at `:196` still returns the step-1 file, `if (wl)` is true purely on file existence, `wl.proofByAddress?.[me]` is `undefined`, and line 201 throws. State delta: none — zero instructions dispatched, zero lamports moved, zero account mutations, and a non-zero process exit with a message pointing at a state the user is already in.

**Remediation.** Gate the whitelist branch on time, not on file existence, and scope it to the actual allowlist window. `cfg` is already in scope at `:180` and currently unused.

```diff
   // During the whitelist window the depositor must present their Merkle proof.
   const wl = readArtifact(ALLOWLIST_ARTIFACT);
-  if (wl) {
+  const nowSec = Math.floor(Date.now() / 1000);
+  const publicStartSec = Math.floor(Date.parse(cfg.timeline.publicStart) / 1000);
+  const inWhitelistWindow = nowSec < publicStartSec;
+  if (wl && inWhitelistWindow) {
     const me = umi.identity.publicKey.toString();
     const hexProof = wl.proofByAddress?.[me];
     if (!hexProof) {
       throw new Error(
         `Wallet ${me} is not on the whitelist (no proof). Add it and re-run ` +
         `presale:whitelist, or wait for the public round (opens ${cfg.timeline.publicStart}).`
       );
     }
     input.proof = proofToPublicKeys(hexProof);
     console.log(`  presenting whitelist proof (${hexProof.length} nodes)`);
+  } else if (wl) {
+    console.log(`  public round is open (since ${cfg.timeline.publicStart}) — no proof required.`);
   }
```

This deliberately also fixes the secondary variant: after `publicStart` no `input.proof` is attached at all, which matches the expired on-chain allowlist and removes the untested proof-against-disabled-allowlist path entirely. Two hardening follow-ups are worth doing at the same time. Stamp the allowlist artifact with the timeline it was built against (`endTime`/`publicStart`) in `cmdWhitelist` at `:219-225` and prefer that stamped value over the live config when deciding the window, so a stale artifact is self-describing rather than silently authoritative. And key the artifact filename to the active config — derive it from a hash of the config path, or have the e2e harness clear `allowlist.devnet.json` when its generated config carries an empty whitelist — so the `GENESIS_CONFIG` override cannot pick up an allowlist from an unrelated run.

*Without this, any machine that has ever run `presale:whitelist` can never dry-run or execute a public-round deposit for a non-whitelisted wallet, and the e2e harness fails its deposit step for reasons its own config does not explain.*

---

### [LOW] F-12 -- Soft cap is derived but reaches no instruction — refundIfSoftCapMissed is an unenforceable on-chain promise

**Severity.** Graded Low, down from the reported Medium. There is no attack vector here at all: no adversary, no attacker-controlled account, no call sequence — this is a promised feature that no instruction implements. Attack complexity, privileges required and user interaction are therefore not applicable in the usual sense; the trigger is simply "the sale under-subscribes." The impact grade turns on what actually happens to depositor capital when it does, and the original finder got that wrong in kind. In a fixed-price presale a missed soft cap does not strand funds: the bucket still holds valid deposits and `claimPresaleV2` (`token/presale/genesis_presale.mjs:308-320`) still pays out tokens at the fixed `allocation / hardCap` price on the committed vesting schedule (33% at TGE via `cliffAmountBps` 3300, linear tail to TGE+60d). Confidentiality impact: none. Integrity impact: none — the program does exactly what it was configured to do. Availability impact on funds: none for the token leg; the SOL leg depends on a window question examined below. What remains is that `docs/TOKEN_ROADMAP.md:177` makes an unqualified public promise ("the sale is cancelled and contributions are **refundable**") that no code backs, which is a buyer-disclosure exposure rather than a loss of principal. It is not Informational, because shipping that promise to a mainnet sale unchanged would be a genuine misrepresentation to purchasers; it is Low with a hard deployment-gate note.

**Confidence.** High. PLAUSIBLE. Votes: 1 sanity check.

**Location.** `token/presale/genesis_lib.mjs:131-135`

**Code.**
```javascript
    // Fixed price is set by (allocation / cap): cap == hard cap in lamports.
    allocationQuoteTokenCap: solToLamports(cfg.sale.hardCapSol),
    softCapLamports: solToLamports(cfg.sale.softCapSol),
    perWalletMinLamports: solToLamports(cfg.sale.minContributionSol),
    perWalletMaxLamports: solToLamports(cfg.sale.maxContributionSol),
```

**What's wrong.** The data-flow claim is correct and I verified it end to end. `softCapLamports` is derived at `genesis_lib.mjs:133` and consumed by exactly one thing — a `console.log` at `genesis_presale.mjs:81`. It is absent from the `presaleInput` object handed to `addPresaleBucketV2` (`genesis_presale.mjs:138-153`), which carries only `baseTokenAllocation`, `allocationQuoteTokenCap`, the four time conditions, `minimumDepositAmount`, `depositLimit`, `claimSchedule`, and an optional `allowlist`. No on-chain account therefore records the soft cap, and no instruction can condition on it. Meanwhile `metaplex-genesis.config.json:24` sets `softCapSol: 1000`, `:28` sets `refundIfSoftCapMissed: true`, and the roadmap tells buyers the sale is cancelled and contributions refundable. A repo-wide grep for `endBehaviors|SendQuoteTokenPercentage|minRaise|min_raise` returns only two console warnings (`genesis_presale.mjs:99`, `:254`) — no refund, cancel, or min-raise instruction exists anywhere.

The correction to the original write-up is the impact, not the mechanism. This is a published-promise-without-implementation gap, not a fund-lockup bug. On a missed soft cap depositors are not stranded; they receive the tokens they paid for and lose only the promised *option* to take SOL back instead. The `withdrawPresaleV1` post-window question (below) determines which of two assets a depositor exits holding, not whether their capital survives. The project is honest about the gap in four places — `metaplex-genesis.config.json:29` (`_softCapNote`), `docs/TOKEN_ROADMAP.md:179-185` (an explicit "Correction (from building it)" block), `token/presale/RUNBOOK.md:83-87`, and a runtime notice printed at `genesis_presale.mjs:276` at the exact moment an operator runs the withdraw path — but honesty is not enforcement, and the named substitute ("an operational cancel-and-refund path") does not exist in this repo.

**Exploit / reachability.** There is no hostile call sequence, and that absence is the primary reason this is not Medium. The reachable sequence is entirely benign: the sale raises 400 SOL against a 1,000 SOL soft cap, `depositEnd` passes, depositors expect the promised cancel-and-refund, and no instruction implements it because `softCapLamports` was never written on-chain for any program to evaluate. Depositors then claim tokens normally via `claimPresaleV2`. `withdrawPresaleV1` (`cmdWithdraw`, `genesis_presale.mjs:258-277`) is a depositor-initiated self-cancel, but its availability window is defined by the Genesis program, not by this repo. This is the precondition I could not verify: `@metaplex-foundation/genesis` 0.40.0 is pinned in `token/package-lock.json` but is neither vendored nor installed (there is no `token/node_modules`), so whether `withdrawPresaleV1` remains callable after `depositEnd` cannot be read from source. `docs/TOKEN_ROADMAP.md:502` records that `create`/`deposit`/`claim`/`liquidity`/`withdraw` have "**never sent a transaction**," so the project has not validated it either. Nothing is deployed — there is no `token/.artifacts` directory — and `genesis_lib.mjs:56-65` refuses any RPC URL containing `mainnet`, so no funds are at risk today.

**Remediation.** This is a launch-gate item; resolve it before any mainnet deployment, in priority order. First, reconcile the promise with the implementation and pick one branch rather than shipping the current mismatch: either retract the guarantee (rewrite `docs/TOKEN_ROADMAP.md:177` to describe what the code actually does and set `metaplex-genesis.config.json:28` `refundIfSoftCapMissed` to `false`), or implement it (a min-raise `endBehaviors` extension on the presale bucket, or a signed, timelocked operational cancel-and-refund procedure naming the key holders and the deadline). Second, while the gap stands, make it unshippable rather than merely logged:

```javascript
// genesis_lib.mjs, in derivePresaleParams()
if (cfg.sale.refundIfSoftCapMissed) {
  throw new Error(
    'refundIfSoftCapMissed=true but no soft-cap/refund instruction is wired ' +
    '(softCapLamports reaches no on-chain account). Implement a min-raise ' +
    'endBehavior or set refundIfSoftCapMissed=false. See TOKEN_ROADMAP §13.'
  );
}
```

Third, drop `softCapLamports` from the return object at `genesis_lib.mjs:133`, or rename it `_softCapLamportsDisplayOnly`, so no future caller mistakes it for a value that ships on-chain — `cmdPlan` can print the soft cap from `cfg` directly. Fourth, before launch, exercise `withdrawPresaleV1` on devnet both inside and after the deposit window and record the result, since that answer bounds what any operational refund path can honestly promise.

*Without this, a mainnet sale opens with a published refund guarantee that no code can honour, and the only way to make good on it is a discretionary operator action that buyers have no on-chain claim to.*

---

### [LOW] F-14 -- The antiAbuse config block is inert and the per-wallet cap is Sybil-bypassable — the declared backendSigner control is implemented nowhere

**Severity.** Graded Low, down from the reported Medium. Attack vector is network-facing and requires no privileges — anyone can generate keypairs and deposit — but the impact is distribution concentration, not loss of funds, authority compromise, or availability. The attacker pays the full 5,000 SOL hard cap for the 150,000,000 `$RCLAW` allocation, so the protocol receives exactly the raise it configured and nothing is stolen. Confidentiality impact: none. Integrity impact: none — no state is corrupted and every deposit is valid under the configured rules. Availability impact: none. The unmitigated harm is that the anti-whale intent stated at `docs/TOKEN_ROADMAP.md:162` ("25 SOL / wallet (anti-whale)") is unenforceable, concentrating the 15% presale tranche in one hand and worsening holder concentration at TGE against a thin Raydium pool. Two further facts cap it: the finder's headline "dump 150,000,000 RCLAW at TGE" is arithmetically impossible under the committed vesting (`cliffAmountBps: 3300` releases 49.5M at TGE, the rest linear to TGE+60d), and the project already carries this on its own launch checklist at `docs/TOKEN_ROADMAP.md:407` ("- [ ] **Anti-snipe / anti-bot** at TGE; per-wallet caps enforced"). A gap the maintainer has itemised as an open pre-launch task is not a Medium audit finding. Nothing here reaches theft, authority takeover, or DoS, so no reading supports High.

**Confidence.** High. CONFIRMED. Votes: 1 sanity check.

**Location.** `token/presale/metaplex-genesis.config.json:53-58`

**Code.**
```json
  "antiAbuse": {
    "perWalletMaxViaDepositLimit": true,
    "perWalletMinViaMinimumDepositAmount": true,
    "whitelistViaAllowlistMerkle": true,
    "backendSignerOptional": true
  },
```

**What's wrong.** The presale has no Sybil resistance. The per-wallet floor and ceiling are enforced per *recipient pubkey*: `genesis_presale.mjs:266` derives deposit state with `findPresaleDepositV2Pda(umi, { bucket, recipient: me })`, so the SDK's own PDA finder takes the recipient as a seed and a fresh keypair deterministically yields a fresh deposit PDA with a fresh 25 SOL allowance. This is structural — the more rigorously the Genesis program enforces `depositLimit`, the more exactly the finding holds, because a per-recipient ceiling is by construction defeated by adding recipients. The one config-declared control that could bind a deposit to a vetted identity, `backendSignerOptional` (`:57`), is implemented nowhere: the deposit input at `genesis_presale.mjs:189-205` has no signer-verification field, and there is no server, KYC hook, or attestation path anywhere in `token/presale/`.

Two corrections to the original framing. First, the `antiAbuse` block is not a set of dead feature toggles hiding missing controls. Three of its four named behaviours are fully wired — `minimumDepositAmount` at `genesis_presale.mjs:149`, `depositLimit` at `:150`, and the merkle allowlist at `:154-157` backed by `buildAllowlist`/`prepareAllowlist` in `genesis_lib.mjs:178-204`. The key names read as an intent-to-primitive mapping index, not as switches: `perWalletMaxViaDepositLimit` literally means "the per-wallet max is implemented *via* the `depositLimit` primitive," and `backendSignerOptional: true` asserts that control is optional, so declining to implement it is consistent with the config rather than contradicted by it. The residual footgun is real but minor and fails safe: setting one of these to `false` would not disable the corresponding guard, so the guard is present when the config says off, never absent when the config says on. That is documentation hygiene, not a control gap. Second, the NTT sub-claim should be dropped entirely — `token/bridge/ntt.config.json:29-30` carries its own `_comment`: "NTT rate limits are set on-chain via the ntt CLI (outbound/inbound per 24h). Mirror the intended values here for review." The config declares itself a review mirror with the on-chain values set by the `ntt` CLI, so `ntt_bridge.mjs:77` merely printing them is the documented contract, not an inert-config defect.

**Exploit / reachability.** Reachable, and nothing on the path blocks it. The committed config has `"whitelist": []` (`metaplex-genesis.config.json:59`), so `cmdWhitelist` throws at `genesis_presale.mjs:215-217`, `allowlist.devnet.json` is never written, and `presaleInput.allowlist` is never set at `:154-157` — the bucket is created with no merkle allowlist at all and the sale is fully open from block one. The Sybil does not even need to defeat the allowlist gate. The sequence: (1) the operator runs `presale:create`, producing `initializeV2` plus `addPresaleBucketV2` with `minimumDepositAmount` 0.25 SOL and `depositLimit` 25 SOL; (2) the attacker generates 200 fresh Ed25519 keypairs offline — no allowlist, no registration, and no signature from any project-held key is required to become a valid depositor; (3) the attacker funds each with 25 SOL plus fees from one source, or through a hop chain to defeat naive clustering; (4) for each keypair the attacker calls `depositPresaleV2` (`genesis_presale.mjs:207`) with `amountQuoteToken = 25e9` lamports. The depositor identity (`umi.identity`) and, derived from it, the deposit PDA are attacker-controlled; `genesisAccount`, `bucket`, and `baseMint` are fixed. Every deposit satisfies both bounds, so all 200 succeed. The resulting state delta is 200 `PresaleDepositV2` PDAs under one entity totalling 5,000 SOL — the entire `allocationQuoteTokenCap` — and claim rights to 150,000,000 `$RCLAW`, released 49.5M at TGE and the balance linearly over 60 days. No dependency internal needs to be trusted for this conclusion, which is why the verdict is CONFIRMED despite `token/node_modules` being absent.

**Remediation.** Treat this as a pre-mainnet launch gate rather than an emergency fix. First, stop the booleans from reading as switches:

```diff
-  "antiAbuse": {
-    "perWalletMaxViaDepositLimit": true,
-    "perWalletMinViaMinimumDepositAmount": true,
-    "whitelistViaAllowlistMerkle": true,
-    "backendSignerOptional": true
-  },
+  "antiAbuse": {
+    "_comment": "DESCRIPTIVE INDEX ONLY — not read by any code. Maps intent to the Genesis primitive that implements it. Changing these values has NO effect; edit sale.* and whitelist instead.",
+    "perWalletMax": "wired via depositLimit (genesis_presale.mjs:150)",
+    "perWalletMin": "wired via minimumDepositAmount (genesis_presale.mjs:149)",
+    "whitelist": "wired via merkle allowlist (genesis_presale.mjs:154-157)",
+    "backendSigner": "NOT IMPLEMENTED — no Sybil/identity binding exists. See TOKEN_ROADMAP.md:407."
+  },
```

Second, for any mainnet launch make the allowlist mandatory rather than optional. Today `presaleInput.allowlist` is set only `if (wl)` (`genesis_presale.mjs:154`), and with an empty `whitelist` array the sale is open. Gate the entire deposit window behind a merkle allowlist of vetted addresses instead of expiring it at `publicStart` (`genesis_lib.mjs:181`) — a merkle allowlist is the only Sybil control the Genesis primitives here actually offer, and it correctly moves the identity problem off-chain to enrolment. Third, if a public round is required, accept that per-wallet caps are cosmetic and defend at enrolment with proof-of-personhood, KYC attestation, or a project-signed deposit authorisation, and stop describing the 25 SOL cap as "anti-whale" in buyer-facing material until one of those is live.

*Without this, one entity can take the entire presale allocation at the fixed price while the published tokenomics claim a 25 SOL per-wallet ceiling — a distribution outcome the launch materials directly contradict.*

---

### [LOW] F-15 -- presale:create is two non-atomic transactions with the artifact written only at the end — a partial failure strands the genesis account and destroys the ephemeral base-mint keypair

**Severity.** Graded Low, down from the reported Medium. There is no hostile caller, no attacker-controlled signer, and no depositor funds in scope — deposits are impossible until the second transaction lands, because `cmdDeposit` hard-requires the artifact at `genesis_presale.mjs:183-184` and the on-chain bucket must exist. This is an operator-side robustness and availability defect only, triggered by the operator's own crashed run. In CVSS-style terms: AV:L, AC:H, PR:H, UI:R, S:U, C:N, I:N, A:L. Blast radius today is bounded to devnet by the mainnet refusal at `genesis_lib.mjs:56-65`, and even hypothetically on mainnet the loss is rent for one genesis account plus one orphan mint with zero holders and zero market value. The reported Medium rested on a "permanently unrecoverable" premise that does not survive inspection (see below), and with that corrected this is a code-quality and ops-hardening item.

**Confidence.** High. CONFIRMED. Votes: 1 sanity check.

**Location.** `token/presale/genesis_presale.mjs:113-115, 158-170`

**Code.**
```javascript
  const transferMode = cfg.fundingMode.mode === 'transfer';
  const baseMint = transferMode ? publicKey(cfg.token.mint) : generateSigner(umi);
  const baseMintPk = transferMode ? baseMint : baseMint.publicKey;

// … lines 116-157 elided (initializeV2 at 122-131, PDA derivation at 133-134, presaleInput at 138-157) …

  console.log('[2/2] addPresaleBucketV2 — configuring the fixed-price presale…');
  await addPresaleBucketV2(umi, presaleInput).sendAndConfirm(umi);

  const artifact = {
    cluster: env.CLUSTER || 'devnet',
    program: GENESIS_PROGRAM_ID,
    baseMint: baseMintPk.toString(),
    genesisAccount: genesisAccount[0].toString(),
```

**What's wrong.** `cmdCreate` (`genesis_presale.mjs:105-176`) performs presale setup as two independently confirmed transactions — `initializeV2` at `:122-131` and `addPresaleBucketV2` at `:159` — with no bundling, no `try`/`catch`, no idempotency check, and no resume path, and it persists the run's only durable identifying state at `:170`, strictly after both transactions. In the committed default `mint` funding mode (`metaplex-genesis.config.json:15`), the base mint is `generateSigner(umi)` at `:114`, an ephemeral in-memory keypair whose secret is never written to disk. If the second transaction throws, the top-level `await table[cmd]()` at `:340` propagates, the process exits non-zero, no artifact is written, and re-running `presale:create` generates a brand-new mint at `:114` rather than resuming — silently creating a second, unrelated genesis account and orphaning the first one's rent. The same absence of a state machine runs across the whole sequence: `create` → `liquidity` → `deposit` → `claim` are four separate invocations, each checking only that `presale.devnet.json` exists, never that the on-chain bucket matches it.

The finder's mechanism is right but its impact chain is not. The orphaned account is *not* unrecoverable. `genesis_presale.mjs:119` prints `baseMintPk` to stdout before any transaction is built, and `findGenesisAccountV2Pda` at `:133` is a deterministic derivation, so the genesis account address is recoverable from the operator's terminal or CI log without a chain scan. The discarded ephemeral secret is irrelevant to recovery, because `addPresaleBucketV2` receives `baseMint: baseMintPk` at `:140` — a plain pubkey, not a signer — and the signer that matters is the durable operator keypair loaded from `./.keys/mint-payer.json` (`genesis_lib.mjs:67-75`) and installed as `umi.identity` at `:81`, which is the `authority` on `:124` and survives the crash on disk. The real cost is operator toil plus stranded rent through a manual, undocumented path, not permanent loss of an authority or of user funds.

**Exploit / reachability.** No attacker sequence exists; the only sequence is operator-driven and self-inflicted. The operator runs `npm run presale:create` (`token/package.json:14`), which enters `cmdCreate`; with the default `fundingMode.mode = "mint"` line `:114` takes the `generateSigner(umi)` branch; `initializeV2` confirms and the genesis account PDA is created with `authority: umi.identity`; then `addPresaleBucketV2` at `:159` throws on blockhash expiry, an RPC 429, an allocation/cap validation error, or a rejected `fundingMode` discriminant — the config itself flags that enum as unverified at `metaplex-genesis.config.json:14`. With no `try`/`catch` anywhere in `cmdCreate`, lines `:161-170` never execute. Re-running does not resume, and the e2e harness offers no protection either: `token/e2e/devnet_dryrun.mjs:139` simply shells out to the same `create` command. The resulting state delta is one orphaned genesis account (plus one orphaned mint in `mint` mode) with no artifact on disk, so every downstream command — `liquidity` (`:241`), `deposit` (`:184`), `claim` (`:312`), `withdraw` (`:262`), `withdraw-unsold` (`:284`) — fails its `No presale.devnet.json` guard. One sub-claim from the original write-up could not be checked: whether `initializeV2` mints `1e9 * 10^9` base units into the genesis account in `mint` mode is asserted rather than demonstrated, since `token/node_modules` is absent and the SDK internals are unreadable here. It does not change the grade — an orphan mint with no holders and no pool has nothing at risk.

**Remediation.** Make the two instructions atomic, which removes the failure window entirely. Umi builders compose:

```javascript
const builder = initializeV2(umi, { baseMint, authority: umi.identity, /* … */ })
  .add(addPresaleBucketV2(umi, presaleInput));
await builder.sendAndConfirm(umi);
```

This requires deriving `genesisAccount`/`bucket` from `baseMintPk` before sending, which lines `:133-134` already do deterministically — move them above the `initializeV2` call. If the combined transaction exceeds size limits once allowlist args are attached, fall back to defence in depth: persist identity *before* the first transaction by writing a partial artifact containing `baseMintPk` (and, in `mint` mode, the base-mint secret bytes) immediately after `:115`, marked `status: "pending"` and flipped to `"complete"` at `:170`; wrap `:122-159` in a `try`/`catch` that prints the derived `genesisAccount` and `bucket` addresses plus an exact resume command instead of dumping a raw unhandled rejection; add an idempotency check at the top of `cmdCreate` that reads any existing artifact and skips `initializeV2` rather than calling `generateSigner`, behind a `--resume` / `--base-mint <pubkey>` flag; and add a partial-failure recovery section to `token/presale/RUNBOOK.md`, which currently has none.

*Without this, a mid-run failure leaves an orphaned genesis account whose only record is a console line, and a second run under time pressure quietly publishes a different genesis address than the one already on chain.*

---

### [LOW] F-17 -- withdraw-unsold hardcodes the recipient to the signing key with no treasury parameter, contradicting the RUNBOOK and roadmap requirement for a Squads multisig destination

**Severity.** Graded Low, down from the reported Medium. The code excerpt and the documentation contradiction are both real, but the claimed exploit crosses no trust boundary. Attack vector is local — it requires possession of the operator keypair — and privileges required are High, because that key *is* the genesis authority set at `genesis_presale.mjs:124`. A principal holding it can already move the unsold allocation anywhere with about five lines of SDK code, with or without the hardcoded `recipient: me`. The incremental attacker capability contributed by this line is therefore zero, and no confidentiality, integrity, or availability impact is attributable to it. What remains is a genuine but non-exploitable governance and launch-readiness gap — the documented Squads-multisig destination is unreachable through the shipped tooling without a code edit — plus a real insider and coercion ergonomics point, since no destination is ever displayed for review. Severity is further bounded by the deployment gate at `genesis_lib.mjs:56-65`, which is real though soft. Low as a security finding, and legitimately a launch blocker in the Phase-0 checklist sense, which is how it should be read.

**Confidence.** High. PLAUSIBLE. Votes: 1 sanity check.

**Location.** `token/presale/genesis_presale.mjs:287-297` (the original report cited 286-303; the function opens at `:280` and `me` is assigned at `:287`)

**Code.**
```javascript
  const me = umi.identity.publicKey;
  const bucketTokenAccount = findAssociatedTokenPda(umi, { mint, owner: bucket });
  const recipientTokenAccount = findAssociatedTokenPda(umi, { mint, owner: me });
  console.log(`Withdrawing unsold presale tokens from bucket ${a.bucket} to ${me}…`);
  await withdrawUnsoldPresaleV1(umi, {
    genesisAccount: publicKey(a.genesisAccount),
    bucket,
    mint,
    bucketTokenAccount: bucketTokenAccount[0],
    recipient: me,
    recipientTokenAccount: recipientTokenAccount[0],
```

**What's wrong.** `cmdWithdrawUnsold` hardcodes the unsold-token destination to the signing identity — `recipient: me` at `:296` and the signer's own ATA at `:289` — with no `--recipient` flag and no treasury field in `metaplex-genesis.config.json`. The `arg()` helper is consumed only for `--amount` at `:185`, and the sole `--recipient` flag anywhere in the repo is at `token/bridge/ntt_bridge.mjs:134`, which shows the pattern exists and simply was not applied here. The documentation contradiction is verbatim: `token/presale/RUNBOOK.md:28` lists as a prerequisite "Treasury **Squads multisig** created; presale proceeds and unsold tokens flow to it," and `docs/TOKEN_ROADMAP.md:404` requires "**Squads multisig** treasury + **time-lock** on privileged actions; no single signer." Neither checklist item can be satisfied by running the documented commands.

The framing needs correcting, though, because it determines what the fix should be. Because the caller must already be the genesis authority (`authority: umi.identity` at `:124`), the hardcoded destination grants an attacker nothing — it constrains the operator, not the adversary. Grading this as an exploitable Medium would ship a finding whose obvious "fix," adding a `--recipient` flag, makes the arbitrary-destination case *easier* rather than harder. The substantive defect is the one buried at the end of the original description: every privileged presale action is single-signature — `initializeV2` (`:124`), `addPresaleBucketV2` (`:159`), `addRaydiumCpmmBucketV2` (`:244-251`), and `withdrawUnsoldPresaleV1` (`:291`) — and that lives at `:124`, not `:296`.

**Exploit / reachability.** The sequence is real but tautological. Preconditions: `token/.artifacts/presale.devnet.json` exists (guarded at `:284`); the caller possesses the keypair at `KEYPAIR_PATH`, default `./.keys/mint-payer.json` (`token/.env.example`, loaded at `genesis_lib.mjs:67-75` — note `token/.keys/` does not exist in the repo, so no key is committed); and `RPC_URL` does not contain the substring `mainnet`. Running `npm run presale:withdraw-unsold` enters `cmdWithdrawUnsold`, `makeUmi` sets `umi.identity` to that keypair, and a single `withdrawUnsoldPresaleV1` instruction moves the unsold base-token balance from the bucket ATA to the signer's ATA. The escalation dies at the authority binding: the Genesis program will only honour withdraw-unsold for the genesis authority, which is the same identity, so the only principal who can execute this already controls the genesis account outright. There is no account-substitution vector either — every account on the instruction is derived, the bucket and genesis account from the artifact and both token accounts via `findAssociatedTokenPda`. The verdict is PLAUSIBLE rather than CONFIRMED because of one precondition I could not verify from source: `@metaplex-foundation/genesis` is not installed, so the on-chain constraint set for `withdrawUnsoldPresaleV1` is unreadable here — specifically whether the program requires the genesis authority to sign, and whether it validates `recipientTokenAccount.owner == recipient`. If it did not require the authority signature, this would become a Critical against a different mechanism (any wallet drains unsold tokens to itself), but that would be a Metaplex program bug, not a defect in this file. Confirm against the deployed program at `GNS1S5J5AspKXgpjz6SvKL66kPaKWAhaGRhCqPRxii2B` before launch.

**Remediation.** Fix the authority model first and the destination second, in that order. Make the genesis authority a multisig at creation time so no single key is ever the authority:

```diff
// genesis_presale.mjs:122-131
+  const authority = cfg.treasury?.authority
+    ? publicKey(cfg.treasury.authority)   // Squads multisig vault PDA
+    : umi.identity.publicKey;
+  if (!cfg.treasury?.authority) {
+    console.warn('WARNING: authority is a single hot key. RUNBOOK.md:28 / TOKEN_ROADMAP.md:404 require a Squads multisig before launch.');
+  }
   await initializeV2(umi, {
     baseMint,
-    authority: umi.identity,
+    authority,
```

With a multisig authority the withdraw-unsold transaction must be proposed and co-signed through Squads, which is what the roadmap actually asks for and which no `--recipient` flag substitutes for. Then parameterise the destination so it is an explicit, reviewed address rather than an implicit self-send:

```diff
// genesis_presale.mjs:287-297
-  const me = umi.identity.publicKey;
-  const recipientTokenAccount = findAssociatedTokenPda(umi, { mint, owner: me });
+  const dest = arg('--recipient') || cfg.treasury?.address;
+  if (!dest) throw new Error('Refusing to withdraw unsold tokens: pass --recipient <TREASURY> or set treasury.address in metaplex-genesis.config.json. Self-send is not a default.');
+  const recipient = publicKey(dest);
+  const recipientTokenAccount = findAssociatedTokenPda(umi, { mint, owner: recipient });
...
-    recipient: me,
+    recipient,
```

Do not land the second change alone — shipping it without the first makes arbitrary-destination withdrawal easier while leaving the single-key authority intact. Add a `treasury: { address, authority }` block to `metaplex-genesis.config.json`, and add `presale:withdraw-unsold` to the "Confirm before launch" list at `token/presale/RUNBOOK.md:83`.

*Without this, the launch runs on a single hot key with a self-directed unsold-token path, and the two rug-resistance checklist items the project publishes to buyers cannot honestly be ticked.*

---

### [LOW] F-20 -- token/ installs with `npm install` and caret ranges on every package that signs privileged transactions — the committed lockfile is never enforced and no npm audit runs anywhere

**Severity.** Graded Low, down from the reported Medium. This is a supply-chain hardening and detection gap, not an exploitable defect in the repo's own code, and the two facts the original grade rested on do not hold. Exploitation requires an upstream npm registry or publisher compromise — an event entirely outside this repo's control and not something any caller can trigger — and the committed lockfile is fully in sync with `package.json`, pinning all 382 non-root entries to `registry.npmjs.org` with integrity hashes, so the documented `npm install` on a fresh clone does not re-resolve into the caret ranges. The residual gap only opens after a second, unverifiable precondition: the lockfile is deleted, merge-conflicted, or drifted. Attack complexity is therefore High, privileges required None but attacker-independent, and the confidentiality impact today is limited to devnet payer keys, since `token/scripts/lib.mjs:30-35` hard-refuses any mainnet RPC URL and `token/package.json:6` declares the tree DRAFT/DEVNET-ONLY. The impact the finding describes — "the raw secret key of every authority in the system" — only materialises after a mainnet deployment gate that has not been passed. Worth fixing, since it is a one-line change, but it should not sit above the authority findings in this report.

**Confidence.** High. PLAUSIBLE. Votes: 1 sanity check.

**Location.** `token/package.json:28-40`

**Code.**
```json
  "dependencies": {
    "@metaplex-foundation/genesis": "^0.40.0",
    "@metaplex-foundation/mpl-token-metadata": "^3.4.0",
    "@metaplex-foundation/mpl-toolbox": "^0.10.0",
    "@metaplex-foundation/umi": "^1.4.1",
    "@metaplex-foundation/umi-bundle-defaults": "^1.4.1",
    "@solana/spl-token": "^0.4.9",
    "@solana/spl-token-metadata": "^0.1.6",
    "@solana/web3.js": "^1.95.0",
    "@wormhole-foundation/sdk": "5.2.0",
    "@wormhole-foundation/sdk-evm-ntt": "7.2.0",
    "@wormhole-foundation/sdk-solana-ntt": "7.2.0"
  }
```

**What's wrong.** The premise that the committed lockfile "is never enforced" is incorrect as stated, and the correction matters. `token/package-lock.json` is `lockfileVersion` 3, its root `packages[""].dependencies` block is byte-identical to `token/package.json`'s, and every locked version satisfies its declared range (`@solana/web3.js` 1.98.4 ⊂ `^1.95.0`; `@solana/spl-token` 0.4.15 ⊂ `^0.4.9`; `@metaplex-foundation/umi` and `umi-bundle-defaults` 1.5.1 ⊂ `^1.4.1`; the rest at their floor). All 382 non-root entries resolve to `registry.npmjs.org` with a sha512 integrity hash, and there are no git or tarball dependencies. Under npm ≥ 7 an in-sync lockfile is authoritative for `npm install`, so a fresh clone installs the pinned safe versions rather than re-resolving. The cited December-2024 `@solana/web3.js` 1.95.6/1.95.7 precedent is misapplied — an in-sync lockfile pinning 1.95.5 *protected* against that payload on plain `npm install`.

The accurate finding is a two-part hardening gap. First, because `token/` is installed with `npm install` rather than `npm ci` at `token/README.md:14` and `:20`, `token/bridge/README.md:24`, `token/e2e/README.md:13`, `token/presale/RUNBOOK.md:39`, and `programs/rclaw_staking/README.md:125`, a deleted, merge-conflicted, or drifted lockfile silently re-resolves the caret ranges on the six packages that build and sign the mint, metadata, and presale transactions, instead of failing loudly. Note that `npm ci` closes only that range-drift path — it does not block lifecycle scripts, so it is not a defence against a malicious `postinstall`. Second, there is no Node-side SCA anywhere: `.github/workflows/ci.yml` is 62 lines of Python-only tooling (ruff, mypy, bandit, and `pip-audit -r requirements.lock` at `:58-59`) with zero Node setup, zero `npm ci`, and zero `npm audit`, so a known-vulnerable or yanked package in `token/` or the root Anchor toolchain would never be flagged. The Python tree gets dependency auditing and the Node tree gets none — an asymmetry worth closing on its own merits.

**Exploit / reachability.** There is no on-chain call sequence; this is an off-chain toolchain finding, so the relevant path is the install path. The reported path fails at its third step: after an upstream publisher compromise, an operator following `token/README.md:20` on a fresh clone does *not* get the malicious in-range version, because the in-sync lockfile pins the tree. The reachable variant requires two independent preconditions, neither verifiable from source. First, an upstream registry or publisher compromise of `@solana/web3.js`, `@solana/spl-token`, or a `@metaplex-foundation/*` package. Second, the `token/` lockfile is absent, stale, or drifted at install time — a merge conflict resolved by deleting it, a shallow copy of the directory, or an operator running `npm install <anything>`, which re-resolves and silently rewrites the lock rather than failing. Nothing in the repo detects or prevents that second condition. If both hold, the impact half of the chain is genuine rather than hypothetical: both signing entry points read raw secret-key bytes into the same Node process as these dependencies — `token/presale/genesis_lib.mjs:70-73` (`const secret = Uint8Array.from(JSON.parse(fs.readFileSync(abs, 'utf8')));` followed by `umi.use(keypairIdentity(kp))` at `:81`) and `token/scripts/create_token.mjs:38` (`const payer = loadKeypair(env);`). Today the state delta is exfiltration of a devnet throwaway payer keypair, since `token/scripts/lib.mjs:29-32` refuses any RPC URL containing `mainnet`.

**Remediation.** Two cheap changes, neither of which requires touching the dependency ranges. Switch every documented install to `npm ci` so lockfile drift fails loudly instead of silently re-resolving, at all six doc sites listed above:

```diff
-npm install
+npm ci                          # lockfile-exact; fails if package.json and the lock disagree
```

Then add a Node job to `.github/workflows/ci.yml` so `token/` gets the same SCA treatment the Python tree already has:

```yaml
  node-audit:
    name: token/ — lockfile + dependency audit
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: '20'
          cache: npm
          cache-dependency-path: token/package-lock.json
      - name: Install lockfile-exact (fails on drift)
        run: npm ci --prefix token
      - name: SCA — npm audit
        run: npm audit --audit-level=high --prefix token
```

`npm ci` in CI is what makes the committed pin authoritative on every PR and turns "someone regenerated the lockfile" into a visible diff plus a failing job. As optional hardening for the mainnet gate, pin the six caret ranges to exact versions to match the Wormhole entries at `:37-39`, and consider `npm ci --ignore-scripts` for the signing scripts, since neither `npm ci` nor `npm install` blocks `postinstall` on its own.

*Without this, a lockfile that drifts for any reason silently widens the trusted set to whatever the registry serves next, in the same process that holds the mint and presale authority keys, and no job in this repo would notice.*

---

### [LOW] F-21 -- Token metadata URI is a mutable, squattable GitHub `main`-branch URL baked immutably into the mint

**Severity.** Graded Low, down from the reported Medium. Impact is display-layer only: no token loss, no authority compromise, no availability loss on chain. Supply, balances, transfers, and the presale and tier-gate logic are unaffected by what the URI resolves to; the realistic harm is brand impersonation or phishing, via a wallet or explorer rendering an attacker-supplied name, image, or `external_url` for the mint. Three factors hold the grade down. The finding's core "unrecoverable" premise is wrong — the Token-2022 metadata update authority is set to the payer at `token/scripts/create_token.mjs:93` and is never revoked by this script, which revokes only `AuthorityType.MintTokens` at `:121`, so the team can repoint the URI at any time with a `TokenMetadataInstruction::UpdateField`. Both exploit paths require a precondition an external attacker cannot force: either the project itself renaming, deleting, or transferring the GitHub org, or insider repository write access. And the whole path is devnet-gated by the hard refusal at `token/scripts/lib.mjs:30-35` plus `"cluster": "devnet"` at `token/config/token.config.json:17`. Even on mainnet this remains a metadata-presentation issue with an existing on-chain remedy, so the deployment-gate rule does not lift it — with one exception noted below, which is a sequencing defect rather than a code defect.

**Confidence.** High. PLAUSIBLE. Votes: 1 sanity check.

**Location.** `token/config/token.config.json:7`

**Code.**
```json
  "metadataUri": "https://raw.githubusercontent.com/Humanoid-Traders/RUNECLAW/main/token/config/rclaw-metadata.json",
```

**What's wrong.** This URI is read as `cfg.metadataUri` into `metadata.uri` at `token/scripts/create_token.mjs:55` and written into the Token-2022 metadata extension at `:98`; the metadata pointer targets the mint itself (`:75-80`), so the URI genuinely lives in the mint account. The same mutable `main`-branch URL is duplicated at `token/presale/metaplex-genesis.config.json:10` (consumed at `genesis_presale.mjs:130`) and, for the image, at `token/config/rclaw-metadata.json:5`. Nothing in the repo uses content-addressed or immutable hosting — there is no Arweave, IPFS, or commit-SHA pin anywhere in the token tooling. `/main/` resolves to whatever is on the default branch right now, so the token's displayed name, description, and image can change with no on-chain event and no version history a wallet can see. Separately, the URL is only as trustworthy as GitHub's ownership of the `Humanoid-Traders` org and the `RUNECLAW` repo name (confirmed as the live remote), which becomes free to register if the org is renamed, deleted, or transferred.

The correction is the load-bearing part of the reported severity. The finding states "Mint authority is revoked and freeze is null, so no one — not the team, not holders — can change the URI." On Solana the mint authority, the freeze authority, and the Token-2022 metadata update authority are three distinct authorities on the same mint account. `create_token.mjs:121` revokes only `AuthorityType.MintTokens`, which governs supply, and freeze is nulled at `:64`; neither touches metadata. `updateAuthority` appears exactly once in the repo, at `:93`, and is never set to null anywhere. A namespace takeover is therefore recoverable today with a single `UpdateField` transaction. The accurate primary concern is a privilege-boundary widening: changing the token's displayed identity on chain requires the update-authority keypair and produces an auditable transaction, whereas changing it through this URL requires only merge rights on the default branch and produces no on-chain record at all — two different, differently-sized, differently-audited sets of people. Also worth noting in passing, `token/scripts/verify_token.mjs:39-40` asserts only `meta.name` and `meta.symbol` against the config and never `meta.uri`, so the post-deploy verifier would not catch a wrong or substituted URI.

**Exploit / reachability.** There is no Anchor program, instruction handler, or account struct on this path — it is entirely off-chain deploy config consumed by a Node script — so the sequence is a deploy sequence. Path A, insider: the token is created via `create_token.mjs`, freezing the URI into the mint; anyone with merge rights to the default branch then pushes a one-line change to `token/config/rclaw-metadata.json`, and every wallet and explorer that dereferences the mint's URI serves the new name, description, image, and `external_url` — with no transaction, no signature from the update authority, and no on-chain event. Path B, namespace takeover: the project renames the org, archives, or transfers the repo (an action only the project can take); an attacker who has been watching — the URL is publicly readable in the mint's own metadata — registers `Humanoid-Traders/RUNECLAW` with a `main` branch containing `token/config/rclaw-metadata.json` and `website/app_icon_512.png`, producing the same rendering hijack. The verdict is PLAUSIBLE because neither path is attacker-forceable from source: Path A needs insider repository access and Path B needs the project to relinquish the GitHub namespace, and `raw.githubusercontent.com` redirect behaviour after a rename is GitHub-side behaviour I cannot verify from this repo. Under either path today the team recovers by calling `UpdateField` with the payer key.

**Remediation.** Fix before any mainnet mint. Host the metadata immutably and content-address it — upload `rclaw-metadata.json` and the image to Arweave/Irys or IPFS and pin the resulting URI; if GitHub must serve as an interim host, pin the full 40-character commit SHA instead of `main` so the bytes cannot change under the mint:

```diff
  token/config/token.config.json:7
- "metadataUri": "https://raw.githubusercontent.com/Humanoid-Traders/RUNECLAW/main/token/config/rclaw-metadata.json",
+ "metadataUri": "https://arweave.net/<TX_ID>",
```

Apply the identical change to `token/presale/metaplex-genesis.config.json:10` and to the image URL at `token/config/rclaw-metadata.json:5` — a `/main/` image is the same problem, and the image is what users actually recognise. Make the verifier assert the URI so a mismatch is caught post-deploy:

```diff
  ['metadata symbol', meta ? meta.symbol === cfg.symbol : false, meta ? meta.symbol : '(none)'],
+ ['metadata uri',    meta ? meta.uri    === cfg.metadataUri : false, meta ? meta.uri    : '(none)'],
```

Most importantly, sequence the launch runbook explicitly: `docs/TOKEN_ROADMAP.md:408` commits to "**Metadata immutability** or multisig-gated update authority, renounced post-launch," and the update authority may only be renounced *after* the URI has been repointed to immutable, content-addressed storage. Until renouncement, hold that authority in the Squads multisig named on the same checklist rather than the single payer key assigned at `create_token.mjs:93`. As optional hardening, record the metadata JSON's SHA-256 in an `additionalMetadata` entry (`token/config/token.config.json:8-12`) so the off-chain document is bound to the mint even if the host is later compromised.

*Without this, anyone with merge rights can silently change what every wallet displays for the token, and executing the roadmap's renounce step in the wrong order makes that hijack permanent by removing the only recovery path.*

---

### [LOW] F-22 -- declare_id! and Anchor.toml both ship the well-known Anchor placeholder program id, with no gate requiring `anchor keys sync`

**Severity.** Attack vector is local and operator-only; there is no remote or on-chain caller who can trigger this. Attack complexity is low but privileges required are high (the repo operator running their own build), and user interaction is required. The confirmed impact is availability of the program itself, not confidentiality or integrity of funds: a deploy that skips `anchor keys sync` lands the program at the generated keypair while `ID` still holds the placeholder, so every instruction fails Anchor's injected program-id check. That failure is self-inflicted, surfaces on the very first transaction, costs zero tokens (the vault is necessarily empty, because no `stake` can ever succeed), and is fully recoverable with `anchor keys sync` plus a rebuild, or `anchor deploy --program-id`. I am grading this down from the Medium a reader would expect from the title because the only security-weighted path -- tier escalation through a hostile program at the placeholder address -- rests on three independent preconditions, one of which (someone hostile actually holding the private key for `Fg6Pa…` on the configured cluster) cannot be settled from source, and which, if false, makes the misconfiguration fail **closed**: an empty `getProgramAccounts` result yields `0.0` staked, `basic` tier, and premium denied at `bot/token/tier_gate.py:214-236`. Nothing in the code wires the placeholder into the gate -- `staking_program()` at `bot/token/tier_gate.py:78` is `return _env("RCLAW_STAKING_PROGRAM")` with no default.

**Confidence.** High. PLAUSIBLE. Votes: 1 sanity check.

**Location.** `programs/rclaw_staking/src/lib.rs:41`

**Code.**
```rust
declare_id!("Fg6PaFpoGXkYsidMpWTK6W2BeZ7FEfcYkg476zPFsLnS");
```

**What's wrong.** The repository publishes the Anchor documentation placeholder program id `Fg6PaFpoGXkYsidMpWTK6W2BeZ7FEfcYkg476zPFsLnS` as its canonical program address in two places -- `programs/rclaw_staking/src/lib.rs:41` and `Anchor.toml:10` (`rclaw_staking = "Fg6PaFpoGXkYsidMpWTK6W2BeZ7FEfcYkg476zPFsLnS"`) -- holds no keypair for it, and enforces `anchor keys sync` in no gate. There is no source-versus-manifest divergence here; both strings are identical. The problem is the value: it is the address every Anchor template ships with, and the project provably does not control it. The mitigation appears exactly twice, both times unenforced: as an ungated line in a README build snippet (`programs/rclaw_staking/README.md:128`) and as a Known Limitation (`:150`). `.github/workflows/ci.yml` contains no Rust or Anchor steps at all -- it is Python-only (ruff, mypy, bandit, pip-audit) -- and the authoritative pre-deploy checklist at `docs/TOKEN_ROADMAP.md:517-522` lists audit, `anchor build && anchor test`, `RCLAW_PINNED_MINT`/`RCLAW_MINT` and §13 ratification, but not `anchor keys sync`.

The primary, confirmed consequence is a bricked deploy. An operator following the README build snippet who skips line 128 deploys to the freshly generated `target/deploy/rclaw_staking-keypair.json` pubkey while `ID` stays `Fg6Pa…`. anchor-lang 0.30.1's generated `try_entry` compares `*program_id` against `ID` before dispatch and returns `DeclaredProgramIdMismatch`, so both `stake` and `unstake` fail unconditionally. The result is a rent-paid, permanently inert program.

The secondary consequence -- tier escalation -- is mechanically correct but should not carry the finding's weight. It requires the operator to hand-copy `Anchor.toml:10` into `RCLAW_STAKING_PROGRAM`, an environment variable with no default and which no document instructs them to source from the build manifest, *and* a hostile party to hold that address's private key on the configured cluster (devnet by default; mainnet RPCs are refused at `bot/token/tier_gate.py:100-104`). I did not verify whether that private key is publicly known and make no claim that it is.

The genuinely valuable adjacent observation, which the original finder buried inside the escalation story: `staked_of` at `bot/token/tier_gate.py:161-179` filters on owner@8 and mint@40 but applies **no** memcmp on the 8-byte Anchor discriminator at offset 0, and no shape check beyond `len(raw) >= 80`. It will therefore decode `raw[72:80]` out of any sufficiently long account owned by whatever program id it is pointed at. That is inert today -- `StakeAccount` (`programs/rclaw_staking/src/lib.rs:257-264`) is the program's only `#[account]` type, and vault ATAs are owned by the Token program, so `getProgramAccounts` against the real staking program can only ever return `StakeAccount`s -- but it is the single guard whose absence makes a wrong program id escalatory rather than merely broken.

**Exploit / reachability.** Path (a), the default path, is fully demonstrable. The operator runs `anchor build`, which generates a fresh random keypair at `target/deploy/rclaw_staking-keypair.json` (Anchor 0.30.1 does not rewrite `declare_id!` -- that is precisely why `anchor keys sync` exists), skips the ungated sync line, then runs `anchor deploy`. The program lands at the generated pubkey; `ID` is unchanged. Every subsequent `stake`/`unstake` hits the generated program-id comparison and errors out. State delta: a live, rent-paid, permanently non-functional program with an empty vault. Nothing in CI or the roadmap checklist catches this before users are pointed at it.

Path (b) is the PLAUSIBLE half. The chain is: (1) operator sets `TOKEN_TIER_GATE_ENABLED=true` and `RCLAW_MINT`, both required by `gate_enabled()` at `bot/token/tier_gate.py:85-91`; (2) operator sets `RCLAW_STAKING_PROGRAM` to the id copied from `Anchor.toml:10` rather than their real deploy address; (3) a hostile party holds the private key for `Fg6Pa…` on the configured cluster and deploys a program there; (4) the attacker links a wallet `W` to their own Telegram uid, then has their program create an account laid out `<anything> @0 | W @8 | RCLAW_MINT @40 | u64::MAX @72`, length >= 80. `staked_of` then memcmps owner@8 and mint@40 -- both trivially forgeable as raw bytes in an attacker-owned account -- decodes `raw[72:80]`, and `allows_user` grants elite, yielding free `/scalp`, `/intraday` and `/swing` access with zero $RCLAW staked. **Precondition (3) is what I could not verify from source**, and it is load-bearing: if no hostile party holds that key, `getProgramAccounts` returns empty, `staked_of` returns `0.0` (not `None`, so no fail-open), and the misconfiguration over-restricts rather than escalating.

**Remediation.** Two independent fixes; the second is the higher-value one because it holds regardless of key custody.

First, gate the keys-sync step so a bricked deploy cannot ship silently. Add `anchor keys sync` as an explicit numbered item to the pre-deploy checklist at `docs/TOKEN_ROADMAP.md:515-522`, and if any Rust/Anchor job is ever added to CI, fail the build on the placeholder:

```yaml
# in a build job, after `anchor build`
- name: Reject the Anchor placeholder program id
  run: |
    ! grep -q 'Fg6PaFpoGXkYsidMpWTK6W2BeZ7FEfcYkg476zPFsLnS' \
      programs/rclaw_staking/src/lib.rs Anchor.toml
```

A cheaper interim measure is a `compile_error!`-style guard in `lib.rs` -- gate the placeholder behind the same `option_env!` pattern already used for `RCLAW_PINNED_MINT`, so a release build without a real id simply fails to compile.

Second, sever the escalation chain in `bot/token/tier_gate.py` by adding the discriminator filter alongside the existing owner/mint ones:

```python
# StakeAccount's Anchor discriminator = first 8 bytes of
# sha256("account:StakeAccount"); base58-encode and filter on it.
filters = [
    {"memcmp": {"offset": 0, "bytes": _STAKE_ACCOUNT_DISC_B58}},
    {"memcmp": {"offset": 8, "bytes": wallet}},
]
```

and tighten `len(raw) >= 80` to the exact `StakeAccount` size (`== 8 + 32 + 32 + 8 + 8 + 1 == 89`, per `StakeAccount::SPACE` at `programs/rclaw_staking/src/lib.rs:268`) so arbitrarily shaped accounts are rejected outright.

Third, cosmetically but usefully, replace the literal at `Anchor.toml:10` with an obviously invalid sentinel plus a comment, so an operator who copies it into `RCLAW_STAKING_PROGRAM` gets an immediate error rather than a plausible-looking address.

*Without the first fix, the first real deployment is a rent-paid program on which no user can ever stake or unstake; without the second, the tier gate will decode a staked balance out of any account of sufficient length owned by whatever program id it is pointed at.*

---

### [LOW] F-24 -- bridge:transfer builds a transfer and discards it, then prints a success message -- nothing is signed, sent or redeemed, and the config documents a VAA/redeem step that does not exist

**Severity.** AV:L / AC:L / PR:H (repo operator only) / UI:R / S:U / C:N / I:N / A:N. There is no attacker anywhere in this path -- the only actor is the operator running their own CLI. No key is held, no transaction is signed, no instruction is submitted, and not one token moves in any direction, so confidentiality, integrity of on-chain state, and availability of the token program are all untouched. What remains is a dead-code no-op, a factually false console line, and a contradictory config comment: a correctness and documentation defect in draft off-chain tooling, not a vulnerability. I grade it Low rather than Informational only because the false "Transfer instruction stream built." message combined with the `ntt.config.json:27` claim that "the script fetches the VAA and submits the redeem" creates a genuine operator-misunderstanding surface on money-moving tooling, and because this file is the seed of a future mainnet bridge. It is explicitly **not** Medium: the original escalation (tokens locked on the hub with no release) requires a signer that does not exist anywhere in the repository, and even in that hypothetical, NTT VAAs are permanently redeemable, so no loss-of-funds outcome exists.

**Confidence.** High. CONFIRMED. Votes: 1 sanity check.

**Location.** `token/bridge/ntt_bridge.mjs:134-140`

**Code.**
```javascript
  const xfer = srcNtt.transfer(sender, units, { chain: dstCtx.chain, address: arg('--recipient') || sender }, {
    queue: false,
    automatic: !!cfg.transfer.automatic,
  });
  console.log('Transfer instruction stream built. Sign + send with signSendWait once a signer is wired.');
  void signSendWait; // documented entry point for the operator's signing step
  void xfer;
```

**What's wrong.** `srcNtt.transfer(...)` returns an `AsyncGenerator<UnsignedTransaction>`. Because async generator bodies are lazy, the body does not execute until the first `.next()` or `for await`, and there is no `await`, no `for await`, and no `.next()` on `xfer` anywhere in the file -- line 140 simply discards it with `void xfer;`. Not one instruction is constructed, let alone signed. The message printed at line 138, "Transfer instruction stream built.", is therefore factually false. `signSendWait` is imported at line 27 and immediately no-op'd at line 139. `cmdTransfer` exits 0 having produced only stdout.

Two secondary accuracy defects sit on the same path. `token/bridge/ntt.config.json:27` documents a destination-side step the tooling does not implement: `"_automaticNote": "false = manual relaying: the script fetches the VAA and submits the redeem on the destination chain. true requires a relayer to be configured for the route."` There is no `wh.getVaa`, no destination-side NTT protocol handle, and no redeem call anywhere under `token/` -- `dstCtx` (line 118) is used only for the log line at 125 and the chain tag at 134. Note that `token/bridge/README.md:52-53` states the opposite and correct thing ("signing and sending stay with the operator"), so the two documents disagree with each other. Separately, line 134 defaults the cross-chain recipient to the source-chain sender (`address: arg('--recipient') || sender`), i.e. a Solana base58 string offered as a Base Sepolia recipient. That is unreachable in practice -- the generator is never iterated, and the SDK's address parsing would reject a base58 string as an EVM address rather than misroute -- but it is a poor default on the one parameter that decides where tokens land.

The impact claimed by the original finder ("tokens would sit locked on the hub with no spoke-side mint and no redeem path") is not reachable. No signer exists anywhere in the repository, the tool never holds a key, mainnet is hard-refused, and NTT VAAs do not expire, so even a real lock without redeem is a deferred claim rather than a loss.

**Exploit / reachability.** There is no hostile call sequence; the defect fires on the intended operator path. The operator completes the `ntt` CLI deploy per `token/bridge/README.md:33-39` and fills `hub.token`, `hub.manager`, `spokes[0].token`, `spokes[0].manager` in `ntt.config.json`, so `validate()` (`ntt_bridge.mjs:48-62`) returns ready. They run `npm run bridge:transfer -- --amount 1000 --sender <solana addr>` (`token/package.json:23` maps to `node bridge/ntt_bridge.mjs transfer`). `cmdTransfer` passes the ready gate at 94-96, lazily imports the NTT protocol packages at 104-114 -- in plain Node ESM this throws first, per the documented upstream `sdk-solana-ntt` packaging bug noted at 15-22; under `npx tsx` it proceeds -- builds `srcNtt` at 120-122, parses units at 124, and requires `--sender` at 130-132. Line 134 creates the generator; line 140 discards it. The console reads "Bridging 1000 $RCLAW: Solana → BaseSepolia" followed by "Transfer instruction stream built."; the process exits 0.

State delta: **none**. Zero on-chain changes on Solana or Base Sepolia. No account created, no lamports or SPL tokens moved, no mint, freeze or upgrade authority touched, no PDA written. The only real-world effect is stdout. The operator who then follows `ntt.config.json:27` and waits for the script to fetch the VAA and submit the redeem waits forever, because that code does not exist.

**Remediation.** Three small, independent fixes, all in off-chain tooling.

First, stop lying in the log, or actually drive the generator:

```diff
   const xfer = srcNtt.transfer(sender, units, { chain: dstCtx.chain, address: arg('--recipient') || sender }, {
     queue: false,
     automatic: !!cfg.transfer.automatic,
   });
-  console.log('Transfer instruction stream built. Sign + send with signSendWait once a signer is wired.');
-  void signSendWait; // documented entry point for the operator's signing step
-  void xfer;
+  // Materialize the lazy generator so we actually know the transfer builds.
+  const txs = [];
+  for await (const tx of xfer) txs.push(tx);
+  console.log(`Built ${txs.length} unsigned transaction(s). NOT SIGNED, NOT SENT.`);
+  console.log('To send: wire a Signer and call signSendWait(srcCtx, xfer, signer) — see bridge/README.md.');
+  // signSendWait is imported for the operator's signing step; unused until a signer is wired.
+  void signSendWait;
```

If wiring a signer is genuinely out of scope for this draft, delete the `srcNtt.transfer` call entirely and have `transfer` print "not implemented -- use the ntt CLI", so no reader can mistake a no-op for a build.

Second, fix the contradicting config comment at `token/bridge/ntt.config.json:27`, which currently promises a redeem this tooling does not perform:

```diff
-  "_automaticNote": "false = manual relaying: the script fetches the VAA and submits the redeem on the destination chain. true requires a relayer to be configured for the route."
+  "_automaticNote": "false = manual relaying: the VAA must be fetched and the redeem submitted on the destination chain BY THE OPERATOR (this tooling does not do it — use the ntt CLI). true requires a relayer to be configured for the route."
```

Third, do not default the cross-chain recipient to the source-chain sender; require it explicitly, mirroring the existing `--sender` guard at 130-132:

```diff
+  const recipient = arg('--recipient');
+  if (!recipient) {
+    throw new Error(`Pass --recipient <${dstCtx.chain} address> — the destination address is never inferred from --sender across a chain boundary.`);
+  }
-  const xfer = srcNtt.transfer(sender, units, { chain: dstCtx.chain, address: arg('--recipient') || sender }, {
+  const xfer = srcNtt.transfer(sender, units, { chain: dstCtx.chain, address: recipient }, {
```

Deployment gate: fixes one and three must land before this script is run against any network holding real value. The existing `cfg.network !== 'Testnet'` refusal at `ntt_bridge.mjs:58-60` is what currently makes this safe -- keep it until the destination-side redeem story is written down and reviewed.

*Without these, an operator reading only the console output and `ntt.config.json` will believe a bridge transfer was built, signed and redeemed when nothing happened at all, and the first person to wire a signer inherits a recipient default that silently points at a source-chain address.*

---

### [LOW] F-25 -- Fixed 100M-token LP allocation paired against 60% of a variable raise opens the DEX pool at least 10% below the presale price, and ~82% below at the soft cap

**Severity.** There is no attacker, no unauthorized state transition, and no authority or availability impact -- this is a deploy-time tokenomics parameter defect, not a vulnerability. Nothing on this path can be triggered by a hostile caller; the harm materializes only if a human operator ratifies these exact numbers and ships to mainnet. Three independent gates stand between the committed config and the claimed impact: mainnet is hard-refused at `token/presale/genesis_lib.mjs:56-65`, the config self-labels as an unratified proposal at `metaplex-genesis.config.json:2`, and both governing inputs -- the presale price and the "Liquidity split of raised SOL (60% assumed)" -- are listed as open, unratified decisions at `docs/TOKEN_ROADMAP.md:437-439`. The impact if all three gates are passed is buyer-facing economic harm (the first unlocked tranche meets a pool marked below entry) against a permanently locked, unwindable LP, which keeps this above Informational. It is not High: severity at that level requires a code defect an attacker or an unwitting operator cannot avoid, and here the numbers are explicitly flagged for pre-launch ratification and are trivially editable in a JSON file. The durable, actionable part is the absence of any assertion in `deriveLiquidityParams`.

**Confidence.** High. PLAUSIBLE. Votes: 1 sanity check.

**Location.** `token/presale/metaplex-genesis.config.json:46-48`

**Code.**
```json
  "liquidity": {
    "raisedSolToLiquidityBps": 6000,
    "tokenAllocation": "100000000",
```

**What's wrong.** `deriveLiquidityParams` derives the DEX pool's token side from a raise-independent constant and never validates the resulting implied pool price against the fixed presale price. `token/presale/genesis_lib.mjs:230-238` converts the hard-coded `cfg.liquidity.tokenAllocation` into `1e17` base units with no reference to how much was raised:

```javascript
export function deriveLiquidityParams(cfg) {
  const decimals = cfg.token.decimals;
  return {
    baseTokenAllocation: BigInt(cfg.liquidity.tokenAllocation) * 10n ** BigInt(decimals),
```

The presale, meanwhile, is fixed-price at `hardCapSol / presaleAllocation` = 5000 / 150,000,000 = 3.33333e-5 SOL per token (`fixedPriceSolPerToken`, `genesis_lib.mjs:155-157`; encoded on-chain as `baseTokenAllocation` 1.5e17 against `allocationQuoteTokenCap` 5e12 at `genesis_lib.mjs:130-132`). Under the documented 60%-of-raise pairing (`metaplex-genesis.config.json:47`; `docs/TOKEN_ROADMAP.md:187-188`, `:235`), the implied opening price is `0.6 * raised / 1e8`, a function of the realised raise, while buyers paid a constant. The two are equal only at a raise of 5,555.6 SOL, which exceeds the 5,000 SOL hard cap and is unreachable. At full subscription the pool opens at 3000/1e8 = 3.0e-5, 10.0% below presale price; at the 1,000 SOL soft cap it opens at 600/1e8 = 6.0e-6, 82.0% below, against a pool holding 3.3x more tokens than the entire presale sold. The LP is permanently locked (`createNeverClaimSchedule`, `genesis_lib.mjs:234`), so the mispricing cannot be unwound. Nothing validates any of this: `deriveLiquidityParams` takes only `cfg` and asserts no relationship at all, and `cmdPlan` (`genesis_presale.mjs:71-102`) prints the fixed price at line 82 and the liquidity allocation at line 98 without ever comparing them.

Two corrections to the original framing. First and materially: **the 60% figure is not encoded by this tooling.** `cmdLiquidity` (`genesis_presale.mjs:244-251`) passes `addRaydiumCpmmBucketV2` only `genesisAccount`, `baseMint`, `bucketIndex`, `baseTokenAllocation`, `lpLockSchedule` and `startCondition` -- no quote-token amount at all. `raisedSolToLiquidityBps` is read into `lp` at `genesis_lib.mjs:236` and used solely to format two console warnings, at `genesis_presale.mjs:99` ("NOT WIRED: the 60% quote-token split is config-only -- no instruction encodes it yet") and `:253-254`. `docs/TOKEN_ROADMAP.md:483` already records this as a prior audit finding. So the 82% and 10% magnitudes are projections of the documented intent, not of code behavior. The direction survives regardless: even pairing 100% of the raise leaves the pool below the presale price for any raise under 3,333 SOL (the soft cap would give 1.0e-5 against 3.33e-5, 70% below). Second and minor: the cliff does not unlock at pool creation. `depositEnd` is `2026-09-06T15:00:00Z` and `tge` is `2026-09-08T15:00:00Z` (config lines 35-36); `claimStartCondition` derives from `tge` (`genesis_lib.mjs:139`) while the LP `startCondition` derives from `depositEnd` (`genesis_lib.mjs:235`). The pool therefore opens 48 hours *before* the first claim, and will already have been arbitraged to its mispriced mark by the time any buyer can sell -- which makes the buyer position marginally worse, not better.

**Exploit / reachability.** No hostile call sequence exists; this is a deterministic outcome of the committed numbers. The operator sequence is: `npm run presale:create` (`initializeV2` + `addPresaleBucketV2`, `genesis_presale.mjs:122-159`) encodes `baseTokenAllocation` 1.5e17 base units against `allocationQuoteTokenCap` 5e12 lamports, fixing the presale price at 3.33333e-5 SOL/token; deposits accrue to the soft cap of 1,000 SOL, allocating 30,000,000 tokens; `npm run presale:liquidity` (`cmdLiquidity`, `genesis_presale.mjs:236-255`) calls `addRaydiumCpmmBucketV2` with `baseTokenAllocation` = 1e17 base units, a raise-independent constant. Under the documented intent that pairs 600 SOL, the pool's opening mark is 6.0e-6 SOL/token, and at TGE 33% of each buyer's allocation unlocks into a market priced 82% below what they paid.

**The precondition I could not verify from source** is the SOL side of the pair. Because `cmdLiquidity` sends no quote-token amount, the actual SOL paired against the 100M is decided inside `@metaplex-foundation/genesis`, which is not installed in this checkout (there is no `node_modules` under the repo root or under `token/`), so I could not read its default. The structural defect -- a fixed token side, a variable SOL side, and no assertion anywhere relating the two -- is confirmed; the specific percentages are not.

**Remediation.** Add an explicit price-consistency assertion so the two numbers cannot drift apart silently, and surface the comparison in the plan output operators actually read:

```javascript
export function deriveLiquidityParams(cfg) {
  const decimals = cfg.token.decimals;
  // Invariant: the pool must not open below the price presale buyers paid.
  // Presale price is fixed at hardCap/allocation; the pool's implied price is
  // (bps/10000 * raised) / tokenAllocation, which is worst-case at the SOFT cap.
  const presalePrice = fixedPriceSolPerToken(cfg);
  const bps = Number(cfg.liquidity.raisedSolToLiquidityBps);
  const lpTokens = Number(cfg.liquidity.tokenAllocation);
  const worstCasePoolPrice = (bps / 10000) * Number(cfg.sale.softCapSol) / lpTokens;
  const bestCasePoolPrice  = (bps / 10000) * Number(cfg.sale.hardCapSol) / lpTokens;
  if (bestCasePoolPrice < presalePrice) {
    throw new Error(
      `Liquidity mispriced at EVERY reachable raise: pool opens at ${bestCasePoolPrice.toExponential(4)} ` +
      `SOL/token even at the hard cap vs presale ${presalePrice.toExponential(4)}. ` +
      `Break-even raise is ${(presalePrice * lpTokens / (bps / 10000)).toFixed(0)} SOL, above the ` +
      `${cfg.sale.hardCapSol} SOL hard cap. Fix liquidity.tokenAllocation, raisedSolToLiquidityBps, or the caps.`
    );
  }
  if (worstCasePoolPrice < presalePrice) {
    console.warn(
      `WARNING: at the ${cfg.sale.softCapSol} SOL soft cap the pool opens at ` +
      `${worstCasePoolPrice.toExponential(4)} SOL/token, ` +
      `${(100 * (1 - worstCasePoolPrice / presalePrice)).toFixed(1)}% below the presale price. ` +
      `LP is permanently locked — this cannot be unwound after TGE.`
    );
  }
  return { /* unchanged */ };
}
```

Then pick one of three ratification options in `docs/TOKEN_ROADMAP.md` §13 so the invariant can actually hold: (a) scale the LP token side to the realised raise rather than hard-coding 100M -- compute `tokenAllocation = 0.6 * raised / presalePrice` at finalize time and return unused tokens through the existing `withdrawUnsoldPresaleV1` path; (b) raise `raisedSolToLiquidityBps` until the hard-cap case clears break-even, which needs above 100% at 100M tokens and therefore forces (a) or (c) anyway; or (c) cut `liquidity.tokenAllocation` to the level that makes the expected raise price-neutral -- at the 1,000 SOL soft cap and a 60% split that is 18M tokens, not 100M.

Separately, close the unwired quote side flagged at `genesis_presale.mjs:99` and `:253-254` by adding the `endBehaviors` `SendQuoteTokenPercentage` on the presale bucket, and pin the exact SOL amount `addRaydiumCpmmBucketV2` pairs by running the flow once on devnet -- until that is observed, no LP price guarantee in this tooling is verifiable. Finally, consider moving the LP `startCondition` (`genesis_lib.mjs:235`) from `depositEnd` to `tge`, so the pool is not live and arbitrageable for 48 hours before any buyer can claim.

*Without the assertion, nothing in the codebase will ever notice that the ratified LP allocation and the ratified presale price are inconsistent, and the inconsistency becomes unfixable the moment the never-claim LP lock takes effect.*

---

### [LOW] F-26 -- No `close` anywhere: a zeroed StakeAccount and the vault ATA hold rent that no instruction can ever return

**Severity.** Permanent, unrecoverable loss of a small fixed amount of SOL -- not of the staked SPL principal -- incurred on the normal path with no attacker involvement and no attacker gain. Local and authenticated, no privilege escalation, and C:None / I:None / A:None for the program itself. The only impact is integrity-of-funds at 1,510,320 lamports (0.00151 SOL) per user-mint pair, plus a one-time ~2,074,080 lamports (0.00207 SOL) ATA subsidy borne asymmetrically by the first staker of each mint. Staked tokens themselves are never at risk: `unstake` is bounded by the caller's own `sa.amount` at line 139, and the vault balance is at least the sum of all `stake_account.amount` by construction, so principal is always retrievable. There is no availability loss -- `stake` and `unstake` keep working indefinitely no matter how many zeroed accounts accumulate. I explicitly checked for an under-graded High and found none: there is no path from the missing `close` to theft, to a stuck vault, or to a denial of service against another user. This does not reach Medium because the loss is capped, self-paid, and disclosed-by-omission rather than adversarial.

**Confidence.** High. CONFIRMED. Votes: 1 sanity check.

**Location.** `programs/rclaw_staking/src/lib.rs:161-162`

**Code.**
```rust
        let sa = &mut ctx.accounts.stake_account;
        sa.amount = sa.amount.checked_sub(amount).ok_or(StakeError::Overflow)?;
```

**What's wrong.** `unstake` decrements the balance and returns. When `sa.amount` reaches zero, the 89-byte `StakeAccount` PDA (`space = 8 + StakeAccount::SPACE` at line 186, with `SPACE = 32 + 32 + 8 + 8 + 1 = 81` at line 268) stays allocated forever. The `Unstake` accounts struct (lines 226-233) carries `mut`, `seeds`, `bump = stake_account.bump`, `has_one = owner` and `has_one = mint` -- and no `close = owner`. There is no separate close instruction either: the program has exactly two handlers, `stake` (line 90) and `unstake` (line 137). Rent-exemption for 89 bytes is `(128 + 89) * 3480 * 2 = 1,510,320` lamports, paid by `payer = owner` (line 185) and never returned.

The vault side is worse because the cost is asymmetric. `vault` is `init_if_needed` with `payer = owner` (lines 198-205), so the **first** staker of any mint funds that mint's vault ATA on everyone else's behalf, and it can never be closed by anyone -- `vault_authority` (line 237) only ever signs the `transfer_checked` at lines 146-159, and the program never CPIs `CloseAccount`. For a Token-2022 ATA carrying `ImmutableOwner` (170 bytes, consistent with the header at lines 18-21 stating the real $RCLAW mint is Token-2022) that is `(128 + 170) * 3480 * 2 = 2,074,080` lamports. Because `mint` carries zero constraints and `PINNED_MINT` defaults to `None` (line 63, `option_env!`), this applies to every junk mint anyone stakes. Related and sharing the root cause: any tokens transferred directly into a vault ATA by mistake are also permanently locked, since `unstake` caps every withdrawal at the caller's own record, so vault balance in excess of the sum of stake records has no path out. It is locked, not stealable.

One correction to the original framing. The "amplified variant" does not amplify against anyone but the griefer. `bot/token/tier_gate.py:161` filters server-side on the querying wallet with `{"memcmp": {"offset": 8, "bytes": wallet}}`, and an attacker cannot place a victim's pubkey at offset 8 because `programs/rclaw_staking/src/lib.rs:176` declares `pub owner: Signer<'info>`, line 187 seeds the PDA with `owner.key().as_ref()`, and line 118 writes `sa.owner = ctx.accounts.owner.key()`. A griefer's junk `StakeAccount`s therefore carry the griefer's own pubkey at offset 8 and can only ever bloat the griefer's own filtered result set. No victim's gated command slows down. Residual growth of the RPC node's program-account index is the RPC provider's concern, not a program-level DoS. The honest framing is a permanent rent-recovery gap plus a first-staker subsidy, not a griefing or availability vector.

Worth crediting: the absence of `close` genuinely eliminates the Anchor close-then-revive attack class, which would otherwise be a real hazard here given `init_if_needed` at line 184.

**Exploit / reachability.** Deterministic on the intended happy path, with every account caller-controlled and every constraint satisfied. Alice calls `stake(mint = M, amount = 1000)`. `Stake<'info>` runs `init_if_needed, payer = owner` on `stake_account` (lines 183-190) and on the `vault` ATA (lines 198-205), so Alice's lamports fund both -- 1,510,320 for the PDA and, if she is the first staker of M, a further 2,074,080 for M's vault ATA. She then calls `unstake(mint = M, amount = 1000)`. Lines 146-159 CPI `transfer_checked` to return her tokens, and lines 161-162 set `sa.amount = 0`. State delta: `stake_account.amount == 0` with the account still allocated and still program-owned, and the vault ATA still allocated with a zero token balance. Alice's ~0.0036 SOL is now in two accounts that no instruction in this program can close, for the lifetime of the program. Repeat per staker; the vault ATA lamports are locked regardless of who staked first. A griefer who stakes one base unit of each of N self-minted tokens pays all N × ~0.0036 SOL themselves, so it is not an economic attack -- but the program does accrue N permanently unclosable accounts.

**Remediation.** Two independent fixes; the first matters most.

Return the stake-record rent by adding a `close` on the fully-exited path. The cleanest shape that avoids the revival hazard is a dedicated handler rather than an unconditional `close` on `unstake`:

```rust
/// Reclaim rent once the record is fully exited.
pub fn close_stake_account(_ctx: Context<CloseStake>) -> Result<()> { Ok(()) }

#[derive(Accounts)]
pub struct CloseStake<'info> {
    #[account(mut)]
    pub owner: Signer<'info>,
    pub mint: InterfaceAccount<'info, Mint>,
    #[account(
        mut,
        seeds = [b"stake", owner.key().as_ref(), mint.key().as_ref()],
        bump = stake_account.bump,
        has_one = owner @ StakeError::WrongOwner,
        has_one = mint  @ StakeError::WrongMint,
        constraint = stake_account.amount == 0 @ StakeError::InsufficientStake,
        close = owner,
    )]
    pub stake_account: Account<'info, StakeAccount>,
}
```

Keep the `has_one` pair and the seeds/bump so rent can only be routed to the true owner. The `amount == 0` constraint is load-bearing: without it, closing a funded record would orphan the escrowed tokens in the vault with no record to redeem them against, turning this Low into a High. Do not omit it. Note also that once `close` exists, the close-then-revive class becomes reachable because `stake` uses `init_if_needed` at line 184. Anchor 0.30.1 defends here -- its `close` writes `CLOSED_ACCOUNT_DISCRIMINATOR` before deallocation, so a same-transaction `init_if_needed` hits a discriminator mismatch and fails -- but do not downgrade Anchor below 0.30 without re-examining this, and add a test asserting that a close and a re-stake bundled into one transaction fails.

The vault ATA rent is largely unfixable after the fact and is better addressed at deployment: pre-create the vault ATA for the canonical mint at deploy time, funded by the protocol rather than by whichever user happens to stake first, so no user silently subsidizes it. A general fix would be an admin-gated sweep that CPIs `CloseAccount` with `vault_authority` signing, guarded on a zero vault balance and no outstanding stake records -- but "no outstanding stake records" is not cheaply provable on-chain, so deploy-time pre-creation is the pragmatic answer.

Deployment-gate note: this program is draft/devnet (header lines 1-11) and already defers real deployment behind the roadmap's Phase 0 Guardrails. Rent reclaim should be settled before mainnet, where the stranded lamports are real user funds, but this is not on its own a mainnet blocker the way a fund-loss bug would be.

*Without this, every user who fully exits permanently forfeits roughly 0.0015 SOL with no instruction able to return it, and the first staker of each mint additionally donates roughly 0.002 SOL that nobody -- including the program authority -- can ever recover.*

---

### [LOW] F-28 -- create_token.mjs pays rent for additionalMetadata it never writes on-chain, and verify_token.mjs does not check it -- the mint ships with three configured metadata fields missing

**Severity.** No confidentiality, integrity-of-funds, or availability impact. Nothing here touches supply, mint authority, freeze authority, or any token balance. Two real but minor effects: 1,204,080 lamports (0.0012 SOL) of rent permanently locked in the mint account -- unrecoverable in practice since a mint with non-zero supply cannot be closed, but economically negligible and worth exactly zero on devnet, where this tooling is pinned; and a silent divergence between the deployed mint's on-chain TLV and the ratified config, which the repo's own verifier actively asserts does not exist. The second effect is the substance: it is an integrity-of-attestation bug in the verification tooling, not an exploitable one. There is no attacker -- this is a deterministic self-inflicted defect on the happy path. Crucially, `updateAuthority: payer.publicKey` (`create_token.mjs:93`) is never revoked, so the three missing fields can be added post-deploy with a single `createUpdateFieldInstruction`; full post-hoc remediability caps this below Medium. It is Low rather than Informational only because the verifier prints a false all-clear, which is exactly the kind of output an operator reasonably relies on. Explicitly not under-graded: the missing fields (project, website, roadmap) are cosmetic display metadata, not authority, not supply, and not a price or routing input to the trading bot's tier gate.

**Confidence.** High. CONFIRMED. Votes: 1 sanity check.

**Location.** `token/scripts/create_token.mjs:56-61`

**Code.**
```javascript
  additionalMetadata: cfg.additionalMetadata || [],
};

// Size = mint with MetadataPointer extension + the packed metadata (TLV) it points to.
const mintLen = getMintLen([ExtensionType.MetadataPointer]);
const metadataLen = pack(metadata).length + 4; // +4 TLV size prefix
```

**What's wrong.** The `metadata` object assembled at lines 51-57 includes `additionalMetadata` from `token/config/token.config.json:8-12` -- three key/value pairs (project, website, roadmap). That object is referenced in exactly two places in the file: `pack(metadata)` at line 61, which sizes the rent, and lines 96-98, which read only `name`, `symbol` and `uri`. `createInitializeMetadataInstruction` at lines 90-99 receives no `additionalMetadata` argument at all, and no `createUpdateFieldInstruction` call exists anywhere under `token/` -- grepping the repo for `createUpdateField|updateField|UpdateField` returns zero hits. The three fields are therefore packed into the rent calculation and then dropped on the floor.

The over-funding is exactly 173 bytes, computed from the spl-token-metadata borsh layout of the committed pairs (4-byte length prefix per key and per value): 4+7+4+8 = 23 for "project"/"RUNECLAW", 4+7+4+44 = 59 for "website", and 4+7+4+76 = 91 for "roadmap". At the default rent schedule (3480 lamports/byte/year × 2.0 exemption threshold = 6960 lamports/byte) that is 1,204,080 lamports = 0.00120408 SOL. The original finder's ~165 bytes / 1,148,400 lamports was flagged as approximate, so this is a refinement rather than a correction.

The rest of the sizing is correct and deliberately not flagged: the `+4` at line 61 is the proper TLV header (`TYPE_SIZE` 2 + `LENGTH_SIZE` 2), and funding `mintLen + metadataLen` at line 62 while allocating only `space: mintLen` at line 69 is the canonical Token-2022 pattern where `InitializeMetadata` reallocs.

Compounding it, `token/scripts/verify_token.mjs:26-41` builds a six-element `checks` array -- decimals, supply, mint authority revoked, freeze authority null, metadata name, metadata symbol -- with no entry comparing `meta.additionalMetadata` to `cfg.additionalMetadata`, and none comparing `meta.uri` to `cfg.metadataUri`. The loop at 44-47 leaves `ok === true`, line 49 prints `ALL CHECKS PASSED ✓`, and line 50 exits 0 on a mint missing every configured additional field.

The more consequential half of the verifier gap is actually the missing `uri` comparison rather than the `additionalMetadata` one, since `uri` points at the off-chain JSON carrying image and description. On the happy path `uri` cannot diverge, because `create_token.mjs:55` and `:98` source it straight from config -- but `verify_token.mjs:14` accepts a `MINT=<address>` override, so the script can be pointed at an arbitrary third-party mint and will bless it on name, symbol, decimals, supply and authorities alone while ignoring where its metadata actually points.

**Exploit / reachability.** Deterministic on the intended operator path, with no hostile caller required. The operator runs `npm run keygen`, then `npm run create`. `lib.mjs:10-13` loads `token/config/token.config.json`; `create_token.mjs:51-57` assembles `metadata` including the three pairs; lines 60-62 compute a rent quote that includes 173 bytes which will never be allocated. Transaction one (lines 102-103) sends `createIx + pointerIx + initMintIx + initMetaIx`; `createInitializeMetadataInstruction` is constructed with exactly `name`, `symbol` and `uri`. Token-2022's `InitializeMetadata` reallocs the mint to `mintLen + 4 + packed(name/symbol/uri only)`; the surplus lamports simply remain as excess balance, so no error surfaces anywhere. No later instruction ever writes the three fields. The operator then runs `npm run verify`, which prints `✓ metadata name`, `✓ metadata symbol` and `ALL CHECKS PASSED ✓`, and exits 0. Final state: a mint holding 0.0012 SOL of dead rent, missing three configured TLV fields, blessed by the repository's own verifier. Wallets and explorers reading the mint's TLV find no project, website or roadmap fields, and the divergence between the ratified config and the deployed mint is surfaced by nothing in the repo.

**Remediation.** Two independent fixes, both small.

First, actually write the fields. Import `createUpdateFieldInstruction` from `@solana/spl-token-metadata` and append one instruction per pair after metadata init, in a separate transaction:

```js
import { createUpdateFieldInstruction } from '@solana/spl-token-metadata';
// after tx1 confirms:
if (metadata.additionalMetadata.length) {
  const fieldTx = new Transaction().add(
    ...metadata.additionalMetadata.map(([field, value]) =>
      createUpdateFieldInstruction({
        programId: TOKEN_2022_PROGRAM_ID,
        metadata: mint.publicKey,
        updateAuthority: payer.publicKey,
        field,
        value,
      })
    )
  );
  await sendAndConfirmTransaction(connection, fieldTx, [payer]);
}
```

Each `UpdateField` reallocs the mint and requires additional rent, which the line 62 funding must cover -- and since line 61 already sizes for the packed additional fields, the existing lamport quote becomes *correct* rather than excessive once this lands. That resolves both halves at once: the over-funding stops being over-funding.

Second, close the verifier blind spot by adding to the `checks` array after line 40:

```js
['metadata uri', meta ? meta.uri === cfg.metadataUri : false, meta ? meta.uri : '(none)'],
['metadata additional fields',
  meta ? JSON.stringify([...(meta.additionalMetadata ?? [])].sort()) ===
         JSON.stringify([...(cfg.additionalMetadata ?? [])].sort()) : false,
  meta ? JSON.stringify(meta.additionalMetadata ?? []) : '(none)'],
```

Sort both sides before comparing -- on-chain TLV field order is not guaranteed to match config order.

Third and optional, but worth doing before any mainnet run: add a `metadata update authority` check to the same array and revoke that authority (set it to null via `createUpdateAuthorityInstruction`) once the fields are written. `create_token.mjs:93` leaves it on the payer indefinitely, and `docs/TOKEN_ROADMAP.md:408` already lists this as an open pre-launch item.

*Without the first fix the deployed mint silently lacks three ratified metadata fields while paying rent for them; without the second, `npm run verify` will continue to print `ALL CHECKS PASSED` for a mint whose metadata URI points anywhere at all.*

---

### [LOW] F-29 -- tier_gate.py scales staked base units by RCLAW_DECIMALS from the environment rather than from the mint, so a misconfigured value mis-scales every tier threshold by orders of magnitude

**Severity.** Confirmed as a mechanism but capped at Low, and it sits on the Low/Informational boundary. It is not attacker-triggerable: `RCLAW_DECIMALS` is a server-side environment variable, and no remote input reaches it, so an attacker cannot cause the divergence, only benefit from one an operator already made. The impact ceiling is feature-gate bypass, not value: the only gated feature is `FEATURE_MIN_TIER = {"premium_scan": "pro"}` (`bot/token/tier_gate.py:45-47`), i.e. the `/scalp`, `/intraday` and `/swing` scan modes. There are no tokens, no mint/freeze/upgrade authority, no on-chain state, and no availability of assets involved; the module docstring at lines 23-24 states that nothing here signs or holds a key. The shipped default is also correct -- `_decimals()` falls back to 9 and `token/config/token.config.json:5` sets `"decimals": 9`, asserted against chain at `token/scripts/verify_token.mjs:27` -- so a default-configuration deploy is unaffected. Most importantly, the bug is strictly dominated by an intentional fail-open already on the same path: `_rpc` at lines 100-104 returns `None` for any mainnet URL, `staked_of` returns `None` at line 170, and `allows_user` returns `True` at lines 233-234. On the mainnet path this audit cares about, everyone already receives premium regardless of decimals -- a bypass that is total, deliberate, and independent of this finding. No reading reaches High: there is no confidentiality, integrity-of-funds, or availability impact available to elevate.

**Confidence.** High. PLAUSIBLE. Votes: 1 sanity check.

**Location.** `bot/token/tier_gate.py:81-85`

**Code.**
```python
def _decimals() -> int:
    try:
        return int(_env("RCLAW_DECIMALS", "9"))
    except (TypeError, ValueError):
        return 9
```

**What's wrong.** `staked_of` (lines 145-183) sums the u64 at bytes `[72:80]` of every matching `StakeAccount` into `total_base`, then returns `total_base / (10 ** _decimals())` at line 183 -- whole tokens, compared against `RCLAW_TIER_PRO_MIN` (10,000) and `RCLAW_TIER_ELITE_MIN` (100,000) in `tier_for_balance` (lines 186-194). The divisor comes exclusively from the environment. The mint's true decimals are available on-chain and are already read by the staking program itself, which passes `ctx.accounts.mint.decimals` into `transfer_checked` at `programs/rclaw_staking/src/lib.rs:114` and `:158`, yet nothing in this repository ever reconciles the two. Every other decimals figure in the codebase is derived from config or from chain (`token/config/token.config.json:5`, `token/presale/genesis_lib.mjs:101-130`, `token/scripts/verify_token.mjs:25-27`), which makes this the one silent divergence point. A wrong `RCLAW_DECIMALS` mis-scales every tier threshold by orders of magnitude, silently and in both directions.

Three corrections to the original report. First, the likelihood is lower than stated: `RCLAW_DECIMALS` is undocumented. It is absent from `.env.example`, from the module's own operator-enable block at `bot/token/tier_gate.py:15-21` (which lists `TOKEN_TIER_GATE_ENABLED`, `RCLAW_MINT`, `RCLAW_RPC_URL`, `RCLAW_TIER_PRO_MIN` and `RCLAW_TIER_ELITE_MIN` and omits this one), from `docs/TOKEN_ROADMAP.md` including the pre-deployment checklist at lines 515-530, and from `programs/rclaw_staking/README.md`. It appears only at line 83 and in `tests/test_token_tier_gate.py`. The "operator copies a 6-decimal runbook" story requires them to invent an undocumented variable name and then contradict the project's own canonical config of 9. Second, the impact is capped by the mainnet fail-open described above, so this can only bite on devnet or localnet, where the code self-describes as draft. Third, the report's citation of the layout contract is wrong: root `README.md:32-41` is the project links footer, not a byte-layout spec. The actual cross-language contract is `programs/rclaw_staking/README.md:35-39` together with `programs/rclaw_staking/src/lib.rs:25-31`.

Both adjacent sub-defects are real but Informational rather than Low. The `staked_of` docstring at lines 150-151 misstates the amount as being "at offset 40" when line 179 correctly reads `raw[72:80]` -- offset 40 is the mint field. That is a genuine documentation defect, but it is bracketed by three correct references: the inline comment seven lines later at 157-158 (`mint @40 (32) | amount @72 (u64 LE)`), `lib.rs:25-31`, and `programs/rclaw_staking/README.md:35-39` -- and it is locked by an explicit regression test, `test_staked_of_reads_amount_at_offset_72` at `tests/test_token_tier_gate.py:213-224`, so a maintainer who re-derived the layout from the docstring would break CI immediately. Likewise, the `getProgramAccounts` filter list at lines 161-164 carries no memcmp on the 8-byte Anchor discriminator at offset 0 and no `dataSize` filter. That is nearly inert: the program declares exactly one `#[account]` type (`lib.rs:257`), `gate_enabled()` at line 94 requires `RCLAW_MINT` to be set so the mint memcmp@40 is unconditionally applied on every reachable call, and line 178 discards accounts shorter than 80 bytes. A future account type would have to carry the genuine $RCLAW mint at offset 40 and a u64 at offset 72 to be miscounted.

**Exploit / reachability.** Not remotely exploitable; this is a deployment-time miscount. The operator sets `TOKEN_TIER_GATE_ENABLED=true`, `RCLAW_MINT=<mint>`, `RCLAW_STAKING_PROGRAM=<program>`, a non-mainnet RPC URL, and `RCLAW_DECIMALS=6`. Mallory stakes 10 whole $RCLAW -- 10,000,000,000 base units -- through `rclaw_staking::stake`, with every on-chain constraint satisfied; this is a legitimate stake. Mallory then calls `/linkwallet` followed by `/scalp`. `allows_user` (line 232) dispatches to `staked_of` because `staking_program()` is set, which returns `10_000_000_000 / 10**6 = 10_000.0`; `tier_for_balance` returns `"pro"`; `premium_scan` is granted for 0.1% of the stated 10,000-token requirement. The inverse holds too: `RCLAW_DECIMALS=12` makes a genuine 100,000-token elite staker read as `100.0` and be denied. State delta: nothing on-chain; off-chain, one user gains access to premium scan modes.

**The precondition I could not verify from source** is the trigger itself -- the operator must set `RCLAW_DECIMALS` to a wrong value. That is deploy-time configuration, not code, and it is what keeps this at PLAUSIBLE rather than CONFIRMED.

**Remediation.** Derive decimals from the mint and demote the environment variable to an override that warns loudly on divergence. Cache the result so this does not add an RPC round-trip per gate check:

```diff
--- a/bot/token/tier_gate.py
+++ b/bot/token/tier_gate.py
@@
+_DECIMALS_CACHE: dict[str, int] = {}
+
 def _decimals() -> int:
-    try:
-        return int(_env("RCLAW_DECIMALS", "9"))
-    except (TypeError, ValueError):
-        return 9
+    """Base-unit exponent for $RCLAW, preferring the mint's on-chain value.
+
+    RCLAW_DECIMALS remains an escape hatch, but a value that disagrees with the
+    mint is logged and ignored -- a wrong divisor mis-scales every tier
+    threshold by orders of magnitude, silently and in both directions.
+    """
+    try:
+        env_dec = int(_env("RCLAW_DECIMALS", "9"))
+    except (TypeError, ValueError):
+        env_dec = 9
+    mint = mint_address()
+    if not mint:
+        return env_dec
+    if mint not in _DECIMALS_CACHE:
+        res = _rpc(
+            "getAccountInfo",
+            [mint, {"encoding": "jsonParsed", "commitment": "confirmed"}],
+        )
+        try:
+            _DECIMALS_CACHE[mint] = int(
+                res["value"]["data"]["parsed"]["info"]["decimals"]
+            )
+        except (KeyError, TypeError, ValueError):
+            return env_dec  # unreadable mint -> fall back, do not cache
+    chain_dec = _DECIMALS_CACHE[mint]
+    if _env("RCLAW_DECIMALS") and env_dec != chain_dec:
+        system_log.warning(
+            "tier_gate: RCLAW_DECIMALS=%s disagrees with mint %s decimals=%s; "
+            "using on-chain value",
+            env_dec, mint, chain_dec,
+        )
+    return chain_dec
```

Three cheap companion fixes. Correct the docstring at lines 150-151, changing "summing ``amount`` (u64 LE) at offset 40" to "at offset 72", so it matches the inline comment at 157-158, `lib.rs:25-31`, and `programs/rclaw_staking/README.md:35-39`. Harden the `getProgramAccounts` filters at lines 161-164 with `{"dataSize": 89}` (8-byte discriminator plus `StakeAccount::SPACE` at `lib.rs:268`) and ideally a memcmp@0 on the base58 Anchor discriminator -- cheap defense in depth against a future second `#[account]` type. And document `RCLAW_DECIMALS` in `.env.example` and in the operator-enable block at lines 15-21, stating that 9 is canonical per `token/config/token.config.json`; even with the on-chain read above, an operator who discovers the variable should be told the right value.

*Without this, the tier gate's staked-balance scaling depends on an undocumented environment variable that nothing validates, so a single wrong digit silently grants pro access for 0.1% of the stated stake or denies elite access to a fully compliant staker, with no log line either way.*

---

### [LOW] F-30 -- `derivePresaleParams` validates no economic or ordering invariant: an inverted timeline or contribution pair is silently converted into an immutable on-chain condition

**Severity.** Low. There is no privilege boundary crossed and no hostile input channel: the config is read from local disk by `loadConfig` (`token/presale/genesis_lib.mjs:32-41`, a bare `JSON.parse` with no schema), and the only actor who can set these values already holds the presale authority keypair. CVSS attack-vector reasoning barely applies — the trigger is operator misconfiguration, not an adversary. Impact if it fires is bounded in both branches. The `tge < depositEnd` branch yields a few days of extra linear accrual on a 60-day vest for late depositors, and because `deriveLiquidityParams` (`genesis_lib.mjs:235`) pins the Raydium pool start to `depositEnd`, there is no venue in which to monetize the extra unlock before deposits close — it is an unlock-timing fairness distortion, not value extraction from other buyers. The `min > max` branch is a dead-sale DoS that is self-evident on the first deposit attempt and recoverable by relaunching, since mint mode calls `generateSigner` (`genesis_presale.mjs:114`) and a fresh genesis account costs one wasted devnet mint. Today the whole path is devnet-gated by the mainnet refusal at `genesis_lib.mjs:56-63`. I checked specifically for under-grading to High and the capped, non-extractive impact does not support it; on mainnet this stays Low-to-Medium, not Critical.

**Confidence.** High. PLAUSIBLE. Votes: 1 sanity check.

**Location.** `token/presale/genesis_lib.mjs:119-126`

**Code.**
```js
  const depositStart = unix(hasWhitelist && t.whitelistStart ? t.whitelistStart : t.publicStart);
  const depositEnd = unix(t.depositEnd);
  const tge = unix(t.tge);
  const claimEnd = unix(t.claimEnd);

  const vestingPeriod = BigInt(cfg.vesting.vestingPeriodSeconds);
  // Linear tail: full unlock `linearMonthsAfterTge` after TGE (~30d/month).
  const vestingEnd = tge + BigInt(cfg.vesting.linearMonthsAfterTge) * 30n * 86400n;
```

**What's wrong.** `derivePresaleParams` (`genesis_lib.mjs:110-152`) turns operator-supplied JSON into immutable on-chain presale conditions without asserting a single ordering or economic invariant. `unix` (`genesis_lib.mjs:89-94`) validates only that each timestamp parses in isolation — its sole throw is `if (Number.isNaN(ms))` at `:92`. Nothing checks `depositStart < depositEnd`, `depositEnd <= tge`, `tge < claimEnd`, or `minContributionSol <= maxContributionSol`. No caller adds the check: `cmdCreate` (`genesis_presale.mjs:105-176`) passes the derived params straight into `presaleInput` (`:138-153`) and on to `addPresaleBucketV2` (`:159`). No test or CI job schema-validates the config — the sole test file under `token/presale/` is `allowlist_serialize.test.mjs`, which asserts allowlist padding/root/height only, and `.github/workflows/ci.yml` never mentions the presale.

The vesting schedule is absolute-time and identical for every depositor — `createClaimSchedule({ startTime: tge, endTime: vestingEnd, cliffTime: tge, ... })` at `genesis_lib.mjs:142-148` — so it is equitable only while `tge` strictly follows `depositEnd`. That makes the timeline ordering a real economic invariant, not a cosmetic one. The config's own timeline (`metaplex-genesis.config.json:31-38`) is labelled `"Placeholders — set at launch"` with September 2026 dates, so these values will be hand-edited immediately before a sale, which is exactly when transposition typos happen.

Two corrections to the original report. `softCapSol <= hardCapSol` is not an on-chain invariant at all: `softCapLamports` is derived at `genesis_lib.mjs:133` and then never placed into `presaleInput` (`genesis_presale.mjs:138-153`), consistent with the config's own `_softCapNote` (`metaplex-genesis.config.json:29`) that soft cap is not a native Genesis field — inverting it affects only the `plan` printout. And the adjacent float64 concern about `solToLamports` (`genesis_lib.mjs:98`, `BigInt(Math.round(Number(x) * 1e9))`) does not survive scrutiny: every plausible lamport product (0.25 / 25 / 1000 / 5000 SOL → 2.5e8 to 5e12) is far below 2^53 and exactly representable, and `Math.round` absorbs binary-fraction residue. That would require a >9,007,199 SOL figure or sub-lamport SOL precision to matter.

**Exploit / reachability.** Client-side reachability is confirmed; nothing in this repo blocks it. (1) Operator edits `token/presale/metaplex-genesis.config.json:31-38` at launch and transposes `depositEnd` and `tge`. (2) `npm run presale:create` → `cmdCreate` → `loadConfig` (`genesis_lib.mjs:32-41`) → `derivePresaleParams`. (3) `unix` parses all four timestamps successfully because each is individually well-formed RFC3339. (4) `createTimeAbsoluteCondition` wraps each scalar independently at `genesis_lib.mjs:137-140`, and `createClaimSchedule` at `:142-148` sets `startTime = cliffTime = tge` for every depositor. (5) `addPresaleBucketV2` is sent at `genesis_presale.mjs:159` with `claimStartCondition` earlier than `depositEndCondition`, and the bucket is immutable thereafter. Resulting state delta: buyers depositing on the final day have already accrued linear vesting at deposit time and claim a larger immediate unlock than day-one buyers at the same price. The alternative inverted-`min`/`max` path produces a bucket whose `minimumDepositAmount` exceeds its `depositLimit`, so no deposit can satisfy both.

I enumerated every guard on this path and none constrains ordering: `genesis_lib.mjs:92` (parseability only), `:56-63` (mainnet substring refusal), `genesis_presale.mjs:186` (`amountSol > 0`, deposit path only). **PLAUSIBLE rather than CONFIRMED for exactly one reason:** the on-chain half is unverifiable from this checkout. `@metaplex-foundation/genesis` is pinned at 0.40.0 (`token/package.json`, `token/package-lock.json:776-778`) but `node_modules` is not installed, so I could not read whether `addPresaleBucketV2`'s handler carries its own `require!(claim_start >= deposit_end)` or `min <= max` check. That single dependency-internal precondition is the gap.

**Remediation.** Add an invariant gate at the top of `derivePresaleParams`, before any condition is constructed. It is purely additive and converts an irreversible on-chain mistake into a loud startup failure that `presale:plan` surfaces offline.

```diff
   const claimEnd = unix(t.claimEnd);
+
+  // Ordering + economic invariants. These become IMMUTABLE on-chain conditions,
+  // so a transposed date must fail here rather than at addPresaleBucketV2.
+  const req = (ok, msg) => { if (!ok) throw new Error(`Invalid presale config: ${msg}`); };
+  req(depositStart < depositEnd, `depositStart (${depositStart}) must precede depositEnd (${depositEnd})`);
+  req(depositEnd <= tge, `depositEnd (${depositEnd}) must not follow tge (${tge}) — the claim schedule is absolute-time and would unlock unevenly across depositors`);
+  req(tge < claimEnd, `tge (${tge}) must precede claimEnd (${claimEnd})`);
+  if (hasWhitelist && t.whitelistStart) req(depositStart < unix(t.publicStart), 'whitelistStart must precede publicStart');
+  const minL = solToLamports(cfg.sale.minContributionSol);
+  const maxL = solToLamports(cfg.sale.maxContributionSol);
+  req(minL > 0n && minL <= maxL, `minContributionSol must be >0 and <= maxContributionSol`);
+  req(maxL <= solToLamports(cfg.sale.hardCapSol), 'maxContributionSol must not exceed hardCapSol');
+  req(BigInt(cfg.sale.presaleAllocation) > 0n, 'presaleAllocation must be > 0');
+  req(Number(cfg.vesting.cliffAmountBps) >= 0 && Number(cfg.vesting.cliffAmountBps) <= 10000, 'cliffAmountBps must be within 0..10000');
```

Then reuse `minL`/`maxL` at `:134-135` instead of recomputing. Add a config-invariant unit test beside `token/presale/allowlist_serialize.test.mjs` asserting that a transposed `tge`/`depositEnd` throws — there is currently no test coverage of the presale config at all. Separately, decide whether `softCapSol` is meant to bind: it is derived at `:133` and discarded, so either wire it via an `endBehaviors` min-raise extension or drop it from the derived params so it cannot read as an enforced control.

*Without this, a single transposed timestamp in the launch-day config edit becomes an immutable presale bucket that vests late depositors ahead of day-one buyers, with no on-chain path to correct it.*

---

### [LOW] F-34 -- The only deploy-gating end-to-end spec derives the PRE-FIX vault-drain PDAs, cannot pass, and never asserts the field the critical fix introduced

**Severity.** Low. This is a test-correctness and deploy-process defect, not an on-chain vulnerability: no attacker, no unauthorized state delta, no token loss, no availability impact on any deployed program. Mapped to CVSS it is essentially N/A — there is no attack vector. Graded on audit-process impact it is Low because the one deploy-checklist artifact it degrades (`docs/TOKEN_ROADMAP.md:519`, `anchor build && anchor test`) is redundant with a second gate that is correct and does execute: `programs/rclaw_staking/tests/attack.rs:152-162` derives the current PDAs and `:279-326` performs the real mint-confusion attack with vault-balance assertions. It is not Medium or High because losing this gate does not leave the fix unverified. It is not Informational because the file is genuinely unpassable, not merely unexecuted, and it sits inside a numbered pre-deployment checklist — which creates real pressure on an operator to patch a security spec until it goes green.

**Confidence.** High. CONFIRMED. Votes: 1 sanity check.

**Location.** `programs/rclaw_staking/tests/rclaw_staking.ts:31-38`

**Code.**
```ts
  const [stakePda] = anchor.web3.PublicKey.findProgramAddressSync(
    [Buffer.from("stake"), owner.publicKey.toBuffer()],
    program.programId
  );
  const [vaultAuthority] = anchor.web3.PublicKey.findProgramAddressSync(
    [Buffer.from("vault")],
    program.programId
  );
```

**What's wrong.** These are the exact pre-fix seed sets that `programs/rclaw_staking/README.md:22-23` identifies as the root cause of the critical vault-drain: `["stake", owner]` with no mint, and one global `["vault"]` authority shared by every mint. The shipped program uses `seeds = [b"stake", owner.key().as_ref(), mint.key().as_ref()]` (`src/lib.rs:187`, `:228`) and `seeds = [b"vault", mint.key().as_ref()]` (`src/lib.rs:194`, `:236`).

The instruction calls themselves still succeed. `Anchor.toml:6` sets `resolution = true` and the program is anchor-lang 0.30.1, so `.accounts({ owner, mint, userTokenAccount })` at `:72-73` resolves `stake_account`, `vault_authority` and `vault` from the IDL's seed definitions and targets the correct addresses. What breaks is the assertion: `await program.account.stakeAccount.fetch(stakePda)` at `:76` and `:88` queries `PDA(["stake", owner])`, an address the current program never writes, and Anchor's `AccountClient.fetch` throws `Account does not exist`. The suite cannot go green.

Beyond the hard failure, the spec is vacuous on the fix itself. Lines `:77-79` assert only `owner`, `amount` and `stakedAt` — never `sa.mint`, the field the critical fix introduced. And the `vault` address computed at `:66-70` from the mint-less `vaultAuthority` is never referenced again, so no vault balance is asserted anywhere in the file. Correcting only the derivation would leave a spec that still proves nothing about mint-scoping.

Nothing in the toolchain surfaces this. `tsconfig.json:13` sets `"strict": false` and there is no `noUnusedLocals`, so `npm run typecheck` passes over both the stale seeds and the dead `vault` binding. `.github/workflows/ci.yml` runs only Python tooling and never invokes npm, cargo, or anchor.

Two qualifications to the original report. The pinned-build skip in the Rust suite is documented and mitigated, not silent: `attack.rs:38-50` prints an explicit `SKIPPED:` message explaining that the test mints its own token which the pin correctly rejects, and `README.md:110-113` prescribes **both** an unpinned `cargo test -p rclaw_staking` (4 unit + 4 integration pass, all vault tests executing) and a pinned run. So "a production-shaped build exercises no vault logic in either suite" holds only for a single pinned invocation, not for the documented procedure. Second, an adjacent doc inconsistency runs the opposite way: `Anchor.toml:20-23` still claims the TS toolchain is "not committed yet, so `anchor test` is not runnable as-is", but `package.json` and `tsconfig.json` are both committed and `README.md:132-135` says so explicitly.

**Exploit / reachability.** Not an attacker path — an operator path, following `docs/TOKEN_ROADMAP.md` step 2 of "Before any deployment holding value". (1) Operator runs `anchor build` unpinned, then `anchor test`; `Anchor.toml:24` runs `ts-mocha ... programs/rclaw_staking/tests/**/*.ts`, which is exactly this file. (2) `before()` (`:40-63`) creates a throwaway legacy-SPL mint and mints 1000 tokens; unpinned, so `check_pinned_mint` returns `Ok`. (3) Test 1 `.stake(amount)` succeeds and writes `PDA(["stake", owner, mint])`, funding the vault at `PDA(["vault", mint])`. (4) `:76` fetches the module-level `stakePda` from `:31-34` and throws `Account does not exist`. Tests 2 and 3 (`:88`, `:100`) inherit the same stale constant. State delta on chain: none beyond a normal, correct stake. The delta is in the audit posture — the operator either skips checklist step 2 or edits the spec until it passes, and neither outcome validates that `StakeAccount.mint` is set, that the vault is mint-scoped, or that mint-confusion is rejected.

**Remediation.** Fix the derivations and, more importantly, make the spec assert what the fix introduced.

```diff
   const [stakePda] = anchor.web3.PublicKey.findProgramAddressSync(
-    [Buffer.from("stake"), owner.publicKey.toBuffer()],
+    [Buffer.from("stake"), owner.publicKey.toBuffer(), mint.toBuffer()],
     program.programId
   );
   const [vaultAuthority] = anchor.web3.PublicKey.findProgramAddressSync(
-    [Buffer.from("vault")],
+    [Buffer.from("vault"), mint.toBuffer()],
     program.programId
   );
```

Note this cannot stay at module scope: both now depend on `mint`, which is only assigned in `before()` at `:41`. Declare them as `let` alongside `mint`/`userAta` at `:28-29` and compute inside `before()`.

Then close the vacuity, which is the part that matters. Assert `assert.ok(sa.mint.equals(mint));` after `:79`. Actually use the vault — `getAccount(provider.connection, vault)` and assert the balance is `amount` after stake and `amount - unstaked` after unstake. Port the headline case: an `it("rejects redeeming a foreign-mint stake against the real vault")` mirroring `attack.rs:279-326` — stake mint B, call unstake with mint A's accounts, assert the failure is `ConstraintSeeds` (2006) by code rather than merely that it threw, and assert mint A's vault balance is unchanged.

To prevent recurrence: set `"noUnusedLocals": true` in `tsconfig.json` so the computed-then-discarded `vault` fails `npm run typecheck` (that alone catches half of this today); add a CI job running `cargo test -p rclaw_staking`, which needs no validator or SBF toolchain because `attack.rs` uses `solana-program-test` in-process; correct the stale `Anchor.toml:20-23` comment; and until the spec is fixed, amend `docs/TOKEN_ROADMAP.md:519` so `anchor test` is not presented as a passing gate — the verification matrix at `:505` already records it as "Never executed", but the checklist does not.

*Without this, the deploy checklist contains a command that cannot succeed, and the most likely operator response is to edit the security spec into something green that asserts none of the three properties it exists to confirm.*

---

### [LOW] F-37 -- The whitelist round has no discounted price: one fixed price serves both rounds, so the OG round confers only queue priority

**Severity.** Low. There is no attacker path, no unauthorized state transition, no token loss, no availability impact, and no Anchor or Solana constraint involved — this is a specification-versus-implementation divergence in draft, mainnet-refusing tooling. I considered raising it and rejected that for four verified reasons: `docs/TOKEN_ROADMAP.md:154` titles the section "## 5. Presale mechanics (proposed)"; §13 explicitly lists "**Soft/hard caps, min/max contribution, round durations, presale price** (§5)" among the unratified proposed defaults; the public-facing condensed page `docs/gitbook/token-roadmap.md:74` says only "Whitelist round (48h) → public round (72h or until cap)" and makes no discount claim, so the discount language is confined to the internal roadmap; and `token/presale/genesis_lib.mjs:56-63` hard-refuses any RPC URL containing "mainnet", so no real buyer can currently be misled. It is not Informational either: the divergence is real, repeated in three places (`:163`, `:164`, `:172`), silently contradicted by both venue configs, and — uniquely among divergences of this class in this repo — carries no correction note. If the team ratifies §5 as written and ships, a published-terms misrepresentation on a 5,000 SOL raise is a legal and disclosure exposure rather than a technical one, which is why the "would be Critical on mainnet" escalation does not fire here.

**Confidence.** High. CONFIRMED. Votes: 1 sanity check.

**Location.** `token/presale/genesis_presale.mjs:48-49` and `:154-159` (the single-bucket construction); supporting `token/presale/genesis_lib.mjs:192-202`

**Code.**
```js
// genesis_presale.mjs:48-49 — index 1 is the Raydium LP bucket, not a second sale round
const BUCKET_INDEX = 0;
const LIQUIDITY_BUCKET_INDEX = 1;

// genesis_presale.mjs:154-159 — the allowlist is a FIELD on the one presale bucket
  if (wl) {
    presaleInput.allowlist = allowlistInitArgsFromArtifact(cfg, wl);
    console.log('    whitelist: applying Merkle root', wl.rootHex.slice(0, 16) + '… (ends at publicStart)');
  }
  console.log('[2/2] addPresaleBucketV2 — configuring the fixed-price presale…');
  await addPresaleBucketV2(umi, presaleInput).sendAndConfirm(umi);
```

**What's wrong.** The roadmap promises a two-price presale; the implementation ships one price. `docs/TOKEN_ROADMAP.md:163-164` tabulates "Round 1 — Whitelist / OG | 48 hours, discounted price" against "Round 2 — Public | 72 hours or until hard cap, standard price", and `:172` restates "Whitelist Round 1 is priced below this; the public round at this level".

The Genesis integration creates exactly one presale bucket. `presaleInput` (`genesis_presale.mjs:138-153`) carries a single `baseTokenAllocation` and a single `allocationQuoteTokenCap` sourced from `derivePresaleParams` (`genesis_lib.mjs:130-132`), so the effective rate `presaleAllocation / hardCapSol` is uniform across the entire deposit window. `addPresaleBucketV2` is called once, at `:159`. The Merkle allowlist is a field on that same bucket, not a separate round: `buildAllowlist` (`genesis_lib.mjs:178-204`) returns `initArgs` with exactly six members — `enabled`, `merkleTreeHeight`, `padding`, `merkleRoot`, `endTime` (`= unix(cfg.timeline.publicStart)`, `:181`) and `quoteCap` (`= solToLamports(cfg.sale.hardCapSol)`, `:182`, identical to the bucket-level cap). Membership and timing only; no price, no rate, no bonus basis points, no separate allocation. `LIQUIDITY_BUCKET_INDEX = 1` is consumed only by `cmdLiquidity` via `addRaydiumCpmmBucketV2` (`:244-251`), a Raydium CPMM LP bucket with a never-claim lock. The fallback venue config has the same shape — `smithii.config.json:22-25` is `"whitelist": { "enabled": true, "phaseHours": 48 }` with no price or bonus field. An OG participant's entire benefit is a 48-hour head start on a cap shared with the public round.

One correction to the original anchor: `fixedPriceSolPerToken` (`genesis_lib.mjs:154-157`) is display-only — its own comment says "for display" and its only callers are the `plan` console output at `genesis_presale.mjs:74` and `:82`. Deleting it would change nothing on chain. The defect is not that one function computes one price; it is that only one price exists to compute.

Aggravating context: this roadmap annotates every other divergence of exactly this class. There is a "**Correction (from building it)**" block for the soft cap immediately after `:176`, a runtime `WARNING: the 60% raise->pool split is NOT encoded on-chain by this command` printed at `genesis_presale.mjs:252`, and §14 names the zero-length-whitelist-window and liquidity-split divergences explicitly. The discount divergence alone has no correction note, no runtime warning, and no entry in the RUNBOOK's pre-launch confirm list — and `token/presale/RUNBOOK.md:19` repeats "whitelist (48h) → public (72h)" without noting both transact at the same price.

**Exploit / reachability.** No hostile caller and no account substitution; the sequence is the honest operator flow. (1) `npm run presale:whitelist` → `cmdWhitelist` (`genesis_presale.mjs:212-233`) → `buildAllowlist`, producing the six price-free `initArgs` above. (2) `npm run presale:create` → `cmdCreate`, one `addPresaleBucketV2` with the allowlist attached as a field. (3) At the deposit boundary, an allowlisted wallet depositing at `whitelistStart` and a public wallet depositing at `publicStart + 1s` both hit bucket 0 with the same allocation-to-cap ratio and receive `presaleAllocation / hardCapSol` tokens per SOL. `cmdDeposit` (`:179-209`) passes an identical `input` in both cases; its only branch (`:196-205`) attaches `input.proof` and changes nothing about pricing. State delta: an OG participant who read §5 expecting a below-market entry receives tokens at exactly the public price, with queue position as the sole differentiator.

I could not inspect `@metaplex-foundation/genesis` internals to see whether the SDK's `AllowlistInitArgs` exposes a price field this code declines to set — `node_modules` is not installed. That does not affect the verdict (whatever the SDK offers, this code sets no differential); it only affects which remediation is correct.

**Remediation.** Pick one direction and make docs and code agree before the sale opens. Shipping neither is the defect.

**A — drop the discount** (lowest effort, matches what is built):

```diff
-| Round 1 — Whitelist / OG | 48 hours, discounted price |
-| Round 2 — Public | 72 hours or until hard cap, standard price |
+| Round 1 — Whitelist / OG | 48 hours, **priority access at the same fixed price** |
+| Round 2 — Public | 72 hours or until hard cap, same fixed price |
@@
-`$RCLAW`** (≈ 30,000 `$RCLAW` per SOL). Whitelist Round 1 is priced below this; the public
-round at this level.
+`$RCLAW`** (≈ 30,000 `$RCLAW` per SOL). Both rounds transact at this price; Round 1 confers
+earlier access to a shared cap, not a lower price.
```

and add a correction block in the established house style, noting that a Genesis presale prices by `baseTokenAllocation / allocationQuoteTokenCap` **per bucket** and the Merkle allowlist is a membership-plus-window gate on a bucket, so a single bucket cannot express two prices. Update `RUNBOOK.md:18-19` to match.

**B — implement the discount** (two buckets): introduce `OG_BUCKET_INDEX = 0` / `PUBLIC_BUCKET_INDEX = 1`, renumber `LIQUIDITY_BUCKET_INDEX` to 2, add `sale.ogAllocation` / `sale.ogCapSol` to the config and derive a second param set, attach the allowlist to the OG bucket only with `depositEndCondition` at `timeline.publicStart`, give the public bucket `depositStartCondition` at `publicStart` and no allowlist, route `cmdDeposit` by wall-clock or proof presence, and have `cmdPlan` print both prices and the implied discount. Confirm first against the installed SDK that multiple presale buckets under one genesis account are supported and that unsold OG allocation can roll into the public bucket, or accept that it must be recovered via `withdraw-unsold`.

Either way, resolve the §13 "presale price" open decision explicitly and add round pricing to the RUNBOOK's pre-launch checklist.

*Without this, the sale ships with published terms that overstate what an OG buyer receives — a disclosure exposure that the current mainnet refusal only defers, not resolves.*

---

### [LOW] F-39 -- Every `RCLAW_*` tier-gate parameter is missing from the operator-facing config surfaces, and the module's own enable recipe omits the two that change gate semantics

**Severity.** Low. Not an attack path: no hostile caller, no Anchor account to substitute, no state delta an attacker can force. Every impact multiplier is small. The gated asset is exactly one capability — `FEATURE_MIN_TIER` (`bot/token/tier_gate.py:45-47`) contains the single entry `"premium_scan": "pro"`, and its sole call site is `bot/skills/telegram_handler.py:8121`, gating the `/scalp` `/intraday` `/swing` scan modes. No funds, signing authority, key material, mint or freeze authority, or availability is reachable through it; the worst realized outcome is a user who holds 10,000 $RCLAW but never staked it receiving scan modes intended for stakers, which is a monetization leak rather than a security-boundary breach. The gate is off by default: `gate_enabled` (`:88-94`) requires both `TOKEN_TIER_GATE_ENABLED` and a non-empty `RCLAW_MINT`, and `allows_user` returns `True` unconditionally when disabled. And fail-open dominates — `_rpc` refuses any URL containing "mainnet" (`:100-104`) and returns `None`, which `allows_user` treats as fail-open at `:233-234`, so on a mainnet deployment every user is granted premium regardless of configuration and the misconfiguration is only observable on devnet. Not Medium: no confidentiality or integrity impact on any asset and no availability impact. Not Informational: the omission of `RCLAW_STAKING_PROGRAM` from the module's own enable recipe is a concrete, actionable defect in a security-relevant file.

**Confidence.** High. CONFIRMED. Votes: 1 sanity check.

**Location.** `bot/token/tier_gate.py:15-21` (the enable recipe); secondary `/home/user/RUNECLAW/.env.example` (684 lines, zero `RCLAW` matches)

**Code.**
```python
Enable (operator, opt-in)::

    TOKEN_TIER_GATE_ENABLED=true
    RCLAW_MINT=<devnet mint address>
    RCLAW_RPC_URL=https://api.devnet.solana.com   # optional, defaults to devnet
    RCLAW_TIER_PRO_MIN=10000                        # whole tokens for 'pro'
    RCLAW_TIER_ELITE_MIN=100000                     # whole tokens for 'elite'
```

**What's wrong.** The root `.env.example` is the canonical operator surface — its header at `:5` reads "Copy this file to .env and fill in your values", and across 684 lines it documents `TELEGRAM_BOT_TOKEN`, `JWT_SECRET`, `BOT_SYNC_SECRET`, `DASHBOARD_TOKEN`, `MCP_AUTH_TOKEN` and dozens more with inline guidance. `grep -ci rclaw` returns 0, as does a search for `TIER_GATE` or `SOLANA`. The secondary `token/.env.example` is 12 lines covering only `KEYPAIR_PATH`, `RPC_URL` and `CLUSTER`, so neither template mentions any tier-gate variable.

The sharper defect, which the original report missed, is inside `tier_gate.py` itself. The block above is an explicit operator recipe headed "Enable (operator, opt-in)". It omits `RCLAW_STAKING_PROGRAM` and `RCLAW_DECIMALS`. An operator who follows the file's own instructions exactly still lands on the wallet-balance path:

```python
    # Prefer on-chain STAKED balance when a staking program is configured;
    # otherwise fall back to raw wallet balance. Both fail open on infra error.
    bal = staked_of(wallet) if staking_program() else balance_of(wallet)
```

`staking_program()` returns `_env("RCLAW_STAKING_PROGRAM")` (`:78`), which is empty, so `:232` silently takes `balance_of` and holders — not stakers — clear the "pro" threshold, defeating the mechanism the staking program exists to enforce.

The `RCLAW_DECIMALS` omission compounds it. `staked_of` ends with `return total_base / (10 ** _decimals())` at `:183`, and `_decimals` (`:81-85`) defaults to 9. A Token-2022 $RCLAW mint with 6 decimals would inflate every computed stake by 1000x, granting "elite" to a holder of 100 tokens. Both silent-fallback helpers swallow bad input without a log line — `_env_float` (`:60-65`) returns the default on any `ValueError`, and `_decimals` returns 9 — so a typo in a threshold or a wrong decimals value is undetectable at runtime.

On the build side, `RCLAW_PINNED_MINT` — the program's only mint-pinning control (`programs/rclaw_staking/src/lib.rs:63`, `option_env!`) — appears in no Makefile, no `deploy.sh`, no `Anchor.toml`, and no CI workflow. `.github/workflows/ci.yml` has no `anchor` or `cargo` step at all, so nothing in CI builds the program today.

Two corrections to the original report. "Undocumented" is too strong: all of these are documented in prose at `docs/TOKEN_ROADMAP.md:355-357` ("When `RCLAW_STAKING_PROGRAM` is set it derives tiers from staked balance ... instead of wallet balance"), at `programs/rclaw_staking/README.md:77-90`, and in the `lib.rs:42-63` doc comment. This is a template and discoverability gap, not missing documentation. And the `RCLAW_PINNED_MINT` impact is overstated: `lib.rs:55-58` documents the unset case and names the compensating control, which is genuinely implemented at `tier_gate.py:161-164`:

```python
    filters = [{"memcmp": {"offset": 8, "bytes": wallet}}]
    mint = mint_address()
    if mint:
        filters.append({"memcmp": {"offset": 40, "bytes": mint}})
```

and covered by `test_staked_of_filters_on_mint` in `tests/test_token_tier_gate.py`. Because `gate_enabled` hard-requires `RCLAW_MINT`, the mint filter is always active whenever the gate is, so a stake of a worthless token can never be counted — and per-mint vaults make cross-mint drain impossible by construction. An unset pin is a documented deployment step with a working backstop, not a shipped vulnerability.

**Exploit / reachability.** No attacker sequence exists; this is an operator sequence. Operator copies `.env.example` to `.env`, finds no `RCLAW_*` section, reads `tier_gate.py`, follows the "Enable (operator, opt-in)" block verbatim, sets `TOKEN_TIER_GATE_ENABLED=true` and `RCLAW_MINT`, and never learns that `RCLAW_STAKING_PROGRAM` exists. `gate_enabled()` returns `True`, `staking_program()` returns `""`, and `:232` reads raw wallet balance. State delta: any wallet holding ≥ `RCLAW_TIER_PRO_MIN` whole tokens passes `premium_scan` without ever staking. Devnet only in practice, because the mainnet refusal at `:100-104` short-circuits the balance read entirely and grants everyone premium there regardless.

**Remediation.** Three small, independent changes; none touch the on-chain program.

First, fix `tier_gate.py`'s own recipe — the highest-value change, because it is the block an operator is most likely to copy. Add to the docstring at `:15-21`:

```
    RCLAW_STAKING_PROGRAM=<staking program id>  # REQUIRED for staked-tier gating;
                                                # if unset the gate falls back to raw
                                                # WALLET balance and holders pass
                                                # without staking
    RCLAW_DECIMALS=9                            # MUST match the mint's decimals;
                                                # a mismatch mis-scales staked amounts
```

Second, make the silent fallbacks audible. Have `_env_float` log a warning before returning the default on a malformed value, apply the same to `_decimals`, and warn at the `:232` branch when the gate is enabled but no staking program is configured.

Third, add an "$RCLAW token tier gate" section to the root `.env.example` mirroring the corrected recipe, all seven variables commented out with `TOKEN_TIER_GATE_ENABLED=false`, plus a one-line note that `RCLAW_PINNED_MINT` is a build-time setting consumed by `RCLAW_PINNED_MINT=<mint> anchor build` and pointing at `programs/rclaw_staking/README.md`.

*Without this, an operator who enables the tier gate by following the module's own instructions gets wallet-balance gating instead of staked-balance gating, and the staking program the tier system is built around is bypassed by anyone who simply holds the token.*

---

### [LOW] F-40 -- Root `.gitignore` has no keypair coverage, and no secret-scanning gate exists

**Severity.** Low. Not a vulnerability in shipped code — a prospective repo-hygiene gap with no current exposure. Every factual claim about file contents is confirmed, but the impact chain requires an unverified future human action: a contributor generating or copying a keypair into a workspace path outside `target/` and committing it. CVSS-style, the attack vector is not applicable because there is no exploit primitive, only a missing preventative control. Confidentiality impact **if** the precondition occurs would be Critical — the BPF upgrade authority is the program's sole trust root, since there is no admin, config PDA, or pause, so a leak means full drain of every per-mint vault — but probability is materially reduced by the fact that Anchor's default keypair path `target/deploy/*-keypair.json` is already ignored at `.gitignore:101`. Low is correct; Informational would be defensible; High is not, given zero current exposure and zero attacker agency. This must be closed before any mainnet deploy, at which point the upgrade-authority keypair becomes a live mainnet trust root rather than a devnet draft one.

**Confidence.** High. PLAUSIBLE. Votes: 1 sanity check.

**Location.** `/home/user/RUNECLAW/.gitignore:100-103`

**Code.**
```gitignore
# Solana / Anchor programs (programs/*) — build output and local validator state
target/
.anchor/
test-ledger/
```

**What's wrong.** Those four lines are the entire Solana section, and they are the last lines of the file — the coverage is build output only. The root `.gitignore`'s only secret-bearing patterns anywhere are `.env` / `.env.*` / `!.env.example` (`:43-45`) and `**/.jwt_secret` (`:90`). There is no `.keys/`, no `*keypair*.json`, no `id.json`, no `*.pem`, and no `*.key`. The only key-directory ignore in the repository is `token/.gitignore:2` (`.keys/`), and because a `.gitignore`'s patterns are evaluated relative to its own directory, it provides zero coverage above `token/`. A keypair written anywhere else — `./deploy-authority.json`, `programs/rclaw_staking/upgrade-authority.json`, or `~/.config/solana/id.json` copied into the workspace to satisfy the provider wallet path at `Anchor.toml:17` — matches nothing. Solana keypair files are plain JSON integer arrays with no distinguishing extension, which is why this is the ecosystem's most common leak class and why it is invisible to default secret-scanner rulesets.

Compounding it, there is no detection layer: `.github/workflows/ci.yml` is the only workflow file and contains no `gitleaks`, `trufflehog`, `git-secrets`, or `detect-secrets` step, and there is no `.pre-commit-config.yaml` in the repo root.

Current state is clean, verified independently: `git ls-files | grep -Ei 'keypair|\.key$|id\.json|secret|\.pem$'` returns only `app/lib/secrets_vault.js`, `app/test/secrets_vault.test.js`, `bot/core/secrets_vault.py`, `docs/SECRETS_VAULT.md`, and `tests/test_secrets_vault.py` — all source, test, or documentation. Parsing every tracked `.json` for a 32- or 64-element all-integer array yields zero hits, and both `.env.example` files contain only empty placeholders.

Two corrections to the original report, both lowering realistic probability. The Anchor build flow documented at `programs/rclaw_staking/README.md:127` (`anchor build`) writes its program keypair to `target/deploy/rclaw_staking-keypair.json`, which **is** ignored by `target/` at `:101` — so the default-path leak is already blocked and only a deliberate off-default placement is exposed. And the claimed container-build vector is unsubstantiated: `/home/user/RUNECLAW/Dockerfile` contains no `anchor`, `solana`, or `cargo` invocation at all — it builds only the Python and Node application — so nothing in this repo drives a copy of `~/.config/solana/id.json` into the workspace.

**Exploit / reachability.** There is no instruction-level call sequence; reachability here is a commit sequence. (1) A contributor runs `solana-keygen new -o /home/user/RUNECLAW/upgrade-authority.json`, or writes any keypair JSON to a repo path outside `target/`, `.anchor/`, `test-ledger/` and outside the `token/` subtree. (2) `git add -A` stages it — I read all 103 lines of the root `.gitignore` and no pattern matches. (3) `git commit && git push` — nothing in CI or pre-commit objects. (4) The 64-integer array lands in a public-repo diff, reading as data rather than as a secret. Anyone watching then holds the program's BPF upgrade authority and can upgrade it to drain every per-mint vault. **PLAUSIBLE rather than CONFIRMED for one reason:** step (1) is a hypothetical human error, not an attacker primitive. Nothing in the repository drives it, and the tree is clean today, so I can demonstrate the absence of a control but not reachability from source.

**Remediation.** Two cheap, independent controls. First, extend the Solana block in `/home/user/RUNECLAW/.gitignore` so key material is denied by default regardless of path:

```gitignore
# Solana key material — deny by default, no exceptions.
.keys/
*keypair*.json
*-keypair.json
id.json
*.pem
*.key
```

`*.key` and `*.pem` are broad; if any tracked fixture would be caught, add a targeted `!path/to/fixture` negation rather than narrowing the deny rule, and verify with `git ls-files -i -c --exclude-standard` that nothing tracked is newly ignored.

Second, add a secret-scanning job to `.github/workflows/ci.yml` so the control is enforced rather than advisory — a `gitleaks/gitleaks-action@v2` step with `fetch-depth: 0`, plus a custom rule for the Solana shape specifically (a JSON file whose entire body is a 64-element array of integers 0-255), since that is the one secret format with no distinguishing extension or prefix. Enable GitHub push protection on the repository as well.

Treat both as blocking before any mainnet deploy. Separately, the mainnet upgrade authority should never be a plain filesystem keypair at all: moving it to a multisig (Squads) or hardware signer makes this entire finding moot by construction.

*Without this, a single `solana-keygen new` run from the repo root followed by `git add -A` publishes the program's sole trust root, and nothing in the toolchain would notice.*

---

### [LOW] F-41 -- Dry-run harness never aborts after a failed step, and shares one unscoped `presale.devnet.json` with real presales, so a fully successful run silently destroys the record of an existing presale (gap sweep; id assigned in the F-4x range)

**Severity.** Low, downgraded from the reported High. No hostile caller exists anywhere on this path — the "attacker" is a dropped RPC confirmation, and every signature is the operator's own. The two facts that carried the High grade are both refuted. Funds at risk are devnet faucet SOL, not live SOL, because `rpcUrl` (`token/presale/genesis_lib.mjs:56-63`) gates `makeUmi` for `create`, `liquidity`, `deposit` and `claim` alike and throws on any mainnet endpoint. And the "permanent, irrevocable LP lock" is not a product of the dry run at all: `deriveLiquidityParams` (`genesis_lib.mjs:230-238`) emits `createNeverClaimSchedule()` and the configured 100M allocation identically for a deliberate `npm run presale:liquidity`, which `token/presale/RUNBOOK.md` documents as the normal step. The only parameter the harness actually corrupts is `startCondition`. Residual real impact: on a devnet genesis account, a liquidity bucket with a wrong trigger timestamp, roughly 0.25 devnet SOL spent, and loss of the artifact pointing at the real presale — operator availability and record integrity, no funds, no privilege escalation, no confidentiality. Attack complexity is high (needs a stale artifact, a mid-`create` RPC failure, and the program to accept a late bucket-add). Not Informational, because the control-flow defect is genuine and the adjacent overwrite variant is happy-path reachable with no failure required.

**Confidence.** Medium. PLAUSIBLE. Votes: 1 refuter (gap sweep).

**Location.** `token/e2e/devnet_dryrun.mjs:138-149`

**Code.**
```js
  steps.push(run('plan', ['presale/genesis_presale.mjs', 'plan']));
  steps.push(run('create', ['presale/genesis_presale.mjs', 'create']));
  steps.push(run('liquidity', ['presale/genesis_presale.mjs', 'liquidity']));

  await sleepUntil(publicStart);
  // Deposit the configured MINIMUM, not 1 SOL: keygen airdrops exactly 1 SOL,
  // so depositing 1 SOL could never cover the deposit plus rent/fees.
  const depositSol = String(cfgBase.sale?.minContributionSol ?? 0.25);
  steps.push(run('deposit', ['presale/genesis_presale.mjs', 'deposit', '--amount', depositSol]));

  await sleepUntil(tge);
  steps.push(run('claim', ['presale/genesis_presale.mjs', 'claim']));
```

**What's wrong.** The root cause is one unscoped artifact filename, `presale.devnet.json`, shared between throwaway dry-run presales and real ones, combined with five signed steps submitted without an abort between them.

Exactly one step in the harness can abort the run: `keygen` (`devnet_dryrun.mjs:130-136`). Every step from `:138` onward pushes `{ok:false}` into `steps` and falls through; the aggregate is first consulted at `:153` (`const ok = steps.every((s) => s.ok)`), after all five privileged steps have been submitted. There is no rollback and no resume. That is safe only if a failed `create` leaves no artifact — and it does leave one, because `saveArtifact('presale.devnet.json', artifact)` sits at `genesis_presale.mjs:170`, after both `sendAndConfirm` calls, with no try/catch, so a rejection exits non-zero with any pre-existing file untouched. Meanwhile every downstream command checks existence, never freshness: `cmdLiquidity` (`:240-241`), `cmdDeposit` (`:183-184`), `cmdClaim` (`:311-312`), `cmdWithdraw` (`:261-262`) and `cmdWithdrawUnsold` (`:283-284`) are each literally `const a = readArtifact('presale.devnet.json'); if (!a) throw ...`, and `readArtifact` (`:60-63`) is `fs.existsSync(file) ? JSON.parse(...) : null`. The artifact records a `cluster` field at `:161`, and grep shows zero readers of it anywhere in the repo.

The signer objection does not save this. `keygen.mjs:13-14` deliberately preserves an existing key ("Keypair already exists ... leaving it in place"), the harness passes `env: { ...process.env, GENESIS_CONFIG: DERIVED_CONFIG }` (`:76`) so `KEYPAIR_PATH` is inherited, and `genesis_lib.mjs:68` resolves the same default `./.keys/mint-payer.json`. The authority therefore does match the stale genesis account.

**Three corrections to the reported mechanism.** First, what `liquidity` corrupts is not the LP lock. `deriveLiquidityParams` sources `lpLockSchedule: createNeverClaimSchedule()` and `baseTokenAllocation` from `cfg.liquidity.*`, and `writeDerivedConfig` (`devnet_dryrun.mjs:48-68`) spreads `...base` while overriding only `fundingMode` and `timeline` — it never touches `liquidity`. Those two values are byte-identical to a deliberate `presale:liquidity` run. The sole contaminated parameter is `startCondition: createTimeAbsoluteCondition(unix(cfg.timeline.depositEnd))`, which under the derived timeline (`OPEN_IN = 20`, `DEPOSIT_FOR = 90` at `:31-32`) is roughly 110 seconds after the harness started instead of the config's 2026-09-06. Second, devnet only, per the `rpcUrl` gate. Third — and this is the part actually worth fixing — **there is a strictly more reachable variant that needs no failure at all.** On the success path, `cmdCreate:170` overwrites the artifact unconditionally, and because the harness forces `fundingMode: { ...base.fundingMode, mode: 'mint' }` (`:59`), line `114` always takes the mint branch (`const baseMint = transferMode ? publicKey(cfg.token.mint) : generateSigner(umi)`), producing a fresh mint and therefore a fresh genesis account PDA every run. A fully green `npm run e2e:dryrun` silently replaces the operator's record of a real presale with the throwaway one. `.artifacts/` is gitignored (`token/.gitignore:3`) and nothing else on disk records those addresses, so the pointer is destroyed with no backup — and every subsequent recovery command (`presale:withdraw-unsold`, `presale:withdraw`, `presale:claim`) then targets the wrong presale.

**Exploit / reachability.** Concrete sequence for the reported path: (1) `token/.artifacts/presale.devnet.json` holds `{genesisAccount: G, bucket: B, baseMint: M}` from a prior devnet presale with no bucket at index 1. (2) Operator runs `npm run e2e:dryrun`; keygen passes (key exists, funded, no rate-limit string). (3) `create` fails mid-flight — a 429 or expired blockhash between `initializeV2` and `addPresaleBucketV2` — and the stale artifact survives. (4) `liquidity` submits `addRaydiumCpmmBucketV2{genesisAccount: G, baseMint: M, bucketIndex: 1, baseTokenAllocation: 100M·10^9, lpLockSchedule: never-claim, startCondition: now+110s}`, signed by the matching authority. (5) `deposit --amount 0.25` and `claim` fire at `B`/`G`/`M`. (6) The harness exits 1 with "SOME STEPS FAILED", which reads as "the dry run didn't work" rather than "the real presale was modified" — and `saveReport` (`:97-109`) records only step names, mode, and output tails, never which `genesisAccount` was touched.

**PLAUSIBLE for one precondition:** step (4)'s on-chain acceptance. Metaplex Genesis V2 normally admits buckets only while the genesis account is in a pre-launch configuration phase, so a real presale whose deposit window has opened or closed would very plausibly have the bucket-add rejected by the program's state machine, and it must also still hold 100M unallocated base tokens. `token/node_modules` is not installed in this checkout, so neither the SDK error codes nor the program constraints were readable. The overwrite variant in the third correction above has no such precondition and is fully confirmed.

**Remediation.** Fix the root cause first — it kills both paths.

Scope the artifact per run so a dry run can neither read nor clobber a real one, threading an override the same way `GENESIS_CONFIG` is already threaded:

```js
// genesis_presale.mjs
const PRESALE_ARTIFACT = process.env.PRESALE_ARTIFACT || 'presale.devnet.json';
// ...replace every literal 'presale.devnet.json' with PRESALE_ARTIFACT

// devnet_dryrun.mjs:76
const env = { ...process.env, GENESIS_CONFIG: DERIVED_CONFIG,
              PRESALE_ARTIFACT: `presale.dryrun.${runId}.json` };
```

Abort on the first failed step, before any further signed transaction:

```js
function step(name, args) {
  const r = run(name, args);
  steps.push(r);
  if (!r.ok) {
    log(`step ${name} failed — aborting before any further signed transactions.`);
    saveReport(steps);
    process.exit(1);
  }
  return r;
}
```

Make `saveArtifact` refuse to clobber — in `cmdCreate`, before `:170`, throw unless `--force` is passed when an artifact already exists. Validate the artifact rather than merely testing existence: stamp `createdAt` and the resolved config path alongside the `cluster` field that is written at `:161` and read nowhere, and have every consumer reject a mismatch. Record the addresses actually used into each step of `e2e-report.json` so the report shows which `genesisAccount` was touched.

One adjacent hardening: before any mainnet gate is lifted, replace the `url.includes('mainnet')` denylist at `genesis_lib.mjs:58` with an explicit host allowlist (`api.devnet.solana.com`, `api.testnet.solana.com`, `localhost`). That substring test is currently the only barrier between this tooling and real funds, and it misses providers whose URLs lack the literal string, such as `https://<name>.rpcpool.com/<token>`.

*Without this, a routine successful dry run overwrites the only on-disk record of a real presale's genesis account, mint and bucket, leaving the operator unable to run withdraw, withdraw-unsold, or claim against the presale they actually created.*

---

### [LOW] F-43 -- A regex-valid but non-32-byte linked address makes `getTokenAccountsByOwner` return a JSON-RPC error, which `_rpc` converts to `None` and `allows_user` converts to "permission granted"

**Severity.** Attack vector network (a Telegram message), attack complexity low (one typed string, deterministic, no race and no timing), privileges required low (the attacker must already be a registered bot user, and on any live-configured deployment must additionally be on the operator's allowlist), user interaction none, scope unchanged. Impact on funds is nil: the tier gate never signs a transaction, never holds a key, and never moves value — `bot/token/tier_gate.py` is read-only RPC. Impact on authority is nil: `allows_user` returns a bool and writes nothing back to `UserStore`, so no `tier`, `role`, `authorized`, or `can_trade_live` field changes; live trading is a separate authority (`_can_trade_live`, `bot/skills/telegram_handler.py:1933`) requiring both the env allowlist and the per-user store flag, and the tier gate does not feed it. Impact on availability is nil because the failure is fail-*open*, not fail-closed. What the attacker actually obtains is free use of three read-only scan commands. That is roughly `AV:N/AC:L/PR:L/UI:N/S:U/C:L/I:N/A:N`, about 3.1. This is graded down hard from the reported High: the finder traced the code path correctly but never checked what the bypassed gate protects. The gate has exactly one consumer, `_token_gate_blocks` at `bot/skills/telegram_handler.py:8113`, reached only from `/scalp` (8132), `/intraday` (8149) and `/swing` (8166). Aggravating factors that keep it above Info: it is deterministic, self-service, silent (logged at DEBUG, `tier_gate.py:111`), it persists across every subsequent call until the attacker re-links, and it defeats precisely the control this audit exists to examine. Mitigating: the module is draft and default-off (`gate_enabled()`, `tier_gate.py:88-94`), `.env.example` contains no `RCLAW_*` entries at all, and a strictly simpler bypass already exists and is filed separately — link any whale or exchange address holding at least `RCLAW_TIER_PRO_MIN`, since no ownership proof is ever demanded. The marginal risk this adds over that companion finding is small. It would become Medium or High the day the tier is wired to anything with financial consequence (live-trading eligibility, fee discounts, allocation); today it is not.

**Confidence.** High. CONFIRMED. Votes: 1 refuter (gap sweep). Id assigned from the F-4x gap range; this came from the gap sweep and carried no id from the primary lenses.

**Location.** `bot/token/tier_gate.py:110-116`

**Code.**
```python
        if "error" in out:
            system_log.debug("tier_gate rpc error: %s", out["error"])
            return None
        return out.get("result")
    except Exception as exc:  # network/timeout/parse — fail-open upstream
        system_log.debug("tier_gate rpc call failed: %s", exc)
        return None
```

**What's wrong.** `_rpc` collapses four distinct outcomes into the single value `None`: transport failure, timeout, JSON parse failure, and a well-formed JSON-RPC *error response*. `balance_of` propagates that `None` at `tier_gate.py:132-133`, and `allows_user` reads it as consent at `tier_gate.py:233-234` (`if bal is None: return True  # fail-open on infra error`). Fail-open is a defensible and explicitly documented policy for an infrastructure hiccup. The defect is that the same `None` channel also carries *user-input-validation failures*, which are attacker-triggerable on demand. Concretely, the first parameter of `getTokenAccountsByOwner` must parse as a 32-byte pubkey; Solana's `verify_pubkey` base58-decodes it and rejects a wrong-size value with `ParsePubkeyError::WrongSize`, surfaced as `{"error": {"code": -32602, "message": "Invalid param: WrongSize"}}` at HTTP 200. That is an `error` key in a successfully parsed response, so it lands on line 110 and returns `None` — the same value that means "the RPC is down". The only address validation anywhere in the repository is a character-class regex at `bot/skills/telegram_handler.py:8103`, `re.fullmatch(r"[1-9A-HJ-NP-Za-km-z]{32,44}", addr)`, which bounds length in *characters* and never decodes. A 32-character base58 string carries roughly 187 bits, about 23 bytes, so essentially every string at the low end of the accepted range is a guaranteed non-pubkey that the regex nonetheless accepts. This is distinct from the already-filed "no ownership proof for a well-formed address" issue: there the address is valid and the payoff is impersonation; here the address is deliberately invalid and the payoff is a guaranteed error that self-grants the tier.

**Exploit / reachability.** Preconditions: `TOKEN_TIER_GATE_ENABLED=true`, `RCLAW_MINT` set, `RCLAW_STAKING_PROGRAM` unset (the wallet-balance configuration the module docstring documents as the enable path), `RCLAW_RPC_URL` not containing the substring `mainnet` — otherwise `tier_gate.py:100-104` short-circuits first and grants the tier even more directly, which is F-44 below — and the attacker being a permitted bot user.

1. Attacker sends `/linkwallet 5iZu7D2jLrasA1sRFW5YXGdcdbz9Ng4P`. The handler is `@guard("help")` (`telegram_handler.py:8079`); `DEFAULT_AUTO_ROLE` is `trader` (`bot/utils/user_store.py:122`) and `ROLE_PERMISSIONS["trader"]` contains `help` (`user_store.py:31-36`), so the guard passes. The string is 32 base58 characters and decodes to 23 bytes — verified by decoding it, not assumed — and matches the regex at line 8103.
2. `set_sol_wallet` stores it verbatim with no validation (`bot/utils/user_store.py:455-462`, `self._users[key]["sol_wallet"] = str(address)`), requiring only that the user already exist, which `/start` guarantees by auto-registering with `"authorized": True` (`user_store.py:199`).
3. Attacker sends `/scalp`. `@guard("scan")` passes for the same role. Line 8132 calls `_token_gate_blocks`, which calls `tier_gate.allows_user(self.users, uid, "premium_scan")` at line 8121.
4. `gate_enabled()` is true; `required` is `pro`; `_resolve_wallet` (`tier_gate.py:205-210`) returns the 23-byte string. The no-wallet guard at `tier_gate.py:227-229` does *not* fire, because a non-empty string is truthy.
5. `balance_of` is selected (staking unset) and calls `_rpc("getTokenAccountsByOwner", [<23-byte value>, ...])` at `tier_gate.py:128-131`. The RPC answers with a JSON-RPC error; line 110-112 returns `None`; `balance_of` returns `None` at line 132-133; `allows_user` returns `True` at line 233-234.

State delta: `allows_user` returns `True`, `_token_gate_blocks` returns `False`, and all three premium scan commands execute. Exactly one persisted field changes — `users.json["<uid>"]["sol_wallet"]` now holds a non-pubkey string. Nothing else mutates on-chain or off. The bypass re-derives on every call and lasts until the attacker re-links. The exploit is robust to the transport detail: had the RPC answered non-2xx, `urllib` raises and lines 114-116 return `None` as well. The only outcome that would refute it is a successful parse returning an empty account list, and no conformant Solana RPC parses 23 bytes as a `Pubkey`.

One narrowing the finder missed and the reader should weigh. `_guard` calls `_is_allowlisted` at `telegram_handler.py:2029`, but that check fails open when unconfigured (`telegram_handler.py:1928-1931`, `if not allow: return True  # no allowlist configured -> preserve open/demo behavior`). Since `is_live` (`bot/config.py:2170-2171`) forces a non-empty allowlist on any live-trading bot, a live deployment confines this exploit to already-allowlisted operators and hand-picked live traders. That is a real narrowing, but not a refutation: the deployment in which a token tier gate has any purpose is the open, multi-user one where the allowlist is empty.

**Remediation.** The root cause is one `None` sentinel carrying three meanings — infra error (fail open is correct), policy refusal, and invalid attacker-controlled input (fail open is an authorization bypass). Fix at both ends.

First, validate at the trust boundary by decoding rather than pattern-matching:

```diff
-        if not re.fullmatch(r"[1-9A-HJ-NP-Za-km-z]{32,44}", addr):
-            await self._send(update, "\U0001f534 That doesn't look like a Solana address (base58, 32-44 chars).")
+        if not _is_solana_pubkey(addr):
+            await self._send(update, "\U0001f534 That doesn't look like a Solana address (base58, 32 bytes).")
             return
```

with a dependency-free helper matching the module's existing no-deps style, which base58-decodes and requires exactly 32 bytes including leading-zero (`1`) padding.

Second, as defence in depth, never let a validation failure ride the fail-open channel. In `bot/token/tier_gate.py`, introduce a distinct sentinel and fail closed on it:

```python
_INVALID = object()   # bad user input: must NOT fail open

# in balance_of, after the `not wallet or not mint` check
if not _is_solana_pubkey(wallet):
    return _INVALID

# allows_user, replacing tier_gate.py:233-234
if bal is _INVALID:
    return False        # attacker-controlled input -> deny
if bal is None:
    return True         # genuine infra error -> fail open, as designed
```

Apply the same guard in `staked_of` (`tier_gate.py:161`), where `wallet` is likewise injected unvalidated into a `memcmp` filter. Finally, raise `tier_gate.py:111` from DEBUG to WARNING and include the wallet string, so a malformed address on the tier path leaves a trace an operator will actually see rather than one that is invisible at default log level.

*Without this fix, any user who can reach `/linkwallet` can turn the tier gate off for themselves permanently with a single typed string, and the operator has no default-visible signal that it happened.*

---

### [LOW] F-44 -- The `"mainnet"` substring guard in `_rpc()` turns the entire tier gate into an unconditional allow-all the moment the operator points it at mainnet

**Severity.** No attack vector in the CVSS sense — this is operator-triggered, not attacker-triggered, so there is no privilege escalation to score. Impact is entitlement and revenue integrity only: unauthorized use of three informational scan commands. No token loss, no key material, no custody change, no availability loss, and no on-chain state delta, because `tier_gate.py` only ever reads. Confidentiality, integrity and availability of funds are all None. Two independent factors bound the blast radius further: the affected population is not "every user" but only operator-allowlisted, authorized users holding the `scan` role permission, since `_guard` runs before the handler body; and the bypass still requires a linked wallet, because `allows_user` fails closed at `tier_gate.py:227-229` for unlinked users. The trigger is a deploy-time configuration that cannot exist today — `gate_enabled()` requires a real `RCLAW_MINT`, and `docs/TOKEN_ROADMAP.md:28` states plainly that no token exists and no sale has run. Even at mainnet launch the worst case is allowlisted users receiving free premium scans, which is why this is not Critical despite the title reading like an authorization catastrophe. Downgraded from the reported Medium to Low: it is a genuine correctness defect — a guard whose refusal grants access — worth fixing on principle and cheap to fix, but Medium overstates a control whose only protected resource is three read-only scans.

**Confidence.** High. PLAUSIBLE. Votes: 1 refuter (gap sweep). Id assigned from the F-4x gap range; gap-sweep finding with no id from the primary lenses.

**Location.** `bot/token/tier_gate.py:99-104`

**Code.**
```python
    url = _env("RCLAW_RPC_URL", _DEFAULT_RPC)
    if "mainnet" in url:
        # Draft tooling is devnet-first; refuse mainnet reads to avoid implying
        # a live deployment (see roadmap Guardrails).
        system_log.warning("tier_gate: refusing mainnet RPC %s; treating as unconfigured", url)
        return None
```

**What's wrong.** The guard is intended as a deployment safety rail, and as a rail the intent is reasonable. The problem is where its refusal lands. `return None` flows to `balance_of` at `tier_gate.py:132-133` or `staked_of` at `tier_gate.py:169-170`, both of which return `None`, and `allows_user` maps `None` to `True` at `tier_gate.py:233-234`. So "refuse to read mainnet" is implemented as "grant everyone the tier". The comment's phrase "treating as unconfigured" is misleading: genuinely unconfigured short-circuits at `gate_enabled()` (`tier_gate.py:88-94`) before any RPC and is a true no-op. This branch is reached only when the gate *is* enabled — that is, exactly when the operator believes it is live.

This is not merely a restatement of the module's documented fail-open design. The module declares fail-open in four places (module docstring lines 6-7, the `except` at 114-116, the `allows_user` docstring at 219, and the return at 234), and that policy covers *transient* errors where the gate reseals on the next successful call. The mainnet branch converts it into a permanent, deterministic, hundred-percent open under the single most likely production configuration, with no self-healing and no operator-visible signal beyond a per-call WARNING that is indistinguishable from log noise on a busy bot.

The substring test is also both over- and under-inclusive as a cluster classifier. Over-inclusive: `"mainnet" in url` matches anywhere in the URL, including path segments, query strings and vanity subdomains, so a devnet endpoint can trip it. Under-inclusive: production mainnet endpoints that contain no such substring — `https://rpc.ankr.com/solana`, a Triton or rpcpool endpoint, any private RPC — sail straight through and perform the live mainnet read the rail exists to prevent. A guard that both misses real mainnet endpoints and fails open on the ones it catches provides neither of the two properties it appears to.

**Exploit / reachability.** No adversary; the operator triggers it, and every step below was read and verified in source.

1. Launch day. Operator sets `TOKEN_TIER_GATE_ENABLED=true`, `RCLAW_MINT=<real mint>`, `RCLAW_STAKING_PROGRAM=<deployed id>`, `RCLAW_RPC_URL=https://api.mainnet-beta.solana.com`.
2. A user sends `/scalp` (`telegram_handler.py:8130`, `@guard("scan")`). The decorator runs `_guard` first, enforcing `_is_allowlisted` (2029), the `authorized` flag (2036), `has_permission(tg_id, "scan")` (2045) and the rate limiter (2054). An allowlisted trader or viewer passes all four, since `scan` is in `ROLE_PERMISSIONS` for both roles.
3. Handler body calls `_token_gate_blocks` (8132) and thence `allows_user(..., "premium_scan")` at 8121. `gate_enabled()` is true.
4. `_resolve_wallet` returns the user's linked address. Note this is where the finder's claim "every user" fails: `tier_gate.py:227-229` returns `False` for an unlinked user, so the bypass requires a prior `/linkwallet` — which, because there is no ownership proof (see F-43 and the companion whale-address finding), any user can satisfy with an arbitrary base58 string.
5. `staked_of` calls `_rpc` at `tier_gate.py:165`; line 100 matches; line 104 returns `None`; `staked_of` returns `None` at 169-170; `allows_user` returns `True` at 233-234.
6. `_token_gate_blocks` returns `False` and the premium scan executes. There is no secondary tier check downstream: the `pro_scan` skill has no tier gating, and `scan` is in `TIER_FEATURES["basic"]` (`bot/utils/user_store.py:52-53`), so the tier-feature table does not differentiate either.

State delta: none on-chain. Off-chain, the stake-tier entitlement check becomes a permanent no-op for every wallet-linked allowlisted user while the operator believes the gate is live — and, because unlinked users are still blocked and still see `upgrade_message`, the operator observes genuine blocks and concludes the gate works. That makes the defect more insidious than the finder's "nobody is ever shown upgrade_message" claim, which is factually wrong.

Verdict is PLAUSIBLE rather than CONFIRMED for one reason only: step 1 is a deploy-time configuration that cannot be verified from source and cannot exist today. No `$RCLAW` mint exists, `.env.example` defines no `RCLAW_*` variables, and the feature ships off. Every line of the code path is verified and unblocked; the unverified precondition is entirely an operator's future launch-day choice.

**Remediation.** Two independent fixes, both small, neither touching on-chain code.

Separate "policy refusal" from "cannot determine", and fail closed on refusal. A gate must never treat its own safety rail as a grant:

```diff
 def _rpc(method: str, params: list) -> Optional[dict]:
     url = _env("RCLAW_RPC_URL", _DEFAULT_RPC)
-    if "mainnet" in url:
-        system_log.warning("tier_gate: refusing mainnet RPC %s; treating as unconfigured", url)
-        return None
+    if not _rpc_host_allowed(url):
+        raise _PolicyRefusal(url)
```
```diff
-    bal = staked_of(wallet) if staking_program() else balance_of(wallet)
+    try:
+        bal = staked_of(wallet) if staking_program() else balance_of(wallet)
+    except _PolicyRefusal as exc:
+        system_log.error("tier_gate: RPC %s refused by policy while the gate is "
+                         "ENABLED; DENYING premium.", exc)
+        return False   # fail CLOSED on policy refusal
     if bal is None:
         return True    # fail open ONLY on genuine infra error
```

Note that `_token_gate_blocks` (`telegram_handler.py:8124-8127`) swallows exceptions and returns `False`, so `_PolicyRefusal` must be caught inside `allows_user` as shown — letting it escape re-introduces the same fail-open one frame higher.

Then replace the substring test with a host allowlist parsed via `urlparse(url).hostname`, checked against an explicit set such as `api.devnet.solana.com`, `api.testnet.solana.com`, `localhost` and `127.0.0.1`. That fixes over- and under-inclusiveness at once: Ankr- and Triton-style mainnet endpoints are correctly refused, and a query string can no longer trip a devnet URL. Better still, run the same check once at startup and hard-disable the gate with an ERROR if it fails, so the operator's mental model matches reality instead of diverging silently.

This fix requires updating a test. `tests/test_token_tier_gate.py:117-125` (`test_mainnet_rpc_refused`) currently asserts `mod._rpc("getVersion", []) is None` with the comment "treated as unconfigured / fail-open" — the suite regression-locks the defect rather than catching it. Rewrite it to assert the end-to-end security property (`allows_user(...) is False`) and add a companion case pinning `https://rpc.ankr.com/solana` as likewise denied.

*Without this fix, the first mainnet deployment of the tier gate silently gates nobody: a configuration that looks fully enabled grants premium to every wallet-linked user, and a mainnet provider whose hostname omits the word "mainnet" defeats the devnet-only guardrail in the opposite direction.*

---

### [INFO] F-42 -- `stake()` unconditionally resets `staked_at` on every top-up, so the field is reset by deposits and frozen by withdrawals — exactly backwards for any time-weighted rule

**Severity.** Impact today is exactly zero, so CVSS is not meaningful: no attack vector produces a consequence. `staked_at` has no on-chain reader — the program has exactly two instructions, `stake` and `unstake`, and neither reads it — and no off-chain reader, since `bot/token/tier_gate.py:161-179` filters on `owner` at offset 8 and optionally `mint` at offset 40 and sums only `amount` from `raw[72:80]`, stopping before offset 80. The single consumer anywhere in the repository is a test assertion at `programs/rclaw_staking/tests/rclaw_staking.ts:79` (`assert.isAbove(sa.stakedAt.toNumber(), 0)`), which this defect does not violate. There is no loss of tokens, no authority change and no availability impact. This is not an under-graded High. The escalation condition is narrow and concrete: severity becomes High — free, unbounded inflation of one user's share of a shared emission pool, plus permanent age-freezing of a dust position — the moment any instruction is added that reads `staked_at` for time-weighted accrual, lockup enforcement, or an early-withdrawal penalty. Because `staked_at` is published as part of the documented cross-language layout (`programs/rclaw_staking/src/lib.rs:27` and `programs/rclaw_staking/README.md:35`) and `docs/TOKEN_ROADMAP.md:136` budgets 25% of supply to staking emissions, that consumer is a plausible next commit rather than a hypothetical. This is a latent-defect and deployment-gate note, not a live vulnerability.

**Confidence.** High. CONFIRMED. Votes: 1 sanity check.

**Location.** `programs/rclaw_staking/src/lib.rs:121`

**Code.**
```rust
        sa.staked_at = Clock::get()?.unix_timestamp;
```

**What's wrong.** `staked_at` is assigned the current slot time on every successful `stake`, including a re-stake onto an existing record, and the only bound on `amount` is `require!(amount > 0, StakeError::ZeroAmount)` at `lib.rs:91` — so a one-base-unit top-up suffices. `unstake` does the opposite and never touches the field: `lib.rs:161-168` mutates `amount` only, via `sa.amount = sa.amount.checked_sub(amount).ok_or(StakeError::Overflow)?` at line 162. The field is therefore reset by deposits and frozen by withdrawals. A user can withdraw 99.9% of a position without disturbing the timestamp, but cannot add to it without forfeiting all accrued age. No cast is involved — `Clock::unix_timestamp` is `i64` and the field is `i64` — so there is no truncation or sign bug, only the reset semantics. The `Staked` event at `lib.rs:123-128` carries `owner`, `mint`, `amount` and `total` but not `staked_at`, so an off-chain indexer replaying logs cannot even observe the reset; it would have to poll account state.

A secondary half of the same lifecycle bug: unstaking down to a zero balance also leaves `staked_at` at its stale value on a live, non-closed record, so even the "first stake sets it correctly" invariant fails for the unstake-to-zero-then-top-up path. Both halves need fixing, not just the top-up branch.

**Exploit / reachability.** The state delta is trivially reachable; only the payoff is absent. Alice calls `stake(1_000_000)` at t0 with `owner` her keypair (`Signer`, `lib.rs:176`), `stake_account` the PDA `["stake", owner, mint]` (`lib.rs:187`), `vault_authority` the PDA `["vault", mint]` (`lib.rs:194`), `vault` that authority's ATA, `user_token_account` her own ATA. The record is created with `amount = 1_000_000`, `staked_at = t0`. A year later she calls `stake(1)` with the identical account set. Every guard passes: line 91 accepts `1 > 0` and there is no minimum-amount or minimum-interval bound anywhere; `check_pinned_mint` at line 92 passes on the same mint; the `if sa.amount > 0` owner and mint assertions at lines 98-101 pass because it is her own record; the `transfer_checked` of one base unit at lines 103-115 succeeds. Line 120 sets `amount` to 1,000,001 and line 121 overwrites `staked_at` with t0 + 1yr. Nothing between the entry point and line 121 is conditioned on the amount being material, on time elapsed, or on the record being newly created — `init_if_needed` at line 184 leaves prior state intact and the handler then rewrites the field regardless. The mirror case is equally reachable: Bob calls `unstake(999_999)` and his one-base-unit residual still reports `staked_at = t0`.

Account substitution offers no alternate route. `stake_account` is seed-bound to `(owner, mint)` with Anchor's PDA derivation check plus the discriminator and owner checks implied by `Account<'info, StakeAccount>`, so an attacker cannot aim the reset at someone else's record; the defect is strictly self-inflicted on the caller's own record. What does not land is the payoff: no code path reads the field, so the corrupted value influences nothing.

**Remediation.** Fix the semantics now, while the field has no consumers and the change is free — retrofitting after an emission instruction ships means migrating live records. Make `staked_at` an amount-weighted average so a top-up dilutes age proportionally instead of erasing it, and make unstake-to-zero reset the clock:

```rust
        let now = Clock::get()?.unix_timestamp;
        let new_total = sa.amount.checked_add(amount).ok_or(StakeError::Overflow)?;
        sa.staked_at = if sa.amount == 0 {
            now
        } else {
            // amount-weighted: prior age survives in proportion to the prior balance,
            // so a 1-base-unit top-up cannot erase a large position's accrual.
            let w = ((sa.staked_at as i128) * (sa.amount as i128)
                   + (now as i128) * (amount as i128))
                   / (new_total as i128);
            w as i64
        };
        sa.amount = new_total;
```

The `i128` intermediate cannot overflow — both operands are bounded by `u64::MAX * i64::MAX` — and the quotient is bounded by `max(sa.staked_at, now)`, so the narrowing `as i64` is lossless. In `unstake`, add `if sa.amount == 0 { sa.staked_at = 0; }` after the subtraction at line 162 so a fully-withdrawn record does not carry stale age into its next life. If weighting is unwanted, the minimal alternative is to guard the assignment with `if sa.amount == 0 { sa.staked_at = now; }` — that errs toward the staker but is at least monotonic. Independently, add `staked_at` to both the `Staked` (lines 123-128) and `Unstaked` events so an indexer can observe the value from logs. Until a reader exists, a one-line comment at the struct definition (`lib.rs:262`) stating that the field is written but unconsumed and that its reset semantics are unsuitable for time-weighted accrual is the cheapest guard against a future author trusting the documented offset at `lib.rs:27` and `README.md:35`.

*Without this fix, the first reward, lockup, or penalty instruction that reads `staked_at` ships with a free, unbounded age reset for anyone willing to spend one base unit and one signature, and a dust position that permanently accrues at full weight.*

---

### [INFO] F-45 -- `--dry` mode asserts only that `presale:plan` exits 0, and nothing runs it automatically

**Severity.** Not a vulnerability. There is no adversary, no attacker-controlled input and no on-chain state delta, so a CVSS vector would be meaningless. This is an assurance-scope and test-coverage observation, and Info is the correct grade. It is not lower than Info because the untested code — `derivePresaleParams` at `token/presale/genesis_lib.mjs:110-152` — derives the presale hard cap, per-wallet minimum and maximum, and vesting cliff, which are the parameters that would govern a real raise. It is not higher than Info because the tooling is draft and devnet-only, `rpcUrl` (`genesis_lib.mjs:56-65`) throws on any RPC URL containing "mainnet", reaching a live sale requires several deliberate manual steps that the roadmap gates behind an independent audit (`docs/TOKEN_ROADMAP.md:517-521`), and — importantly — the repository does not overclaim: it self-discloses this exact limitation in its own verification matrix at `docs/TOKEN_ROADMAP.md:502-503`.

**Confidence.** High. CONFIRMED. Votes: 1 refuter (gap sweep). Id assigned from the F-4x gap range; gap-sweep finding with no id from the primary lenses.

**Location.** `token/e2e/devnet_dryrun.mjs:120-127`

**Code.**
```javascript
  if (DRY) {
    // Offline: just prove the config drives the plan derivation, print sequence.
    steps.push(run('plan', ['presale/genesis_presale.mjs', 'plan']));
    log('planned live sequence: keygen → create → liquidity → deposit → (wait) → claim');
    const out = saveReport(steps);
    log(`report: ${out}`);
    process.exit(steps.every((s) => s.ok) ? 0 : 1);
  }
```

**What's wrong.** The harness's success condition is exit status alone: `ok` is defined as `res.status === 0` at `devnet_dryrun.mjs:86`, and line 126 exits on `steps.every((s) => s.ok)`. No stdout is parsed and no derived value is compared against anything — not `allocationQuoteTokenCap`, not the unix window ordering, not the claim schedule.

One correction to how this is usually stated: `cmdPlan` (`token/presale/genesis_presale.mjs:71-102`) is not literally assertion-free, so exit 0 is not a vacuous signal. It has genuine throw paths — `unix()` throws on an unparseable RFC3339 timestamp (`genesis_lib.mjs:89-94`), `baseUnits` throws via `BigInt(whole)` on a non-integer `presaleAllocation` (`genesis_lib.mjs:101-103`), `publicKey(a)` throws on an invalid base58 whitelist entry (`genesis_lib.mjs:179`), and `prepareAllowlist`, `createClaimSchedule` and `createTimeAbsoluteCondition` are real `@metaplex-foundation/genesis` SDK calls that execute for real. So a green `plan` does prove structural and serialization validity. What it does not prove is *value* correctness. `loadConfig` (`genesis_lib.mjs:32-41`) is a bare `JSON.parse(fs.readFileSync(...))` with zero schema validation, and `derivePresaleParams` enforces no invariants at all — nothing asserts `depositStart < depositEnd`, `tge >= depositEnd`, `softCap <= hardCap`, `perWalletMin <= perWalletMax`, or `0 <= cliffAmountBps <= 10000`. Any regression that keeps values well-typed sails through.

The second half is the part with no mitigating nuance: nothing runs this automatically. `.github/workflows/ci.yml` is a single `test` job whose steps are checkout, `actions/setup-python@v5`, `pip install`, three `ruff check` invocations, `mypy`, `bandit`, `pip-audit -r requirements.lock`, and `python scripts/ci_test_gate.py`. There is no `actions/setup-node`, no `npm ci`, no `npm test`, and no reference to `token/` anywhere in the workflow. `scripts/ci_test_gate.py` never invokes Node — its `node` matches are pytest node-IDs. The Makefile has no node or npm target, there are no non-sample git hooks, and there is no `.pre-commit-config.yaml`. `token/package.json` defines no `test` script at all (only `create`, `verify`, `keygen`, the `presale:*`, `e2e:*` and `bridge:*` entries), so `token/presale/allowlist_serialize.test.mjs` — a real `node:test` file that exists on disk — is referenced by nothing.

Two facts widen the gap beyond what the finder claimed. First, this failure mode has already occurred in this repository. `token/presale/allowlist_serialize.test.mjs:5-9` documents a shipped defect — a missing required `padding` field in `AllowlistInitArgs` that made `presale:create` throw a `TypeError` before building any transaction whenever a whitelist artifact existed — and states outright that "`presale:plan` never touched that path, so the 'offline plan runs green' verification could not catch it." The regression test written in response is itself run by no automated gate. Second, the root `package.json:8` defines a `test` script (`ts-mocha` over `programs/rclaw_staking/tests/**/*.ts`) that CI also never runs; `docs/TOKEN_ROADMAP.md:504` records the Anchor TS spec as never executed. One missing `setup-node` step withholds automated coverage from both the presale tooling and the on-chain program's TypeScript spec.

**Exploit / reachability.** Not applicable — no adversary. The concrete demonstration: change `solToLamports` at `genesis_lib.mjs:96-99` to scale by `1e6` instead of `1e9`. On the `plan` path its four outputs (`allocationQuoteTokenCap`, `softCapLamports`, `perWalletMinLamports`, `perWalletMaxLamports`, `genesis_lib.mjs:132-135`) are consumed at exactly one place — `console.log` calls at `genesis_presale.mjs:80-84` — and never passed to a validator or comparison; they reach `addPresaleBucketV2` only under `create`. A smaller-but-valid `BigInt` prints fine, `npm run e2e:plan` exits 0, the harness reports `ok: true`, and CI is entirely unaffected because CI never executes Node. A presale created afterwards carries a hard cap 1000x too small and nothing in the repository's automated gates catches it. This was verified statically; `token/node_modules` is not installed in this environment, so the mutation was not executed.

**Remediation.** Two changes, neither on the critical path to any deployment. First, make CI execute Node at all, as a second job alongside the existing Python-only one:

```yaml
  token-tooling:
    name: Token tooling (offline)
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: { node-version: '20', cache: npm, cache-dependency-path: token/package-lock.json }
      - run: npm ci
        working-directory: token
      - run: node --test presale/*.test.mjs   # picks up allowlist_serialize.test.mjs
        working-directory: token
      - run: npm run e2e:plan
        working-directory: token
```

That alone converts the already-written allowlist regression test from dead code into an enforced gate. Second, give the derivation real assertions so exit 0 means something — a `presale/derive_params.test.mjs` that pins `p.allocationQuoteTokenCap` against `BigInt(Math.round(cfg.sale.hardCapSol * 1e9))` (this is the assertion that catches the `1e9`→`1e6` mutation), and asserts the orderings `perWalletMin <= perWalletMax`, `softCap <= hardCap`, `depositStart < depositEnd`, `depositEnd <= tge <= vestingEnd <= claimEnd`, and `0 <= cliffAmountBps <= 10000`. Those orderings are exactly what `unix()` cannot catch, since it only rejects unparseable strings and never illogical sequences. Better still, promote the invariants into `derivePresaleParams` itself so a wrong-ordering config fails closed before `addPresaleBucketV2` is ever called, rather than merely printing oddly. Add whichever lands to the pre-deployment checklist at `docs/TOKEN_ROADMAP.md:517-521`.

*Without this, the only thing standing between a mis-scaled hard cap or an inverted vesting window and a live on-chain presale is whoever happens to read the printed numbers, and the repository's one existing presale regression test never runs.*

---

### [INFO] F-46 -- `tier_gate.py`'s own docstring states `amount` is read at offset 40 while the code reads `[72:80]`, and nothing keeps the Python offsets in sync with the Rust struct

**Severity.** No attack vector, no privileges, no impact — `C:N/I:N/A:N` in the current tree. The code at `bot/token/tier_gate.py:179` is correct, the `memcmp` filters are correct, and every user resolves to the correct tier today. There is no attacker and no call sequence; the defect is a stale comment. The high-impact branch a reader would infer from the title — a maintainer "fixes" the read to `raw[40:48]`, reads the first eight bytes of the mint pubkey as a `u64`, and grants elite to every staker — is refuted by an enforced regression test, verified numerically below. What survives is the low-impact branch: no mechanism derives the Python offsets from the Rust struct, so a future field insertion desyncs silently. That is latent only, requires a maintainer error rather than an attacker, and is further gated by three independent deployment guards: the program is marked DO NOT DEPLOY at `programs/rclaw_staking/src/lib.rs:3-11`, the gate is feature-flagged off at `bot/token/tier_gate.py:88-94`, and mainnet RPC is refused outright at `bot/token/tier_gate.py:100-104`. Downgraded from the reported Low: grading it Low would imply a security consequence that does not exist in this tree.

**Confidence.** High. CONFIRMED. Votes: 1 refuter (gap sweep). Id assigned from the F-4x gap range; gap-sweep finding with no id from the primary lenses.

**Location.** `bot/token/tier_gate.py:145-153`

**Code.**
```python
def staked_of(wallet: Optional[str]) -> Optional[float]:
    """On-chain *staked* $RCLAW for ``wallet`` in whole tokens.

    Reads the rclaw_staking program's ``StakeAccount`` for this owner via
    ``getProgramAccounts`` + a ``memcmp`` on the owner field (Anchor 8-byte
    discriminator, then owner Pubkey at offset 8), summing ``amount`` (u64 LE)
    at offset 40. No PDA derivation needed. Returns ``None`` when it cannot be
    determined (unconfigured, no wallet, or an RPC error) so callers fail-open.
    """
```

**What's wrong.** The code is correct and the docstring is stale. `programs/rclaw_staking/src/lib.rs:23-32` and `programs/rclaw_staking/README.md:32-36` both document the layout as `8 disc | owner @8 (32) | mint @40 (32) | amount @72 (u64 LE) | staked_at @80 (i64) | bump @88`, total `8 + StakeAccount::SPACE` = 8 + 81 = 89 (`lib.rs:186`, `lib.rs:267-268`). Line 179 reads `raw[72:80]`, which is right. Line 151's "at offset 40" names the *mint* field, and the inline comment eight lines below at `tier_gate.py:157-158` gets it right, so the file contradicts itself inside one function.

`git show b5e9868` ("fix(token): critical vault-drain") is the commit that inserted `mint` into `StakeAccount` and moved `amount` from 40 to 72. Its diff on this file replaced `if len(raw) >= 48: total_base += int.from_bytes(raw[40:48], "little")` with the current 72-based read and updated the inline comment, but not the module docstring — where "offset 40" had been correct for the prior layout. So this is provable drift, and what saved it was that the code was updated by hand, not any test or CI check.

One sub-claim from the original report needs correcting. It asserts the docstring is wrong in two independent ways, the second being that it describes a discriminator that is not filtered on. That is a misreading of the prose: the parenthetical "(Anchor 8-byte discriminator, then owner Pubkey at offset 8)" explains *why* `owner` sits at byte 8, and is factually correct. The docstring is wrong in exactly one way.

The genuinely valuable observation is not the comment at all. Nothing derives the Python offsets from the Rust struct. `tests/test_token_tier_gate.py:213-225` does assert offset 72, but against a hand-written Python mirror at `tests/test_token_tier_gate.py:130-137` (`data = bytearray(89)` with `data[72:80] = int(amount_base).to_bytes(8, "little")`). That pins `tier_gate.py` to the fixture; it does not read `lib.rs`, does not parse the Anchor IDL, and does not compute the discriminator. `.github/workflows/ci.yml` has no `cargo`, no `anchor build` and no IDL diff, so `programs/rclaw_staking/tests/attack.rs` never executes in CI either. Consequently `programs/rclaw_staking/README.md:41`'s claim that "`tests/test_token_tier_gate.py` locks both the offsets and the mint filter" is true only in the Python-versus-Python sense and reads as a cross-language guarantee it does not provide.

**Exploit / reachability.** No attacker-reachable path exists; this is not an instruction handler and there are no accounts to substitute.

The dangerous branch is closed. If a maintainer trusts line 151 and edits line 179 to `raw[40:48]`, `tests/test_token_tier_gate.py:213-225` fails: its fixture is `bytearray(89)` zeroed except `[72:80]`, so `staked_of("W")` returns 0.0 instead of the asserted 7.0. I replicated the fixture arithmetic in a scratchpad script rather than assuming it — `read[72:80]` yields 7.0, `read[40:48]` yields 0.0. The test is enforced: it is absent from `tests/known_failures.txt`, and `scripts/ci_test_gate.py` fails CI on any new failure outside that baseline. (pytest is not installed in this container, so this branch is closed by fixture arithmetic plus baseline inspection rather than a live run.)

The latent branch is real but requires a maintainer, not an attacker. Insert `version: u8` between `mint` and `amount` in `StakeAccount` — which the upgradeability finding recommends — and `mint` stays at offset 40, so the `memcmp` filter at `tier_gate.py:164` still matches and the account is still returned. The length guard at line 178 does not help either, since 90 >= 80 passes. `amount` now sits at 73 while Python still reads `[72:80]`. I simulated it: a 10,000-token stake reads as 2,560,000 tokens, exactly the 256x inflation the finder predicted, resolving a pro staker to elite. The Rust side compiles and its own tests pass; `tests/test_token_tier_gate.py` stays green because its fixture shifts with the reader; CI is green because it never compiles the program. The failure surfaces only as users being granted or denied the wrong tier, with no error anywhere.

**Remediation.** Three changes, in increasing order of value. First, fix the stale docstring at `bot/token/tier_gate.py:150-151` so it reads "…owner Pubkey at offset 8) and — when `RCLAW_MINT` is set — a second `memcmp` on `mint` at offset 40, summing `amount` (u64 LE) at offset 72." Second, correct the overclaim at `programs/rclaw_staking/README.md:41` to say the test pins the reader against a hand-written Python fixture, which catches an accidental edit to `tier_gate.py` but not a field reordering in `StakeAccount`, since the fixture would shift with the reader and stay green.

Third, and the only change that closes the latent branch, add a real cross-language lock. Preferably add a Rust job to `.github/workflows/ci.yml` running `cargo test -p rclaw_staking`, plus a Rust-side test asserting the byte offsets explicitly — `assert_eq!(8 + std::mem::offset_of!(StakeAccount, amount), 72)` (or the `memoffset` equivalent) and `assert_eq!(8 + StakeAccount::SPACE, 89)`. That makes a field insertion fail in Rust CI, at the point of change rather than downstream, and has the side benefit of finally executing `programs/rclaw_staking/tests/attack.rs`. The weaker alternative is deriving the offsets in Python from `target/idl/rclaw_staking.json` at test time instead of hardcoding 8/40/72, which requires a committed or built IDL.

*Without the cross-language lock, the next field added to `StakeAccount` silently misreads every user's stake by a factor of 256 with a fully green test suite and a fully green CI, and the exact desync it guards against has already happened once, in commit b5e9868.*

## Refuted Candidates

Each row below is a defence that is currently doing real work. If a refactor removes one of these guards, the candidate stops being refuted and becomes live — so treat this table as a list of load-bearing invariants, not merely as a record of what was rejected.

| Candidate | Original severity | Why it fails |
|---|---|---|
| **F-13** — whitelist round's `quoteCap` equals the full hard cap, so the OG round can starve the public round (`token/presale/genesis_lib.mjs:181-182`) | Medium | The first step of the claimed exploit is "the operator adds 200+ addresses to `cfg.whitelist`" — a plaintext config array the operator owns, read at `genesis_presale.mjs:214`. The trust root choosing its own parameters is not an exploit; the same operator can already set `hardCapSol` to 5000 and never open a public round. Non-whitelisted wallets are blocked by the on-chain Merkle root (`genesis_lib.mjs:199`) and by the client guard at `genesis_presale.mjs:198-201`. The single shared cap is a documented design choice, stated inline at `genesis_lib.mjs:175-176`. The *price* half of the roadmap mismatch is real and survives as F-37. |
| **F-27** — `create_token.mjs` has no idempotency guard, so a second run mints another full 1B supply (`token/scripts/create_token.mjs:47`) | Low | The duplicate mint is an orphan with no economic role. No consumer auto-adopts the overwritten artifact: `metaplex-genesis.config.json:11` and `ntt.config.json:10-11` ship `<FILL_FROM …>` placeholders requiring manual transcription, and the one automated reader — `verify_token.mjs:29-38` — re-derives from chain and rejects a live mint authority under the shipped config (`token.config.json:14`). The impersonation argument is EVM instinct: Token-2022 metadata carries no exclusivity, so a look-alike mint costs any stranger ~0.002 SOL regardless of this bug. Partial-run recovery remains a genuine runbook gap — deferred, see Coverage. |
| **F-32** — `GENESIS_PROGRAM_ID` is display-only and never compared against the program the SDK targets (`token/presale/genesis_presale.mjs:47`) | Low | The premise is false today. The integrity-pinned tarball for `@metaplex-foundation/genesis@0.40.0` (`token/package-lock.json:776-780`) embeds the identical constant, so the artifact record at `genesis_presale.mjs:163` is accurate rather than fabricated. The proposed fix is also circular: `umi.programs.get('genesis').publicKey` is supplied by the very package the threat model assumes hostile. Hardcoding a constant the SDK exports is real hygiene debt, not a security boundary. |
| **F-33** — the NTT bridge passes the manager address as the Wormhole transceiver (`token/bridge/ntt_bridge.mjs:120-122`) | Low | On Solana the Wormhole transceiver is an embedded module of the NTT program, so `transceiver: { wormhole: src.manager }` is the correct value for the default (Solana-source) direction. More decisively, nothing executes: `srcNtt.transfer(...)` returns an AsyncGenerator that is never iterated and is discarded at `ntt_bridge.mjs:138-140`, and `ntt_bridge.mjs:58-60` refuses any network other than `Testnet`. The `--reverse` (EVM-source) leg would need a distinct transceiver proxy, but the worst outcome there is a reverting transaction, not a silent lock. |
| **F-36** — 37 npm advisories including an unpatchable `bigint-buffer` overflow on the live decode path (`token/package-lock.json`) | Low | The decode path is length-pinned before the native code is reached: `@solana/spl-token@0.4.15` `unpackMint` throws on short data and slices to exactly `MINT_SIZE`, and `@solana/buffer-layout-utils@0.3.0` defines `u64` as a fixed `blob(8)`, so the buffer handed to `toBigIntLE` is always 8 bytes and every dangerous branch in `bigint-buffer-1.1.5/src/bigint-buffer.c:52-60` is unreachable. The stated impact is also wrong for the named script — `verify_token.mjs` never calls `loadKeypair` and holds no secret key. The absent `npm audit` gate is real and survives as F-20. |
| **F-38** — the LP lock is permanent (never-claim) while the RUNBOOK says "locked ≥ 12 months" (`token/presale/genesis_lib.mjs:230-238`) | Low | `docs/TOKEN_ROADMAP.md:237-241` names the 12-month phrasing as an earlier baseline and explicitly supersedes it, and `docs/TOKEN_ROADMAP.md:439-440` records the decision as settled. The RUNBOOK defers to the roadmap by name at `token/presale/RUNBOOK.md:111-113`. A permanent lock also *satisfies* the `RUNBOOK.md:102` checkbox rather than violating it. Recommending a weaker lock would be a net regression. |
| **F-41** — `PINNED_MINT` defaults to `None`, so the mint invariant lives in an off-chain memcmp (`programs/rclaw_staking/src/lib.rs:63`) | Info | The mint field cannot be forged: `sa.mint` is written from the same mint account `transfer_checked` debits (`lib.rs:119`), cross-checked by `user_token_account.mint == mint.key()` (`lib.rs:210`) and `associated_token::mint = mint` (`lib.rs:201`), so a record carrying the real mint required escrowing real tokens. Off-chain, `gate_enabled()` requires a non-empty `RCLAW_MINT` (`tier_gate.py:94`), so the offset-40 memcmp (`tier_gate.py:163-164`) is applied on every decision. The residual "`RCLAW_MINT` unset" branch is not a bypass — it disables the gate for everyone. |
| **F-43** — interactions-before-effects in `unstake`; state written after the CPI (`programs/rclaw_staking/src/lib.rs:146-162`) | Info | The Solana runtime's reentrancy check is stack-membership based, not identity based, so it kills both the transfer-hook route and the finder's hypothetical "future CPI to an intermediate program" route. Independently, `stake_account` is program-owned, so no CPI callee can write it, and `lib.rs:139` already bounds `amount <= staked`, making the `checked_sub` at `lib.rs:162` provably dead. Writing state before the CPI is better hygiene and worth doing; the stated "repeat until the vault is empty" impact is unreachable in both the current and the hypothetical code. |

No candidates were discarded at triage. Every issue any lens raised was carried into full adversarial verification, and the eight above were killed there rather than filtered out early. That is a deliberate property of this pass — a cheap triage filter would have dropped F-41 and F-43 as "Info anyway" and, with them, the two most useful structural observations in this table.

## Clean Areas

The strongest part of this codebase is the on-chain program's account-constraint model: every account in both `Accounts` structs is pinned by a signer, by PDA seeds, by a type-checked program id, or by an explicit owner-and-mint constraint, and the one deliberately unconstrained account is harmless by construction.

**Account substitution — the #1 Solana bug class**

- `Unstake` constrains the stake record three ways at once: `seeds = [b"stake", owner, mint]`, `bump = stake_account.bump`, `has_one = owner`, `has_one = mint` (`lib.rs:226-233`). This is what structurally blocks the historical mint-confusion drain.
- `Stake.stake_account` is `init_if_needed` under the same seeds (`lib.rs:183-190`) with `owner: Signer<'info>` (`lib.rs:175-176`), so the address is a pure function of the signer and the mint — a caller can only ever address their own record.
- `vault_authority` is `seeds = [b"vault", mint.key().as_ref()], bump` in both structs (`lib.rs:194`, `lib.rs:236`). Its `UncheckedAccount` type carries no owner or discriminator check, but Anchor's `find_program_address` plus key equality is total, so substitution fails regardless.
- The vault is pinned to the canonical per-mint ATA by `associated_token::{mint, authority, token_program}` in both structs (`lib.rs:198-205`, `lib.rs:239-245`), which compiles to field checks *plus* an address check against `get_associated_token_address_with_program_id`. This was confirmed by macro expansion and by an ad-hoc decoy test (`ConstraintAssociated` 2009 on unstake, `AccountNotAssociatedTokenAccount` 3014 on stake); it is **not** covered by the committed suite — see Coverage.
- `user_token_account` carries `owner == owner.key()` and `mint == mint.key()` in both structs (`lib.rs:207-212`, `lib.rs:247-252`), blocking unstake-to-a-third-party, stake-from-a-victim, and passing the vault itself as the user account.
- `token_program: Interface<'info, TokenInterface>` (`lib.rs:214`, `lib.rs:254`) restricts the key to `spl_token::ID` or `spl_token_2022::ID`, so the Solana analogue of delegatecall to caller-supplied code does not exist; `associated_token_program` and `system_program` are `Program<'info, _>` and likewise address-checked (`lib.rs:215-216`).
- The unstake signer seeds are rebuilt from `ctx.accounts.mint.key()` and `ctx.bumps.vault_authority` (`lib.rs:141-144`) — the canonical values Anchor validated moments earlier, never a caller-supplied bump. There is no seed-forging path and no way to redirect the PDA's signature to another token account.
- The only unconstrained account is `mint` (`lib.rs:180`, `lib.rs:224`), and substituting it is inert: the stake record, the vault authority and the vault ATA are all derived from it, so an attacker's arbitrary mint receives an isolated namespace rather than access to anyone else's.
- There is no privileged instruction anywhere in the 338 lines — no `initialize`, admin account, config PDA, pause, `close`, or emergency withdraw. Only `stake` (`lib.rs:90`) and `unstake` (`lib.rs:137`) exist, both user-scoped, so the uninitialized-initializer and init-front-running classes have no surface. (The trade-off is that the BPF upgrade authority becomes the whole privileged surface — F-02.)

**CPI and re-entry**

- Both CPIs use plain `CpiContext::new` / `new_with_signer` with no remaining accounts, and neither handler reads `ctx.remaining_accounts` (`lib.rs:103-115`, `lib.rs:146-159`), so there is no attacker-supplied account path into a CPI.
- No account is read after a CPI. `vault.amount` and `user_token_account.amount` are never read anywhere in the program, so the absent `reload()` has no consequence. (The converse defect — never reading the vault at all — is F-08.)
- The program issues no `Approve`, `Revoke`, `SetAuthority` or `CloseAccount` CPI, so there is no SPL delegate/approve race and the vault can never be delegated.
- `unstake` deliberately omits `check_pinned_mint` (contrast `lib.rs:92`). This is correct: tightening `RCLAW_PINNED_MINT` and redeploying cannot strand already-escrowed tokens, and `has_one = mint` (`lib.rs:231`) keeps the relaxation from widening anything.

**Arithmetic**

- Program code contains exactly two arithmetic sites outside `#[cfg(test)]`: `checked_add` at `lib.rs:120` and `checked_sub` at `lib.rs:162`. There are zero `as` casts, zero divisions, zero multiplications and zero `unwrap`/`expect`, so truncation, narrowing and precision-loss classes have no surface here.
- `overflow-checks = true` genuinely governs the shipped artifact: it is set in the workspace-root `[profile.release]` (`Cargo.toml:8-9`) with `members = ["programs/*"]` (`Cargo.toml:6`), the member manifest declares no `[profile.*]`, there is no `[profile.release.package.*]` override anywhere, and `cargo build-sbf` builds release.
- There is no share or ratio math to inflate: `StakeAccount` stores a raw base-unit `u64` (`lib.rs:257-264`), `stake` adds exactly `amount` and `unstake` subtracts exactly `amount`. The first-depositor share-inflation class does not apply.
- Base-unit conversion is BigInt end to end in every script that performs one: `create_token.mjs:45` and `verify_token.mjs:25` both compute `BigInt(cfg.totalSupply) * 10n ** BigInt(cfg.decimals)` = 1e18, comfortably under `u64::MAX`, and `baseUnits` (`genesis_lib.mjs:101-103`) never touches a float. `solToLamports` (`genesis_lib.mjs:96-99`) has a float intermediate, but every committed value (0.25, 25, 1000, 5000 SOL) is an exactly representable integer-valued double far below 2^53 and `Math.round` precedes the `BigInt()` call.

**Account layout, initialization and squatting**

- `StakeAccount` is byte-exact: 32 + 32 + 8 + 8 + 1 = 81 = `SPACE` (`lib.rs:268`), and `space = 8 + StakeAccount::SPACE` = 89 (`lib.rs:186`) accounts for the discriminator exactly once. Every field is fixed-size Borsh, so nothing can push serialization past the allocation.
- `init_if_needed` is not a re-initialization vector here: the seeds contain the signer (`lib.rs:187`), and `stake` writes `sa.amount = sa.amount.checked_add(amount)` (`lib.rs:120`) rather than an assignment, so a prior balance cannot be zeroed by re-staking.
- The stored bump used by `Unstake` (`bump = stake_account.bump`, `lib.rs:229`) cannot be poisoned: its only writer is `lib.rs:122`, taking `ctx.bumps.stake_account`, which Anchor derives canonically in the same transaction that creates the account.
- `grep` over `programs/` returns zero hits for `zero_copy`, `repr(C)`, `realloc`, `AccountLoader` and `close =`, so there is no manual-layout, realloc-truncation, or account-revival surface at all.
- Neither PDA can be squatted. The stake PDA has no private key, and Anchor 0.30.1's `init` falls back to transfer + allocate + assign when the address is pre-funded; the vault is an ATA-program PDA that only that program can create, and Anchor re-validates it afterwards.

**The off-chain tier gate**

- Default-off is genuinely inert: `allows_user` returns `True` at `tier_gate.py:222-223` before resolving a wallet or issuing any RPC, and the module has no import-time side effects. `tests/test_token_tier_gate.py:38-51` locks both the flag-off and flag-on-without-mint cases.
- The mint filter cannot be dropped on any path that grants a tier, because `gate_enabled()` requires `bool(mint_address())` (`tier_gate.py:88-94`) and the offset-40 memcmp is appended whenever a mint is set (`tier_gate.py:163-164`). State the scope precisely: this excludes foreign-mint stakes; it constrains neither account type, nor account size, nor whose stake is being read.
- The field it filters on is trustworthy, because `sa.mint` is written from the same mint account `transfer_checked` debits (`lib.rs:119`) and `unstake` enforces `has_one = mint` (`lib.rs:231`).
- The module holds no key material and signs nothing — a single `urllib` POST at `tier_gate.py:105-109` — so a compromise yields a wrong authorization decision, never a token movement.
- `_resolve_wallet` fails **closed** (`tier_gate.py:205-211` with the check at `:227-229`): a broken or legacy `UserStore` denies premium rather than granting it, which is the opposite of the RPC path's policy and the right direction.
- The summing loop cannot overflow or truncate: `int.from_bytes(raw[72:80], "little")` (`tier_gate.py:179`) is arbitrary-precision, and the single lossy step — the float division at `:183` — stays far inside double precision against the 10,000 / 100,000 thresholds.
- The `len(raw) >= 80` guard (`tier_gate.py:178`) does reject the pre-fix 57-byte layout, and `tests/test_token_tier_gate.py:213-225` pins the offset-72 read.

**Secrets and key material**

- Nothing secret is committed. Four independent scans over tracked files — 85-90-character base58 strings, JSON arrays of 64 integers, provider credential prefixes (`sk-`, `gsk_`, `AIza`, `xox*`, `ghp_`, `AKIA`, `BEGIN … PRIVATE KEY`, the Telegram bot-token shape), and keypair-shaped filenames — returned only three obvious test fixtures (`tests/test_llm_key_encryption.py:47`, `tests/test_llm_key_health.py:48`, `tests/test_ultra_ai.py:32`). The 684-line root `.env.example` has every credential slot empty.
- Generated material is excluded from git: `token/.gitignore:2-5` covers `.keys/`, `.artifacts/`, `.env` and `node_modules/`, and root `.gitignore:101-103` covers `target/`, `.anchor/` and `test-ledger/`, so Anchor's `target/deploy/*-keypair.json` is covered too. (Root-level keypair coverage for paths outside `token/` is F-40.)
- `keygen.mjs:13-14` is idempotent and its airdrop block is try/caught and balance-guarded (`keygen.mjs:23-40`), so a re-run — including one driven by the e2e harness — cannot destroy an existing authority key or hammer the faucet.

**Dependency pinning**

- `Cargo.lock` is committed at lockfile version 4 with 624 packages; every `[[package]]` carries `source = "registry+https://github.com/rust-lang/crates.io-index"` and a checksum, so no crate can be substituted from a git, path or patched source.
- Both npm lockfiles are committed at lockfileVersion 3, and every entry in `token/package-lock.json` resolves to `registry.npmjs.org` with an integrity hash — no git URLs, tarball URLs, `file:` links or aliases.
- The apparent Wormhole major-version mismatch is not a defect: `token/package.json:37-39` pins `@wormhole-foundation/sdk` 5.2.0 and both NTT packages at 7.2.0 exactly, and the 7.2.0 NTT packages peer-depend on `^5.0.0` of `sdk-base`/`sdk-definitions`/`sdk-solana`/`sdk-evm`, which 5.2.0 satisfies.
- Python SCA is properly gated — `pip-audit -r requirements.lock` at `.github/workflows/ci.yml:58-59`, plus bandit at `:55-56` — which establishes that the Node and Rust gaps are specific omissions rather than a project-wide blind spot.

**Cluster guards — placement, not strength**

- The NTT bridge's gate is a strict equality evaluated before any chain context exists (`ntt_bridge.mjs:58-60`), and the SDK is built from `cfg.network` (`ntt_bridge.mjs:116`), not from the config's `rpc` fields, so editing an RPC URL cannot reach mainnet. Its placeholder detection (`unfilled()`, `ntt_bridge.mjs:43-45`) is exactly the pattern the presale configs lack.
- Every privileged presale command reaches the refusal: `rpcUrl()` (`genesis_lib.mjs:56-65`) is called unconditionally by `makeUmi` (`genesis_lib.mjs:79`), which is the first thing create, deposit, liquidity, claim, withdraw and withdraw-unsold each do, and the keygen path is covered by `getConnection` (`lib.mjs:28-36`). The substring implementation is weak (F-19); the placement leaves no bypass.
- CLI dispatch fails closed via fixed command tables with `process.exit(2)` on an unknown command (`genesis_presale.mjs:322-339`, `ntt_bridge.mjs:143-148`) — no dynamic dispatch, no `eval`, nothing from `argv` reaching a code path.

**Tokenomics and config consistency**

- The allocation table sums to exactly 100% and exactly 1,000,000,000 tokens (`docs/TOKEN_ROADMAP.md:134-141`), matching `totalSupply: "1000000000"` in both `token/config/token.config.json:6` and `token/presale/metaplex-genesis.config.json:9`.
- Sale economics agree field by field across `smithii.config.json`, `metaplex-genesis.config.json` and the roadmap: 150,000,000 presale allocation, 1000/5000 SOL soft and hard caps, 0.25/25 SOL contribution bounds, and a 48h whitelist followed by a 72h public round that the absolute timeline at `metaplex-genesis.config.json:33-35` reproduces exactly. Vesting agrees too: 33% at TGE encoded as `cliffAmountBps: 3300`, with a two-month linear tail.
- The whitelist window is deliberately non-degenerate: `derivePresaleParams` opens deposits at `whitelistStart` rather than `publicStart` precisely so `[whitelistStart, publicStart)` is non-empty, with the reasoning stated inline (`genesis_lib.mjs:114-119`).
- Vesting is uniform across depositors by construction — `createClaimSchedule` uses absolute `startTime`/`cliffTime` = TGE (`genesis_lib.mjs:142-148`), not a per-deposit clock — so no deposit-timing unlock advantage exists.
- The allowlist serializer has a genuine offline regression test guarding a named failure: the required fixed-size `u8[6]` `padding` field whose omission makes the umi serializer throw before any transaction is built (`genesis_lib.mjs:169-171`, `:198`; test in `token/presale/allowlist_serialize.test.mjs`). Nothing in CI runs it.

**Test evidence that actually executes**

- `attack.rs` derives the current post-fix PDAs — `[b"stake", owner, mint]` and `[b"vault", mint]` at `attack.rs:152-158` — and `mint_confusion_attack_is_rejected` (`attack.rs:280-327`) runs the historical vault drain in-process against the real program, asserting both that it errors and that the honest vault's balance is byte-for-byte unchanged (`attack.rs:326`). Be precise about what it proves: the crossed record fails the seeds check on `stake_account` first (`ConstraintSeeds` 2006, the log reproduced at `programs/rclaw_staking/README.md:57-62`), so it exercises the seeds leg, not the vault ATA pin.
- `cannot_unstake_more_than_staked` (`attack.rs:247-273`) and `vault_authority_is_mint_scoped` (`attack.rs:332-341`) execute the over-withdrawal rejection and per-mint PDA distinctness respectively.
- The pin logic is unit-tested including the case that matters: a malformed `RCLAW_PINNED_MINT` yields `InvalidPinnedMint` rather than silently accepting every mint (`lib.rs:330-337`).

## Coverage & Limitations

File coverage is complete. Diffing the in-scope list against the files actually opened produced zero omissions: every on-chain source file, every `.mjs` under `token/`, every config in `token/config/` and `token/presale/`, and the tier-gate consumer in `bot/` were read. The only tracked in-scope-adjacent file never opened is `token/e2e/README.md`, which is documentation. Handler coverage is likewise complete — `programs/rclaw_staking/src/lib.rs` contains exactly two instruction handlers, `stake` (`lib.rs:90`) and `unstake` (`lib.rs:137`), plus two free functions (`enforce_pinned_mint` at `lib.rs:67` and `check_pinned_mint` at `lib.rs:80`), and all four are the subject of findings or verified clean-area claims. There is no unexamined entry point.

One clean-area claim was corrected during verification and the correction matters for the maintainer. An earlier draft asserted that the vault's canonical-ATA address check "is executed against the real program in `tests/attack.rs:280-327`." It is not. In that test the attacker passes `stake_account: stake_pda(owner, evil)` with `mint: rclaw`, so Anchor's seeds check on `stake_account` (`lib.rs:228`) fails first and the `associated_token::*` constraints on `vault` are never evaluated. The ATA pin was confirmed by macro expansion and by an ad-hoc decoy test written during this audit — a token account whose `owner` field is the genuine off-curve `vault_authority(RCLAW)` PDA, rejected with `ConstraintAssociated` (2009) on unstake and `AccountNotAssociatedTokenAccount` (3014) on stake. That decoy test is **not committed to the repository**. The single most important guard in the vault-drain remediation therefore has no permanent regression coverage, and porting the decoy case into `attack.rs` is the cheapest coverage improvement available.

Two areas were deliberately deferred rather than swept, and a reader should treat them as unexamined:

1. **The Anchor IDL account and the deploy-time authority lifecycle.** The IDL is never mentioned anywhere in this audit. It is a live surface: `programs/rclaw_staking/Cargo.toml` has `default = []` with `no-idl` disabled, so an IDL is emitted, and Anchor 0.30.1 stores it in a program-owned PDA whose authority is claimed by whoever calls `anchor idl init` first. Nothing in the repository ever initializes, versions, or assigns authority over it — `deploy.sh` contains no Solana commands, the `Makefile` and root `package.json` contain no anchor invocations, and `.github/workflows/ci.yml` has no anchor step. F-02 and F-22 each cover an adjacent piece (upgrade authority; placeholder program id); the IDL account itself is unowned in this analysis.

2. **Partial-failure state of the three-transaction mint sequence** in `token/scripts/create_token.mjs:101-127`, and the absence of priority fees, retry, or blockhash-expiry handling anywhere in `token/`. The failure shape is specific: tx1 and tx2 succeed, tx3 (revoke mint authority, `create_token.mjs:118-124`) fails, and the process throws before `saveArtifact` at `:144` — leaving a mint holding the full supply *with a live mint authority* and no artifact recording its address, which also means `npm run verify` cannot be pointed at it. F-15 covers this class for the presale path; the mint path was never analyzed for it.

What a reader should **not** conclude from this audit:

- **No fuzzing and no formal verification** were performed. No property-based or differential testing was run against either handler; no invariant (including vault solvency) was mechanically proven.
- **No live devnet deployment and no SBF build.** The program was read and reasoned about, and exercised only through the committed `solana-program-test` suite, which runs the entry point in-process. `anchor build`, `anchor test` and `cargo build-sbf` were never run, so the BPF/SBF runtime — compute-budget behaviour, stack depth, syscall differences — is untested. Note also that `skip_if_pinned!` (`attack.rs:38-50`) causes three of the four integration tests, including the headline vault-drain case, to self-skip under a pinned build. A pinned build is the only configuration intended to hold value, and it exercises none of the vault logic.
- **No transaction was ever sent by any script in `token/`.** Per `docs/TOKEN_ROADMAP.md:462-507`, presale create, deposit, claim, liquidity and withdraw have never been executed against any cluster. Every claim about the Genesis program's behaviour — whether `withdrawUnsoldPresaleV1` is callable before `depositEnd`, whether `withdrawPresaleV1` survives the deposit window, how `addRaydiumCpmmBucketV2` sources its quote side — is an inference from the SDK surface, not an observation. `token/node_modules` is not installed in this checkout, so the Genesis SDK's internals could not be read either.
- **Dependency advisories are current only as of the audit date, 2026-07-25.** The npm counts (1 critical, 15 high, 15 moderate, 6 low across 382 production dependencies in `token/`) and the RustSec matches against `Cargo.lock`'s 624 packages are point-in-time. Because no `npm audit` or `cargo audit` step exists in CI, a new advisory landing tomorrow will not surface anywhere.
- **`bot/` was reviewed along the token trust chain, not as a full application audit.** `bot/token/tier_gate.py` was read in full and `bot/skills/telegram_handler.py` was read only at the wallet-linking and gate-dispatch sites (`:8095-8130`, `:1727-1738`) and `bot/utils/user_store.py` only at `set_sol_wallet`. Findings F-01, F-03 and the two unnumbered tier-gate items are what the token trust chain surfaced; they are not a statement about the trading bot's overall security posture, which was not in scope.

## Prioritized Remediation Plan

### (A) Before ANY mainnet deployment

1. **Restore automated enforcement.** Add a Rust/Anchor job to `.github/workflows/ci.yml` running `cargo test -p rclaw_staking --all-targets`, `cargo clippy -- -D warnings` and a release build, plus `npm ci` + `npm run typecheck`, plus secret scanning; make them required checks on `main`; fix the pre-fix PDAs in `programs/rclaw_staking/tests/rclaw_staking.ts:31-38` and port the ATA-decoy case into `attack.rs`. Closes the unnumbered CI finding, **F-34**, and the enforcement half of **F-40**. Do this first: every remaining item in this plan is a guard that a future refactor can silently delete. *Without it nothing in this report is enforced — the mint-scoped seeds that block the vault drain can be reverted and CI stays green.*
2. **Land one layout-freezing commit.** Add a `version: u8` first field and reserved trailing space to `StakeAccount`, fix the `staked_at` semantics so a top-up dilutes rather than erases accrued age, add `unlock_at`, update the offsets in `bot/token/tier_gate.py` and `programs/rclaw_staking/README.md` in the same commit, and add a machine-checked cross-language offset assertion. Closes **F-35**, **F-42**, the on-chain half of **F-18**, and the offset-drift item. These must ship together — three separate layout changes mean three migrations. *Without it, after the first value-bearing deployment this becomes exactly the migration the program cannot perform: there is no realloc, no migrate handler, and no authority able to sign as the vault PDA outside `unstake`.*
3. **Fix vault solvency and vet the mint.** Credit the observed vault delta rather than the requested amount in `stake`, and reject freeze-capable mints and hostile Token-2022 extensions (`TransferFeeConfig`, `PermanentDelegate`, `TransferHook`, `DefaultAccountState`) at stake time only — never in `unstake`, where a constraint would itself become a bricking vector. Closes **F-08** and **F-07**. *Without it a fee-bearing or permanent-delegate mint leaves Σ `stake_account.amount` greater than the vault balance and the last unstakers cannot withdraw; a freeze authority or default-frozen state bricks the vault with no recovery instruction.*
4. **Make the build reproducible and the program identifiable.** Run `anchor keys sync`, publish the resulting id, populate `[toolchain]` in `Anchor.toml:3` and add a `rust-toolchain.toml` pinning an exact patch channel, replace `option_env!("RCLAW_PINNED_MINT")` with a committed literal once the canonical mint exists, and ship `anchor build --verifiable` plus `solana-verify verify-from-repo` per tagged release. Closes **F-22** and **F-23**. Do this before item 5 — a published, verifiable id is what makes the subsequent authority transfer auditable by a third party. *Without it the deployed bytecode cannot be matched to this source, so a malicious upgrade is undetectable in practice even though it is technically on-chain.*
5. **Move the upgrade authority off the deploy key before the vault accepts its first deposit.** Transfer to a Squads multisig with a timelock, publish the result of `solana program show`, and publish the immutability plan (multisig-then-`--final`, never straight to `--final` on an unaudited program). Closes **F-02**. *Without it a single filesystem keypair can replace the bytecode and sign for every `["vault", mint]` PDA; no on-chain constraint in this program mitigates that, because there is no admin, config PDA or pause to constrain it with.*
6. **Replace every substring cluster guard with an authoritative, fail-closed check.** Resolve `getGenesisHash()` and allowlist the permitted chains in `token/scripts/lib.mjs:28-37`, `token/presale/genesis_lib.mjs:56-65` and `bot/token/tier_gate.py:99-104`; reject an unrecognized genesis hash rather than accepting it. Closes **F-19**. *Without it a private mainnet RPC whose hostname omits the literal string "mainnet" walks straight past the only barrier standing between this draft tooling and real funds.*
7. **Fix key custody and separation of duties.** Write `.keys/` at 0700 and the keypair at 0600 with an explicit `chmod` after `mkdirSync`, refuse to load a group- or world-readable key in both `lib.mjs:39-47` and `genesis_lib.mjs:67-75`, add `**/.keys/` and `**/.env` to `.dockerignore`, extend root `.gitignore` with keypair patterns, and split mint, freeze, metadata-update, metadata-pointer and presale authority across a multisig with the supply-holding ATA under a distinct key. Closes **F-04** and the coverage half of **F-40**. *Without it one plaintext, world-readable file holds every authority plus the entire 1B supply, and a single read is total loss with no recovery path.*
8. **Make the mint's safety properties absolute rather than config-relative.** Assert `mintAuthority === null` and `freezeAuthority === null` unconditionally in `verify_token.mjs:29-38`; refuse to verify at all if either `authorities.*` flag is false; add checks for the metadata URI, `additionalMetadata`, the metadata update authority and the metadata-pointer authority; renounce both surviving authorities in `create_token.mjs` after the final metadata write; actually write the `additionalMetadata` the script already pays rent for; and repoint `metadataUri` to Arweave/IPFS or a pinned commit SHA. Closes **F-05**, **F-06**, **F-21** and **F-28**. Ordering is load-bearing within this item: repoint the URI to immutable storage *before* renouncing the metadata update authority, or the token's displayed identity is permanently delegated to whoever controls the mutable path. *Without it the mint ships with two live authorities that nothing checks, and the verifier that is supposed to catch this is a tautology that turns green the moment someone flips the flag it reads.*

### (B) Before a public presale

1. **Check every confirmation.** Route all seven `sendAndConfirm` calls through a helper that throws on a non-null `result.value.err` and prints the signature, and move the artifact write and `=== DONE ===` after both sends confirm. Closes **F-16**. Do this first — every verification step below is worthless while a transaction that landed and failed reports success. *Without it the operator's only evidence that a presale was configured correctly is a success message that a failed transaction also produces.*
2. **Make the allowlist fail closed.** Refuse to create when `config.whitelist` is non-empty and the artifact is missing, re-derive the Merkle root from the live config instead of trusting the artifact, read the `PresaleBucketV2` account back and assert `allowlist.enabled` matches intent, and gate the depositor's proof branch on the wall-clock window rather than on file existence. Closes **F-09** and **F-10**. *Without it, forgetting `npm run presale:whitelist` silently ships a fully open sale that starts 48 hours early, and a stale artifact on disk permanently locks every non-whitelisted wallet out of the public round.*
3. **Encode the raise-to-liquidity split on-chain, or stop calling it a guarantee.** Add the `endBehaviors: [SendQuoteTokenPercentage]` the code's own comment names at `genesis_presale.mjs:253-254`, make `cmdLiquidity` throw if `raisedSolToLiquidityBps > 0` and no quote-routing behaviour was set, and fix the pricing invariant so the pool cannot open below the presale price. Closes **F-11** and **F-25**. Ordering: never create the irrevocable never-claim LP lock before the quote-side routing is encoded. *Without it the permanently locked Raydium bucket is created with 100M tokens and no protocol-routed quote side, and buyers were told a 60% split was a protocol property when it is an operator undertaking.*
4. **Reconcile every promise the code cannot keep.** Either implement the soft-cap refund or retract it from `metaplex-genesis.config.json:28` and `docs/TOKEN_ROADMAP.md:177`; either implement the OG discount as a second priced bucket or amend §5 to describe priority access at one price; and either delete the inert `antiAbuse` block or convert it into a self-documenting index. Closes **F-12**, **F-37** and **F-14**. *Without it the published sale terms overstate a purchaser's protections and economics — a disclosure exposure that the current mainnet refusal only defers.*
5. **Validate the config and the mint being sold.** Add ordering and economic invariants to `derivePresaleParams` (deposit start before end, end at or before TGE, TGE before claim end, min at or below max, max at or below hard cap), make the `mint`-mode default coherent with the runbook and throw when `token.mint` is set but ignored, and add a `presale:verify` command that checks the artifact's `baseMint` against chain. Closes **F-30** and **F-31**. *Without it a transposed date or a wrong funding mode becomes an immutable on-chain condition, and the mint actually sold is never verified against anything.*
6. **Make the operational paths safe to re-run.** Compose `initializeV2` and `addPresaleBucketV2` into one transaction (or persist the base-mint identity before tx1), refuse to overwrite an existing `presale.devnet.json`, parameterize the `withdraw-unsold` recipient and make the genesis authority a multisig at creation time, and make the e2e harness abort on the first failed step with a per-run artifact name. Closes **F-15**, **F-17** and the e2e-harness finding. Within this item, the multisig authority must land before or with the `--recipient` flag: shipping the flag alone makes arbitrary-destination withdrawal easier while leaving the single hot key intact. *Without it a partial failure strands the genesis account and destroys the only copy of the base-mint keypair, and a stale artifact lets a dry run sign a permanent LP lock against a real presale.*

### (C) Before enabling the tier gate

1. **Require proof of wallet ownership.** Split `/linkwallet` into an address step that issues a single-use, expiring, Telegram-id-bound nonce and a verify step that checks an ed25519 signature before calling `set_sol_wallet`; store a `sol_wallet_verified_at` flag and treat legacy addresses as unverified; enforce one-wallet-per-account inside the existing lock in `bot/utils/user_store.py:455-469`. Closes **F-01**. Do this first — `cryptography>=43.0.1` is already a declared dependency and `Ed25519PublicKey` is already used at `bot/proofofpnl/erc8004.py:226-228`. *Without it every other control in this group is decorative: any user can type a whale's public address and inherit its tier.*
2. **Close the ungated dispatch paths.** Call `_token_gate_blocks` in the natural-language intent branch at `bot/skills/telegram_handler.py:1727-1738`, audit the identical mapping at `bot/web/user_gateway.py:325-326`, and enforce the gate once inside the `pro_scan` dispatch rather than at each caller. Part of **F-03**. *Without it typing "scalp" bypasses the paywall in every configuration, gate enabled or not.*
3. **Fail closed on anything that is not a transient infra error.** Distinguish permanent misconfiguration and policy refusal from a genuine RPC hiccup, deny premium on the former, and validate that a linked address base58-decodes to exactly 32 bytes at the trust boundary so an invalid address cannot ride the fail-open channel. Also rewrite `tests/test_token_tier_gate.py:117-125`, which currently asserts the fail-open behaviour and will fail after this change. Closes **F-03** and the two unnumbered tier-gate Lows. *Without it the paid gate unlocks for every user the moment it is pointed at mainnet, and anyone able to degrade the RPC — or simply to link a malformed address — opens it for themselves.*
4. **Honour the lockup off-chain.** Have `staked_of` read `unlock_at` and require it comfortably in the future, or gate on a time-weighted snapshot of `amount` rather than an instantaneous read. Completes **F-18**; depends on group A item 2. *Without it the tier is a live spot balance and one position serves unlimited users by rotation, since the off-chain read cannot be composed into a transaction but a position surviving a single scan interval is enough.*
5. **Harden the reader and document its parameters.** Add a memcmp on the `StakeAccount` discriminator at offset 0 and tighten `len(raw) >= 80` to an exact `dataSize` of 89, derive decimals from the mint rather than `RCLAW_DECIMALS`, log loudly when the gate is enabled but `RCLAW_STAKING_PROGRAM` is unset, and add every `RCLAW_*` variable to `.env.example`. Closes the reader half of **F-22**, plus **F-29** and **F-39**. *Without it a misconfigured `RCLAW_DECIMALS` mis-scales every threshold by orders of magnitude in silence, and pointing `RCLAW_STAKING_PROGRAM` at a hostile program lets arbitrarily shaped accounts be read as stake records.*

### (D) Optional hardening, in rough value order

1. **Enforce the npm lockfile and add SCA.** Change all six documented `npm install` sites to `npm ci` and add a Node job running `npm ci --prefix token` plus `npm audit --audit-level=high`. Closes **F-20**. *Without it the committed pins are advisory, and the exact structural condition that made the `@solana/web3.js` 1.95.6/1.95.7 incident work — caret ranges plus a re-resolving install — remains fully present on packages that sign privileged transactions.*
2. **Reclaim stranded rent.** Add a `close_stake_account` handler with `close = owner`, the seeds and `has_one` pair retained, and a load-bearing `constraint = stake_account.amount == 0`; pre-create the vault ATA at deploy time so no user subsidizes it. Closes **F-26**. Omitting the zero-balance constraint would orphan escrowed tokens and turn a Low into a High. *Without it every staker permanently forfeits roughly 0.0016 SOL of rent that no instruction can return.*
3. **Make the bridge honest.** Either materialize the transfer generator and report the true count of unsigned transactions, or delete the call and print "not implemented"; require `--recipient` explicitly rather than defaulting to the source-chain sender; and fix the config comment at `ntt.config.json:27` that promises a redeem this tooling never performs. Closes **F-24**. *Without it a command that signs and sends nothing reports success, and the destination address silently defaults to a sender address from the wrong chain.*
4. **Give the offline harness real assertions.** Add a `derive_params.test.mjs` pinning the derived lamport values and window ordering against the committed config, and run `node --test` plus `npm run e2e:plan` in CI. Closes the `--dry` mode finding. *Without it `--dry` asserts only that a command exits 0, and the already-written allowlist serialization regression test never runs anywhere.*
5. **Fix the documentation drift.** Correct `bot/token/tier_gate.py:150-151` (which says offset 40 where the code correctly reads 72), and correct `programs/rclaw_staking/README.md:41`, which claims the Python test "locks both the offsets and the mint filter" when the fixture would shift with the reader and stay green. *Without it the next person to edit either side trusts a comment that already desynchronized once, in commit `b5e9868`.*