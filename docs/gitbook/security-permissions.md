# Security & Permissions

RUNECLAW handles exchange API keys, a Telegram bot token and optional model-provider keys. This page documents the security model, permission boundaries and operational practices.

---

## Credential Management

### Where Credentials Live

CI runs a gitleaks secret scan (`.github/workflows/ci.yml`). Credentials load from `.env` at startup through `python-dotenv`, and `.env` overrides the process environment. The encrypted secrets vault (`bot/core/secrets_vault.py`, `SECRETS_VAULT_ENABLED`, default on) mirrors the operator's keys from `.env` into `data/`, restores a key that a wiped `.env` lost, and holds keys an admin enters with `/setexchange`. The code review behind the fixes listed below was AI-assisted and internal; no independent third-party audit has been performed.

```text
.env              ← Your credentials (never committed)
.env.example      ← Template with safe defaults (committed)
.gitignore        ← Lists .env to prevent accidental commit
```

### Credential Inventory

| Credential | Variable | Purpose | Required |
|---|---|---|---|
| Bitget API key, secret, passphrase | `BITGET_API_KEY`, `BITGET_API_SECRET`, `BITGET_PASSPHRASE` | Signing orders and account reads | For live trading; market data needs none |
| Other venues | `HYPERLIQUID_WALLET_ADDRESS` + `HYPERLIQUID_PRIVATE_KEY` (an agent wallet's key), `BYBIT_API_KEY` + `BYBIT_API_SECRET`, `BINGX_API_KEY` + `BINGX_API_SECRET` | The same, on the venue `VENUE` names | Only for that venue |
| Telegram bot token | `TELEGRAM_BOT_TOKEN` | Bot authentication | For Telegram mode |
| Telegram allow-list | `TELEGRAM_CHAT_ID` (+ `ADMIN_TELEGRAM_IDS`, `LIVE_TRADER_TELEGRAM_IDS`) | Who may use the bot, and as whom | `TELEGRAM_CHAT_ID` is required for live mode |
| Model provider keys | `LLM_API_KEY`, and per-provider keys such as `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `GROQ_API_KEY` | Model reads and chat | Optional |
| Encryption key | `RUNECLAW_SECRETS_KEY` | Encrypts per-user exchange keys and the secrets vault | Recommended; when unset, a key is generated and stored on the host |
| Website sync secret | `BOT_SYNC_SECRET` | Authenticates the bot's pushes to the website | For website sync |

### Key Rotation

- Rotate API keys regularly, especially after sharing access or suspected exposure
- Revoke keys immediately on the [Bitget API dashboard](https://www.bitget.com/account/newapi) if compromised
- Telegram bot tokens can be revoked via [@BotFather](https://t.me/BotFather) → `/revoke`

---

## Permission Boundaries

### Exchange API Permissions

The market scanner uses **public endpoints only** and passes no credentials for scanning or analysis (`MarketScanner._get_exchange`). Authenticated keys are needed only for live orders and account reads.

| Mode | Required permissions | Recommendation |
|---|---|---|
| Simulation (default) | **None** -- public endpoints, no key needed | Leave the keys empty until you go live |
| Live trading | **Read + Trade** -- account reads and order placement | Enable trade permission only when ready |
| Withdrawal | **Never required** | Never enable withdrawal permissions |

On Hyperliquid, use an agent (API) wallet's key. The venue adapter refuses a key whose address is the main wallet itself (`bot/core/venues.py`).

> **Best practice:** Create a dedicated API key for RUNECLAW with the minimum permissions needed. Never reuse keys across applications.

### Telegram Authorization

Access is decided by the allow-list and by each user's role (`TelegramHandler._is_allowlisted` in `bot/skills/telegram_handler.py`, and `ROLE_PERMISSIONS` in `bot/utils/user_store.py`):

```text
Allow-list set (TELEGRAM_CHAT_ID / ADMIN_TELEGRAM_IDS / LIVE_TRADER_TELEGRAM_IDS)
  → listed IDs are let in; TELEGRAM_CHAT_ID IDs are seeded as admins
  → anyone else is admitted as `paper` on first contact (any message,
    /start included) while PAPER_AUTO_ACCEPT is on (default true), or
    else refused until an admin runs /approve
No allow-list (all three empty)
  → anyone can use the bot; a newcomer is admitted as `paper`
  → live mode refuses to arm without TELEGRAM_CHAT_ID
```

| Role | How a user gets it | What it can do |
|---|---|---|
| `admin` | Listed in `TELEGRAM_CHAT_ID` (seeded as admin), or `/approve <id> admin` | Every command, including the operator's controls |
| `trader` | An admin's `/approve <id>` (the default role it grants) | Trading commands. `/halt` is refused; `/reset` and `/resume` clear only the trader's own risk engine, which exists only with per-user live trading on |
| `paper` | Self-admitted on first contact (with an allow-list set, only while `PAPER_AUTO_ACCEPT` is on) | Trading commands on their own practice book; no `/halt`, `/reset` or `/mode` |
| `viewer` | `/approve <id> viewer` | Read commands, plus linking their own exchange keys (`/connect`) |
| `pending` | Revoked by an admin's `/revoke` | At most `/start`, `/help` and `/lang` |

A `paper` user's confirm opens on their own practice book (`RuneClawEngine.confirm_is_practice`). A live order is placed only for the operator, on the operator's account, or, with `PER_USER_LIVE_ENABLED` on (default `false`), for a trader on their own exchange account linked with `/connect` (`TelegramHandler._can_trade_live`). A web-only account is always paper. An admin's `/revoke_live` restricts a user to paper.

Every card shows a person their own book. Nobody but the operator (and an admin) sees the operator's account, with per-user live trading on or off: on a live bot, a caller with no live account of their own sees their practice book on every card and in the chat (`RuneClawEngine.viewer_executor`).

A `/revoke` holds. While `PAPER_AUTO_ACCEPT` is on, a newcomer is admitted on first contact, but an account an admin revoked is not admitted again that way; only an admin's `/approve` lifts the revocation (`UserStore.authorize`).

Authorization applies to slash commands and to inline-keyboard callbacks (confirm and reject buttons). A button pressed by someone other than the user it was sent to is refused.

### MCP

The public `POST /mcp` serves its read-only and Guardian safety tools without authentication, and its paper-only `arena_*` tools only with an Arena key (`Authorization: Bearer rcarena_…`). No tool can place a live order, read an exchange credential or read another account's private data. See [MCP Integration](mcp-integration.md).

### Model Provider Data

- The model key is optional. The rule engine decides direction and confidence (`LLM_DECIDES` is off by default); without a key, the model's read is simply absent.
- The analysis prompt carries indicator values and market context.
- Chat sends what a user types, and the account readings the chat model asks for (the caller's portfolio, open orders, risk state; `bot/nlp/chat_tools.py`), to the configured provider.
- The website's public chat and the MCP `ask_runeclaw` tool send a visitor's question, with no account data.
- Calls are billed by your provider. The bot books an estimated cost per call against a daily budget (`LLM_DAILY_BUDGET_USD`) and stops asking the model when it is spent.

---

## Data Flow Security

### What data leaves the system

| Destination | Data sent | Purpose |
|---|---|---|
| Exchange (Bitget, or the venue in use) | Public market-data requests, with no credentials; signed order and account requests in live mode | Market data, orders |
| Telegram API | Bot token and message content | Scan results, trade ideas, confirmations |
| Model provider(s) | Indicators and market context; chat messages and the readings the chat model asks for | The model's read, chat |
| Your RUNECLAW website (`WEBSITE_URL`) | Portfolio state, trade events with equity, scans, signals and decision records (`bot/utils/website_sync.py`) | The website's dashboards |

### What data stays local

| Data | Storage | Retention |
|---|---|---|
| Portfolio state | `data/portfolio_state.json` (`PORTFOLIO_STATE_FILE`) | Persistent, auto-saved |
| Risk engine state | `data/risk_state.json` | Persisted for the circuit breaker |
| Users and roles | `data/users.json` | Persistent |
| Audit logs | `logs/*.jsonl` | Each channel rotates at 10 MB and keeps five old files |
| Tamper-evident record | `logs/audit_chain.jsonl` | Hash-chained (`bot/utils/audit_chain.py`) |
| Secrets vault and per-user exchange keys | `data/` | Encrypted; the key is kept on the same host (see Known Limitations) |

### API keys

- The exchange API secret. It signs requests and is never sent. The key and passphrase go only to the venue, over HTTPS, as its authentication headers (`bot/core/bitget_v3_client.py`).
- Keys stored through `/connect` or `/setexchange` are encrypted at rest; keys in `.env` are plaintext on disk.

---

## Thread Safety

These components lock their shared state:

| Component | Lock | Purpose |
|---|---|---|
| `PortfolioTracker` | `_lock` (`threading.RLock`) | Position lifecycle, balance updates |
| `RiskEngine` | `_lock` (`threading.RLock`) | Circuit breaker state, loss tracking |
| `MarketScanner` | `_lock` (`threading.RLock`) | Volume history, signal cache |
| `RateLimiter` (`bot/skills/chat_runtime.py`) | `_lock` (`threading.Lock`) | Per-user rate counters |

Scan batches run on a separate thread with their own event loop (`bot/core/scan_lane.py`), and website sync posts from background threads, so these locks are load-bearing.

---

## Rate Limiting

### Telegram

| Parameter | Default | Behavior |
|---|---|---|
| `rate_limit_per_minute` (`TelegramConfig`, not an environment variable) | 20 | Sliding one-minute window per user ID |
| Exceeded | -- | Command refused with a warning |

### MCP

`POST /mcp` allows 60 requests a minute per IP address.

### Bitget API

The scanner respects Bitget's rate limits by:
- Fetching tickers in bulk (one call for all pairs)
- Using ccxt's built-in rate limiter (`enableRateLimit`)

Market data always comes from production endpoints, even when orders run in Bitget demo trading (`BITGET_SANDBOX=true`).

---

## Audit & Compliance

Security-relevant actions are logged as structured JSON with UTC timestamps (`bot/utils/logger.py`):

| Event | Log channel | `action` → `result` |
|---|---|---|
| Risk verdict on an idea or a confirm | `risk.jsonl` | `"risk_check"` → `APPROVED` / `REJECTED`, with the passed and failed checks |
| Circuit breaker tripped | `risk.jsonl` | `"circuit_breaker"` → `HALTED` |
| Circuit breaker reset | `risk.jsonl` | `"circuit_breaker"` → `RESET` (manual) / `AUTO_RESET` |
| Practice fill | `trade.jsonl` | `"paper_fill"` → `FILLED` |
| Live order | `trade.jsonl` | `"live_execute"` → `FILLED`, `LIMIT_PLACED`, or the reason it was blocked |
| Confirm refused | `trade.jsonl` | `"confirm"` → `VETO_SIMULATION` / `REJECT_NOT_ELIGIBLE` / `REJECT` |
| Auto-confirm | `trade.jsonl` | `"auto_confirm"` → `TRIGGERING` / `DONE` / `SUPPRESSED_LIVE` |
| `/halt` | `system.jsonl` | `"halt"` → `HALTED` |
| `/golive` | `system.jsonl` | `"golive"` → `ENABLED` / `REFUSED` / `DISABLED` |
| New user registered | `system.jsonl` | `"user_auto_approve"` → `OK` |
| Telegram button | `system.jsonl` | `"telegram_callback"` |

A command refused by the role check, and a rate-limited command, get a reply and no audit entry. A refused operator control (`/halt`, `/reset`, `/mode`) is audited as `control_denied`. The `*.jsonl` channels rotate at 10 MB and keep five old files. The tamper-evident record is `logs/audit_chain.jsonl`: each entry is hash-chained to the one before, so an edit, deletion or reorder breaks `verify()`.

---

## Deployment Checklist

Before deploying RUNECLAW, even in simulation mode:

- [ ] `.env` created from `.env.example`, with your credentials
- [ ] `.env` is listed in `.gitignore` (it is)
- [ ] `TELEGRAM_CHAT_ID` set (live mode refuses without it; with it, `ADMIN_TELEGRAM_IDS` and `LIVE_TRADER_TELEGRAM_IDS` all empty, anyone can use the bot)
- [ ] `PAPER_AUTO_ACCEPT` decided: on (the default) admits any newcomer for paper trading
- [ ] `RUNECLAW_SECRETS_KEY` set
- [ ] `SIMULATION_MODE=true` (default)
- [ ] `LIVE_TRADING_ENABLED=false` (default)
- [ ] `AUTO_CONFIRM_LIVE_ENABLED=false` unless you mean autonomous live orders (code default `true`; the shipped `.env.example` sets `false`)
- [ ] `BITGET_SANDBOX=true` only with keys created in Bitget demo trading (live keys fail there with error 40099)
- [ ] Exchange key: none needed in simulation; trade permission only when going live; never withdrawal
- [ ] No secrets in git history (`git log --all -p | grep -i "api_key"`)
- [ ] `logs/` and `data/` are writable and persist across redeploys

---

## Known Limitations

| Limitation | Impact | Mitigation |
|---|---|---|
| Plaintext state | Audit logs, portfolio and risk state are plaintext JSON; keys in `.env` are plaintext | Deploy on an encrypted volume. Keys stored through `/connect` or `/setexchange`, and the vault's copy of `.env` keys, are encrypted |
| Encryption key on the same host | The master key is stored on the same host as the encrypted data, whether it was generated or set with `RUNECLAW_SECRETS_KEY`, so the encryption protects a leaked copy of the data files, not a compromised host | Protect `data/` and the host as you would the keys themselves |
| No TLS pinning | Standard HTTPS to the venue, Telegram and model providers | Relies on the system certificate store |

---

## Security Hardening Log (internal review v3.0)

Fixes from an internal, AI-assisted review (v3.0). No independent third-party audit has been performed.

### Critical Fixes

| ID | Issue | Fix |
|----|-------|-----|
| C1 | Frozen dataclass mutation via `object.__setattr__` | Created thread-safe `RuntimeState` class; `/mode` command and scanner read from `RUNTIME` instead of mutating frozen CONFIG |
| C3 | No log redaction — API keys could appear in logs/tracebacks | Added `_redact_dict()` and `_redact_string()` in logger.py; regex scrubs sensitive keys and inline secrets before write |
| C5 | MCP adapter exposed tools without authentication | `bot/mcp/server.py` refuses to start without `MCP_AUTH_TOKEN` and checks it on every call (`hmac.compare_digest`). This in-process adapter has no HTTP door; the public `POST /mcp` is `app/routes/mcp.js` |

### Warning Fixes

| ID | Issue | Fix |
|----|-------|-----|
| W1 | CostTracker never reset daily — `llm_cost_usd` accumulated forever | Added UTC day boundary detection; daily auto-reset with separate `snapshot_lifetime()` for cumulative stats |
| W5 | Cache key truncated to 16-char SHA-256 — collision risk | Changed to full 64-char SHA-256 hex digest |
| W6 | Walk-forward backtest leaked temp directories | Added explicit `cleanup()` calls after each fold's train and test engines |

### Hardening

| Area | Change |
|------|--------|
| Input validation | `/approve` rejects non-numeric Telegram IDs; `/analyze` rejects non-alphanumeric symbols via regex whitelist |
| Encapsulation | Risk engine uses `portfolio.get_position_value()` public API instead of accessing private `_last_prices` |
| Financial disclaimer | Pro-scan cards carry a "Not financial advice" notice |
| Portfolio corruption | `load_state()` logs at CRITICAL level on corrupted state files instead of silent fallback |
| Traceback redaction | The in-process MCP adapter redacts tracebacks before logging via `_redact_string()` |

### Security Test Suite

`tests/test_security.py` covers:
- Log redaction (dict scrubbing, inline secrets, nested values, depth limits)
- MCP adapter auth (no token, wrong token, refusing to start without `MCP_AUTH_TOKEN`)
- RuntimeState (valid and invalid modes, thread safety)
- Cache key collision prevention (full SHA-256 length)
- CostTracker daily reset and lifetime accumulation
- Portfolio corruption handling and public API
- Backtest temp directory cleanup
- Input validation regex (symbol injection, Telegram ID format)
