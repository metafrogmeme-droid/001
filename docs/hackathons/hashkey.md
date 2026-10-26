# HashKey Chain seal anchor

Internal review, 25 October 2026, revised 26 October 2026 after the Fable 5.1 audit (`HashKey Seal: Fable 5.1 audit`).

RUNECLAW seals agent calls and receipts (Claw Guard receipts and Agent Leash receipts) as hashes. `HashKeySeal` anchors those hashes on HashKey Chain. The contract stores the hash, the publisher that anchored it and the kind that publisher passed. It rejects any call that carries value and has no function that sends value; a balance forced in from outside (a selfdestruct beneficiary, an address funded before deployment) is unreachable and stays stuck, and nothing in the contract moves it. It is not upgradeable. Ownership moves in two steps, and renouncing the owner freezes the publisher allowlist: after renounce a publisher can be neither added nor removed, so a leaked publisher key could never be revoked. Renounce only when no hot key is a publisher.

The chain parameters below are the ones on HashKey's network page. The chain ids were checked with `eth_chainId` on 25 October 2026 and the two answers are recorded in `contracts/hashkey/networks.json` under `rpc_checked`:

- Network page: <https://docs.hskchain.net/docs/Build-on-HashKey-Chain/network-info>
- The developer quick start names the same testnet endpoint and chain id: <https://docs.hskchain.net/docs/Developer-QuickStart>
- `https://testnet.hsk.xyz` answered `0x85` (133)
- `https://mainnet.hsk.xyz` answered `0xb1` (177)

| | chain id | RPC | explorer |
|---|---|---|---|
| testnet | 133 | `https://testnet.hsk.xyz` | `https://testnet-explorer.hsk.xyz` |
| mainnet | 177 | `https://mainnet.hsk.xyz` | `https://hashkey.blockscout.com` |

`contracts/hashkey/networks.json` is the reading the deploy script and the publisher share. The deploy script's chain ids are checked against that file, and the guard in `bot/web/mainnet_guard.py` reads the mainnet id from it too. The explorer hosts are the ones the network page prints; no check in this repository has reached them, and the `ethereum-lists/chains` registry prints `hsk.blockscout.com` and `testnet-explorer.hskchain.net` instead. The file says so (`explorer_checked` is null), and a verify run that succeeds is what records it.

## What the contract does

`anchor(bytes32 hash, bytes32 kind)` stores the hash with the sender and the kind, and emits `Anchored` with the sender, the hash, the kind (all three indexed) and the block timestamp. The caller must be on the publisher allowlist. The zero hash and the zero kind revert. A hash that is already stored reverts, whatever kind is passed and whichever publisher calls: the first publisher to anchor a hash fixes its publisher and kind for good. Adding a second publisher therefore hands it that power over every hash it reaches first; keep one publisher per deployment unless that is wanted.

`anchorOf(bytes32 hash)` returns the publisher and the kind, or two zero values when the hash is not stored. `isAnchored(bytes32)` is the yes-or-no view. `anchorMany(bytes32[] hashes, bytes32 kind)` anchors a batch under one kind with the same checks per element; one bad element reverts the whole batch.

The deployer is the owner and is not a publisher. The constructor sets the first publisher and emits `OwnershipTransferred(address(0), deployer)` and `PublisherSet`, so the allowlist and the owner can be rebuilt from logs. The owner adds and removes publishers with `setPublisher` until `renounceOwnership`. Ownership moves in two steps: `transferOwnership(newOwner)` proposes, `acceptOwnership()` by the proposed owner completes, and a later proposal replaces the pending one. Renounce drops any pending proposal and sets the owner to the zero address, which the owner check refuses outright.

Kind is opaque apart from zero. The two receipt labels are the keccak of the ascii strings `claw-guard` and `agent-leash`. The server publisher anchors only those two; the contract accepts any non-zero kind.

## Tests

Foundry 1.8.6, the version CI installs, with solc 0.8.24 and the EVM version `cancun` pinned in `contracts/hashkey/foundry.toml`:

```bash
cd contracts/hashkey
forge test
forge snapshot --no-match-test 'testFuzz_' --check
```

The unit suite covers construction events, a duplicate hash from the same and from a second publisher (and that revoking the second does not free the hash), zero hash and zero kind, the batch, publisher authorization and re-adding, the two-step transfer, renounce (including that nobody and not the zero address can change the allowlist after it), every selector refusing value with the same calldata landing without it, and a walk over the runtime bytecode that finds no CALL, DELEGATECALL, CREATE, SELFDESTRUCT or transient-storage opcode. Fuzz runs cover a publisher anchoring once, strangers, non-owners before and after renounce, and the pending owner. An invariant suite drives the contract through a handler that impersonates the deployer, the publisher, a second publisher and a stranger, and holds that no call lands value, the owner is an actor or zero and zero only after a renounce, ownership changes only by accept, no non-publisher ever anchors, no non-owner ever changes the allowlist, and every recorded anchor keeps its first writer and kind. A separate suite drives the deploy script contract's guards without a network.

The gas snapshot is a ratchet: `forge snapshot --no-match-test 'testFuzz_' --check` runs in CI, and an intended gas change is re-recorded with `forge snapshot --no-match-test 'testFuzz_'` in the same commit.

## Deploy to testnet

The server does not deploy. Run this on the owner's machine. The script refuses `RUNECLAW_SERVER=1`, refuses to run while a server signing key is set (`WEB3_SIGNER_PRIVATE_KEY`, `HYPERLIQUID_PRIVATE_KEY`, `SAR_ORACLE_SIGNING_KEY`, `HRC_FEED_SIGNER_KEY`, `HASHKEY_SEAL_SIGNER_KEY`), and refuses to run while `contracts/hashkey/.env` exists, because forge would load that file on its own. The shell sources nothing. The key is not written into the repo and is not placed on the command line.

`HASHKEY_SEAL_PUBLISHER` is the first allowlisted publisher; a mixed-case value must carry a correct EIP-55 checksum. Foundry (`forge` and `cast`) has to be on `PATH`.

Hardware wallet, or a keystore created with `cast wallet import`, is the path to prefer:

```bash
HASHKEY_WALLET=ledger HASHKEY_SEAL_PUBLISHER=<publisher-address> \
  contracts/hashkey/script/deploy.sh --network testnet
HASHKEY_WALLET=ledger HASHKEY_SEAL_PUBLISHER=<publisher-address> \
  contracts/hashkey/script/deploy.sh --network testnet --broadcast

HASHKEY_WALLET=trezor HASHKEY_SEAL_PUBLISHER=<publisher-address> \
  contracts/hashkey/script/deploy.sh --network testnet --broadcast

HASHKEY_WALLET=account HASHKEY_ACCOUNT=mykey HASHKEY_SEAL_PUBLISHER=<publisher-address> \
  contracts/hashkey/script/deploy.sh --network testnet --broadcast
```

A raw key is read by the script contract from the environment, never passed as an argument, and must carry the `0x` prefix (forge reads a key without it as a decimal number):

```bash
export HASHKEY_SEAL_PUBLISHER=<publisher-address>
export ETH_PRIVATE_KEY=0xYourKey
contracts/hashkey/script/deploy.sh --network testnet
contracts/hashkey/script/deploy.sh --network testnet --broadcast
```

If `HASHKEY_WALLET` and `ETH_PRIVATE_KEY` are both set, the script stops and asks you to unset one.

The script asks the endpoint for `eth_chainId` and stops unless the answer is 133. The expected chain id then travels into the script contract as an argument and is compared with `block.chainid` there, so the chain forge connected to is the chain you named. It is never passed as `--chain`: in forge that flag overrides `block.chainid` in the simulation while the broadcast still signs for the endpoint's real chain, which would make the comparison true by construction.

A simulation always runs first and prints the owner-to-be under `== Logs ==`. The deploying wallet is the owner for the life of the contract. With `--broadcast` the script then deploys with that address as the expected owner, and the script contract refuses to deploy for anyone else.

Gas is paid by that wallet. The network page explains how to obtain testnet HSK for gas.

## Mainnet, by the owner, by hand

Mainnet publish is not wired into the server. There is no server path that signs chain 177. The owner deploys from their own wallet, with the same script:

```bash
HASHKEY_WALLET=ledger HASHKEY_SEAL_PUBLISHER=<publisher-address> \
  contracts/hashkey/script/deploy.sh --network mainnet --broadcast
```

The script stops unless `eth_chainId` is 177, runs the simulation, prints the owner-to-be, and asks you to type that address back before it broadcasts. Without a terminal, `HASHKEY_SEAL_CONFIRM_OWNER=<that address>` stands in for the typed answer. Record the contract address yourself. The server will not publish to it. Confirm that the first publisher anchors once; renounce only after the publisher set is final and no hot key is on it.

## Verification on the explorer

The explorers on the network page are Blockscout. From the contract directory the deploy script runs `forge verify-contract` with verifier `blockscout`, compiler `0.8.24`, EVM version `cancun`, optimizer on, 200 runs (the same settings as `contracts/hashkey/foundry.toml`):

```bash
export HASHKEY_SEAL_PUBLISHER=<publisher-address>
contracts/hashkey/script/deploy.sh --network testnet --verify 0xDeployedAddress
contracts/hashkey/script/deploy.sh --network mainnet --verify 0xDeployedAddress
```

`--verify` does not sign and does not broadcast. The constructor argument is `HASHKEY_SEAL_PUBLISHER`.

If the explorer API refuses the submission, open the contract on the explorer and use its verify page. The compiler input is:

```bash
cd contracts/hashkey
forge verify-contract --show-standard-json-input \
  0xDeployedAddress src/HashKeySeal.sol:HashKeySeal
```

Paste that input. Compiler `0.8.24`. EVM version `cancun`. Optimizer enabled, 200 runs.

## Server publisher

Off unless `HASHKEY_SEAL_ENABLED` is `1` or `true`. The door is `flag_on_for` in `app/lib/admin_preview.js`, and the route stays admin-only when the flag is on: a non-operator gets 404 either way. An operator, while the flag is off, can read and is told this is an admin preview. A preview does not sign. `POST /api/hashkey-seal/anchor` signs only when the flag is on, the caller is an operator, and the publisher has accepted the request.

The publisher lives in `bot/hashkey_seal` and runs as a child process of the web container, which holds `HASHKEY_SEAL_SIGNER_KEY`. Three processes ask the same startup guard: `bot.main` and `api_bridge` refuse to start, and the publisher child refuses to run, when `HASHKEY_SEAL_CHAIN_ID` is set to anything other than 133 while that key is set, or when any server-wide mainnet refusal applies. An unset chain id is not treated as a chain: the publisher asks the endpoint and refuses any answer other than 133 before the key is read. The signed transaction carries chain id 133 whatever the endpoint said, so a mainnet node rejects it.

Before it signs, the publisher also refuses a kind other than the two it advertises, an RPC URL that is not `https` (or `http` on the loopback host), a hash already stored, a gas estimate above its ceiling, a priority fee or fee cap above `HASHKEY_SEAL_MAX_FEE_GWEI` (default 10), a nonce above 2^64 - 1, and a budget too short to sign and send. The whole publish runs inside `HASHKEY_SEAL_DEADLINE_SEC` (default 14); the route waits longer than that, so it never kills a child that may already have sent.

After signing there are three values, not two. The publisher writes `tx_hash=… nonce=… sender=…` to stderr before the send, then polls for the receipt while the budget lasts: `ok: true` on a receipt whose status is 1, `ok: false` with reason `reverted` on status 0, and `ok: null` with reason `submitted` when the chain has not answered. A send whose answer was lost is `send_unconfirmed` with `broadcast: null`; a node that answers a different hash is `hash_mismatch`. `anchored` is read back from the contract, never derived from the receipt. The route answers 202 for `ok: null`, 200 otherwise, and a child that dies or prints nothing usable is reported with `signed`, `broadcast` and `anchored` all null, with the hash from stderr when it is there. A failed read is never reported as "not anchored".

`GET /api/hashkey-seal` reports whether the flag, the contract address and a signer key are set, the budget and the fee ceiling. `GET /api/hashkey-seal?hash=0x…` asks `isAnchored` and, when the hash is stored, `anchorOf`: the body carries `publisher` and `kind`, with `attribution` saying whether they were read. Both require the same admin door.

| variable | meaning |
|---|---|
| `HASHKEY_SEAL_ENABLED` | off unless `1` or `true` |
| `HASHKEY_SEAL_ADDRESS` | the deployed contract |
| `HASHKEY_SEAL_CHAIN_ID` | when set, must be 133 while the key is set |
| `HASHKEY_SEAL_RPC_URL` | replaces the documented testnet endpoint for that process only; `https` or loopback `http`; never returned by the route |
| `HASHKEY_SEAL_SIGNER_KEY` | the testnet publisher key; never printed, never committed |
| `HASHKEY_SEAL_DEADLINE_SEC` | the whole publish's budget, default 14 |
| `HASHKEY_SEAL_MAX_FEE_GWEI` | the ceiling on the priority fee and the fee cap, default 10 |
