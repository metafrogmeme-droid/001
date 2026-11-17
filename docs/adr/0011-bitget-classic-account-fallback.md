# ADR 0011: Bitget Classic-account fallback

- **Status:** Accepted
- **Date:** 2026-11-16
- **Plan items:** none (operator request, 2026-11-16)
- **Informed by:** the operator's report of 2026-11-16 (Bitget code 40084 on the balance read, HTTP 400 on the v3 position read, and code 00000 from `GET /api/v2/mix/account/accounts?productType=USDT-FUTURES` with the same key); ccxt 4.5.56 driven on 2026-11-16 with the transport replaced, no venue reached.

## Context

Every Bitget client was built with `options["uta"] = True`
(`bot/core/venues.py` `BitgetVenue.create_exchange`, `_bitget_balance_probe`
and `balance_snapshot` in `bot/core/exchange_credentials.py`, and
`bot/skills/scan_skill.py` `_fetch_live_exchange_data`). An account in Classic mode refuses each unified
call with code 40084. The executor had classic branches and probed the
account type into `LiveExecutor._is_uta`, but that reading never reached the
client's option, which is what routes every ccxt call.

On such an account the startup credential check (`bot/main.py`
`_credential_preflight`) reported the refusal as a rejected key and latched
live auth down, the position sync failed on every pass, and the reconcile
sweep's read raised at DEBUG, so a row the venue no longer held stayed in the
book.

## Options

- **(a)** Probe the account mode before the first call of every client.
- **(b)** Switch on the venue's own 40084, remembered per account in memory.
- **(c)** A configuration flag naming the account's mode.

## Recommended default

(b). The trigger is the venue's statement, read from the reply's `code` field
(`bot/core/order_state.py` `venue_code`), so an account that never answers
40084 is asked exactly what it was asked before. (a) adds a call to every
unified account. (c) is a second reading of a fact the venue already states.

## Consequences

- `bot/core/bitget_account_mode.py` is the one place 40084 is recognised and
  remembered. The cache is keyed by a sha256 of the refused client's API key,
  holds no key, is not persisted, and costs one refused call per account per
  process start. No new environment flag.
- `LiveExecutor._switch_to_classic` flips the client in hand, records
  `_is_uta = False`, audits `account_mode`/`CLASSIC` once and reloads a market
  set that was loaded as unified. It is reached from the balance read and
  from the reconcile sweep, whichever meets the refusal first.
- `LiveExecutor.sync_positions_from_exchange` reads the classic
  `all-position` rows through the ccxt client for an account the venue has
  said is Classic, and the v3 channel for every other account.
- The credential checks test for an account-mode refusal first
  (`bot/main.py` `_is_account_mode_refusal`). It marks nothing down and
  nothing up: 40084 names the account's mode and does not judge the key. The
  classic read that follows does, so a real rejection still halts, and so
  does an unrecognised error.
- A row the venue reports flat is cleared by the existing
  `LiveExecutor.reconcile_positions` path, with a `reconcile_flat` audit line
  first. No order is sent on any branch of it. No new removal routine was
  added, and the sync still removes nothing.
- Order placement is not edited. Orders, cancels and leverage sets follow the
  client's option.

Known limits:

- An account upgraded from Classic to unified while the bot runs needs a
  restart: the cache only moves one way.
- A reconciled close with no row in the venue's history keeps the existing
  fallback after ten passes (the ticker, or unpriced when the ticker is
  unread). Changing that would change unified accounts too.

Two limits recorded here on 2026-11-16 were lifted by the follow-up below:
the flash close was a v3-only channel, and each executor's client switched
only on its own first refusal.

## Decision

Decision: option (b), approved by the owner on 2026-11-16.

## Follow-up, 2026-11-17

Approved by the owner on 2026-11-17, after the first production boot of the
change above. Option (b) stands in what decides: only the venue's 40084
switches a client. What changed is when it is asked.

- **One family read at first use.** With no read up front, whichever call
  came first met the refusal, and a position sync that ran before the
  executor's own client had heard it audited a failed sync.
  `LiveExecutor._get_exchange` now starts one time-limited read when it
  builds a Bitget client (`LiveExecutor._learn_account_family`, the read the
  balance already makes), and every caller waits for it. A timeout or any
  other error leaves the family unknown and records nothing. A unified
  account pays one more read per executor per process start.
- **The client agrees with the cache.** The mode is remembered per account,
  and a client was switched only by its own refusal. Another reader with the
  same key (the scan payload builds one every tick) could mark the account
  while the executor's client still routed to the unified API.
  `_get_exchange` now switches such a client at every hand-out, and a 40084
  inside `LiveExecutor._classic_position_rows` switches and asks once more
  instead of failing the sync.
- **A timeout is not a rejected key.** The driver's text for a timeout or a
  lost connection is only `bitget GET <url>`, so the startup check reported a
  timed-out balance as "exchange auth FAILED". `LiveExecutor.fetch_balance`
  now names the class of a network-class error in front of its text, and
  `bot/main.py` `_TRANSPORT_MARKERS` knows those names. A clock or signature
  error (`InvalidNonce`) is excluded and still halts, as does an
  unrecognised error.
- **The flash close has a classic path.** On an account the venue has said
  is Classic, `LiveExecutor._flash_close_position` posts the same close to
  `/api/v2/mix/order/close-positions`, with the symbol and the side always
  named. A unified account is asked what it was asked before.
  `bot/core/order_state.py` `classic_flash_close_reply` spells the classic
  reply for `flash_close_applied`, which stays the one reader of "applied".
- **The readiness assessment left the event loop.** Not an account-mode
  change, but found on the same boot, which hung for about 78 seconds twice.
  `ProactiveMonitor._check_learning_readiness`
  (`bot/core/proactive_monitor.py`) ran `assess_readiness` inline on the
  monitor's first pass, right after Telegram starts, and hourly after. It now
  runs on a worker thread, and so does `/readiness`. Measured on 2026-11-17
  in the build container on a synthetic decision log: about 0.14 s per 1,000
  rows, linear, 8.5 s at 60,000 rows. Whether the production log is large
  enough to account for the whole freeze was not measured.

Not verified against the venue (no Bitget call was made):

- Whether a one-way Classic account accepts `holdSide` on the classic flash
  close. If it refuses, the flash close fails as it did before; it cannot
  close the other side.
- Which refusal codes the classic API answers where the unified one answers
  25231, 25610 and 25227. The flash close's callers fire on those codes, so
  the classic path may rarely be reached.

Found on the same boot and not changed: `website_scan`, `/scan` and the deep
scan build a synchronous client and load markets on the event loop, and every
booked close re-verifies the whole audit chain twice on it.
