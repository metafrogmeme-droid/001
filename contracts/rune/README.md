# RUNECLAW Rune of Entry — soulbound signup NFT

A membership sigil, **free forever** (the user pays only gas, well under a
cent on Base), **one per wallet**, **soulbound** (ERC-5192: every transfer
and approval path reverts), with art **generated and stored fully on-chain**
— a deterministic rune drawn from `keccak(tokenId, minter)`, no IPFS, no
server, no link that can die.

## Hard lines (by construction, pinned by tests)

- **Not an investment.** Nothing is payable — no mint price variable even
  exists — and every token's metadata says "a badge, not an investment".
  Never market it with value, scarcity or roadmap language.
- **No admin surface.** No owner, no pause, no withdraw, no upgrade. What is
  deployed is what runs, forever. The voucher signer is immutable.
- **Non-custodial.** The server never signs or sends a transaction. Users
  mint from their own wallet; the operator deploys from theirs.
- **Voucher-key blast radius.** The off-chain voucher key only gates *who
  may mint*. It can never move funds or touch an existing token — worst-case
  compromise is unauthorized free mints, nothing else. The key never enters
  this repository.

## How the voucher works

The server (`GET /api/nft/mint-plan`, authed) signs an EIP-712
`MintVoucher(address to)` over the domain
`{name: "RUNECLAW Rune of Entry", chainId, verifyingContract}` for a
signed-up, wallet-linked user. The contract recovers the signer and mints
iff it matches the immutable `voucherSigner`. The voucher binds to
`msg.sender`, this chain and this contract — it cannot be replayed for
another wallet or another deployment, and `tokenOf[msg.sender] != 0` makes
same-wallet replay a no-op.

## Deploy runbook (operator wallet only)

1. Generate a **dedicated** voucher keypair on your machine (never the
   deploy key, never in the repo):
   `cast wallet new`
   The *address* goes into the constructor; the *private key* later becomes
   the server's `NFT_VOUCHER_KEY` env var.
2. Deploy to Base (8453) from your own wallet:
   ```sh
   forge create contracts/rune/RuneOfEntry.sol:RuneOfEntry \
     --rpc-url https://mainnet.base.org \
     --private-key <YOUR_DEPLOY_KEY_NEVER_SHARED> \
     --constructor-args <VOUCHER_SIGNER_ADDRESS>
   ```
3. Verify the source on Basescan (`forge verify-contract`, compiler 0.8.x,
   optimizer 200 runs, evmVersion paris) so anyone can read what they mint.
4. Record the deployed address — it becomes `NFT_CONTRACT_ADDRESS` for the
   web integration stage.

## Tests

`npm test` compiles the real contract (solc) and runs the real bytecode on
an in-process EVM (`@ethereumjs/vm`) with real EIP-712 signatures — no
network, no mocks of the thing under test. Covered: voucher gating and
binding, one-per-wallet, non-payability of the whole ABI, every soulbound
revert path, ERC-165/721/5192 interfaces, absence of any admin surface,
mint gas ceiling, and that `tokenURI` yields valid honest JSON + SVG,
unique per token.
