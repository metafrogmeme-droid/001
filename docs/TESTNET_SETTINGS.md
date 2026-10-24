# Testnet book settings

Internal review 2026-10-24. These settings apply on a venue testnet, and on a
paper process that opts in. A live mainnet process keeps the values it has
today: sizing leverage 5, no streak auto-reset, a 120 second wait after a
loss, and the hourly house book.

The reading is `bot/core/testnet_book.py`. The fields on `bot.config` are
unchanged. Uncommenting the block in `.env.example` does not by itself turn
the profile on for a live mainnet process.

## When the profile is on

- `BITGET_SANDBOX` or `HYPERLIQUID_TESTNET` is on, and the process is not live
  mainnet. A live flag on a venue testnet is still testnet.
- Or `TESTNET_BOOK_PROFILE=1` on a paper process (simulation on, live off,
  neither venue-testnet flag).

A paper process without that opt-in is unchanged. That is the process the
tests and a default install run.

Live mainnet is live trading enabled, simulation off, and neither venue
testnet flag. It is also a process `CONFIG.is_live()` says is live whose
exchange is not a venue testnet, including one armed with `/golive` after
boot. The profile does not apply there, even if `TESTNET_BOOK_PROFILE=1`.

## B1v, the primary book

On the profile the primary book is **B1v**: the daily long-only trend rule
(H1b). Lookbacks 20, 60 and 120 days, a 10% rebalance band, a 20% annual vol
target, a 60-day vol lookback, and a leverage cap of 2x.

The daily sleeve still does not place an order. What changes is the book the
engine records once a day (`data/learning/daily_trend_book.jsonl`): the vol
window is 60 days, and the row names `book`, `vol_window` and `leverage_cap`.
The pre-registered preset stays at its 30-day window. Each scan on the
profile writes a `primary_book` row to the scan log.

`PRIMARY_BOOK=hourly` while the profile is on keeps that 30-day sleeve as the
recorded book. The hourly overrides below stay on. `PRIMARY_BOOK=b1v` is the
same as leaving it unset. Any other spelling is not a selection.

## Hourly book

The hourly house scan still places the orders. On the profile:

| knob | profile | off the profile |
|---|---|---|
| `SIZING_LEVERAGE` | 2 | the configured value, 5 if unset |
| `STREAK_BREAKER_AUTORESET_HOURS` | 24 | the configured value, 0 if unset |
| `COOLDOWN_AFTER_LOSS_SEC` | A/B, below | the configured value, 120 if unset |

Sizing 2 is the leverage an order is sized at. The venue ceiling still binds,
so a venue standard below 2 keeps that lower number. The streak breaker
clears a streak trip 24 hours after the last loss, on testnet only, so an
unattended testnet bot does not stay paused. A live streak stays latched
until someone clears it, unless that deployment had already set the hours
itself: the profile does not change a live value, and it does not turn the
reset on for live.

## Cooldown trial

Not a proven edge. Half of symbols wait 14400 seconds after a loss. Half
keep the configured wait. The arm is the low bit of SHA-256 of the base
asset, so `BTC/USDT` and `BTCUSDT` are one symbol and the arm does not move
between restarts. A symbol that does not read is not assigned and keeps the
configured wait.

The arm is on the risk check (`arm=cooldown_14400` or `arm=control`), on the
risk audit, and as `action=cooldown_ab` in the trade log with the symbol,
the wait, the configured wait, and the primary book. The account-wide scan
pause stays at the configured wait. Putting the treatment wait on that pause
would halt the control symbols too.

## Revert

Set `TESTNET_BOOK_PROFILE=0` and restart. Sizing, the streak reset, the
cooldown and the primary book go back to the configured fields. On a venue
testnet the profile is on until that line is set; on paper it is off until
`TESTNET_BOOK_PROFILE=1`.

`PRIMARY_BOOK=hourly` reverts only the named book. It does not restore
sizing 5, the manual streak reset, or a single cooldown.
