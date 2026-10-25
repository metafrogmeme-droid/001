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

## Tokenized-equity bucket

Bitget's R-prefix tokenized equities (RAAPL, RTSLA, RNVDA, RFSLY, and the
same shape of replica) stay in the scanner. `EQUITY_BUCKET_ENABLED` caps
them and keeps their closes out of the crypto book. The flag defaults off.
A live mainnet process ignores it, including one armed with `/golive` after
boot. On a venue testnet or a paper process it applies only when the flag
is set. The reading is `bot/core/equity_bucket.py`.

### Which names

The Bitget instrument row is the preferred reading: `symbolType` `stock`,
`isRwa` `YES`, or `isReality` `yes`, and the base still has to be the
R-prefix family (`R` plus a ticker). `symbolType` `crypto` is crypto. A
base on the crypto refusal list is never an equity, including when a row
is mis-tagged. That list includes RUNE, RENDER, RSR and RVN.

When the row does not carry those fields, the name is an equity only if
it has that shape and the ticker after the leading `R` is on the
documented underlying list (the replica names in `US_STOCK_SYMBOLS`, plus
FSLY). A leading `R` by itself is not enough.

### Ceilings

While the bucket is on:

| knob | default | what it bounds |
|---|---|---|
| `EQUITY_MAX_OPEN_POSITIONS` | 2 | open equity positions |
| `EQUITY_MAX_RISK_SHARE` | 0.20 | equity open risk as a share of total open risk |
| `EQUITY_RISK_MULTIPLE` | 0.5 | per-trade equity risk versus the crypto size |

The share is of risk that is already open. One equity in an otherwise flat
book would be the whole of open risk, so the ceiling admits none until
another position has risk on record. The performance budget below can only
lower this share. None of these raise a size, and none of them change
`BID_DOMINANCE` or `OF_DOMINANCE_RATIO`.

Crypto keeps priority on `max_open_positions`. In one confirm batch, crypto
ideas are ordered first. An equity is refused when crypto already holds
every slot, or the book is already full. The existing max-open check is
unchanged and still counts every position.

### What an equity close does not teach the crypto book

It does not enter confidence calibration, voter-weight learning, or setup
expectancy. It does not move the streak breaker, the live-performance
window, or a per-symbol cooldown. The bucket keeps its own close record
and uses that for the budget below.

The account daily-loss limit still counts the close. On a live book that
is the daily P&L accumulator. On a paper book it is the portfolio's own
daily P&L, which is recorded before the streak callback.

### Adaptive, under those ceilings

Each cut is deterministic and recorded as a reason code on the risk check
(`equity_reasons` in the risk audit, and `action=equity_bucket` in the
trade log). A trade-log open and close for one of these names carries
`asset_class=equity`.

* Same-direction equity positions are one cluster. Size shrinks when the
  new trade joins that cluster, and when rolling-return correlation with
  an open name is at or above `EQUITY_CORR_THRESHOLD` (default 0.50). With
  no return history the bucket assumes the cluster is correlated
  (`EQUITY_ASSUMED_CORR_MULT`, default 0.5) rather than treating a missing
  series as uncorrelated.
* Size scales by the equity's own realized vol (ATR over price) against
  `EQUITY_VOL_TARGET` (default 0.03). It never scales up. An unreadable vol
  uses a 0.5 cut.
* The risk share starts at `EQUITY_BUDGET_FLOOR` (default 0.05) until
  `EQUITY_BUDGET_MIN_CLOSES` (default 8) equity closes are on record. Over
  the last `EQUITY_BUDGET_WINDOW` closes (default 20), a profit factor
  below 1 or a drawdown above `EQUITY_BUDGET_DD` (default 0.10) returns
  the share to the floor. A healthy window steps it up by
  `EQUITY_BUDGET_STEP` (default 0.02) and no further, and never above the
  hard share.
* Notional is capped at `EQUITY_DEPTH_FRACTION` (default 0.10) of the
  measured top-of-book depth on the side the entry takes. An unread book
  refuses the trade.
* A closed US reference session scales size by `EQUITY_SESSION_CLOSED_MULT`
  (default 0.25). Setting that to 0 skips the entry instead. An unreadable
  session clock does not pretend the market is closed.

### Revert the bucket

Unset `EQUITY_BUCKET_ENABLED`, or set it to 0, and restart. Caps, ordering,
and the learning wall go back to the crypto book. Live mainnet was never
on this path.
