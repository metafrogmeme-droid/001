# HashKey Seal: Fable 5.1 audit (re-run, 31 October 2026)

Review only. Nothing here deploys, signs or changes code. The brief for this
run is the operator's `FABLE_5.1_AUDIT_BRIEF_HashKeySeal.md`; its eight
questions are answered at the end.

## Scope

- `contracts/hashkey/src/HashKeySeal.sol`
- `contracts/hashkey/script/DeployHashKeySeal.s.sol`
- `contracts/hashkey/script/deploy.sh`
- Read as context, not audited: `contracts/hashkey/networks.json`,
  `contracts/hashkey/foundry.toml`, `contracts/hashkey/.gas-snapshot`, the
  three Foundry test files, `bot/hashkey_seal/publish.py`,
  `bot/hashkey_seal/__main__.py`, `bot/web/mainnet_guard.py`,
  `app/routes/hashkey_seal.js`, `tests/test_hashkey_seal.py`,
  `.github/workflows/ci.yml`, `docs/hackathons/hashkey.md`,
  `docs/HASHKEY_SEAL_SECOND_OPINION_2026-10-25.md`.
- Commit: `main` at `d7aa1f33`. The three in-scope files quoted in the brief
  are byte-identical to the files at that commit (`diff` of each quoted block
  against the file was empty).

Line numbers below are `cat -n` of the files at `d7aa1f33`.

## Method

- Manual read of the contract, the script contract and the shell script,
  with the EVM semantics of Solidity 0.8.24 compiled for `cancun`.
- Comparison of the in-scope code with every finding of the second-opinion
  audit (`docs/HASHKEY_SEAL_SECOND_OPINION_2026-10-25.md`, M-1 to M-4, L-1
  to L-23, I-1 to I-24) to decide which are fixed, accepted or still open.
- Read of the Foundry tests and the committed gas snapshot to see what the
  suite asserts.
- **Not executed.** The sandbox's proxy refuses `mainnet.hsk.xyz`,
  `hsk.blockscout.com` and the Foundry release download, so:
  - `forge test` was not run. The brief's "40/40" is not reproduced here
    (see F-17).
  - No live read was made. The owner, pending owner, publisher flag,
    balance, bytecode, verification status and the demo anchor receipt are
    taken from the brief and are **assumptions** throughout this report.
  - The HTTP 403 that `mainnet.hsk.xyz` is said to return to Python's
    default User-Agent was not reproduced; it is taken from the brief.
- The earlier Fable 5.1 audit report is not in the repository. Only its
  counts (2 Medium, 16 Low, 13 Info) and the fix commit `90a3153` (PR #735)
  are. Q7 is therefore answered against the second-opinion audit, which is
  in the repository, and against the fix commit's own list.

## Summary

| Severity | Count |
|---|---|
| Critical | 0 |
| High | 0 |
| Medium | 2 |
| Low | 5 |
| Info | 11 |

Both Mediums are in `deploy.sh`, bear on future deploys and on the keys
around the contract, and do not change what the deployed bytecode does.
No finding in `HashKeySeal.sol` is above Low.

## What holds in the contract

- **Only an allowlisted publisher writes.** `anchor` (line 93) and
  `anchorMany` (line 100) both begin with
  `if (!publishers[msg.sender]) revert NotPublisher();`. `publishers` is
  written only by the constructor (line 52) and `setPublisher` (line 56),
  which calls `_onlyOwner()` first.
- **An anchor is written once and never changed.** `_anchor` (line 122)
  reverts `DuplicateHash` when `_anchors[hash].publisher != address(0)`
  (line 125), then writes `Anchor({publisher: msg.sender, kind: kind})`
  (line 126). There is no function that writes `_anchors` on any other
  path, no `delete`, and no setter for publisher or kind. The invariant
  `invariant_everyGhostAnchorStaysWithItsFirstWriter` holds this under a
  four-actor handler.
- **No value path.** No function is `payable`; there is no `receive` and
  no `fallback`, so a call with value, with any selector or none, reverts
  in the dispatcher before any check. No function reads `address(this).balance`
  or performs CALL, DELEGATECALL, STATICCALL, CREATE or SELFDESTRUCT;
  `testRuntimeHasNoCallCreateOrSelfdestruct` walks the runtime for those
  opcodes. Coin forced in (a SELFDESTRUCT beneficiary, a pre-funded
  address) is stuck for good, which the header comment (lines 4-8) says.
- **Two-step ownership is consistent.** `transferOwnership` (line 65)
  only sets `pendingOwner`; `acceptOwnership` (line 73) requires
  `msg.sender == pendingOwner` and a non-zero pending; `renounceOwnership`
  (line 83) zeroes both; `_onlyOwner` (line 133) refuses a zero owner
  explicitly, so after renounce nothing passes it even in an `eth_call`
  from `address(0)`.
- **Events suffice to rebuild state.** Every write emits: the constructor
  emits `OwnershipTransferred(0, deployer)` and `PublisherSet`;
  `setPublisher` emits `PublisherSet`; both ownership steps and renounce
  emit; `_anchor` emits `Anchored(sender, hash, kind, timestamp)` with
  all three identifying fields indexed. The current `pendingOwner` is the
  `newOwner` of the last `OwnershipTransferStarted` after the last
  `OwnershipTransferred`, or zero.
- **Compiler.** Pinned `0.8.24`, `cancun`, optimizer 200 runs in
  `foundry.toml`, matched by `deploy.sh --verify`. The second opinion's
  review of the five published 0.8.24 bugs (none reachable here) still
  applies: no inline assembly, no `delete` of a memory byte array, via-IR
  off, no storage arrays.

## Findings

### F-01 · Medium · `deploy.sh:210`, `DeployHashKeySeal.s.sol:43` · A raw private key is still an accepted mainnet deploy path

**Description.** When `HASHKEY_WALLET` is unset, the script requires
`ETH_PRIVATE_KEY` (`[ -n "${ETH_PRIVATE_KEY:-}" ] || die "set ETH_PRIVATE_KEY, or HASHKEY_WALLET=…"`)
and the script contract reads it with `vm.envUint("ETH_PRIVATE_KEY")`. No
branch refuses this when `NETWORK=mainnet`. The second opinion's M-2 asked
for the hardware or keystore path to be the only mainnet path, or an
explicit override variable.

**Impact.** A mainnet owner key exported in a shell lands in the shell
history and in the environment of every child process (forge, python3,
tee). The owner key controls the allowlist for the life of the contract.
The brief says the current owner is a team wallet reached by the two-step
transfer, so the deploying key no longer holds owner power; this finding
is about the next deploy and about how that transfer was signed, which
this review cannot see.

**Recommendation.** In the mainnet branch, refuse `ETH_PRIVATE_KEY` unless
`HASHKEY_ALLOW_ENV_KEY=1` is also set, and print why. Document ledger,
trezor or `cast wallet import` as the mainnet path. Add a shell test that
drives the refusal.

**Status.** Open (second opinion M-2).

### F-02 · Medium · `deploy.sh:180-194`, `networks.json` · Nothing stops a server-held key from becoming the mainnet publisher or owner

**Description.** `HASHKEY_SEAL_PUBLISHER` is validated for shape and EIP-55
checksum only. The script never compares it, or the owner-to-be it parses
from the simulation, with the testnet publisher address (the address of
the server's `HASHKEY_SEAL_SIGNER_KEY`), and `networks.json` does not
record that address. The leaked-key loop (lines 71-81) checks variable
*names*; a server key's *value* placed in `ETH_PRIVATE_KEY` passes it.

**Impact.** The repository's standing rule is that no key the server has
ever held gains mainnet power. The script does not enforce it. Whether the
live publisher `0x4476…e9dF` or the live owner has ever been in a server
environment is not knowable from the code and is **not asserted** here.

**Recommendation.** Record the testnet publisher address in
`networks.json` (it is public). On `--network mainnet`, refuse when the
publisher or the parsed owner-to-be equals it. Read the mainnet publisher
from its own variable (`HASHKEY_SEAL_MAINNET_PUBLISHER`) so the testnet
value cannot be reused by habit. Write down, outside git, which wallet
deployed and which accepted ownership.

**Status.** Open (second opinion M-1).

### F-03 · Low · `deploy.sh:148-173` · The mainnet chain-id probe cannot complete through Python's default User-Agent

**Description.** The pre-deploy probe posts `eth_chainId` with
`urllib.request`, sending only a `content-type` header. The brief reports
that `mainnet.hsk.xyz` answers HTTP 403 to that User-Agent (not reproduced
here; the sandbox cannot reach the host). On a 403 the probe prints
"chain id unreadable" and the script dies with "stopped before any deploy".

**Impact.** Fail-closed, so no wrong-chain deploy results. But it means the
documented mainnet procedure cannot reach `forge`, so the live deploy of
`0x9ce6…EcFE` was made some other way (**assumption**): a modified script,
a different endpoint, or a direct `forge script`. A direct run skips the
owner-to-be confirmation and the `.env` refusal, though the in-contract
`ChainMismatch` and `OwnerMismatch` checks still apply only if the
arguments were passed as the script passes them.

**Recommendation.** Replace the Python probe with
`cast chain-id --rpc-url "$RPC"` (cast is already required at line 101), or
set an explicit `User-Agent`. Record in `docs/hackathons/hashkey.md` how the
live deploy was actually performed and with which forge version.

**Status.** Open (new; noted in the brief).

### F-04 · Low · `networks.json:17-18`, `deploy.sh:134-136` · The mainnet explorer host is stale and the verify record is out of date

**Description.** `networks.json` lists `https://hashkey.blockscout.com` and
`…/api` for mainnet. The brief reports that host now answers 301 to
`hsk.blockscout.com`, where the contract is verified as an exact match.
`explorer_checked` is still `null` with a note that "a verify run that
succeeds is what records explorer_checked". `docs/hackathons/hashkey.md`
prints the same stale host.

**Impact.** `forge verify-contract --verifier-url "$EXPLORER_API"` posts to
a redirecting host. Whether forge's HTTP client replays a POST body across
a 301 is **not verified** here; if it does not, the scripted `--verify`
fails and the operator verifies by hand, which is one more step outside
the script. Three places (`networks.json`, the doc table, the explorer's
own redirect) now give two answers for one host, against the repository's
one-reading rule.

**Recommendation.** Set both mainnet explorer fields to
`hsk.blockscout.com`, record `explorer_checked` with the date and the
verified address, and regenerate the doc table from the file rather than
by hand. Add a probe of `<explorer_api>/v2/stats` before `--verify` that
dies on a 3xx.

**Status.** Open (second opinion I-12, residual).

### F-05 · Low · `HashKeySeal.sol:122-128` · The first allowlisted writer fixes a hash's publisher and kind for every later caller

**Description.** `_anchor` keys the duplicate check on `hash` alone. With a
second publisher on the list, that publisher can anchor any hash it learns
of, under any kind, before the intended publisher does, and the honest
anchor then reverts `DuplicateHash`. Revoking the squatter does not free
the hash (`testASecondPublisherSquatsAHashAndRevocationDoesNotFreeIt`).
A hash anchored by a publisher that is later revoked is stored exactly like
an honest one; distinguishing it needs the `PublisherSet` log history,
because no block number is stored on-chain (see F-15).

**Impact.** None while there is one publisher. The moment a second publisher
is added, attribution becomes first-come, and a compromised second key can
poison kinds. The header comment (lines 14-16) says so, and the doc says
"keep one publisher per deployment unless that is wanted".

**Recommendation.** Before adding a publisher, decide the policy in
writing. If per-publisher attribution is wanted, a v2 keys storage on
`keccak256(abi.encode(msg.sender, hash))` or on `(hash, kind)`. Off-chain
verifiers should check `anchorOf(hash).publisher` against the set of
publishers allowlisted at the anchoring block, rebuilt from logs.

**Status.** Accepted for one publisher; re-decide before a second
(second opinion L-5, residual).

### F-06 · Low · `HashKeySeal.sol:83-90` · Renounce is one step and irreversible, and a publisher cannot resign

**Description.** `renounceOwnership` zeroes `owner` and `pendingOwner` in
one owner-signed transaction. There is no delay, no second confirmation
and no `resign()` for a publisher. After renounce no publisher can be
added or removed, ever (`testFuzz_afterRenounceNoCallerChangesTheAllowlist`).

**Impact.** A single mistaken or coerced transaction from the owner freezes
the allowlist with whatever hot keys it holds. A leaked publisher key is
then permanent. There is no pause or write cap to fall back on; the brief
records that the owner accepted their absence on 31 October 2026.

**Recommendation.** Do not renounce. Move ownership to a multisig or a
timelocked owner by the two-step path instead; that gives the same
"no single key" property while keeping revocation. If renounce is ever
wanted, remove every hot-key publisher first, as the header comment says.

**Status.** Accepted by the owner (second opinion L-2 fixed, L-3 accepted).

### F-07 · Low · `deploy.sh:92-96`, `foundry.toml` · `FOUNDRY_*` environment variables still change the compiled bytecode unrefused

**Description.** The script refuses a `contracts/hashkey/.env` (line 94)
but does not check inherited `FOUNDRY_OPTIMIZER`, `FOUNDRY_EVM_VERSION`,
`FOUNDRY_OPTIMIZER_RUNS` or `FOUNDRY_SOLC_VERSION`, which override
`foundry.toml`. The second opinion showed the runtime size changing under
them. There is no post-broadcast comparison of `cast code` with
`forge inspect HashKeySeal deployedBytecode`.

**Impact.** For the live contract: none, given the brief's exact-match
verification (an **assumption** here). For the next deploy: bytecode that
differs from what CI tested, discovered only at verification.

**Recommendation.** Add `FOUNDRY_*` compile variables to the refusal loop,
and after a broadcast compare the deployed runtime (minus the CBOR tail)
with the build artifact before printing success.

**Status.** Open (second opinion L-16, residual).

### F-08 · Info · `.gitignore:36-38` · Forge output directories are ignored only under `contracts/hashkey`

**Description.** The root `.gitignore` lists `out/` and `.cache/` but not
`broadcast/` or `cache/`. `deploy.sh` changes directory into
`contracts/hashkey` before every forge call, so the script itself writes
into the ignored tree; a raw `forge script` from the repository root does
not. Forge's `cache/` can hold the RPC URL.

**Recommendation.** Add `/broadcast/` and `/cache/` to the root
`.gitignore`.

**Status.** Open (second opinion L-21).

### F-09 · Info · `HashKeySeal.sol:100-107` · `anchorMany` has no batch cap

**Description.** The loop runs over `hashes.length` with no upper bound.
Each element writes two fresh storage slots and one four-topic log. From
the EVM gas schedule that is roughly 44,200 gas of storage plus about 2,100
of log plus calldata per element, so on the order of 47,000 gas each
(a derivation from the schedule, **not a measurement**; the second opinion
measured 49,228 for a whole single-anchor transaction). One bad element
(zero, duplicate, or duplicated within the batch) reverts the batch.

**Impact.** Only the calling publisher pays and only its own transaction
fails; no third party can be denied. A batch beyond the block gas limit
fails at estimation. With a second publisher, one squatted hash in a
batch reverts the honest publisher's whole batch (F-05).

**Recommendation.** Chunk in the publisher (for example 100 per call) and
pre-read `isAnchored` for each element, as the single-hash publisher
already does. A `MAX_BATCH` constant in a v2 would make the bound a
contract fact.

**Status.** Accepted (by design; documented at lines 98-99).

### F-10 · Info · `HashKeySeal.sol:56-61` · `setPublisher` emits on a no-op

**Description.** Setting a publisher to the value it already has writes the
slot and emits `PublisherSet`. A log-based alert on allowlist changes sees
a change that is not one.

**Recommendation.** `if (publishers[publisher] == allowed) return;` in a
v2; indexers should compare with the prior state.

**Status.** Open (second opinion I-5).

### F-11 · Info · `test/HashKeySeal.t.sol:430-450` · The opcode walk does not cover MCOPY

**Description.** `testRuntimeHasNoCallCreateOrSelfdestruct` refuses CREATE,
the CALL family, SELFDESTRUCT and TLOAD/TSTORE (`0x5c`, `0x5d`), but not
MCOPY (`0x5e`). `foundry.toml` says the runtime "uses PUSH0 (Shanghai) and
no Cancun-only opcode"; that claim has no test.

**Impact.** If a future compiler emitted MCOPY, a chain without Cancun
would fail the transaction, and nothing in CI would say so. HashKey Chain
has Cancun at genesis per the second opinion, so this is hygiene.

**Recommendation.** Add `require(op != 0x5e, "MCOPY")` to the walk.

**Status.** Open (new).

### F-12 · Info · `deploy.sh:60` · An unknown argument is echoed verbatim

**Description.** `*) die "unknown argument: $1"` prints the value. A key
pasted as an argument by mistake is printed to stderr.

**Recommendation.** Print "unknown argument" without the value.

**Status.** Open (second opinion I-17).

### F-13 · Info · `deploy.sh:132` · `--verify` encodes today's publisher, not the deployment's

**Description.** The constructor argument for verification is taken from
the current `HASHKEY_SEAL_PUBLISHER`, not from the broadcast record or the
first `PublisherSet` log. A verify run with a different value fails with
a bytecode mismatch, which is confusing but harmless.

**Recommendation.** Read the argument from `broadcast/…/run-latest.json`
or from the first `PublisherSet` log of the address being verified.

**Status.** Open (second opinion I-19).

### F-14 · Info · `HashKeySeal.sol:125` · No domain separation of the hash

**Description.** The stored key is the raw 32-byte hash. The same hash can
be anchored on testnet 133 and mainnet 177, and in any other deployment of
this contract. The contract cannot tell them apart; only the verifier can,
by pinning chain id and address together.

**Recommendation.** Verifiers must pin `(chainId, address, hash)`. Upstream
seal hashes should be salted and domain-separated before anchoring.

**Status.** Open (second opinion I-8); a verifier rule, not a contract
change.

### F-15 · Info · `HashKeySeal.sol:17-20` · The `Anchor` struct uses two slots and stores no block number

**Description.** `address publisher` (20 bytes) and `bytes32 kind` occupy
two storage slots. A `uint96 blockNumber` would fit beside the address at
no extra slot, and would let `anchorOf` say *when* a hash was anchored,
which is what distinguishes an anchor by a since-revoked publisher without
replaying logs (F-05).

**Recommendation.** For a v2 only; the ABI is live.

**Status.** Open (new; related to second opinion L-4).

### F-16 · Info · `deploy.sh:251-252` · An exported `HASHKEY_SEAL_CONFIRM_OWNER` makes every mainnet broadcast non-interactive

**Description.** The confirmation step is skipped whenever the variable is
set. That is the intended non-TTY path, but a value left exported in a
shell turns the next `--network mainnet --broadcast` into a single command
with no prompt.

**Recommendation.** Document `HASHKEY_SEAL_CONFIRM_OWNER=… deploy.sh …` as
a one-command prefix, never `export`. Optionally also require the
confirmation value to carry the chain id, for example `177:0x…`.

**Status.** Open (new).

### F-17 · Info · `contracts/hashkey/test/`, `.gas-snapshot` · The brief's test count does not match the tree

**Description.** The brief says "40/40 Foundry tests pass". The tree at
`d7aa1f33` holds 48 test functions: 26 unit and 5 fuzz in
`HashKeySeal.t.sol`, 9 invariants, 8 deploy-guard tests. The committed
gas snapshot lists 43 entries (fuzz tests are excluded from it by CI).
Neither figure is 40. `forge test` could not be run in this sandbox, so no
pass count is asserted here.

**Recommendation.** Re-run `forge test` at `d7aa1f33` and quote the count
it prints, with the forge version, in the deploy record.

**Status.** Open (new; a reporting discrepancy, not a code defect).

### F-18 · Info · `deploy.sh:71-77`, `bot/web/mainnet_guard.py:25-32` · The server-key list is copied by hand

**Description.** The five server-key names are the same in both files
today. Only one name (`HASHKEY_SEAL_SIGNER_KEY`) is tied by a test
(`test_the_operator_broadcast_also_refuses_the_seal_key`, and that test
reads `scripts/anchor_roots.sh`, a third copy). A new server key added to
the guard is not automatically refused by the deploy script.

**Recommendation.** A test that reads both lists and asserts equality.

**Status.** Open (second opinion L-17, residual).

## Earlier findings, by status at `d7aa1f33`

Against `docs/HASHKEY_SEAL_SECOND_OPINION_2026-10-25.md`:

| Earlier ID | Status now | Where |
|---|---|---|
| M-1 | **Open** | F-02 |
| M-2 | **Open** | F-01 |
| M-3, M-4 | Fixed | route awaits the child off the event loop and answers three unknowns; publisher computes the hash first and reports `send_unconfirmed` / `submitted` |
| L-1 | Fixed | `_default_signer` hands `to` as bytes; real-signer test present |
| L-2 | Fixed | two-step ownership, constructor event; renounce risk remains as F-06 |
| L-3 | Accepted by the owner, 31 Oct 2026 | F-06 |
| L-4 | Mostly fixed | publisher and kind stored and exposed; no block number (F-15); reads at `latest` |
| L-5 | Residual by design | F-05 |
| L-6, L-7, L-8 | Fixed in the publisher and guard | out of this scope |
| L-9 to L-13 | Fixed in the publisher and route | out of this scope |
| L-14, L-15 | Fixed | expected chain as a script argument; simulate, print owner, confirm |
| L-16 | Residual | F-07 |
| L-17 | Residual | F-18 |
| L-18 | Fixed | checksum check |
| L-19 | Open | no `HASHKEY_HD_PATH` / mnemonic index option; a hardware wallet signs only from the default path |
| L-20 | Fixed | `evm_version` pinned |
| L-21 | Open | F-08 |
| L-22, L-23 | Fixed | test suites rewritten |
| I-3, I-4, I-9, I-14, I-18 | Fixed | |
| I-5 | Open | F-10 |
| I-8 | Open | F-14 |
| I-12 | Residual, now confirmed stale | F-04 |
| I-13 | Open | no genesis-hash check; low value while chain ids are unique |
| I-17 | Open | F-12 |
| I-19 | Open | F-13 |
| I-24 | Half fixed | forge is now checked early; `CDPATH` still unguarded (fails closed) |

The earlier Fable 5.1 report itself is not in the repository, so its 31
items cannot be ticked one by one. Every fix named in commit `90a3153`'s
message is present in the code read for this review.

## Answers to the brief's questions

1. **Can anyone other than an allowlisted publisher write an anchor?** No.
   Both write entry points check `publishers[msg.sender]` first, the
   allowlist is written only by the owner, and the owner is not a
   publisher unless added. The fuzz and invariant suites hold this.
2. **Can any anchor be overwritten, deleted or re-attributed?** No. The
   only write to `_anchors` is behind the `DuplicateHash` check, and no
   function deletes or edits an entry. Revoking a publisher changes
   nothing already stored.
3. **Can the contract receive, hold or send value?** It cannot receive by
   any call: every selector and the empty selector revert on value. It
   cannot send: no CALL-family opcode is in the runtime. It can be made to
   *hold* value forced in from outside (SELFDESTRUCT beneficiary, pre-funded
   address), and that value is stuck for good; the header says so.
4. **Is the two-step flow correct, and what are the risks of
   `renounceOwnership`?** The flow is correct: propose, accept by the
   proposed address only, a later proposal replaces the pending one,
   renounce drops it, and a zero owner can never pass `_onlyOwner`.
   Renounce is one step and irreversible, freezes the allowlist with
   whatever keys it holds, and makes any later key leak permanent (F-06).
   Transferring to a multisig is the alternative that keeps revocation.
5. **Gas or DoS in `anchorMany`?** No cap; whole-batch revert on one bad
   element; only the publisher pays. No third party can block a publisher.
   With a second publisher, a squatted hash reverts the honest batch
   (F-05, F-09).
6. **Are the events enough to rebuild state?** Yes. Owner, pending owner,
   allowlist and every anchor's publisher and kind are each emitted at the
   write. An indexer needs the address and deploy block, and must treat a
   no-op `PublisherSet` as no change (F-10).
7. **Which earlier findings are still open?** M-1 and M-2 of the second
   opinion (F-02, F-01), and the residuals listed in the table above. All
   earlier contract-level items are fixed or accepted.
8. **What should change before more publishers or an automated server
   publisher go live on mainnet?** See the closing note.

## Closing note on mainnet

Nothing in `HashKeySeal.sol` blocks continued use of the deployed contract
as it is being used: one publisher, no value, an owner reached by the
two-step transfer, no renounce. The two Medium findings are in the deploy
procedure. They do not alter the deployed bytecode; they govern the next
deploy and the keys around this one.

Before a second publisher is added:

- Decide and write down the first-writer policy (F-05). If per-publisher
  attribution is wanted, that is a v2 contract, not an allowlist change.
- Confirm, from a record outside git, that neither the owner nor any
  publisher key has ever been in a server environment (F-02).
- Have the publisher chunk batches and pre-read each hash (F-09).

Before an automated server publisher signs on mainnet:

- `bot/hashkey_seal/publish.py` hard-codes the testnet chain id into the
  signed transaction and refuses any other endpoint; `mainnet_guard.py`
  refuses startup with the key aimed elsewhere. Reaching mainnet is a code
  change behind a new, off-by-default flag with its own key name, a key
  that is never `HASHKEY_SEAL_SIGNER_KEY`, held in a signer service or
  HSM rather than the web container's environment.
- Keep the owner off the server entirely; the server key is a publisher
  only, so the owner can revoke it. Never renounce (F-06).
- An off-chain write cap and pause in the publisher (per-hour count,
  kill flag), since the owner accepted that the contract has neither.
- Alerting on `PublisherSet`, `OwnershipTransferStarted` and
  `OwnershipTransferred` from the live address.
- Fix the deploy tooling first: F-01, F-02, F-03, F-04, F-07, and record
  how the live deploy and the ownership transfer were actually signed.
- Re-run `forge test` on the exact tree and quote the count (F-17).

This note is a reading of the code and the brief. The live state named in
the brief was not read from the chain in this review.
