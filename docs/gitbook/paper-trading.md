# Paper Trading

RUNECLAW keeps a practice (paper) book for each person. A practice book is a simulated portfolio: its positions open and close against real market prices, and no order is sent to any exchange.

## Who Trades on a Practice Book

At every confirm the engine asks one question, `RuneClawEngine.confirm_is_practice(user_id)`. It answers yes for:

- **A self-admitted account.** While `PAPER_AUTO_ACCEPT` is on (the default), a newcomer is admitted with the `paper` role on first contact (any message, `/start` included). Every confirm they make opens on their own practice book until an admin vouches for them with `/approve`, whatever keys they link and whatever the live flags say.
- **A practice opt-in.** When `PAPER_SIM_OPT_IN_ENABLED` is on (default off), any account can send `/paper on` to have its own confirms simulated on its practice book, and `/paper off` to stop.

Every other confirm is a live order or nothing:

- On a bot in live mode, a vouched account's confirm is placed on a real exchange account (see [Enabling Live Trading](live-trading.md)).
- On a bot that is not in live mode, it is refused: `⛔ Paper trading is disabled on this bot. This bot is LIVE-ONLY.`
- A confirm with no person behind it (an auto-confirm, or the CLI's `execute_paper_trade`) never lands on a practice book.
- If an account's record cannot be read, its confirm is not treated as practice, and no live order is placed on it either.

## Opening a Position

When a practice confirm is accepted:

1. The pre-trade risk gate re-checks the idea against **this person's practice book**, through a risk engine of its own (`RuneClawEngine.practice_risk_for`). The operator's equity, positions and breakers are not read, and a practice loss never trips a live breaker.
2. The gate sizes the position as a **margin** (see Sizing below).
3. Quantity is derived: `margin * leverage / entry_price`, at the sizing leverage (`SIZING_LEVERAGE`, default 5x; never above the venue leverage setting, and lower when the gate capped this idea's leverage).
4. The position is recorded with entry price, direction, stop-loss and take-profit. A self-admitted account's row carries the label `PRACTICE`.
5. The cash balance is reduced by the margin. A margin larger than the cash balance is refused, not clamped.

A second confirm on a symbol the practice book already holds is skipped as a duplicate. After a losing practice close, the book's next entry waits `COOLDOWN_AFTER_LOSS_SEC` (default 120 seconds).

## Sizing

The size is a margin, decided by the risk gate (`bot/risk/risk_engine.py::RiskEngine._evaluate_locked`):

1. **Risk budget.** Each strategy type has a budget: the share of equity lost if the stop is hit. Scalp 1%, intraday 1.5%, swing 2%, position 2% (`SCALP_MAX_RISK_PCT` … `POSITION_MAX_RISK_PCT`). Margin = equity × budget ÷ (stop distance × leverage), with the stop distance floored at 0.1%.
2. **Adjustments.** Multipliers for the macro calendar, the market regime and other conditions adjust that figure. Once the book has enough closed trades (`KELLY_MIN_TRADES`, default 20), a half-Kelly ceiling from its own history applies (`KELLY_SIZING_ENABLED`, default on). None of these can lift the size past the cap below.
3. **Cap.** The margin is capped at a share of equity. By default the cap is per strategy type: scalp 8%, intraday 10%, swing 13%, position 15% (`PER_STRATEGY_NOTIONAL_CAP_ENABLED`). With that switched off, `MAX_POSITION_PCT` (13%) applies to every type. A volatility target (`VOL_TARGET_SIZING_ENABLED`, default on) lowers the cap when ATR is high.

Worked example, a swing trade at 5x, before any of the adjustments in step 2 or a volatility-lowered cap:

| Stop distance | Budget-based margin | After the 13% cap | Loss if the stop is hit |
|---------------|---------------------|-------------------|-------------------------|
| 2% | 2% ÷ (2% × 5) = 20% of equity | 13% of equity | 13% × 5 × 2% = 1.3% of equity |
| 4% | 2% ÷ (4% × 5) = 10% of equity | 10% of equity | 10% × 5 × 4% = 2% of equity |

## Closing a Position

### Automatic (Stop-Loss / Take-Profit)

The engine's monitor loop prices every open practice position on each tick and checks it (`MultiUserPortfolio.check_stops_all`):

- **Long position:** SL hit if price <= stop; TP hit if price >= take_profit.
- **Short position:** SL hit if price >= stop; TP hit if price <= take_profit.

The stop it compares against is the trailed stop (`bot/utils/trailing.py`). Once the trade is 1R in profit the stop moves to breakeven; at 2R it locks 0.5R of profit, at 3R 1.5R. The trail behind price narrows from 2× to 1.5× to 1× ATR across those stages, and the stop only ever tightens.

When either level is hit, the position closes at the price that tick read (not at the level itself) and PnL is calculated.

### Manual

`/positions` lists your practice positions, each with a **Close** button: on a bot that is not in live mode, and on a live bot for anyone with no live account of their own to view. It closes the position at the current market price. If no price can be read, the position stays open and the reply says so. On a live bot the operator, and with per-user live trading on a trader who linked keys, see their live positions there instead.

## PnL Calculation

```
LONG:  gross = (exit_price - entry_price) * quantity
SHORT: gross = (entry_price - exit_price) * quantity
commission = (entry_price * quantity + exit_price * quantity) * COMMISSION_PCT / 100
pnl = gross - commission
```

After closing:
- Cash balance is restored: `balance += margin + pnl`, where margin is the entry notional ÷ leverage. A balance that would go negative is clamped at zero.
- The day's realized PnL (UTC day) is updated.
- Peak equity and the deepest drawdown are recalculated.

Commission is charged when the position closes, on both legs. Unrealized PnL on an open position does not include it.

## Portfolio Snapshot

Each book's `snapshot()` returns a `PortfolioState` (`bot/utils/models.py`):

| Field | Description |
|-------|-------------|
| `balance_usd` | Cash not locked as margin |
| `equity_usd` | Cash plus each open position's margin and unrealized PnL. A position with no price on record is valued at its entry |
| `unpriced_positions` | How many open positions were valued at entry because no price was on record |
| `open_positions` | Number of open positions |
| `total_trades` | Number of closed trades on record |
| `win_rate` | Share (0 to 1) of closed trades with net PnL above zero. It reads `0.0` when nothing has closed, so read it with `total_trades` |
| `total_pnl` | Realized PnL of the closed trades, net of commission |
| `total_gross_pnl` / `total_commission` | The same before commission, and the commission paid |
| `daily_pnl` | Today's (UTC) realized PnL plus open positions' unrealized PnL |
| `max_drawdown_pct` | Deepest drawdown from peak equity ever recorded; it never recovers |
| `current_drawdown_pct` | Drawdown from peak equity right now |

A book keeps its last 1,000 closed trades, and the totals above cover those.

Where you see it: `/portfolio`, `/positions`, `/risk`, `/performance`, `/daily_report`, `/start`, `/status` and the chat describe your practice book. On a live bot they do so for everyone with no live account of their own to view, under a line that says so: only the operator sees the operator's account, and with per-user live trading on (`PER_USER_LIVE_ENABLED`, default `false`) a trader who linked keys sees their own. On the website, a signed-in account's dashboard reads its practice book.

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `PAPER_BALANCE_USD` | 10000 | Starting cash of each new practice book |
| `PAPER_AUTO_ACCEPT` | true | A newcomer is admitted as `paper` on first contact (any message, `/start` included), and their confirms open on their practice book. The operator can switch it on the website's Admin page while the bot runs; that choice is saved across restarts |
| `PAPER_SIM_OPT_IN_ENABLED` | false | Lets an account use `/paper on` to simulate its own confirms |
| `COMMISSION_PCT` | 0.06 | Fee charged at close on the entry and the exit notional, in percent |
| `SIZING_LEVERAGE` | 5 | The leverage a practice position is sized and held at |
| `MAX_POSITION_PCT` | 13.0 | Per-trade margin cap, % of equity, for every strategy type when `PER_STRATEGY_NOTIONAL_CAP_ENABLED=false` (the default per-type caps are 8 / 10 / 13 / 15%) |
| `MAX_OPEN_POSITIONS` | 5 | Maximum open positions on one book |
| `SIMULATION_MODE` | true | While true, every live order is vetoed. Practice books work either way |
| `PORTFOLIO_STATE_FILE` | `data/portfolio_state.json` | The engine's own paper book; `combined_state.json` is written in the same folder |

## Persistence

Each person's practice book is saved to `data/portfolio_<user_id>.json` after every open and close, and restored on startup. This means balance, open positions, trade history and drawdown tracking survive restarts.

- **Atomic writes:** State is written to a temp file, flushed to disk and renamed. A `.json.bak` copy holding the same state is written after it.
- **Recovery:** A missing file starts a fresh book at `PAPER_BALANCE_USD`. A file that will not load is recovered from its `.json.bak` backup; only if that fails too does the book start fresh, and the failure is logged at CRITICAL level.
- **No stale overwrite:** A save from a process holding an older copy of the file is refused and kept aside as a `.conflict-<pid>.json` file, rather than written over the newer book.
- **Thread-safe:** Every read and write of a book runs under that book's lock.

The engine's own paper book and the risk state are saved together in `combined_state.json` (`RuneClawEngine._save_combined_state`). If that file exists but cannot be read, it is moved aside to `combined_state.json.corrupt` and the risk engine starts with its breaker tripped until `/reset` (fail-closed).

---

## Limitations

The practice book is a simulation:

- **No slippage.** A practice position opens at once at the idea's entry price, a limit idea's included. It never rests as an order.
- **Fees are charged, funding is not.** A close deducts `COMMISSION_PCT` (0.06%) of the entry and the exit notional. Funding payments are not charged to the book. The **backtest engine** models commission (0.1%) and slippage (0.05%), with commission computed once by the portfolio tracker to avoid double-counting.
- **No partial fills.** Every position fills in full, at once.

Live orders do not share these simplifications: they go through the live executor and fill on the venue.

## Example Session

The flow on the Telegram bot for a self-admitted account. Prices and the trade ID are illustrative.

```
/analyze BTC
→ LONG BTC/USDT card: the TP / IN / SL ladder, confidence,
  risk:reward and the idea ID, with [Take it] [Limit] [Skip]

Take it
→ ✅ Trade executed!

  📝 [PAPER] Simulated LONG BTC/USDT
  Entry $108,420.0000 | SL $106,800.0000 | TP $111,700.0000
  Size $<margin> @ 5x  •  PRACTICE — not the engine record. practice mode — no real order placed
  Sizing: <the step that decided the size>
  Trade ID: TI-a1b2c3d4
```

The CLI (`python3 -m bot.main --mode cli`) runs a separate engine whose confirms have no person behind them, so `execute_paper_trade` there does not open a practice position.
