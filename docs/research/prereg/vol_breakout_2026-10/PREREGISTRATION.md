# Volatility breakout with momentum confirmation: pre-registration

Date: 27 October 2026. Code: `scripts/vol_breakout_backtest.py`. Offline only;
it places nothing and is not wired to the bot. Written before the first run on
real data. The 27 October signal research (`../voter_candidates_2026-10/`)
found no feature that survives honest clustering, so this is a test of a
hypothesis, and no edge is claimed.

## Fixed parameters (none is tuned)

| item | value |
|---|---|
| universe | BTC/USDT:USDT, ETH/USDT:USDT, SOL/USDT:USDT perpetuals |
| timeframe | 1h bars, signal at the bar's close, fill at the next bar's open |
| ATR | 14-bar simple mean of true range, taken at the signal bar |
| Donchian window | 20 bars (the 20 bars BEFORE the signal bar) |
| range expansion | (high - low) of the signal bar / ATR14 >= 1.5 |
| momentum | 6-bar log return / (24-bar realised volatility x sqrt(6)); sign only |
| risk per trade | 1% of the account balance at the signal |
| stop | 2.0 x ATR from the fill price |
| target | 4.0 x ATR from the fill price (1:2 reward to risk) |
| time stop | exit at the open after 48 bars held |
| direction | long and short, symmetric |
| maximum notional | 1.0 x account balance (1x), one position per symbol |
| fees / slippage | 0.06% taker fee on each side and 0.05% adverse slippage on each fill |
| account | 10,000 USDT per symbol, compounding, books independent |

## Rules

**Long entry**, evaluated when bar t has closed: close(t) is above the highest
high of bars t-20 to t-1, and (high - low)(t) / ATR14(t) is at least 1.5, and
the volatility-scaled 6-bar momentum at t is positive. The order fills at the
open of t+1 plus slippage. **Short entry** is the mirror: close below the
lowest low of the prior 20 bars, the same range expansion, negative momentum.
No entry while a position in that symbol is open.

**Exits**, checked from the entry bar onward, stop first: the stop and the
target are fixed in price at the fill. If one bar touches both, the stop is
taken. A bar that opens beyond the stop fills at its open, not at the stop.
A bar that opens beyond the target fills at its open. After 48 bars the
position exits at the next open. Each exit pays slippage and fee.

**Size**: quantity = (1% x balance) / (2 x ATR14), then capped so notional does
not exceed 1x balance. When the cap binds the trade risks less than 1%, and
the realised risk of every trade is saved.

## Windows

* Discovery: `benchmark/corr_dense_1h`, bars before 2026-05-22T02:00Z. Trades
  still open at the cut are dropped.
* Holdout: `benchmark/majors_1h_v3`, signal bars from 2026-07-06T12:00Z.
  Earlier bars are feature lookback only.

## Measures

Net R-multiple per trade (net profit after fees divided by the 1% risk
amount), win rate, profit factor, maximum drawdown per symbol, trade count,
mean realised risk. Intervals are a 95% percentile bootstrap over ISO weeks
(every symbol of a week is one cluster), 2000 draws, seed 7.

**Random-entry control.** The same exits, sizing and costs, with entries at
random bars and random directions, the same number of entries per symbol,
200 draws. It says how much of the result the exits and costs alone produce.

## Pass rule (all must hold)

1. Discovery net expectancy per trade is positive with the week-cluster interval
   above zero.
2. Discovery expectancy is above the 95th percentile of the random-entry control.
3. Holdout net expectancy is positive, with at least 30 holdout trades.
   With fewer than 30 the holdout gives no verdict and the strategy is not
   passed.

Failing any one is a fail. The parameters are not changed to rescue it.

## Edge cases where drawdown is expected

1. **Range-bound chop.** Breakouts fail and reverse into the stop. A run of
   2-ATR losses at 1% risk each is the normal cost of this style; ten in a row
   is a 9.6% drawdown. The control is size: 1% is not raised after a drawdown.
2. **Volatility collapse then spike.** ATR is small before a news bar, so the
   stop sits close and the position is large relative to price. A gap through
   the stop fills at the open, so the loss can exceed 1R. The 1x notional cap
   limits how large such a position can be.
3. **Trend exhaustion after the breakout.** A late entry on an extended move
   reverts to the stop. The range-expansion filter helps against drift but not
   against a blow-off bar.
4. **Fee and slippage drag.** Each round trip costs about 0.22% of notional.
   With a 2 x ATR stop near 1% of price that is roughly 0.2R per trade, which
   is why the pass rule is net of cost.

## Limits stated in advance

Funding payments are not modelled. The signal bar's close is not tradeable, so
the fill at the next open is the honest convention but still assumes the order
fills. Three correlated symbols over about 2 months of holdout give few
trades. One crypto regime sample cannot say how this behaves in another.
