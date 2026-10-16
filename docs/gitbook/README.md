# Introduction

**AI Trading Command Core | Forged in Volatility. Governed by Discipline.**

*by Humanoid Traders · began as an entry to the Bitget AI Base Camp · Hackathon S1*

Welcome to the official RUNECLAW documentation. It is on the website at
[humanoid-traders.com/docs](https://www.humanoid-traders.com/docs) and in the project's repository on GitHub.

RUNECLAW is a risk-first AI trading assistant. It scans Bitget markets for opportunities, builds trade ideas from technical indicators with a rule engine (a configured LLM's read rides along and decides nothing unless `LLM_DECIDES` is on), applies macro and volatility context, enforces strict risk controls, and requires human confirmation before any trade can proceed unless the operator enables auto-confirm (`AUTO_CONFIRM_LIVE_ENABLED` with `AUTO_CONFIRM_THRESHOLD`) — both off in the shipped `.env.example`, on in the code default when no `.env` is present. Even then, a live order nobody confirmed is placed only when a committed eligibility record exists for the running strategy, and none ships.

Out of the box it places no real order: live trading stays off until the operator arms it (see [Enabling Live Trading](live-trading.md)). It is built around safety, transparency, explainability, and disciplined execution.

> **RUNECLAW is not another hype bot. It is a survival-first AI trading agent.**

---

## Overview

RUNECLAW supports the full trading decision loop:

```text
Market Data
 ↓
Technical Indicators
 ↓
Rule-Engine Trade Idea (LLM read alongside)
 ↓
Macro / Volatility / Risk Context
 ↓
Fail-Closed Risk Gate
 ↓
Human Confirmation   <-- skipped only with AUTO_CONFIRM_LIVE_ENABLED on AND an eligibility record
 ↓
Practice Fill, or an exchange order when live trading is armed
 ↓
Structured Audit Log
```

---

## Core Principles

1. **Off until armed.** Live trading stays off until the operator sets `SIMULATION_MODE=false` and arms it (`LIVE_TRADING_ENABLED=true`, or `/golive CONFIRM`), and it never arms without `TELEGRAM_CHAT_ID`.
2. **Fail-closed risk.** Every trade must pass the fail-closed risk gate. Most of its checks reject a trade when their data is missing; the order-flow and funding-clock gates pass without data and say so on the decision record, and the liquidity guard passes when there is no order book to read. One failed check means rejection.
3. **Human-in-the-loop.** No trade executes without explicit confirmation via Telegram inline keyboard, unless the operator enables auto-confirm (`AUTO_CONFIRM_LIVE_ENABLED` with `AUTO_CONFIRM_THRESHOLD`) — both off in the shipped `.env.example`, on in the code default when no `.env` is present. Even then, a live order nobody confirmed is placed only when a committed eligibility record exists for the running strategy, and none ships.
4. **Full auditability.** Every decision is logged as structured JSON for post-mortem review.

---

## Documentation Sections

- [Getting Started](getting-started.md) -- Setup and first run
- [Architecture](architecture.md) -- System design and data flow
- [Telegram Dashboard & AI Chat](v5-dashboard.md) -- The Telegram dashboard cards and the built-in AI chat
- [Skills & Commands](skills-and-commands.md) -- Telegram commands and the skill system
- [Telegram Confirmation Flow](telegram-confirmation.md) -- How a trade idea is confirmed, re-checked and executed
- [AI Learning System](ai-learning-system.md) -- The learning modules and the safety policy that governs them
- [Solana Ecosystem Mode](solana-ecosystem.md) -- The scanner's Solana focus
- [$RCLAW Token Roadmap](token-roadmap.md) -- The token: utility, allocation, locks and treasury
- [Pre-Trade Risk Checks](risk-checks-18.md) -- Every check a trade idea must pass
- [Risk Framework](risk-framework.md) -- How risk is managed and enforced
- [Agent Hub Alignment](agent-hub-alignment.md) -- How RUNECLAW maps to the Bitget Agent Hub
- [Partner Integrations](partner-integrations.md) -- Integrations with partner technology
- [Pi Network](pi-network.md) -- RUNECLAW Local on Pi Desktop, and the Arena in the Pi Browser
- [MCP Integration](mcp-integration.md) -- The MCP surface and what it exposes
- [Enabling Live Trading](live-trading.md) -- The flags and limits for trading real funds
- [Security & Permissions](security-permissions.md) -- Keys, tokens and permission boundaries
- [Paper Trading](paper-trading.md) -- The paper trading ledger
- [Demo Scenarios](demo-scenarios.md) -- Step-by-step demos to try
- [Live Market Validation](live-validation.md) -- A dated snapshot and a stress test anyone can re-run
- [API Reference](api-reference.md) -- Data models and programmatic interface
- [FAQ](faq.md) -- Common questions

---

## Quick Links

- **Website:** [humanoid-traders.com](https://www.humanoid-traders.com/)
- **$RCLAW token:** [humanoid-traders.com/token](https://www.humanoid-traders.com/token) -- the official mint, the presale's terms, the locks and the treasury multisig
- **Try the Bot:** [@HTRUNECLAW_bot](https://t.me/HTRUNECLAW_bot) -- live on Telegram, try it now
- **GitHub:** [RUNECLAW](https://github.com/metafrogmeme-droid/001)
- **Telegram:** [Join Community](https://t.me/+VRNgsmkR5pszZTdk)
- **X / Twitter:** [@RUNECLAW_AI](https://x.com/RUNECLAW_AI)
- **Team:** Patrick Baur (Lead Developer) & Daan (Co-Founder & Strategy)
- **Began as:** an entry to the Bitget AI Base Camp · Hackathon S1
- **License:** BUSL-1.1 (Business Source License; source-available)

> **DISCLAIMER:** RUNECLAW began as an entry to the Bitget AI Base Camp · Hackathon S1 and is
> early-stage software. It trades live with small positions, and **no independent third-party
> security audit has been performed**. Running it with real funds is **at your own risk**: it has
> not had the stress testing, security review or regulatory review that production trading
> software needs. Backtest results use synthetic data and do not predict future performance. Past
> performance is not indicative of future results. This project is not financial advice.
