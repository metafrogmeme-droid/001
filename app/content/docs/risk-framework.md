# Risk Framework

RUNECLAW's pre-trade risk gate (`RiskEngine.evaluate` in `bot/risk/risk_engine.py`) decides
whether a trade idea may be placed. It is **fail-closed** on the checks that carry the money: if
such a check fails, or cannot be evaluated, the trade is rejected. There is no force-execute
flag. What an operator can change is listed on [Pre-Trade Risk Checks](risk-checks-18.md), which
also gives every check's threshold.

`config/risk_manifest.yaml` states how each check it lists behaves when its data is missing. Most
are fail-closed. The advisory checks `MTF_ALIGNMENT`, `PORTFOLIO_VAR` and `CONCENTRATION_PCA`
declare `fail_behavior: skip`. The liquidity guard is marked fail-open, and the manifest calls it
"the ONLY fail-open check the manifest lists: no data = pass". The engine runs more gates than the manifest lists,
and some of them also pass when they have no data: the order-flow gates (`TAKER_3BAR`,
`BID_DOMINANCE`) and the funding-clock gate (`FUNDING_CLOCK`). The skips and the order-flow and
funding-clock passes without data name themselves and their reason on the decision record, and
each is still counted with the passes in `checks_passed`, so read the record's lines rather than
its tally. The liquidity guard runs outside the gate, when an idea is proposed, and leaves no line
on the record when it has no order-book data.

## Design philosophy

### Fail-closed, not fail-open

A gate that fails open lets a trade through when a check errors. RUNECLAW's gate does the
opposite on its core checks:

- An error inside a core check is recorded as a failure, and any failure rejects the trade.
- One failed check makes the whole evaluation `REJECTED`.
- On most checks, missing market data blocks the trade. The ones that pass without data are
  named above, and each says so on the decision record.

This means the system will miss some opportunities. That is an acceptable trade-off: a missed
trade is recoverable, a bad one may not be.

### Defense in depth

Risk is enforced at several layers:

1. **Analyzer** — the analyzer drops ideas below its strategy's confidence floor, and the engine
   drops any idea below the gate's floor (`MIN_CONFIDENCE`, 0.60) before it is offered.
2. **Risk gate** — every check that applies must pass.
3. **Confirmation** — when an idea is confirmed, the price is re-read and the gate runs again
   (the market may have moved).
4. **Configuration** — `SIMULATION_MODE=true` and `LIVE_TRADING_ENABLED=false` by default. Live
   trading needs `SIMULATION_MODE=false`, plus either `LIVE_TRADING_ENABLED=true` or an admin's
   `/golive CONFIRM`, and a `TELEGRAM_CHAT_ID` allow-list.

## The core risk checks

Every `TradeIdea` must pass every check that applies to it before it is offered. The decision
record lists each check that ran. The thresholds below are code defaults; percentages are of
account equity.

### Circuit breaker

**Check:** Is the circuit breaker tripped?

While it is tripped, every new idea is rejected. The triggers and the ways it clears are under
Circuit breaker, further down this page.

### Position size

**Check:** Is the position's margin within its strategy's cap?

| Parameter | Default | Description |
|-----------|---------|-------------|
| `SCALP_MAX_POSITION_PCT` | 8% | Margin cap for a scalp |
| `INTRADAY_MAX_POSITION_PCT` | 10% | Margin cap for an intraday trade |
| `SWING_MAX_POSITION_PCT` | 13% | Margin cap for a swing trade |
| `POSITION_MAX_POSITION_PCT` | 15% | Margin cap for a position trade |
| `MAX_POSITION_PCT` | 13% | The cap for every strategy when `PER_STRATEGY_NOTIONAL_CAP_ENABLED=false` |

The size is clamped to the cap before this check, and the check asserts that the clamp held. A
swing trade's margin of at most 13% of equity is 65% of equity in notional at the 5x sizing
leverage.

### Daily loss

**Check:** Has today's loss reached `MAX_DAILY_LOSS_PCT`?

| Parameter | Default | Description |
|-----------|---------|-------------|
| `MAX_DAILY_LOSS_PCT` | 5.0% | Maximum daily loss as % of equity |

On the paper book the day's P&L is realized plus the unrealized P&L of open positions, so an
adverse move against an open position can trip the breaker. Live, it is the day's realized closes
only; open positions are not marked into it. A breach trips the circuit breaker.

### Maximum drawdown

**Check:** Has equity fallen from its peak by the drawdown limit or more?

| Parameter | Default | Description |
|-----------|---------|-------------|
| `MAX_DRAWDOWN_PCT` | 10.0% | Drawdown limit on the paper book |
| `LIVE_MAX_DRAWDOWN_PCT` | 7.0% | Drawdown limit live, while `LIVE_RISK_HARDENING_ENABLED` is on (the default) |

A breach trips the circuit breaker. The live peak is per exchange account and is re-seeded on a
restart unless `PERSIST_LIVE_DRAWDOWN_PEAK=true`. An admin can change the live cap at runtime with
`/drawdownlimit` (1–30%).

### Open positions limit

**Check:** Is the number of open positions at or above `MAX_OPEN_POSITIONS`?

| Parameter | Default | Description |
|-----------|---------|-------------|
| `MAX_OPEN_POSITIONS` | 5 | Maximum concurrent open positions |

### Risk/reward ratio

**Check:** Is the trade's risk/reward at least its strategy's minimum?

```
Risk/Reward = |Take Profit - Entry| / |Entry - Stop Loss|
```

| Parameter | Default |
|-----------|---------|
| `SCALP_MIN_RR` | 1.2 |
| `INTRADAY_MIN_RR` | 1.5 |
| `SWING_MIN_RR` (swing is the default strategy) | 1.5 |
| `POSITION_MIN_RR` | 2.0 |

A manual `/trade` skips this check. `MIN_RISK_REWARD` is not read by the gate.

### Confidence threshold

**Check:** Is the idea's confidence at least the floor (`MIN_CONFIDENCE`, 0.60)?

Ideas below the floor are already dropped before they are offered; this check asks again. With
`PER_STRATEGY_CONFIDENCE_FLOOR_ENABLED` on (default off) the floor is the strategy's own. A manual
trade skips the check.

### Correlation

**Check:** Would this position exceed the cap for its correlation group (for example MEME,
ALT_L1, DEFI)?

| Parameter | Default | Description |
|-----------|---------|-------------|
| `MAX_CORRELATION_PER_GROUP` | 2 | Maximum positions in one correlation group |
| `MAX_UNMAPPED_CORRELATED` | 3 | Maximum positions among symbols outside the group map |

The groups are listed under Correlation check, further down this page.

### Consecutive loss streak

**Check:** Are there 3 or more consecutive losses (`MAX_CONSECUTIVE_LOSSES` minus 2)?

New trades are rejected during the streak. 24 hours after the last loss
(`LOSS_STREAK_PROBE_HOURS`), one probe trade is allowed at a time, while no position is open. At
5 consecutive losses the circuit breaker trips. A win takes one off the streak.

### Entry price sanity

**Check:** Is the entry price positive and finite?

### Stop-loss required

**Check:** Is a valid stop-loss set, different from the entry, on the correct side, and at least
0.4% away (`MIN_STOP_DISTANCE_PCT`)?

| Parameter | Default | Description |
|-----------|---------|-------------|
| `REQUIRE_STOP_LOSS` | true | Whether a stop-loss is mandatory |

### Stale data guard

**Check:** Is the trade idea younger than the maximum age?

| Parameter | Default | Description |
|-----------|---------|-------------|
| `STALE_DATA_MAX_AGE_SEC` | 300 | Maximum age of a trade idea in seconds (doubled for a limit entry) |

### Cooldown after loss

**Check:** Has the cooldown elapsed since the last loss, or since a close whose P&L could not be
read?

| Parameter | Default | Description |
|-----------|---------|-------------|
| `COOLDOWN_AFTER_LOSS_SEC` | 120 | Seconds to wait |

### Portfolio exposure limit

**Check:** Would total margin across open positions, with this one, exceed the cap?

| Parameter | Default | Description |
|-----------|---------|-------------|
| `MAX_PORTFOLIO_EXPOSURE_PCT` | 80% | Maximum total margin exposure |

### Per-symbol exposure limit

**Check:** Would this symbol's margin, with this position, exceed the per-symbol cap?

| Parameter | Default | Description |
|-----------|---------|-------------|
| `MAX_SYMBOL_EXPOSURE_PCT` | 20% | Maximum margin exposure to one symbol |

### Volatility guard

**Check:** Is ATR, as a percentage of the entry price, below the threshold?

| Parameter | Default | Description |
|-----------|---------|-------------|
| `VOLATILITY_GUARD_ATR_PCT` | 7.0% | Maximum ATR-to-price ratio |

Symbols in the MEME correlation group are allowed a wider ATR: the larger of this setting and 10%
(the 10% is fixed in code). The group is looked up by the spot symbol, so a perpetual's id
(`DOGE/USDT:USDT`) takes the ordinary threshold. **When ATR is unavailable, the volatility guard fails closed and
rejects the trade.** At confirm, an idea stored without an ATR is given one from its stop
distance before the gate runs again.

### Liquidity guard

**Check:** Can the book absorb the position without excessive slippage?

It rejects when the spread exceeds 50 bps (`OF_MAX_SPREAD_BPS`), or when the thinner side of the
book holds less than 10 times the position or the depth floor (`OF_MIN_DEPTH_USD`, raised by
`OF_LARGE_CAP_DEPTH_FLOOR_USD` for BTC, ETH and SOL). It runs in the engine
(`OrderFlowAnalyzer.liquidity_guard`) when an idea is proposed, not again at confirm. With no
order-book data it passes: this is the manifest's "no data = pass" case.

### Macro event gate

**Check:** Is a scheduled macro release close?

The gate reads `MacroEventProvider` (`bot/core/macro_events.py`):

| Severity | Events | Size halved from | New entries blocked | Size halved after the block |
|----------|--------|------------------|---------------------|-----------------------------|
| Critical | FOMC decision | 180 min before | 60 min before to 30 min after | 30 min |
| High | CPI, NFP | 120 min before | 30 min before to 15 min after | 30 min |
| Medium | Core PCE, ISM PMI, PPI, GDP, retail sales | — | 15 min before to 10 min after | 30 min |
| Low | Jobless claims, Fed speeches | — | — | — |

The shipped calendar (`config/macro_calendar.seed.json`) schedules FOMC decisions through
December 2027, and CPI and NFP releases through December 2026. The gate fails closed: with no
calendar, or with one that no longer covers the next 72 hours when the bot loads it, it blocks new
entries unless `MACRO_FAILSAFE=false`, and an error while reading it rejects the trade. It also blocks new
entries on a symbol whose funding rate reads 0.10% per 8 h or more.

### Further checks

The gate also runs higher-timeframe alignment (`MTF_ALIGNMENT`), PCA concentration
(`CONCENTRATION_PCA`, paper and backtest only), portfolio value-at-risk (`PORTFOLIO_VAR`, 15%),
the stop-to-margin cap (`MARGIN_RISK`), the warning-rate breaker, the order-flow gates and the
funding clock. Each is described on [Pre-Trade Risk Checks](risk-checks-18.md).

## Circuit breaker

The circuit breaker halts new trades when a risk limit is breached.

**Trigger conditions:**
- Daily loss reaches `MAX_DAILY_LOSS_PCT` (default 5%).
- Drawdown reaches `MAX_DRAWDOWN_PCT` (default 10%), or `LIVE_MAX_DRAWDOWN_PCT` (default 7%) live.
- Consecutive losses reach `MAX_CONSECUTIVE_LOSSES` (default 5).
- A manual halt (`/halt` or `/pause`).

**Behavior when tripped:**
- Every new trade idea is rejected (`CIRCUIT_BREAKER`).
- `/risk` and `/status` name the gate that is refusing entries, for example `TRIPPED (drawdown)`;
  they show `CLEAR` when nothing is refusing, and `UNREAD` when the state cannot be read.

**Reset:**
- A daily-loss trip clears by itself at the UTC day rollover (`DAILY_LOSS_BREAKER_AUTORESET`, on
  by default), unless the losing streak has reached its limit. If the new day is also losing, the
  daily-loss check trips it again.
- A drawdown, streak or manual trip stays until the operator runs `/reset` or `/resume`.
  `STREAK_BREAKER_AUTORESET_HOURS` can let a streak trip clear itself; it is 0 (off) by default.
- Every trip and every reset is written to the audit log.

## Backtest state isolation

The `BacktestEngine` gives each backtest its own temporary risk-state file (breaker and streak)
and keeps the backtest portfolio in memory, so a backtest never touches the paper or live
circuit breaker or portfolio. Tripping the breaker during a replay has no effect outside it. The
temporary file is removed when the backtest is cleaned up.

## Re-check on confirmation

When an idea is confirmed, the engine first re-reads the price on the perpetual the order would
be placed on. It refuses the confirm if the price cannot be read, has moved more than 2% from the
analysed entry, is already past the stop, or has moved more than halfway from the entry toward the
stop (the drift and halfway checks apply to market ideas, not to limit or manual ones). Then the risk gate runs again against the current state, which catches:

- another trade confirmed between the idea and the confirm;
- the daily loss limit reached by another closed position;
- a macro window that opened in the meantime;
- an idea that has gone stale.

If the re-check fails, the confirm is refused with the check that failed. The liquidity guard
is not re-run at confirm.

## Position sizing

Size is computed as margin, from the strategy's risk budget and the stop distance:

```
risk_budget = equity × strategy risk %          # SWING_MAX_RISK_PCT 2.0; scalp 1.0, intraday 1.5, position 2.0
margin      = risk_budget / (stop_distance × SIZING_LEVERAGE)      # sizing leverage 5
margin      = after the adjustment steps (regime, session, macro, correlation, half-Kelly, …)
margin      = min(margin, equity × strategy margin cap)            # swing 13%
```

Worked example, for a swing trade before any reduction step, with a stop 2.5% from the entry:

- Risk budget: 2% of equity.
- Uncapped margin: 2% / (2.5% × 5) = 16% of equity.
- Capped margin: 13% of equity, which is 65% of equity in notional.
- Loss if stopped out: 65% × 2.5% = 1.625% of equity, below the 2% budget.

The steps between the budget and the cap can raise or lower the size (a trending or expanding
regime raises it, up to ×1.5 in a strong trend), but none can lift it past the cap. The cap itself can be
tightened, never loosened: by the volatility target (`VOL_TARGET_ATR_PCT`, 3%, when ATR is above
it), by a down-sizing regime, and by the other cap-tightening steps the decision record names.
The half-Kelly ceiling applies once there are 20 closed trades (`KELLY_MIN_TRADES`).

## Correlation check

The `MAX_CORRELATION_PER_GROUP` parameter (default 2) prevents concentrated bets in one
correlation group. Assets are mapped to groups in `_CORRELATION_GROUPS`: BTC, ETH, ALT_L1, MEME,
SOLANA_ECO, DEFI, L2, AI, CEX, and the tokenized-stock groups STOCK_TECH, STOCK_INDEX,
STOCK_CRYPTO and STOCK_SEMI. If 2 positions are already open in the MEME group, a third MEME
position is rejected. Symbols outside the map share one bucket, `UNMAPPED_ALT`, capped at 3. The
map is keyed by spot symbols; a perpetual's symbol (`SOL/USDT:USDT`) counts in its group only
when `CORRELATION_PERP_GROUP_MAPPING_ENABLED` is on (default off), and otherwise counts in the
unmapped bucket.

## Why simulation first

RUNECLAW defaults to paper trading:

1. **Safety.** Anyone can run the full system without risking money.
2. **Iterative development.** A strategy can be tested and refined before capital is at risk.
3. **Regulatory caution.** Automated trading with real funds may have legal implications
   depending on jurisdiction.
4. **Trust building.** A system should prove itself in simulation before it handles real money.

To enable live trading (see [Enabling Live Trading](live-trading.md)):

```bash
SIMULATION_MODE=false
LIVE_TRADING_ENABLED=true
TELEGRAM_CHAT_ID=<your chat id>
```

Both flags must be set, and live trading will not arm without `TELEGRAM_CHAT_ID`. This two-key
mechanism prevents accidental activation. An admin's `/golive CONFIRM` is the runtime
alternative to `LIVE_TRADING_ENABLED`; it refuses while `SIMULATION_MODE=true`.

## Compliance considerations

RUNECLAW trades live for its operator with small positions. It is not a regulated product, and no
independent third-party security audit has been performed. The architecture supports
compliance-friendly patterns:

- **Full audit trail.** Every decision is logged with timestamp, action, reasoning and result.
- **Tamper-evident audit chain.** Each entry of the audit chain (`bot/utils/audit_chain.py`,
  `logs/audit_chain.jsonl`) is SHA-256 hash-chained to the previous one, so an edit, deletion or
  reorder is caught on verification.
- **Human-in-the-loop by default.** A live order with no human tap is placed only when
  `AUTO_CONFIRM_LIVE_ENABLED` is on and an eligibility record for the running strategy exists
  (`benchmark/eligibility/<strategy_hash>.json`; none ships). The code default of
  `AUTO_CONFIRM_LIVE_ENABLED` is off, and the shipped `.env.example` sets it to `false`.
- **Rate limiting.** Telegram commands are rate-limited per user.
- **Fail-closed defaults.** The system starts in paper mode with live trading off.

Wider production use would need more: KYC integration, regulatory reporting, segregated accounts
and third-party risk audits.
