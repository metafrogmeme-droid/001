# Claw Watch, Hyperliquid account

`/hlwatch` watches a Hyperliquid account through the public info API and
sends an advisory Telegram note. It looks for a new agent or API wallet, a
builder-fee approval, a withdrawal or a transfer whose destination is not
this account or one of its sub-accounts, and a sudden position or margin
change. The note does not stop any of those. It does not revoke anything.

The flag is `CLAW_WATCH_HL_ENABLED`. Only `1` and `true` turn it on. Unset
is off. While it is off, `/hlwatch` answers "Not found." to anyone who is
not an admin, and an admin can preview it. The info host is the Hyperliquid
testnet unless `CLAW_WATCH_HL_MAINNET_INFO` is `1` or `true`. That switch is
still read-only.

An address is stored under an HMAC of the Telegram id. The id is not a
column. The salt is `CLAW_WATCH_HL_ID_SALT` (at least 16 characters).
`/hlwatch stop` deletes that chat's rows, and so does `/stop` on the
anonymous Daily Duel bot. `/hlwatch now` reads once. The background poll
runs only while the flag is on, and only for chats that have used the
command since this process started.

## Claw Watch

Claw Watch (`DRAIN_WATCH_ENABLED`, `/clawwatch`) is its own package. This
watch does not edit it. `bot.claw_watch_hl.hook.read_claw_watch` includes
the addresses that chat stored there. A failed import leaves them out. A
read that does not succeed leaves them unread, which is not an empty list.

## What this is not

This package does not hold a key and does not sign. It does not ask for a
seed phrase or a private key. A missing read is left out. It is not reported
as zero, and it is not a verdict.

## Open

- The poll is HTTP, not the WebSocket user streams. Subscription names and
  rate-limit weights still need a check before that change.
- A fill on a thin market by a new agent is not called out on its own.
- The watch does not prove the person controls the address. It stores an
  address they sent.
- A baseline with many API wallets is counted, and individual rows stop
  after 12.
- After a restart the background poll has no chat to send to until the next
  `/hlwatch`. The rows are still stored.
- `/hlwatch now` saves the new snapshot before Telegram accepts the note. A
  failed send on that path is not retried. The background poll saves only
  after the send.
