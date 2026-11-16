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

- `LiveExecutor._flash_close_position` is a v3-only channel. A Classic
  account is refused there and the caller falls back as it does today.
- An account upgraded from Classic to unified while the bot runs needs a
  restart: the cache only moves one way.
- A reconciled close with no row in the venue's history keeps the existing
  fallback after ten passes (the ticker, or unpriced when the ticker is
  unread). Changing that would change unified accounts too.
- A user's trading executor and view executor each switch on their own first
  refusal.

## Decision

Decision: option (b), approved by the owner on 2026-11-16.
