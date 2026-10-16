# Telegram Dashboard & AI Chat

RUNECLAW's Telegram interface is built from HTML cards, inline keyboards, text gauges and a chat assistant.

**Try it live:** [@HTRUNECLAW_bot](https://t.me/HTRUNECLAW_bot)

---

## Dashboard Navigation

`/dashboard` sends the pane card. Its four buttons, **Status**, **Risk**, **Portfolio** and **Scan**, switch panes by editing the same message rather than sending new ones, and the card reopens on the pane you viewed last.

| Pane | Content |
|------|---------|
| **Status** | Breaker state, mode (LIVE / PAPER / IDLE), macro state and a system-health score; Capital (equity, daily P&L, drawdown); Positions (open, total, win rate, exposure); Risk Gate (breaker, loss streak). The operator's card also shows today's model and infrastructure costs. |
| **Risk** | Circuit breaker, a health score, the drawdown, exposure and streak gauges, a capital breakdown, and the configured limits |
| **Portfolio** | Your own book: balance (equity, available, win rate), a P&L waterfall (gross, commission, trading) and open positions. Outside live mode this is your practice book; in live mode, your own linked account. |
| **Scan** | The latest market scan |

`/status` sends the engine-status card with the main menu: **Scan Market**, **Latest Signal**, **Positions**, **Performance**, **Orders**, **Risk** and **Stop Bot** (the emergency stop, which asks first and runs only for the operator).

Macro and learning have their own commands, `/macro` and `/learn`. Model and API spend is the operator's `/costs` card.

---

## Visual Design

### Gauges

The Risk pane draws its limits as gauges. The tip colour is green below half the limit, yellow from half, and red from 80% of it:

```
🛡 RISK DASHBOARD
────────────────

  🟢 Circuit Breaker: CLEAR
  ● Health Score │━━━━━━━━━━━━╌╌│ 89%

  🟢 Drawdown   │━━━╌╌╌╌╌╌╌╌╌│ 3.2% / 10% (paper snapshot)
  🟢 Exposure   │╌╌╌╌╌╌╌╌╌╌╌╌│ 1.8% / 80%
  🟢 Streak     │╌╌╌╌╌╌╌╌╌╌╌╌│ 0 / 5
```

The limits are the configured ones: `MAX_DRAWDOWN_PCT` (default 10), `MAX_PORTFOLIO_EXPOSURE_PCT` (default 80) and `MAX_CONSECUTIVE_LOSSES` (default 5). The drawdown line names its source (paper snapshot, live equity high-water mark, or your peak across venues).

A value that could not be read prints `not read` with an empty bar and a yellow tip, never a zero:

```
  🟡 Exposure   │╌╌╌╌╌╌╌╌╌╌╌╌│ not read
```

When a gauge input is unread, the health score prints `not measured` instead of a number.

### Sectioned Cards

Each pane is split into sections with an emoji heading and `- Label: value` lines. The Risk pane, below its gauges, has **Capital** (equity, daily P&L, exposure, positions, correlation groups) and **Limits** (minimum confidence, minimum R:R, maximum drawdown, maximum daily loss, volatility guard). A figure nobody could read prints `--`, not `0`.

### Status Icons

| Icon | Meaning |
|------|---------|
| 🟢 | Clear, or within limits |
| 🟡 | Caution, or a value that could not be read |
| 🔴 | Tripped, refusing entries, or near a limit |
| ⚪ | Unread breaker state, or a measured flat value |

The breaker line reads `🟢 CLEAR`, `🔴 TRIPPED (<cause>)`, `🔴 REFUSING ENTRIES` (with the reason) or `⚪ UNREAD`.

---

## AI Chat

You can send **free-text messages** as well as commands.

### How It Works

- Free text is matched first. A typed trade such as `buy SOL 71.42 sl 70.05 tp 76.42` opens the same confirm card as `/trade`, and phrases such as "scan BTC" or "show my portfolio" run the matching skill.
- Anything else goes to the chat model, which answers as a trading assistant with the bot's current context.
- Chat runs on the chat routing tier, chosen by the caller's tier and the operator's settings (`LLM_TIER_CHAT_PROVIDER`; the operator's `/llmtiers` shows the routing). If a provider fails, chat falls back along a fixed chain: Gemini, then Alibaba's Qwen, with Claude Haiku in between for admins.
- Reply length follows the question: a quick question gets a short answer, a request for a full analysis a longer one.
- The bot suggests relevant commands where they help.

### Example

The wording comes from the model, so this is an illustration:

```
User: What does RSI mean and when should I use it?

Bot: RSI (Relative Strength Index) measures momentum on
a 0-100 scale. Below 30 is usually read as oversold, above
70 as overbought.

RUNECLAW uses RSI-14 as one of its confluence voters.
It's most useful in ranging markets — in strong trends,
RSI can stay overbought/oversold for extended periods.

Try /analyze BTC to see RSI in action with full
confluence scoring.
```

### Safety

- Chat cannot place, change or close a trade, halt or resume the engine, or move funds. Asked to, it says so and names the command or button that can.
- Chat spend counts against the daily LLM budget, and chat stops at its share of it (`LLM_CHAT_BUDGET_SHARE`, default 0.5). The spend is a line on the operator's `/costs` card.

---

## User Management

### Role-Based Access

| Role | Access |
|------|--------|
| **Admin** | Every command, including the operator groups (users, engine operations, LLM settings, costs) |
| **Trader** | Vouched for by an admin's `/approve`. Analysis, scans, trading and backtests, plus `/mode`, `/stake`, and `/reset`, `/pause` and `/resume` on their own account when per-user live trading is on |
| **Paper** | Self-admitted. The trader commands without `/reset`, `/pause`, `/resume`, `/mode` or `/stake`; their confirms open on their own practice book |
| **Viewer** | Read-only: status, risk, portfolio, scans, macro, learning |
| **Pending** | `/start`, `/help` and `/lang` only |

`/halt` and `/emergency_stop` act on every account at once, so only the operator can run them.

### Onboarding Flow

1. With no allow-list configured (`TELEGRAM_CHAT_ID`, `ADMIN_TELEGRAM_IDS` and `LIVE_TRADER_TELEGRAM_IDS` all empty), anyone who messages the bot is registered as a paper user.
2. With an allow-list, a newcomer is admitted as a paper user on first contact (any message, `/start` included) while `PAPER_AUTO_ACCEPT` is on (the default).
3. With `PAPER_AUTO_ACCEPT` off, the newcomer is turned away and the operator is notified, once per person.
4. An admin sends `/approve <user_id> [role]` to admit someone by hand: trader by default, or paper, viewer or admin.

### Admin Commands

| Command | Description |
|---------|-------------|
| `/approve <ID> [role]` | Admit a user as trader (default), paper, viewer or admin |
| `/revoke <ID>` | Set a user back to pending |
| `/users` | List registered users with their roles |


---

## Technical Implementation

- **Parse mode:** HTML. If Telegram refuses the HTML, the tags are stripped and the text is resent as plain text.
- **Long replies** are split into chunks under Telegram's message limit.
- **Rate limiting:** 20 requests per minute per user, over a sliding 60-second window.
- **User store:** `data/users.json`, guarded by a `threading.Lock`.
- **Inline keyboards:** callbacks with the `pane:` prefix switch dashboard panes in place.
