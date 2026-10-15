# Skills & Commands

RUNECLAW is built from skills. Most capabilities are registered as skills in `bot/skills/skill_registry.py`, and Telegram commands, the CLI and the website's chat call the same skills.

**Try it live:** [@HTRUNECLAW_bot](https://t.me/HTRUNECLAW_bot)

## Telegram Commands

The table below lists the core commands. The bot has many more: `/help` lists every command by group (the operator groups appear only to admins), and the README's [command list](https://github.com/metafrogmeme-droid/001/blob/main/README.md#telegram-commands) is generated from `bot/skills/command_catalog.py`, which a test holds to the commands the bot registers.

"Viewer+" means viewer, paper, trader and admin; "Paper+" means paper, trader and admin.

### Getting started

| Command | Description | Access |
|---------|-------------|--------|
| `/start` | Register and see where things stand | Everyone |
| `/help` | Every command, by group | Everyone |
| `/lang` | Switch the interface language | Everyone |
| `/status` | Engine status, with the main menu | Viewer+ |
| `/dashboard` | The Status / Risk / Portfolio / Scan pane card | Viewer+ |
| `/connect` | Link your own exchange account; the message with your keys is deleted at once | Viewer+ |

### Scan and analyse

| Command | Description | Access |
|---------|-------------|--------|
| `/scan` | Scan the market for setups. `/scan <venue>` reads one venue's own market list; `/scan venues` lists them. | Viewer+ |
| `/analyze <SYMBOL>` | Analysis of one asset, with an idea card when there is a setup | Paper+ |
| `/latest_signal` | Pending signals with action buttons | Viewer+ |
| `/macro` | Macro event calendar; `/macro brief` shows the macro gate's posture | Viewer+ |
| `/whynot` | Why a trade was rejected | Paper+ |

### Trading and record

| Command | Description | Access |
|---------|-------------|--------|
| `/trade buy <SYMBOL> <entry> sl <stop> tp <target>` | Stage a manual trade (`buy` or `sell`), for example `/trade buy SOL 71.42 sl 70.05 tp 76.42`. Nothing is placed until you tap Confirm. | Paper+ |
| `/portfolio` | Equity, positions and win rate. In live mode, your own linked account. | Viewer+ |
| `/risk` | Risk status, circuit breaker and exposure gauges | Viewer+ |
| `/rejected` | Signals the risk gate turned down, with the failed checks | Paper+ |
| `/watch` | Proactive alerts for this chat | Viewer+ |

### Research

| Command | Description | Access |
|---------|-------------|--------|
| `/backtest` | Run a backtest | Paper+ |
| `/learn` | What the engine has learned | Viewer+ |
| `/proposals` | Pending tuning proposals | Paper+ |
| `/optimize` | LLM token optimizer stats: cache hit rate, tier distribution | Paper+ |

When the operator turns on tier gating (`TIER_GATE_ENABLED`, or the token gate `TOKEN_TIER_GATE_ENABLED` with a mint; both off by default), `/analyze` needs the pro tier and `/backtest`, `/optimize` and `/learn` need elite.

### Engine controls

| Command | Description | Access |
|---------|-------------|--------|
| `/pause`, `/resume` | Pause or resume trading. The operator acts on every account; a trader acts on their own account when per-user live trading is on, and is refused otherwise. | Trader+ |
| `/reset` | Reset the circuit breaker, with the same scope as `/resume` | Trader+ |
| `/halt` | Halt the engine for every account | Operator |
| `/emergency_stop` | Stop everything; asks first, and on a live deployment also closes open live positions | Operator |

### Operator

| Command | Description | Access |
|---------|-------------|--------|
| `/approve <ID> [role]` | Admit a user: trader by default, or paper, viewer or admin | Admin |
| `/revoke <ID>` | Set a user back to pending | Admin |
| `/users` | List registered users and their roles | Admin |
| `/autoconfirm` | View or set the auto-confirm threshold | Admin |
| `/costs` | Today's model and API spend against the daily budget and chat's cap | Admin |
| `/patterns` | The learning store's closes by regime, with win rates and their intervals | Admin |

## Role-Based Access

| Role | Access Level |
|------|-------------|
| **Admin** | Every command, including the operator groups |
| **Trader** | Vouched for by an admin's `/approve`: analysis, scans, trading, research, and the scoped engine controls above |
| **Paper** | Self-admitted: the trader commands without `/reset`, `/pause`, `/resume`, `/mode` or `/stake`. Their confirms open on their own practice book. |
| **Viewer** | Read commands (status, risk, portfolio, scans, macro, learning), plus linking their own exchange keys (`/connect`) |
| **Pending** | `/start`, `/help` and `/lang` only |

"Operator" on this page means an admin, or the chat named in `TELEGRAM_CHAT_ID`.

With no allow-list configured, anyone who messages the bot is registered as a paper user. With one (`TELEGRAM_CHAT_ID`, `ADMIN_TELEGRAM_IDS` or `LIVE_TRADER_TELEGRAM_IDS` set), a newcomer is admitted as a paper user on their first command while `PAPER_AUTO_ACCEPT` is on (the default); otherwise the operator is notified and an admin admits them with `/approve <user_id> [role]`.

## AI Chat

Free text that is not a command goes first to a rule-based intent router: a typed trade opens a confirm card, and phrases such as "scan BTC" run the matching skill. Anything else goes to the chat model, which follows the deployment's chat routing and cannot place, change or close a trade. See [Telegram Dashboard & AI Chat](v5-dashboard.md).

## Dashboard Navigation

`/dashboard` opens the pane card with four buttons:

- **Status**: breaker, mode, macro state, capital, positions
- **Risk**: gauges for drawdown, exposure and loss streak, plus the limits
- **Portfolio**: balance, P&L waterfall, open positions
- **Scan**: the latest market scan

The buttons edit the same message, so the chat stays clean. `/status` sends the engine-status card with the main menu instead.
