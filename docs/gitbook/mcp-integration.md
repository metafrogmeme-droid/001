# MCP Integration

RUNECLAW is designed as a **standalone trading agent** but its architecture maps naturally to the **Model Context Protocol (MCP)** used by the Bitget Agent Hub.

---

## What is MCP?

The Model Context Protocol is a standard interface that allows AI agents to expose their capabilities as structured tools. An MCP-compatible agent publishes a set of tools (functions) that other systems -- including the Bitget Agent Hub -- can discover and invoke.

Each tool has:
- A **name** (e.g. `runeclaw_scan`)
- A **description** (what it does)
- A **schema** (what inputs it accepts)
- A **handler** (the function that executes it)

---

## RUNECLAW Tool Map

RUNECLAW's internal skill registry maps directly to MCP tools. Each skill is a self-contained async function that takes structured input and returns structured output via Pydantic models.

| MCP Tool | Internal Skill | Description |
|----------|---------------|-------------|
| `runeclaw_scan` | `scan_market` | Scan Bitget markets for volume spikes and momentum signals |
| `runeclaw_analyze` | `analyze_asset` | Run AI + technical analysis on a specific asset, generate trade idea |
| `runeclaw_risk` | `check_risk` | Evaluate current risk status, circuit breaker, exposure limits |
| `runeclaw_execute` | `execute_paper_trade` | Execute a confirmed paper trade |
| `runeclaw_portfolio` | `get_portfolio` | Return current portfolio state (balance, equity, positions, PnL) |
| `runeclaw_explain` | `explain_trade` | Return full decision chain for a specific trade idea |
| `runeclaw_macro` | `macro_calendar` | Return macro event calendar and current risk state |
| `runeclaw_backtest` | `run_backtest` | Run backtest with synthetic data and return metrics |

---

## Architecture Alignment

RUNECLAW's skill registry pattern was designed with MCP compatibility in mind:

```text
MCP Client (Agent Hub / External AI)
 |
 v
 MCP Tool Layer ← thin adapter, maps tool calls to skills
 |
 v
 Skill Registry ← existing RUNECLAW skill system
 |
 v
 RuneClaw Engine ← orchestrator with full state
 |
 ┌───┼───┐
 v v v
Scanner Analyzer Risk Engine
```

The MCP adapter is a thin translation layer. It does not add business logic -- it maps MCP tool calls to the existing `BaseSkill.execute()` interface and serializes Pydantic responses back to the caller.

### Skill Interface

Every RUNECLAW skill follows this contract:

```python
class BaseSkill(ABC):
 name: str = "unnamed"
 description: str = ""

 @abstractmethod
 async def execute(self, engine: RuneClawEngine, **kwargs) -> str:
 ...
```

MCP tools call `skill.execute(engine, **params)` and return the string result. Input validation happens via Pydantic at the engine boundary.

---

## Data Flow

```text
MCP Request: { "tool": "runeclaw_analyze", "input": { "symbol": "BTC/USDT" } }
 |
 v
 AnalyzeAssetSkill.execute(engine, symbol="BTC/USDT")
 |
 v
 Engine: fetch candles → compute indicators → LLM thesis → TradeIdea
 |
 v
 RiskEngine: fail-closed gate
 |
 v
 MCP Response: { "result": "LONG BTC/USDT | Confidence 72% | R:R 2.8 | ..." }
```

All inputs are validated. All outputs are structured. The risk gate runs on every analysis regardless of whether the call comes from Telegram, CLI, or MCP.

---

## Integration Status

| Component | Status |
|-----------|--------|
| Skill registry (internal) | Implemented |
| Pydantic schemas at all boundaries | Implemented |
| Async execution model | Implemented |
| MCP tool adapter layer | **Implemented** -- `bot/mcp/server.py`, live over JSON-RPC at `POST /mcp` |
| Bitget Agent Hub registration | Planned -- pending Agent Hub availability |

The adapter is shipped, not planned. `bot/mcp/server.py` builds JSON Schema tool
definitions from `TOOL_CATALOGUE` and dispatches `call_tool` into the skill
registry; `app/routes/mcp.js` mounts it at `POST /mcp` as MCP Streamable HTTP.

**A SUBSET of registered skills is exposed, not all of them.** The registry is
larger than the catalogue, and that is a deliberate gap rather than an
oversight: a skill reachable by an unauthenticated agent is a different security
question from one reachable by an operator on Telegram. Read `TOOL_CATALOGUE`
for what is actually callable.

> No count appears in this table, and that is on purpose. This row used to read
> "12 skills registered" while the registry held thirty, and the line below used
> to promise "all 12" as MCP tools while nine were exposed. A hand-maintained
> count against a file that changes drifts in one direction — the same defect
> `_TOTAL_RISK_CHECKS = 23` produced against an engine emitting thirty-six
> labels, on eleven surfaces at once. The registry and the catalogue are both
> enumerable at runtime; a number typed into a document is a second, staler
> copy of something already knowable.

---

## Future: Agent Hub Registration

When the Bitget Agent Hub supports MCP tool registration, RUNECLAW will:

1. Register the existing `POST /mcp` surface with the Hub's discovery mechanism
2. Widen `TOOL_CATALOGUE` toward the registry where a skill is safe to expose
   to an unauthenticated caller

Both fail-closed guarantees already hold on every interface today: the risk gate
runs on every analysis whether the call arrives from Telegram, the CLI or MCP,
and every MCP call is logged through the structured audit system.

**One promise was dropped from this list, not moved.** It used to read
"require human confirmation for any trade execution (even via MCP)", and that is
false on shipped defaults — `bot/config.py` sets `auto_confirm_live_enabled` to
True, so a signal clearing the confidence bar places a live order with nobody
pressing anything. The honest pair is the one that IS true by default:
simulation mode is on and live trading is off until an operator switches it on.
The same sentence was corrected on the homepage, the meta description, the
JSON-LD and `llms.txt`; this was the fifth surface carrying it.
