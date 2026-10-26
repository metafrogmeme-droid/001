# Claw Watch

Claw Watch watches addresses a person registers in Telegram and sends an
advisory note when something on those addresses changes. The note describes
the action and the public signals that could be read. It does not stop a
drain. If indexing lags, or the bot is down, there is no alert.

The flag is `DRAIN_WATCH_ENABLED`. Unset is off. While it is off, the
command answers "Not found." to anyone who is not an admin, and the page
answers 404 to anyone who is not an operator. An admin can preview both.

## What a person can do

`/clawwatch` followed by an address stores that address under an HMAC of
the Telegram id. The id itself is not a column. `/clawwatch stop` deletes
every row for that id, and so does `/stop` on the Daily Duel bot.
`/clawwatch` with no argument shows what is stored. `/clawwatch now` reads
once.

The page is [https://www.humanoid-traders.com/claw-watch](https://www.humanoid-traders.com/claw-watch).
A check there decodes calldata in the browser's request and returns the
same kind of signals. It does not store the address.

## Revokes

A revoke in an alert or on the page is a plain unsigned transaction: the
`to`, the `data`, the `value`, and the chain id. A 7702 clear is an
authorization to the zero address, with the nonce and the signature left
for the wallet. The person can sign and submit that transaction from any
wallet if this site is offline. The server does not store a pre-signed
transaction and does not relay one.

A cancel link applies once an on-chain module exists. None is installed,
so the cancel answer is that there is no transaction.

## What this is not

Claw Watch does not hold a key and does not sign. It does not ask for a
seed phrase, a private key, or for anyone to run code. It does not decide
that an address is safe, and a missing read is unread, not a clear result.
