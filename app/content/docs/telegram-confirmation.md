# Telegram Confirmation Flow

RUNECLAW proposes trades and a person confirms them, unless the operator turns on auto-confirm. This page follows an idea from analysis to execution or rejection.

---

## Why Human-in-the-Loop?

RUNECLAW is designed so that the software proposes and a person decides, and a standard install works that way: the shipped `.env.example` sets `AUTO_CONFIRM_THRESHOLD=1.0` and `AUTO_CONFIRM_LIVE_ENABLED=false`, which turn auto-confirm off. The code defaults are different (`0.85` and on), so an install without those two lines has auto-confirm switched on, within the limits below.

What auto-confirm can and cannot do:

- It confirms only the engine's own ideas, on the operator account, when their confidence reaches `AUTO_CONFIRM_THRESHOLD`. A value of 1.0 (or `/autoconfirm off`) turns it off. With `ADAPTIVE_THRESHOLD_ENABLED` (on by default) the bar moves with the recent win rate, only upward in live mode, and never re-enables a bar set to 1.0.
- A person's own `/analyze` idea or `/trade` ticket is never auto-confirmed.
- In live mode, an order with no human tap also needs `AUTO_CONFIRM_LIVE_ENABLED` and an eligibility record for the running strategy under `benchmark/eligibility/`. None ships, so today a new live position is opened only when a person confirms it.

The analyzer builds trade ideas from technical analysis and confluence scoring, and the risk gate refuses ideas that break its limits. Keeping a person on the final step guards against:

- Runaway automated trading during unusual market conditions
- Execution of trades the operator disagrees with
- Loss of control over capital allocation

---

## Flow Diagram

```text
/analyze BTC, or an idea from the engine's own scan
 |
 v
 Analyzer: indicators, confluence, rule-engine decision -> TradeIdea
 |
 v
 Risk gate: pre-trade checks
 |
 ├── a check fails -> refused; the reply names the failed checks
 |
 v
 Idea queued; card sent with [Take it] [Limit] [Skip]
 |                                   |
 v                                   v
 TAKE IT (or a limit price)          SKIP
 |                                   |
 v                                   v
 Price check + risk re-check         Idea discarded
 |                                   Audit entry (human_reject)
 ├── fails -> refused, with the reason
 |
 v
 Practice fill or live order
 Audit entries
```

When auto-confirm is on, the engine's own ideas skip the card and go straight to the price check and re-check.

---

## Step-by-Step Walkthrough

### 1. You request analysis

Send `/analyze BTC` (or any symbol). The bot replies "Analyzing BTC/USDT..." and runs the pipeline. Ideas also come from the engine's own scans: they are pushed as cards to chats that turned alerts on with `/watch`, and `/latest_signal` lists the pending signals with their buttons.

### 2. The analyzer builds a trade idea

The analyzer:
- Fetches OHLCV candles from Bitget
- Computes indicators (RSI, MACD, Bollinger Bands, ADX, ATR, VWAP, OBV), candlestick patterns and Fibonacci levels
- Classifies the market regime (TREND_UP, TREND_DOWN, EXPANSION, RANGE, CHOP)
- Sets direction and confidence with the rule engine. A configured LLM's read is attached, marked as not used to decide (`LLM_DECIDES` is off by default).
- Produces a `TradeIdea` with entry, stop-loss, take-profit, confidence and reasoning

Ideas below their strategy type's confidence floor (scalp 0.65, intraday 0.55, swing 0.50, position 0.45) are dropped before the risk gate, and the engine then requires 0.60 (`MIN_CONFIDENCE`) of every idea while per-strategy floors are off, the default.

### 3. The risk gate evaluates the idea

The fail-closed pre-trade gate checks account state (circuit breaker, daily loss, drawdown, loss streak, cooldown), size and exposure (position size, open positions, portfolio and per-symbol exposure, correlation groups, PCA concentration, portfolio VaR), the idea itself (entry price, stop-loss, data freshness, risk/reward, and confidence of at least `MIN_CONFIDENCE`, default 0.60) and market conditions (volatility, order-book liquidity, macro events, multi-timeframe alignment).

A failed check refuses the idea. On `/analyze` the reply says "Refused by the risk gate" and lists the failed checks; `/whynot` and `/rejected` show refusals afterwards. Some checks are lenient by design: the liquidity guard, the order-flow gates and the funding-clock gate pass when they have no data, the multi-timeframe and VaR checks skip when their data is missing, and PCA is not evaluated in live mode. Every decision records the checks that ran in `checks_passed` and `checks_failed`. See [Risk Framework](risk-framework.md).

### 4. The idea card

If the idea passes, the bot sends its card, under a signal-card image when one can be drawn. The text reads like this:

```
🟢 LONG  BTC/USDT
────────────────

  🎯 TP    │ $111,836.00
  ──────┼────────────────────────────
  ▲  IN   │ $108,420.00
  ──────┼────────────────────────────
  🛑 SL    │ $107,200.00

  ◑ Confidence │████████▁▁▁▁│ score 72 (uncalibrated)
  ☆ Risk:Reward ★★★ 2.80x (2.43x after fees)

📎 TI-a1b2c3d4

[ Take it ]  [ Limit ]  [ Skip ]
```

- The take-profit and stop-loss lines also show each level's price distance from the entry.
- Confidence is the analyzer's own 0-100 score. It is printed with "(uncalibrated)" because it is not a probability or a win rate.
- Risk:Reward shows the price ratio and what is left of it after estimated fees.
- The card can also carry the setup's record from the trade journal and the model's reasoning.

The buttons:

- **Take it**: confirm the idea as shown
- **Limit**: the bot asks you to type a limit price; sending it confirms the idea as a limit order at that price
- **Skip**: discard the idea

A `/trade` ticket shows **Confirm** and **Cancel** instead. Only the person a card was made for can use its buttons; on the engine's own scan ideas that is the operator. Pending ideas expire after `PENDING_IDEA_TTL` seconds (default 300).

### 5. Confirmation and re-check

When you tap **Take it**:

1. The bot checks that you may confirm. In live mode, a confirm from anyone but an admin needs live-trading permission, unless it opens on their practice book.
2. The current price is read again. If it cannot be read, the confirm is refused. An analysed market-order idea whose price has moved more than 2% from its entry is refused, and the bot re-analyzes it and offers a fresh card.
3. The risk gate's checks run **again**, against the book the trade will land on (the liquidity guard is not re-run). This catches cases where:
   - Another trade was confirmed between the idea and your tap
   - A closed position took the daily loss to its limit
   - The idea's data went stale
   - A macro event entered its blackout window
4. If the re-check passes, the trade executes. If it fails, the reply says why.

The re-check exists because time passes between the analysis and your decision, and the market may have moved.

### 6. Rejection

When you tap **Skip**:

1. The idea is removed from the pending queue.
2. An audit entry is written to `logs/trade.jsonl` with `"action": "human_reject"` and `"result": "REJECTED"`.
3. Nothing else happens.

### 7. Execution

After a successful confirmation and re-check:

- **A practice confirm** (a self-admitted paper account, or a `/paper on` opt-in when `PAPER_SIM_OPT_IN_ENABLED` is on) opens a position on that person's own practice book. Nothing reaches an exchange. See [Paper Trading](paper-trading.md).
- **Any other confirm in live mode** becomes an order on a real exchange account through the live executor: the operator account, or, with `PER_USER_LIVE_ENABLED` on, the trader's own linked account (`/connect`). The live executor tracks the position, places its stop and target on the exchange, and reconciles fills. See [Enabling Live Trading](live-trading.md).
- **Any other confirm on a bot that is not in live mode** is refused: `⛔ Paper trading is disabled on this bot. This bot is LIVE-ONLY.`

The engine then checks open positions every cycle for stop-loss and take-profit exits.

---

## Authorization

Not everyone can confirm trades. The Telegram handler enforces an allow-list built from `TELEGRAM_CHAT_ID`, `ADMIN_TELEGRAM_IDS` and `LIVE_TRADER_TELEGRAM_IDS`:

| Setting | Behavior |
|---------|----------|
| Any of the three set | Those IDs get in. Anyone else needs an admin's `/approve`, or is admitted as a paper user on first contact (any message, `/start` included) while `PAPER_AUTO_ACCEPT` is on (the default). |
| All three empty | There is no allow-list: anyone can use the bot as a paper user. Live trading will not arm without `TELEGRAM_CHAT_ID`. |

Authorization applies to slash commands and to inline buttons alike. A tap from someone who has not been admitted is refused, and a tap on someone else's card is refused.

---

## Rate Limiting

Commands and button taps are rate-limited per user:

| Parameter | Default | Description |
|-----------|---------|-------------|
| Rate limit | 20 requests/minute | Per-user sliding 60-second window |

Over the limit, a command is answered with a warning and a button tap is ignored, until the window clears.

---

## Audit Trail

Each step writes a JSON line. Every line carries `ts`, `level`, `channel`, `message`, `prev_hash` (the SHA-256 of the previous line, so an edited line breaks the chain), `action`, `reasoning`, `result` and `data`. A confirmed idea leaves these entries, in this order (fields shortened with `…`):

```text
logs/scan.jsonl    "action": "trade_idea",        "result": "GENERATED", "message": "Trade idea generated: LONG BTC/USDT",
                   "data": {"id": "TI-a1b2c3d4", "asset": "BTC/USDT", "direction": "LONG", "confidence": 0.72,
                            "entry": 108420.0, "sl": 107200.0, "tp": 111836.0, "rr": 2.8, "timeframe": "1h"}
logs/risk.jsonl    "action": "risk_check",        "result": "APPROVED",
                   "data": {"trade_id": "TI-a1b2c3d4", "verdict": "APPROVED",
                            "checks_passed": ["CIRCUIT_BREAKER: OK", "ENTRY_PRICE: valid", …], "checks_failed": [], …}
logs/risk.jsonl    "action": "risk_check",        "result": "APPROVED", …          (the re-check after the tap)
logs/trade.jsonl   "action": "open_position",     "result": "EXECUTED", "message": "Opened LONG BTC/USDT", …   (a practice fill)
logs/system.jsonl  "action": "telegram_callback", "message": "Callback: confirm:TI-a1b2c3d4:<your id>"
```

A live order writes the live executor's entries to `logs/trade.jsonl` in place of the practice fill. A skipped idea writes `human_reject` to `logs/trade.jsonl` and a `reject:` callback to `logs/system.jsonl`.

Logs are written to `logs/scan.jsonl`, `logs/risk.jsonl`, `logs/trade.jsonl` and `logs/system.jsonl`. Every decision, confirmation and rejection is traceable.
