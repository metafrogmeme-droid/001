# Agent Hub Alignment

RUNECLAW began as an entry to the Bitget AI Base Camp · Hackathon S1 (Strategy & Risk category). This page maps what it does today against the capability areas of Bitget's agent tooling (GetClaw and the Agent Hub).

---

## Capability Map

| Capability area | What RUNECLAW does | Status |
|---|---|---|
| **Market activity monitoring** | `MarketScanner` (`bot/core/market_scanner.py`) ranks Bitget USDT pairs by momentum and volume, keeping pairs above a volume floor (`MIN_CRYPTO_VOLUME_USD`). The universe is `ASSET_UNIVERSE`, default `all_markets` (spot and USDT-M futures). | Implemented |
| **Portfolio exposure tracking** | `PortfolioTracker` (`bot/risk/portfolio.py`) keeps the paper and practice books: equity, per-symbol and total exposure, drawdown from peak, daily P&L. Live positions and balance are read from the venue (`bot/core/live_executor.py`). | Implemented |
| **Funding rate awareness** | Funding is read as a contrarian order-flow vote (`of_funding`) and a funding-arb confidence nudge. A carry-cost haircut lowers confidence when a trade would pay adverse funding over its hold (`FUNDING_COST_AWARE_ENABLED`, default on). A funding-clock check refuses entering on the paying side of an extreme rate just before settlement (`FUNDING_CLOCK_GATE_ENABLED`, default on). A cross-venue funding radar compares Bitget, Bybit and Hyperliquid (`bot/core/funding_radar.py`). | Implemented |
| **Volatility shift detection** | ADX-based regime classification (TREND_UP / TREND_DOWN / EXPANSION / RANGE / CHOP), regime-aware position sizing (`REGIME_SIZING_ENABLED`, default on), and an ATR volatility guard in the pre-trade risk gate | Implemented |
| **Liquidation risk monitoring** | No liquidation-price monitor. The limits that keep an account away from liquidation: a circuit breaker on daily loss and drawdown (defaults 5% and 10% of equity; 7% drawdown on a live book) and on a losing streak; per-symbol and total margin caps (defaults 20% and 80% of equity); a maximum number of open positions | Implemented |
| **Macro development awareness** | The macro gate (`bot/core/macro_events.py`) blocks new entries around FOMC, CPI, NFP, Core PCE, ISM PMI, PPI, GDP and retail-sales releases, and halves size in the hours before FOMC, CPI and NFP. A blind or stale calendar blocks new entries unless `MACRO_FAILSAFE=false`. | Implemented |
| **Crypto narrative tracking** | No dedicated narrative tracker. The rule engine decides direction and confidence (`LLM_DECIDES` is off by default); a configured model's read rides along on each idea, marked as not used to decide. | Partial |
| **Autonomous decision loop** | A state machine: IDLE → SCANNING → ANALYZING → RISK_CHECK → CONFIRMING → EXECUTING → MONITORING → COOLING_DOWN / HALTED. A live order waits for a confirm unless auto-confirm is on and an eligibility record exists for the running strategy; none ships. See [Enabling Live Trading](live-trading.md). | Implemented |

---

## Interfaces

### MCP

RUNECLAW serves MCP at `POST /mcp` (`app/routes/mcp.js`, at `https://www.humanoid-traders.com/mcp`): read-only and Guardian safety tools, plus paper-only Arena tools behind an Arena key. Ask it `tools/list` for the current catalogue; see [MCP Integration](mcp-integration.md).

| Area | Tools on `POST /mcp` (a sample) |
|---|---|
| Market | `get_signals`, `get_systemic_risk`, `get_dex_compare` |
| Track record | `get_track_record`, `get_proof_of_pnl`, `verify_call` |
| Safety (Guardian) | `scan_transaction`, `xray_transaction`, `stress_portfolio`, `plan_escape` |
| Execution | `arena_open` / `arena_close`, paper only. No tool places a live order. |

### Exchange and HTTP APIs

| Layer | Implementation |
|---|---|
| Exchange REST | Market data from Bitget's public endpoints via ccxt (spot and USDT-M futures). Orders on USDT-M perpetuals via ccxt and a signed Bitget v3 client (`bot/core/bitget_v3_client.py`). Hyperliquid, Bybit and BingX are selectable with `VENUE` (`bot/core/venues.py`). |
| Exchange WebSocket | A Bitget public WebSocket price feed (`bot/core/ws_feed.py`) for position monitoring and order flow. REST polling remains the scanner's source. |
| HTTP APIs | The website API and `POST /mcp` (`app/routes/`), the bot's gateway for the website (`bot/web/`), and `api_bridge.py` |

### Skills, Telegram, Website and CLI

| Interface | Details |
|---|---|
| Skill registry | Skills registered in `build_default_registry()` (`bot/skills/skill_registry.py`); a new skill is a `BaseSkill` subclass |
| Telegram bot | Slash commands with inline-keyboard confirmation; `/help` lists them by group |
| Website | https://www.humanoid-traders.com/ — signed-in members propose and confirm trades through the bot's gateway |
| CLI mode | `python3 -m bot.main --mode cli` |
| Scan mode | One-shot market scan: `python3 -m bot.main --mode scan` |

### Developer Ecosystem

| Asset | Location |
|---|---|
| Source code | [GitHub](https://github.com/metafrogmeme-droid/001) -- BUSL-1.1 (source-available) |
| Documentation | [docs/gitbook on GitHub](https://github.com/metafrogmeme-droid/001/tree/main/docs/gitbook) |
| Tests | The Python and Node suites, gated in CI (`scripts/ci_test_gate.py`; `scripts/preflight.py` runs CI's steps locally) |
| Demo data | `demo/sample_output.json`, `demo/sample_risk_check.json`, `demo/sample_portfolio.json` |
| Backtest engine | Synthetic data (GBM + GARCH) and frozen recorded market datasets (`benchmark/`), with intrabar SL/TP and trailing-stop simulation |

---

## Architecture

```text
 Interfaces: Telegram bot │ Website │ CLI          MCP: POST /mcp (website process)
        │                                          read-only · Guardian · paper Arena
        v
 RUNECLAW ENGINE (bot process)
   Scanner (Bitget) → Analyzer (rule engine; model read rides along)
     → Risk engine (pre-trade gate) → Confirm → Executor
          Executor: practice book (paper accounts) or the live venue
   Context: macro gate · order flow · funding · portfolio tracker
   State machine: IDLE → SCANNING → ANALYZING → RISK_CHECK → CONFIRMING
                  → EXECUTING → MONITORING → COOLING_DOWN / HALTED

 Audit: trade.jsonl │ risk.jsonl │ system.jsonl │ audit_chain.jsonl (hash-chained)
```

---

## Not Built

| Capability | Today |
|---|---|
| Agent-directory registration | MCP is served at `POST /mcp`; it is not registered with any agent directory. `/.well-known/mcp.json` and `agent_card.json` describe it. |
| `bgc` CLI integration | Not built |
| Narrative engine | No structured narrative or news tracking |
| Multi-venue routing | Bitget is the default venue; Hyperliquid, Bybit and BingX are selectable with `VENUE`. Honouring a user's selected venues when routing an order is behind `MULTI_VENUE_TRADING_ENABLED` (default `false`), whose mode defaults to `shadow`: it records the routing decision and executes unchanged. |
