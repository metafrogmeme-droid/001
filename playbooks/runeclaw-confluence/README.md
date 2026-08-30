# RUNECLAW Confluence

Regime-adaptive multi-asset USDT perpetual-futures strategy over a universe of
major names (BNB, ETH, SOL, XRP, DOGE, ADA, LINK, AVAX, SUI, BCH, NEAR, AAVE).
BTCUSDT appears in the package contract as the market-leader regime context
only — it is never traded and no signal is ever emitted for it.

## 策略 / Strategy

The system reads the market leader (BTC) first and routes every name in the
universe to the playstyle that matches the backdrop: momentum pullback entries
while the leader trends, mean reversion toward the session VWAP while it
ranges, and a volume-spike breakout overlay when unusual activity arrives.
Each symbol keeps its own independent feature state and finite-state machine;
a blended confluence score built from independent technical voters
(multi-timeframe EMA alignment, RSI, MACD histogram, VWAP side, range
location, OBV slope, volume ratio, Bollinger %B) must clear a hard bar before
that symbol places anything. Flat is the default state: a mixed or unreadable
backdrop opens nothing, and during indicator warmup order submission is
suppressed entirely.

The live RUNECLAW system additionally blends an LLM assessment on top of this
electorate; that layer depends on live context and is not fairly replayable,
so this package is the deterministic electorate renormalized to the full
scale, in both replay and live execution.

## 开仓 / Entry

- 趋势 (momentum): when the leader trends, a resting limit order is placed on
  the favorable side of that symbol's session VWAP, waiting for a normal
  retracement.
- 区间 (mean reversion): when the leader ranges, entries fade a stretched move
  (measured in ATR from VWAP with an RSI extreme) back toward VWAP.
- 放量突破 (breakout): a volume spike breaking the symbol's recent range
  high/low can enter in the sanctioned direction ahead of the other two
  families.
- Every entry requires the confluence score at or above `min_score`. Shorts
  require `allow_short`. Unfilled limit entries expire after their TTL. At
  most `max_concurrent` symbols may hold a position or resting entry at once;
  in live mode each pass scans the universe and places only the best
  qualifying candidate.

## 平仓 / Exit

- A protective stop-market order rests at the venue from the moment the
  position opens (never a bar-close check), floored at a minimum stop
  distance; position size is solved backward from `max_loss_usdt` so the
  worst-case loss at the stop is bounded, then capped by `margin_budget`.
- A take-profit limit rests alongside; whichever exits first cancels the rest.
- The stop lifts to entry once the trade moves `breakeven_pct` in favor.
- Positions carry a per-family time stop; one daily circuit breaker over the
  summed realized PnL pauses new entries after `circuit_pause_usdt` of daily
  loss and stops the day at `circuit_stop_usdt`. An unreadable realized-PnL
  feed disables the breaker visibly in the run log rather than silently
  counting zero.

## 参数 / Parameters

- `trading_symbols` — the traded universe; keep BTCUSDT listed (regime leader,
  never traded). Widening spreads attention but increases data load.
- `leverage` — amplifies both gains and drawdowns; sizing is risk-based first.
- `margin_budget` — sizing cap and the denominator of the displayed return %.
- `max_loss_usdt` — worst-case dollar loss allowed per trade; drives size.
- `min_score` — confluence bar; higher trades less often, more selectively.
- `allow_short` — disable to trade supportive backdrops only.
- `max_concurrent` — portfolio cap on simultaneous positions.
- `max_backtest_symbols` / `backtest_days` — size of the official replay
  slice; the replay trades the first N symbols of the universe and reports
  requested/loaded/skipped symbols honestly.

## 回测指标解读 / Reading the backtest

Strategy return % shown on the card is `net_pnl / margin_budget`. The replay
runs on real exchange bars with maker/taker fees applied on every fill across
the replayed slice of the universe; the account-basis numbers are preserved
alongside for analysts. Trade count, win rate, profit factor, and max
drawdown come from the actual Nautilus position ledger, never from summary
fields. Symbols without replayable data are skipped and reported, never
silently filled in.

## 风险 / Risk

The strategy underperforms when the leader chops without direction, when a
quiet range resolves violently against a reversion entry, or when fast moves
gap through stops (the stop is then filled at the market's next tradable
price, so slippage beyond the cap is possible in extreme gaps). Correlated
majors can hit their stops together even under the portfolio cap. It can sit
idle for long stretches by design. Past performance is not a guarantee; live
trading pays fees, funding, and slippage that erode edge. Size to a drawdown
you can tolerate.
