# Memeclaw: a memecoin launchpad on the DogeOS testnet

**Status:** being built on branch `ccr-fe24fce0-wgj268`, 21 October 2026.
**Owner's decisions (21 Oct):** 1 and 2 of the three put to them.

1. The "never launches tokens" rule is lifted **for a user launchpad only**.
   The agent still never launches, buys or sells a coin, on any chain: no
   bot code path reaches Memeclaw, and a test holds that.
2. **Testnet only**, on its own page (`/memeclaw`), with **no $RCLAW** and
   **no tokenized stocks** in this version.

Later the same day the owner asked for three more: "launch tokens on it and
all just testnet and paper for now", and "we make seperate website for it".
Asked how, they chose:

3. **A no-wallet practice mode** beside the testnet, on the same curve
   (below).
4. **Its own domain, served by this same server** (`MEMECLAW_HOSTS`, below).
5. **The first coins launched by the owner, from their own wallet**: a
   one-press starter-coins flow in the operator's section. Nothing on the
   server holds a key to do it for them.

Not decided, and not built: **mainnet**. The recommendation stands that a
mainnet launchpad comes only after an independent contract audit and a legal
check. The contract enforces the testnet: its constructor refuses every chain
but the DogeOS testnet (chain id read from `lib/root_anchor.js`), so a mainnet
deployment needs a different contract, not a setting.

## What a person can do

Two modes, switched at the top of the page; practice is the first.

- **Practice** needs no wallet and no account. A browser starts a practice
  player and gets 100 practice DOGE; it can launch practice coins (five a
  day) and buy and sell any practice coin. A player with less than 1
  practice DOGE can top back up to 100 once a day.
- **Testnet** uses the person's own wallet on the DogeOS testnet:

- **Launch a coin** from their own wallet: name (32 printable ASCII
  characters at most), symbol (A–Z, 0–9, 10 at most), an optional short
  description (no links). Optionally buy first, in the same transaction.
- **Buy and sell** any Memeclaw coin on its bonding curve, in testnet DOGE.
- **See every coin** the factory has launched, its price, how much of the
  supply has sold, its trades, and a reading of the launch (below).
- **Check a launch's receipt** on the page itself, in the browser: the terms
  the page sealed, the hash the chain recorded at launch, and the day's
  Merkle proof (the Pi app's receipt check, `js/pi-receipt.js`, on the
  `launch:` key).

No account is needed. The wallet that sends the launch is the creator; the
chain says so, and the server only records what the chain says.

## The contract (`contracts/memeclaw/`)

- `MemeclawFactory` creates each coin (`MemeclawToken`, a plain ERC-20 with
  no owner and no mint after its constructor) with a fixed supply of
  1,000,000,000, all held by the factory's curve.
- **The curve:** constant product over virtual reserves (10 DOGE and
  250,000,000 coins), no fee. Buying out the whole supply costs 40 testnet
  DOGE. Rounding favours the curve on both sides, so every sale is paid from
  DOGE that coin's curve received. Once sold out, buys stop and sells work.
- **No owner, no fee, no pause, no withdraw, no upgrade, no pool, no
  graduation.** Nobody, the operator included, can take DOGE out of a curve
  except by selling coins into it.
- `launch()` takes a `termsHash`: the SHA-256 of the terms the page sealed.
  The chain records it in the `Launched` event, so the chain itself says
  which terms a coin launched under, at the block it launched.
- Tests (`npm test`): the real bytecode on an in-process EVM, including
  random trading by three wallets followed by everyone selling out, a
  re-entering seller, every refusal with its other arm, and a check that the
  website's quotes equal what the contract pays.

## Practice mode (`lib/memeclaw_paper.js`)

- **The same curve.** A practice coin is priced by `lib/memeclaw_curve.js`,
  the function the contract tests compare trade by trade with the real
  bytecode. Practice and testnet differ in where the DOGE is, not in how a
  price moves.
- **Every practice DOGE is accounted for.** A trade moves DOGE and coins
  between a player and a coin's curve in one database transaction, rows
  locked coin, then player, then holding; a trade below its `min_out` is
  refused, not filled. A test replays random trading and checks that every
  player's DOGE plus every curve's DOGE adds up to what was handed out.
- **The key.** A practice player is a random 32-byte key this browser keeps
  in local storage and sends as `x-memeclaw-practice`. The server keeps its
  SHA-256 only. "Forget my practice player" deletes the hash, the balance and
  the holdings; coins and trades stay under the player's random id.
- **The same names.** A practice launch is held to the page's rules for a
  testnet launch (`pageProblem`): no project names, stocks, majors, money
  words or links.
- **No chain, no seal.** Practice coins are never on a chain and are not
  sealed into the daily roots.

## Its own domain (`lib/memeclaw_host.js`)

- `MEMECLAW_HOSTS` names the hosts that are Memeclaw. On one of them, `/` is
  the launchpad and only Memeclaw's own paths answer: its API, its scripts
  and stylesheet, a `launch:` receipt, the two policies, health. Everything
  else is a 404, so the presale bar, the token page and sign-in are not one
  typed path away; no write outside `/api/memeclaw` is accepted.
- `MEMECLAW_PUBLIC_URL` is the address its link preview names.
- Unset, nothing changes, and the operator's tools (deploy, starter coins,
  hide) stay on the main site's `/memeclaw`, where the operator is signed in.
  Nothing on the main site links to the launchpad.

## The website

| piece | what |
|---|---|
| `GET /memeclaw` | the page, its own scripts, no presale, no `/token` link |
| `GET /api/memeclaw/config` | chain (from the registry), factory, curve constants; off until the factory is set and checked |
| `POST /api/memeclaw/launch-plan` | validates terms, seals them into a terms hash, returns the transaction for the wallet |
| `POST /api/memeclaw/coins` | the launch's tx hash: the server reads the receipt and records the coin only if the chain agrees |
| `GET /api/memeclaw/coins`, `/coins/:address` | the list and one coin with its trades |
| `POST /api/memeclaw/trade-plan` | buy, sell or approve: a live quote from the contract and the transaction for the wallet |
| `POST /api/memeclaw/coins/:address/hide` | operator only: take a coin off the page |
| `GET /api/memeclaw/deploy-plan` | operator only: the factory's creation code, and the starter coins |
| `/api/memeclaw/paper/*` | practice mode: a player, its balance, launch, quote, trade, coins, forget |
| indexer (`lib/memeclaw_indexer.js`) | reads the factory's events from the chain on the shared schedule |

- **Non-custodial:** the server never signs or sends. It hands a wallet a
  transaction and reads the chain afterwards.
- **The factory address** comes from `MEMECLAW_FACTORY`. The server checks
  that the code at that address is the committed artifact's runtime code,
  byte for byte, before the page points anyone at it. The operator deploys
  it from their own wallet.
- **A coin launched straight at the contract**, not through the page, is
  listed with "terms not recorded here". A name or symbol the page would
  refuse is listed hidden.
- **Refused names:** our own names (RUNECLAW, RCLAW, Humanoid Traders,
  Memeclaw), the majors the bot trades, and every stock, ETF and stock-perp
  symbol in `bot/config.py`, read from there by a generator, not typed here.
  Words that present a coin as a stock (stock, share, equity, tokenized, ETF)
  are refused too.
- **The proof:** each recorded launch is sealed (v8, `memeclaw_launch`) into
  the daily root, with the terms hash the chain recorded. A receipt is checked
  on the coin's page; `/api/call/launch:<seal>` serves it.

## Not in this version

- Mainnet, $RCLAW, tokenized stocks.
- Images. A coin is drawn from its address; no upload, nothing to moderate.
- Languages other than English on `/memeclaw`.
- The agent: no bot command, MCP tool or LLM tool reads or trades Memeclaw.

## Found on the way

- The chain registry served any RPC URL set in the server's environment
  (`DOGEOS_TESTNET_RPC_URLS`, `WEB3_RPC_URL_BASE_ANCHOR`) to every visitor
  and wallet, though its own comment said no override leaks. A keyed URL
  would have been public. Pages now get the public endpoints only.
- The daily root skipped a seal table it could not read and stored the root
  without it for good. A table that does not exist is still skipped; any
  other failure leaves the day uncomputed, to be read again.
- Wallet sign-in calls `ethers.verifyMessage`, and the stub production
  resolves `ethers` to (as `app/test/ethers_stub_calldata.test.js`
  reproduces it) has no such function. If the real stub matches, wallet
  sign-in on `/doge` answers 401 in production. Not confirmed from here;
  Memeclaw does not depend on it.

## For the owner, before people use it

1. Deploy the factory on the DogeOS testnet from your own wallet (the
   operator section of `/memeclaw` on the main site, signed in), then set
   `MEMECLAW_FACTORY` and `MEMECLAW_FACTORY_BLOCK` to what the check prints
   and restart. Testnet mode stays off until the server has checked the code
   at that address; practice mode works before.
2. Launch the starter coins (CLAW, WOOF, PAW) from the same section: your
   wallet asks you to sign each one. A coin your wallet already launched is
   skipped.
3. For the domain: point its DNS and TLS at this server, then set
   `MEMECLAW_HOSTS` and `MEMECLAW_PUBLIC_URL` and restart.
4. Read the page's notice and the privacy policy's Memeclaw section. The
   Terms of Service are unchanged: whether they need a launchpad section is
   for the legal check before any mainnet decision.
5. A trademark check of the name "Memeclaw", before a domain is bought.
