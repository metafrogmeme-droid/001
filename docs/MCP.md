# RUNECLAW MCP Server

RUNECLAW exposes its **read-only trading intelligence** as a
[Model Context Protocol](https://modelcontextprotocol.io) server, so any
MCP-capable agent — Claude Code, Claude Desktop, agent frameworks — can use
the engine's data as tools.

## Endpoint

```
POST https://<your-deployment>/mcp
```

Transport: **Streamable HTTP, stateless** (plain JSON responses; no SSE
stream, no sessions). Per-IP rate-limited.

### Connect from Claude Code

```bash
claude mcp add --transport http runeclaw https://<your-deployment>/mcp
```

## Scope — read-only by design

Every tool either serves data the public website already publishes or
evaluates input the caller supplies (the Guardian safety tools, which store
nothing they are sent). **No tool can access a user account, and no tool can
place, modify, or cancel a real trade.** The `arena_*` tools open and close
PAPER positions and need an Arena key; trade-capable MCP tools are a separate,
operator-gated decision that has not been taken.

## Tools

The live list is `tools/list` on the endpoint; the rows below are the ones
worth knowing before you connect.

| Tool | What it returns |
|------|-----------------|
| `ask_runeclaw` | A plain-language answer from the same account-free chat the public website serves: no portfolio data, no memory between calls, no live price feed (`intent: public_scan_gate` says a question needed one), nothing traded. Rate-limited per caller at the website's rate |
| `get_track_record` | Public verifiable performance: win rate, profit factor, net PnL, recent closed trades — from recorded history |
| `get_signals` | Recent engine-generated signals (taken or not) with confidence, levels, and resolved outcomes |
| `get_agent_feed` | The agent's live mind-stream: scans, theses, opens/closes, stop moves |
| `get_rwa_radar` | Tokenized-RWA sector radar from live venue tickers (volume-weighted, vs-BTC) |
| `get_dex_compare` | DEX↔CEX basis: Hyperliquid mids vs venue perp prices, in bps |
| `get_showcase_trade` | One real recorded trade (biggest recent \|PnL\|, win or loss) |
| `run_what_if` | Hypothetical fixed-stake replay of the recorded history (`stake_usd`, `days`, `symbol`) |
| `get_weekly_letter` | The Agent Letter for the last completed ISO week |

All responses are JSON; hypothetical outputs are labelled hypothetical, and
past performance never predicts future results.
