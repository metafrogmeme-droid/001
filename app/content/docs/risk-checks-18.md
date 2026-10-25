# Pre-Trade Risk Checks

Every trade idea RUNECLAW generates goes through the pre-trade risk gate
(`RiskEngine.evaluate` in `bot/risk/risk_engine.py`) before it is offered for confirmation,
and again when it is confirmed. This page is a quick reference for the gate's core checks.
The decision record of each idea lists every check that ran for it (`checks_passed` /
`checks_failed`), with the reading behind each verdict.

> **How the gate fails.** One failed check rejects the trade. For the core checks below, an
> evaluation error rejects too, and so does missing data in most of them. The order-flow gates
> (`TAKER_3BAR`, `BID_DOMINANCE`) and the funding-clock gate pass without data and say so on the
> decision record. The liquidity guard runs when an idea is proposed and passes, with no line on
> the record, when there is no order book to read. `MTF_ALIGNMENT` and
> `PORTFOLIO_VAR` record a skip when they have no data to evaluate, and `CONCENTRATION_PCA` is not
> evaluated in live mode. Several optional gates (fee-aware entry, re-entry cooldown, funding
> clock, intent policy, validation, authority, user risk preference) record a skip instead of a
> rejection when their own evaluation raises. There is no force-execute flag; what an operator
> can change is listed under Overrides below.

All percentages below are of account equity unless stated otherwise. Position size is
**margin**; notional is margin times the sizing leverage (`SIZING_LEVERAGE`, default 5).

---

## Summary

| Check (name on the decision record) | What it guards against | Default |
|---|---|---|
| Circuit breaker (`CIRCUIT_BREAKER`) | Trading on after a breach | Trips at a 5% daily loss, a 10% drawdown (7% live) or 5 consecutive losses |
| Position size (`POSITION_SIZE`) | One oversized position | Margin cap by strategy: scalp 8%, intraday 10%, swing 13%, position 15% |
| Daily loss (`DAILY_LOSS`) | Exceeding the day's loss budget | 5%; trips the breaker |
| Drawdown (`DRAWDOWN`) | A deep fall from peak equity | 10% (paper), 7% (live); trips the breaker |
| Open positions (`OPEN_POSITIONS`; `MAX_POSITIONS` when it fails) | Too many positions at once | 5 |
| Risk/reward (`RISK_REWARD`) | Poor reward for the risk | By strategy: scalp 1.2, intraday 1.5, swing 1.5, position 2.0; skipped for a manual trade |
| Confidence (`CONFIDENCE`) | Low-conviction ideas | 0.60; skipped for a manual trade |
| Correlation (`CORRELATION`) | Concentrated sector bets | 2 positions per group, 3 in the shared unmapped bucket |
| Loss streak (`LOSS_STREAK`) | Trading on through a losing run | Rejects from 3 consecutive losses; one probe trade after 24 h while flat |
| Entry price (`ENTRY_PRICE`) | Bad price data | Positive and finite |
| Stop-loss (`STOP_LOSS`) | No downside protection | Required, and not equal to the entry |
| Stop/target sides (`SLTP_SIDES`) | Inverted levels | LONG: stop < entry < target; SHORT: target < entry < stop |
| Stop distance (`STOP_DISTANCE`) | A stop inside the noise | At least 0.4% from the entry |
| Margin risk (`MARGIN_RISK`) | A stop too wide for the leverage | The stop may lose at most 30% of the margin at the sizing leverage (about a 6% stop) |
| Stale data (`STALE_DATA`) | Acting on old analysis | 300 s (600 s for a limit entry) |
| Cooldown (`COOLDOWN`) | Re-entering straight after a loss | 120 s after a loss, or after a close whose P&L could not be read |
| Portfolio exposure (`PORTFOLIO_EXPOSURE`) | Total overexposure | 80% in margin across open positions |
| Symbol exposure (`SYMBOL_EXPOSURE`) | Single-asset concentration | 20% in margin per symbol |
| Volatility (`VOLATILITY`) | Extreme volatility | ATR below 7% of the entry (10% for the MEME group); rejects when ATR is missing |
| Warning-rate breaker (`WARNING_RATE_BREAKER`) | Trading while infrastructure warnings fire too often | Rejects while tripped |
| Macro event (`MACRO_EVENT`) | Scheduled macro releases | Blocks or halves size around FOMC, CPI and NFP |
| Higher-timeframe alignment (`MTF_ALIGNMENT`) | Counter-trend entries | Rejects against the higher-timeframe trend; skipped when it is neutral |
| Concentration (`CONCENTRATION_PCA`) | A held book that moves as one | Rejects when the first principal component explains over 70%; paper and backtest only |
| Portfolio VaR (`PORTFOLIO_VAR`) | Tail risk of the book | 15%; skipped below 5 closed trades |
| Taker 3-bar (`TAKER_3BAR`) | Entering against taker flow | Passes without order-flow data |
| Bid dominance (`BID_DOMINANCE`) | Entering against the book | Passes without a fresh order-flow reading |
| Funding clock (`FUNDING_CLOCK`) | Paying extreme funding just before settlement | Blocks the paying side at 0.05% or more within 30 min of a settlement |
| Liquidity guard (runs in the engine) | A thin or wide book | Spread at most 50 bps; depth at least 10 × the position; passes without book data |

The decision record can carry more lines than this table: sizing notes (`QUALITY_LADDER`,
`EQUITY_THROTTLE`, `SIZE_REDUCTIONS`), the live-performance governor (`LIVE_PERF_GOVERNOR`,
which shrinks size or pauses entries when recent realized closes are losing), and gates that are
off by default (`FEE_AWARE`, `REENTRY_COOLDOWN`, `VALIDATION`, `INTENT_POLICY`, `AUTHORITY`).

---

## Detailed breakdown

### Circuit breaker

```text
IF circuit_breaker_active THEN → REJECTED
```

The breaker trips when the daily-loss check or the drawdown check fails, when the losing streak
reaches `MAX_CONSECUTIVE_LOSSES` (5), or on a manual halt. While it is tripped, every new idea is
rejected.

- A **daily-loss** trip clears by itself at the UTC day rollover
  (`DAILY_LOSS_BREAKER_AUTORESET`, on by default), unless the losing streak has reached its limit.
  If the new day is also losing, the daily-loss check trips it again.
- A **drawdown**, **streak** or **manual** trip stays until the operator runs `/reset` or
  `/resume`. (`STREAK_BREAKER_AUTORESET_HOURS` can let a streak trip clear itself; it is 0, off,
  by default.)

---

### Position size

```text
margin_cap  = equity × strategy cap  (scalp 8%, intraday 10%, swing 13%, position 15%)
position    = min(position, margin_cap)      # the size is clamped first
IF position / equity > strategy cap THEN → REJECTED    # the check asserts the clamp held
```

The cap is per strategy while `PER_STRATEGY_NOTIONAL_CAP_ENABLED` is on (the default; settings
`SCALP_MAX_POSITION_PCT` … `POSITION_MAX_POSITION_PCT`). With it off, `MAX_POSITION_PCT`
(default 13) applies to every strategy. Example: a swing trade's margin is at most 13% of equity,
which is 65% of equity in notional at the 5x sizing leverage. How the size is computed before the
cap is on the [Risk Framework](risk-framework.md) page.

---

### Daily loss

```text
IF day_pnl < 0 AND |day_pnl| / equity >= MAX_DAILY_LOSS_PCT THEN → REJECTED + trip breaker
```

- **Paper:** the day's realized P&L plus the unrealized P&L of open positions, so an adverse move
  against an open position counts.
- **Live:** the day's realized closes only; open positions are not marked into it.

`DAILY_LOSS_BREAKER_MIN_USD` (default 0, off) can require a minimum absolute loss as well, for
very small accounts.

---

### Drawdown

```text
IF (peak_equity - equity) / peak_equity >= max_drawdown THEN → REJECTED + trip breaker
```

- **Paper:** 10% (`MAX_DRAWDOWN_PCT`).
- **Live:** 7% (`LIVE_MAX_DRAWDOWN_PCT`), while `LIVE_RISK_HARDENING_ENABLED` is on (the default).
  An admin can override the live cap at runtime with `/drawdownlimit`, bounded to 1–30%.

The live peak is tracked per exchange account and held in memory: a restart re-seeds it at the
current equity unless `PERSIST_LIVE_DRAWDOWN_PEAK=true` (default false). `/reset` and `/resume`
re-seed it as well.

---

### Open positions

```text
IF open_position_count >= MAX_OPEN_POSITIONS THEN → REJECTED
```

Default 5. In live mode the count is the live book's.

---

### Risk/reward ratio

```text
R:R = |take_profit - entry| / |entry - stop_loss|
IF R:R < minimum for the idea's strategy THEN → REJECTED
```

| Strategy | Minimum R:R | Setting |
|---|---|---|
| Scalp | 1.2 | `SCALP_MIN_RR` |
| Intraday | 1.5 | `INTRADAY_MIN_RR` |
| Swing (the default strategy) | 1.5 | `SWING_MIN_RR` |
| Position | 2.0 | `POSITION_MIN_RR` |

A manual `/trade` skips this check. `MIN_RISK_REWARD` is not read by the gate.

---

### Confidence

```text
IF confidence < floor THEN → REJECTED
```

The floor is `MIN_CONFIDENCE` (0.60). With `PER_STRATEGY_CONFIDENCE_FLOOR_ENABLED` on (default
off) it is the strategy's own floor instead (scalp 0.65, intraday 0.55, swing 0.50, position
0.45). The engine drops ideas below the floor before they are offered, and this check asks again.
A manual trade skips it, because its confidence is a stamp, not a measurement. An unreadable
confidence fails the check.

---

### Correlation

```text
IF positions_in_same_group >= cap THEN → REJECTED
```

Groups (`_CORRELATION_GROUPS`): BTC, ETH, ALT_L1, MEME, SOLANA_ECO, DEFI, L2, AI, CEX, and the
tokenized-stock groups STOCK_TECH, STOCK_INDEX, STOCK_CRYPTO and STOCK_SEMI. The cap is 2 per group
(`MAX_CORRELATION_PER_GROUP`). Symbols outside the map share one bucket, `UNMAPPED_ALT`, capped
at 3 (`MAX_UNMAPPED_CORRELATED`). The map is keyed by spot symbols (`SOL/USDT`); a perpetual's
symbol (`SOL/USDT:USDT`) counts in its group only when `CORRELATION_PERP_GROUP_MAPPING_ENABLED` is
on (default off), and otherwise counts in the unmapped bucket. On the paper book the check also
rejects a same-direction position whose recent returns correlate above 0.85 (`MAX_CORRELATION`)
with one already held.

---

### Loss streak

```text
IF consecutive_losses >= MAX_CONSECUTIVE_LOSSES - 2 THEN → REJECTED   (3 by default)
IF consecutive_losses >= MAX_CONSECUTIVE_LOSSES     THEN → trip breaker (5 by default)
```

A win takes one off the streak; it does not reset it. 24 hours after the last loss
(`LOSS_STREAK_PROBE_HOURS`), one probe trade is allowed at a time, and only while no position is
open. A losing probe adds to the streak; a winning one takes one off.

---

### Entry price

```text
IF entry_price is not finite OR entry_price <= 0 THEN → REJECTED
```

---

### Stop-loss, sides and distance

```text
IF stop_loss is missing, not finite or <= 0  THEN → REJECTED   (REQUIRE_STOP_LOSS, default true)
IF stop_loss == entry_price                    THEN → REJECTED
IF LONG  AND NOT (stop < entry < target)       THEN → REJECTED
IF SHORT AND NOT (target < entry < stop)       THEN → REJECTED
IF |entry - stop| / entry < 0.4%               THEN → REJECTED   (MIN_STOP_DISTANCE_PCT)
```

`MARGIN_RISK` then refuses a stop that would lose more than 30% of the margin at the sizing
leverage, which is a stop wider than about 6% (`MAX_MARGIN_RISK_PCT`, unset by default).

---

### Stale data

```text
IF now - idea.timestamp > STALE_DATA_MAX_AGE_SEC (×2 for a limit entry) THEN → REJECTED
```

300 s by default, 600 s for a limit entry. An idea stamped more than 30 s in the future is
rejected as well.

---

### Cooldown

```text
IF now - last_loss_or_unpriced_close < COOLDOWN_AFTER_LOSS_SEC THEN → REJECTED
```

120 s by default. The wait also starts after a close whose P&L could not be read.

---

### Portfolio and symbol exposure

```text
IF (open margin + this margin) / equity > MAX_PORTFOLIO_EXPOSURE_PCT THEN → REJECTED   (80%)
IF (margin on this symbol + this margin) / equity > MAX_SYMBOL_EXPOSURE_PCT THEN → REJECTED (20%)
```

Both compare margin with equity. In live mode they read the live book the trade joins.

---

### Volatility

```text
IF atr IS MISSING OR atr <= 0 THEN → REJECTED (fail-closed)
IF atr / entry_price > threshold THEN → REJECTED
```

The threshold is `VOLATILITY_GUARD_ATR_PCT` (7%). Symbols in the MEME correlation group get a
wider threshold: the larger of that setting and 10% (the 10% is fixed in code). The group is
looked up by the spot symbol, so a perpetual's id (`DOGE/USDT:USDT`) takes the ordinary 7%. At confirm, an
idea stored without an ATR is given one from its stop distance, |entry − stop|, before the gate
runs again.

---

### Liquidity guard

```text
IF spread > OF_MAX_SPREAD_BPS THEN → REJECTED                                  (50 bps)
IF thinner side of the book < max(10 × position, depth floor) THEN → REJECTED
IF executable depth in the top levels < position THEN → REJECTED
```

The depth floor is `OF_MIN_DEPTH_USD` (2,000 USD of book depth), raised to
`OF_LARGE_CAP_DEPTH_FLOOR_USD` (10,000) for BTC, ETH and SOL. The guard lives in
`OrderFlowAnalyzer.liquidity_guard` (`bot/core/order_flow.py`) and runs on the scan path, when an
idea is proposed; it is not run again at confirm. When there is no order-book data it passes:
`config/risk_manifest.yaml` calls it "the ONLY fail-open check the manifest lists: no data =
pass", and the order-flow gates and the funding-clock gate below also pass without data.

---

### Macro event gate

The gate reads `MacroEventProvider` (`bot/core/macro_events.py`) with the calendar in
`config/macro_calendar.seed.json`:

| Severity | Events | Size halved from | New entries blocked | Size halved after the block |
|---|---|---|---|---|
| Critical | FOMC decision | 180 min before | 60 min before to 30 min after | 30 min |
| High | CPI, NFP | 120 min before | 30 min before to 15 min after | 30 min |
| Medium | Core PCE, ISM PMI, PPI, GDP, retail sales | — | 15 min before to 10 min after | 30 min |
| Low | Jobless claims, Fed speeches | — | — | — |

The shipped calendar schedules FOMC decisions through December 2027, and CPI and NFP releases
through December 2026. The other kinds have windows but no dates in it.

The gate also blocks new entries on a symbol whose funding rate reads 0.10% per 8 h or more.
It fails closed: with no calendar, or with one that no longer covers the next 72 hours when the
bot loads it, it blocks new entries unless `MACRO_FAILSAFE=false`, and an error while reading it
rejects the trade.

---

### Higher-timeframe alignment

```text
IF LONG  AND higher-timeframe trend is bearish THEN → REJECTED
IF SHORT AND higher-timeframe trend is bullish THEN → REJECTED
```

The trend comes from the analyzer's EMA20/50 reading on 1h, 4h and 1d, weighted to the daily.
A neutral or unknown trend skips the check (`MTF_ALIGNMENT_GATE_ENABLED`, default on).

---

### Concentration and portfolio VaR

- `CONCENTRATION_PCA` rejects when the first principal component of the held book's returns
  explains more than 70% of their variance. It runs on the paper book and in backtests; in live
  mode it records "not evaluated".
- `PORTFOLIO_VAR` rejects when the book's parametric value-at-risk with the new position would
  exceed 15% (`MAX_PORTFOLIO_VAR_PCT`). It records a skip below 5 closed trades.

---

### Order-flow and funding gates

- `TAKER_3BAR`: a LONG needs the last 3 taker bars buy-dominated, a SHORT sell-dominated. It
  passes when there is no order-flow analyzer or fewer than 3 bars.
- `BID_DOMINANCE`: the book side in the trade's direction must outweigh the other side
  (`OF_DOMINANCE_RATIO`, 1.2). It passes when there is no fresh order-flow reading for the symbol.
- `FUNDING_CLOCK` (`FUNDING_CLOCK_GATE_ENABLED`, default on): blocks an entry on the paying side
  of a funding rate of 0.05% or more (`FUNDING_CLOCK_EXTREME_RATE`) within 30 minutes
  (`FUNDING_CLOCK_WINDOW_MIN`) of a settlement at 00, 08 or 16 UTC. It passes when funding data
  is missing or its own evaluation errors.

---

## Re-check on confirmation

When an idea is confirmed, the engine first re-reads the price on the perpetual the order would
be placed on, and refuses the confirm if:

- the price cannot be read;
- it moved more than 2% from the analysed entry (a market idea), or, for a limit idea whose
  levels the engine may still re-price, more than 2% from the market at signal time;
- it is already past the stop;
- it has moved more than halfway from the entry toward the stop (a market idea).

A manual trade skips the drift and halfway checks, not the past-the-stop check. Then the risk
gate's checks run again against the current book: new positions, the day's losses, the
breaker, the macro window and the idea's age. The liquidity guard is not re-run. A refused
confirm names the check that failed.

A confirm is a person's tap, or an autonomous confirm. An autonomous live order is placed only
when `AUTO_CONFIRM_LIVE_ENABLED` is on and an eligibility record for the running strategy exists
(`benchmark/eligibility/<strategy_hash>.json`; none ships). The code default of
`AUTO_CONFIRM_LIVE_ENABLED` is off; the shipped `.env.example` sets it to `false`.

---

## Overrides

There is no flag that forces a trade through the gate. What can change its behaviour:

- The thresholds named with a setting on this page are environment settings. The others (for
  example the 10% meme-coin volatility floor, the macro windows and the 2% confirm drift) are
  fixed in code.
- An admin can change the live drawdown cap at runtime with `/drawdownlimit` (bounded to 1–30%).
- `MACRO_FAILSAFE=false` stops a missing or stale macro calendar from blocking entries.
- A manual `/trade` skips the risk/reward and confidence checks.

---

## Configuration reference

Code defaults (the shipped `.env.example` sets the same values where it sets them):

```bash
PER_STRATEGY_NOTIONAL_CAP_ENABLED=true  # margin cap by strategy (below)
SWING_MAX_POSITION_PCT=13.0            # SCALP 8, INTRADAY 10, POSITION 15
MAX_POSITION_PCT=13.0                  # margin cap when the per-strategy cap is off
SWING_MAX_RISK_PCT=2.0                 # risk budget per trade; SCALP 1.0, INTRADAY 1.5, POSITION 2.0
MAX_DAILY_LOSS_PCT=5.0                 # daily loss cap (%)
DAILY_LOSS_BREAKER_AUTORESET=true      # a daily-loss trip clears at the UTC rollover
MAX_DRAWDOWN_PCT=10.0                  # drawdown from peak, paper (%)
LIVE_MAX_DRAWDOWN_PCT=7.0              # drawdown from peak, live (%)
PERSIST_LIVE_DRAWDOWN_PEAK=false       # keep the live peak across restarts
MAX_OPEN_POSITIONS=5                   # concurrent positions
SWING_MIN_RR=1.5                       # SCALP 1.2, INTRADAY 1.5, POSITION 2.0
MIN_CONFIDENCE=0.60                    # confidence floor
MAX_CONSECUTIVE_LOSSES=5               # losses before the breaker trips
LOSS_STREAK_PROBE_HOURS=24             # hours before a probe trade after a streak
COOLDOWN_AFTER_LOSS_SEC=120            # wait after a loss (seconds)
MAX_PORTFOLIO_EXPOSURE_PCT=80.0        # total margin exposure (%)
MAX_SYMBOL_EXPOSURE_PCT=20.0           # margin exposure per symbol (%)
MAX_CORRELATION_PER_GROUP=2            # positions per correlation group
MAX_UNMAPPED_CORRELATED=3              # positions in the unmapped bucket
VOLATILITY_GUARD_ATR_PCT=7.0           # ATR / entry threshold (%)
STALE_DATA_MAX_AGE_SEC=300             # max idea age (seconds)
REQUIRE_STOP_LOSS=true                 # stop-loss required
MAX_PORTFOLIO_VAR_PCT=15.0             # portfolio VaR cap (%)
OF_MAX_SPREAD_BPS=50                   # liquidity guard: max spread
OF_MIN_DEPTH_USD=2000                  # liquidity guard: depth floor
```
