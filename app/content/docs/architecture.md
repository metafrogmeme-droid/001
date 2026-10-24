# Architecture

This page describes how RUNECLAW is put together: the processes, the trading pipeline, and the parts around it.

## High-Level Overview

```
  Telegram (@HTRUNECLAW_bot)        Website (www.humanoid-traders.com)
            |                           |                     |
            v                           v                     v
 +----------------------------------------------+   +--------------------+
 |  Bot process  (python3 -m bot.main)          |   |  API bridge        |
 |                                              |   |  (api_bridge.py,   |
 |  Telegram handler     Gateway for the        |   |   uvicorn :8000)   |
 |        |              website (:8080)        |   |  reads the bot's   |
 |        v                    v                |   |  saved state       |
 |  Skill registry (commands, chat, web)        |   +--------------------+
 |                   |                          |
 |                   v                          |
 |            RuneClaw Engine                   |
 |     +-------------+-------------+            |
 |     v             v             v            |
 |  Market        Analyzer      Risk engine     |
 |  scanner       (rules +      (pre-trade      |
 |  (ccxt)        optional LLM)  gate)          |
 |                                 |            |
 |                                 v            |
 |                 Confirmation (idea card)     |
 |                                 |            |
 |                                 v            |
 |       Practice book  or  live executor       |
 +----------------------------------------------+
```

The confirmation step is skipped for the engine's own ideas when the operator turns on auto-confirm (`AUTO_CONFIRM_THRESHOLD`; for a live order also `AUTO_CONFIRM_LIVE_ENABLED` and an eligibility record). Stage 4 below has the details.

A deployment runs **two processes**:

| Process | Start | What it does |
|---------|-------|--------------|
| Bot | `python3 -m bot.main --mode telegram` | Telegram bot, the trading engine, and the gateway the website calls on port 8080 |
| API bridge | `python3 api_bridge.py` | A separate FastAPI app (uvicorn, port 8000) for the website's REST endpoints and several dashboard panels. It reads the bot's saved state through its own engine copy, which cannot write the bot's files. |

Docker Compose adds Redis and an nginx TLS proxy. See [Getting Started](getting-started.md).

## Pipeline Stages

The engine runs a loop with five stages. A cycle starts every `SCAN_INTERVAL` seconds (default 60). With `SMART_SCAN_ENABLED` (on by default) the interval follows volatility, kept between `SMART_SCAN_MIN_INTERVAL` and `SMART_SCAN_MAX_INTERVAL` (defaults 60 and 600 seconds); at the default settings a quiet market stretches it to 90 seconds.

### Stage 1: SCAN

The `MarketScanner` reads Bitget tickers through ccxt. Market data is public and needs no API keys. With the default universe (`ASSET_UNIVERSE=all_markets`) it reads USDT spot pairs and USDT-margined perpetuals, including TradFi perps such as stocks and metals. It then:

1. Drops markets under a 24h-volume floor: `MIN_CRYPTO_VOLUME_USD` (default 1,500,000 USD) for crypto, `MIN_TRADFI_VOLUME_USD` (default 5,000 USD) for TradFi perps.
2. Scores momentum from the 24h price change, boosted when volume spikes.
3. Flags a volume spike when this scan's turnover is more than 2x the symbol's average over its recent scans (up to 20). With fewer than 3 earlier scans the ratio is left unmeasured rather than read as no spike.
4. Ranks by absolute momentum and keeps up to `TOP_MOVERS_COUNT` (default 200), sharing the slots across asset categories.

Output: a list of `MarketSignal` objects.

### Stage 2: ANALYZE

The `Analyzer` receives every ranked signal, at most `SCAN_ANALYSIS_CONCURRENCY` (default 12) at a time, and for each:

1. Fetches 100 candles on the signal's timeframe (1h for the engine's own scan), plus higher-timeframe candles for the multi-timeframe read.
2. Computes indicators: RSI-14, MACD (12/26/9), Bollinger Bands (20/2), ATR-14, ADX-14, VWAP, rolling VWAP (20-bar and 50-bar) and On-Balance Volume (OBV).
3. Detects 18 candlestick patterns: doji (plus dragonfly, gravestone and long-legged doji), hammer, shooting star, spinning top, marubozu, bullish/bearish engulfing, bullish/bearish harami, tweezer top/bottom, morning star, evening star, three white soldiers and three black crows.
4. Computes Fibonacci retracement levels (23.6%, 38.2%, 50%, 61.8%, 78.6%) from the swing high and low of the last 50 bars.
5. Classifies the market regime: TREND_UP, TREND_DOWN, EXPANSION, RANGE or CHOP.
6. Scores confluence across many voters (momentum, trend, volume, structure, patterns, order flow, multi-timeframe, smart money, sentiment). Family caps (`CONFLUENCE_FAMILY_CAP_ENABLED`, on by default) stop correlated voters from stacking.
7. Sets direction and confidence with the rule engine. If an LLM is configured, the model is asked beside each decision and its read rides on the idea, marked as not used to decide. `LLM_DECIDES` (off by default) gives the LLM's thesis the decision instead.
8. Builds a `TradeIdea` with entry, stop-loss, take-profit, confidence, strategy type and reasoning.
9. Drops ideas below their strategy type's confidence floor: scalp 0.65, intraday 0.55, swing 0.50, position 0.45. The engine then requires 0.60 (`MIN_CONFIDENCE`) of every idea while `PER_STRATEGY_CONFIDENCE_FLOOR_ENABLED` is off, the default.

Output: a `TradeIdea`, or nothing when there is no setup.

### Stage 3: RISK GATE

Every `TradeIdea` goes to the `RiskEngine`, the fail-closed pre-trade gate. `config/risk_manifest.yaml` documents its checks and the engine adds further gates; each decision records the checks that ran in `checks_passed` and `checks_failed`. The checks cover:

- **Account state:** the circuit breaker, the daily loss limit (`MAX_DAILY_LOSS_PCT`, default 5%), the drawdown limit (`MAX_DRAWDOWN_PCT`, default 10%; 7% when live), the loss streak, and a cooldown after a loss.
- **Size and exposure:** the position-size cap, the number of open positions (`MAX_OPEN_POSITIONS`, default 5), total exposure (`MAX_PORTFOLIO_EXPOSURE_PCT`, default 80%), per-symbol exposure, correlation groups, PCA concentration and portfolio Value at Risk.
- **The idea itself:** a valid entry price, a stop-loss, data freshness, the risk/reward ratio (scalp 1.2, intraday and swing 1.5, position 2.0) and confidence (`MIN_CONFIDENCE`, default 0.60).
- **Market conditions:** volatility (ATR), order-book liquidity, the macro event gate (below) and multi-timeframe alignment.

A check that fails, or that cannot be evaluated, rejects the trade, with these exceptions: the liquidity guard, the order-flow gates (`TAKER_3BAR`, `BID_DOMINANCE`) and the funding-clock gate pass without data; the multi-timeframe and VaR checks skip when their data is missing, and PCA is not evaluated in live mode; several optional gates record a skip when their own evaluation raises; and a manual `/trade` skips the risk/reward and confidence checks. [Pre-Trade Risk Checks](risk-checks-18.md) lists each, and [Risk Framework](risk-framework.md) covers the gate in depth.

The circuit breaker trips on a daily-loss breach, a drawdown breach, or `MAX_CONSECUTIVE_LOSSES` losses in a row (default 5). A daily-loss trip clears at the UTC day rollover (`DAILY_LOSS_BREAKER_AUTORESET`, on by default). A streak trip can clear on a timer (`STREAK_BREAKER_AUTORESET_HOURS`, off by default). Otherwise the breaker stays tripped until `/reset` or `/resume`.

### Stage 4: CONFIRM

Approved ideas wait in a pending queue and appear as cards with buttons: **Take it**, **Limit** and **Skip** on an idea card, **Confirm** and **Cancel** on a `/trade` card. The website offers the same confirm. Ideas expire after `PENDING_IDEA_TTL` seconds (default 300).

This is the human-in-the-loop step, and it holds unless the operator turns on auto-confirm. Auto-confirm confirms the engine's own ideas, on the operator account, when their confidence reaches `AUTO_CONFIRM_THRESHOLD` (code default 0.85; `.env.example` sets 1.0, which turns it off). In live mode an order with no human tap also needs `AUTO_CONFIRM_LIVE_ENABLED` (code default off; `.env.example` sets `false`) and an eligibility record for the running strategy under `benchmark/eligibility/`, and none ships. A person's own idea or `/trade` ticket is never auto-confirmed.

On confirmation:
1. The current price is read again. An analysed market-order idea whose price has moved more than 2% from its entry is refused; in Telegram the bot then re-analyzes it and offers a fresh card.
2. The risk gate runs again against the book the trade will land on.
3. If it still passes, the trade executes. A practice confirm (a self-admitted paper account, or a `/paper on` opt-in when `PAPER_SIM_OPT_IN_ENABLED` is on) opens on that person's own practice book. Any other confirm becomes a live order through the live executor, and on a bot that is not in live mode it is refused. See [Paper Trading](paper-trading.md).

On rejection, the idea is removed from the queue and an audit entry is written.

### Stage 5: MONITOR

Each cycle checks open positions. A practice position closes when price crosses its stop-loss or take-profit, and its P&L is recorded. A live position carries its stop and target as orders on the exchange, and the live executor reconciles fills and closes.

## Data Models

The pipeline passes Pydantic v2 models (`bot/utils/models.py`):

| Model | Purpose |
|-------|---------|
| `MarketSignal` | Scanner output: price, volume, momentum |
| `TradeIdea` | Analyzer output: entry, SL, TP, confidence, reasoning |
| `RiskCheck` | Risk engine output: verdict, checks passed and failed |
| `TradeExecution` | Execution record: position details, P&L |
| `PortfolioState` | Portfolio snapshot: balance, equity, drawdown |

The models are mutable `BaseModel` instances. Mutation is discouraged outside the owning module but not enforced with `frozen=True`.

## Logging Architecture

Four log channels write structured JSON lines (`bot/utils/logger.py`):

| Channel | File | Content |
|---------|------|---------|
| `runeclaw.trade` | `logs/trade.jsonl` | Executions, closes, rejections by a person |
| `runeclaw.risk` | `logs/risk.jsonl` | Every risk evaluation, circuit breaker events |
| `runeclaw.system` | `logs/system.jsonl` | Engine lifecycle, button taps, access, errors |
| `runeclaw.scan` | `logs/scan.jsonl` | Scan cycles and generated trade ideas |

Every entry carries `ts`, `level`, `channel`, `message` and `prev_hash`, the SHA-256 of the previous line, so an edited line breaks the chain. Audit entries add `action`, `reasoning`, `result` and `data`. Known secret shapes are redacted before a line is written.

## Configuration

Configuration is read from environment variables (`bot/config.py`). The `AppConfig` dataclass nests one dataclass per area: risk, exchange, Telegram, LLM, analyzer, learning, strategy types and more. The main ones:

- `RiskLimits`: position sizing, loss limits, drawdown caps
- `ExchangeConfig`: the venue (`VENUE`, default `bitget`) and its credentials. Production unless `BITGET_SANDBOX=true`.
- `TelegramConfig`: bot token, the allow-list IDs, the per-user rate limit
- `LLMConfig`: provider, key, model, budgets

`.env.example` lists the configurable values. Where its value differs from the code default (auto-confirm, for example), the file says so.

---

## AI Learning System

The learning modules in `bot/learning/` record outcomes and propose changes:

| Module | Purpose |
|--------|---------|
| **Experience Memory** | Append-only record of decisions, rejections and closed outcomes (runs) |
| **Reflection Engine** | Writes a structured reflection on each rejected idea (runs); post-trade reflection is built but not called yet; may recommend, never applies |
| **Strategy Evaluator** | Grades strategies on an S/A/B/C/D scale from risk-adjusted results (built; the engine does not call it yet) |
| **Pattern Learner** | Groups recorded closes by condition (regime, macro state, low volatility) with win rates and their 95% intervals (runs on request, `/patterns`) |
| **Macro Learner** | A memory of how markets reacted to macro events; context only, never an order (built; nothing feeds it yet) |
| **Model Comparer** | Compares the rule engine's, the LLM's and the macro-aware call on the same decisions (built; nothing records comparisons yet) |
| **Prompt Optimizer** | Versions prompts, scores them and proposes changes that keep the safety wording (built; no prompt is registered yet) |
| **Feedback Collector** | Records operator feedback on decisions and explanations (built; no command feeds it yet) |

See [AI Learning System](ai-learning-system.md) for each module.

### Safety Policy

`bot/learning/safety_policy.py` blocks the learning system from, among other things:

- Disabling risk checks or bypassing the macro lockdown
- Removing stop-losses, loss limits, drawdown limits or human approval
- Raising leverage or enabling live trading
- Deleting or reducing audit logs, or rewriting historical results

An action on neither its blocked list nor its allowed list is blocked.

---

## LLM Token Optimizer

Three layers bound what the LLM costs:

| Layer | Mechanism |
|-------|-----------|
| **Semantic Cache** | Caches LLM responses keyed by bucketed market conditions, so near-identical conditions reuse a result instead of a new call. |
| **Tiered Pipeline** | Lets the rule engine answer clear-cut signals, sends ambiguous ones to a cheaper model and promising ones to the full model. |
| **Adaptive Frequency** | Skips the LLM, using the rule engine alone, when the market is quiet (ADX under 15, no volume spike, 24h move under 2%). |

A batching layer (`SmartBatcher` in `bot/core/token_optimizer.py`) is built but not wired into the analysis path. `/optimize` shows the cache and tier statistics. Because the rule engine decides by default, these layers bound the cost of the LLM's side read.

---

## Macro Event Gate

The risk gate's macro check reads `MacroEventProvider` (`bot/core/macro_events.py`), loaded from `config/macro_calendar.seed.json`. Each event's window depends on its severity:

| Severity | Events | Window |
|----------|--------|--------|
| Critical | FOMC rate decisions | Size halved from 3 hours before; new entries blocked from 60 minutes before to 30 minutes after; size halved for 30 minutes after that |
| High | CPI, NFP | Size halved from 2 hours before; blocked from 30 minutes before to 15 minutes after; size halved for 30 minutes after that |
| Medium | Core PCE, ISM PMI, PPI, GDP, retail sales | Blocked from 15 minutes before to 10 minutes after; size halved for 30 minutes after that |
| Low | Jobless claims, Fed speeches | No window |

The shipped seed lists FOMC decisions through December 2027 and CPI and NFP releases through December 2026. An extreme funding rate on the traded symbol also blocks new entries. If the calendar is missing, unreadable or stale when the bot loads it (older than 72 hours with no event beyond the next 72 hours), new entries are blocked unless `MACRO_FAILSAFE=false`. An evaluation error also blocks.

`/macro` shows the event calendar (`bot/macro/calendar.py`), which reports one of five states: NORMAL, PRE_EVENT_CAUTION, EVENT_LOCKDOWN, POST_EVENT_VOLATILITY or BLACKOUT. `/macro brief` shows the gate's own posture: its risk state, the size multiplier on new entries, and whether the calendar is stale or missing.
