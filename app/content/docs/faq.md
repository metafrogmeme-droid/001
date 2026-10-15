# FAQ

## General

**What is RUNECLAW?**

RUNECLAW is an AI-powered trading command core by Humanoid Traders that began as an entry to the Bitget AI Base Camp · Hackathon S1. It scans markets, generates trade ideas, enforces strict risk limits, and trades: live with small positions on its operator's account, and on a per-person practice (paper) book for accounts nobody has vouched for. It runs on the website, as a Telegram bot and as an MCP server. It is not a regulated product.

**Where do I use it?**

- On the website: [www.humanoid-traders.com](https://www.humanoid-traders.com/)
- On Telegram: [@HTRUNECLAW_bot](https://t.me/HTRUNECLAW_bot). Your first command admits you with the `paper` role (while `PAPER_AUTO_ACCEPT` is on, the default), and your trades open on your own practice book.

**Does RUNECLAW trade with real money?**

Yes, on its operator's account: the live bot trades real money with small positions. A new account on @HTRUNECLAW_bot trades on its own practice book and is never placed live until an admin vouches for it. A vouched account trades live only on its own linked exchange account, and only where the deployment has per-user live trading on (`PER_USER_LIVE_ENABLED`, default false).

A copy you install yourself starts in simulation: `SIMULATION_MODE` defaults to true and vetoes every live order. Live orders need `SIMULATION_MODE=false`, then either `LIVE_TRADING_ENABLED=true` or an admin's `/golive CONFIRM`, plus a `TELEGRAM_CHAT_ID` and exchange keys. See [Enabling Live Trading](live-trading.md).

**What exchange does it support?**

Bitget is the default venue. Market data (prices and candles) comes from Bitget's public endpoints through the ccxt library, with no keys. Orders can be placed on Bitget, Hyperliquid, Bybit and BingX, through keys the account's owner connects; Bybit EU, OKX, Gate.io, KuCoin Futures and Paradex can be linked for balances only. RUNECLAW trades perpetual futures only (`TRADE_MODE=futures`). Bitget's demo environment is opt-in (`BITGET_SANDBOX=true`, default false), and market data always reads the production markets.

**Is there a token?**

Yes, $RCLAW. Read about it on the token page, [www.humanoid-traders.com/token](https://www.humanoid-traders.com/token), or with `/rclaw` in the bot; both read the same record of the mint. The [$RCLAW Token Roadmap](token-roadmap.md) describes plans, not features that are live. Nothing here is financial advice or an offer to buy any token.

---

## Setup

To use RUNECLAW, open @HTRUNECLAW_bot or www.humanoid-traders.com. The questions below are for running your own copy.

**Do I need a Bitget account?**

Not to use the hosted bot, and not for market data: prices and candles come from Bitget's public endpoints without keys. You need exchange keys only to link your own account (`/connect`) or to run live on your own install. CLI mode (`python3 -m bot.main --mode cli`) runs without a Telegram token.

**Do I need an LLM API key?**

No. By default the rule engine decides direction and confidence (`LLM_DECIDES` is off): direction from the confluence score and the regime, with RSI and MACD as tie-breaks. A configured LLM is still asked, and its read rides along on the idea, marked as not used to decide. Leave `LLM_API_KEY` blank to run without one.

**What Python version is required?**

Python 3.11 or newer.

**How do I get a Telegram bot token?**

Message [@BotFather](https://t.me/BotFather) on Telegram, use the `/newbot` command, and follow the prompts. You will receive a token to set as `TELEGRAM_BOT_TOKEN`.

---

## Trading

**How does the AI generate trade ideas?**

The analyzer computes technical indicators (RSI-14, MACD, Bollinger Bands, ATR-14, ADX-14, VWAP, OBV, Fibonacci retracement levels, candlestick patterns, SMA-50) from hourly candles, with higher-timeframe context, scores how far they agree (the confluence score) and classifies the market regime. By default the rule engine turns that into a direction and a confidence; an LLM decides only when `LLM_DECIDES` is on. The result is a `TradeIdea` with computed entry, stop-loss and take-profit levels, which the pre-trade risk gate then checks.

**What is the minimum confidence for a trade?**

0.60 (60%, `MIN_CONFIDENCE`). The engine and the risk gate refuse an analyzer idea below it. The analyzer applies its own per-strategy floor first, which is higher for a scalp (65%). A ticket you type yourself (`/trade`) skips the confidence check.

**What is the minimum risk/reward ratio?**

It depends on the strategy type: 1.2x for scalps, 1.5x for intraday and swing trades, 2.0x for position trades (`SCALP_MIN_RR`, `INTRADAY_MIN_RR`, `SWING_MIN_RR`, `POSITION_MIN_RR`). The gate does not read `MIN_RISK_REWARD`. A ticket you type yourself is not held to a minimum.

**Can I override a risk rejection?**

No. No command overrides a refusal. To change a limit on your own install, change its environment variable (`MAX_POSITION_PCT`, `MAX_DAILY_LOSS_PCT`, etc.) and restart. An admin can also change the live drawdown cap at runtime with `/drawdownlimit`, within hard bounds.

---

## Risk

**What triggers the circuit breaker?**

1. The day's loss reaches `MAX_DAILY_LOSS_PCT` (default 5%) of equity. On a paper book the day's figure includes open positions' unrealized PnL.
2. Drawdown from peak equity reaches `MAX_DRAWDOWN_PCT` (default 10%), or `LIVE_MAX_DRAWDOWN_PCT` (default 7%) while the bot runs live with `LIVE_RISK_HARDENING_ENABLED` on (the default).
3. `MAX_CONSECUTIVE_LOSSES` (default 5) closed trades lose in a row. Practice-book closes do not count toward it.
4. The operator sends `/halt`.

Daily loss and drawdown are measured at each risk check, and the check that finds a breach trips the breaker and refuses that trade. The losing streak trips it when the losing trade closes.

**How do I reset the circuit breaker?**

Send `/reset` or `/resume`. The operator's `/reset` clears every account's breaker; `/resume` clears the operator's own. A trader with a risk engine of their own (`PER_USER_LIVE_ENABLED`) resets only their own, and a `paper` account cannot reset. A daily-loss trip clears itself at the UTC day rollover (`DAILY_LOSS_BREAKER_AUTORESET`, default on); drawdown, losing-streak and manual halts wait for a reset. If a loss is still over its limit, the next risk check trips the breaker again.

**What are the default risk limits?**

| Parameter | Default |
|-----------|---------|
| Max position size | 13% of equity as margin (`MAX_POSITION_PCT`); by strategy type 8% scalp, 10% intraday, 13% swing, 15% position |
| Risk budget per trade (loss at the stop) | 1% scalp, 1.5% intraday, 2% swing and position |
| Max daily loss | 5% of equity |
| Max drawdown | 10% from peak (7% while the bot runs live) |
| Max open positions | 5 |
| Losing streak | 5 in a row |
| Min risk/reward | 1.2x scalp, 1.5x intraday and swing, 2.0x position |
| Min confidence | 60% |

---

## Paper Trading

**Who trades on paper?**

A self-admitted account, on its own practice book, until an admin vouches for it; and, where `PAPER_SIM_OPT_IN_ENABLED` is on, any account that sends `/paper on`. See [Paper Trading](paper-trading.md).

**What is the default paper balance?**

The `PAPER_BALANCE_USD` setting, default `10000`. Each new practice book starts with it.

**Is portfolio state persisted?**

Yes. Each practice book is saved to `data/portfolio_<user_id>.json` after every open and close, and restored on restart. A file that will not load is recovered from its `.json.bak` backup; only if that fails too does the book start fresh at the default balance. The engine's own paper book and the risk state are saved together in `data/combined_state.json`; if that file cannot be read, it is moved aside and the risk engine starts halted (fail-closed).

**Does it simulate fees or slippage?**

Practice trades fill at the idea's entry price, with no slippage, and pay `COMMISSION_PCT` (default 0.06%) on the entry and the exit notional when they close. Funding is not charged. The **backtest engine** models both commission (0.1%) and slippage (0.05%). Commission is computed once by the portfolio tracker to avoid double-counting.

---

## Technical

**What indicators does the analyzer compute?**

- RSI-14 (Relative Strength Index)
- MACD (12, 26, 9) with signal line
- Bollinger Bands (20-period, 2 standard deviations)
- ATR-14 (Wilder's average true range, from high, low and close)
- ADX-14 (Average Directional Index for regime detection)
- VWAP (session-anchored by default) and VWAP anchored at the last swing pivot
- OBV (On-Balance Volume trend)
- Fibonacci retracement levels (23.6%, 38.2%, 50%, 61.8%, 78.6% over the last 50 bars' swing)
- Candlestick patterns (18, including four doji variants, hammer, shooting star, engulfing, harami, tweezers, morning/evening star, three white soldiers and three black crows)
- SMA-50 (trend alignment filter)

**How is volume spike detection implemented?**

The scanner keeps each symbol's 24h turnover from its last 20 scans. Once it has at least 3, a turnover more than 2x their average, and above `MIN_SPIKE_NOTIONAL_USD`, is flagged as a spike.

**What logging format is used?**

Structured JSON lines (JSONL). Each line is a self-contained JSON object with timestamp, level, channel, message, action, reasoning, result, data, and `prev_hash`, the SHA-256 of the previous line in the same file. Four files: `trade.jsonl`, `risk.jsonl`, `system.jsonl`, `scan.jsonl`. Decisions are also sealed in a hash-chained `logs/audit_chain.jsonl`. See the [API Reference](api-reference.md).

**Can I add custom skills?**

Yes. Subclass `BaseSkill`, implement the `execute` method, and register it with the `SkillRegistry`. See the [API Reference](api-reference.md) for details.
