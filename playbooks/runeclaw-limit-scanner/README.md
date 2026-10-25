# RUNECLAW Limit Entry Scanner

A live scanner for Bitget USDT-margined perpetual futures. On every scheduled
pass it runs a configurable universe through a regime gate, scores each coin,
and surfaces the single best pullback-limit setup as a signal. Long and short
are both in the live playbook. Default execution is `signal_only`: a signal
is not an order.

## Safe setup

This playbook runs inside Bitget's GetAgent runner, on the isolated trading
sub-account that runner supplies. That sub-account must be dedicated to this
playbook. Do not place manual trades there, and do not point another strategy
at it. All funds in the sub-account are available to the playbook, so fund it
with a small fixed balance you can lose (Bitget's own beta note says keep
exposure under 500 USDT) and leave it at that.

Before any order, the playbook calls `subaccount_exists()` and `total_value()`.
If either cannot be confirmed it emits a watch and places nothing. It records
the exchange order id of each order it places and later cancels, closes or
moves a stop only on those ids, and only on symbols named in the manifest. A
position it cannot match is left alone. Notional size is not ownership.

The contract SDK cannot send a post-only flag. A long limit is placed only
when it is at least one tick below the best bid, and a short only when it is
at least one tick above the best ask. The pullback is not slid onto the last
trade. A limit may move toward the market by at most the smaller of 0.25%
and half an ATR (`limit_chase_pct` can only lower that). A move that would
reach the best ask on a buy, or the best bid on a sell, is cancelled.
`stale_limit_pct` drops a resting order the market has run away from; it
does not reprice. If the book or the tick cannot be read, nothing is placed.
Margin is isolated when the opener accepts `margin_mode`.

The margin budget is 100 USDT. At 10x that is 1000 USDT notional, and a 2.5%
alt stop is 25 USDT, under the 30 USDT pause. The sizer does not stop there:
one trade's loss at the stop is strictly under `circuit_pause_usdt`, and the
open book (every concurrent stop) is inside the smaller of
`circuit_stop_usdt` and twice `max_loss_usdt`. Five stops at the per-trade
cap cannot exceed the circuit stop. The breaker reads account equity. A
Bitget equity field already includes open PnL. A wallet field does not, so
open PnL is added, and an unreadable open PnL refuses the pass. Ownership of
an order is the id stored at placement. Notional is not a claim.

Tokenized equities (the `ON` and replica `R` names) are excluded unless
`include_equities` is set. Opting in still skips a name whose order book was
not read: that weight stays zero and the other four are not rescaled onto
100. A v2 refusal that names the v3 book is retried once (`uta: true`); PR
#712's detector is used when that module is present. The schedule timezone
is Asia/Shanghai. US macro prints and US earnings blackouts, when
`event_blackouts.enabled` is true, are America/New_York wall times converted
to UTC with the zone database. They are not read as Shanghai hours.

The authoring ACCESS-KEY is a control-plane credential. It is not a trading
key, it is not written into this repository, and it is not put in a signal.
Do not commit an account id, a key, a secret or a passphrase. If you create
your own Bitget key for anything beside GetAgent, make it trade-and-read only,
with no withdraw and no transfer, on its own sub-account, and IP-whitelist it
if the venue allows.

Keep `execution_mode` at `signal_only` until you have read a forward shadow
that cleared its bar. Follow-trade is opt-in and still refuses when the
sub-account cannot be confirmed.

This Playbook is **live-only**. Its order-book dimension reads resting bid/ask
demand that has no historical feed to replay, so it declares
`backtest_support: none` and produces paper / live evidence rather than a
historical equity curve. Default `execution_mode` is `signal_only` (a platform
rule for live-only Playbooks); `follow_trade_supported` is `true`, so subscribers
who opt into follow-trade also get the order placed for them.

## 策略 / Strategy

RUNECLAW buys strength on a pullback. Nothing is considered until the market
leader (BTCUSDT, used as the regime gate only and never traded) is constructive:
BTC must be up on the day and trading above its session VWAP, with taker buying
as a bonus confirmation. With the gate open, each tradable name is scored across
five dimensions:

- **Momentum (0–25)** — relative strength of the coin's 24h change versus BTC,
  ranked across the scanned universe.
- **VWAP position (0–20)** — above VWAP scores full, at VWAP scores half, below
  VWAP scores zero.
- **Range position (0–20)** — upper third of the 24h range scores full, the
  middle third scores half, the lower third scores zero.
- **Order book (0–20)** — resting best bid/ask volume imbalance; bid-heavy books
  score full, balanced books warn, and an extreme ask wall hard-skips the name.
- **Volume (0–15)** — 24h quote volume ranked across peers; thin names are
  disqualified.

## 开仓 / Entry

When the gate is open and a coin clears the minimum score, the strategy places a
**resting limit long** below the day's average price, at
`VWAP − 0.5 × ATR14`, where `ATR14` is estimated as the 24h range divided by
2.5. The order waits for a normal retracement instead of paying up. If the BTC
gate is only partially constructive, position size is automatically reduced.

## 平仓 / Exit

- **Stop loss** sits just below the 24h low, with per-tier minimums enforced:
  BTC/ETH ≥ 1.5%, SOL/BNB ≥ 1.2%, other alts ≥ 2.5%. If the 24h low implies a
  tighter stop than the tier minimum, the minimum is used instead.
- **Take-profit ladder** — first target at +3.5% (50%), extended target at +7.0%
  (25%), and a final 25% runner trailed by 1 × ATR. The time stop is 4 hours.
  The extended target is inside that window. There is no +20% target.
- **Breakeven** — once price is +2% in favor, the stop is lifted to entry.
  The trigger is that percent alone.

In follow-trade mode the executed bracket is the limit entry plus the protective
stop and the first take-profit target; the staged second target, runner, and
trailing/breakeven management are surfaced in the signal metadata for the live
management layer and for manual subscribers.

## Position sizing

Size is solved **backward from risk**: `notional = allowed_loss / stop_%`, then
`margin = notional / leverage`, capped by `margin_budget` (100 USDT). The
allowed loss is under the pause and, with the other open stops, inside the
book cap. A 150 USDT margin at 10x is a 1500 notional; 2.5% of that is 37.5
USDT, over the 30 USDT pause, and the sizer will not place it.

## Parameters

- **trading_symbols** — the scan universe (USDT perpetuals). The first
  constructive entry, BTCUSDT, is the regime gate and is never traded.
- **leverage** — amplifies gains and drawdowns equally; also feeds the
  margin-from-notional math.
- **margin_budget** — per-strategy capital cap and the denominator for the
  user-facing return percentage.
- **max_loss_usdt** — hard per-trade dollar risk; drives position size from the
  stop distance.
- **max_scan_symbols** — how many names are scanned each pass (lower it if runs
  approach the sandbox time limit).
- **min_score** — the 0–100 quality bar a coin must clear to be traded.

## How to read the signal

A `long` signal reports `limit_price`, `sl_price`, `sl_pct`, `tp1_price`,
`tp2_price`, `notional_usdt`, `margin_usdt`, `leverage`, the BTC `gate` breakdown,
and a ranked `board` of the top candidates with their per-dimension scores. A
`watch` signal means the gate was closed or no coin cleared the score bar; its
`meta.reason` explains why.

## 风险 / Risk

This is a momentum-continuation strategy and it underperforms when BTC is choppy
or falling, when market breadth is weak, or when thin names gap straight through
the stop. By design it can sit idle for long stretches, placing no orders when the
gate is closed. Order-book imbalance is a live, fast-moving feature; resting
demand can evaporate, and limit fills are not guaranteed. There is no historical
backtest for this Playbook, so judge it on live/paper evidence only. Past results
never guarantee future performance, and live trading pays fees and slippage that
erode edge — size every trade to a drawdown you can actually tolerate, and do not
run leverage you cannot afford to lose.
