# Go-Live Runbook — the three operator ceremonies

The three manual procedures only the operator can perform, in the order that
cannot bite. Everything here runs from the operator's own machine and wallet;
the server never signs, never sends, never holds keys. No secret VALUE ever
appears in this file — only the NAMES of environment variables that are
documented in `.env.example`.

---

## 1. Rune of Entry — lighting the forge

The contract (`contracts/rune/RuneOfEntry.sol`) is self-contained, immutable
after deploy, has no admin surface and nothing payable. The web stack
(`/rune`, the dashboard mint card, `/api/nft/*`, the `get_rune_stats` MCP
tool) is already live and honest about the "forge is cold" state — deploying
lights everything up with **zero code changes**.

### Steps (operator machine, foundry installed)

1. **Mint a fresh voucher key** — this key signs EIP-712 mint VOUCHERS, not
   transactions. Its blast radius if leaked is "strangers can mint free
   soulbound badges", nothing more. Still: fresh key, never reused, never
   committed.

       cast wallet new

   Keep the private key for step 4; note the address for step 2.

2. **Deploy to Base (chain 8453)** with the voucher signer address as the
   only constructor argument:

       forge create contracts/rune/RuneOfEntry.sol:RuneOfEntry \
         --rpc-url https://mainnet.base.org \
         --private-key <YOUR-DEPLOYER-KEY> \
         --constructor-args <VOUCHER-SIGNER-ADDRESS>

   The deployer key pays gas once and holds no ongoing power — the contract
   has no owner functions.

3. **Verify the deploy before wiring anything**: on Basescan, call
   `locked(1)` (should revert or return true once minted), and check
   `totalMinted()` returns 0. The contract address is public by design.

4. **Set the two environment variables** (names as in `.env.example`) on the
   web deployment, then redeploy:

   - `NFT_CONTRACT_ADDRESS` = the deployed address
   - `NFT_VOUCHER_KEY` = the voucher private key from step 1

5. **Confirm the forge is lit**: `/rune` shows the count and the Basescan
   link; `/api/nft/stats` returns `deployed: true`. Mint the first rune from
   your own wallet via the dashboard card — that is the end-to-end test.

### What can go wrong

- Setting `NFT_CONTRACT_ADDRESS` without `NFT_VOUCHER_KEY` (or vice versa):
  mint plans fail while stats read fine. Set both, redeploy once.
- The voucher key signs against domain
  `{name: "RUNECLAW Rune of Entry", chainId: 8453, verifyingContract}` — the
  server derives this from the same source as the contract; nothing to
  configure.

---

## 2. ERC-8257 — registering the tool manifest

**The trap this section exists for:** `creatorAddress` is INSIDE the hashed
manifest. Setting `TOOL_CREATOR_ADDRESS` changes the manifest hash — so any
calldata captured BEFORE the env var was set becomes invalid the moment it
is set. The registration plan endpoint refuses to pretend otherwise: while
the creator is unset it serves `ready: false` with a hash warning.

Also: **every MCP tool change moves the hash.** Register when the tool set
is settled; after registration, each tool change costs a re-registration
transaction to stay verifiable.

### Steps (safe order — do not reorder)

1. Set `TOOL_CREATOR_ADDRESS` to your full wallet address (only you have
   it; it is never in the repo) on the web deployment.
2. Redeploy and wait for the app to come up.
3. Fetch the **live** plan — never a saved copy:

       GET /api/tool/registration-plan

   Confirm `ready: true` and note the NEW manifest hash.
4. Send the transaction from that live plan's calldata to the registry
   `0x265BB2DBFC0A8165C9A1941Eb1372F349baD2cf1` on Base (8453) — the plan
   includes a ready-made `cast send` command.
5. Verify: the plan endpoint reports the registration; the manifest at
   `/.well-known/ai-tool/runeclaw-intel.json` must now stay byte-identical
   for the on-chain hash to keep verifying.

### After registration

Directory listings and ENS pointers become worthwhile only now (they
reference the registered record). Any future tool change: repeat steps 3–5.

---

## 3. Daily seal roots — the first on-chain anchor

Once a UTC day completes with at least one sealed call, that day's Merkle
root can be anchored on Base with a zero-value transaction whose calldata
is `hex(utf8("RCROOT1:<day>:<root>"))`.

### Steps

1. Pick a completed day and fetch the plan:

       GET /api/roots/anchor-plan/<YYYY-MM-DD>

2. Send the zero-value transaction it describes from any wallet you
   control — the anchor's authority comes from the calldata content, not
   the sender.
3. Record the transaction hash back (the plan response documents the
   endpoint) so `/call/<key>` pages and frame cards can show the anchor.

The block timestamp then bounds every seal in that day with a fact no one
here controls. Mirroring the `/api/roots` feed anywhere public (a repo, a
tweet) covers days you choose not to anchor.

---

## Standing rules (all three ceremonies)

- The server never holds keys, never signs transactions, never sends them.
  Every ceremony runs from the operator's wallet.
- No secret value in this repo, ever — env var NAMES only.
- When a plan endpoint says `ready: false`, believe it. The plans are
  computed from live state precisely so stale copies cannot be trusted.
