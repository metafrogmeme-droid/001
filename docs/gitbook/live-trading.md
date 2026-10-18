# Enabling Live Trading

RUNECLAW ships in **simulation mode**. `SIMULATION_MODE` defaults to `true` and `LIVE_TRADING_ENABLED` to `false`, in the code and in `.env.example`, so no order reaches an exchange until an operator arms live mode. This page is for an operator running their own bot.

> **Warning:** RUNECLAW began as an entry to the Bitget AI Base Camp · Hackathon S1 and is early-stage software. It trades live with small positions, and no independent third-party security audit has been performed. Live trading with real funds is at your own risk. Arm live mode only if you understand the risks, have watched the bot on a practice book, and accept responsibility for any losses.

---

## How Live Mode Arms

A live order needs all of these (`AppConfig.is_live` in `bot/config.py`, and the readiness report in `bot/core/live_readiness.py`):

| Requirement | Setting | Default |
|---|---|---|
| Simulation off | `SIMULATION_MODE=false`. While it is `true`, the engine vetoes every live order, whatever else is set. | `true` |
| Live armed | `LIVE_TRADING_ENABLED=true` in `.env`, or an admin's `/golive CONFIRM` at runtime | `false` |
| A Telegram chat allow-list | `TELEGRAM_CHAT_ID`. Live mode refuses to arm while it is empty. | empty |
| Venue credentials | `BITGET_API_KEY`, `BITGET_API_SECRET`, `BITGET_PASSPHRASE` on the default venue (`VENUE=bitget`) | empty |

What each combination of the two flags does:

| `SIMULATION_MODE` | `LIVE_TRADING_ENABLED` | Result |
|---|---|---|
| `true` | `false` | Simulation (the default). No live order; `/golive CONFIRM` refuses. |
| `true` | `true` | The same. `SIMULATION_MODE` vetoes every live order. |
| `false` | `false` | Idle. No live order until an admin arms live with `/golive CONFIRM`. |
| `false` | `true` | **Live**, once `TELEGRAM_CHAT_ID` is set and the venue accepts the credentials. |

No single flag arms live mode: `SIMULATION_MODE=false` also needs `LIVE_TRADING_ENABLED=true` or an admin's `/golive CONFIRM`, and a chat allow-list.

The bot places no paper trade on its own book. Outside live mode, a confirm from the operator or a trader is refused. In every mode, a confirm from a `paper` account, or from a user in practice mode, opens on that user's own practice book and never reaches the exchange (`RuneClawEngine.confirm_is_practice`).

---

## Step-by-Step Activation

### 1. Practice first

Watch the bot before you arm it. A `paper` account (a newcomer admitted on first contact; see [Security & Permissions](security-permissions.md)) confirms onto its own practice book. With `PAPER_SIM_OPT_IN_ENABLED=true` (default `false`), any user can switch their own confirms to practice with `/paper on`. Check that:

- Trade ideas are sensible and match your risk tolerance
- The risk gate rejects what you expect it to (`logs/risk.jsonl` names each decision's passed and failed checks)
- Drawdown, win rate and P&L on the practice book are within bounds
- The macro gate blocks new entries around high-impact events

### 2. Configure exchange credentials

```bash
# .env
VENUE=bitget
BITGET_API_KEY=your_api_key
BITGET_API_SECRET=your_api_secret
BITGET_PASSPHRASE=your_passphrase
BITGET_SANDBOX=false   # true only with keys created in Bitget demo trading
```

Market data needs no key: scanning and analysis use Bitget's public endpoints, always on production, even when orders run in Bitget demo trading (`BITGET_SANDBOX=true`). Give the key trade permission and never withdrawal permission. An admin can also enter the keys with `/setexchange`, which stores them encrypted in the secrets vault (`bot/core/secrets_vault.py`).

### 3. Set risk limits

Review the limits before going live. A tighter example, with each code default in brackets:

```bash
# .env
MAX_POSITION_PCT=5.0              # margin per trade, % of equity [13]; see below
MAX_DAILY_LOSS_PCT=3.0            # daily-loss breaker [5]
LIVE_MAX_DRAWDOWN_PCT=5.0         # drawdown breaker on a live book [7]
MAX_OPEN_POSITIONS=3              # concurrent positions [5]
MAX_PORTFOLIO_EXPOSURE_PCT=50.0   # total margin, % of equity [80]
MAX_SYMBOL_EXPOSURE_PCT=10.0      # margin on one symbol, % of equity [20]
```

- **`MAX_POSITION_PCT` is a margin cap**, not a risk budget. It bounds the margin one trade commits, as a percent of equity, at `SIZING_LEVERAGE` (default 5); the notional is that margin times the sizing leverage. With `PER_STRATEGY_NOTIONAL_CAP_ENABLED` on (the default), each strategy type has its own cap instead: `SCALP_MAX_POSITION_PCT` (8), `INTRADAY_MAX_POSITION_PCT` (10), `SWING_MAX_POSITION_PCT` (13) and `POSITION_MAX_POSITION_PCT` (15). `MAX_POSITION_PCT` then applies only to a strategy type with no cap of its own. To tighten every trade, lower the per-type caps or set `PER_STRATEGY_NOTIONAL_CAP_ENABLED=false`.
- **The live drawdown breaker reads `LIVE_MAX_DRAWDOWN_PCT`**, not `MAX_DRAWDOWN_PCT`. While `LIVE_RISK_HARDENING_ENABLED` is on (the default) and the bot is live, the drawdown limit is `LIVE_MAX_DRAWDOWN_PCT` (default 7); `MAX_DRAWDOWN_PCT` (default 10) is the limit off live. An admin can change the live limit at runtime with `/drawdownlimit <pct>` and restore it with `/drawdownlimit off`; the override is bounded and never disables the breaker.

### 4. Arm live mode

```bash
# .env
SIMULATION_MODE=false
LIVE_TRADING_ENABLED=true
TELEGRAM_CHAT_ID=your_chat_id
AUTO_CONFIRM_LIVE_ENABLED=false   # keep a human confirm on every live order
```

Restart the bot. Send `/golive` (admin) to read the readiness report: what blocks a live order (simulation mode on, no chat allow-list, missing credentials, a failing venue login) and what is unverified (credentials no authenticated call has tested yet). Once armed, `/livebalance` makes an authenticated call and shows the venue's own answer.

To arm without `LIVE_TRADING_ENABLED`, leave it `false` and send `/golive CONFIRM`. It refuses while any blocker remains, and the arm lasts until a restart. `/golive OFF` turns live off whatever the environment says: every confirm a person taps opens on the paper book, it holds across restarts (`data/live_suspended.json`), and it lasts until the next `/golive CONFIRM`. It is refused while a live position or resting entry is open, so close those first. With `LIVE_ADMIN_ONLY` on (the default), only an operator or admin ever trades live; every other member's confirm opens on their own paper book.

### 5. Monitor closely

- Watch Telegram, or the website, for trade proposals, and confirm or reject each one
- Use `/risk` and `/status` to watch exposure and the breaker
- Use `/halt` (operator) at once if anything looks wrong

**Human confirmation.** A live order needs a confirm, a tap in Telegram or on the website, unless the operator enables auto-confirm (`AUTO_CONFIRM_LIVE_ENABLED` with `AUTO_CONFIRM_THRESHOLD`). Both are off in the shipped `.env.example` (`false` and `1.0`) and on in the code default when no `.env` sets them (`true` and `0.85`). Even with auto-confirm on, a live order nobody confirmed is placed only when a committed eligibility record exists for the running strategy (`benchmark/eligibility/<strategy_hash>.json`, read by `bot/core/live_eligibility.py`), and none ships. Auto-confirmed trades run on the operator account; a regular user's trade is never auto-confirmed. An admin can read or change the threshold at runtime with `/autoconfirm` (`/autoconfirm off` sets it to 1.0).

---

## Safeguards That Remain Active in Live Mode

| Safeguard | Behavior |
|-----------|----------|
| Pre-trade risk gate | Runs on every idea and again on every confirm, in Telegram or on the website. The checks that carry the money reject a trade they cannot evaluate. The liquidity guard is fail-open, and advisory checks are skipped, counted with the passes, when their data is missing; each skip names itself on the decision record (`checks_passed` / `checks_failed`). |
| Confirmation | A confirm in Telegram or on the website, unless `AUTO_CONFIRM_LIVE_ENABLED` is on and an eligibility record exists for the running strategy (none ships). |
| Circuit breaker | Halts new entries on a daily-loss breach (`MAX_DAILY_LOSS_PCT`), a drawdown breach (`LIVE_MAX_DRAWDOWN_PCT` on a live book), or a losing streak (`MAX_CONSECUTIVE_LOSSES`, default 5). |
| Cooldown | Pauses new entries for `COOLDOWN_AFTER_LOSS_SEC` (default 120 seconds) after a losing trade. |
| Stale data guard | Rejects an idea older than `STALE_DATA_MAX_AGE_SEC` (default 300 seconds; twice that for a limit order). |
| Macro event gate | Blocks new entries from 60 minutes before an FOMC decision to 30 minutes after, from 30 before to 15 after CPI and NFP, and from 15 before to 10 after Core PCE, ISM PMI, PPI, GDP and retail sales. Size is halved from 180 minutes before FOMC and 120 before CPI and NFP. A blind or stale calendar blocks new entries unless `MACRO_FAILSAFE=false`. |
| Volatility guard | Rejects a trade when ATR is above the volatility threshold, or cannot be read. |
| Audit logging | Risk verdicts, fills and refusals are written as structured JSON to `logs/trade.jsonl`, `logs/risk.jsonl` and `logs/system.jsonl`; decisions are also sealed in the hash-chained `logs/audit_chain.jsonl`. |

---

## Emergency Shutdown

If anything goes wrong in live mode:

1. Send `/halt` via Telegram (operator only). It trips the circuit breaker on the shared engine and on every per-user engine, cancels pending ideas and moves the engine to HALTED. Open positions stay open, with their stops and targets still managed; `/closeall` (admin, behind a confirm button) closes every position on every account.
2. Once the book is flat, send `/golive OFF` (it is refused while a live position or resting entry is open), or set `SIMULATION_MODE=true` in `.env` and restart.
3. Revoke the API key's trade permission on the exchange.
4. Review `logs/trade.jsonl` and `logs/risk.jsonl`.

A drawdown, loss-streak or manual trip stays latched until an operator runs `/reset` or `/resume`; with per-user live trading on, a trader's `/reset` clears only their own engine. A daily-loss trip clears itself at the UTC day rollover by default (`DAILY_LOSS_BREAKER_AUTORESET`, default `true`; set it to `false` to keep it manual), and a bad new day trips it again. A loss-streak trip clears itself only when `STREAK_BREAKER_AUTORESET_HOURS` is above 0 (default 0, off).

---

## Reverting to Simulation Mode

```bash
# .env
SIMULATION_MODE=true
LIVE_TRADING_ENABLED=false
```

Restart the bot. No live order is placed after the restart. Positions already open on the exchange are not closed by the switch: close them first (`/closeall`), or manage them on the exchange.

---

## Per-User Live Trading

With per-user live trading on (`PER_USER_LIVE_ENABLED`, default `false`), a trader links their own exchange key with `/connect`, and their confirmed live trades run on their own account. The key message is deleted and the key is stored encrypted.
