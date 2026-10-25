# HashKey Seal: second-opinion audit

**Verdict: Fix first.** There are no Critical or High findings, and the contract cannot take or send value through any entry point. Four Medium findings need fixing before any deploy decision: two in the mainnet deploy procedure and two in how the testnet publisher reports what it signed. Two other gaps also need closing. The publisher cannot sign for any real contract address today. And the contract does not meet the repo's own mainnet precondition (a cap and a pause).

Review only. Nothing here proposes a deploy. 25 October 2026.

## Scope and method

- **Commit.** `main` at merge commit `97fe1917` (PR #718). The in-scope files are byte-identical on the current `main` (`4a4c35a`). `git diff --stat 97fe1917 origin/main` over them is empty.
- **Files.** `contracts/hashkey/{src/HashKeySeal.sol, test/HashKeySeal.t.sol, script/DeployHashKeySeal.s.sol, script/deploy.sh, networks.json, foundry.toml, .gitignore}`, `bot/hashkey_seal/{publish,networks,flag,__main__}.py`, `bot/web/mainnet_guard.py`, `docs/hackathons/hashkey.md`. Read as context: `app/routes/hashkey_seal.js` (the only caller of the publisher), `app/lib/hashkey_seal_flag.js`, `api_bridge.py`, `bot/fvb/keccak.py`, `tests/test_hashkey_seal.py`, `.github/workflows/ci.yml`.
- **Tools.** Foundry 1.8.6 (the release CI pins, checksum checked) and solc 0.8.24 (sha256 matches the official solc-bin list), run offline.
- **What was executed.** Everything ran locally. Nothing was broadcast to any real network, and no real key was used.
  - Forge PoCs, invariants and a mutation round.
  - `anvil` nodes started with `--chain-id 133`, `177` and `31337`. `deploy.sh` was run end to end against them.
  - The real publisher, driven through Python harnesses with a fake JSON-RPC server.
  - The real Express route with its real `spawnSync` runner.
  - `eth-account` 0.8.0, 0.11.3, 0.13.7 and 0.14.0.
- **How the findings were checked.** Five independent reviewers each covered one dimension: contract, compiler and chain, deploy and keys, publisher, tests. A separate verifier then re-ran each finding and tried to refute it. I re-ran the highest-impact items myself: the signing failure (L-1), the `.env` load (L-16), the keccak padding defect (I-16), every example test, and the mutation table.
- **Chain facts.** These came from HashKey's own published sources:
  - the docs source repository `HSKChain/documentation` at `3736efc2`;
  - HashKey's `genesis.json` and `rollup.json`;
  - an Ethereum L1 read of HashKey's dispute-game contract, whose `l2ChainId()` returned `0xb1`;
  - `ethereum-lists/chains`.

Counts: Critical 0, High 0, Medium 4, Low 23, Info 24.

## What holds

- **No value path, checked at bytecode level.** The runtime code has 954 code bytes plus 53 CBOR bytes. It contains no `CALL`, `CALLCODE`, `DELEGATECALL`, `STATICCALL`, `CREATE`, `CREATE2`, `SELFDESTRUCT`, `BALANCE` or `SELFBALANCE`.
  - One `CALLVALUE` guard in the dispatcher makes every function non-payable.
  - There is no `receive` and no `fallback`.
  - There is no proxy, initializer or delegatecall, so the contract is not upgradeable.
  - There are no ERC-20 or ERC-721 approvals and no external calls, so reentrancy is impossible.
  - All 6 selectors revert when called with value.
- **Only allowlisted callers write.** The zero hash is refused. A duplicate is refused whatever kind is passed. No path overwrites or deletes an anchor: the model-based invariant held over 128,000 calls, and the ABI-targeted invariant below held over all three senders.
- **Zero-address checks hold.** The constructor and `setPublisher` both refuse the zero address. The owner comes from `msg.sender`, so it is never zero at deploy.
- **Chain ids are correct.** Mainnet is 177 (`0xb1`) and testnet is 133 (`0x85`). Both match:
  - the official docs source (`network-info/index.mdx` lines 10-13 and 20-23);
  - HashKey's `genesis.json` `chainId` and `rollup.json` `l2_chain_id`;
  - `ethereum-lists`.

  The L1 read independently confirms 177. The RPC URLs `https://mainnet.hsk.xyz` and `https://testnet.hsk.xyz` match the docs. No other registered chain uses 133 or 177. The discarded testnet is a different id, 230315.
- **The publisher reads the key last.** The order is: flag, hash, kind, zero hash, address, configured chain, `eth_chainId`, `isAnchored`, then key. A recording `Mapping` showed `HASHKEY_SEAL_SIGNER_KEY` is read only after `eth_chainId` and `eth_call`. An endpoint answering `0xb1` is refused before the key is read. `HASHKEY_SEAL_CHAIN_ID=177` is refused before any RPC.
- **The signature is bound to chain 133.** `chainId` comes from `networks.json`, not from the RPC (`publish.py` line 339). Under EIP-155, a chain-133 transaction replayed on an anvil 177 node was rejected with "invalid chain id for signer".
- **No secret leaks were observed.** Across all CLI, subprocess and route runs, no stdout, stderr or response body contained the key. Forge traces redact env values. `deploy.sh` keeps the key off argv.
- **Reads keep three values.** An unreadable read is `readable:false, anchored:null` with HTTP 503, never "not anchored".
- **The route's admin door holds.**
  - A non-operator gets 404 whether the flag is on or off.
  - An operator with the flag off gets a 403 preview and nothing is spawned.
  - The cookie is SameSite=Lax and the only body parser is JSON, so a form-encoded cross-site POST is refused with no spawn.
  - The child is spawned from an argv array with no shell, and inputs are checked against `^0x[0-9a-fA-F]{64}$`.
- **Selectors match.** `anchor(bytes32,bytes32)` is `0xa21f3c6a` and `isAnchored(bytes32)` is `0x4f0b5801`. The Python calldata, `cast` and `forge inspect` agree, and so do both kind words.
- **The compiler is sound for this contract.** The pragma is pinned to `0.8.24`. None of the five known 0.8.24 bugs applies (I-10). HashKey runs Shanghai and Cancun from genesis on both networks, and the bytecode needs only Shanghai (`PUSH0`).
- **`deploy.sh` refuses what it should.**
  - It refuses `RUNECLAW_SERVER=1` and the listed server key names before any RPC.
  - It stops when `eth_chainId` differs from `networks.json`.
  - The keystore path deploys with `owner()` equal to the keystore address, with no `--sender` needed.
  - Ledger and Trezor without a device fail closed.
  - `broadcast/` holds no key, and `contracts/hashkey/{cache,out,broadcast}` are gitignored.
- **The existing tests pass.**
  - Forge: 14/14.
  - `tests/test_hashkey_seal.py` plus `tests/test_the_server_does_not_sign_mainnet.py`: 36 passed.
  - `app/test/hashkey_seal.test.js`: 5/5.

## Findings

Ranked by severity. Line numbers are from `cat -n` of the files at `97fe1917`.

| ID | Severity | File and line | Issue | Fix |
|---|---|---|---|---|
| M-1 | Medium | `docs/hackathons/hashkey.md` 76-84; `contracts/hashkey/script/deploy.sh` 60-70, 81; `contracts/hashkey/script/DeployHashKeySeal.s.sol` 42 | **Nothing stops a server-held key from gaining mainnet power.** The mainnet block reuses the testnet `HASHKEY_SEAL_PUBLISHER=<publisher-address>` placeholder, and deploy.sh only checks that the value is non-empty. The testnet publisher is the address of the server's `HASHKEY_SEAL_SIGNER_KEY`. Reproduced on anvil 177: that address deployed as mainnet publisher, and `cast send anchor(...)` signed with the server key succeeded. The leaked-key loop checks env var *names*, not values: with `ETH_PRIVATE_KEY` holding the server key's value, the server key became the mainnet owner and could `setPublisher`. The server code still refuses chain 177, but anyone who obtains that key does not need the server code. That defeats "the server never holds mainnet keys" in effect. | Record the testnet publisher address (it is public) in `networks.json`. On `--network mainnet`, derive the deployer address (`cast wallet address`, or the simulation's `from` for a hardware or keystore wallet) and refuse when either it or `HASHKEY_SEAL_PUBLISHER` equals the testnet publisher. Read the mainnet publisher from a separate variable, `HASHKEY_SEAL_MAINNET_PUBLISHER`. In the doc, say the mainnet owner and publisher must be keys that have never been in any server environment. |
| M-2 | Medium | `docs/hackathons/hashkey.md` 78-82 (hardware path only at 57-68); `deploy.sh` 16 | **The only documented mainnet procedure exports a raw hot key.** `export ETH_PRIVATE_KEY=0x…` puts the mainnet key in `~/.bash_history` (reproduced with `bash -i`) and in the environment of every later child process. Ledger, Trezor and keystore appear only under the testnet heading. `ETH_PRIVATE_KEY` is also the name `mainnet_guard.py` (lines 22-24, 109-113) reserves for the operator's Base mainnet key. An already-exported Base key would be used silently, because deploy.sh never shows the deployer (L-15). | Make `HASHKEY_WALLET=ledger\|trezor\|account` the documented mainnet path. In deploy.sh, refuse `--network mainnet` with `ETH_PRIVATE_KEY` unless `HASHKEY_ALLOW_ENV_KEY=1` is set. If an env key stays documented, show `read -rs ETH_PRIVATE_KEY` and a single-command env followed by `unset`, never `export`. |
| M-3 | Medium | `app/routes/hashkey_seal.js` 26-32, 46-54, 56-67, 116-117 | **The route can report "not signed, not broadcast" for a transaction it sent.** `spawnSync` times out at 20,000 ms, but the publisher makes up to 7 sequential RPC calls with a 20 s socket timeout each (`publish.py` 144). When the child is killed or exits non-zero, `parseRun` returns null and `unreadable()` hard-codes `signed:false, broadcast:false`. Reproduced with the real router and real runner against a fake RPC that delayed its send reply: the RPC had received the signed raw transaction, yet the body said `signed:false, broadcast:false`. A retry on that answer double-spends the nonce or duplicates the seal. Testnet only, and latent until L-1 is fixed. | For POST, treat a run that ends without parseable JSON (killed, signalled or non-zero) as unknown: `signed:null, broadcast:null`, plus a sentence telling the operator to check the sender's nonce or the explorer before retrying. Give `publish_anchor` an overall deadline below the spawn timeout (for example 5 s per call, and refuse to sign with less than 10 s left). Print the locally computed transaction hash before posting. Use async `spawn`. |
| M-4 | Medium | `bot/hashkey_seal/publish.py` 355-365, 143-147 (catch list at 146); `tests/test_hashkey_seal.py` 249-257 | **The publisher reports a confident negative after it has posted the transaction.** Once `eth_sendRawTransaction` has gone out, any `RpcFailed` becomes `reason:"rejected", broadcast:false, anchored:false`. That includes a read timeout, a dropped connection, and a JSON-RPC `already known`, which means the node *has* the transaction. Some exceptions `http_transport` does not convert also escape after posting: `http.client.IncompleteRead`, `BadStatusLine`, `UnicodeDecodeError`, and a JSON integer over 4,300 digits. The process then exits 1 with no JSON, and the route says `signed:false` (M-3). A deeply nested reply crashes Python 3.11 with SIGSEGV, because `eth_account` (through py_ecc) raises the recursion limit to 100,000. The repo test asserts the false reading. | Before posting, compute the transaction hash locally (`eth_utils.keccak` of the raw bytes, not `bot.fvb.keccak`; see I-16). On any failure after posting, return `reason:"send_unconfirmed", signed:true, broadcast:null, anchored:null, tx_hash:<local>`. Inside `http_transport`, build the Request inside the `try`, read at most 1 MiB, and convert `http.client.HTTPException`, `ValueError` and `RecursionError` to `RpcFailed`. Change the test to assert `None`. |
| L-1 | Low (fails closed; blocks the feature) | `bot/hashkey_seal/publish.py` 79, 344 (raises at 253, called from 350); `tests/test_hashkey_seal.py` 36 | **The publisher cannot sign for any real contract address.** `_address()` lowercases the address (line 79) and the result becomes the transaction's `to` (line 344). `eth_account` rejects a non-checksum string, so `Account.sign_transaction` raises `TypeError` whenever the address contains a letter a-f, which is almost every address. I re-ran this on eth-account 0.13.7: the lowercase address raises `TypeError`, the checksum form signs, and `0x11…11` signs. The route then answers 503 `readable:false`, which is wrong, because the read succeeded. The test fixture `ADDRESS = "0x" + "11" * 20` has no letters, and every test injects a fake signer, so CI cannot see this. `eth-account` is not pinned in any requirements file. Fixing L-1 makes M-3, M-4, L-9, L-11 and L-12 live, so fix them together. | Use `"to": bytes.fromhex(address[2:])` or `eth_utils.to_checksum_address(address)`. Change the fixture to an address that contains letters. Add a `pytest.importorskip("eth_account")` test that signs through `_default_signer` and decodes `chainId == 133`, `value == 0`, `to` and `data`. Pin `eth-account` if testnet publishing is intended. |
| L-2 | Low | `contracts/hashkey/src/HashKeySeal.sol` 28, 33-38, 40-44 | **Ownership cannot move or be stopped safely.** There is no ownership transfer of either kind, so the owner is the deploy key forever. If that key is lost, the allowlist is frozen with whatever publishers it holds. `renounceOwnership` is one step and irreversible, and it leaves every publisher, including a self-allowlisted owner, able to write forever. A publisher cannot resign either, so nothing can stop a leaked publisher key once the owner is gone. | Take `initialOwner` in the constructor and emit `OwnershipTransferred(address(0), initialOwner)`. Add two-step `transferOwnership` / `acceptOwnership`. Allow `renounceOwnership` only when no publisher is active, or replace it with `freeze()` that also stops `anchor`. Add `resign()` for a publisher. |
| L-3 | Low | `HashKeySeal.sol` (whole contract) | **No pause and no write cap.** CLAUDE.md requires "a cap, a pause" before mainnet. The only brake is revoking publishers one by one, which is impossible after renounce. The publisher set is not enumerable on-chain. | Add `paused` plus a `guardian` who can only pause, kept independent of renounce, and check it first in `anchor`. Optionally add a per-block write cap. |
| L-4 | Low | `HashKeySeal.sol` 47-57; `publish.py` 172-214 (read at 208) | **The on-chain record is a bare bool.** Anchors written by a later-revoked or compromised publisher stay `true` and look identical to honest ones. `read_anchor` reports them `anchored:true`, reading at `latest`. | Store publisher, block number and a kind tag in one packed slot (measured +230 gas) and expose `sealOf(hash)`. Optionally add a revocation cutoff with `isValid(hash)`. The reader should return those fields and read at `finalized`. |
| L-5 | Low | `HashKeySeal.sol` 46-53; `publish.py` 307-308 | **The first writer wins across publishers and kinds.** The kind is not stored, so a second publisher can permanently occupy a hash with the wrong kind (reproduced). The honest publisher then reports a plain `duplicate`. | Store the kind tag (see L-4) and report `duplicate_other_kind`. Keep the allowlist to one publisher. |
| L-6 | Low | `bot/hashkey_seal/__main__.py` 15-34; `app/routes/hashkey_seal.js` 26-32; `bot/web/mainnet_guard.py` 168-180; `docs/hackathons/hashkey.md` 112; `deploy.sh` 4-5 | **The startup guard never runs in the process that holds the key.** The only caller of the publisher is the Node web server, which spawns it with the inherited env. Neither the Node server nor the publisher's `__main__` calls `startup_refusal` or sets `RUNECLAW_SERVER`; only `bot/main.py` 877 and `api_bridge.py` 1173 do. So `ETH_PRIVATE_KEY`, or `HASHKEY_SEAL_CHAIN_ID=177` next to the signer key in the web env, is not refused at start. Driven: that process started and stopped only at the per-call `not_testnet` check. Doc line 112 ("refuses process startup") and the deploy.sh comment at lines 4-5 overstate. Also, `.env.example` (HashKey section, lines 1702-1714 on current main) puts the key in the bot's `.env`. `scripts/prepare_web_env.py` copies only `SHARED_KEYS` (lines 20-26), so a key placed by the template lands in processes that never use it. | Call `mainnet_guard.startup_refusal()` first in `__main__.main` and exit before any command. Pass `RUNECLAW_SERVER=1` and a minimal env in the `spawnSync` options. Document `web.env` as the key's home, and correct line 112 and the comment. |
| L-7 | Low | `api_bridge.py` 1169-1173; `Dockerfile` 92 (context, outside the listed files) | `refuse_server_start()` sits under `if __name__ == "__main__":`. The Docker `CMD ["uvicorn", "api_bridge:app", …]` imports the module, so on that path the bridge starts with no guard and no marker. Runtime signing sites still call `execution_refused`. | Call it at import or in the FastAPI lifespan, and test that importing the module sets the marker. |
| L-8 | Low | `bot/web/mainnet_guard.py` 48-65, 87-89, 119-131 | **The guard's network table does not know HashKey.** 133 and 177 are not in `web3_exec_gate.NETWORKS`. So `WEB3_CHAIN_ID=177` or `CHAIN_ID=177` next to a signing key starts without refusal, and `execution_refused(133)` labels the testnet as mainnet. Only `HASHKEY_SEAL_CHAIN_ID` gets the HashKey-specific check. | Union `mainnet_chain_ids()` with `networks.json`'s mainnet id, failing closed when the file is unreadable. Do not add rows to `NETWORKS`: that would widen the generic web3 signer to 133. Give an unknown id its own reason text. |
| L-9 | Low | `publish.py` 319-348 (cap check at 335-337) | Gas, nonce, base fee and priority fee are taken from the RPC uncapped; the only bound is 256 bits. A hostile or intercepted endpoint can make the signer commit its whole testnet balance (reproduced: maxFee 3e40, gas 3e7, signed). | Refuse gas above 100,000 (measured 49,228 per anchor), `maxFeePerGas` above a configured cap, and a nonce of 2^64 or more. |
| L-10 | Low | `publish.py` 128-152 (Request built at 140, outside the `try` at 143) | `HASHKEY_SEAL_RPC_URL` accepts `http://`, `file://` and `data:`. A URL with no scheme escapes as `ValueError`. Over plain http, a peer can lie about `isAnchored` (reported as `readable:true`) and about fees. It cannot obtain a mainnet signature. | Accept only `https` with a hostname, and build the Request inside the `try`. |
| L-11 | Low | `publish.py` 361-375 | The `tx_hash` the RPC returns is reported as `ok:true, broadcast:true` without being compared with keccak(raw). | Compute the hash locally. On a mismatch, report `send_unconfirmed`. |
| L-12 | Low | `publish.py` 300-308, 319, 366-375 | There is no receipt wait, the nonce is `pending` with no local tracking, and `isAnchored` is read at `latest`. A second publish of the same hash before mining reports `ok:true` and then reverts on-chain (gasUsed 26,857, status 0). A retry answered `already known` is reported `rejected`. | Also check `isAnchored` at `pending`. Record (hash, nonce, local tx hash) and refuse a hash with an unconfirmed transaction. Map `already known` to unknown. Optionally wait for a bounded receipt and report its status. |
| L-13 | Low | `app/routes/hashkey_seal.js` 26-32 | `spawnSync` blocks the Node event loop for the whole publish. Both websites share that process. Measured: `/ping` took 18 s during a slow publish. | Use async `execFile` with a single-flight lock for POST. |
| L-14 | Low | `DeployHashKeySeal.s.sol` 25-43; `deploy.sh` 128-153 | **The script does not know which network the operator chose.** It accepts 133 or 177 whatever `--network` was. The shell's `eth_chainId` check uses a different connection from forge's. Reproduced with a proxy that answers the two clients differently: the shell printed "network testnet chain 133" while forge deployed to 177. A direct `forge script` skips every deploy.sh check, and the chain check runs after `vm.startBroadcast`. | Export `HASHKEY_EXPECT_CHAIN` from deploy.sh. In the script, require `block.chainid == expected` before `vm.startBroadcast` in both entry points, and for 177 require an explicit confirm variable. |
| L-15 | Low | `deploy.sh` 155-158 | A mainnet `--broadcast` has no confirmation, and never prints the deployer, balance, publisher or deployed address. The doc's instruction to "record the address yourself" means digging it out of `broadcast/…/177/run-latest.json`. | Simulate first, then print the deployer, its balance, the EIP-55 publisher and the chain id, and require the owner to type `177`. Print the contract address after the run. |
| L-16 | Low | `deploy.sh` 189-197 (comment 6-7; doc line 42) | **forge loads a hidden `.env` after the guards have run.** forge auto-loads `contracts/hashkey/.env`, which is gitignored and so invisible in `git status`, after deploy.sh has checked the shell env. Compiler settings, wallet variables and server key names in that file are never checked. I reproduced it: `FOUNDRY_OPTIMIZER=false` and `FOUNDRY_EVM_VERSION=paris` in that file flipped `forge config`. Inherited `FOUNDRY_*` shell variables also change the deployed bytecode (1,291 to 2,691 bytes), while `--verify` hard-codes the settings. | Refuse when `contracts/hashkey/.env` exists or any `FOUNDRY_*` compile variable is set. After a broadcast, compare `cast code` with `forge inspect HashKeySeal deployedBytecode`, ignoring the metadata tail. |
| L-17 | Low | `deploy.sh` 60-70; `mainnet_guard.py` 25-32 | The server-key list misses `XNAV_SIGNING_KEY` and `QCH_SIGNING_KEYS`, and it is copied by hand in three places (with `scripts/anchor_roots.sh`). No test ties the copies together. | Keep one tuple and generate the loops from it, or add a test asserting the sets are equal plus a census of key names in `.env.example`. |
| L-18 | Low | `deploy.sh` 81 | `HASHKEY_SEAL_PUBLISHER` is not validated. A bad EIP-55 checksum, a value with no `0x`, the deployer's own address and `0x…dEaD` all deployed. | Require `^0x[0-9a-fA-F]{40}$` and a valid checksum when the value is mixed case, and show the checksummed value in the mainnet confirmation. |
| L-19 | Low | `deploy.sh` 49, 164-174 | The hardware-wallet path can sign only from index 0 of the default path, and any pass-through flag is refused. An owner whose account is elsewhere is pushed toward the env key (M-2) or raw forge (L-14). | Add validated `HASHKEY_MNEMONIC_INDEX` and `HASHKEY_HD_PATH` options. |
| L-20 | Low | `deploy.sh` 115-125; `contracts/hashkey/foundry.toml` 1-8 | **Verification depends on unpinned settings.** `--verify` hard-codes the compiler and runs but not the EVM version, and `foundry.toml` does not pin `evm_version`. Foundry 1.8.6 resolves `cancun` for 0.8.24; another Foundry, or solc 0.8.29 or later, resolves `osaka`, which changes the metadata hash. The deploy-time standard-JSON is not kept anywhere. | Pin `evm_version = "cancun"`. Drop the hard-coded flags, or derive them from `forge config`. Keep the standard-JSON and `forge --version` with the mainnet deploy record, outside git. |
| L-21 | Low | `contracts/hashkey/.gitignore` 1-3; root `.gitignore` (only `out/`, line 36) | A forge run from the repo root writes `broadcast/` and `cache/` to unignored paths. `cache/` holds the RPC URL, including any API key in it. | Add `/broadcast/` and `/cache/` to the root `.gitignore`. |
| L-22 | Low | `contracts/hashkey/test/HashKeySeal.t.sol` 43-46, 111-117, 119-132, 168-176; `.github/workflows/ci.yml` 1327-1329; `tests/test_hashkey_seal.py` 484-488 | **The contract tests miss real defects.** The existing suite lets 8 of 12 mutants survive (table below). CI's `forge test` never compiles the deploy script. The no-funds guard is a word scan for `payable`, `selfdestruct` and `delegatecall`, which a `call{value:}` sweep passes. | Add the example tests below. Add `forge build` before `forge test`. Replace the word scan with a check that the selector set is exactly the six expected selectors. |
| L-23 | Low | `tests/test_hashkey_seal.py` 36, 130-134, 240, 249-257 | **The Python tests do not exercise the real paths.** Every publisher test injects a fake signer and uses a letter-free address, so L-1 is invisible. `http_transport` is never exercised. Calldata is compared only with itself. Failures after posting are asserted as `broadcast:false` (M-4). | Add a real-signer test (see L-1), `http_transport` tests against a local stub (error object, HTTP 500, non-JSON, timeout), and literal calldata pins (`a21f3c6a…`, `4f0b5801…`), and assert `None` after posting. |
| I-1 | Info | `HashKeySeal.sol` 4; `docs/hackathons/hashkey.md` 5 | "Stores a hash and a kind" and "holds no balance" are both inaccurate: only a bool is stored, and forced coin is held. | Say it "accepts no value and has no function that sends value; coin forced in stays there", and that the kind is in the event only. |
| I-2 | Info | `HashKeySeal.sol` (whole contract) | Native HSK forced in by a same-transaction or later `SELFDESTRUCT`, a pre-funded CREATE address or a coinbase reward lands and stays forever. Nothing reads the balance (reproduced three ways). | None. Do not add a sweep. |
| I-3 | Info | `HashKeySeal.sol` 34, 41 | After renounce, `msg.sender == address(0)` passes the owner check. That is unreachable on-chain: there is no key, and OP Stack deposit aliasing would need a 160-bit preimage. But an `eth_call` from `0x0` shows `setPublisher` succeeding on a frozen seal. | `if (owner == address(0) \|\| msg.sender != owner) revert NotOwner();` |
| I-4 | Info | `HashKeySeal.sol` 26-31; `networks.json` | Events rebuild the publisher set and the anchored set (tested below) but not the owner, because the constructor emits no ownership event. A verifier also needs the contract address and deploy block. Readers use `latest`. | Emit `OwnershipTransferred` in the constructor. Record address and deploy block per network. Read at `finalized`. |
| I-5 | Info | `HashKeySeal.sol` 36-37 | `PublisherSet` is emitted for no-op changes, which misleads add/remove alerting. | `if (publishers[p] == allowed) return;` |
| I-6 | Info | `HashKeySeal.sol` 11-16 | The `Anchored` layout is correct: sender, hash and kind are raw topics. `timestamp` repeats the block header and is the sequencer's claim. | None. |
| I-7 | Info | `HashKeySeal.sol` 47-53 | All operations are O(1) with no loops, so the block gas limit cannot affect them. An anchor transaction costs about 49.2k gas (execution median 26,857) and deploy costs 320,672. Storage grows by one slot per hash, forever. There is no batch function. | Optional: a length-capped batch, or one Merkle root per period. |
| I-8 | Info | `HashKeySeal.sol` 50-51 | **No domain separation.** The same hash can sit on 133 (written by the server hot key) and 177, at the same address if the same deployer and nonce are used (shown). A non-publisher cannot front-run. Low-entropy receipts can be confirmed by guessing. | Verifiers must pin chain id and address together. Use salted, domain-separated commitments. Deploy mainnet from a different deployer. |
| I-9 | Info | `HashKeySeal.sol` 27, 49 | A zero or arbitrary `kind` is accepted, `keccak256("")` is accepted as a hash, and the deployer can be the first publisher. Hash collisions are a keccak-256 property of the upstream hash and out of scope. | Optional `ZeroKind` and owner-is-publisher checks. |
| I-10 | Info | `HashKeySeal.sol` 2 | solc 0.8.24 has five known bugs. YulOptimizerKnowledgeBaseStaleBookkeeping applies in legacy codegen only to inline assembly and generated Yul utilities, and the 10 ABI helpers here reassign no base variable. MemoryByteArrayElementDeleteClearsWholeWord: no `delete`. The two via-IR spill bugs: via-IR is off. LostStorageArrayWriteOnSlotOverflow: no arrays. None applies. | Stay on 0.8.24 with this recorded, or move to 0.8.37 with `evm_version` pinned. Do not use 0.8.38 (released 22 October 2026) for mainnet until it has aged. |
| I-11 | Info | `foundry.toml` 6-8 | The bytecode needs only Shanghai: 24 `PUSH0` and no Cancun opcodes. HashKey's genesis and rollup files show Shanghai/Canyon and Cancun/Ecotone at genesis, Prague/Isthmus and Jovian since July 2026, and no Osaka. The Superchain registry's fork list is stale. | Read HashKey's `rollup.json` when checking a future fork. |
| I-12 | Info | `networks.json` 2, 9-10, 16-17 | **The cited source URL does not resolve, and explorer hostnames vary.** The source URL `docs.hskchain.net` returns ENOTFOUND and appears nowhere in the docs source. The explorer values match the docs. `ethereum-lists` lists `hsk.blockscout.com` and `testnet-explorer.hskchain.net`, the Superchain registry lists `explorer.hsk.xyz`, and the docs' contract page uses a third testnet host, `testnet-explorer.hashkeychain.net`. Explorer liveness could not be checked. | Cite the docs source repository and commit. Record the alias hostnames. Check `<explorer_api>/v2/stats` before `--verify`. |
| I-13 | Info | `deploy.sh` 128-153; `publish.py` 292-298 | A chain-id-only check cannot detect a testnet re-genesis under the same id. | Add `genesis_hash` per network (published in HashKey's `rollup.json`: testnet `0x656cb220…45ed8c`, mainnet `0xa96aea94…6de208`) and compare it with block 0. |
| I-14 | Info | `foundry.toml` 13-15 | `rpc_endpoints` is a second copy of the RPCs that nothing checks, and the `hashkey_mainnet` alias puts a direct mainnet deploy one word away. | Remove it, or test it against `networks.json`. |
| I-15 | Info | `ci.yml` 1319-1325 | The pin is correct, but Foundry 1.8.6 is 4 days old, and the bytecode metadata depends on the forge version. | Record `forge --version` with any mainnet deploy, and verify with the same version. |
| I-16 | Info | `bot/fvb/keccak.py` 58-61 | **The repo's keccak mis-pads.** For inputs whose length is 135 mod 136 the digest is wrong. I re-ran it: at 135 bytes it differs from `cast keccak`, at 134 and 136 they agree. HashKey inputs are 10-23 bytes, so the selectors and kinds are correct. The defect becomes live if this keccak is used for a transaction hash (M-4, L-11). | Append `0x01`, zero-fill to a multiple of the rate, then `msg[-1] \|= 0x80`. Add test vectors at 135 and 136. |
| I-17 | Info | `deploy.sh` 49 | An unknown argument is echoed verbatim, so a key pasted as an argument is printed. | Do not echo the value. |
| I-18 | Info | `deploy.sh` 178-186 | A key without `0x` passes the shell check and then fails in forge without echoing the key. A 64-digit all-numeric key is read as decimal. | Require the `0x` prefix. |
| I-19 | Info | `deploy.sh` 113 | `--verify` encodes today's `HASHKEY_SEAL_PUBLISHER`, not the deployment's. | Read the argument from `broadcast/…/run-latest.json` or the first `PublisherSet` log. |
| I-20 | Info | `app/routes/hashkey_seal.js` 27-31 | The key must sit in the Node env, and unrelated Python children (panic, t2c, srb) inherit it. | Pass a minimal env, or a 0600 key file read after the chain check. |
| I-21 | Info | `publish.py` 117-125; `__main__.py` 31-33 | `_key` accepts 0 and values at or above the curve order. Exceptions before signing lose their reason, because the route discards stderr. This fails closed. | Check `1 <= k < n`, and log the exception class in the route. |
| I-22 | Info | `bot/hashkey_seal/flag.py` 20 | The Node and Python flag readers disagree on exotic whitespace (`\x1c`, `\x85`, U+FEFF). Both directions fail closed. | Strip ASCII whitespace only, in both readers. |
| I-23 | Info | `publish.py` 320-327 | An `eth_estimateGas` revert (NotPublisher, a DuplicateHash race) is reported as `rpc_unreadable`. | Add an `estimate_reverted` reason. |
| I-24 | Info | `deploy.sh` 76-77, 160 | An exported `CDPATH` breaks `HERE` (it fails closed), and a missing forge is detected only after the RPC call. | `HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)`, and check for forge early. |

## Test quality

The existing 14 tests are live. Each assertion fails for the reason its name gives, and `prank` before `expectEmit` is valid in Foundry 1.8.6. But they cover too little.

**Mutation round.** I applied each mutant to a copy, ran the existing suite and then the example tests below, and restored the file. The source hash was checked afterwards.

| mutant | change | existing suite | example tests |
|---|---|---|---|
| m1 | drop the zero check in `setPublisher` | survives | killed (`testOwnerCannotAllowlistTheZeroAddress`) |
| m2 | drop the owner check in `setPublisher` | killed | killed |
| m2b | owner check only when adding | survives | killed (`testStrangerCannotRemoveTheLivePublisher`) |
| m3 | drop `DuplicateHash` | killed | killed (events rebuild) |
| m4 | add `receive() external payable {}` | killed | killed |
| m7 | drop `ZeroHash` | killed | killed |
| m9 | anyone can renounce | survives | killed (`testFuzz_nonOwnerCannotRenounce`) |
| m10 | removal emits `allowed=true` | survives | killed (`testRemovalEmitsPublisherSetFalseFromTheSeal`) |
| m11 | constructor emits no `PublisherSet` | survives | killed (`testConstructorEmitsExactlyOnePublisherSet`, deploy tests) |
| m15 | owner can sweep the balance with `call{value:}` | survives | killed (`invariant_noEntryPointMovesTheBalance`) |
| v1 | payable constructor | survives | killed (`testConstructorRefusesValue`) |
| v6 | a publisher can clear a seal | survives | killed (`invariant_aSealIsNeverCleared`) |

**Missing tests**

- **Unit.**
  - `setPublisher(address(0), …)` by the owner.
  - `renounceOwnership` by a non-owner.
  - A non-owner removing the live publisher.
  - The `PublisherSet(p, false)` removal event, checked with its emitter.
  - The constructor's single `PublisherSet`.
  - Idempotent set and unset.
  - A deploy that carries value.
  - A forced send that lands and leaves the balance stuck.
  - A non-publisher sending the zero hash, to pin which error it gets.
  - A call from `address(0)` after renounce (I-3).
- **Fuzz.**
  - The `allowed` flag, and `who == publisher`, in the non-owner fuzz: the current assumptions exclude both.
  - An events-only rebuild of state.
- **Invariant.**
  - ABI-targeted: balance never moves, owner is the deployer or zero, a seal is never cleared. This catches functions added later.
  - A spec-model handler, where every call matches the spec, including revert selectors.
  - A variant with `renounceOwnership` excluded, so owner-gated paths get real coverage.
- **Deploy script.**
  - Compile it in CI.
  - `UnexpectedChain` for any other id.
  - Owner is the broadcaster and the publisher is the env value, on 133 and 177.
  - A `deploy.sh` success arm against anvil.
  - Key-list equality with `mainnet_guard`.
- **Python and route.**
  - The real `eth_account` signer with a letter-containing address (L-1).
  - Failures after posting (M-4).
  - The route's spawn timeout (M-3).
  - `http_transport` against a local stub.
  - Fee caps (L-9).
  - Literal calldata pins on both sides.

## Example Foundry tests

These are five files for `contracts/hashkey/test/`, 24 tests in all. They use the repo's minimal `Vm` interface (no forge-std, which is not vendored), so they run under the CI job as it is. Together with the existing suite: **38 passed, 0 failed** (Foundry 1.8.6, solc 0.8.24, 256 fuzz runs). Each one kills the mutants listed above. The deploy test uses the small scalar `0xA11CE5EED` as its key, not a real or well-known key.

### 1. Access control: the zero address, non-owner renounce and removal, and events

`contracts/hashkey/test/HashKeySealAccessGaps.t.sol`. Kills m1, m2b, m9, m10 and m11, which the existing suite misses.

```solidity
// SPDX-License-Identifier: BUSL-1.1
pragma solidity 0.8.24;

import {HashKeySeal} from "../src/HashKeySeal.sol";

interface Vm {
    struct Log {
        bytes32[] topics;
        bytes data;
        address emitter;
    }

    function prank(address) external;
    function assume(bool) external pure;
    function expectRevert(bytes4) external;
    function expectEmit(bool, bool, bool, bool, address) external;
    function recordLogs() external;
    function getRecordedLogs() external returns (Log[] memory);
}

/// Access-control gaps in HashKeySeal.t.sol: the zero address through
/// setPublisher, renounce by a non-owner, removal by a non-owner, the removal
/// event, the constructor event, and idempotent set/unset. Each test asserts
/// the refusal arm and the arm that must still work.
contract HashKeySealAccessGapsTest {
    Vm internal constant vm = Vm(address(uint160(uint256(keccak256("hevm cheat code")))));

    address internal owner = address(0xA11CE);
    address internal publisher = address(0xB0B);
    address internal stranger = address(0xCAFE);

    HashKeySeal internal seal;

    event PublisherSet(address indexed publisher, bool allowed);

    function setUp() public {
        vm.prank(owner);
        seal = new HashKeySeal(publisher);
    }

    function testOwnerCannotAllowlistTheZeroAddress() public {
        vm.expectRevert(HashKeySeal.ZeroPublisher.selector);
        vm.prank(owner);
        seal.setPublisher(address(0), true);
        vm.expectRevert(HashKeySeal.ZeroPublisher.selector);
        vm.prank(owner);
        seal.setPublisher(address(0), false);
        require(!seal.publishers(address(0)), "zero stays out");
        // the other arm: the same owner call with a real address lands
        vm.prank(owner);
        seal.setPublisher(stranger, true);
        require(seal.publishers(stranger), "owner still adds");
    }

    function testFuzz_nonOwnerCannotRenounce(address caller) public {
        vm.assume(caller != owner);
        vm.expectRevert(HashKeySeal.NotOwner.selector);
        vm.prank(caller);
        seal.renounceOwnership();
        require(seal.owner() == owner, "owner kept");
        // the allowlist is not frozen: the owner still changes it
        vm.prank(owner);
        seal.setPublisher(stranger, true);
        require(seal.publishers(stranger), "allowlist still live");
    }

    /// No assume on `who` or `allowed`: the non-owner removing the live
    /// publisher, and the non-owner passing address(0), are both in range.
    function testFuzz_nonOwnerCannotAddOrRemove(address caller, address who, bool allowed) public {
        vm.assume(caller != owner);
        bool before = seal.publishers(who);
        vm.expectRevert(HashKeySeal.NotOwner.selector);
        vm.prank(caller);
        seal.setPublisher(who, allowed);
        require(seal.publishers(who) == before, "entry unchanged");
        require(seal.publishers(publisher), "live publisher kept");
    }

    function testStrangerCannotRemoveTheLivePublisher() public {
        vm.expectRevert(HashKeySeal.NotOwner.selector);
        vm.prank(stranger);
        seal.setPublisher(publisher, false);
        vm.prank(publisher);
        seal.anchor(keccak256("still-live"), keccak256("claw-guard"));
        require(seal.isAnchored(keccak256("still-live")), "publisher still writes");
    }

    function testRemovalEmitsPublisherSetFalseFromTheSeal() public {
        vm.expectEmit(true, false, false, true, address(seal));
        emit PublisherSet(publisher, false);
        vm.prank(owner);
        seal.setPublisher(publisher, false);
        require(!seal.publishers(publisher), "removed");
    }

    function testSetAndUnsetAreIdempotentAndEachEmits() public {
        for (uint256 i; i < 2; i++) {
            vm.expectEmit(true, false, false, true, address(seal));
            emit PublisherSet(stranger, true);
            vm.prank(owner);
            seal.setPublisher(stranger, true);
            require(seal.publishers(stranger), "set");
        }
        for (uint256 i; i < 2; i++) {
            vm.expectEmit(true, false, false, true, address(seal));
            emit PublisherSet(stranger, false);
            vm.prank(owner);
            seal.setPublisher(stranger, false);
            require(!seal.publishers(stranger), "unset");
        }
        // unsetting an address that was never set also lands and emits
        address never = address(0xD00D);
        vm.expectEmit(true, false, false, true, address(seal));
        emit PublisherSet(never, false);
        vm.prank(owner);
        seal.setPublisher(never, false);
        require(!seal.publishers(never), "never set");
    }

    function testConstructorEmitsExactlyOnePublisherSet() public {
        address first = address(0xF1257);
        vm.recordLogs();
        vm.prank(owner);
        HashKeySeal fresh = new HashKeySeal(first);
        Vm.Log[] memory logs = vm.getRecordedLogs();
        require(logs.length == 1, "one log");
        require(logs[0].emitter == address(fresh), "emitter");
        require(logs[0].topics.length == 2, "one indexed topic");
        require(logs[0].topics[0] == HashKeySeal.PublisherSet.selector, "topic0");
        require(logs[0].topics[1] == bytes32(uint256(uint160(first))), "topic1");
        require(keccak256(logs[0].data) == keccak256(abi.encode(true)), "allowed=true");
        require(fresh.owner() == owner, "owner is the deployer");
        require(fresh.publishers(first), "first publisher");
    }
}
```

### 2. ABI-targeted invariant: no balance moves, the owner only goes to zero, a seal is never cleared

`contracts/hashkey/test/HashKeySealAbiInvariant.t.sol`. Targets the seal's own ABI, so a function added later is fuzzed without a handler. Kills m15 (value sweep) and v6 (seal clearing).

```solidity
// SPDX-License-Identifier: BUSL-1.1
pragma solidity 0.8.24;

import {HashKeySeal} from "../src/HashKeySeal.sol";

interface Vm {
    function prank(address) external;
    function deal(address, uint256) external;
}

/// Targets the seal's own ABI, so a function added later is fuzzed without
/// anyone writing a handler for it. setUp leaves a balance (as a forced send
/// would), and no call by the owner, the publisher or a stranger may move it.
contract HashKeySealAbiInvariantTest {
    Vm internal constant vm = Vm(address(uint160(uint256(keccak256("hevm cheat code")))));

    address internal constant OWNER = address(0xA11CE);
    address internal constant PUBLISHER = address(0xB0B);
    bytes32 internal constant SEEDED = keccak256("seeded-receipt");

    HashKeySeal internal seal;

    function setUp() public {
        vm.prank(OWNER);
        seal = new HashKeySeal(PUBLISHER);
        vm.deal(address(seal), 1 ether);
        vm.prank(PUBLISHER);
        seal.anchor(SEEDED, keccak256("claw-guard"));
    }

    function targetContracts() public view returns (address[] memory t) {
        t = new address[](1);
        t[0] = address(seal);
    }

    function targetSenders() public pure returns (address[] memory s) {
        s = new address[](3);
        s[0] = OWNER;
        s[1] = PUBLISHER;
        s[2] = address(0xCAFE);
    }

    function invariant_noEntryPointMovesTheBalance() public view {
        require(address(seal).balance == 1 ether, "balance moved");
    }

    function invariant_ownerIsTheDeployerOrZero() public view {
        address o = seal.owner();
        require(o == OWNER || o == address(0), "owner moved elsewhere");
    }

    /// A seal written in setUp stays written whatever the ABI is asked,
    /// including by a function added later. The hash is in the Anchored
    /// log, so the fuzzer's dictionary can find it.
    function invariant_aSealIsNeverCleared() public view {
        require(seal.isAnchored(SEEDED), "a seal was cleared");
    }
}
```

### 3. No funds: value refused everywhere, a value-carrying deploy fails, a forced send is stuck, calldata pinned to the Python bytes

`contracts/hashkey/test/HashKeySealNoFunds.t.sol`. Kills m4 and v1. Also pins the calldata the Python publisher builds, byte for byte.

```solidity
// SPDX-License-Identifier: BUSL-1.1
pragma solidity 0.8.24;

import {HashKeySeal} from "../src/HashKeySeal.sol";

interface Vm {
    function prank(address) external;
    function deal(address, uint256) external;
}

/// Sends its whole balance to `to` on creation. No call into `to` runs, so
/// a contract without receive or fallback cannot refuse it.
contract ForceSender {
    constructor(address payable to) payable {
        selfdestruct(to);
    }
}

/// The no-funds claim, both arms: every entry point refuses value, there is
/// no fallback, and a forced send does land (the balance is not always zero)
/// but leaves the seal working and moves nothing out. The calldata the Python
/// publisher builds is pinned here byte for byte.
contract HashKeySealNoFundsTest {
    Vm internal constant vm = Vm(address(uint160(uint256(keccak256("hevm cheat code")))));

    address internal constant OWNER = address(0xA11CE);
    address internal constant PUBLISHER = address(0xB0B);

    HashKeySeal internal seal;

    function setUp() public {
        vm.prank(OWNER);
        seal = new HashKeySeal(PUBLISHER);
    }

    function _entryPoints() internal pure returns (bytes[] memory c) {
        c = new bytes[](6);
        c[0] = abi.encodeCall(HashKeySeal.anchor, (keccak256("v"), keccak256("claw-guard")));
        c[1] = abi.encodeCall(HashKeySeal.setPublisher, (address(0xCAFE), true));
        c[2] = abi.encodeCall(HashKeySeal.renounceOwnership, ());
        c[3] = abi.encodeCall(HashKeySeal.isAnchored, (keccak256("v")));
        c[4] = abi.encodeWithSignature("owner()");
        c[5] = abi.encodeWithSignature("publishers(address)", PUBLISHER);
    }

    function testEveryEntryPointRefusesValueAndThereIsNoFallback() public {
        bytes[] memory c = _entryPoints();
        address[2] memory callers = [OWNER, PUBLISHER];
        for (uint256 k; k < 2; k++) {
            vm.deal(callers[k], 10);
            for (uint256 i; i < c.length; i++) {
                vm.prank(callers[k]);
                (bool ok,) = address(seal).call{value: 1}(c[i]);
                require(!ok, "an entry point took value");
            }
        }
        // no fallback and no receive: empty and unknown calldata revert even at zero value
        (bool bare,) = address(seal).call("");
        require(!bare, "empty calldata landed");
        (bool unknown,) = address(seal).call(abi.encodeWithSelector(bytes4(0xdeadbeef)));
        require(!unknown, "unknown selector landed");
        require(address(seal).balance == 0, "balance");
        require(!seal.isAnchored(keccak256("v")), "a paid anchor stored");
        require(seal.owner() == OWNER, "a paid renounce landed");
        // the other arm: the same anchor at zero value lands
        vm.prank(PUBLISHER);
        (bool free,) = address(seal).call(c[0]);
        require(free && seal.isAnchored(keccak256("v")), "the unpaid anchor did not land");
    }

    function testForcedSendLandsIsStuckAndChangesNothing() public {
        vm.deal(address(this), 1 ether);
        new ForceSender{value: 1 ether}(payable(address(seal)));
        require(address(seal).balance == 1 ether, "a forced send does land");
        vm.prank(PUBLISHER);
        seal.anchor(keccak256("after-force"), keccak256("agent-leash"));
        vm.prank(OWNER);
        seal.setPublisher(address(0xCAFE), true);
        vm.prank(OWNER);
        seal.renounceOwnership();
        require(seal.isAnchored(keccak256("after-force")), "seal still works");
        require(address(seal).balance == 1 ether, "nothing moved the balance");
    }

    /// A deploy that carries value must fail, and the same deploy without
    /// value must land. A payable constructor would otherwise pass every
    /// other test in this directory.
    function testConstructorRefusesValue() public {
        vm.deal(address(this), 1);
        bytes memory init = abi.encodePacked(type(HashKeySeal).creationCode, abi.encode(PUBLISHER));
        address paid;
        address free;
        assembly {
            paid := create(1, add(init, 0x20), mload(init))
            free := create(0, add(init, 0x20), mload(init))
        }
        require(paid == address(0), "a deploy carrying value landed");
        require(free != address(0), "the unpaid deploy did not land");
        require(HashKeySeal(payable(free)).publishers(PUBLISHER), "first publisher");
        require(address(this).balance == 1, "value stayed with the deployer");
    }

    /// The same bytes `bot/hashkey_seal/publish.py::anchor_calldata` returns
    /// for seal 0x22..22 and kind keccak256("claw-guard"), as printed by
    /// python3 on 2026-10-25. A Python test should pin the same literal.
    function testCalldataMatchesThePythonPublisherBytes() public pure {
        bytes32 h = 0x2222222222222222222222222222222222222222222222222222222222222222;
        bytes32 k = keccak256("claw-guard");
        require(k == 0xfb1cc3e9bf0f09ca245b7fbcde281562c19302fd2397f211f16fbe0f1913c88e, "kind");
        require(HashKeySeal.anchor.selector == bytes4(0xa21f3c6a), "anchor selector");
        require(HashKeySeal.isAnchored.selector == bytes4(0x4f0b5801), "isAnchored selector");
        require(
            keccak256(abi.encodeCall(HashKeySeal.anchor, (h, k)))
                == keccak256(
                    hex"a21f3c6a2222222222222222222222222222222222222222222222222222222222222222fb1cc3e9bf0f09ca245b7fbcde281562c19302fd2397f211f16fbe0f1913c88e"
                ),
            "anchor calldata"
        );
        require(
            keccak256(abi.encodeCall(HashKeySeal.isAnchored, (h)))
                == keccak256(hex"4f0b58012222222222222222222222222222222222222222222222222222222222222222"),
            "isAnchored calldata"
        );
    }
}
```

### 4. An off-chain verifier rebuilds the state from events alone

`contracts/hashkey/test/HashKeySealEventsRebuild.t.sol`. Replays 40 fuzzed calls, some of them refused, and rebuilds the allowlist, the anchored set and the renounce from logs only. Kills m3, m7, m9, m10 and m11.

```solidity
// SPDX-License-Identifier: BUSL-1.1
pragma solidity 0.8.24;

import {HashKeySeal} from "../src/HashKeySeal.sol";

interface Vm {
    struct Log {
        bytes32[] topics;
        bytes data;
        address emitter;
    }

    function prank(address) external;
    function recordLogs() external;
    function getRecordedLogs() external returns (Log[] memory);
}

/// An off-chain verifier sees only the logs. This replays a fuzzed sequence
/// of calls (some refused), then rebuilds the allowlist, the anchored set and
/// the renounce from the seal's logs alone, and compares every view.
/// It also checks, from logs alone, that every Anchored sender was on the
/// rebuilt allowlist at that point and that no hash is anchored twice.
contract HashKeySealEventsRebuildTest {
    Vm internal constant vm = Vm(address(uint160(uint256(keccak256("hevm cheat code")))));

    address internal constant OWNER = address(0xA11CE);
    address internal constant PUBLISHER = address(0xB0B);

    bytes32 internal constant T_ANCHORED = keccak256("Anchored(address,bytes32,bytes32,uint256)");
    bytes32 internal constant T_PUBLISHER_SET = keccak256("PublisherSet(address,bool)");
    bytes32 internal constant T_RENOUNCED = keccak256("OwnershipRenounced(address)");

    function _actors() internal pure returns (address[4] memory a) {
        a = [OWNER, PUBLISHER, address(0xCAFE), address(0xD00D)];
    }

    function _pool() internal pure returns (bytes32[5] memory p) {
        p = [bytes32(0), keccak256("e1"), keccak256("e2"), keccak256("e3"), keccak256("e4")];
    }

    function _index(address[4] memory a, address who) internal pure returns (uint256) {
        for (uint256 i; i < 4; i++) {
            if (a[i] == who) return i;
        }
        revert("unknown address in a log");
    }

    function testFuzz_eventsAloneRebuildTheState(uint256 seed) public {
        address[4] memory a = _actors();
        bytes32[5] memory p = _pool();

        vm.recordLogs();
        vm.prank(OWNER);
        HashKeySeal seal = new HashKeySeal(PUBLISHER);
        for (uint256 i; i < 40; i++) {
            seed = uint256(keccak256(abi.encode(seed, i)));
            uint256 op = seed % 16;
            address who = a[(seed >> 8) % 4];
            if (op < 9) {
                vm.prank(who);
                try seal.anchor(p[(seed >> 16) % 5], bytes32(seed >> 24)) {} catch {}
            } else if (op < 15) {
                address target = a[(seed >> 32) % 4];
                bool allowed = (seed >> 40) & 1 == 1;
                vm.prank(op < 13 ? OWNER : who);
                try seal.setPublisher(target, allowed) {} catch {}
            } else {
                vm.prank(who);
                try seal.renounceOwnership() {} catch {}
            }
        }
        Vm.Log[] memory logs = vm.getRecordedLogs();

        bool[4] memory pub;
        bool[5] memory seen;
        bool renounced;
        for (uint256 i; i < logs.length; i++) {
            Vm.Log memory l = logs[i];
            require(l.emitter == address(seal), "only the seal logs here");
            bytes32 t0 = l.topics[0];
            if (t0 == T_PUBLISHER_SET) {
                pub[_index(a, address(uint160(uint256(l.topics[1]))))] = abi.decode(l.data, (bool));
            } else if (t0 == T_ANCHORED) {
                address sender = address(uint160(uint256(l.topics[1])));
                require(pub[_index(a, sender)], "Anchored by a sender the logs never allowlisted");
                bytes32 h = l.topics[2];
                require(h != bytes32(0), "zero hash in a log");
                bool matched;
                for (uint256 j; j < 5; j++) {
                    if (p[j] == h) {
                        require(!seen[j], "a hash anchored twice in the logs");
                        seen[j] = true;
                        matched = true;
                    }
                }
                require(matched, "unknown hash in a log");
            } else if (t0 == T_RENOUNCED) {
                require(!renounced, "renounced twice");
                require(address(uint160(uint256(l.topics[1]))) == OWNER, "previous owner");
                renounced = true;
            } else {
                revert("unknown log");
            }
        }

        for (uint256 i; i < 4; i++) {
            require(seal.publishers(a[i]) == pub[i], "allowlist differs from its logs");
        }
        for (uint256 j; j < 5; j++) {
            require(seal.isAnchored(p[j]) == seen[j], "anchored set differs from its logs");
        }
        require((seal.owner() == address(0)) == renounced, "renounce differs from its logs");
    }
}
```

### 5. The deploy script: owner, publisher, and the chain gate

`contracts/hashkey/test/DeployHashKeySealScript.t.sol`. Importing the script makes `forge test` compile it, which CI does not do today. Kills m11. Each wrong-chain case runs in its own test, because a reverted `run()` leaves the broadcast open (see L-14).

```solidity
// SPDX-License-Identifier: BUSL-1.1
pragma solidity 0.8.24;

import {HashKeySeal} from "../src/HashKeySeal.sol";
import {DeployHashKeySeal} from "../script/DeployHashKeySeal.s.sol";

interface Vm {
    struct Log {
        bytes32[] topics;
        bytes data;
        address emitter;
    }

    function setEnv(string calldata, string calldata) external;
    function chainId(uint256) external;
    function addr(uint256) external pure returns (address);
    function assume(bool) external pure;
    function expectRevert(bytes calldata) external;
    function recordLogs() external;
    function getRecordedLogs() external returns (Log[] memory);
    function computeCreateAddress(address, uint256) external pure returns (address);
    function getNonce(address) external view returns (uint64);
}

/// The deploy script, driven in-process. Nothing is sent: forge test records
/// the broadcast and never signs for a network. The key is a small test
/// scalar, never a real key. Every test sets the same two env values,
/// because tests run in parallel and share one process environment.
/// Importing the script also makes `forge test` compile it.
contract DeployHashKeySealScriptTest {
    Vm internal constant vm = Vm(address(uint160(uint256(keccak256("hevm cheat code")))));

    uint256 internal constant TEST_KEY = 0xA11CE5EED;
    address internal constant PUBLISHER = address(0xB0B01);

    function _env() internal {
        vm.setEnv("ETH_PRIVATE_KEY", "0xa11ce5eed");
        vm.setEnv("HASHKEY_SEAL_PUBLISHER", "0x00000000000000000000000000000000000B0B01");
    }

    function _sealFrom(Vm.Log[] memory logs) internal pure returns (HashKeySeal) {
        address found;
        uint256 n;
        for (uint256 i; i < logs.length; i++) {
            if (logs[i].topics[0] == HashKeySeal.PublisherSet.selector) {
                found = logs[i].emitter;
                n++;
            }
        }
        require(n == 1, "one seal per run");
        // payable() keeps this compiling if a payable receive is ever added,
        // so the no-funds tests fail for the reason they name
        return HashKeySeal(payable(found));
    }

    function _runOn(uint256 id) internal {
        _env();
        vm.chainId(id);
        address broadcaster = vm.addr(TEST_KEY);
        address predicted = vm.computeCreateAddress(broadcaster, vm.getNonce(broadcaster));
        DeployHashKeySeal script = new DeployHashKeySeal();
        vm.recordLogs();
        script.run();
        HashKeySeal seal = _sealFrom(vm.getRecordedLogs());
        require(address(seal) == predicted, "created by the broadcaster");
        require(address(seal).code.length > 0, "code");
        require(seal.owner() == broadcaster, "owner is the broadcasting wallet");
        require(seal.owner() != address(script), "owner is not the script contract");
        require(seal.publishers(PUBLISHER), "publisher is the env value");
        require(!seal.publishers(broadcaster), "the owner is not a publisher");
        require(address(seal).balance == 0, "balance");
    }

    function testRunOnTestnet133() public {
        _runOn(133);
    }

    function testRunOnMainnet177() public {
        _runOn(177);
    }

    function testRunWithCliWalletMakesTheSenderTheOwner() public {
        _env();
        vm.chainId(133);
        DeployHashKeySeal script = new DeployHashKeySeal();
        vm.recordLogs();
        script.runWithCliWallet();
        HashKeySeal seal = _sealFrom(vm.getRecordedLogs());
        // with no key, the broadcaster is the sender forge was given (tx.origin here)
        require(seal.owner() == tx.origin, "owner is the CLI wallet");
        require(seal.owner() != address(script), "owner is not the script contract");
        require(seal.publishers(PUBLISHER), "publisher is the env value");
    }

    /// One revert per run: a reverted run leaves the broadcast open, so a
    /// second run in the same test would fail for that reason instead.
    function testFuzz_otherChainsRevertUnexpectedChain(uint64 id) public {
        vm.assume(id != 133 && id != 177);
        _env();
        vm.chainId(id);
        DeployHashKeySeal script = new DeployHashKeySeal();
        vm.expectRevert(abi.encodeWithSelector(DeployHashKeySeal.UnexpectedChain.selector, uint256(id)));
        script.run();
    }

    function testFuzz_cliWalletOnOtherChainsReverts(uint64 id) public {
        vm.assume(id != 133 && id != 177);
        _env();
        vm.chainId(id);
        DeployHashKeySeal script = new DeployHashKeySeal();
        vm.expectRevert(abi.encodeWithSelector(DeployHashKeySeal.UnexpectedChain.selector, uint256(id)));
        script.runWithCliWallet();
    }

    /// Both sides of each accepted id, plus Ethereum mainnet and anvil's
    /// default, one per test (see above).
    function testRevertsOn132() public {
        testFuzz_otherChainsRevertUnexpectedChain(132);
    }

    function testRevertsOn134() public {
        testFuzz_otherChainsRevertUnexpectedChain(134);
    }

    function testRevertsOn176() public {
        testFuzz_otherChainsRevertUnexpectedChain(176);
    }

    function testRevertsOn178() public {
        testFuzz_otherChainsRevertUnexpectedChain(178);
    }

    function testRevertsOnEthereum1() public {
        testFuzz_otherChainsRevertUnexpectedChain(1);
    }

    function testRevertsOnAnvil31337() public {
        testFuzz_otherChainsRevertUnexpectedChain(31337);
    }
}
```

## What I could not verify

- **Live `eth_chainId` from `https://testnet.hsk.xyz` and `https://mainnet.hsk.xyz`.** The sandbox proxy refuses hsk.xyz, WebFetch cannot POST JSON-RPC, and the read-only QuickNode relay does not serve HashKey. Chain 177 was confirmed by an Ethereum L1 read of HashKey's dispute-game factory (`0x04Ec030f…DE50`, `gameImpls(1).l2ChainId() = 0xb1`). Chain 133 was confirmed only through HashKey's published `genesis.json`, its `rollup.json` and the docs source. The doc's statement that both endpoints were checked with `eth_chainId` on 25 October was not reproduced.
- **The rendered official docs site.** `docs.hskchain.net`, `docs.hsk.xyz` and `docs.hashkeychain.net` all returned ENOTFOUND. The values were taken from the docs source repository (`HSKChain/documentation` at `3736efc2`), which may differ from what is deployed.
- **Explorer liveness and the Blockscout verify API.** I could not reach either explorer, and could not run `forge verify-contract` end to end, because forge needs `binaries.soliditylang.org`, which is blocked.
- **Real Ledger or Trezor signing.** Only the no-device refusal was seen. Sender selection and the default derivation path come from Foundry 1.8.6 source.
- **Real HashKey RPC behaviour.** Not verified: the exact `already known` and `nonce too low` strings, whether a `pending` nonce includes pool transactions, which state `eth_estimateGas` uses, and real latencies against the 20 s budget. All RPC behaviour here came from local fakes and anvil.
- **What the production web container holds.** Whether it has `python3`, `bot/`, `eth-account` and `cryptography`, and whether `HASHKEY_SEAL_SIGNER_KEY` is in `web.env`. Also whether production uses the Docker `uvicorn api_bridge:app` path, which decides whether L-7 is live.
- **How seal hashes are built upstream.** Their entropy, salt and domain are out of scope; the route accepts any 32-byte word from an operator.
- **The repo preflight and full test gate.** I did not run them against the code; this was a review, and no code was changed. The targeted Python and Node tests named above were run.
