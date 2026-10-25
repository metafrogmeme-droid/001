# Demo Scenarios

This page walks through what RUNECLAW does, one scenario at a time, on the Telegram bot ([@HTRUNECLAW_bot](https://t.me/HTRUNECLAW_bot)) or on a copy you run yourself. Some scenarios need the market to cooperate: a circuit-breaker trip needs real losses, and a macro block needs a scheduled event inside its window.

If the operator has switched on the tier ladder (`TIER_GATE_ENABLED`, off by default), `/analyze` needs the Pro tier and `/backtest` the Elite tier. `/tier` shows yours.

## Before You Start: What Take It Does

An idea card from `/analyze` carries three buttons: **Take it**, **Limit** and **Skip**. What Take it does depends on the account and on the bot:

- **A new account** is admitted with the `paper` role on first contact (while `PAPER_AUTO_ACCEPT` is on, the default). Its Take it opens a position on its own practice book, and nothing is sent to an exchange. The reply reads `📝 [PAPER] Simulated …` and ends `practice mode — no real order placed`. See [Paper Trading](paper-trading.md).
- **An account an admin has vouched for** (`/approve`, as a trader) is not on the practice book. On the live bot, its Take it places a **real order** on its own linked exchange account when the deployment has per-user live trading on (`PER_USER_LIVE_ENABLED`) and its keys are linked (`/connect`). Otherwise it is refused and nothing is placed. The operator's Take it places a real order on the operator's account.
- **On a bot that is not in live mode**, a vouched account's Take it is refused: `⛔ Paper trading is disabled on this bot. This bot is LIVE-ONLY.` The exception is an account that has opted into practice with `/paper on`, which needs `PAPER_SIM_OPT_IN_ENABLED` (off by default).

---

## Scenario 1: Market Scan and Analysis

**Goal:** Show the pipeline from market scan to trade idea.

### Steps

```text
1. /scan
 → "Scanning the market…", then a MARKET SCAN card: up to 18 movers
   with price, 24h change, a 1h sparkline and RSI, plus a breadth
   summary (a text list if the card cannot be drawn)

2. /analyze BTC
 → The analysis pipeline:
 - reads 1h candles plus higher-timeframe context
 - computes the indicators (RSI, MACD, Bollinger Bands, ATR, ADX,
   VWAP, OBV, Fibonacci levels, candlestick patterns)
 - classifies the regime (TREND_UP / TREND_DOWN / EXPANSION / RANGE / CHOP)
 - the rule engine decides direction and confidence; a configured
   LLM's read rides along, marked as not used to decide
   (LLM_DECIDES is off by default)
 - the pre-trade risk gate checks the idea
 → An idea card: the TP / IN / SL ladder, confidence, risk:reward and
   the idea ID, with [Take it] [Limit] [Skip]
 → Or, when there is no idea: price, 24h change and volume, and the
   reason when one was recorded (the analyzer's own, or "Refused by
   the risk gate" followed by the checks that failed)

3. Take it → see "Before You Start" above. On a practice account:
   ✅ Trade executed! / 📝 [PAPER] Simulated LONG BTC/USDT …
   Limit   → type your own entry price for the idea
   Skip    → ❌ Got it, trade skipped.

4. /portfolio
 → On a bot that is not in live mode: equity, available cash, win rate
   and PnL of your practice book. In live mode: the live account you
   may view.
```

### What to look for

- Volume spikes measured against each symbol's own recent scans (more than 2x its average 24h turnover)
- The regime the analysis read, which also scales position size
- A structured idea with explicit entry, stop and target levels
- An inline keyboard for human confirmation: a person's `/analyze` idea waits for their tap. The engine's own scan ideas can be confirmed without a tap when the operator enables auto-confirm: `AUTO_CONFIRM_THRESHOLD` below 1.0, and for live orders `AUTO_CONFIRM_LIVE_ENABLED` plus an eligibility record for the running strategy (none ships). The code defaults are a 0.85 threshold with `AUTO_CONFIRM_LIVE_ENABLED` off; the shipped `.env.example` sets the threshold to 1.0 and leaves the live flag false.

---

## Scenario 2: Risk Gate Refusal

**Goal:** Show the fail-closed risk gate refusing a trade.

### Steps

```text
1. /analyze different assets and tap Take it until your book holds
   5 open positions (MAX_OPEN_POSITIONS default). On a practice
   account these are practice positions. On an account that trades
   live, each Take it is a real order.

2. /analyze SOL → Take it
 → ❌ Trade didn't go through
   Trade REJECTED on re-check: MAX_POSITIONS: 5 >= 5

3. /whynot
 → Your own confirms that placed nothing, with the reason

4. /rejected
 → The risk engine's recent rejections, each with the first check
   that failed (marked "engine-wide record" when you have no risk
   engine of your own)
```

On the operator's live account the same limit refuses at `/analyze` already: the card reads `◇ Refused by the risk gate`, then `MAX_POSITIONS: 5 >= 5`.

### What to look for

- The refusal names the check that failed, in the gate's own words
- No command overrides a refusal
- `/whynot` and `/rejected` show refusals after the fact

---

## Scenario 3: Circuit Breaker Trip

**Goal:** Show the circuit breaker halting trading after a loss limit is breached.

### Steps

```text
1. Trade until positions close at a loss. Stops are hit by the
   market, so this needs real price moves.

2. The breaker trips at the next risk check after any of:
 - the day's loss reaches 5% of equity (MAX_DAILY_LOSS_PCT); on a
   paper book open positions' unrealized PnL counts
 - drawdown from peak equity reaches 10% (MAX_DRAWDOWN_PCT), or 7%
   while the bot runs live (LIVE_MAX_DRAWDOWN_PCT, with
   LIVE_RISK_HARDENING_ENABLED on, the default)
 - 5 trades in a row lose (MAX_CONSECUTIVE_LOSSES); practice-book
   closes do not count toward this one

3. Take it on any idea
 → Trade REJECTED on re-check: CIRCUIT_BREAKER: system halted due
   to prior losses (plus any limit that is still breached)

4. /status (the operator, or a trader with a risk engine of their own)
 → 🔴 HALTED, and a "⛔ Trading blocked: circuit breaker" line naming why

5. /reset
 → The operator's /reset clears every account's breaker. A trader with
   a risk engine of their own (PER_USER_LIVE_ENABLED) clears only
   theirs. A paper account cannot run it.
 → 🟢 Circuit breaker reset / Trading resumed.
   If the loss is still over the limit, the next check trips it again.
```

A practice book's breaker is its own: it refuses that book's confirms and halts nothing else, and `/status` does not show it. A daily-loss trip clears itself at the UTC day rollover (`DAILY_LOSS_BREAKER_AUTORESET`, default on). Drawdown, losing-streak and manual halts wait for `/reset` or `/resume`.

### What to look for

- An automatic halt when a limit is reached, with no human step needed
- Entries refused until the breaker is cleared
- A reset scoped to who sent it (a trader's card says it was their account only), written to `logs/risk.jsonl`
- The breaker state on `/status` and `/risk`

---

## Scenario 4: Macro Event Block

**Goal:** Show the macro calendar blocking new entries around high-impact events.

### Steps

```text
1. /macro
 → MACRO CALENDAR card: the calendar's state (Normal, Pre Event
   Caution, Event Lockdown, Post Event Volatility), "Next event in:
   2.2h", the entry gate's own reading (risk state CLEAR, REDUCE or
   BLOCK_NEW_ENTRIES, and the size multiplier), and upcoming events
 → /macro brief shows the gate's posture alone

2. Inside an event's block window:
 → /analyze BTC
 → ◇ Refused by the risk gate
   MACRO_EVENT: BLOCK — BLACKOUT: CPI Release - October 2026 in 20 min
   — blocking new entries.

3. /whynot or /rejected
 → The macro check as the reason

4. After the block window:
 → New entries are allowed again, at half size for 30 minutes
```

The gate's windows by event:

| Events | Half size from | Blocked | Then half size for |
|--------|----------------|---------|--------------------|
| FOMC decision | 180 min before | 60 min before to 30 min after | 30 min |
| CPI, NFP | 120 min before | 30 min before to 15 min after | 30 min |
| Core PCE, ISM PMI, PPI, GDP, retail sales | — | 15 min before to 10 min after | 30 min |
| Lower-impact events (jobless claims, Fed speeches) | — | — | — |

The calendar the gate ships with (`config/macro_calendar.seed.json`) lists FOMC, CPI and NFP dates; the other rows apply to a calendar that lists those events. A funding rate of 0.10% per 8 hours or more on a symbol also blocks new entries on that symbol.

### What to look for

- Awareness of scheduled macro events (FOMC, CPI, NFP and others)
- Automatic blocking inside the high-impact windows, and half size around them
- Fail-closed: a missing or stale calendar blocks new entries unless the operator sets `MACRO_FAILSAFE=false`, and an error while evaluating it always blocks them

---

## Scenario 5: Backtest

**Goal:** Run the backtesting engine and read its scorecard.

### Steps

```text
1. /backtest
 → "⏳ Backtest running • 720 bars • seed 42", then a BACKTEST card
   headed "Synthetic data — tests plumbing, not alpha": return, net PnL,
   commission, slippage and funding; win rate, profit factor, max
   drawdown, Sharpe and Sortino; and the pipeline the bars went through

2. /backtest 1440 99
 → Custom run: 1440 bars, seed 99 (at most 5000 bars)
 → Different synthetic data, different results

3. Run the same seed twice: it generates the same bars.
```

### What to look for

- Synthetic data (geometric Brownian motion with volatility clustering), so the card tests the pipeline, not the strategy
- Stop, target and trailing-stop exits checked against each bar's high and low
- Commission (0.1%) and slippage (0.05%) modeled, with commission computed once by the portfolio tracker, never double-counted
- State isolation: each run uses its own temporary state directory, so a backtest never touches the live breaker or any book

---

## Scenario 6: Audit Trail

**Goal:** Show that decisions are logged and traceable.

### Steps

```text
1. /analyze ETH → Take it
2. /analyze SOL → Skip
3. Keep analysing until the risk gate refuses one (see /whynot)

4. On your own install, read the logs:
 logs/trade.jsonl       → ideas, fills, closes, skips
 logs/risk.jsonl        → every risk evaluation, with its verdict and checks
 logs/system.jsonl      → engine state changes and system events
 logs/scan.jsonl        → scan results and analysis-time risk evaluations
 logs/audit_chain.jsonl → the hash-chained decision record

5. Each log line is one JSON object with:
 - ts (UTC, to the microsecond), level, channel, message
 - action (analyze, risk_check, confirm, paper_fill, open_position,
   close_position, human_reject, …)
 - result (APPROVED, REJECTED, FILLED, EXECUTED, CLOSED, …)
 - reasoning and data (asset, direction, confidence, checks, …)
 - prev_hash: the SHA-256 of the previous line in that file
```

### What to look for

- Four log channels plus a tamper-evident audit chain
- Machine-readable JSONL, with secret-looking values redacted
- Each decision traceable: analysis → risk check → confirmation → fill
- Refusals carry the checks that failed

---

## CLI Quick Demo

To run the skills from a terminal on your own install:

```bash
# A separate engine: nothing it does is saved, and nothing reaches a
# running bot. No Telegram token needed.
python3 -m bot.main --mode cli

# At the runeclaw> prompt:
scan_market                      # Show top movers
analyze_asset BTC                # Analyse one symbol; the card ends with the idea ID
check_risk                       # Show risk status
get_portfolio                    # Show the portfolio card
explain_trade trade_id=TI-...    # Explain one pending idea
quit                             # Exit
```

`explain_trade` with no ID lists the engine's own pending ideas from its scans. A closed trade is explained by `/postmortem` on the bot instead.

---

## Running the Tests

```bash
# The Python suite as CI runs it, compared against tests/known_failures.txt
python3 scripts/ci_test_gate.py

# Everything CI runs, locally, without the network gates
python3 scripts/preflight.py --fast
```

The test harness deletes the runtime state files under `data/` (books, positions, risk state) before and after every test. Run it in a checkout whose `data/` holds nothing you need.

### What `known-baseline` counts

`scripts/ci_test_gate.py` loads `tests/known_failures.txt`. A line whose text before `#` is empty is not an entry. The gate prints that set's size as `known-baseline: N`. A failure whose node id is outside the set fails the gate. An id in the set that passes fails the gate too, and the file is edited in that same commit. The file's header says the baseline is empty: the last id in it was removed when that test started passing.

That count is not 53. On the last green CI run of `main` before this note (GitHub Actions run `40107603330`, job "Lint + tests (baseline gate)", 2026-10-24) the gate printed `known-baseline: 0`, then `7 failed, 26720 passed`, and `PASS — no new failures beyond the known baseline` after the flake filter re-ran those seven. The same log contains `53%` (pytest's progress meter) and `53.6s` (`tests/test_every_candle_read_is_hygiened.py`). Neither is a baseline.

The 53 written in this tree is a different baseline. `tests/unreachable_methods_baseline.txt` states that 53 public method names stay ambiguous: one call the receiver sweep cannot attribute makes the whole name unchecked. `test_the_ambiguity_the_sweep_cannot_resolve_is_stated_not_hidden` in `tests/test_no_new_unreachable_functions.py` pins that number to the sweep. The file says it uses the same two-way rule as `tests/known_failures.txt` (a new entry fails, and an entry that no longer applies fails). It is not the list `ci_test_gate.py` loads. Adding ids to `tests/known_failures.txt` does not change it, and this note does not re-count the sweep.
