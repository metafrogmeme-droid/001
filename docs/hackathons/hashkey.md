# HashKey Chain seal anchor

Internal review, 25 October 2026.

RUNECLAW seals agent calls and receipts (Claw Guard receipts and Agent Leash receipts) as hashes. `HashKeySeal` anchors those hashes on HashKey Chain. The contract stores a hash and a kind. It holds no balance, it has no function that sends the chain's native coin, it is not upgradeable, and renouncing the owner freezes the publisher allowlist.

The chain parameters below are the ones on HashKey's network page, checked the same day with `eth_chainId`:

- Network page: <https://docs.hskchain.net/docs/Build-on-HashKey-Chain/network-info>
- The developer quick start names the same testnet endpoint and chain id: <https://docs.hskchain.net/docs/Developer-QuickStart>
- `https://testnet.hsk.xyz` answered `0x85` (133)
- `https://mainnet.hsk.xyz` answered `0xb1` (177)

| | chain id | RPC | explorer |
|---|---|---|---|
| testnet | 133 | `https://testnet.hsk.xyz` | `https://testnet-explorer.hsk.xyz` |
| mainnet | 177 | `https://mainnet.hsk.xyz` | `https://hashkey.blockscout.com` |

`contracts/hashkey/networks.json` is the reading the deploy script and the publisher share. The deploy script's chain ids are checked against that file.

## What the contract does

`anchor(bytes32 hash, bytes32 kind)` stores the hash and emits `Anchored` with the sender, the hash, the kind, and the block timestamp. The caller must be on the publisher allowlist. The deployer sets the first publisher in the constructor and can add or remove publishers with `setPublisher` until `renounceOwnership`. A hash that is already stored reverts, including when the kind differs. The zero hash reverts. `isAnchored(bytes32)` is the view.

Kind is opaque. The two receipt labels are the keccak of the ascii strings `claw-guard` and `agent-leash`.

A call that carries value does not land. There is no function that forwards the native coin.

## Tests

Foundry 1.8.6, the version CI installs:

```bash
cd contracts/hashkey
forge test
```

The suite covers a duplicate hash, publisher authorization, owner and renounce, a call that carries value, the anchor event, and fuzz runs (a publisher anchors once, a stranger cannot, only the owner changes the allowlist).

## Deploy to testnet

The server does not deploy. Run this on the owner's machine. The script refuses `RUNECLAW_SERVER=1` and refuses to run while a server signing key is set (`WEB3_SIGNER_PRIVATE_KEY`, `HYPERLIQUID_PRIVATE_KEY`, `SAR_ORACLE_SIGNING_KEY`, `HRC_FEED_SIGNER_KEY`, `HASHKEY_SEAL_SIGNER_KEY`). It never sources `.env` or `web.env`. The key is not written into the repo and is not placed on the command line.

`HASHKEY_SEAL_PUBLISHER` is the first allowlisted publisher. Foundry (`forge` and `cast`) has to be on `PATH`.

Environment key, read by the script contract and never passed as an argument:

```bash
export HASHKEY_SEAL_PUBLISHER=0xYourPublisher
export ETH_PRIVATE_KEY=0xYourKey
contracts/hashkey/script/deploy.sh --network testnet
contracts/hashkey/script/deploy.sh --network testnet --broadcast
```

The first command simulates. `--broadcast` deploys. The script asks the endpoint for `eth_chainId` and stops unless the answer is 133.

Hardware wallet, or a keystore created with `cast wallet import`:

```bash
HASHKEY_WALLET=ledger HASHKEY_SEAL_PUBLISHER=0xYourPublisher \
  contracts/hashkey/script/deploy.sh --network testnet --broadcast

HASHKEY_WALLET=trezor HASHKEY_SEAL_PUBLISHER=0xYourPublisher \
  contracts/hashkey/script/deploy.sh --network testnet --broadcast

HASHKEY_WALLET=account HASHKEY_ACCOUNT=mykey HASHKEY_SEAL_PUBLISHER=0xYourPublisher \
  contracts/hashkey/script/deploy.sh --network testnet --broadcast
```

If `HASHKEY_WALLET` is set, the script unsets `ETH_PRIVATE_KEY` for the forge process. If both are set, it stops and asks you to unset one.

Gas is paid by that wallet. The network page explains how to obtain testnet HSK for gas.

## Mainnet, by the owner, by hand

Mainnet publish is not wired into the server. There is no server path that signs chain 177. The owner deploys from their own wallet, with the same script:

```bash
export HASHKEY_SEAL_PUBLISHER=0xYourPublisher
export ETH_PRIVATE_KEY=0xYourKey
contracts/hashkey/script/deploy.sh --network mainnet --broadcast
```

The script stops unless `eth_chainId` is 177. Record the address yourself. The server will not publish to it.

## Verification on the explorer

The explorers on the network page are Blockscout. From the contract directory the deploy script runs `forge verify-contract` with verifier `blockscout`, compiler `0.8.24`, optimizer on, 200 runs (the same settings as `contracts/hashkey/foundry.toml`):

```bash
export HASHKEY_SEAL_PUBLISHER=0xYourPublisher
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

Paste that input. Compiler `0.8.24`. Optimizer enabled, 200 runs.

## Server publisher

Off unless `HASHKEY_SEAL_ENABLED` is `1` or `true`. The door is `flag_on_for` in `app/lib/admin_preview.js`, and the route stays admin-only when the flag is on: a non-operator gets 404 either way. An operator, while the flag is off, can read and is told this is an admin preview. A preview does not sign. `POST /api/hashkey-seal/anchor` signs only when the flag is on, the caller is an operator, and the publisher has accepted the request.

The publisher lives in `bot/hashkey_seal`. It signs only after the endpoint answers chain id 133. Any other answer, including 177, is refused before the signer key is read. A failed read is not reported as "not anchored". `HASHKEY_SEAL_CHAIN_ID`, when set to anything other than 133, refuses process startup while `HASHKEY_SEAL_SIGNER_KEY` is set. An unset chain id does not: the publisher still asks the endpoint.

`HASHKEY_SEAL_ADDRESS` is the deployed contract. `HASHKEY_SEAL_RPC_URL`, when set, replaces the documented testnet endpoint for that process only and is not returned by the route. The key is never printed and never committed.

`GET /api/hashkey-seal` reports whether the flag, the contract address, and a signer key are set. `GET /api/hashkey-seal?hash=0x…` asks `isAnchored`. Both require the same admin door.
