# RWA pre-check

A read-only check of one tokenized-asset contract before a platform or a
person interacts with it. It reports flags. It does not issue, rate,
recommend or promote an asset, and it is not an audit and not investment
advice.

The same sentence is `DISCLAIMER` in `app/lib/rwa_words.js` and on `/rwa`.

## Flag

`RWA_PRECHECK_ENABLED` — off unless the value is `1` or `true`. Read on
every request. While it is off, `GET /rwa`, `GET /rwa.html`,
`GET /js/rwa-precheck.js` and `/v1/rwa/*` answer 404. The canonical is
`https://www.humanoid-traders.com/rwa`, and `app.get('/rwa')` is the route
that serves it, the same shape as the other pages. It is not in the sitemap
and not linked from the landing page: that link would open a 404 until the
flag is on.

## Chains

The list is `app/lib/rwa_chains.js`. One EVM reader (`app/lib/rwa_evm.js`)
serves all of them. `readerFor` in `app/lib/rwa_precheck.js` is the seam: a
family with no reader answers `no_reader` and is not run through the EVM
reader.

| id | chain id | RPC variable |
|---|---|---|
| ethereum | 1 | `RWA_RPC_URL_ETHEREUM` |
| base | 8453 | `RWA_RPC_URL_BASE` |
| avalanche | 43114 | `RWA_RPC_URL_AVALANCHE` |
| bnb | 56 | `RWA_RPC_URL_BNB` |
| arbitrum | 42161 | `RWA_RPC_URL_ARBITRUM` |
| polygon | 137 | `RWA_RPC_URL_POLYGON` |
| zksync | 324 | `RWA_RPC_URL_ZKSYNC` |

A variable may hold several URLs separated by commas. The next URL is asked
only when the read itself fails. A revert is an answer and stops the walk.
URLs are never written into the repo and never returned by the API.
`rpc_configured` is a boolean.

Not in v0:

- **Solana** and **Stellar** need their own readers. They are not rows in the
  chain list. v0.1 adds a reader under `READERS` without a second stack.
- **Monad** is not a row. It was not shown to be the same reader plus a
  recorded fixture, so it was left out rather than guessed.

ZKsync Era uses the same reader. Its bytecode may not contain EVM `PUSH4`
opcodes; view calls are still made, and a selector that is not seen is
reported as not observed, not as absent. On 23 October 2026 the official
T-REX factory `0x7F1fD450a5c4d91286f00F3297dCD5C12767f17f` had no code on BNB
Chain or ZKsync Era. Those chains stay in the list because the reader is the
same. A fixture is recorded only where a real contract was read.

## Recorded contracts

`app/test/fixtures/rwa/` replays the JSON-RPC answers from 23 October 2026.
The tests do not open a network connection. `heartbeat()` reverted on every
feed below, so `stale` is null and the age is the block time minus
`updatedAt`. The feed rows are the feed contracts themselves: the token that
the feed describes does not name it through the getters this reader checks.

| chain | contract | what was read |
|---|---|---|
| ethereum | MS PoC Demo Token `0x887c7cb4747039d26fff92156f33a26e4b306e6b` | ERC-3643, version 4.1.3, paused. `forcedTransfer` is on the implementation behind `getImplementationAuthority`, and it was observed. |
| ethereum | WBTC PoR feed `0xa81fe04086865e63e12dd3776978e49deea2ea4e` | proof-of-reserve, source self |
| ethereum | JTRSY NAV feed `0x0c2e4df738e99e8db80012f5bb2a303f3f48ca74` | NAV, source self |
| base | CSM Demo `0xf2299dcd9c189e0d7e47c55ee7c7b215ecd37c9d` | ERC-3643, version 4.1.3, paused |
| base | Coinbase AAPL feed `0x787f13dea48db0897cbcdd985de77809d837f988` | description "Coinbase AAPL" classifies as other, not proof-of-reserve or NAV |
| base | AAPLc `0xb200000000000000000000c2e324d24d7eecd1fb` | code is the `0xef` marker. View calls still run. Selectors are not reported as absent. |
| avalanche | AV4T2 `0x0fbe4ef1f715815366189a747cccc84f317c9b45` | ERC-3643, version 4.1.3, paused |
| avalanche | LINK.e PoR feed `0x943cef1b112ca9fd7edadc9a46477d3812a382b6` | proof-of-reserve, source self |
| arbitrum | token besync transfer `0x6de1f50724489ba80b26bc8f8b29d3ae4ae47059` | ERC-3643, version 4.1.3, paused |
| arbitrum | FBTC PoR feed `0xfb010c715ff2b85db9c896f0c030bf9329638905` | proof-of-reserve, source self |
| polygon | ENEGRA `0xa8c557c7ac1626eacaa0e80fac7b6997346306e8` | ERC-3643, version 4.1.3, not paused |
| polygon | bNVDA reserves feed `0x0fb2bed999da86cb1fdd97e746600a96141eea09` | proof-of-reserve, source self |

BNB Chain and ZKsync Era have no fixture. Monad is not a chain row. Solana
and Stellar are not rows either.

## What a check reads

`POST /v1/rwa/precheck` with `{ "chain": "base", "asset": "0x…", "account": "0x…" }`.
`account` is optional. `GET /v1/rwa/chains` lists the chains.

1. **ERC-3643 / T-REX.** `identityRegistry()`, `compliance()`, `paused()`,
   and, when a registry is set, `isVerified` for the account that was named.
   A revert of `isVerified` is the unreadable id `registry_isVerified`, not "unverified". A revert of
   `identityRegistry` is not "this is outside ERC-3643".
2. **Oracle freshness.** Getters on the asset (`oracle`, `porFeed`,
   `navFeed`, and the others named in `app/lib/rwa_evm.js`), or the asset
   itself when it implements `latestRoundData`. The answer is discarded.
   Age is the block time minus `updatedAt`. `updatedAt` of zero is "no
   update", not age zero. An `updatedAt` after the block is clock skew, and
   age is null. `stale` is true only when age and heartbeat are both finite
   numbers and age is greater. A reverted `heartbeat()` leaves `stale` null.
   Addresses embedded with `PUSH20` count only when their description
   classifies as proof-of-reserve or NAV.
3. **Admin powers.** Owner, ERC-1967 implementation slot (preferred over
   `implementation()`, which reverts on some proxies whose slot is set),
   and, where that slot is empty, a T-REX implementation authority
   (`getImplementationAuthority` then `getTokenImplementation`). Forced
   transfer, freeze, pause, mint or burn, and agent appointment are read
   from that code. `setImplementationAuthority` counts as an upgrade path.
   "Not observed in the bytecode" is not proof the function is absent.
   Code `0x` is an empty account. Code `0xef` has no EVM body; view calls
   still run and selectors are not reported as absent.

An investor check runs only when `account` is an address. `canTransfer` is
not called: a refusal can mean the balance rather than a rule.

Colour is a claim. Flags are `caution`, `neutral` or `muted`. There is no
tone that means the asset is sound.

## Receipt

A signed-in caller gets a v11 `rwa_precheck` receipt in `scan_seals`, opened
at `/call/sc_…`. The payload is specified in `docs/PROVABLE_CALLS_SPEC.md`
§2.11. It stores flag id and state, the unreadable ids, the chain id and the
block. It does not store the address. An anonymous check stores nothing.
A failed seal does not fail the check.

## Honesty

Unreadable is not zero, and a missing heartbeat is not "fresh". Empty
`eth_call` data is not a decoded zero. The whole check fails (`read: false`,
reason `unreadable`) when the block itself cannot be read, so a missing
block is not a list of "not paused" flags.
