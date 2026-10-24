# Getting Started

This guide sets up RUNECLAW from a fresh clone. The live bot is [@HTRUNECLAW_bot](https://t.me/HTRUNECLAW_bot) and the website is https://www.humanoid-traders.com/ ; this page is for running your own copy.

## Prerequisites

- Python 3.11 or newer
- A Telegram bot token (from [@BotFather](https://t.me/BotFather)) for Telegram mode
- Optional: Bitget API keys. Market data is public and needs no keys; keys are only needed to trade live.
- Optional: an API key for any supported LLM provider. Trades are decided by the rule engine with or without one.

## Installation

```bash
# Clone the repository
git clone https://github.com/metafrogmeme-droid/001.git RUNECLAW
cd RUNECLAW

# Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate   # Linux/macOS
.venv\Scripts\activate      # Windows

# Install dependencies
pip install -r requirements.lock
```

## Configuration

Copy the example environment file and fill in your values:

```bash
cp .env.example .env
```

The settings most people set first:

| Variable | Needed for | Description |
|----------|------------|-------------|
| `TELEGRAM_BOT_TOKEN` | Telegram mode | Bot token from BotFather. Telegram mode stops at startup without it. |
| `TELEGRAM_CHAT_ID` | Live trading | The operator's Telegram chat ID(s), comma-separated, usually your own user ID. These IDs are given the admin role at startup. Live trading will not arm without it, and it starts the allow-list (see below). |
| `ADMIN_TELEGRAM_IDS` | Optional | Further admin IDs, comma-separated. They join the allow-list and pass the admin checks on commands such as `/approve` and `/users`. |
| `BITGET_API_KEY` | Live trading | Bitget API key (market data needs none) |
| `BITGET_API_SECRET` | Live trading | Bitget API secret |
| `BITGET_PASSPHRASE` | Live trading | Bitget API passphrase |
| `LLM_PROVIDER` | Optional | `openai`, `anthropic`, `gemini`, `groq` and others. Code default `openai`; `.env.example` sets `anthropic`. |
| `LLM_API_KEY` | Optional | The provider's key. With it, the model's read rides beside each decision (marked as not used to decide, since `LLM_DECIDES` is off by default) and powers chat. Spend stops at `LLM_DAILY_BUDGET_USD` (code default 1.0, `.env.example` 5.0). |
| `LLM_MODEL` | Optional | Leave empty to use the provider's default model. |

**Who can use your bot.** `TELEGRAM_CHAT_ID`, `ADMIN_TELEGRAM_IDS` and `LIVE_TRADER_TELEGRAM_IDS` together make up the allow-list. While any of them is set, everyone else gets in either through an admin's `/approve` or as a paper user on first contact (any message, `/start` included), while `PAPER_AUTO_ACCEPT` is on (the default). If all three are empty there is no allow-list and anyone who finds the bot can use it as a paper user.

**Keep the copied auto-confirm lines.** `.env.example` lists the settings. `AUTO_CONFIRM_THRESHOLD=1.0` departs from the code default of `0.85`, so the bot's own ideas wait for a person. `AUTO_CONFIRM_LIVE_ENABLED=false` matches the code default: a live order with no human press stays off until the operator sets the flag. `BITGET_SANDBOX=true` and `HYPERLIQUID_TESTNET=true` also depart: the code defaults are production and mainnet, so a deployment that loses the variable is not flipped, and a fresh copy of this file uses demo and testnet. See [Telegram Confirmation Flow](telegram-confirmation.md).

New installs do not trade live: `SIMULATION_MODE=true` and `LIVE_TRADING_ENABLED=false` in both the code and `.env.example`. In that mode no order reaches an exchange. A self-admitted paper user's confirms open on their own practice book, which starts at `PAPER_BALANCE_USD` (default 10000). With `PAPER_SIM_OPT_IN_ENABLED` on (default off), anyone can opt in with `/paper on`. Any other confirm is refused; see [Paper Trading](paper-trading.md). [Enabling Live Trading](live-trading.md) covers going live.

## Running

`bot.main` has four modes: `telegram` (the default), `cli`, `scan` and `backtest`. Docker Compose runs the whole stack.

### Telegram Mode (default)

```bash
python3 -m bot.main
# same as: python3 -m bot.main --mode telegram
```

This starts the bot, the trading engine and the bot's gateway on port 8080. Open your bot in Telegram and send `/help`.

Several of the website's panels and REST endpoints are served by a second process, the API bridge (uvicorn on port 8000). FastAPI and uvicorn are pinned in `requirements.lock`, which the install step above installs:

```bash
python3 api_bridge.py
```

A deployment that starts only one of the two processes is not complete. `scripts/launch_all.sh.template` (copy it out of the repository before use) starts both and checks that each stays up.

### CLI Mode

Interactive testing without Telegram. No bot token needed.

```bash
python3 -m bot.main --mode cli
```

You get a `runeclaw>` prompt that lists the available skills. Type a skill name to run it:

```
runeclaw> scan_market
runeclaw> analyze_asset BTC
runeclaw> get_portfolio
runeclaw> quit
```

The CLI runs its own copy of the engine. Nothing it does is saved, and nothing typed here reaches a running bot.

### Scan Mode

One market scan that prints its results and exits:

```bash
python3 -m bot.main --mode scan
```

### Backtest Mode

Replays 720 synthetic 1-hour bars (30 days) of BTC/USDT and prints the result summary:

```bash
python3 -m bot.main --mode backtest
```

The bars are generated, not market history, so the result says nothing about how the strategy trades real markets.

### Docker Compose

```bash
docker compose up -d
```

Compose starts four services: the bot (Telegram mode, so `TELEGRAM_BOT_TOKEN` is required), the API bridge on port 8000, Redis, and nginx. `.env` must set `REDIS_PASSWORD`, and, while the nginx service is in the file, `RUNECLAW_DOMAIN`; nginx also needs TLS certificates for that domain. For local use, comment out the nginx service, as the comment in `docker-compose.yml` says. `./data` (portfolio and breaker state) and `./logs` (the JSONL audit logs) are mounted from the host and survive restarts.

## Verifying the Setup

1. Run `python3 -m bot.main --mode cli` and type `get_portfolio`.
2. On a fresh install, Equity and Available equal `PAPER_BALANCE_USD` (default 10000) and there are no open positions.
3. Type `check_risk`. On a fresh install the breaker line reads `CLEAR`.
4. Type `quit` to exit.

If you see the RUNECLAW banner and the portfolio card, the install works.

## Next Steps

- Read the [Architecture](architecture.md) overview to see how the pieces fit together.
- See [Skills & Commands](skills-and-commands.md) for the core commands and who may run them.
- Review the [Risk Framework](risk-framework.md) before generating trade ideas.
