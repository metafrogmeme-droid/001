# Vibe Code on DOGE — the RUNECLAW entry

*Hackathon by DogeOS with Noah AI, 19 October – 6 November 2026 (winners 11 November). 36,000 DOGE in prizes. Category: Gaming & Entertainment (or Community & Social).*

## The entry in one paragraph

**The Daily Duel, provable on DogeOS.** Every day the RUNECLAW agent deals a few rounds (a market call, a range, a mover, a football match). The agent's own stance is sealed before anyone sees it, every player's call is sealed the moment it is made, and every settlement is sealed when it is scored. Each completed day's seals fold into one Merkle root. On DogeOS the root is **anchored by the players' own wallets**: a zero-value transaction to yourself whose data is the root, so the DogeOS block clock becomes an independent upper bound on when every call in the day existed. No key ever touches our server. The judge can take any receipt, replay the Merkle proof in the browser, and check the calldata on the DogeOS explorer.

## What is built (branch `dogeos`)

| piece | where | what it does |
|---|---|---|
| Chain registry | `app/lib/root_anchor.js` | Base and the DogeOS testnet (chain id 6281971, `https://rpc.testnet.dogeos.com/`, explorer `https://dogeos-testnet.l2scan.co/tx/`, native DOGE). One reading of each chain; `DOGEOS_TESTNET_RPC_URLS` overrides the RPC list. |
| Per-chain anchors | `app/db.js` (`seal_root_anchors`), `app/lib/seal_roots.js` | One row per (day, chain), written only after the chain confirmed the calldata, first write per chain wins. The Base leg is mirrored into the older column. |
| Routes | `app/routes/roots.js` | `GET /api/roots/chains`; `GET /api/roots/anchor-plan/:day?chain=`; `GET /api/roots/verify/:day?chain=`; `POST /api/roots/anchor {day, tx_hash, chain}`. The feed `GET /api/roots` lists every chain's anchor per day. |
| Pages | `app/public/roots.html`, `app/public/call.html`, `app/public/js/anchor_cell.js` | A cell per chain, painted only from what that chain said (verified / recorded-not-confirmed / mismatch / unanchored). |
| The DogeOS app | `GET /doge` (`app/routes/dogeapp.js`, `app/public/js/doge-app.js`, `doge-proof.js`) | Sign in with a DogeOS wallet (the site's own wallet sign-in: a signed nonce, no transaction), play the Daily Duel (the website's own renderer), see the proof panel, and **anchor a day's root on the DogeOS testnet from your wallet**. |
| Tests | `app/test/roots_anchor_on_more_than_one_chain.test.js`, `app/test/the_doge_app_plays_the_duel_and_anchors_on_dogeos.test.js`, `anchor_cell.test.js`, `root_anchor.test.js` | The routes end to end with the chain stubbed at the RPC; the page; the proof block; the cells. |

### How the anchoring works, step by step

1. A completed UTC day has a root: `GET /api/roots` (every seal of the day, deduped, sorted, sha256 Merkle; the construction is in the feed).
2. `GET /api/roots/anchor-plan/2026-10-18?chain=dogeos-testnet` answers the exact transaction: `to` = your own address, `value` 0, `data` = hex of `RCROOT1:2026-10-18:<root>`.
3. Your wallet sends it on the DogeOS testnet (the app asks the wallet to switch to chain 6281971, adding it from the registry's description when the wallet has never seen it).
4. `POST /api/roots/anchor {day, tx_hash, chain: "dogeos-testnet"}`: the server reads the transaction through the DogeOS RPC, compares the calldata byte for byte with the payload, reads the block, and records the anchor with the block time. Not mined yet answers 503 (try again); wrong calldata answers 400; a second anchor on the same chain answers 409.
5. `GET /api/roots/verify/2026-10-18?chain=dogeos-testnet` re-checks live, for anyone, any time. The cell on `/roots`, the receipt on `/call/<id>` and the proof panel on `/doge` are painted from that answer, never from our column.

### What a receipt carries

`GET /api/call/<seal>` (and `round:`, `duel:`, `settle:` for the duel) answers the seal, the Merkle proof to the day's root, and `anchors: [{chain, tx, block_time}]` for every chain the day is anchored on. The verify page replays the proof in the browser.

## Operator steps (you)

1. **Wallet.** Any EVM wallet (MetaMask, Rabby) with an account funded from the DogeOS testnet faucet (`https://faucet.testnet.dogeos.com`). The app adds the network to the wallet itself. Never paste a key anywhere.
2. **Deploy** the `dogeos` branch (after review) to the website. No new secret is needed; `DOGEOS_TESTNET_RPC_URLS` is optional.
3. **Anchor.** Open `/doge`, sign in with the wallet, open the Proof tab, press *Anchor on DogeOS testnet* on each completed day. Each is one zero-value transaction.
4. **Noah AI.** Sign in at trynoah.ai, connect with DogeOS, and build the submission app from the prompt pack below. The app is a front end over the public API; it needs no key.
5. **Submit** by 6 November: project name, description, the Noah AI project, screenshots (the duel card, a receipt with the DogeOS anchor, the explorer page of an anchor transaction), the demo.

## Prompt pack for Noah AI

Paste, in order. Replace `https://www.humanoid-traders.com` if the deployment moves.

> **1. Purpose.** Build a DogeOS web app called "RUNECLAW Daily Duel". The user connects a DogeOS wallet and plays a daily prediction duel against an AI trading agent. Every call is sealed with a hash before the market moves, and each day's seals are anchored on the DogeOS testnet by the players' wallets. The app is a front end over an existing public API; it holds no keys and takes no payments.
>
> **2. Sign-in.** On "Connect", request accounts with `eth_requestAccounts`, then read `GET https://www.humanoid-traders.com/api/roots/chains`, find the chain with key `dogeos-testnet`, and ask the wallet to switch to its `chain_id` (`wallet_switchEthereumChain`; on error 4902 call `wallet_addEthereumChain` with `chainName`, `nativeCurrency`, `rpcUrls` and the explorer from that entry). Then `POST /api/auth/wallet/nonce {address}` returns `{message}`; sign it with `personal_sign`; `POST /api/auth/wallet/verify {address, signature}` returns `{token}`. Send `Authorization: Bearer <token>` on every call after that. The signature is not a transaction and costs no gas.
>
> **3. The duel.** `GET /api/duel/today` returns the day's rounds: each has `id`, `kind`, `question`, `options` (the exact words to show as buttons), `status`, `settles_at`, the player's own call if any, and `round_seal` (the hash shown on the card before any call). `POST /api/duel/pick {round_id, pick}` records a call and returns the agent's stance now that the player has committed, plus the call's `seal`. `GET /api/duel/me` returns the player's record (counts and accuracy percent only; there is no stake and no currency anywhere in this game). Show every refusal the server names.
>
> **4. The proof.** `GET /api/roots` lists completed days with `root`, `seal_count` and `anchors: [{chain, tx, block_time}]`. For each day without a `dogeos-testnet` anchor, show "Anchor on DogeOS": call `GET /api/roots/anchor-plan/<day>?chain=dogeos-testnet`, send `eth_sendTransaction` with `from` = `to` = the connected address, `value: "0x0"`, `data` = the plan's `data`; then `POST /api/roots/anchor {day, tx_hash, chain: "dogeos-testnet"}` (retry every 12 seconds while it answers 503 "not yet mined", at most 10 times). Show `GET /api/roots/verify/<day>?chain=dogeos-testnet` live: green only when `status` is `verified`; "recorded, not confirmed" otherwise; red on `mismatch`. Link `explorer` from the answer.
>
> **5. Receipts.** Every call's `seal` opens `https://www.humanoid-traders.com/call/<seal>`, where the Merkle proof is replayed in the browser and the day's DogeOS anchor is shown. Link it from each call.
>
> **6. Rules of the surface.** No dollar amounts, no prices presented as advice, no token or sale content. Percent, counts and the market facts the API returns only. Say "unreadable" when a call fails; never show an empty list as "no rounds".

## The demo, in three minutes

1. Connect the wallet; the app switches it to the DogeOS testnet and signs you in.
2. Call a round. Show the round seal that was on the card before the call, and the agent's stance revealed after.
3. Open the Proof tab: a day anchored on DogeOS, the explorer page of the transaction with `RCROOT1:<day>:<root>` in the calldata, and a receipt whose Merkle proof replays in the browser to that root.
4. Press Anchor on an unanchored day and watch the cell go from "recorded, not confirmed" to "verified on DogeOS testnet" once the chain confirms it.

## Judging criteria, answered

- **Functionality.** The duel, the sign-in and the anchoring are live code with end-to-end tests; the chain is read, never trusted from our own database.
- **DogeOS integration.** Wallet sign-in on the DogeOS testnet; every day's root anchored on DogeOS by players' wallets; receipts that link to the DogeOS explorer.
- **Originality.** A prediction game where the house cannot cheat: the agent's stance is committed before the player calls, and the chain timestamps the day.
- **User experience.** One page, three tabs, every refusal named, every absence said in words.
- **Potential.** The same anchoring serves every surface RUNECLAW seals (signals, arena trades, agent claims), and virtual stakes in testnet DOGE are the next step.

## Not in this entry, on purpose

- No stake, wager or currency in the duel (the house rule, and the category).
- No custody: the server never signs. Anchoring is the player's transaction.
- No presale or token content anywhere on `/doge`.
- Draco and USDZ cuts of the mascot are not served; the owl on the site is the 660 KB glTF.
