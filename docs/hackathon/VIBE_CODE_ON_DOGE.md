# Vibe Code on DOGE — the RUNECLAW entry

*Hackathon by DogeOS with Noah AI, 19 October – 6 November 2026 (winners 11 November). The rules give no time zone for the close, so **submit by 5 November UTC**. 36,000 DOGE in prizes: Grand Prize 10,000, Best Overall 8,000, Best DeFi 6,000, Best Gaming 6,000, Community Vote 6,000. Our track: **Best Gaming**; the entry is also eligible for the Grand Prize, Best Overall and the Community Vote. Rules read 21 October 2026.*

## The entry in one paragraph

**The Daily Duel, provable on DogeOS.** Every day the RUNECLAW agent deals a few rounds (a market call, a range, a mover, a football match). The agent's own stance is sealed before anyone sees it, every player's call is sealed the moment it is made, and every settlement is sealed when it is scored. Each completed day's seals fold into one Merkle root. On DogeOS the root is **anchored by the players' own wallets**: a zero-value transaction to yourself whose data is the root, so the block time, set by DogeOS's sequencer and not by us, bounds when every call in the day existed. No key ever touches our server. The judge can take any receipt, replay the Merkle proof in the browser, and check the calldata on a DogeOS explorer.

## DogeOS Integration (the submission form's own field)

Paste as is:

> Players sign in with a DogeOS wallet (a signed nonce, no gas) and the app switches the wallet to the DogeOS testnet, chain 6281971, adding it if the wallet has never seen it. Every completed day's Merkle root of sealed calls is anchored on DogeOS by a player's own zero-value transaction whose calldata is `RCROOT1:<day>:<root>`. The server never signs: it reads the transaction back through DogeOS's RPC (dRPC as a fallback), compares the calldata byte for byte, and records the anchor only once it is 17 blocks deep, the reorg depth DogeOS documents. Anyone can re-verify any day live; the answer also says whether DogeOS has finalized the block. Every receipt links to the anchor on the DogeOS explorer (l2scan, with Blockscout beside it).

## What is built (merged in #647, hardened 21 October)

| piece | where | what it does |
|---|---|---|
| Chain registry | `app/lib/root_anchor.js` | Base and the DogeOS testnet: chain id 6281971, RPCs `https://rpc.testnet.dogeos.com/` then `https://dogeos-testnet.drpc.org` (the server reads both; pages and wallets are handed only DogeOS's own), explorers `https://dogeos-testnet.l2scan.co/tx/` and `https://blockscout.testnet.dogeos.com/tx/`, native DOGE, reorg depth 17. One reading of each chain; `DOGEOS_TESTNET_RPC_URLS` overrides the server's RPC list. |
| Per-chain anchors | `app/db.js` (`seal_root_anchors`), `app/lib/seal_roots.js` | One row per (day, chain), written only after the chain confirmed the calldata 17 blocks deep, first write per chain wins. The Base leg is mirrored into the older column. |
| Routes | `app/routes/roots.js` | `GET /api/roots/chains`; `GET /api/roots/anchor-plan/:day?chain=`; `GET /api/roots/verify/:day?chain=`; `POST /api/roots/anchor {day, tx_hash, chain}`. The feed `GET /api/roots` lists every chain's anchor per day. |
| Pages | `app/public/roots.html`, `app/public/call.html`, `app/public/js/anchor_cell.js` | A cell per chain, painted only from what that chain said (verified / recorded-not-confirmed / mismatch / unanchored). |
| The DogeOS app | `GET /doge` (`app/routes/dogeapp.js`, `app/public/js/doge-app.js`, `doge-proof.js`) | Sign in with a DogeOS wallet (the site's own wallet sign-in: a signed nonce, no transaction), play the Daily Duel (the website's own renderer), see the proof panel, and **anchor a day's root on the DogeOS testnet from your wallet**. |
| Hosted front ends | `app/lib/api_cors.js` | A page on another origin (the Noah AI app) may call the duel, roots, receipt and wallet sign-in routes, only from the origins in `API_CORS_ORIGINS`, never with cookies. |
| Tests | `app/test/roots_anchor_on_more_than_one_chain.test.js`, `the_doge_app_plays_the_duel_and_anchors_on_dogeos.test.js`, `anchor_cell.test.js`, `root_anchor.test.js`, `a_hosted_front_end_can_call_the_duel_api.test.js`, `wallet_sign_in_works_without_ethers.test.js` | The routes end to end with the chain stubbed at the RPC; the page; the proof block; the cells; the cross-origin door; sign-in with production's `ethers` stub. |

### How the anchoring works, step by step

1. A completed UTC day has a root: `GET /api/roots` answers `{construction, chains, roots: [{day, root, seal_count, anchors: [{chain, tx, block_time}]}]}` (every seal of the day, deduped, sorted, sha256 Merkle; the construction is spelled out in the feed).
2. `GET /api/roots/anchor-plan/2026-10-18?chain=dogeos-testnet` answers the exact transaction: `to` = your own address, `value` 0, `data` = hex of `RCROOT1:2026-10-18:<root>`.
3. Your wallet sends it on the DogeOS testnet (the app asks the wallet to switch to chain 6281971, adding it from the registry's description when the wallet has never seen it).
4. `POST /api/roots/anchor {day, tx_hash, chain: "dogeos-testnet"}`: the server reads the transaction through the DogeOS RPC, compares the calldata byte for byte with the payload, reads the block, and records the anchor with the block time. Not mined yet, or mined but fewer than 17 blocks deep, answers 503 (try again; 17 blocks is about a minute); wrong calldata answers 400; a second anchor on the same chain answers 409.
5. `GET /api/roots/verify/2026-10-18?chain=dogeos-testnet` re-checks live, for anyone, any time, and adds `final`: `true` once DogeOS's `finalized` block has passed the anchor (about 2.5 hours on the testnet), `false` before, absent when the chain could not say. The cell on `/roots`, the receipt on `/call/<id>` and the proof panel on `/doge` are painted from that answer, never from our column.

### What a receipt carries

`GET /api/call/<seal>` (and `round:`, `duel:`, `settle:` for the duel) answers the seal and an `anchor` block: the day, the root, the Merkle proof to it, and `anchors: [{chain, tx, block_time}]` for every chain the day is anchored on. The verify page replays the proof in the browser.

## Operator steps (you)

1. **Wallet.** Any EVM wallet (MetaMask, Rabby) with an account funded from the DogeOS testnet faucet (`https://faucet.testnet.dogeos.com`). The app adds the network to the wallet itself. Never paste a key anywhere.
2. **Deploy** this repository's `main` to the website, as usual. No new secret is needed; `DOGEOS_TESTNET_RPC_URLS` is optional. Deploying also mends wallet sign-in: until 21 October it answered every signature "Signature verification failed" in production.
3. **Anchor.** Open `/doge`, sign in with the wallet, open the Proof tab, press *Anchor on DogeOS testnet* on each completed day. Each is one zero-value transaction.
4. **Noah AI.** Sign in at trynoah.ai, connect with DogeOS, and build the submission app from the prompt pack below. The rules require the entry to be built with Noah AI, so the Noah app is the product the judges use; `/doge` is the reference it is checked against.
5. **Open the door for it, on day one.** Once Noah shows the app's address, put its origin in `~/runeclaw-persist/web.env` (a website setting, never the bot's `.env`) as `API_CORS_ORIGINS=https://<the app's host>` and publish `app/` again. Then, in the Noah app, sign in and load the day's rounds: if the browser console says "blocked by CORS policy", the origin in `web.env` does not match the app's address exactly.
6. **Submit by 5 November (UTC):** project name, description, the DogeOS Integration paragraph above, the Noah AI project, screenshots (the duel card, a receipt with the DogeOS anchor, the explorer page of an anchor transaction), the demo.

## Prompt pack for Noah AI

Paste, in order. Replace `https://www.humanoid-traders.com` if the deployment moves. Every call is made with `fetch` and no cookies.

> **1. Purpose.** Build a DogeOS web app called "RUNECLAW Daily Duel". The user connects a DogeOS wallet and plays a daily prediction duel against an AI trading agent, "the Claw". Every call is sealed with a hash before the market moves, and each day's seals are anchored on the DogeOS testnet by the players' wallets. The app is a front end over an existing public API at `https://www.humanoid-traders.com`; it holds no keys and takes no payments.
>
> **2. Sign-in.** On "Connect", request accounts with `eth_requestAccounts`, then read `GET /api/roots/chains`, find the entry in `chains` with key `dogeos-testnet`, and ask the wallet to switch to its `chain_id` (`wallet_switchEthereumChain` with the id in hex; on error 4902 call `wallet_addEthereumChain` with `chainName` = `name`, `nativeCurrency` = `native`, `rpcUrls` = `rpcs`, `blockExplorerUrls` = the origin of `explorer_tx`). Then `POST /api/auth/wallet/nonce {address}` answers `{message}`; sign that exact text with `personal_sign`; `POST /api/auth/wallet/verify {address, signature}` answers `{token}`. Send `Authorization: Bearer <token>` on every duel call after that. The signature is not a transaction and costs no gas. Show the server's `error` text when it refuses.
>
> **3. The duel.** `GET /api/duel/today` answers `{day, horizon_hours, open, rounds}`. Each round has `id`, `kind` (`direction`, `backfade`, `range`, `mover` or `sports`), `symbol`, `options` (the picks it takes, lowercase keys), `callable` (true while it can be called), and `seal` (the hash it was dealt under, shown on the card before anyone calls). A `mover` round adds `symbol_b`, a `range` round `threshold_pct`, a `sports` round `match {home, away, competition, kickoff}`. A `backfade` round carries `agent_direction` from the start (the Claw's call, which the player backs or fades); on a `direction` round it appears only after the player has called. Label the picks: long "Up", short "Down", back "Back the Claw", fade "Fade the Claw", break "Breaks out", hold "Stays in range", first and second the two symbols, home and away the two teams, draw "Draw", pass "Pass". The question per kind: direction "Will <symbol> be higher or lower in <horizon_hours> hours?"; backfade "The Claw calls <symbol> <agent_direction>. Back it or fade it?"; range "Will <symbol> move more than <threshold_pct>% either way?"; mover "Which moves more: <symbol> or <symbol_b>?"; sports "<home> v <away>, <competition>". A round the player has called carries `my_call {pick, entry_price, resolves_at, seal, outcome}` and `my_result {state, marks, beat_agent}`. `outcome` is null until the round resolves; beside it `pending: true` means it is not due yet, and `unresolved: true` means it is due but its settle price could not be read, which is never a loss. `POST /api/duel/pick {round_id, pick}` records a call and answers `{ok, round_id, kind, symbol, pick, entry_price, resolves_at, seal}`, plus `agent_direction` on a direction or backfade round. `agent_direction` is `long`, `short`, `flat`, or null when the Claw held no view; null is a real answer, never a direction. `GET /api/duel/me` answers the player's record: `accuracy`, `marks`, `streak`, `history`. Counts and percent only: there is no stake and no currency anywhere in this game.
>
> **4. The proof.** `GET /api/roots` answers `roots`, one per completed day, each with `day`, `root`, `seal_count` and `anchors: [{chain, tx, block_time}]`. For each day without a `dogeos-testnet` anchor, show "Anchor on DogeOS": call `GET /api/roots/anchor-plan/<day>?chain=dogeos-testnet`, send `eth_sendTransaction` with `from` = `to` = the connected address, `value: "0x0"`, `data` = the plan's `data`; then `POST /api/roots/anchor {day, tx_hash, chain: "dogeos-testnet"}`. While it answers 503, say "Waiting for DogeOS to confirm it 17 blocks deep" and retry every 12 seconds, at most 10 times. Show `GET /api/roots/verify/<day>?chain=dogeos-testnet` live: green only when `status` is `verified`, with "final" beside it when `final` is true; "recorded, not confirmed" otherwise; red on `mismatch`. Link the answer's `explorer`.
>
> **5. Receipts.** Every call's `seal` opens `https://www.humanoid-traders.com/call/<seal>`, where the Merkle proof is replayed in the browser and the day's DogeOS anchor is shown. Link it from each call.
>
> **6. Rules of the surface.** No dollar amounts, no prices presented as advice, no token or sale content. Percent, counts and the market facts the API returns only. When a call fails, say it could not be read; never show a failed read as "no rounds" or as 0%.

## The demo, in three minutes

1. Connect the wallet; the app switches it to the DogeOS testnet and signs you in.
2. Call a round. Show the round seal that was on the card before the call, and the agent's stance revealed after.
3. Open the Proof tab: a day anchored on DogeOS, the explorer page of the transaction with `RCROOT1:<day>:<root>` in the calldata, and a receipt whose Merkle proof replays in the browser to that root.
4. Press Anchor on an unanchored day and watch the cell go from "waiting for 17 blocks" to "verified on DogeOS testnet" about a minute later.

## Judging criteria, answered

- **Functionality.** The duel, the sign-in and the anchoring are live code with end-to-end tests; the chain is read, never trusted from our own database, and an anchor counts only past DogeOS's documented reorg depth.
- **DogeOS integration.** Wallet sign-in on the DogeOS testnet; every day's root anchored on DogeOS by players' wallets; receipts that link to the DogeOS explorers; finality read from DogeOS itself.
- **Originality.** A prediction game where the house cannot quietly change its call: the agent's stance is committed before the player calls, and DogeOS timestamps the day.
- **User experience.** One page, three tabs, every refusal named, every absence said in words.
- **Potential.** The same anchoring serves every surface RUNECLAW seals: next, an Agent Scorecard on DogeOS (the agent's own sealed calls and its hit rate, anchored day by day), then settling the day's roots to Dogecoin L1 through DogeOS.

## Not in this entry, on purpose

- No stake, wager or currency in the duel (the house rule, and the category).
- No custody: the server never signs. Anchoring is the player's transaction.
- No presale or token content anywhere on `/doge` or in the Noah app.
- Draco and USDZ cuts of the mascot are not served; the owl on the site is the 660 KB glTF.
